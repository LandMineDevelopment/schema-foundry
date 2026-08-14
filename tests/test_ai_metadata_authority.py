import tempfile
import unittest
import uuid
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.ai_metadata_authority import SchemiiMetadataAuthority, retire_legacy_schemii_authority


class MetadataDouble:
    def __init__(self):
        self.calls = []
        self.chat_id = str(uuid.uuid4())
        self.proposal_id = str(uuid.uuid4())
        self.operation_id = str(uuid.uuid4())

    def health(self): return {"ok": True, "version": 3, "expectedVersion": 3}
    def provision_chat(self, *args): self.calls.append(("provision_chat", args)); return {"chatId": self.chat_id}
    def bind_chat_external_session(self, *args): self.calls.append(("bind", args)); return {"chatId": args[0]}
    def activate_chat(self, *args, **kwargs): self.calls.append(("activate", args, kwargs)); return {"state": "active"}
    def get_chat(self, chat_id):
        return {
            "chatId": chat_id, "resourceId": "schema_one", "externalSessionId": "external_7",
            "displayTitle": "Display only", "state": "active", "target": None,
        }
    def get_current_policy(self, chat_id):
        return {"revision": 1, "policy": {"capabilities": ["schema"], "approvals": {name: "every_action" for name in ("schema", "structured", "write", "rawread", "rawwrite")}}}
    def list_grants(self, *args, **kwargs): return []
    def create_proposal(self, *args, **kwargs): self.calls.append(("proposal", args)); return {"proposalId": self.proposal_id}
    def get_proposal(self, proposal_id):
        return {
            "proposalId": proposal_id, "chatId": self.chat_id, "state": "ready", "action": {"type": "add_table"},
            "binding": {"policyBinding": {"capability": "schema", "policyRevision": 1, "configuredMode": "every_action", "effectiveMode": "every_action", "origin": "model"}, "authorizationTarget": {}, "schemaConcurrency": {"revision": 1}},
        }
    def authorize_and_create_operation(self, *args, **kwargs):
        self.calls.append(("authorize", args, kwargs))
        return {"operationId": self.operation_id, "state": "running", "executionOwner": True, "attemptId": str(uuid.uuid4()), "claimToken": "secret"}
    def get_operation(self, operation_id):
        return {"operationId": operation_id, "proposalId": self.proposal_id, "chatId": self.chat_id, "state": "running", "outcome": None}


class SchemiiMetadataAuthorityTests(unittest.TestCase):
    def test_targetless_activation_persists_external_session_and_initial_policy(self):
        metadata = MetadataDouble()
        authority = SchemiiMetadataAuthority(metadata, worker_id="worker-1")

        provisioned = authority.provision_chat("schema_one")
        authority.bind_external_session(provisioned["chatId"], "external_7", "Display only")
        chat = authority.activate_chat(
            provisioned["chatId"], {}, ["schema"],
            {name: "every_action" for name in ("schema", "structured", "write", "rawread", "rawwrite")},
        )

        activation = next(call for call in metadata.calls if call[0] == "activate")
        self.assertIsNone(activation[1][1])
        self.assertEqual(activation[2]["capabilities"]["schema"], "approval")
        self.assertEqual(activation[2]["capabilities"]["write"], "deny")
        self.assertEqual(chat["id"], metadata.chat_id)
        self.assertEqual(chat["externalSessionId"], "external_7")

    def test_authorization_creates_and_claims_one_attempt_in_the_same_store_call(self):
        metadata = MetadataDouble()
        authority = SchemiiMetadataAuthority(metadata, worker_id="worker-1")

        operation, _ = authority.authorize_and_claim(
            metadata.proposal_id, metadata.chat_id, 1,
            {"accepted": True, "mode": "every_action"},
        )

        call = next(item for item in metadata.calls if item[0] == "authorize")
        self.assertEqual(call[2]["worker_id"], "worker-1")
        self.assertTrue(call[2]["approved"])
        self.assertEqual(operation["claimToken"], "secret")
        self.assertTrue(operation["executionOwner"])

    def test_legacy_json_is_archived_idempotently_without_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chat = root / "ai_chats" / "v1"
            authority = root / "ai_authority" / "v1" / "schemii"
            chat.mkdir(parents=True)
            authority.mkdir(parents=True)
            (chat / "chat.json").write_text('{"executable":true}', encoding="ascii")
            (authority / "proposal.json").write_text('{"action":"drop"}', encoding="ascii")

            self.assertEqual(set(retire_legacy_schemii_authority(root)), {"ai-chats-v1", "ai-authority-v1-schemii"})
            self.assertEqual(retire_legacy_schemii_authority(root), [])
            self.assertTrue((root / "retired-json-authority" / "ai-chats-v1" / "chat.json").exists())
            self.assertIn("must never be executed", (root / "retired-json-authority" / "README.txt").read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
