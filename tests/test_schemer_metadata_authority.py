import tempfile
import unittest
import uuid
from pathlib import Path

from schemii.schemer_metadata_authority import SchemerMetadataAuthority, retire_legacy_schemer_authority
from schemii.ai_policy import default_policy, effective_capabilities, policy_digest


class MetadataDouble:
    def __init__(self):
        self.chat_id = str(uuid.uuid4())
        self.calls = []
        self.current_policy = None

    def provision_chat(self, *args):
        self.calls.append(("provision", args))
        return {"chatId": self.chat_id}

    def activate_chat(self, *args, **kwargs):
        self.calls.append(("activate", args, kwargs))
        self.current_policy = kwargs["policy"]

    def get_agent_settings(self, application, agent):
        policy = default_policy(application)
        policy["capabilities"] = {name: "every_action" for name in policy["capabilities"]}
        return {
            "application": application, "agentId": agent, "revision": 1, "schemaVersion": 1,
            "policyRevisionId": str(uuid.UUID(int=2)), "policyDigest": policy_digest(policy),
            "capabilities": effective_capabilities(application, policy), "effectiveBounds": dict(policy["bounds"]),
        }

    def get_chat(self, chat_id):
        return {
            "chatId": chat_id, "resourceId": "dashboard_one", "externalSessionId": "external_one",
            "displayTitle": "Display title", "state": "active", "target": None,
        }

    def get_current_policy(self, chat_id):
        return {
            "revision": 1,
            "policy": self.current_policy or {"accessLevel": "dashboard", "capabilities": ["dashboard", "metadata"]},
            "agentPolicyRevisionId": str(uuid.UUID(int=2)) if self.current_policy else None,
        }


class SchemerMetadataAuthorityTests(unittest.TestCase):
    def test_activation_atomically_persists_explicit_policy_and_capabilities(self):
        metadata = MetadataDouble()
        authority = SchemerMetadataAuthority(metadata, worker_id="schemer-worker")

        provisioned = authority.provision_chat("dashboard_one")
        chat = authority.activate_chat(provisioned["chatId"], {}, "dashboard")

        self.assertEqual(metadata.calls[0], ("provision", ("schemer", "dashboard", "dashboard_one")))
        activation = metadata.calls[1]
        self.assertIsNone(activation[1][1])
        self.assertEqual(activation[2]["policy"]["disclosureClass"], "dashboard")
        self.assertEqual(activation[2]["capabilities"], {"structured_read": "deny", "dashboard_read": "approval", "dashboard_write": "approval"})
        self.assertEqual(activation[2]["agent_policy_binding"]["schemaVersion"], 1)
        self.assertEqual(chat["id"], metadata.chat_id)

    def test_data_target_uses_server_fingerprint_and_connected_binding(self):
        metadata = MetadataDouble()
        authority = SchemerMetadataAuthority(metadata, worker_id="schemer-worker")
        target = {
            "profileId": "analytics", "database": "reporting", "namespace": "public",
            "profileFingerprint": "a" * 64,
        }

        authority.activate_chat(metadata.chat_id, target, "data")

        stored = metadata.calls[0][1][1]
        self.assertEqual(stored["profileFingerprint"], "a" * 64)
        self.assertRegex(stored["connectedTargetFingerprint"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(stored["connectedTargetFingerprint"], stored["profileFingerprint"])

    def test_legacy_schemer_json_is_archived_without_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "ai_authority" / "v1" / "schemer"
            legacy.mkdir(parents=True)
            (legacy / "proposal.json").write_text('{"executable":true}', encoding="ascii")

            self.assertEqual(retire_legacy_schemer_authority(root), ["ai-authority-v1-schemer"])
            self.assertEqual(retire_legacy_schemer_authority(root), [])
            self.assertTrue((root / "retired-json-authority" / "ai-authority-v1-schemer" / "proposal.json").exists())
            self.assertIn("title bindings were retired without import", (root / "retired-json-authority" / "SCHEMER.txt").read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
