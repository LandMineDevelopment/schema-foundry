import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import DashboardStore, DashboardStoreError, mercury_dashboard_record


SOURCE = {
    "profileId": "schemii_example_postgres",
    "database": "schemii",
    "namespace": "bookstore",
    "relation": "orders",
    "kind": "table",
    "fingerprint": "a" * 64,
}


class DashboardStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "dashboards"
        self.store = DashboardStore(self.root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_example_initializes_once_and_deletion_is_respected(self):
        self.store.initialize_once()
        records = self.store.list()
        self.assertEqual([record["id"] for record in records], ["dashboard_mercury"])
        self.assertEqual(len(records[0]["dashboard"]["widgets"]), 6)
        self.store.delete("dashboard_mercury")
        self.store.initialize_once()
        self.assertEqual(self.store.list(), [])

    def test_create_duplicate_and_permissions(self):
        self.store.initialize_once()
        created = self.store.create("Operations")
        duplicate = self.store.create("Mercury copy", "dashboard_mercury")
        self.assertEqual(created["dashboard"]["widgets"], [])
        self.assertEqual(len(duplicate["dashboard"]["widgets"]), 6)
        self.assertNotEqual(duplicate["id"], "dashboard_mercury")
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / f"{created['id']}.json").stat().st_mode), 0o600)

    def test_stale_revision_is_rejected_without_changing_layout(self):
        self.store.initialize_once()
        first = self.store.get("dashboard_mercury")
        stale = json.loads(json.dumps(first))
        before_mobile = json.loads(json.dumps(first["dashboard"]["widgets"][0]["layout"]["mobile"]))
        first["dashboard"]["widgets"][0]["layout"]["desktop"]["x"] = 1
        saved = self.store.save(first["id"], first)
        self.assertEqual(saved["dashboard"]["widgets"][0]["layout"]["mobile"], before_mobile)
        with self.assertRaises(DashboardStoreError) as error:
            self.store.save(stale["id"], stale)
        self.assertEqual(error.exception.payload["error"]["code"], "dashboard_conflict")
        self.assertEqual(self.store.get(first["id"])["dashboard"]["widgets"][0]["layout"]["desktop"]["x"], 1)

    def test_invalid_records_and_duplicate_widget_ids_are_rejected(self):
        record = mercury_dashboard_record()
        record["dashboard"]["widgets"][1]["id"] = record["dashboard"]["widgets"][0]["id"]
        with self.assertRaises(DashboardStoreError):
            self.store.save(record["id"], record)
        with self.assertRaises(DashboardStoreError):
            self.store.create("  invalid  ")

    def test_single_widget_source_persists_and_duplicates_independently(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        record["dashboard"]["widgets"][0]["configuration"] = {"source": SOURCE}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["source"], SOURCE)
        duplicate = self.store.create("Sourced copy", record["id"])
        duplicate_source = duplicate["dashboard"]["widgets"][0]["configuration"]["source"]
        duplicate_source["relation"] = "customers"
        self.assertEqual(self.store.get(record["id"])["dashboard"]["widgets"][0]["configuration"]["source"]["relation"], "orders")

    def test_widget_source_rejects_multiple_sources_joins_sql_and_malformed_identity(self):
        invalid_configurations = [
            {"sources": [SOURCE]},
            {"source": {**SOURCE, "join": {"relation": "customers"}}},
            {"source": {**SOURCE, "sql": "SELECT * FROM orders"}},
            {"source": [SOURCE]},
            {"source": {**SOURCE, "kind": "sequence"}},
            {"source": {**SOURCE, "fingerprint": "short"}},
            {"source": {key: value for key, value in SOURCE.items() if key != "namespace"}},
        ]
        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                record = mercury_dashboard_record()
                record["dashboard"]["widgets"][0]["configuration"] = configuration
                with self.assertRaises(DashboardStoreError) as error:
                    self.store.save(record["id"], record)
                self.assertEqual(error.exception.payload["error"]["code"], "invalid_dashboard")

    def test_malformed_file_is_not_listed_or_overwritten(self):
        malformed = self.root / "broken.json"
        malformed.write_text("not json", encoding="utf-8")
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(DashboardStoreError):
            self.store.get("broken")
        self.assertEqual(malformed.read_text(encoding="utf-8"), "not json")


if __name__ == "__main__":
    unittest.main()
