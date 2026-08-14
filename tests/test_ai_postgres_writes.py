import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_service import ConflictError, NotFoundError, PostgresService, PostgresServiceError, canonical_fingerprint
from tests.test_postgres_service import Connection, PROFILE


TARGET_CATALOG = {
    "database": "demo", "namespace": "public", "relation": "events", "liveOid": 7,
    "catalogKind": "r", "columns": [
        {"name": "name", "type": "text", "nullable": False, "ordinal": 1, "default": None, "identity": "", "generated": ""},
    ],
    "rowSecurity": False, "forceRowSecurity": False, "constraints": [], "triggers": [], "policies": [],
    "rules": [], "executableDependencies": [], "requestedColumnPrivileges": [{"name": "name", "can_insert": True}],
}
TARGET = {"kind": "table", "fingerprint": canonical_fingerprint(TARGET_CATALOG), "catalog": TARGET_CATALOG}


class RowCountCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = -1

    def execute(self, sql, params=()):
        self.connection.executed.append((sql, params))
        if "pg_current_xact_id()::text" in sql:
            self.connection.rows = [{"xid": "42"}]
        elif "pg_xact_status" in sql:
            self.connection.rows = [{"status": self.connection.transaction_status}]
        else:
            self.connection.rows = []
        if sql.startswith("INSERT INTO"):
            self.rowcount = 2

    def fetchall(self):
        return list(self.connection.rows)

    def close(self):
        pass


class WriteConnection(Connection):
    def __init__(self, *, transaction_status="committed", fail_commit=False):
        super().__init__()
        self.rows = []
        self.transaction_status = transaction_status
        self.fail_commit = fail_commit

    def cursor(self):
        return RowCountCursor(self)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("lost commit response")


class AiPostgresWriteTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.connections = []

        def connect(**kwargs):
            connection = WriteConnection()
            self.connections.append(connection)
            return connection

        self.service = PostgresService(self.temporary_directory.name, connect_factory=connect)
        self.service.save_profile("local", PROFILE)
        self.service._inspect_ai_insert_target = lambda *args: TARGET
        self.binding = {"schemaId": "schema_one", "revision": 1, "layoutToken": "0" * 64}

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_insert_preview_is_durable_and_apply_uses_one_bound_json_parameter(self):
        rows = [{"name": "launch"}, {"name": "review"}]
        preview = self.service.preview_ai_insert_rows(
            "operation_preview", "local", "demo", "public", "events", rows, self.binding,
        )
        self.assertEqual(preview["rowCount"], 2)
        self.assertEqual(preview["rows"], rows)
        self.assertNotIn("expectation", preview)
        if os.name != "nt":
            plan_path = Path(self.temporary_directory.name) / "ai_migration_plans" / f"{preview['applyPlanId']}.json"
            self.assertEqual(stat.S_IMODE(plan_path.stat().st_mode), 0o600)

        restarted = PostgresService(self.temporary_directory.name, connect_factory=self.service._connect_factory)
        restarted._inspect_ai_insert_target = lambda *args: TARGET
        result = restarted.apply_ai_postgres_write(
            "operation_apply", preview["applyPlanId"], "local", "demo", "public", "events", "insert_rows", preview["planDigest"],
        )
        self.assertEqual(result["insertedRowCount"], 2)
        connection = self.connections[-1]
        inserts = [(sql, params) for sql, params in connection.executed if sql.startswith("INSERT INTO")]
        self.assertEqual(len(inserts), 1)
        self.assertIn('INSERT INTO "public"."events" ("name")', inserts[0][0])
        self.assertEqual(json.loads(inserts[0][1][0]), rows)
        self.assertNotIn("launch", inserts[0][0])
        self.assertEqual(connection.commits, 1)

        repeated = restarted.apply_ai_postgres_write(
            "operation_apply", preview["applyPlanId"], "local", "demo", "public", "events", "insert_rows", preview["planDigest"],
        )
        self.assertEqual(repeated, result)
        self.assertEqual(len([item for item in connection.executed if item[0].startswith("INSERT INTO")]), 1)

    def test_uncertain_insert_reconciles_transaction_status_without_retry(self):
        preview = self.service.preview_ai_insert_rows(
            "operation_uncertain_preview", "local", "demo", "public", "events", [{"name": "launch"}], self.binding,
        )
        uncertain = WriteConnection(fail_commit=True)
        self.service._connect_factory = lambda **kwargs: uncertain
        with self.assertRaises(PostgresServiceError) as error:
            self.service.apply_ai_postgres_write(
                "operation_uncertain_apply", preview["applyPlanId"], "local", "demo", "public", "events", "insert_rows", preview["planDigest"],
            )
        self.assertEqual(error.exception.code, "execution_outcome_unknown")
        self.assertEqual(len([item for item in uncertain.executed if item[0].startswith("INSERT INTO")]), 1)

        status_connection = WriteConnection(transaction_status="committed")
        self.service._connect_factory = lambda **kwargs: status_connection
        result = self.service.reconcile_ai_postgres_write(preview["applyPlanId"], "local")
        self.assertEqual(result["kind"], "rows_inserted")
        self.assertFalse(any(sql.startswith("INSERT INTO") for sql, _ in status_connection.executed))

    def test_aborted_transaction_requires_fresh_preview(self):
        preview = self.service.preview_ai_insert_rows(
            "operation_aborted_preview", "local", "demo", "public", "events", [{"name": "launch"}], self.binding,
        )
        plan = self.service._read_ai_plan(preview["applyPlanId"])
        plan.update({
            "state": "uncertain", "applyOperationId": "operation_aborted_apply", "transactionId": "43",
            "intendedResult": {
                "kind": "rows_inserted", "operationId": "operation_aborted_apply", "planId": preview["applyPlanId"],
                "target": {"profileId": "local", "database": "demo", "namespace": "public", "relation": "events"},
                "insertedRowCount": 1,
            },
        })
        self.service._write_ai_plan(plan)
        self.service._connect_factory = lambda **kwargs: WriteConnection(transaction_status="aborted")
        with self.assertRaises(ConflictError) as error:
            self.service.reconcile_ai_postgres_write(preview["applyPlanId"], "local")
        self.assertEqual(error.exception.code, "apply_not_committed")

    def test_write_plan_identity_and_kind_fail_closed(self):
        preview = self.service.preview_ai_insert_rows(
            "operation_identity_preview", "local", "demo", "public", "events", [{"name": "launch"}], self.binding,
        )
        with self.assertRaises(NotFoundError):
            self.service.apply_ai_postgres_write(
                "operation_identity_apply", preview["applyPlanId"], "local", "demo", "public", "events", "create_view", preview["planDigest"],
            )
        with self.assertRaises(ConflictError) as review_error:
            self.service.apply_ai_postgres_write(
                "operation_identity_apply", preview["applyPlanId"], "local", "demo", "public", "events", "insert_rows", "0" * 64,
            )
        self.assertEqual(review_error.exception.code, "review_changed")

    def test_corrupted_plan_values_fail_closed(self):
        preview = self.service.preview_ai_insert_rows(
            "operation_corrupt_preview", "local", "demo", "public", "events", [{"name": "launch"}], self.binding,
        )
        path = Path(self.temporary_directory.name) / "ai_migration_plans" / f"{preview['applyPlanId']}.json"
        plan = json.loads(path.read_text())
        plan["input"]["rows"][0]["name"] = "changed"
        path.write_text(json.dumps(plan))
        with self.assertRaises(PostgresServiceError) as error:
            self.service.apply_ai_postgres_write(
                "operation_corrupt_apply", preview["applyPlanId"], "local", "demo", "public", "events", "insert_rows", preview["planDigest"],
            )
        self.assertEqual(error.exception.code, "plan_store_error")


@unittest.skipUnless(os.environ.get("SCHEMII_TEST_PG17_DSN"), "SCHEMII_TEST_PG17_DSN is not configured")
class AiPostgresWriteIntegrationTests(unittest.TestCase):
    def test_insert_and_view_apply_against_disposable_postgres_target(self):
        try:
            import psycopg
            from psycopg.conninfo import conninfo_to_dict
        except ImportError as exc:
            self.skipTest(str(exc))
        dsn = os.environ["SCHEMII_TEST_PG17_DSN"]
        schema = "schemii_ai_write_test"
        setup = psycopg.connect(dsn, autocommit=True)
        connection_info = conninfo_to_dict(dsn)
        with tempfile.TemporaryDirectory() as directory:
            try:
                setup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                setup.execute(f'CREATE SCHEMA "{schema}"')
                setup.execute(f'CREATE TABLE "{schema}".events (id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text NOT NULL, details jsonb)')
                info = setup.info
                service = PostgresService(directory)
                service.save_profile("local", {
                    "name": "Integration", "host": info.host, "port": info.port, "dbname": info.dbname,
                    "user": info.user, "password": connection_info.get("password", ""),
                    "sslmode": connection_info.get("sslmode", "prefer"), "timeout": 5,
                })
                binding = {"schemaId": "schema_one", "revision": 1, "layoutToken": "0" * 64}
                insert = service.preview_ai_insert_rows(
                    "integration_insert_preview", "local", info.dbname, schema, "events",
                    [{"name": "launch", "details": {"priority": 2}}, {"name": "review", "details": None}], binding,
                )
                inserted = service.apply_ai_postgres_write(
                    "integration_insert_apply", insert["applyPlanId"], "local", info.dbname, schema, "events", "insert_rows", insert["planDigest"],
                )
                self.assertEqual(inserted["insertedRowCount"], 2)
                self.assertEqual(setup.execute(f'SELECT name FROM "{schema}".events ORDER BY id').fetchall(), [("launch",), ("review",)])

                stale = service.preview_ai_insert_rows(
                    "integration_stale_preview", "local", info.dbname, schema, "events", [{"name": "later", "details": None}], binding,
                )
                setup.execute(f'ALTER TABLE "{schema}".events ADD CONSTRAINT events_name_check CHECK (name <> \'blocked\')')
                with self.assertRaises(ConflictError) as stale_error:
                    service.apply_ai_postgres_write(
                        "integration_stale_apply", stale["applyPlanId"], "local", info.dbname, schema, "events", "insert_rows", stale["planDigest"],
                    )
                self.assertEqual(stale_error.exception.code, "relation_changed")
                self.assertEqual(setup.execute(f'SELECT count(*) FROM "{schema}".events').fetchone()[0], 2)

                view = service.preview_ai_create_view(
                    "integration_view_preview", "local", info.dbname, schema, "event_names",
                    f'CREATE VIEW "{schema}"."event_names" AS SELECT id, name FROM "{schema}".events', binding,
                )
                created = service.apply_ai_postgres_write(
                    "integration_view_apply", view["applyPlanId"], "local", info.dbname, schema, "event_names", "create_view", view["planDigest"],
                )
                self.assertEqual(created["descriptor"]["kind"], "view")
                self.assertEqual(setup.execute(f'SELECT count(*) FROM "{schema}".event_names').fetchone()[0], 2)
            finally:
                setup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                setup.close()


if __name__ == "__main__":
    unittest.main()
