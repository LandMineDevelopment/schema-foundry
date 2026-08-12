import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.schema_store import SchemaStore, SchemaStoreError, schema_layout_token
from schemii.ai_schema_mutations import apply_schema_action, deterministic_id


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

    def test_layout_token_binds_legacy_table_coordinates(self):
        original = record("schema_one")
        original["schema"]["tables"] = [{"id": "table_one", "x": 1, "y": 2, "color": "yellow", "columns": []}]
        changed = json.loads(json.dumps(original))
        changed["schema"]["tables"][0]["x"] = 3
        self.assertNotEqual(schema_layout_token(original), schema_layout_token(changed))

    def test_v2_table_view_and_viewport_replacements_require_layout_token(self):
        original = record("schema_one")
        original["schema"]["layout"] = {
            "version": 2,
            "layers": {
                "tables": {
                    "objects": {
                        f"table_{index}": {"x": index, "y": 0, "color": "yellow"}
                        for index in range(8)
                    },
                    "viewport": {"x": 0, "y": 0, "zoom": 1},
                },
                "views": {
                    "objects": {
                        f"view_{index}": {"x": index, "y": 10, "color": "blue"}
                        for index in range(8)
                    },
                    "viewport": {"x": 20, "y": 30, "zoom": 1},
                },
            },
        }
        first = self.store.save(
            "schema_one", original, expected_layout_token=None, layout_protocol=None
        )

        for changed_layer in ("tables", "views", "both", "viewport"):
            changed = json.loads(json.dumps(original))
            changed["revision"] = first["revision"]
            if changed_layer == "viewport":
                changed["schema"]["layout"]["layers"]["views"]["viewport"]["x"] = 999
            else:
                names = ("tables", "views") if changed_layer == "both" else (changed_layer,)
                for name in names:
                    changed["schema"]["layout"]["layers"][name]["objects"] = {}
            with self.subTest(changed_layer=changed_layer):
                with self.assertRaises(SchemaStoreError) as error:
                    self.store.save(
                        "schema_one", changed, expected_layout_token=None, layout_protocol="2"
                    )
                self.assertEqual(error.exception.payload["error"]["code"], "layout_conflict")

    def test_stale_v2_token_allows_a_narrow_object_save(self):
        original = record("schema_one")
        original["schema"]["layout"] = {
            "version": 2,
            "layers": {
                "tables": {
                    "objects": {
                        f"table_{index}": {"x": index, "y": 0, "color": "yellow"}
                        for index in range(8)
                    },
                    "viewport": {"x": 0, "y": 0, "zoom": 1},
                },
                "views": {"objects": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}},
            },
        }
        first = self.store.save(
            "schema_one", original, expected_layout_token=None, layout_protocol=None
        )
        changed = json.loads(json.dumps(original))
        changed["revision"] = first["revision"]
        changed["schema"]["layout"]["layers"]["tables"]["objects"]["table_0"]["x"] = 100

        saved = self.store.save(
            "schema_one", changed, expected_layout_token=None, layout_protocol="2"
        )
        self.assertEqual(saved["revision"], 2)

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

    def test_view_mutation_reservation_blocks_concurrent_save_until_release(self):
        original = record("schema_one")
        original["schema"].update({
            "views": [{"id": "view_summary", "name": "summary", "namespace": "public"}],
            "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
        })
        saved = self.store.save("schema_one", original, expected_layout_token=None, layout_protocol=None)
        entered = threading.Event()
        completed = threading.Event()

        def concurrent_save():
            entered.set()
            changed = self.store.get("schema_one")
            changed["schema"]["projectName"] = "Concurrent"
            self.store.save(
                "schema_one", changed,
                expected_layout_token=saved["layoutToken"], layout_protocol="2",
            )
            completed.set()

        with self.store.reserve_view_mutation_binding(
            "schema_one", saved["revision"], saved["layoutToken"],
            "local", "demo", "public", "summary",
            "upsert", {"kind": "view", "fingerprint": "a" * 64}, "view_summary",
        ):
            thread = threading.Thread(target=concurrent_save)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(completed.wait(0.05))
        thread.join(1)
        self.assertTrue(completed.is_set())

    def test_view_mutation_reservation_does_not_block_another_schema(self):
        for schema_id in ("schema_one", "schema_two"):
            item = record(schema_id)
            item["schema"].update({
                "views": [{"id": "view_summary", "name": "summary", "namespace": "public"}],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            })
            self.store.save(schema_id, item, expected_layout_token=None, layout_protocol=None)
        one = self.store.get("schema_one")
        completed = threading.Event()

        def save_other_schema():
            other = self.store.get("schema_two")
            self.store.save(
                "schema_two", other,
                expected_layout_token=schema_layout_token(other), layout_protocol="2",
            )
            completed.set()

        with self.store.reserve_view_mutation_binding(
            "schema_one", one["revision"], schema_layout_token(one),
            "local", "demo", "public", "summary",
            "upsert", {"kind": "view", "fingerprint": "a" * 64}, "view_summary",
        ):
            thread = threading.Thread(target=save_other_schema)
            thread.start()
            self.assertTrue(completed.wait(1))
        thread.join(1)

    def test_ai_mutation_is_atomic_idempotent_and_preserves_existing_layout(self):
        original = record("schema_one")
        original["schema"]["tables"] = [{
            "id": "table_users", "name": "users", "x": 100, "y": 200, "color": "#abc",
            "columns": [{"id": "col_id", "name": "id", "type": "uuid", "primary": True, "nullable": False, "unique": True}],
            "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
        }]
        original["schema"]["layout"] = {"version": 2, "layers": {
            "tables": {"objects": {"table_users": {"x": 100, "y": 200, "color": "#abc", "custom": "keep"}}, "viewport": {"x": 9, "y": 8, "zoom": 0.7}},
            "views": {"objects": {"view_one": {"x": 4, "y": 5, "color": "blue"}}, "viewport": {"x": 1, "y": 2, "zoom": 1}},
        }}
        saved = self.store.save("schema_one", original, expected_layout_token=None, layout_protocol=None)
        before = self.store.get("schema_one")["schema"]["layout"]
        action = {"type": "add_table", "name": "events", "purpose": "Events", "columns": [{"name": "id", "type": "uuid", "primary": True}], "requiresConfirmation": True}
        callback = lambda current: apply_schema_action(current, action, "operation_one")

        result = self.store.apply_ai_mutation("schema_one", "operation_one", saved["revision"], saved["layoutToken"], callback)
        duplicate = SchemaStore(self.schema_dir).apply_ai_mutation("schema_one", "operation_one", saved["revision"], saved["layoutToken"], callback)
        current = self.store.get("schema_one")

        self.assertEqual(result, duplicate)
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["schema"]["layout"]["layers"]["tables"]["objects"]["table_users"], before["layers"]["tables"]["objects"]["table_users"])
        self.assertEqual(current["schema"]["layout"]["layers"]["tables"]["viewport"], before["layers"]["tables"]["viewport"])
        self.assertEqual(current["schema"]["layout"]["layers"]["views"], before["layers"]["views"])
        self.assertEqual(current["schema"]["tables"][1]["id"], deterministic_id("table", "operation_one", "table"))

    def test_ai_mutation_rejects_stale_revision_and_layout(self):
        saved = self.store.save("schema_one", record("schema_one"), expected_layout_token=None, layout_protocol=None)
        callback = lambda current: (current, {"actionType": "test", "changed": [], "impact": []})
        with self.assertRaises(SchemaStoreError) as revision_error:
            self.store.apply_ai_mutation("schema_one", "operation_one", 2, saved["layoutToken"], callback)
        self.assertEqual(revision_error.exception.payload["error"]["code"], "schema_conflict")
        with self.assertRaises(SchemaStoreError) as layout_error:
            self.store.apply_ai_mutation("schema_one", "operation_one", 1, "0" * 64, callback)
        self.assertEqual(layout_error.exception.payload["error"]["code"], "layout_conflict")

    def test_ai_column_delete_returns_dependency_manifest(self):
        original = record("schema_one")
        original["schema"]["tables"] = [{
            "id": "table_events", "name": "events", "columns": [
                {"id": "col_id", "name": "id", "type": "uuid", "primary": True, "nullable": False, "unique": True},
                {"id": "col_status", "name": "status", "type": "text", "primary": False, "nullable": True, "unique": False},
            ], "uniqueConstraints": [], "checks": [{"id": "check_status", "name": "events_status_check", "definition": "status <> ''"}], "indexes": [], "triggers": [],
        }]
        saved = self.store.save("schema_one", original, expected_layout_token=None, layout_protocol=None)
        action = {"type": "delete_element", "elementType": "column", "tableId": "table_events", "columnId": "col_status", "reason": "Unused", "destructive": True, "requiresConfirmation": True}
        result = self.store.apply_ai_mutation("schema_one", "operation_delete", 1, saved["layoutToken"], lambda current: apply_schema_action(current, action, "operation_delete"))
        self.assertEqual(result["impact"], [{"kind": "check", "id": "check_status"}, {"kind": "column", "id": "col_status"}])

    def test_ai_project_creation_is_deterministic_across_store_restart(self):
        first = self.store.create_ai_project("operation_project", "Demo")
        second = SchemaStore(self.schema_dir).create_ai_project("operation_project", "Demo")
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.list()), 1)

    def test_two_store_instances_serialize_ai_mutations(self):
        saved = self.store.save("schema_one", record("schema_one"), expected_layout_token=None, layout_protocol=None)
        second_store = SchemaStore(self.schema_dir)
        entered = threading.Event()
        outcomes = []

        def slow_transform(current):
            entered.set()
            time.sleep(0.05)
            current["schema"]["projectName"] = "First"
            return current, {"actionType": "test", "changed": [], "impact": []}

        def first():
            outcomes.append(self.store.apply_ai_mutation("schema_one", "operation_one", 1, saved["layoutToken"], slow_transform))

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(SchemaStoreError) as error:
            second_store.apply_ai_mutation("schema_one", "operation_two", 1, saved["layoutToken"], lambda current: (current, {"actionType": "test", "changed": [], "impact": []}))
        thread.join(1)
        self.assertEqual(error.exception.payload["error"]["code"], "schema_conflict")
        self.assertEqual(len(outcomes), 1)


if __name__ == "__main__":
    unittest.main()
