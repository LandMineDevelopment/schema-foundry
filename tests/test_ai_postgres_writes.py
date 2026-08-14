import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_service import PostgresService, PostgresServiceError, canonical_fingerprint
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


class RetiredAiPlanAuthorityTests(unittest.TestCase):
    def test_ai_json_plan_authority_is_unavailable_without_metadata_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PostgresService(directory, connect_factory=lambda **kwargs: WriteConnection())
            service.save_profile("local", PROFILE)
            with self.assertRaises(PostgresServiceError) as caught:
                service.preview_ai_insert_rows(
                    "operation_preview", "local", "demo", "public", "events", [{"name": "launch"}],
                    {"schemaId": "schema_one", "revision": 1, "layoutToken": "0" * 64},
                )
            self.assertEqual(caught.exception.code, "durable_migrations_unavailable")
            self.assertFalse((Path(directory) / "ai_migration_plans").exists())

    def test_legacy_json_plans_are_archived_but_never_activated(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "ai_migration_plans"
            legacy.mkdir()
            (legacy / "ai_plan_old.json").write_text('{"state":"ready"}', encoding="utf-8")
            PostgresService(directory, connect_factory=lambda **kwargs: WriteConnection())
            self.assertFalse(legacy.exists())
            self.assertTrue((Path(directory) / "retired_ai_migration_plans" / "ai_plan_old.retired.json").exists())

    def test_process_local_apply_facades_cannot_execute_without_durable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PostgresService(directory, connect_factory=lambda **kwargs: WriteConnection())
            service.save_profile("local", PROFILE)
            service.introspect = lambda *args: {
                "projectName": "demo.public", "tables": [], "relationships": [], "functions": [], "views": [],
                "postgres": {"namespace": "public", "database": "demo", "fingerprint": "a" * 64},
            }
            with self.assertRaises(PostgresServiceError) as preview_error:
                service.preview("local", "public", service.introspect("local", "public"))
            self.assertEqual(preview_error.exception.code, "durable_migrations_unavailable")
            with self.assertRaises(PostgresServiceError) as apply_error:
                service.apply("local", "12345678-1234-4123-8123-123456789abc", False)
            self.assertEqual(apply_error.exception.code, "durable_migrations_unavailable")
            self.assertFalse(hasattr(service, "_plans"))


if __name__ == "__main__":
    unittest.main()
