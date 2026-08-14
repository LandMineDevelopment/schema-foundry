import hashlib
import sys
import unittest
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.metadata import MetadataStore, MetadataStoreError, canonical_review_digest
from schemii.metadata.migrator import packaged_migrations


class FakeCursor:
    def __init__(self, rows=None, rowcounts=None):
        self.rows = list(rows or [])
        self.rowcounts = list(rowcounts or [])
        self.rowcount = 1
        self.executions = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))
        self.rowcount = self.rowcounts.pop(0) if self.rowcounts else 1

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows.pop(0) if self.rows else []

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows=None, rowcounts=None):
        self.cursor_value = FakeCursor(rows, rowcounts)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


TARGET = {
    "profileId": "profile-1",
    "databaseName": "app",
    "namespaceName": "public",
    "profileFingerprint": "a" * 64,
    "connectedTargetFingerprint": "b" * 64,
}


class MetadataRepositoryMigrationTests(unittest.TestCase):
    def test_0002_is_additive_and_adds_repository_evidence(self):
        migrations = packaged_migrations()
        self.assertEqual([migration.version for migration in migrations], [1, 2])
        sql = resources.files("schemii.metadata.migrations").joinpath("0002_authority_repository.sql").read_text()
        self.assertIn("ADD COLUMN binding jsonb", sql)
        self.assertIn("lease_expires_at", sql)
        self.assertIn("reconciliation_status", sql)
        self.assertNotIn("FORCE ROW LEVEL SECURITY", sql)
        self.assertIn("metadata_proposal_snapshot_guard", sql)
        self.assertIn("metadata_migration_plan_snapshot_guard", sql)
        self.assertNotIn("DROP TABLE", sql)


class MetadataRepositoryContractTests(unittest.TestCase):
    def test_canonical_review_digest_is_order_independent(self):
        first = canonical_review_digest({"steps": [{"sql": "SELECT 1"}], "destructive": False})
        second = canonical_review_digest({"destructive": False, "steps": [{"sql": "SELECT 1"}]})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_plan_rejects_noncanonical_digest_before_database_access(self):
        called = False

        def connection_factory():
            nonlocal called
            called = True
            return FakeConnection()

        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(connection_factory).create_migration_plan(
                "schemii", "schema", "schema-1", 7, "layout-7", TARGET,
                "c" * 64, "d" * 64, {"desired": {}}, {"steps": []}, "0" * 64, False,
            )
        self.assertEqual(caught.exception.code, "review_digest_mismatch")
        self.assertFalse(called)

    def test_provisioning_retry_returns_same_saga_without_new_provider_owner(self):
        chat = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "chat_id": chat, "resource_kind": "schema", "resource_id": "schema-1", "state": "provisioning",
        }])
        result = MetadataStore(lambda: connection).provision_chat(
            "schemii", "schema", "schema-1", external_session_id="session-1",
        )
        self.assertEqual(result, {"chatId": str(chat), "state": "provisioning", "provisioningOwner": False})
        self.assertFalse(any("INSERT INTO metadata_chats" in sql for sql, _ in connection.cursor_value.executions))

    def test_active_chat_rejects_activation_against_different_target(self):
        connection = FakeConnection(rows=[
            {"application_id": "schemii", "state": "active"},
            {"target_id": uuid.uuid4(), "profile_id": "other", "database_name": "app",
             "namespace_name": "public", "profile_fingerprint": "a" * 64,
             "connected_target_fingerprint": "b" * 64},
        ])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).activate_chat(str(uuid.uuid4()), TARGET)
        self.assertEqual(caught.exception.code, "target_conflict")

    def test_proposal_creation_binds_current_policy_and_immutable_snapshots(self):
        connection = FakeConnection(rows=[
            {"application_id": "schemii", "state": "active"}, {"exists": 1},
        ])
        result = MetadataStore(lambda: connection).create_proposal(
            str(uuid.uuid4()), "schema.write", 3, {"resourceRevision": 8}, {"kind": "apply"},
        )
        self.assertEqual(result["policyRevision"], 3)
        insert = next(item for item in connection.cursor_value.executions if "INSERT INTO metadata_proposals" in item[0])
        self.assertIn('"resourceRevision":8', insert[1][4])
        self.assertIn('"kind":"apply"', insert[1][5])

    def test_create_plan_persists_exact_binding_and_canonical_digest(self):
        connection = FakeConnection()
        review = {"steps": [], "warnings": []}
        digest = canonical_review_digest(review)
        result = MetadataStore(lambda: connection).create_migration_plan(
            "schemii", "schema", "schema-1", 7, "layout-7", TARGET,
            "c" * 64, "d" * 64, {"desired": {}}, review, digest, True,
        )
        self.assertEqual(result["reviewDigest"], digest)
        insert = next(item for item in connection.cursor_value.executions if "INSERT INTO metadata_migration_plans" in item[0])
        self.assertEqual(insert[1][2:6], ("schema", "schema-1", 7, "layout-7"))
        self.assertEqual(insert[1][6:11], ("profile-1", "app", "public", "a" * 64, "b" * 64))

    def test_execution_authorization_is_atomic_and_idempotent(self):
        execution = uuid.uuid4()
        plan = uuid.uuid4()
        existing = FakeConnection(rows=[{
            "review_payload": {"steps": []}, "review_digest": canonical_review_digest({"steps": []}),
            "destructive": False, "state": "ready", "current": True,
        }, {"execution_id": execution, "state": "applying"}])
        result = MetadataStore(lambda: existing).create_migration_execution(
            str(plan), canonical_review_digest({"steps": []}), False,
        )
        self.assertFalse(result["executionOwner"])
        self.assertEqual(result["executionId"], str(execution))

    def test_begin_execution_records_target_evidence_before_applying_transition(self):
        execution = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "state": "ready", "target_xid": None, "target_identity": None,
            "intended_result": None, "commit_outcome": None,
            "reconciliation_status": "not_required", "reconciliation_evidence": None,
        }])
        result = MetadataStore(lambda: connection).begin_migration_execution(str(execution), "8123", {"databaseOid": 42})
        self.assertEqual(result["state"], "applying")
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("target_xid = %s", sql)
        self.assertIn("INSERT INTO metadata_migration_transitions", sql)

    def test_committed_execution_requires_intended_result(self):
        connection = FakeConnection(rows=[{
            "state": "applying", "target_xid": "1", "target_identity": {},
            "intended_result": None, "commit_outcome": None,
            "reconciliation_status": "not_required", "reconciliation_evidence": None,
        }])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).finish_migration_execution(
                str(uuid.uuid4()), "succeeded", "committed",
            )
        self.assertEqual(caught.exception.code, "intended_result_required")

    def test_uncertain_execution_reconciliation_records_proven_commit_without_replay(self):
        execution = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "state": "uncertain", "target_xid": "7", "target_identity": {"databaseOid": 4},
            "intended_result": {"fingerprint": "f"}, "commit_outcome": "uncertain",
            "reconciliation_status": "required", "reconciliation_evidence": None,
        }])
        result = MetadataStore(lambda: connection).reconcile_migration_execution(
            str(execution), "committed", {"xidStatus": "committed"},
        )
        self.assertEqual(result["state"], "succeeded")
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("reconciliation_status = 'reconciled'", sql)
        self.assertNotIn("private_payload", sql)

    def test_sync_outcome_is_separate_from_committed_execution(self):
        sync = uuid.uuid4()
        connection = FakeConnection(rows=[{
            "state": "succeeded", "target_xid": "7", "target_identity": {},
            "intended_result": {}, "commit_outcome": "committed",
            "reconciliation_status": "not_required", "reconciliation_evidence": None,
        }, {"sync_id": sync, "state": "pending", "receipt": None}])
        result = MetadataStore(lambda: connection).record_migration_sync(
            str(uuid.uuid4()), "conflict", receipt={"code": "layout_conflict"},
        )
        self.assertEqual(result["state"], "conflict")
        self.assertTrue(any("UPDATE metadata_migration_syncs" in sql for sql, _ in connection.cursor_value.executions))

    def test_stale_operation_is_abandoned_to_uncertain_not_ready(self):
        operation = uuid.uuid4()
        connection = FakeConnection(rows=[[
            {"attempt_id": uuid.uuid4(), "operation_id": operation, "chat_id": uuid.uuid4()},
        ], {"application_id": "schemii"}])
        result = MetadataStore(lambda: connection).abandon_stale_operations(
            stale_before=datetime.now(timezone.utc),
        )
        self.assertEqual(result, [str(operation)])
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("state = 'abandoned'", sql)
        self.assertIn("state = 'uncertain'", sql)
        self.assertNotIn("state = 'ready'", sql)

    def test_heartbeat_renews_live_lease_and_rejects_expired_update(self):
        token = "claim"
        row = {"operation_id": uuid.uuid4(), "state": "running",
               "claim_token_hash": hashlib.sha256(token.encode()).hexdigest(), "lease_expires_at": datetime.now(timezone.utc)}
        connection = FakeConnection(rows=[row], rowcounts=[1, 0])
        with self.assertRaises(MetadataStoreError) as caught:
            MetadataStore(lambda: connection).heartbeat_operation(str(uuid.uuid4()), token)
        self.assertEqual(caught.exception.code, "operation_lease_expired")

    def test_identical_terminal_operation_retry_is_idempotent(self):
        token = "claim"
        operation = uuid.uuid4()
        attempt = uuid.uuid4()
        result_payload = {"receipt": "ok"}
        connection = FakeConnection(rows=[{
            "operation_id": operation, "state": "succeeded",
            "claim_token_hash": hashlib.sha256(token.encode()).hexdigest(), "lease_expires_at": datetime.now(timezone.utc),
        }, {"state": "succeeded", "result": result_payload, "error": None}])
        result = MetadataStore(lambda: connection).finish_operation(
            str(attempt), token, "succeeded", result=result_payload,
        )
        self.assertFalse(result["resolutionOwner"])

    def test_stale_delivery_recovery_scrubs_post_dispatch_payload_and_audits(self):
        delivery = uuid.uuid4()
        result_ref = uuid.uuid4()
        connection = FakeConnection(rows=[[
            {"delivery_id": delivery, "result_ref_id": result_ref, "state": "delivering"},
        ], {"application_id": "schemii"}])
        now = datetime.now(timezone.utc)
        recovered = MetadataStore(lambda: connection).recover_stale_results(
            reserved_before=now, delivering_before=now,
        )
        self.assertEqual(recovered, {"released": [], "uncertain": [str(delivery)]})
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("scrubbed_at = clock_timestamp()", sql)
        self.assertIn("metadata_authority_transitions", sql)

    def test_cleanup_is_bounded_and_only_targets_terminal_or_unowned_rows(self):
        connection = FakeConnection(rowcounts=[2, 3, 4])
        result = MetadataStore(lambda: connection).cleanup(before=datetime.now(timezone.utc), limit=25)
        self.assertEqual(result, {"results": 2, "plans": 3, "chats": 4})
        self.assertTrue(all(params[1] == 25 for _, params in connection.cursor_value.executions))
        sql = "\n".join(item[0] for item in connection.cursor_value.executions)
        self.assertIn("state IN ('consumed', 'uncertain', 'expired')", sql)
        self.assertIn("e.execution_id IS NULL", sql)
        self.assertIn("state = 'deleted'", sql)


if __name__ == "__main__":
    unittest.main()
