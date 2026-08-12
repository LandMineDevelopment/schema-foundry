import json
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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
                    self.rows = [dict(row) if isinstance(row, dict) else row for row in response]
                    if "server_version_num" in sql:
                        for row in self.rows:
                            row.setdefault("server_version_num", 160000)
                    if "c.oid AS live_oid" in sql:
                        for row in self.rows:
                            row.setdefault("live_oid", 1)
                            row.setdefault("can_refresh", row.get("can_alter", False))
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
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
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
        self.assertEqual(first["definition"], {
            "status": "available", "format": "query", "sql": "SELECT id, total FROM orders",
        })

        changed = {**responses, "a.attname AS column_name": [
            responses["a.attname AS column_name"][0],
            {"column_name": "total", "data_type": "numeric(12,2)", "nullable": False, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
        ]}
        changed_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed))
        self.assertNotEqual(first["fingerprint"], changed_service.inspect_relation("local", "demo", "reporting", "order_summary")["fingerprint"])
        changed_definition = {**responses, "c.relkind AS catalog_kind": [{
            "catalog_kind": "v", "relation_kind": "view", "view_definition": "SELECT id, total, tax FROM orders",
        }]}
        definition_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed_definition))
        self.assertNotEqual(first["fingerprint"], definition_service.inspect_relation("local", "demo", "reporting", "order_summary")["fingerprint"])

    def test_relation_inspection_adds_permissions_lineage_and_provenance_without_fingerprinting_them(self):
        dependencies = [
            {"live_oid": 41, "namespace": "sales", "relation_name": "orders", "relation_kind": "table"},
            {"live_oid": 42, "namespace": "shared", "relation_name": "rates", "relation_kind": "foreign_table"},
            {"live_oid": 41, "namespace": "sales", "relation_name": "orders", "relation_kind": "table"},
        ]
        dependents = [
            {"live_oid": oid, "namespace": "reports", "relation_name": f"summary_{oid:03}", "relation_kind": "view"}
            for oid in range(1000, 1501)
        ]
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{
                "live_oid": 20, "catalog_kind": "v", "relation_kind": "view",
                "view_definition": "SELECT * FROM sales.orders", "owner_name": "reporter",
                "can_alter": True, "can_select": False, "materialized_populated": True,
            }],
            "a.attname AS column_name": [],
            "relation_dependencies": dependencies,
            "relation_dependents": dependents,
        }
        connection = Connection(responses=responses)
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.inspect_relation("local", "demo", "reports", "order_summary")
        self.assertEqual(result["owner"], {"status": "available", "name": "reporter"})
        self.assertEqual(result["permissions"], {"canSelect": False, "canAlter": True, "canRefresh": False})
        self.assertEqual(result["columnProvenance"], {"status": "unavailable", "reason": "not_supported"})
        self.assertEqual(result["materialized"], {"status": "unavailable", "reason": "not_applicable"})
        self.assertEqual(result["dependencies"]["items"], [
            {"database": "demo", "namespace": "sales", "relation": "orders", "kind": "table", "liveOid": 41},
            {"database": "demo", "namespace": "shared", "relation": "rates", "kind": "foreign_table", "liveOid": 42},
        ])
        self.assertFalse(result["dependencies"]["truncated"])
        self.assertEqual(len(result["dependents"]["items"]), 500)
        self.assertTrue(result["dependents"]["truncated"])
        lineage_queries = [item for item in connection.executed if "relation_depend" in item[0]]
        self.assertEqual([item[1] for item in lineage_queries], [(20, 20, 501), (20, 20, 501)])
        relation_query = next(sql for sql, _ in connection.executed if "c.relkind AS catalog_kind" in sql)
        self.assertIn("c.oid AS live_oid", relation_query)
        self.assertIn("pg_catalog.pg_get_userbyid(c.relowner)", relation_query)
        self.assertIn("pg_catalog.pg_has_role(c.relowner, 'USAGE')", relation_query)
        self.assertIn("pg_catalog.has_table_privilege(c.oid, 'SELECT')", relation_query)
        self.assertIn("c.relispopulated", relation_query)

        changed = {**responses, "c.relkind AS catalog_kind": [{
            **responses["c.relkind AS catalog_kind"][0], "owner_name": "other", "can_alter": False, "can_select": True,
        }]}
        changed_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed))
        self.assertEqual(result["fingerprint"], changed_service.inspect_relation("local", "demo", "reports", "order_summary")["fingerprint"])

    def test_materialized_relation_reports_population_and_concurrent_refresh_eligibility(self):
        for populated, has_index, expected in ((True, True, True), (True, False, False), (False, True, False)):
            with self.subTest(populated=populated, has_index=has_index):
                responses = {
                    "SELECT current_database() AS database": [{"database": "demo"}],
                    "c.relkind AS catalog_kind": [{
                        "live_oid": 30, "catalog_kind": "m", "relation_kind": "materialized_view",
                        "view_definition": "SELECT 1", "owner_name": None, "can_alter": True,
                        "can_select": True, "materialized_populated": populated,
                    }],
                    "a.attname AS column_name": [],
                    "concurrent_refresh_index": [{"has_refresh_index": has_index}],
                    "relation_dependencies": [],
                    "relation_dependents": [],
                }
                connection = Connection(responses=responses)
                service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
                result = service.inspect_relation("local", "demo", "reports", "daily_sales")
                self.assertEqual(result["owner"], {"status": "unavailable", "reason": "not_permitted"})
                self.assertEqual(result["materialized"], {
                    "status": "available", "populated": populated, "concurrentRefreshEligible": expected,
                })
                self.assertTrue(result["permissions"]["canRefresh"])
                index_query = next(sql for sql, _ in connection.executed if "concurrent_refresh_index" in sql)
                for condition in ("i.indisunique", "i.indisvalid", "i.indisready", "i.indimmediate", "i.indpred IS NULL", "i.indexprs IS NULL"):
                    self.assertIn(condition, index_query)

    def test_postgres_17_materialized_refresh_uses_maintain_permission(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo", "server_version_num": 170000}],
            "c.relkind AS catalog_kind": [{
                "live_oid": 30, "catalog_kind": "m", "relation_kind": "materialized_view",
                "view_definition": "SELECT 1", "owner_name": "owner", "can_alter": False,
                "can_select": True, "can_refresh": True, "materialized_populated": True,
            }],
            "a.attname AS column_name": [],
            "concurrent_refresh_index": [{"has_refresh_index": False}],
            "relation_dependencies": [],
            "relation_dependents": [],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        result = service.inspect_relation("local", "demo", "reports", "daily_sales")
        self.assertFalse(result["permissions"]["canAlter"])
        self.assertTrue(result["permissions"]["canRefresh"])
        relation_query = next(sql for sql, _ in connection.executed if "c.relkind AS catalog_kind" in sql)
        self.assertIn("has_table_privilege(c.oid, 'MAINTAIN')", relation_query)

    def test_relation_inspection_uses_repeatable_read_and_oid_bound_columns(self):
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo", "server_version_num": 160000}],
            "c.relkind AS catalog_kind": [{
                "live_oid": 77, "catalog_kind": "v", "relation_kind": "view", "view_definition": "SELECT 1",
            }],
            "a.attname AS column_name": [],
            "relation_dependencies": [],
            "relation_dependents": [],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        service.inspect_relation("local", "demo", "reports", "summary")
        self.assertEqual(connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        column_query = next(item for item in connection.executed if "a.attname AS column_name" in item[0])
        self.assertIn("a.attrelid = %s", column_query[0])
        self.assertEqual(column_query[1], (77,))
        self.assertEqual(connection.rollbacks, 1)

    def test_table_relation_operational_envelopes_are_explicitly_not_applicable(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{
                "live_oid": 10, "catalog_kind": "r", "relation_kind": "table", "view_definition": None,
                "owner_name": "developer", "can_alter": True, "can_select": True, "materialized_populated": True,
            }],
            "a.attname AS column_name": [],
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=responses))
        result = service.inspect_relation("local", "demo", "public", "orders")
        unavailable = {"status": "unavailable", "reason": "not_applicable"}
        self.assertEqual(result["dependencies"], unavailable)
        self.assertEqual(result["dependents"], unavailable)
        self.assertEqual(result["materialized"], unavailable)
        self.assertFalse(result["permissions"]["canRefresh"])

    def test_relation_definitions_are_bounded_and_tables_do_not_claim_complete_ddl(self):
        base = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "a.attname AS column_name": [],
        }
        table_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
            **base, "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
        }))
        self.assertEqual(table_service.inspect_relation("local", "demo", "public", "orders")["definition"], {
            "status": "unavailable", "reason": "not_supported",
        })
        view_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
            **base, "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": "x" * (64 * 1024 + 1)}],
        }))
        self.assertEqual(view_service.inspect_relation("local", "demo", "public", "orders")["definition"], {
            "status": "unavailable", "reason": "too_large",
        })
        for catalog_kind, relation_kind in (("v", "view"), ("m", "materialized_view")):
            with self.subTest(kind=relation_kind):
                permitted_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
                    **base, "c.relkind AS catalog_kind": [{
                        "catalog_kind": catalog_kind, "relation_kind": relation_kind, "view_definition": "SELECT 1",
                    }],
                }))
                self.assertEqual(permitted_service.inspect_relation("local", "demo", "public", "orders")["definition"], {
                    "status": "available", "format": "query", "sql": "SELECT 1",
                })
        unavailable_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
            **base, "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": None}],
        }))
        self.assertEqual(unavailable_service.inspect_relation("local", "demo", "public", "orders")["definition"], {
            "status": "unavailable", "reason": "not_permitted",
        })
        untrusted = '</code><script>throw new Error("unsafe")</script>'
        untrusted_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses={
            **base, "c.relkind AS catalog_kind": [{"catalog_kind": "v", "relation_kind": "view", "view_definition": untrusted}],
        }))
        self.assertEqual(untrusted_service.inspect_relation("local", "demo", "public", "orders")["definition"]["sql"], untrusted)

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

    def test_relation_source_verification_reports_missing_added_and_changed_columns(self):
        base_rows = [
            {"column_name": "id", "data_type": "bigint", "nullable": False, "ordinal": 1, "type_category": "N", "type_name": "int8"},
            {"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 2, "type_category": "S", "type_name": "text"},
        ]
        base = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": base_rows,
        }
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=base))
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal")} for column in descriptor["columns"]],
        }
        self.assertEqual(service.verify_relation_source("local", source)["status"], "verified")

        changed = {**base, "a.attname AS column_name": [
            {"column_name": "id", "data_type": "integer", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "int4"},
            {"column_name": "created_at", "data_type": "timestamp", "nullable": False, "ordinal": 3, "type_category": "D", "type_name": "timestamp"},
        ]}
        changed_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=changed))
        result = changed_service.verify_relation_source("local", source)
        self.assertEqual(result["status"], "changed")
        self.assertFalse(result["matches"])
        self.assertEqual(result["missingColumns"], ["status"])
        self.assertEqual(result["addedColumns"], ["created_at"])
        self.assertEqual(result["changedColumns"], [{"name": "id", "changes": ["type", "nullable", "ordinal"]}])

        missing = {**base, "c.relkind AS catalog_kind": []}
        missing_service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: Connection(responses=missing))
        missing_result = missing_service.verify_relation_source("local", source)
        self.assertEqual(missing_result["status"], "missing")
        self.assertEqual(missing_result["missingColumns"], ["id", "status"])

    def test_widget_query_is_verified_read_only_bounded_and_returns_provenance(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [
                {"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 1, "type_category": "S", "type_name": "text"},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            '"status" AS "__schemer_d0"': {
                "columns": ["__schemer_d0", "__schemer_m0"],
                "rows": [("paid", Decimal("30.50")), ("pending", Decimal("12")), ("extra", Decimal("1"))],
            },
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal")} for column in descriptor["columns"]],
        }
        query = {
            "version": 2,
            "dimensions": [{"id": "dimension_status", "label": "Status", "column": "status"}],
            "measures": [{"id": "measure_revenue", "label": "Revenue", "column": "amount", "aggregation": "sum", "distinct": False, "nullBehavior": "zero", "numberFormat": {"style": "currency", "currency": "USD", "fractionDigits": 2}}],
            "filters": [{"id": "filter_group_status", "conditions": [{"id": "filter_status", "column": "status", "operator": "neq", "values": ["cancelled"]}]}],
            "sort": [{"targetKind": "measure", "targetId": "measure_revenue", "direction": "desc", "nulls": "last"}],
            "limit": 2,
        }
        result = service.execute_widget_query("local", source, query)
        self.assertEqual(result["rows"], [["paid", "30.50"], ["pending", "12"]])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["queryVersion"], 2)
        self.assertEqual(result["parameters"], ["cancelled", 3])
        self.assertIsInstance(result["queryDurationMs"], int)
        self.assertTrue(result["queriedAt"].endswith("Z"))
        self.assertEqual(result["lineage"]["measures"][0]["sourceColumn"], "amount")
        self.assertEqual(result["lineage"]["filterGroups"][0]["conditions"][0]["operator"], "neq")
        self.assertEqual(result["provenance"]["profile"], {"id": "local", "label": "Local"})
        self.assertNotIn("password", json.dumps(result["provenance"]))
        self.assertEqual(result["provenance"]["relation"]["definition"]["reason"], "not_supported")
        query_connection = connections[-1]
        self.assertEqual(query_connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertIn(('LOCK TABLE "public"."orders" IN ACCESS SHARE MODE', ()), query_connection.executed)
        sql, parameters = next(item for item in query_connection.executed if '"status" AS "__schemer_d0"' in item[0])
        self.assertEqual(sql, result["sql"])
        self.assertNotIn("cancelled", sql)
        self.assertEqual(parameters, ("cancelled", 3))
        self.assertNotIn("JOIN", sql.upper())
        self.assertEqual(query_connection.rollbacks, 1)
        self.assertTrue(query_connection.closed)

        changed_source = {**source, "fingerprint": "0" * 64}
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_widget_query("local", changed_source, query)
        self.assertEqual(error.exception.code, "relation_changed")
        self.assertFalse(any('"status" AS "__schemer_d0"' in sql for sql, _ in connections[-1].executed))
        duplicate_ordinals = {**source, "columns": [{**column, "ordinal": 1} for column in source["columns"]]}
        with self.assertRaises(ValidationError):
            service.execute_widget_query("local", duplicate_ordinals, query)

    def test_temporal_series_manifest_and_windows_use_one_proportional_utc_domain(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [
                {"column_name": "ordered_on", "data_type": "date", "nullable": False, "ordinal": 1, "type_category": "D", "type_name": "date"},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            "pg_catalog.min": [{"__schemer_min": datetime(2026, 1, 1), "__schemer_max": datetime(2026, 1, 10), "__schemer_points": 10}],
            "pg_catalog.to_timestamp": {
                "columns": ["__schemer_t0", "__schemer_m0"],
                "rows": [
                    (datetime(2026, 1, 1, tzinfo=timezone.utc), Decimal("30.50")),
                    (datetime(2026, 1, 3, tzinfo=timezone.utc), Decimal("12.00")),
                ],
            },
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal")} for column in descriptor["columns"]],
        }
        query = {
            "version": 2,
            "dimensions": [{"id": "dimension_ordered", "label": "Ordered on", "column": "ordered_on"}],
            "measures": [{"id": "measure_revenue", "label": "Revenue", "column": "amount", "aggregation": "sum", "distinct": False, "nullBehavior": "zero", "numberFormat": {"style": "currency", "currency": "USD", "fractionDigits": 2}}],
            "filters": [], "sort": [], "limit": 10,
        }
        manifest = service.execute_temporal_series("local", source, query, "manifest", "refresh-one")
        self.assertFalse(manifest["empty"])
        self.assertEqual(manifest["domain"], {"min": "2026-01-01T00:00:00.000Z", "max": "2026-01-10T00:00:00.000Z"})
        self.assertEqual(manifest["series"]["bucketSeconds"], 86400)
        self.assertEqual(manifest["series"]["alignedStart"], "2026-01-01T00:00:00.000Z")
        self.assertEqual(manifest["series"]["alignedEndExclusive"], "2026-01-11T00:00:00.000Z")
        self.assertEqual(manifest["series"]["refreshGeneration"], "refresh-one")
        self.assertGreater(manifest["series"]["expiresAtEpoch"], 0)
        self.assertEqual(len(manifest["series"]["key"]), 64)
        manifest_connection = connections[-1]
        self.assertIn(("SET LOCAL TIME ZONE 'UTC'", ()), manifest_connection.executed)
        self.assertEqual(manifest_connection.rollbacks, 1)

        window = service.execute_temporal_series(
            "local", source, query, "window", "refresh-one", manifest["series"], manifest["series"]["alignedStart"]
        )
        self.assertEqual(window["rows"], [["2026-01-01T00:00:00.000Z", "30.50"], ["2026-01-03T00:00:00.000Z", "12.00"]])
        self.assertEqual(window["range"], {"start": "2026-01-01T00:00:00.000Z", "endExclusive": "2026-01-11T00:00:00.000Z"})
        window_connection = connections[-1]
        sql, parameters = next(item for item in window_connection.executed if "pg_catalog.to_timestamp" in item[0])
        self.assertIn(">= %s", sql)
        self.assertIn("< %s", sql)
        self.assertEqual(parameters[:2], (86400, 86400))
        self.assertEqual(parameters[-1], 11)
        self.assertEqual(window_connection.rollbacks, 1)

        stale = {**manifest["series"], "bucketSeconds": 60}
        with self.assertRaises(ValidationError):
            service.execute_temporal_series("local", source, query, "window", "refresh-one", stale, stale["alignedStart"])
        stale_key = {**manifest["series"], "key": "0" * 64}
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_temporal_series("local", source, query, "window", "refresh-one", stale_key, stale_key["alignedStart"])
        self.assertEqual(error.exception.code, "temporal_series_stale")
        with self.assertRaises(ValidationError):
            service.execute_temporal_series("local", source, query, "window", "refresh-two", manifest["series"], manifest["series"]["alignedStart"])
        expired = {**manifest["series"], "expiresAtEpoch": 0}
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_temporal_series("local", source, query, "window", "refresh-one", expired, expired["alignedStart"])
        self.assertEqual(error.exception.code, "temporal_series_expired")

    def test_relation_detail_counts_and_pages_one_verified_snapshot(self):
        responses = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "c.relkind AS catalog_kind": [{"catalog_kind": "r", "relation_kind": "table", "view_definition": None}],
            "a.attname AS column_name": [
                {"column_name": "status", "data_type": "text", "nullable": False, "ordinal": 1, "type_category": "S", "type_name": "text"},
                {"column_name": "amount", "data_type": "numeric", "nullable": True, "ordinal": 2, "type_category": "N", "type_name": "numeric"},
            ],
            'count(*) AS "__schemer_count"': [{"__schemer_count": 3}],
            '"status" AS "__schemer_c0"': {
                "columns": ["__schemer_c0", "__schemer_c1"],
                "rows": [("paid", Decimal("30.50")), ("paid", Decimal("12"))],
            },
        }
        connections = []
        service = PostgresService(
            self.temporary_directory.name,
            connect_factory=lambda **kwargs: (connections.append(Connection(responses=responses)) or connections[-1]),
        )
        descriptor = service.inspect_relation("local", "demo", "public", "orders")
        source = {
            **{key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
            "columns": [{key: column[key] for key in ("name", "type", "nullable", "ordinal")} for column in descriptor["columns"]],
        }
        query = {
            "version": 2,
            "dimensions": [{"id": "dimension_status", "label": "Status", "column": "status"}],
            "measures": [{"id": "measure_revenue", "label": "Revenue", "column": "amount", "aggregation": "sum", "distinct": False, "nullBehavior": "zero", "numberFormat": {"style": "decimal", "fractionDigits": 2}}],
            "filters": [{"id": "filter_group_status", "conditions": [{"id": "filter_status", "column": "status", "operator": "neq", "values": ["cancelled"]}]}],
            "sort": [],
            "limit": 100,
        }
        selection = {"dimensions": [{"targetId": "dimension_status", "value": "paid"}], "measureId": "measure_revenue"}
        detail = {
            "version": 1,
            "columns": [
                {"id": "detail_status", "label": "Status", "column": "status", "numberFormat": {"style": "auto"}, "searchable": True},
                {"id": "detail_amount", "label": "Amount", "column": "amount", "numberFormat": {"style": "decimal", "fractionDigits": 2}, "searchable": False},
            ],
            "rowIdentifier": None,
        }
        result = service.execute_relation_detail(
            "local", source, query, selection, detail, 0, 2,
            {"targetId": "detail_amount", "direction": "desc", "nulls": "last"},
            [{"targetId": "detail_status", "value": "acme"}],
        )
        self.assertEqual(result["rows"], [["paid", "30.50"], ["paid", "12"]])
        self.assertEqual(result["matchingRowCount"], 3)
        self.assertTrue(result["hasMore"])
        self.assertEqual(result["parameters"], ["cancelled", "paid", "%acme%", 2, 0])
        self.assertEqual(result["countParameters"], ["cancelled", "paid", "%acme%"])
        self.assertIsInstance(result["queryDurationMs"], int)
        self.assertTrue(result["queriedAt"].endswith("Z"))
        self.assertEqual(result["provenance"]["profile"], {"id": "local", "label": "Local"})
        self.assertNotIn("host", result["provenance"]["profile"])
        query_connection = connections[-1]
        self.assertEqual(query_connection.executed[0][0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        self.assertIn(('LOCK TABLE "public"."orders" IN ACCESS SHARE MODE', ()), query_connection.executed)
        count_index = next(index for index, item in enumerate(query_connection.executed) if '__schemer_count' in item[0])
        page_index = next(index for index, item in enumerate(query_connection.executed) if '__schemer_c0' in item[0])
        self.assertLess(count_index, page_index)
        self.assertNotIn("JOIN", result["sql"].upper())
        self.assertNotIn("acme", result["sql"])
        self.assertEqual(query_connection.rollbacks, 1)
        self.assertTrue(query_connection.closed)

        with self.assertRaises(ValidationError):
            service.execute_relation_detail("local", source, query, selection, detail, 0, 101, None, [])

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
        connection = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
            query: {"columns": ["id", "amount"], "rows": rows},
        })
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
        metadata = {
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
        }
        connection = Connection(responses=metadata, fail_on="SELECT secret FROM payments")
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

        connection = Connection(responses=metadata, fail_on="SELECT DISTINCT", failure=QueryError())
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: connection)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT DISTINCT ON (id) id FROM payments ORDER BY created_at")
        self.assertEqual(
            error.exception.message,
            "Read-only SQL query failed: SELECT DISTINCT ON expressions must match initial ORDER BY expressions",
        )

    def test_read_only_sql_verifies_exact_target_and_schemer_limits(self):
        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_read_only_sql("local", "public", "SELECT 1", expected_profile_fingerprint="stale")
        self.assertEqual(error.exception.code, "profile_changed")

        with self.assertRaises(PostgresServiceError) as error:
            self.service.execute_read_only_sql("local", "public", "SELECT 1", database="other")
        self.assertEqual(error.exception.code, "database_changed")

        mismatch = Connection(responses={"SELECT current_database() AS database": [{"database": "other"}]})
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: mismatch)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT 1", database="demo", reject_privileged_role=True)
        self.assertEqual(error.exception.code, "database_changed")
        self.assertFalse(any(sql == "SELECT 1" for sql, _ in mismatch.executed))

        privileged = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo", "rolsuper": True, "rolbypassrls": False}],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: privileged)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT 1", database="demo", reject_privileged_role=True)
        self.assertEqual(error.exception.code, "unsafe_database_role")

        missing = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": False}],
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: missing)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "missing", "SELECT 1", database="demo")
        self.assertEqual(error.exception.code, "not_found")

        query = "SELECT value FROM metrics"
        limited = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
            query: {"columns": ["value"], "rows": [(float("nan"),), ("x" * 1000,), ("later",)]},
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: limited)
        result = service.execute_read_only_sql(
            "local", "public", query, database="demo", allow_explain=False,
            max_rows=100, max_columns=50, max_result_bytes=400,
        )
        self.assertEqual(result["rows"], [["nan"]])
        self.assertTrue(result["truncated"])
        json.dumps(result, allow_nan=False)
        with self.assertRaises(ValidationError):
            service.execute_read_only_sql("local", "public", "EXPLAIN SELECT 1", database="demo", allow_explain=False)

        wide = Connection(responses={
            "SELECT current_database() AS database": [{"database": "demo"}],
            "SELECT EXISTS": [{"exists": True}],
            "SELECT * FROM wide": {"columns": [f"column_{index}" for index in range(51)], "rows": []},
        })
        service = PostgresService(self.temporary_directory.name, connect_factory=lambda **kwargs: wide)
        with self.assertRaises(PostgresServiceError) as error:
            service.execute_read_only_sql("local", "public", "SELECT * FROM wide", database="demo", max_columns=50)
        self.assertEqual(error.exception.code, "sql_result_too_wide")

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
