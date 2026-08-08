import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.schema_store import SchemaStore, SchemaStoreError


def record(schema_id, project_name="Untitled schema"):
    return {
        "id": schema_id,
        "updatedAt": "2026-07-25T00:00:00.000Z",
        "schema": {
            "projectName": project_name,
            "tables": [],
            "relationships": [],
            "functions": [],
        },
    }


class SchemaStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.schema_dir = Path(self.temporary_directory.name)
        self.store = SchemaStore(self.schema_dir)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_equal_project_names_are_stored_in_separate_id_files(self):
        for schema_id in ("schema_one", "schema_two"):
            self.store.save(
                schema_id,
                record(schema_id),
                expected_layout_token=None,
                layout_protocol=None,
            )

        self.assertEqual(
            {path.name for path in self.schema_dir.glob("*.json")},
            {"schema_one.json", "schema_two.json"},
        )
        self.assertEqual({item["id"] for item in self.store.list()}, {"schema_one", "schema_two"})

    def test_save_migrates_a_legacy_project_name_file(self):
        legacy_path = self.schema_dir / "schema_old_name.json"
        legacy_path.write_text(json.dumps(record("schema_one", "Old name")), encoding="utf-8")

        self.store.save(
            "schema_one",
            record("schema_one", "New name"),
            expected_layout_token=None,
            layout_protocol=None,
        )

        self.assertFalse(legacy_path.exists())
        saved = json.loads((self.schema_dir / "schema_one.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["schema"]["projectName"], "New name")

    def test_schema_revisions_reject_stale_writes(self):
        first = self.store.save(
            "schema_one",
            record("schema_one"),
            expected_layout_token=None,
            layout_protocol=None,
        )
        self.assertEqual(first["revision"], 1)

        with self.assertRaises(SchemaStoreError) as error:
            self.store.save(
                "schema_one",
                record("schema_one", "Stale edit"),
                expected_layout_token=None,
                layout_protocol=None,
            )
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(error.exception.payload["error"]["code"], "schema_conflict")

        current = record("schema_one", "Current edit")
        current["revision"] = first["revision"]
        saved = self.store.save(
            "schema_one",
            current,
            expected_layout_token=None,
            layout_protocol=None,
        )
        self.assertEqual(saved["revision"], 2)

    def test_wholesale_layout_changes_require_current_v2_layout_token(self):
        original = record("schema_one")
        original["schema"]["tables"] = [{"id": f"table_{index}", "columns": []} for index in range(10)]
        original["schema"]["layout"] = {
            "version": 1,
            "tables": {
                f"table_{index}": {"x": index * 100, "y": 0, "color": "#f4b942"}
                for index in range(10)
            },
            "view": {"x": 0, "y": 0, "zoom": 1},
        }
        first = self.store.save(
            "schema_one", original, expected_layout_token=None, layout_protocol=None
        )

        changed = json.loads(json.dumps(original))
        changed["revision"] = first["revision"]
        for layout in changed["schema"]["layout"]["tables"].values():
            layout["x"] += 500
            layout["color"] = "#e58d4c"

        for token, protocol in ((None, None), (first["layoutToken"], None), (None, "2")):
            with self.subTest(token=token, protocol=protocol), self.assertRaises(SchemaStoreError) as error:
                self.store.save(
                    "schema_one",
                    changed,
                    expected_layout_token=token,
                    layout_protocol=protocol,
                )
            self.assertEqual(error.exception.payload["error"]["code"], "layout_conflict")

        saved = self.store.save(
            "schema_one",
            changed,
            expected_layout_token=first["layoutToken"],
            layout_protocol="2",
        )
        self.assertNotEqual(saved["layoutToken"], first["layoutToken"])

    def test_invalid_records_paths_and_delete_contract(self):
        for schema_id, payload in (("../bad", record("../bad")), ("schema_one", [])):
            with self.subTest(schema_id=schema_id), self.assertRaises(SchemaStoreError):
                self.store.save(
                    schema_id,
                    payload,
                    expected_layout_token=None,
                    layout_protocol=None,
                )

        self.store.save(
            "schema_one", record("schema_one"), expected_layout_token=None, layout_protocol=None
        )
        self.assertEqual(self.store.delete("schema_one"), {"deleted": "schema_one"})
        self.assertFalse((self.schema_dir / "schema_one.json").exists())


if __name__ == "__main__":
    unittest.main()
