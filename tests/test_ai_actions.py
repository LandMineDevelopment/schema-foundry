import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.ai_actions import normalize_schemii_action, normalize_schemer_action


class AiActionTests(unittest.TestCase):
    def test_schemii_query_is_canonical_and_allows_multiline_sql(self):
        action = {
            "action": "schema_read_query", "profileId": "local", "namespace": "public",
            "sql": "SELECT 1\nFROM demo", "purpose": "Inspect demo", "readOnly": True, "requiresApproval": True,
        }
        normalized = normalize_schemii_action(action, "data")
        self.assertEqual(normalized["type"], "schema_read_query")
        self.assertTrue(normalized["requiresConfirmation"])
        self.assertEqual(normalized["sql"], action["sql"])
        with self.assertRaises(ValueError):
            normalize_schemii_action({**action, "unknown": True}, "data")
        with self.assertRaises(ValueError):
            normalize_schemii_action(action, "schema")

    def test_schemii_client_commands_retain_review_metadata(self):
        project = normalize_schemii_action({
            "type": "open_project", "schemaId": "schema_one", "projectName": "Demo", "requiresConfirmation": True,
        }, "schema")
        self.assertTrue(project["requiresConfirmation"])
        connection = normalize_schemii_action({
            "type": "connection_setup", "name": "Demo", "host": "127.0.0.1", "port": 5432,
            "database": "demo", "user": "reader", "sslmode": "prefer",
            "requiresPasswordEntry": True, "requiresConfirmation": True,
        }, "schema")
        self.assertTrue(connection["requiresPasswordEntry"])
        self.assertNotIn("password", connection)

    def test_schemii_mutations_are_exact_and_canonical(self):
        action = {
            "type": "add_table", "name": "events", "purpose": "Store events",
            "columns": [{"name": "id", "type": "uuid", "primary": True}],
            "requiresConfirmation": True,
        }
        normalized = normalize_schemii_action(action, "schema")
        self.assertEqual(normalized["columns"][0]["name"], "id")
        with self.assertRaises(ValueError):
            normalize_schemii_action({**action, "unknown": True}, "schema")
        with self.assertRaises(ValueError):
            normalize_schemii_action({**action, "columns": [{"name": "id", "type": "uuid", "extra": True}]}, "schema")

        deletion = normalize_schemii_action({
            "type": "delete_element", "elementType": "column", "tableId": "table_one", "columnId": "col_one",
            "reason": "No longer used", "destructive": True, "requiresConfirmation": True,
        }, "schema")
        self.assertTrue(deletion["destructive"])

    def test_schemer_actions_are_exact_and_revision_bound(self):
        action = {
            "type": "dashboard_open", "dashboardId": "dashboard_one", "expectedRevision": 3,
            "title": "Demo", "requiresConfirmation": True,
        }
        self.assertEqual(normalize_schemer_action(action, "dashboard")["expectedRevision"], 3)
        with self.assertRaises(ValueError):
            normalize_schemer_action({**action, "expectedRevision": True}, "dashboard")


if __name__ == "__main__":
    unittest.main()
