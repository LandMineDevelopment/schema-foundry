import json
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_service import (
    ConflictError,
    PostgresService,
    PostgresServiceError,
    ValidationError,
    canonical_fingerprint,
    quote_identifier,
)


PROFILE = {
    "name": "Local", "host": "localhost", "port": 5432, "dbname": "demo",
    "user": "developer", "password": "secret", "sslmode": "prefer", "timeout": 5,
}


class Column:
    def __init__(self, name):
        self.name = name


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.description = []

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise self.connection.failure or RuntimeError("database detail that must not escape")
        for marker, response in self.connection.responses.items():
            if marker in sql:
                if isinstance(response, dict) and "rows" in response:
                    self.rows = response["rows"]
                    self.description = [Column(name) for name in response.get("columns", [])]
                else:
                    self.rows = response
                    if self.rows and isinstance(self.rows[0], dict):
                        self.description = [Column(name) for name in self.rows[0]]
                break
        else:
            self.rows = []
            self.description = []

    def fetchall(self):
        return self.rows

    def fetchmany(self, size):
        return self.rows[:size]

    def close(self):
        pass


class Connection:
    def __init__(self, responses=None, fail_on=None, failure=None):
        self.responses = responses or {}
        self.fail_on = fail_on
        self.failure = failure
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def empty_schema(fingerprint="live"):
    return {
        "projectName": "demo.public", "tables": [], "relationships": [], "functions": [], "views": [],
        "postgres": {"namespace": "public", "database": "demo", "serverVersion": "16", "fingerprint": fingerprint},
    }


class PostgresServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection())
        self.profile = self.service.save_profile("local", PROFILE)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_profile_secret_redaction_permissions_and_blank_update(self):
        self.assertNotIn("password", self.profile)
        self.assertNotIn("password", self.service.list_profiles()[0])
        store = Path(self.temporary_directory.name) / "postgres_profiles.json"
        self.assertEqual(stat.S_IMODE(Path(self.temporary_directory.name).stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
        self.service.save_profile("local", {**PROFILE, "name": "Updated", "password": ""})
        self.assertEqual(json.loads(store.read_text())["profiles"]["local"]["password"], "secret")
        store.chmod(0o644)
        PostgresService(self.temporary_directory.name)
        self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)

    def test_profile_validation_identifier_quoting_and_plan_invalidation(self):
        for change in ({"port": 0}, {"timeout": True}, {"sslmode": "maybe"}, {"host": "bad host"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.service.save_profile("bad", {**PROFILE, **change})
        with self.assertRaises(ValidationError):
            self.service.save_profile("../bad", PROFILE)
        self.assertEqual(quote_identifier('Odd"Name'), '"Odd""Name"')

        self.service.introspect = lambda profile_id, namespace: empty_schema()
        plan = self.service.preview("local", "public", empty_schema())
        self.service.save_profile("local", {**PROFILE, "dbname": "other"})
        self.assertNotIn(plan["id"], self.service._plans)

    def test_connection_uses_keyword_arguments_without_exposing_password(self):
        captured = {}
        connection = Connection({"current_database()": [{"database": "demo", "version": "PostgreSQL 16"}]})
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (captured.update(kwargs) or connection),
        )
        result = service.test_profile("local")
        self.assertTrue(result["ok"])
        self.assertEqual(captured["dbname"], "demo")
        self.assertEqual(captured["password"], "secret")
        self.assertNotIn("password", result)

    def test_table_data_preview_is_paginated_ordered_and_json_safe(self):
        connection = Connection(responses={
            "con.contype = 'p'": [{"column_name": "id"}],
            "a.attname AS column_name": [
                {"column_name": "id", "data_type": "uuid", "nullable": False, "ordinal": 1},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2},
                {"column_name": "created_at", "data_type": "timestamp", "nullable": False, "ordinal": 3},
            ],
            'SELECT * FROM "public"."payments"': [
                {"id": UUID(int=1), "amount": Decimal("10.25"), "created_at": datetime(2026, 7, 25, 12, 30)},
                {"id": UUID(int=2), "amount": None, "created_at": datetime(2026, 7, 25, 12, 31)},
                {"id": UUID(int=3), "amount": Decimal("3"), "created_at": datetime(2026, 7, 25, 12, 32)},
            ],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.preview_table_data("local", "public", "payments", offset=10, limit=2)
        self.assertEqual(result["primaryKey"], ["id"])
        self.assertTrue(result["stableOrder"])
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["nextOffset"], 12)
        self.assertEqual(result["rows"][0]["amount"], "10.25")
        data_query = next(item for item in connection.executed if 'SELECT * FROM "public"."payments"' in item[0])
        self.assertIn('ORDER BY "id"', data_query[0])
        self.assertEqual(data_query[1], (3, 10))
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertTrue(connection.closed)

    def test_relation_catalog_verifies_database_and_lists_supported_kinds(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relname AS relation_name": [
                {"relation_name": "orders", "relation_kind": "table"},
                {"relation_name": "order_summary", "relation_kind": "view"},
                {"relation_name": "daily_sales", "relation_kind": "materialized_view"},
            ],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.list_relations("local", "demo", "public")
        self.assertEqual(result["profileId"], "local")
        self.assertEqual(result["database"], "demo")
        self.assertEqual(result["namespace"], "public")
        self.assertEqual([item["kind"] for item in result["relations"]], ["table", "view", "materialized_view"])
        catalog_query = next(sql for sql, _ in connection.executed if "c.relname AS relation_name" in sql)
        self.assertIn("c.relkind IN ('r', 'p', 'v', 'm')", catalog_query)
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertTrue(connection.closed)

    def test_relation_catalog_rejects_unverified_database(self):
        connection = Connection(responses={"SELECT current_database() AS database": [{"database": "other"}]})
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.list_relations("local", "demo", "public")
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(error.exception.code, "database_changed")
        self.assertFalse(any("c.relname AS relation_name" in sql for sql, _ in connection.executed))
        self.assertTrue(connection.closed)

    def test_relation_inspection_returns_ordered_columns_and_stable_fingerprint(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": "SELECT id, total FROM orders"}],
            "a.attname AS column_name": [
                {"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
                {"column_name": "total", "data_type": "numeric(12,2)", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=responses))
        first = service.inspect_relation("local", "demo", "reporting", "order_summary")
        second = service.inspect_relation("local", "demo", "reporting", "order_summary")
        self.assertEqual(first["kind"], "view")
        self.assertEqual(first["columns"], [
            {"name": "id", "type": "bigint", "nullable": False, "ordinal": 1, "suggestions": ["dimension", "identifier"]},
            {"name": "total", "type": "numeric(12,2)", "nullable": True, "ordinal": 2, "suggestions": ["dimension", "measure"]},
        ])
        self.assertEqual(len(first["fingerprint"]), 64)
        self.assertEqual(first["fingerprint"], second["fingerprint"])

        changed = {**responses, "a.attname AS column_name": [
            responses["a.attname AS column_name"][0],
            {"column_name": "total", "data_type": "numeric(12,2)", "nullable": False, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
        ]}
        changed_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed))
        self.assertNotEqual(first["fingerprint"], changed_service.inspect_relation("local", "demo", "reporting", "order_summary")["fingerprint"])

    def test_relation_inspection_rejects_missing_relation(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.inspect_relation("local", "demo", "public", "missing")
        self.assertEqual(error.exception.code, "not_found")
        self.assertFalse(any("a.attname AS column_name" in sql for sql, _ in connection.executed))

    def test_relation_column_role_suggestions_are_advisory_and_not_fingerprinted(self):
        rows = [
            {"column_name": "customer_id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
            {"column_name": "amount", "data_type": "numeric", "nullable": False, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            {"column_name": "ordered_at", "data_type": "timestamp with time zone", "nullable": False, "ordinal": 3, "type_category": "D", "type_name": "timestamptz"},
            {"column_name": "external_key", "data_type": "uuid", "nullable": False, "ordinal": 4, "type_category": "U", "type_name": "uuid"},
            {"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 5, "type_category": "S", "type_name": "text"},
            {"column_name": "metadata", "data_type": "jsonb", "nullable": True, "ordinal": 6, "type_category": "U", "type_name": "jsonb"},
        ]
        base = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": rows,
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=base))
        result = service.inspect_relation("local", "demo", "public", "orders")
        self.assertEqual([column["suggestions"] for column in result["columns"]], [
            ["dimension", "identifier"], ["dimension", "measure"], ["dimension", "date"],
            ["dimension", "identifier"], ["dimension"], [],
        ])
        changed_policy_input = {**base, "a.attname AS column_name": [{**row, "type_category": "S"} for row in rows]}
        changed_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed_policy_input))
        self.assertEqual(result["fingerprint"], changed_service.inspect_relation("local", "demo", "public", "orders")["fingerprint"])

    def test_verified_relation_preview_is_read_only_bounded_and_uses_one_relation(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": "SELECT id, amount FROM payments"}],
            "a.attname AS column_name": [
                {"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            'SELECT "id", "amount" FROM "public"."orders"': [
                {"id": 1, "amount": Decimal("10.25")},
                {"id": 2, "amount": Decimal("20")},
                {"id": 3, "amount": Decimal("30")},
            ],
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")}
        result = service.preview_relation_rows("local", source, offset=10, limit=2)
        self.assertEqual(result["rows"], [{"id": 1, "amount": "10.25"}, {"id": 2, "amount": "20"}])
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["nextOffset"], 12)
        self.assertFalse(result["stableOrder"])
        preview_connection = connections[-1]
        self.assertEqual(preview_connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertIn("SET LOCAL statement_timeout", preview_connection.executed[1][0])
        data_sql, parameters = next(item for item in preview_connection.executed if 'FROM "public"."orders"' in item[0])
        self.assertNotIn("*", data_sql)
        self.assertNotIn("JOIN", data_sql.upper())
        self.assertEqual(parameters, (3, 10))
        self.assertTrue(preview_connection.closed)

    def test_verified_relation_preview_rejects_stale_or_unbounded_sources_before_select(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [{"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"}],
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        source = {
            "profileId": "local", "database": "demo", "namespace": "public", "relation": "orders",
            "kind": "table", "fingerprint": "0" * 64,
        }
        with self.assertRaises(PostgresServiceError) as error:
            service.preview_relation_rows("local", source)
        self.assertEqual(error.exception.code, "relation_changed")
        self.assertFalse(any('FROM "public"."orders"' in sql for sql, _ in connections[-1].executed))
        for invalid_source, limit in (({**source, "join": "customers"}, 20), (source, 51)):
            with self.subTest(source=invalid_source, limit=limit), self.assertRaises(ValidationError):
                service.preview_relation_rows("local", invalid_source, limit=limit)

    def test_relation_inspection_rejects_stale_kind_and_fingerprint(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [{"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"}],
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=responses))
        current = service.inspect_relation("local", "demo", "public", "orders")
        verified = service.inspect_relation("local", "demo", "public", "orders", "table", current["fingerprint"])
        self.assertEqual(verified["fingerprint"], current["fingerprint"])
        for kind, fingerprint in (("view", current["fingerprint"]), ("table", "0" * 64)):
            with self.subTest(kind=kind, fingerprint=fingerprint), self.assertRaises(PostgresServiceError) as error:
                service.inspect_relation("local", "demo", "public", "orders", kind, fingerprint)
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(error.exception.code, "relation_changed")
        with self.assertRaises(ValidationError):
            service.inspect_relation("local", "demo", "public", "orders", "table", "invalid")

    def test_table_data_preview_validates_page_and_missing_table(self):
        with self.assertRaises(ValidationError):
            self.service.preview_table_data("local", "public", "events", limit=51)
        with self.assertRaises(ValidationError):
            self.service.preview_table_data("local", "public", "bad\x00table")
        connection = Connection(responses={"a.attname AS column_name": []})
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.preview_table_data("local", "public", "missing")
        self.assertEqual(error.exception.code, "not_found")

    def test_separate_service_instances_share_profile_updates(self):
        first = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection())
        second = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection())
        first.save_profile("first", PROFILE)
        second.save_profile("second", {**PROFILE, "name": "Reporting", "dbname": "reports"})

        profiles = {profile["id"]: profile for profile in first.list_profiles()}
        self.assertEqual(set(profiles), {"local", "first", "second"})
        self.assertNotIn("password", profiles["first"])
        self.assertTrue((Path(self.temporary_directory.name) / ".postgres_profiles.lock").is_file())

    def test_read_only_sql_limits_rows_and_serializes_values(self):
        query = "SELECT id, amount FROM payments"
        rows = [(UUID(int=index + 1), Decimal(f"{index}.25")) for index in range(501)]
        connection = Connection(responses={query: {"columns": ["id", "amount"], "rows": rows}})
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.execute_read_only_sql("local", "public", query)
        self.assertEqual(result["rowCount"], 500)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["rows"][0], [str(UUID(int=1)), "0.25"])
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION READ ONLY")
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)

    def test_read_only_sql_rejects_invalid_or_failed_queries(self):
        for statement in ("", "UPDATE payments SET amount = 0", "SELECT 1; SELECT 2", "DO $$ BEGIN NULL; END $$"):
            with self.subTest(statement=statement), self.assertRaises(ValidationError):
                self.service.execute_read_only_sql("local", "public", statement)
        connection = Connection(fail_on="SELECT secret FROM payments")
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT secret FROM payments")
        self.assertEqual(error.exception.code, "sql_query_failed")
        self.assertNotIn("database detail", error.exception.message)
        self.assertEqual(connection.rollbacks, 1)

        class Diagnostic:
            message_primary = "SELECT DISTINCT ON expressions must match initial ORDER BY expressions"

        class QueryError(Exception):
            sqlstate = "42P10"
            diag = Diagnostic()

        connection = Connection(fail_on="SELECT DISTINCT", failure=QueryError())
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT DISTINCT ON (id) id FROM payments ORDER BY created_at")
        self.assertEqual(
            error.exception.message,
            "Read-only SQL query failed: SELECT DISTINCT ON expressions must match initial ORDER BY expressions",
        )

    def test_canonical_fingerprint_ignores_layout_and_transients(self):
        first = {"tables": [{"id": "t", "name": "x", "x": 1, "color": "red"}], "postgres": {"profileId": "one", "importedAt": "then"}}
        second = {"postgres": {"profileId": "two", "importedAt": "now"}, "tables": [{"color": "blue", "x": 99, "name": "x", "id": "t"}]}
        self.assertEqual(canonical_fingerprint(first), canonical_fingerprint(second))
        second["tables"][0]["name"] = "y"
        self.assertNotEqual(canonical_fingerprint(first), canonical_fingerprint(second))

    def test_introspection_maps_composite_keys_indexes_triggers_and_routines(self):
        columns = [
            {"table_name": "parent", "column_name": "tenant", "ordinal": 1, "data_type": "uuid", "nullable": False, "default_sql": None, "identity_kind": "", "generated_kind": ""},
            {"table_name": "parent", "column_name": "number", "ordinal": 2, "data_type": "integer", "nullable": False, "default_sql": None, "identity_kind": "", "generated_kind": ""},
            {"table_name": "child", "column_name": "tenant", "ordinal": 1, "data_type": "uuid", "nullable": False, "default_sql": None, "identity_kind": "", "generated_kind": ""},
            {"table_name": "child", "column_name": "parent_number", "ordinal": 2, "data_type": "integer", "nullable": False, "default_sql": None, "identity_kind": "", "generated_kind": ""},
        ]
        constraints = [
            {"constraint_name": "parent_pkey", "table_name": "parent", "constraint_type": "p", "columns": ["tenant", "number"], "target_namespace": None, "target_table": None, "target_columns": [], "update_action": "a", "delete_action": "a", "deferrable": False, "initially_deferred": False, "definition": "PRIMARY KEY (tenant, number)"},
            {"constraint_name": "child_parent_fkey", "table_name": "child", "constraint_type": "f", "columns": ["tenant", "parent_number"], "target_namespace": "public", "target_table": "parent", "target_columns": ["tenant", "number"], "update_action": "c", "delete_action": "r", "deferrable": False, "initially_deferred": False, "definition": "FOREIGN KEY ..."},
        ]
        indexes = [{"table_name": "child", "index_name": "child_tenant_idx", "definition": "CREATE INDEX child_tenant_idx ON public.child USING btree (tenant)", "is_unique": False, "method": "btree"}]
        routines = [{"name": "touch_child", "kind": "f", "identity_arguments": "", "arguments": "", "return_type": "trigger", "language": "plpgsql", "definition": "CREATE FUNCTION public.touch_child() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$"}]
        triggers = [{"table_name": "child", "trigger_name": "child_touch", "definition": "CREATE TRIGGER child_touch BEFORE UPDATE ON public.child FOR EACH ROW EXECUTE FUNCTION public.touch_child()", "enabled": "O"}]
        schema = self.service._build_schema(
            "local", "public", {"database": "demo", "server_version": "16.3", "server_version_num": "160003"},
            columns, constraints, indexes, routines, [], triggers,
        )
        relation = schema["relationships"][0]
        self.assertEqual(len(relation["fromColumnIds"]), 2)
        self.assertEqual(len(relation["toColumnIds"]), 2)
        self.assertEqual(relation["onUpdate"], "CASCADE")
        parent = next(table for table in schema["tables"] if table["name"] == "parent")
        child = next(table for table in schema["tables"] if table["name"] == "child")
        self.assertEqual([column["name"] for column in parent["columns"] if column["primary"]], ["tenant", "number"])
        self.assertEqual(child["indexes"][0]["name"], "child_tenant_idx")
        self.assertEqual(child["triggers"][0]["name"], "child_touch")
        self.assertEqual(schema["functions"][0]["name"], "touch_child")

    def test_preview_is_immutable_and_destructive_drops_are_gated(self):
        self.service.introspect = lambda profile_id, namespace: empty_schema()
        desired = empty_schema()
        desired["tables"] = [{"id": "t", "name": "new table", "columns": [{"id": "c", "name": "id", "type": "integer", "nullable": False}], "uniqueConstraints": []}]
        plan = self.service.preview("local", "public", desired)
        self.assertIn('CREATE TABLE "public"."new table"', plan["steps"][0]["sql"])
        plan["steps"][0]["sql"] = "MUTATED"
        self.assertNotEqual(self.service._plans[plan["id"]]["steps"][0]["sql"], "MUTATED")
        live = empty_schema()
        live["tables"] = desired["tables"]
        self.service.introspect = lambda profile_id, namespace: live
        omitted = self.service.preview("local", "public", empty_schema(), False)
        self.assertEqual(omitted["steps"], [])
        self.assertEqual(omitted["warnings"][0]["code"], "destructive_omitted")
        included = self.service.preview("local", "public", empty_schema(), True)
        self.assertTrue(included["steps"][0]["destructive"])

    def test_preview_rejects_additional_top_level_sql_statements(self):
        self.service.introspect = lambda profile_id, namespace: empty_schema()
        desired = empty_schema()
        desired["tables"] = [{
            "id": "t", "name": "events", "uniqueConstraints": [],
            "columns": [{"id": "c", "name": "value", "type": "integer", "nullable": True, "default": "0; DROP TABLE important"}],
        }]
        with self.assertRaises(ValidationError):
            self.service.preview("local", "public", desired)
        desired["tables"][0]["columns"][0]["default"] = "0"
        desired["functions"] = [{
            "name": "safe", "kind": "function", "identityArguments": "",
            "definition": "CREATE FUNCTION public.safe() RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM 1; END; $$; DROP TABLE important",
        }]
        with self.assertRaises(ValidationError):
            self.service.preview("local", "public", desired)

    def test_apply_rejects_stale_and_requires_confirmation(self):
        desired = empty_schema()
        desired["tables"] = [{"id": "t", "name": "gone", "columns": [{"id": "c", "name": "id", "type": "integer"}]}]
        live = json.loads(json.dumps(desired))
        live["postgres"] = empty_schema("one")["postgres"]
        self.service.introspect = lambda profile_id, namespace: live
        plan = self.service.preview("local", "public", empty_schema(), True)
        self.service._introspect_connection = lambda connection, profile_id, namespace: live
        with self.assertRaises(ConflictError) as error:
            self.service.apply("local", plan["id"])
        self.assertEqual(error.exception.code, "destructive_confirmation_required")
        self.service._introspect_connection = lambda connection, profile_id, namespace: empty_schema("changed")
        with self.assertRaises(ConflictError) as error:
            self.service.apply("local", plan["id"], True)
        self.assertEqual(error.exception.code, "stale_plan")

    def test_apply_rolls_back_and_sanitizes_errors(self):
        connection = Connection(fail_on="CREATE TABLE")
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        service.introspect = lambda profile_id, namespace: empty_schema("same")
        desired = empty_schema()
        desired["tables"] = [{"id": "t", "name": "x", "columns": [{"id": "c", "name": "id", "type": "integer"}]}]
        plan = service.preview("local", "public", desired)
        service._introspect_connection = lambda connection, profile_id, namespace: empty_schema("same")
        with self.assertRaises(PostgresServiceError) as error:
            service.apply("local", plan["id"])
        self.assertEqual(error.exception.code, "apply_failed")
        self.assertNotIn("database detail", error.exception.message)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 0)

    def test_apply_commits_stored_steps_and_records_history(self):
        connection = Connection()
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        service.introspect = lambda profile_id, namespace: empty_schema("same")
        introspections = 0

        def introspect_connection(connection, profile_id, namespace):
            nonlocal introspections
            introspections += 1
            return empty_schema("same" if introspections == 1 else "refreshed")

        service._introspect_connection = introspect_connection
        desired = empty_schema()
        desired["tables"] = [{"id": "t", "name": "x", "columns": [{"id": "c", "name": "id", "type": "integer"}]}]
        plan = service.preview("local", "public", desired)
        result = service.apply("local", plan["id"])
        self.assertEqual(connection.commits, 1)
        self.assertEqual(result["postgres"]["fingerprint"], "refreshed")
        self.assertNotIn(plan["id"], service._plans)
        history = service.list_history("local")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["planId"], plan["id"])
        self.assertEqual(history[0]["steps"][0]["objectType"], "table")
        self.assertEqual(stat.S_IMODE((Path(self.temporary_directory.name) / "migration_history.json").stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
