import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.examples import (
    LOCAL_SCHEMA_ID,
    POSTGRES_LAYOUT,
    POSTGRES_PROFILE_ID,
    POSTGRES_SCHEMA_ID,
    ExampleInstaller,
    local_example_record,
)
from schemii.schema_store import SchemaStore, SchemaStoreError


PROFILE = {
    "name": "Mercury Books: Included PostgreSQL", "host": "postgres", "port": 5432,
    "dbname": "schemii", "user": "schemii", "password": "local", "sslmode": "disable", "timeout": 10,
}


class FakePostgresService:
    def __init__(self):
        self.profiles = {}
        self.calls = []

    def list_profiles(self):
        return [{"id": key, **{name: value for name, value in profile.items() if name != "password"}} for key, profile in self.profiles.items()]

    def save_profile(self, profile_id, profile):
        self.calls.append(("save_profile", profile_id))
        self.profiles[profile_id] = dict(profile)
        return {"id": profile_id, **{name: value for name, value in profile.items() if name != "password"}}

    def test_profile(self, profile_id):
        self.calls.append(("test_profile", profile_id))
        return {"ok": True, "database": "schemii", "serverVersion": "PostgreSQL 17"}

    def list_namespaces(self, profile_id):
        self.calls.append(("list_namespaces", profile_id))
        return ["bookstore", "public"]

    def introspect(self, profile_id, namespace):
        self.calls.append(("introspect", profile_id, namespace))
        tables = []
        for index, name in enumerate(POSTGRES_LAYOUT):
            tables.append({
                "id": f"live_table_{name}", "name": name, "namespace": namespace,
                "x": index * 100, "y": 100, "color": "red", "columns": [],
                "primaryKey": None, "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
                "postgres": {"liveOid": 1000 + index},
            })
        return {
            "projectName": "schemii.bookstore", "tables": tables, "relationships": [], "functions": [], "views": [],
            "postgres": {"sourceProfileId": profile_id, "database": "schemii", "namespace": namespace, "fingerprint": "live"},
        }


class ExampleInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = SchemaStore(self.root / "schemas")
        self.service = FakePostgresService()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_local_example_has_valid_relationships_and_deliberate_layout(self):
        record = local_example_record()
        schema = record["schema"]
        tables = {table["id"]: table for table in schema["tables"]}
        columns = {column["id"] for table in tables.values() for column in table["columns"]}

        self.assertEqual(record["id"], LOCAL_SCHEMA_ID)
        self.assertEqual(len(tables), 7)
        self.assertEqual(set(schema["layout"]["tables"]), set(tables))
        self.assertLess(schema["layout"]["view"]["zoom"], 1)
        for relationship in schema["relationships"]:
            self.assertIn(relationship["fromTableId"], tables)
            self.assertIn(relationship["toTableId"], tables)
            self.assertIn(relationship["fromColumnId"], columns)
            self.assertIn(relationship["toColumnId"], columns)

    def test_first_run_marker_respects_deletion_until_explicit_restore(self):
        installer = ExampleInstaller(self.service, self.store, self.root / "config", "local")
        first = installer.initialize_once()
        self.assertEqual(first["installed"], [LOCAL_SCHEMA_ID])
        self.store.delete(LOCAL_SCHEMA_ID)

        second = installer.initialize_once()
        self.assertEqual(second["installed"], [])
        self.assertEqual(self.store.list(), [])

        restored = installer.restore()
        self.assertEqual(restored["installed"], [LOCAL_SCHEMA_ID])
        self.assertEqual([record["id"] for record in self.store.list()], [LOCAL_SCHEMA_ID])

    def test_postgres_example_verifies_target_and_saves_introspection_with_custom_layout(self):
        installer = ExampleInstaller(self.service, self.store, self.root / "config", "all", PROFILE)
        result = installer.initialize_once()
        saved = self.store.get(POSTGRES_SCHEMA_ID)["schema"]

        self.assertEqual(result["errors"], [])
        self.assertEqual(set(result["installed"]), {LOCAL_SCHEMA_ID, POSTGRES_PROFILE_ID, POSTGRES_SCHEMA_ID})
        self.assertIn(("test_profile", POSTGRES_PROFILE_ID), self.service.calls)
        self.assertIn(("introspect", POSTGRES_PROFILE_ID, "bookstore"), self.service.calls)
        self.assertEqual(saved["postgres"]["fingerprint"], "live")
        self.assertEqual(saved["projectName"], "Mercury Books: PostgreSQL tutorial")
        for table_name, (x, y, color) in POSTGRES_LAYOUT.items():
            layout = saved["layout"]["tables"][f"live_table_{table_name}"]
            self.assertEqual((layout["x"], layout["y"], layout["color"]), (x, y, color))
        self.assertTrue(all("x" not in table and "color" not in table for table in saved["tables"]))

    def test_restore_preserves_existing_example_layout_and_does_not_reintrospect(self):
        installer = ExampleInstaller(self.service, self.store, self.root / "config", "all", PROFILE)
        installer.initialize_once()
        before = json.dumps({record["id"]: record["schema"]["layout"] for record in self.store.list()}, sort_keys=True)
        introspections = self.service.calls.count(("introspect", POSTGRES_PROFILE_ID, "bookstore"))

        result = installer.restore()
        after = json.dumps({record["id"]: record["schema"]["layout"] for record in self.store.list()}, sort_keys=True)

        self.assertEqual(result["installed"], [])
        self.assertEqual(before, after)
        self.assertEqual(self.service.calls.count(("introspect", POSTGRES_PROFILE_ID, "bookstore")), introspections)
        self.assertIn(("test_profile", POSTGRES_PROFILE_ID), self.service.calls)

    def test_restore_reconciles_the_reserved_profile_password_before_verification(self):
        installer = ExampleInstaller(self.service, self.store, self.root / "config", "all", PROFILE)
        installer.initialize_once()
        self.service.profiles[POSTGRES_PROFILE_ID]["password"] = "stale"

        result = installer.restore()

        self.assertEqual(result["errors"], [])
        self.assertEqual(self.service.profiles[POSTGRES_PROFILE_ID]["password"], PROFILE["password"])

    def test_schema_store_errors_are_returned_as_bounded_restore_errors(self):
        class FailingStore:
            def list(self):
                return []

            def save(self, *args, **kwargs):
                raise SchemaStoreError(500, "schema_store_error", "Example design could not be saved")

        installer = ExampleInstaller(self.service, FailingStore(), self.root / "config", "local")
        result = installer.restore()

        self.assertEqual(result["installed"], [])
        self.assertEqual(result["errors"], [{"component": "local", "message": "Example design could not be saved"}])

    def test_bookstore_seed_showcases_relational_and_postgres_objects(self):
        sql = (ROOT / "examples/postgres/001_bookstore.sql").read_text(encoding="utf-8")
        self.assertEqual(sql.count("CREATE TABLE bookstore."), 9)
        self.assertIn("\\if :install_bookstore\n\nBEGIN;", sql)
        self.assertIn("COMMIT;\n\n\\endif", sql)
        for feature in (
            "GENERATED ALWAYS", "PRIMARY KEY (book_id, author_id)", "REFERENCES bookstore.",
            "CREATE INDEX", "USING gin", "CREATE FUNCTION", "CREATE TRIGGER", "CREATE VIEW", "INSERT INTO bookstore.",
        ):
            self.assertIn(feature, sql)


if __name__ == "__main__":
    unittest.main()
