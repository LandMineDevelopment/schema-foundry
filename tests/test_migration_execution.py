import copy
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.migration_execution import DurableMigrationCoordinator
from schemii.metadata import MetadataStoreError
from schemii.postgres_common import PostgresServiceError, canonical_fingerprint
from schemii.schema_store import SchemaStoreError


IDENTITY = {"database": "demo", "databaseOid": "42", "serverVersionNum": "160000", "serverAddress": None, "serverPort": None}


class MemoryMetadata:
    def __init__(self, plan, *, fail_finalize=False, fail_sync=False):
        self.plan = copy.deepcopy(plan)
        self.execution = None
        self.events = []
        self.fail_finalize = fail_finalize
        self.fail_sync = fail_sync

    def create_migration_execution(self, plan_id, digest, confirmed):
        self.events.append("confirmation")
        if self.execution:
            return {"executionId": self.execution["executionId"], "state": self.execution["state"], "executionOwner": False}
        self.execution = {"executionId": str(uuid.uuid4()), "planId": plan_id, "state": "ready", "confirmedReviewDigest": digest,
                          "destructiveConfirmed": confirmed, "targetXid": None, "targetIdentity": None, "intendedResult": None,
                          "commitOutcome": None, "reconciliationStatus": "not_required", "reconciliationEvidence": None, "sync": None}
        return {"executionId": self.execution["executionId"], "state": "ready", "executionOwner": True}

    def get_migration_plan(self, plan_id, include_private=False):
        result = copy.deepcopy(self.plan)
        if not include_private:
            result.pop("privatePayload", None)
        return result

    def get_migration_status(self, plan_id):
        return {"plan": self.get_migration_plan(plan_id), "execution": copy.deepcopy(self.execution)}

    def get_migration_execution_context(self, execution_id):
        return {"plan": self.get_migration_plan(self.plan["planId"], include_private=True), "execution": copy.deepcopy(self.execution)}

    def get_migration_execution(self, execution_id):
        return copy.deepcopy(self.execution)

    def begin_migration_execution(self, execution_id, xid, identity):
        self.events.append("xid")
        self.execution.update(state="applying", targetXid=xid, targetIdentity=copy.deepcopy(identity))
        return {"transitionOwner": True}

    def record_migration_intended_result(self, execution_id, intended):
        self.events.append("intended")
        self.execution["intendedResult"] = copy.deepcopy(intended)
        return {"recordOwner": True}

    def finish_migration_execution(self, execution_id, state, outcome, evidence=None):
        self.events.append(state)
        if self.fail_finalize:
            self.fail_finalize = False
            raise MetadataStoreError("metadata_unavailable", "metadata unavailable", status=503)
        self.execution.update(state=state, commitOutcome=outcome, reconciliationStatus="required" if state == "uncertain" else "not_required")
        return {"transitionOwner": True}

    def prepare_migration_reconciliation(self, execution_id, evidence):
        manual = not self.execution.get("targetXid") or self.execution.get("targetIdentity") is None
        self.execution.update(state="uncertain", commitOutcome="uncertain",
                              reconciliationStatus="failed" if manual else "required",
                              reconciliationEvidence=copy.deepcopy(evidence))
        return {"state": "uncertain", "manualRequired": manual}

    def require_manual_migration_reconciliation(self, execution_id, evidence):
        self.execution.update(state="uncertain", commitOutcome="uncertain", reconciliationStatus="failed",
                              reconciliationEvidence=copy.deepcopy(evidence))
        return {"state": "uncertain", "manualRequired": True}

    def fail_migration_execution_before_mutation(self, execution_id, evidence):
        self.execution.update(state="failed", commitOutcome="rolled_back")

    def reconcile_migration_execution(self, execution_id, outcome, evidence):
        self.execution.update(state="succeeded" if outcome == "committed" else "failed", commitOutcome=outcome,
                              reconciliationStatus="reconciled", reconciliationEvidence=copy.deepcopy(evidence))

    def record_migration_sync(self, execution_id, state, receipt=None):
        if self.fail_sync:
            raise MetadataStoreError("metadata_unavailable", "metadata unavailable", status=503)
        self.events.append(f"sync:{state}")
        self.execution["sync"] = {"syncId": str(uuid.uuid4()), "state": state, "receipt": copy.deepcopy(receipt)}
        return self.execution["sync"]


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 2

    def execute(self, sql, params=()):
        self.connection.events.append((sql, params))
        if self.connection.fail_on and self.connection.fail_on in sql:
            raise RuntimeError("untrusted database failure")

    def close(self):
        pass


class Connection:
    def __init__(self, fail_commit=False, fail_on=None):
        self.events = []
        self.fail_commit = fail_commit
        self.rollbacks = 0
        self.commits = 0
        self.fail_on = fail_on

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("lost acknowledgement")

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class FakeService:
    _plan_ttl = 900

    def __init__(self, connection, *, transaction_status="committed"):
        self.connection = connection
        self.connects = 0
        self.mutations = 0
        self.catalog = {"postgres": {"fingerprint": "a" * 64, "database": "demo"}}
        self.transaction_status = transaction_status

    def _profile(self, profile_id):
        return {"dbname": "demo"}

    def _profile_fingerprint(self, profile):
        return "b" * 64

    def _connect_profile(self, profile):
        self.connects += 1
        return self.connection

    def _close(self, connection):
        connection.close()

    def _acquire_namespace_mutation_lock(self, cursor, namespace, database):
        cursor.execute("LOCK NAMESPACE")

    def _introspect_connection(self, connection, profile_id, namespace):
        return copy.deepcopy(self.catalog)

    def introspect(self, profile_id, namespace):
        return copy.deepcopy(self.catalog)

    def _execute_rows(self, connection, sql, params=()):
        if "pg_xact_status" in sql:
            return [{"status": self.transaction_status}]
        if "pg_current_xact_id" in sql:
            return [{"xid": "77"}]
        if "current_database" in sql:
            return [{"database": "demo", "database_oid": "42", "server_version_num": "160000", "server_address": None, "server_port": None}]
        return []


class FakeSchemas:
    def __init__(self, conflict=False):
        self.conflict = conflict

    def require_migration_binding(self, *args):
        return {"schema": {}}

    def sync_full_migration_result(self, *args):
        if self.conflict:
            raise SchemaStoreError(409, "schema_conflict", "changed")
        return {"status": "saved", "revision": 2, "layoutToken": "c" * 64}


def full_plan():
    plan_id = str(uuid.uuid4())
    review = {"adapterKind": "full_schema", "target": {"profileId": "local", "database": "demo", "namespace": "public"},
              "steps": [{"sql": "CREATE TABLE events(id integer);", "destructive": False}], "warnings": [], "destructive": False}
    return {"planId": plan_id, "applicationId": "schemii", "resourceKind": "schema", "resourceId": "schema_one",
            "resourceRevision": 1, "layoutToken": "c" * 64, "adapterKind": "full_schema", "sourceKind": "normal",
            "target": {"profileId": "local", "databaseName": "demo", "namespaceName": "public", "profileFingerprint": "b" * 64,
                       "connectedTargetFingerprint": canonical_fingerprint(IDENTITY)},
            "liveFingerprint": "a" * 64, "desiredFingerprint": "d" * 64, "reviewPayload": review,
            "reviewDigest": "e" * 64, "destructive": False, "state": "ready",
            "privatePayload": {"schemaBinding": {"schemaId": "schema_one", "revision": 1, "layoutToken": "c" * 64},
                               "desiredSchema": {}, "steps": review["steps"]}}


class DurableMigrationExecutionTests(unittest.TestCase):
    @staticmethod
    def interrupted_execution(metadata, *, state, intended=False):
        metadata.create_migration_execution(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        if state == "applying":
            metadata.begin_migration_execution(metadata.execution["executionId"], "77", IDENTITY)
            if intended:
                metadata.record_migration_intended_result(metadata.execution["executionId"], {
                    "kind": "migration_applied", "resultFingerprint": "a" * 64,
                })
        return metadata.execution["executionId"]

    def test_confirmation_xid_intended_and_commit_are_ordered_and_apply_is_single_owner(self):
        connection = Connection()
        metadata = MemoryMetadata(full_plan())
        coordinator = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        result = coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        duplicate = coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(duplicate["state"], "succeeded")
        self.assertEqual(connection.commits, 1)
        self.assertLess(metadata.events.index("confirmation"), metadata.events.index("xid"))
        self.assertLess(metadata.events.index("xid"), metadata.events.index("intended"))
        mutation_index = next(index for index, event in enumerate(connection.events) if event[0].startswith("CREATE TABLE"))
        xid_event_index = metadata.events.index("xid")
        self.assertGreater(mutation_index, 0)
        self.assertGreater(xid_event_index, metadata.events.index("confirmation"))

    def test_commit_exception_is_uncertain_and_never_claims_rollback(self):
        connection = Connection(fail_commit=True)
        metadata = MemoryMetadata(full_plan())
        coordinator = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "execution_outcome_unknown")
        self.assertEqual(metadata.execution["state"], "uncertain")
        self.assertEqual(connection.rollbacks, 0)

    def test_reconcile_uses_xact_status_without_replaying_sql_and_sync_conflict_does_not_change_commit(self):
        connection = Connection(fail_commit=True)
        metadata = MemoryMetadata(full_plan())
        service = FakeService(connection)
        coordinator = DurableMigrationCoordinator(service, metadata, FakeSchemas(conflict=True))
        with self.assertRaises(PostgresServiceError):
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        mutations_before = len([event for event in connection.events if event[0].startswith("CREATE TABLE")])
        connection.fail_commit = False
        result = coordinator.reconcile(metadata.execution["executionId"])
        mutations_after = len([event for event in connection.events if event[0].startswith("CREATE TABLE")])
        self.assertEqual((mutations_before, mutations_after), (1, 1))
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["execution"]["commitOutcome"], "committed")
        self.assertEqual(result["execution"]["sync"]["state"], "conflict")

    def test_restart_uses_same_metadata_plan_without_process_memory(self):
        metadata = MemoryMetadata(full_plan())
        first = DurableMigrationCoordinator(FakeService(Connection()), metadata, FakeSchemas())
        plan_id = metadata.plan["planId"]
        self.assertEqual(first.status(plan_id)["state"], "ready")
        restarted = DurableMigrationCoordinator(FakeService(Connection()), metadata, FakeSchemas())
        self.assertEqual(restarted.apply(plan_id, metadata.plan["reviewDigest"], False)["state"], "succeeded")

    def test_crash_after_confirmation_before_xid_closes_as_not_started_without_connecting(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="ready")
        service = FakeService(Connection())
        result = DurableMigrationCoordinator(service, metadata, FakeSchemas()).reconcile(execution_id)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["execution"]["commitOutcome"], "rolled_back")
        self.assertEqual(service.connects, 0)

    def test_crash_after_xid_before_intended_aborted_is_failed_without_replay(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="applying")
        connection = Connection()
        result = DurableMigrationCoordinator(
            FakeService(connection, transaction_status="aborted"), metadata, FakeSchemas(),
        ).reconcile(execution_id)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_crash_after_xid_before_intended_committed_requires_manual_recovery(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="applying")
        connection = Connection()
        result = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas()).reconcile(execution_id)
        self.assertEqual(result["state"], "uncertain")
        self.assertEqual(result["recovery"]["status"], "manual_required")
        self.assertEqual(result["execution"]["reconciliationStatus"], "failed")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_crash_after_intended_before_commit_reconciles_aborted_without_replay(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="applying", intended=True)
        connection = Connection()
        result = DurableMigrationCoordinator(
            FakeService(connection, transaction_status="aborted"), metadata, FakeSchemas(),
        ).reconcile(execution_id)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_crash_after_intended_and_commit_reconciles_success_and_sync_without_replay(self):
        metadata = MemoryMetadata(full_plan())
        execution_id = self.interrupted_execution(metadata, state="applying", intended=True)
        connection = Connection()
        result = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas()).reconcile(execution_id)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["execution"]["sync"]["state"], "succeeded")
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_metadata_finalize_failure_after_successful_commit_is_bounded_and_recoverable(self):
        connection = Connection()
        metadata = MemoryMetadata(full_plan(), fail_finalize=True)
        service = FakeService(connection)
        coordinator = DurableMigrationCoordinator(service, metadata, FakeSchemas())
        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "execution_outcome_unknown")
        self.assertEqual(caught.exception.details, {"executionId": metadata.execution["executionId"], "reconcileRequired": True})
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        mutations = len([event for event in connection.events if event[0].startswith("CREATE TABLE")])
        restarted = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        result = restarted.reconcile(metadata.execution["executionId"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(len([event for event in connection.events if event[0].startswith("CREATE TABLE")]), mutations)

    def test_stale_catalog_fails_before_xid_and_never_executes_mutation(self):
        connection = Connection()
        metadata = MemoryMetadata(full_plan())
        service = FakeService(connection)
        service.catalog["postgres"]["fingerprint"] = "9" * 64
        coordinator = DurableMigrationCoordinator(service, metadata, FakeSchemas())
        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "stale_plan")
        self.assertEqual(metadata.execution["state"], "failed")
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any(sql.startswith("CREATE TABLE") for sql, _ in connection.events))

    def test_mutation_failure_after_xid_is_proven_rolled_back_and_sanitized(self):
        connection = Connection(fail_on="CREATE TABLE")
        metadata = MemoryMetadata(full_plan())
        coordinator = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        with self.assertRaises(PostgresServiceError) as caught:
            coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(caught.exception.code, "apply_failed")
        self.assertNotIn("untrusted", caught.exception.message)
        self.assertEqual(metadata.execution["state"], "failed")
        self.assertEqual(metadata.execution["commitOutcome"], "rolled_back")
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 0)

    def test_post_commit_sync_metadata_failure_returns_recoverable_committed_status(self):
        connection = Connection()
        metadata = MemoryMetadata(full_plan(), fail_sync=True)
        coordinator = DurableMigrationCoordinator(FakeService(connection), metadata, FakeSchemas())
        result = coordinator.apply(metadata.plan["planId"], metadata.plan["reviewDigest"], False)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["execution"]["commitOutcome"], "committed")
        self.assertEqual(result["recovery"]["status"], "sync_pending")
        self.assertEqual(connection.rollbacks, 0)
        metadata.fail_sync = False
        recovered = coordinator.reconcile(metadata.execution["executionId"])
        self.assertEqual(recovered["execution"]["sync"]["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
