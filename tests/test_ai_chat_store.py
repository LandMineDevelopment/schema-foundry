import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.ai_chat_store import AiChatStore, AiChatStoreError


APPROVALS = {
    "schema": "every_action", "structured": "every_action", "write": "every_action",
    "rawread": "every_action", "rawwrite": "every_action",
}
TARGET = {"profileId": "local", "database": "demo", "namespace": "public", "profileFingerprint": "abc"}


class AiChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = AiChatStore(Path(self.temporary_directory.name) / "chats")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_fixed_identity_and_mutable_policy_persist(self):
        created = self.store.create("ses_1", "schema_one", TARGET, ["schema"], APPROVALS)
        self.assertEqual(created["policyRevision"], 1)
        updated = self.store.update_policy("ses_1", ["schema", "structured"], {**APPROVALS, "structured": "automatic"}, 1)
        self.assertEqual(updated["policyRevision"], 2)
        self.assertEqual(self.store.get("ses_1"), updated)
        with self.assertRaises(AiChatStoreError) as error:
            self.store.update_policy("ses_1", ["schema"], APPROVALS, 1)
        self.assertEqual(error.exception.code, "chat_policy_changed")
        self.assertEqual(self.store.get("ses_1")["target"], TARGET)

    def test_schema_only_chat_has_an_explicit_empty_target(self):
        created = self.store.create("ses_schema", "schema_one", {}, ["schema"], APPROVALS)
        self.assertEqual(created["target"], {})
        self.assertEqual([item["id"] for item in self.store.list("schema_one")], ["ses_schema"])

    def test_capability_checks_and_delete_fail_closed(self):
        self.store.create("ses_1", "schema_one", TARGET, ["rawread"], APPROVALS)
        self.assertEqual(self.store.require_capability("ses_1", "rawread")["id"], "ses_1")
        with self.assertRaises(AiChatStoreError) as error:
            self.store.require_capability("ses_1", "rawwrite")
        self.assertEqual(error.exception.code, "capability_disabled")
        self.store.delete("ses_1")
        with self.assertRaises(AiChatStoreError):
            self.store.get("ses_1")

    def test_once_per_chat_grant_is_durable_and_cleared_by_policy_change(self):
        approvals = {**APPROVALS, "schema": "once_per_chat"}
        self.store.create("ses_1", "schema_one", TARGET, ["schema"], approvals)
        started = []
        operation, decision = self.store.authorize(
            "ses_1", "schema", 1, "once_per_chat", {"accepted": True, "mode": "once_per_chat"},
            lambda: started.append(True) or {"id": "operation_1"},
        )
        self.assertEqual(operation["id"], "operation_1")
        self.assertTrue(decision["grantCreated"])
        replacement = AiChatStore(self.store.root)
        _, granted = replacement.authorize("ses_1", "schema", 1, "once_per_chat", None, lambda: {"id": "operation_2"})
        self.assertEqual(granted["source"], "chat_grant")
        updated = replacement.update_policy("ses_1", ["schema"], APPROVALS, 1)
        self.assertEqual(updated["grants"], {})

    def test_automatic_is_server_only_and_targetless_data_policy_is_rejected(self):
        approvals = {**APPROVALS, "write": "automatic"}
        self.store.create("ses_1", "schema_one", TARGET, ["write"], approvals)
        _, decision = self.store.authorize("ses_1", "write", 1, "automatic", None, lambda: {"id": "operation_1"})
        self.assertEqual(decision["source"], "automatic")
        with self.assertRaises(AiChatStoreError):
            self.store.authorize("ses_1", "write", 1, "automatic", {"accepted": True, "mode": "automatic"}, lambda: {})
        self.store.create("ses_schema", "schema_one", {}, ["schema"], APPROVALS)
        with self.assertRaises(AiChatStoreError) as error:
            self.store.update_policy("ses_schema", ["schema", "write"], approvals, 1)
        self.assertEqual(error.exception.code, "chat_target_required")


if __name__ == "__main__":
    unittest.main()
