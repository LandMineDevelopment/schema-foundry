from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from .errors import MetadataStoreError
from .migrator import MetadataMigrator, validate_applied_migrations
from .validation import bounded_json, identity


_TERMINAL_OPERATION_STATES = {"succeeded", "failed", "uncertain"}
_TERMINAL_MIGRATION_STATES = {"succeeded", "failed", "uncertain"}
_HEX_DIGEST = frozenset("0123456789abcdef")


class MetadataStore:
    """Transactional repository for shared server authority metadata."""

    def __init__(self, connection_factory: Callable[[], Any], *, max_json_bytes: int = 1024 * 1024):
        if not callable(connection_factory):
            raise ValueError("connection_factory must be callable")
        if isinstance(max_json_bytes, bool) or not 1024 <= max_json_bytes <= 1024 * 1024:
            raise ValueError("max_json_bytes must be between 1024 and 1048576")
        self.connection_factory = connection_factory
        self.max_json_bytes = max_json_bytes

    def migrate(self) -> int:
        return MetadataMigrator(self.connection_factory).migrate()

    def health(self) -> dict[str, Any]:
        migrator = MetadataMigrator(self.connection_factory)
        expected = migrator.expected_version
        try:
            with self._transaction(write=False) as cursor:
                cursor.execute("SELECT version, name, checksum FROM metadata_schema_migrations ORDER BY version")
                applied = validate_applied_migrations(cursor.fetchall(), migrator.migrations)
                version = len(applied)
        except MetadataStoreError:
            raise
        except Exception as exc:
            raise MetadataStoreError("metadata_unavailable", "Server metadata PostgreSQL is unavailable", status=503, retryable=True) from exc
        if version != expected:
            raise MetadataStoreError(
                "metadata_schema_outdated",
                "Server metadata schema is not current",
                status=503,
                details={"currentVersion": version, "expectedVersion": expected},
            )
        return {"ok": True, "version": version, "expectedVersion": expected}

    def provision_chat(
        self,
        application_id: str,
        resource_kind: str,
        resource_id: str,
        *,
        external_session_id: str | None = None,
    ) -> dict[str, Any]:
        application = identity(application_id, "application_id")
        kind = identity(resource_kind, "resource_kind")
        resource = _bounded_text(resource_id, "resource_id", 256)
        external = None if external_session_id is None else _bounded_text(external_session_id, "external_session_id", 512)
        chat = uuid.uuid4()
        with self._transaction() as cursor:
            if external is not None:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"{application}\0{external}",))
                cursor.execute(
                    """SELECT chat_id, resource_kind, resource_id, state FROM metadata_chats
                       WHERE application_id = %s AND external_session_id = %s FOR UPDATE""",
                    (application, external),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if (_row_value(existing, "resource_kind", 1), _row_value(existing, "resource_id", 2)) != (kind, resource):
                        raise MetadataStoreError("external_session_conflict", "External session belongs to another resource", status=409)
                    return {"chatId": str(_row_value(existing, "chat_id", 0)),
                            "state": _row_value(existing, "state", 3), "provisioningOwner": False}
            cursor.execute(
                """INSERT INTO metadata_chats
                   (chat_id, application_id, resource_kind, resource_id, external_session_id, state)
                   VALUES (%s, %s, %s, %s, %s, 'provisioning')""",
                (chat, application, kind, resource, external),
            )
            self._audit(cursor, application, "chat", chat, None, "provisioning", "provision_requested")
        return {"chatId": str(chat), "state": "provisioning", "provisioningOwner": True}

    def activate_chat(self, chat_id: str, target: dict[str, Any]) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        safe = _target(target)
        with self._transaction() as cursor:
            row = self._lock_chat(cursor, chat)
            state = _row_value(row, "state", 1)
            if state == "active":
                cursor.execute(
                    """SELECT target_id, profile_id, database_name, namespace_name,
                              profile_fingerprint, connected_target_fingerprint
                       FROM metadata_targets WHERE chat_id = %s""",
                    (chat,),
                )
                existing = cursor.fetchone()
                if existing is None:
                    raise MetadataStoreError("metadata_invariant", "Active chat has no target")
                stored = {
                    "profileId": _row_value(existing, "profile_id", 1),
                    "databaseName": _row_value(existing, "database_name", 2),
                    "namespaceName": _row_value(existing, "namespace_name", 3),
                    "profileFingerprint": _row_value(existing, "profile_fingerprint", 4),
                    "connectedTargetFingerprint": _row_value(existing, "connected_target_fingerprint", 5),
                }
                if stored != safe:
                    raise MetadataStoreError("target_conflict", "Chat is active for a different immutable target", status=409)
                return {"chatId": str(chat), "state": "active", "activationOwner": False}
            if state != "provisioning":
                raise MetadataStoreError("chat_transition_invalid", "Chat cannot be activated from its current state", status=409)
            target_id = uuid.uuid4()
            cursor.execute(
                """INSERT INTO metadata_targets
                   (target_id, chat_id, profile_id, database_name, namespace_name,
                    profile_fingerprint, connected_target_fingerprint)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (target_id, chat, safe["profileId"], safe["databaseName"], safe["namespaceName"],
                 safe["profileFingerprint"], safe["connectedTargetFingerprint"]),
            )
            cursor.execute("UPDATE metadata_chats SET state = 'active', updated_at = clock_timestamp() WHERE chat_id = %s", (chat,))
            self._audit(cursor, _row_value(row, "application_id", 0), "chat", chat, state, "active", "provision_succeeded")
        return {"chatId": str(chat), "targetId": str(target_id), "state": "active", "activationOwner": True}

    def fail_chat(self, chat_id: str, reason: str) -> dict[str, Any]:
        return self._transition_chat(chat_id, {"provisioning"}, "failed", reason)

    def begin_chat_deletion(self, chat_id: str, reason: str = "delete_requested") -> dict[str, Any]:
        return self._transition_chat(chat_id, {"active", "failed"}, "deleting", reason, idempotent=True)

    def mark_chat_deleted(self, chat_id: str, reason: str = "provider_deleted") -> dict[str, Any]:
        return self._transition_chat(chat_id, {"deleting"}, "deleted", reason, idempotent=True)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT c.chat_id, c.application_id, c.resource_kind, c.resource_id,
                          c.external_session_id, c.state, c.created_at, c.updated_at, c.deleted_at,
                          t.target_id, t.profile_id, t.database_name, t.namespace_name,
                          t.profile_fingerprint, t.connected_target_fingerprint
                   FROM metadata_chats c LEFT JOIN metadata_targets t USING (chat_id)
                   WHERE c.chat_id = %s""",
                (chat,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("chat_not_found", "Chat was not found", status=404)
        return _chat_record(row)

    def list_chats(
        self,
        *,
        resource_kind: str | None = None,
        resource_id: str | None = None,
        states: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        count = _limit(limit)
        kind = None if resource_kind is None else identity(resource_kind, "resource_kind")
        resource = None if resource_id is None else _bounded_text(resource_id, "resource_id", 256)
        allowed_states = [identity(state, "state") for state in (states or [])]
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT c.chat_id, c.application_id, c.resource_kind, c.resource_id,
                          c.external_session_id, c.state, c.created_at, c.updated_at, c.deleted_at,
                          t.target_id, t.profile_id, t.database_name, t.namespace_name,
                          t.profile_fingerprint, t.connected_target_fingerprint
                   FROM metadata_chats c LEFT JOIN metadata_targets t USING (chat_id)
                   WHERE (%s IS NULL OR c.resource_kind = %s)
                     AND (%s IS NULL OR c.resource_id = %s)
                     AND (cardinality(%s::text[]) = 0 OR c.state = ANY(%s::text[]))
                   ORDER BY c.created_at DESC, c.chat_id DESC LIMIT %s""",
                (kind, kind, resource, resource, allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_chat_record(row) for row in rows]

    def get_current_policy(self, chat_id: str) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT v.policy_version_id, v.revision, v.policy, v.created_at
                   FROM metadata_policy_versions v WHERE v.chat_id = %s
                   ORDER BY v.revision DESC LIMIT 1""",
                (chat,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("policy_not_found", "Chat policy was not found", status=404)
            policy_id = _row_value(row, "policy_version_id", 0)
            cursor.execute(
                "SELECT capability, grant_mode FROM metadata_capabilities WHERE policy_version_id = %s ORDER BY capability",
                (policy_id,),
            )
            capabilities = {_row_value(item, "capability", 0): _row_value(item, "grant_mode", 1) for item in cursor.fetchall()}
        return {"policyVersionId": str(policy_id), "revision": int(_row_value(row, "revision", 1)),
                "policy": _json_value(_row_value(row, "policy", 2)), "capabilities": capabilities,
                "createdAt": _row_value(row, "created_at", 3)}

    def list_grants(self, chat_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
        chat = _uuid(chat_id, "chat_id")
        if type(active_only) is not bool:
            raise MetadataStoreError("invalid_metadata", "active_only must be a boolean", status=400)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT grant_id, capability, policy_revision, state, expires_at, created_at, revoked_at
                   FROM metadata_grants WHERE chat_id = %s AND (NOT %s OR state = 'active')
                   ORDER BY created_at DESC, grant_id DESC""",
                (chat, active_only),
            )
            rows = cursor.fetchall()
        return [{"grantId": str(_row_value(row, "grant_id", 0)), "capability": _row_value(row, "capability", 1),
                 "policyRevision": int(_row_value(row, "policy_revision", 2)), "state": _row_value(row, "state", 3),
                 "expiresAt": _row_value(row, "expires_at", 4), "createdAt": _row_value(row, "created_at", 5),
                 "revokedAt": _row_value(row, "revoked_at", 6)} for row in rows]

    def update_policy(
        self,
        chat_id: str,
        expected_revision: int,
        policy: dict[str, Any],
        capabilities: dict[str, str],
    ) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise MetadataStoreError("invalid_metadata", "expected_revision is invalid", status=400)
        if not isinstance(capabilities, dict) or len(capabilities) > 1000:
            raise MetadataStoreError("invalid_metadata", "capabilities must be a bounded object", status=400)
        document = bounded_json(policy, "policy", self.max_json_bytes)
        modes = {identity(name, "capability"): mode for name, mode in capabilities.items()}
        if any(mode not in {"deny", "approval", "once_per_chat", "automatic"} for mode in modes.values()):
            raise MetadataStoreError("invalid_metadata", "capability grant mode is invalid", status=400)
        policy_id = uuid.uuid4()
        revision = expected_revision + 1
        with self._transaction() as cursor:
            cursor.execute("SELECT state FROM metadata_chats WHERE chat_id = %s FOR UPDATE", (chat,))
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("chat_not_found", "Chat was not found", status=404)
            if _row_value(row, "state", 0) != "active":
                raise MetadataStoreError("chat_inactive", "Chat is not active", status=409)
            cursor.execute("SELECT COALESCE(MAX(revision), 0) AS revision FROM metadata_policy_versions WHERE chat_id = %s", (chat,))
            current = int(_row_value(cursor.fetchone(), "revision", 0))
            if current != expected_revision:
                raise MetadataStoreError("policy_changed", "Chat policy changed; refresh required", status=409, details={"currentRevision": current})
            cursor.execute(
                "INSERT INTO metadata_policy_versions (policy_version_id, chat_id, revision, policy) VALUES (%s, %s, %s, %s::jsonb)",
                (policy_id, chat, revision, _json(document)),
            )
            for capability, mode in sorted(modes.items()):
                cursor.execute(
                    "INSERT INTO metadata_capabilities (capability_id, policy_version_id, capability, grant_mode) VALUES (%s, %s, %s, %s)",
                    (uuid.uuid4(), policy_id, capability, mode),
                )
            cursor.execute(
                """UPDATE metadata_grants SET state = 'revoked', revoked_at = clock_timestamp()
                   WHERE chat_id = %s AND state = 'active'
                     AND (policy_revision <> %s OR capability <> ALL(%s))""",
                (chat, revision, list(name for name, mode in modes.items() if mode == "once_per_chat")),
            )
            self._audit(cursor, self._application_for_chat(cursor, chat), "grant", policy_id, str(expected_revision), str(revision), "policy_updated")
        return {"chatId": str(chat), "revision": revision, "policyVersionId": str(policy_id)}

    def create_proposal(
        self,
        chat_id: str,
        capability: str,
        policy_revision: int,
        binding: dict[str, Any],
        action: dict[str, Any],
        *,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        capability_name = identity(capability, "capability")
        revision = _positive_int(policy_revision, "policy_revision")
        ttl = _seconds(ttl_seconds, "ttl_seconds", maximum=86400)
        safe_binding = bounded_json(binding, "binding", self.max_json_bytes)
        safe_action = bounded_json(action, "action", self.max_json_bytes)
        proposal = uuid.uuid4()
        with self._transaction() as cursor:
            chat_row = self._lock_chat(cursor, chat)
            if _row_value(chat_row, "state", 1) != "active":
                raise MetadataStoreError("chat_inactive", "Chat is not active", status=409)
            cursor.execute(
                """SELECT 1 FROM metadata_policy_versions v JOIN metadata_capabilities c USING (policy_version_id)
                   WHERE v.chat_id = %s AND v.revision = %s AND c.capability = %s
                     AND v.revision = (SELECT MAX(revision) FROM metadata_policy_versions WHERE chat_id = %s)""",
                (chat, revision, capability_name, chat),
            )
            if cursor.fetchone() is None:
                raise MetadataStoreError("policy_changed", "Proposal must bind the current policy capability", status=409)
            cursor.execute(
                """INSERT INTO metadata_proposals
                   (proposal_id, chat_id, capability, policy_revision, binding, action, expires_at)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb,
                           clock_timestamp() + (%s * interval '1 second'))""",
                (proposal, chat, capability_name, revision, _json(safe_binding), _json(safe_action), ttl),
            )
            self._audit(cursor, _row_value(chat_row, "application_id", 0), "proposal", proposal, None, "ready", "proposal_created")
        return {"proposalId": str(proposal), "chatId": str(chat), "state": "ready", "policyRevision": revision}

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = _uuid(proposal_id, "proposal_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT proposal_id, chat_id, capability, policy_revision, binding, action,
                          state, created_at, expires_at
                   FROM metadata_proposals WHERE proposal_id = %s""",
                (proposal,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("proposal_not_found", "Proposal was not found", status=404)
        return _proposal_record(row)

    def list_proposals(self, chat_id: str, *, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        chat = _uuid(chat_id, "chat_id")
        allowed_states = [identity(state, "state") for state in (states or [])]
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT proposal_id, chat_id, capability, policy_revision, binding, action,
                          state, created_at, expires_at
                   FROM metadata_proposals WHERE chat_id = %s
                     AND (cardinality(%s::text[]) = 0 OR state = ANY(%s::text[]))
                   ORDER BY created_at DESC, proposal_id DESC LIMIT %s""",
                (chat, allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_proposal_record(row) for row in rows]

    def authorize_and_create_operation(
        self,
        proposal_id: str,
        *,
        expected_policy_revision: int,
        approved: bool = False,
    ) -> dict[str, Any]:
        proposal = _uuid(proposal_id, "proposal_id")
        if isinstance(expected_policy_revision, bool) or not isinstance(expected_policy_revision, int) or expected_policy_revision <= 0:
            raise MetadataStoreError("invalid_metadata", "expected_policy_revision is invalid", status=400)
        if type(approved) is not bool:
            raise MetadataStoreError("invalid_metadata", "approved must be a boolean", status=400)
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT chat_id, capability, policy_revision, state,
                          expires_at > clock_timestamp() AS current
                   FROM metadata_proposals WHERE proposal_id = %s FOR UPDATE""",
                (proposal,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("proposal_not_found", "Proposal was not found", status=404)
            state = _row_value(row, "state", 3)
            if state == "authorized":
                cursor.execute("SELECT operation_id, state FROM metadata_operations WHERE proposal_id = %s", (proposal,))
                existing = cursor.fetchone()
                if existing is None:
                    raise MetadataStoreError("metadata_invariant", "Authorized proposal has no operation")
                return {"operationId": str(_row_value(existing, "operation_id", 0)), "state": _row_value(existing, "state", 1), "executionOwner": False}
            if state != "ready":
                raise MetadataStoreError("proposal_unavailable", "Proposal is not ready", status=409)
            if not _row_value(row, "current", 4):
                raise MetadataStoreError("proposal_expired", "Proposal has expired", status=409)
            chat_id = _row_value(row, "chat_id", 0)
            capability = _row_value(row, "capability", 1)
            cursor.execute("SELECT state FROM metadata_chats WHERE chat_id = %s FOR UPDATE", (chat_id,))
            chat_row = cursor.fetchone()
            if chat_row is None or _row_value(chat_row, "state", 0) != "active":
                raise MetadataStoreError("chat_inactive", "Chat is not active", status=409)
            cursor.execute(
                """SELECT c.grant_mode,
                          (SELECT MAX(current.revision) FROM metadata_policy_versions current WHERE current.chat_id = v.chat_id) AS current_revision,
                          (SELECT g.grant_id FROM metadata_grants g
                           WHERE g.chat_id = v.chat_id AND g.capability = c.capability AND g.state = 'active'
                             AND (g.expires_at IS NULL OR g.expires_at > clock_timestamp())) AS grant_id
                   FROM metadata_policy_versions v
                   JOIN metadata_capabilities c ON c.policy_version_id = v.policy_version_id
                   WHERE v.chat_id = %s AND v.revision = %s AND c.capability = %s""",
                (chat_id, _row_value(row, "policy_revision", 2), capability),
            )
            authority = cursor.fetchone()
            if authority is None:
                raise MetadataStoreError("policy_changed", "Proposal policy binding is stale", status=409)
            revision = int(_row_value(row, "policy_revision", 2))
            current_revision = int(_row_value(authority, "current_revision", 1))
            if revision != expected_policy_revision or revision != current_revision:
                raise MetadataStoreError("policy_changed", "Proposal policy binding is stale", status=409)
            mode = _row_value(authority, "grant_mode", 0)
            grant_id = _row_value(authority, "grant_id", 2)
            if mode == "deny" or (mode == "approval" and not approved) or (mode == "once_per_chat" and grant_id is None and not approved):
                raise MetadataStoreError("approval_required", "Proposal requires explicit approval", status=403)
            decision = "automatic" if mode == "automatic" else "grant" if grant_id is not None else "explicit"
            if mode == "once_per_chat" and grant_id is None:
                cursor.execute(
                    "INSERT INTO metadata_grants (grant_id, chat_id, capability, policy_revision) VALUES (%s, %s, %s, %s)",
                    (uuid.uuid4(), chat_id, capability, revision),
                )
            operation_id = uuid.uuid4()
            cursor.execute(
                "INSERT INTO metadata_operations (operation_id, proposal_id, chat_id, capability) VALUES (%s, %s, %s, %s)",
                (operation_id, proposal, chat_id, capability),
            )
            cursor.execute(
                "INSERT INTO metadata_operation_approvals (approval_id, operation_id, policy_revision, decision) VALUES (%s, %s, %s, %s)",
                (uuid.uuid4(), operation_id, revision, decision),
            )
            cursor.execute("UPDATE metadata_proposals SET state = 'authorized' WHERE proposal_id = %s", (proposal,))
            self._audit(cursor, self._application_for_chat(cursor, chat_id), "proposal", proposal, "ready", "authorized", "proposal_authorized")
            self._audit(cursor, self._application_for_chat(cursor, chat_id), "operation", operation_id, None, "ready", "operation_created")
        return {"operationId": str(operation_id), "state": "ready", "executionOwner": True}

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        operation = _uuid(operation_id, "operation_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT o.operation_id, o.proposal_id, o.chat_id, o.capability, o.state,
                          o.created_at, o.updated_at, a.attempt_id, a.worker_id, a.lease_expires_at,
                          x.state AS outcome_state, x.result, x.error
                   FROM metadata_operations o
                   LEFT JOIN metadata_operation_attempts a ON a.operation_id = o.operation_id AND a.state = 'running'
                   LEFT JOIN metadata_operation_outcomes x ON x.operation_id = o.operation_id
                   WHERE o.operation_id = %s""",
                (operation,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
        return _operation_record(row)

    def list_operations(self, chat_id: str, *, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        chat = _uuid(chat_id, "chat_id")
        allowed_states = [identity(state, "state") for state in (states or [])]
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT o.operation_id, o.proposal_id, o.chat_id, o.capability, o.state,
                          o.created_at, o.updated_at, a.attempt_id, a.worker_id, a.lease_expires_at,
                          x.state AS outcome_state, x.result, x.error
                   FROM metadata_operations o
                   LEFT JOIN metadata_operation_attempts a ON a.operation_id = o.operation_id AND a.state = 'running'
                   LEFT JOIN metadata_operation_outcomes x ON x.operation_id = o.operation_id
                   WHERE o.chat_id = %s AND (cardinality(%s::text[]) = 0 OR o.state = ANY(%s::text[]))
                   ORDER BY o.created_at DESC, o.operation_id DESC LIMIT %s""",
                (chat, allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_operation_record(row) for row in rows]

    def claim_operation(self, operation_id: str, worker_id: str, *, lease_seconds: int = 60) -> dict[str, Any]:
        operation = _uuid(operation_id, "operation_id")
        worker = identity(worker_id, "worker_id")
        token = secrets.token_urlsafe(32)
        attempt = uuid.uuid4()
        lease = _seconds(lease_seconds, "lease_seconds", maximum=3600)
        with self._transaction() as cursor:
            cursor.execute("SELECT state, chat_id FROM metadata_operations WHERE operation_id = %s FOR UPDATE", (operation,))
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
            if _row_value(row, "state", 0) != "ready":
                raise MetadataStoreError("operation_not_claimable", "Operation is not ready for execution", status=409)
            cursor.execute(
                """INSERT INTO metadata_operation_attempts
                   (attempt_id, operation_id, worker_id, claim_token_hash, lease_expires_at)
                   VALUES (%s, %s, %s, %s, clock_timestamp() + (%s * interval '1 second'))""",
                (attempt, operation, worker, _token_hash(token), lease),
            )
            cursor.execute("UPDATE metadata_operations SET state = 'running', updated_at = clock_timestamp() WHERE operation_id = %s", (operation,))
            self._audit(cursor, self._application_for_chat(cursor, _row_value(row, "chat_id", 1)), "operation", operation, "ready", "running", "operation_claimed")
        return {"attemptId": str(attempt), "claimToken": token, "state": "running"}

    def heartbeat_operation(self, attempt_id: str, claim_token: str, *, lease_seconds: int = 60) -> dict[str, Any]:
        return self._touch_attempt(attempt_id, claim_token, finish_state=None, result=None, error=None,
                                   lease_seconds=_seconds(lease_seconds, "lease_seconds", maximum=3600))

    def finish_operation(
        self,
        attempt_id: str,
        claim_token: str,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state not in _TERMINAL_OPERATION_STATES:
            raise MetadataStoreError("invalid_metadata", "operation finish state is invalid", status=400)
        safe_result = None if result is None else bounded_json(result, "result", self.max_json_bytes)
        safe_error = None if error is None else bounded_json(error, "error", self.max_json_bytes)
        if (state == "succeeded") != (safe_result is not None and safe_error is None):
            raise MetadataStoreError("invalid_metadata", "operation outcome payload does not match state", status=400)
        if state != "succeeded" and safe_error is None:
            raise MetadataStoreError("invalid_metadata", "failed or uncertain operation requires an error", status=400)
        return self._touch_attempt(attempt_id, claim_token, finish_state=state, result=safe_result, error=safe_error, lease_seconds=None)

    def abandon_stale_operations(self, *, stale_before: datetime, limit: int = 100) -> list[str]:
        cutoff = _aware_datetime(stale_before, "stale_before")
        count = _limit(limit)
        error = {"code": "lease_expired", "message": "Execution lease expired; reconcile without replay"}
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT a.attempt_id, a.operation_id, o.chat_id
                   FROM metadata_operation_attempts a JOIN metadata_operations o USING (operation_id)
                   WHERE a.state = 'running' AND a.lease_expires_at < %s
                   ORDER BY a.lease_expires_at FOR UPDATE OF a, o SKIP LOCKED LIMIT %s""",
                (cutoff, count),
            )
            rows = cursor.fetchall()
            for row in rows:
                attempt = _row_value(row, "attempt_id", 0)
                operation = _row_value(row, "operation_id", 1)
                cursor.execute("UPDATE metadata_operation_attempts SET state = 'abandoned', finished_at = clock_timestamp() WHERE attempt_id = %s", (attempt,))
                cursor.execute(
                    """INSERT INTO metadata_operation_outcomes (outcome_id, operation_id, state, error)
                       VALUES (%s, %s, 'uncertain', %s::jsonb) ON CONFLICT (operation_id) DO NOTHING""",
                    (uuid.uuid4(), operation, _json(error)),
                )
                cursor.execute("UPDATE metadata_operations SET state = 'uncertain', updated_at = clock_timestamp() WHERE operation_id = %s AND state = 'running'", (operation,))
                self._audit(cursor, self._application_for_chat(cursor, _row_value(row, "chat_id", 2)), "operation", operation, "running", "uncertain", "lease_abandoned_reconcile_only")
        return [str(_row_value(row, "operation_id", 1)) for row in rows]

    def resolve_uncertain_operation(
        self,
        operation_id: str,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = _uuid(operation_id, "operation_id")
        if state not in {"succeeded", "failed"}:
            raise MetadataStoreError("invalid_metadata", "resolution state is invalid", status=400)
        safe_result = None if result is None else bounded_json(result, "result", self.max_json_bytes)
        safe_error = None if error is None else bounded_json(error, "error", self.max_json_bytes)
        if (state == "succeeded") != (safe_result is not None and safe_error is None):
            raise MetadataStoreError("invalid_metadata", "resolution payload does not match state", status=400)
        if state == "failed" and safe_error is None:
            raise MetadataStoreError("invalid_metadata", "failed resolution requires an error", status=400)
        with self._transaction() as cursor:
            cursor.execute("SELECT state, chat_id FROM metadata_operations WHERE operation_id = %s FOR UPDATE", (operation,))
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
            current = _row_value(row, "state", 0)
            cursor.execute("SELECT state, result, error FROM metadata_operation_outcomes WHERE operation_id = %s FOR UPDATE", (operation,))
            outcome = cursor.fetchone()
            if current == state:
                if outcome is None or _json_value(_row_value(outcome, "result", 1)) != safe_result or _json_value(_row_value(outcome, "error", 2)) != safe_error:
                    raise MetadataStoreError("resolution_conflict", "Operation was resolved with a different outcome", status=409)
                return {"operationId": str(operation), "state": state, "resolutionOwner": False}
            if current != "uncertain":
                raise MetadataStoreError("operation_not_uncertain", "Only uncertain operations can be reconciled", status=409)
            cursor.execute("UPDATE metadata_operation_outcomes SET state = %s, result = %s::jsonb, error = %s::jsonb WHERE operation_id = %s",
                           (state, None if safe_result is None else _json(safe_result), None if safe_error is None else _json(safe_error), operation))
            cursor.execute("UPDATE metadata_operations SET state = %s, updated_at = clock_timestamp() WHERE operation_id = %s", (state, operation))
            self._audit(cursor, self._application_for_chat(cursor, _row_value(row, "chat_id", 1)), "operation", operation, "uncertain", state, "operation_reconciled")
        return {"operationId": str(operation), "state": state, "resolutionOwner": True}

    def create_result(self, chat_id: str, binding: dict[str, Any], payload: dict[str, Any], *, ttl_seconds: int = 300) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        if isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 86400:
            raise MetadataStoreError("invalid_metadata", "ttl_seconds is invalid", status=400)
        safe_binding = bounded_json(binding, "binding", self.max_json_bytes)
        safe_payload = bounded_json(payload, "payload", self.max_json_bytes)
        byte_count = len(_json(safe_payload).encode("utf-8"))
        result_ref = uuid.uuid4()
        with self._transaction() as cursor:
            cursor.execute("SELECT state FROM metadata_chats WHERE chat_id = %s FOR UPDATE", (chat,))
            chat_row = cursor.fetchone()
            if chat_row is None:
                raise MetadataStoreError("chat_not_found", "Chat was not found", status=404)
            if _row_value(chat_row, "state", 0) != "active":
                raise MetadataStoreError("chat_inactive", "Chat is not active", status=409)
            cursor.execute(
                """INSERT INTO metadata_query_result_references
                   (result_ref_id, chat_id, binding, expires_at)
                   VALUES (%s, %s, %s::jsonb, clock_timestamp() + (%s * interval '1 second'))""",
                (result_ref, chat, _json(safe_binding), ttl_seconds),
            )
            cursor.execute(
                "INSERT INTO metadata_query_result_payloads (result_ref_id, payload, byte_count) VALUES (%s, %s::jsonb, %s)",
                (result_ref, _json(safe_payload), byte_count),
            )
            self._audit(cursor, self._application_for_chat(cursor, chat), "result", result_ref, None, "ready", "result_created")
        return {"resultRefId": str(result_ref), "state": "ready"}

    def reserve_result(self, result_ref_id: str, chat_id: str, binding: dict[str, Any]) -> dict[str, Any]:
        result_ref = _uuid(result_ref_id, "result_ref_id")
        chat = _uuid(chat_id, "chat_id")
        safe_binding = bounded_json(binding, "binding", self.max_json_bytes)
        token = secrets.token_urlsafe(32)
        delivery = uuid.uuid4()
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT r.chat_id, r.binding, r.state, r.expires_at > clock_timestamp() AS current, p.payload
                   FROM metadata_query_result_references r JOIN metadata_query_result_payloads p USING (result_ref_id)
                   WHERE r.result_ref_id = %s FOR UPDATE OF r""",
                (result_ref,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("result_not_found", "Query result was not found", status=404)
            if str(_row_value(row, "chat_id", 0)) != str(chat) or _json_value(_row_value(row, "binding", 1)) != safe_binding:
                raise MetadataStoreError("result_binding_mismatch", "Query result binding does not match", status=403)
            if _row_value(row, "state", 2) != "ready" or not _row_value(row, "current", 3):
                raise MetadataStoreError("result_unavailable", "Query result is not available", status=409)
            cursor.execute(
                "INSERT INTO metadata_query_result_deliveries (delivery_id, result_ref_id, reservation_token_hash) VALUES (%s, %s, %s)",
                (delivery, result_ref, _token_hash(token)),
            )
            cursor.execute("UPDATE metadata_query_result_references SET state = 'reserved' WHERE result_ref_id = %s", (result_ref,))
            self._audit(cursor, self._application_for_chat(cursor, chat), "result", result_ref, "ready", "reserved", "delivery_reserved")
        return {"deliveryId": str(delivery), "reservationToken": token, "payload": _json_value(_row_value(row, "payload", 4)), "state": "reserved"}

    def begin_result_delivery(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "reserved", "delivering")

    def consume_result(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "delivering", "consumed", scrub=True)

    def release_result(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "reserved", "released")

    def mark_result_uncertain(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "delivering", "uncertain", scrub=True)

    def recover_stale_results(self, *, reserved_before: datetime, delivering_before: datetime, limit: int = 100) -> dict[str, list[str]]:
        reserved_cutoff = _aware_datetime(reserved_before, "reserved_before")
        delivering_cutoff = _aware_datetime(delivering_before, "delivering_before")
        count = _limit(limit)
        released: list[str] = []
        uncertain: list[str] = []
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT d.delivery_id, d.result_ref_id, d.state
                   FROM metadata_query_result_deliveries d
                   WHERE (d.state = 'reserved' AND d.reserved_at < %s)
                      OR (d.state = 'delivering' AND d.dispatch_started_at < %s)
                   ORDER BY d.reserved_at FOR UPDATE OF d SKIP LOCKED LIMIT %s""",
                (reserved_cutoff, delivering_cutoff, count),
            )
            rows = cursor.fetchall()
            for row in rows:
                delivery = _row_value(row, "delivery_id", 0)
                result_ref = _row_value(row, "result_ref_id", 1)
                cursor.execute("SELECT c.application_id FROM metadata_query_result_references r JOIN metadata_chats c USING (chat_id) WHERE r.result_ref_id = %s", (result_ref,))
                application = _row_value(cursor.fetchone(), "application_id", 0)
                if _row_value(row, "state", 2) == "reserved":
                    cursor.execute("UPDATE metadata_query_result_deliveries SET state = 'released', finished_at = clock_timestamp() WHERE delivery_id = %s", (delivery,))
                    cursor.execute("UPDATE metadata_query_result_references SET state = 'ready' WHERE result_ref_id = %s", (result_ref,))
                    self._audit(cursor, application, "result", result_ref, "reserved", "ready", "stale_reservation_released")
                    released.append(str(delivery))
                else:
                    cursor.execute("UPDATE metadata_query_result_deliveries SET state = 'uncertain', finished_at = clock_timestamp() WHERE delivery_id = %s", (delivery,))
                    cursor.execute("UPDATE metadata_query_result_references SET state = 'uncertain' WHERE result_ref_id = %s", (result_ref,))
                    cursor.execute("UPDATE metadata_query_result_payloads SET payload = '{}'::jsonb, byte_count = 2, scrubbed_at = clock_timestamp() WHERE result_ref_id = %s", (result_ref,))
                    self._audit(cursor, application, "result", result_ref, "delivering", "uncertain", "stale_delivery_uncertain")
                    uncertain.append(str(delivery))
        return {"released": released, "uncertain": uncertain}

    def create_migration_plan(
        self,
        application_id: str,
        resource_kind: str,
        resource_id: str,
        resource_revision: int,
        layout_token: str,
        target: dict[str, Any],
        live_fingerprint: str,
        desired_fingerprint: str,
        private_payload: dict[str, Any],
        review_payload: dict[str, Any],
        review_digest: str,
        destructive: bool,
        *,
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        application = identity(application_id, "application_id")
        kind = identity(resource_kind, "resource_kind")
        if kind not in {"schema", "view", "materialized_view"}:
            raise MetadataStoreError("invalid_metadata", "resource_kind is invalid", status=400)
        resource = _bounded_text(resource_id, "resource_id", 256)
        revision = _nonnegative_int(resource_revision, "resource_revision")
        layout = _bounded_text(layout_token, "layout_token", 256)
        safe_target = _target(target)
        live = _digest(live_fingerprint, "live_fingerprint")
        desired = _digest(desired_fingerprint, "desired_fingerprint")
        private = bounded_json(private_payload, "private_payload", self.max_json_bytes)
        review = bounded_json(review_payload, "review_payload", self.max_json_bytes)
        digest = _digest(review_digest, "review_digest")
        if not secrets.compare_digest(digest, canonical_review_digest(review)):
            raise MetadataStoreError("review_digest_mismatch", "Review digest does not match the canonical review payload", status=400)
        if type(destructive) is not bool:
            raise MetadataStoreError("invalid_metadata", "destructive must be a boolean", status=400)
        ttl = _seconds(ttl_seconds, "ttl_seconds", maximum=86400)
        plan = uuid.uuid4()
        with self._transaction() as cursor:
            cursor.execute(
                """INSERT INTO metadata_migration_plans
                   (plan_id, application_id, resource_kind, resource_id, resource_revision, layout_token,
                    profile_id, database_name, namespace_name, profile_fingerprint,
                    connected_target_fingerprint, live_fingerprint, desired_fingerprint,
                    private_payload, review_payload, review_digest, destructive, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s::jsonb, %s::jsonb, %s, %s,
                           clock_timestamp() + (%s * interval '1 second'))""",
                (plan, application, kind, resource, revision, layout, safe_target["profileId"],
                 safe_target["databaseName"], safe_target["namespaceName"], safe_target["profileFingerprint"],
                 safe_target["connectedTargetFingerprint"], live, desired, _json(private), _json(review), digest,
                 destructive, ttl),
            )
        return {"planId": str(plan), "state": "ready", "reviewDigest": digest, "expiresInSeconds": ttl}

    def get_migration_plan(self, plan_id: str, *, include_private: bool = False) -> dict[str, Any]:
        plan = _uuid(plan_id, "plan_id")
        if type(include_private) is not bool:
            raise MetadataStoreError("invalid_metadata", "include_private must be a boolean", status=400)
        with self._transaction(write=False) as cursor:
            cursor.execute("SELECT * FROM metadata_migration_plans WHERE plan_id = %s", (plan,))
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("plan_not_found", "Migration plan was not found", status=404)
        return _plan_record(row, include_private=include_private)

    def list_migration_plans(self, *, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        allowed_states = [identity(state, "state") for state in (states or [])]
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT * FROM metadata_migration_plans
                   WHERE cardinality(%s::text[]) = 0 OR state = ANY(%s::text[])
                   ORDER BY created_at DESC, plan_id DESC LIMIT %s""",
                (allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_plan_record(row, include_private=False) for row in rows]

    def create_migration_execution(
        self,
        plan_id: str,
        confirmed_review_digest: str,
        destructive_confirmed: bool,
    ) -> dict[str, Any]:
        plan = _uuid(plan_id, "plan_id")
        digest = _digest(confirmed_review_digest, "confirmed_review_digest")
        if type(destructive_confirmed) is not bool:
            raise MetadataStoreError("invalid_metadata", "destructive_confirmed must be a boolean", status=400)
        with self._transaction() as cursor:
            cursor.execute(
                """SELECT review_payload, review_digest, destructive, state,
                          expires_at > clock_timestamp() AS current
                   FROM metadata_migration_plans WHERE plan_id = %s FOR UPDATE""",
                (plan,),
            )
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("plan_not_found", "Migration plan was not found", status=404)
            stored_digest = _row_value(row, "review_digest", 1)
            canonical = canonical_review_digest(_json_value(_row_value(row, "review_payload", 0)))
            if not secrets.compare_digest(stored_digest, canonical) or not secrets.compare_digest(digest, stored_digest):
                raise MetadataStoreError("review_digest_mismatch", "Confirmed review does not match the durable plan", status=409)
            if _row_value(row, "destructive", 2) and not destructive_confirmed:
                raise MetadataStoreError("destructive_confirmation_required", "Destructive plan requires explicit confirmation", status=403)
            cursor.execute("SELECT execution_id, state FROM metadata_migration_executions WHERE plan_id = %s", (plan,))
            existing = cursor.fetchone()
            if existing is not None:
                return {"executionId": str(_row_value(existing, "execution_id", 0)), "state": _row_value(existing, "state", 1), "executionOwner": False}
            if _row_value(row, "state", 3) != "ready" or not _row_value(row, "current", 4):
                raise MetadataStoreError("plan_expired", "Migration plan has expired", status=409)
            execution = uuid.uuid4()
            cursor.execute(
                """INSERT INTO metadata_migration_executions
                   (execution_id, plan_id, confirmed_review_digest, destructive_confirmed)
                   VALUES (%s, %s, %s, %s)""",
                (execution, plan, digest, destructive_confirmed),
            )
            self._migration_transition(cursor, execution, None, "ready", {"reviewDigest": digest, "destructiveConfirmed": destructive_confirmed})
        return {"executionId": str(execution), "state": "ready", "executionOwner": True}

    def begin_migration_execution(self, execution_id: str, target_xid: str, target_identity: dict[str, Any]) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        xid = _bounded_text(target_xid, "target_xid", 128)
        target = bounded_json(target_identity, "target_identity", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            state = _row_value(row, "state", 0)
            if state == "applying":
                if _row_value(row, "target_xid", 1) != xid or _json_value(_row_value(row, "target_identity", 2)) != target:
                    raise MetadataStoreError("execution_evidence_conflict", "Execution already has different target evidence", status=409)
                return {"executionId": str(execution), "state": state, "transitionOwner": False}
            if state != "ready":
                raise MetadataStoreError("execution_transition_invalid", "Execution cannot begin from its current state", status=409)
            cursor.execute(
                """UPDATE metadata_migration_executions SET state = 'applying', target_xid = %s,
                          target_identity = %s::jsonb, updated_at = clock_timestamp()
                   WHERE execution_id = %s""",
                (xid, _json(target), execution),
            )
            self._migration_transition(cursor, execution, "ready", "applying", {"targetXid": xid, "targetIdentity": target})
        return {"executionId": str(execution), "state": "applying", "transitionOwner": True}

    def record_migration_intended_result(self, execution_id: str, intended_result: dict[str, Any]) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        intended = bounded_json(intended_result, "intended_result", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            if _row_value(row, "state", 0) != "applying":
                raise MetadataStoreError("execution_transition_invalid", "Intended result requires an applying execution", status=409)
            existing = _json_value(_row_value(row, "intended_result", 3))
            if existing is not None:
                if existing != intended:
                    raise MetadataStoreError("execution_evidence_conflict", "Execution already has a different intended result", status=409)
                return {"executionId": str(execution), "state": "applying", "recordOwner": False}
            cursor.execute("UPDATE metadata_migration_executions SET intended_result = %s::jsonb, updated_at = clock_timestamp() WHERE execution_id = %s",
                           (_json(intended), execution))
        return {"executionId": str(execution), "state": "applying", "recordOwner": True}

    def finish_migration_execution(
        self,
        execution_id: str,
        state: str,
        commit_outcome: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        if state not in _TERMINAL_MIGRATION_STATES or commit_outcome not in {"committed", "rolled_back", "uncertain"}:
            raise MetadataStoreError("invalid_metadata", "migration outcome is invalid", status=400)
        if (state, commit_outcome) not in {("succeeded", "committed"), ("failed", "rolled_back"), ("uncertain", "uncertain")}:
            raise MetadataStoreError("invalid_metadata", "migration state does not match commit outcome", status=400)
        safe_evidence = None if evidence is None else bounded_json(evidence, "evidence", self.max_json_bytes)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            current = _row_value(row, "state", 0)
            current_outcome = _row_value(row, "commit_outcome", 4)
            if current == state:
                if current_outcome != commit_outcome:
                    raise MetadataStoreError("execution_outcome_conflict", "Execution has a different terminal outcome", status=409)
                return {"executionId": str(execution), "state": state, "transitionOwner": False}
            if current != "applying":
                raise MetadataStoreError("execution_transition_invalid", "Execution is not applying", status=409)
            if state == "succeeded" and _row_value(row, "intended_result", 3) is None:
                raise MetadataStoreError("intended_result_required", "Committed execution requires a durable intended result", status=409)
            reconciliation = "required" if state == "uncertain" else "not_required"
            cursor.execute(
                """UPDATE metadata_migration_executions SET state = %s, commit_outcome = %s,
                          reconciliation_status = %s, updated_at = clock_timestamp()
                   WHERE execution_id = %s""",
                (state, commit_outcome, reconciliation, execution),
            )
            self._migration_transition(cursor, execution, "applying", state, safe_evidence)
        return {"executionId": str(execution), "state": state, "commitOutcome": commit_outcome, "transitionOwner": True}

    def reconcile_migration_execution(
        self,
        execution_id: str,
        commit_outcome: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        if commit_outcome not in {"committed", "rolled_back"}:
            raise MetadataStoreError("invalid_metadata", "reconciled commit outcome is invalid", status=400)
        safe_evidence = bounded_json(evidence, "evidence", self.max_json_bytes)
        target_state = "succeeded" if commit_outcome == "committed" else "failed"
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            current = _row_value(row, "state", 0)
            status = _row_value(row, "reconciliation_status", 5)
            if current == target_state and status == "reconciled":
                if _row_value(row, "commit_outcome", 4) != commit_outcome or _json_value(_row_value(row, "reconciliation_evidence", 6)) != safe_evidence:
                    raise MetadataStoreError("reconciliation_conflict", "Execution was reconciled with different evidence", status=409)
                return {"executionId": str(execution), "state": target_state, "commitOutcome": commit_outcome, "reconciliationOwner": False}
            if current != "uncertain" or status not in {"required", "reconciling"}:
                raise MetadataStoreError("reconciliation_not_required", "Execution is not awaiting reconciliation", status=409)
            if commit_outcome == "committed" and _row_value(row, "intended_result", 3) is None:
                raise MetadataStoreError("intended_result_required", "Committed reconciliation requires a durable intended result", status=409)
            cursor.execute(
                """UPDATE metadata_migration_executions SET state = %s, commit_outcome = %s,
                          reconciliation_status = 'reconciled', reconciliation_evidence = %s::jsonb,
                          updated_at = clock_timestamp() WHERE execution_id = %s""",
                (target_state, commit_outcome, _json(safe_evidence), execution),
            )
            self._migration_transition(cursor, execution, "uncertain", target_state, safe_evidence)
        return {"executionId": str(execution), "state": target_state, "commitOutcome": commit_outcome, "reconciliationOwner": True}

    def record_migration_sync(
        self,
        execution_id: str,
        state: str,
        *,
        receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        if state not in {"pending", "succeeded", "conflict", "failed"}:
            raise MetadataStoreError("invalid_metadata", "sync state is invalid", status=400)
        safe_receipt = None if receipt is None else bounded_json(receipt, "receipt", self.max_json_bytes)
        if state != "pending" and safe_receipt is None:
            raise MetadataStoreError("invalid_metadata", "terminal sync requires a receipt", status=400)
        with self._transaction() as cursor:
            row = self._lock_execution(cursor, execution)
            if _row_value(row, "state", 0) != "succeeded" or _row_value(row, "commit_outcome", 4) != "committed":
                raise MetadataStoreError("sync_not_allowed", "Only committed executions can synchronize resources", status=409)
            cursor.execute("SELECT sync_id, state, receipt FROM metadata_migration_syncs WHERE execution_id = %s FOR UPDATE", (execution,))
            existing = cursor.fetchone()
            if existing is None:
                sync = uuid.uuid4()
                cursor.execute("INSERT INTO metadata_migration_syncs (sync_id, execution_id, state, receipt) VALUES (%s, %s, %s, %s::jsonb)",
                               (sync, execution, state, None if safe_receipt is None else _json(safe_receipt)))
                return {"syncId": str(sync), "executionId": str(execution), "state": state, "transitionOwner": True}
            sync = _row_value(existing, "sync_id", 0)
            current = _row_value(existing, "state", 1)
            if current == state:
                if _json_value(_row_value(existing, "receipt", 2)) != safe_receipt:
                    raise MetadataStoreError("sync_conflict", "Sync state already has a different receipt", status=409)
                return {"syncId": str(sync), "executionId": str(execution), "state": state, "transitionOwner": False}
            if current != "pending" or state == "pending":
                raise MetadataStoreError("sync_transition_invalid", "Sync cannot transition from its current state", status=409)
            cursor.execute("UPDATE metadata_migration_syncs SET state = %s, receipt = %s::jsonb, updated_at = clock_timestamp() WHERE sync_id = %s",
                           (state, _json(safe_receipt), sync))
        return {"syncId": str(sync), "executionId": str(execution), "state": state, "transitionOwner": True}

    def get_migration_execution(self, execution_id: str) -> dict[str, Any]:
        execution = _uuid(execution_id, "execution_id")
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT e.*, s.sync_id, s.state AS sync_state, s.receipt AS sync_receipt
                   FROM metadata_migration_executions e
                   LEFT JOIN metadata_migration_syncs s USING (execution_id)
                   WHERE e.execution_id = %s""",
                (execution,),
            )
            row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("execution_not_found", "Migration execution was not found", status=404)
        return _execution_record(row)

    def list_migration_executions(self, *, states: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        allowed_states = [identity(state, "state") for state in (states or [])]
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            cursor.execute(
                """SELECT e.*, s.sync_id, s.state AS sync_state, s.receipt AS sync_receipt
                   FROM metadata_migration_executions e LEFT JOIN metadata_migration_syncs s USING (execution_id)
                   WHERE cardinality(%s::text[]) = 0 OR e.state = ANY(%s::text[])
                   ORDER BY e.created_at DESC, e.execution_id DESC LIMIT %s""",
                (allowed_states, allowed_states, count),
            )
            rows = cursor.fetchall()
        return [_execution_record(row) for row in rows]

    def list_transitions(self, aggregate_kind: str, aggregate_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        kind = identity(aggregate_kind, "aggregate_kind")
        aggregate = _uuid(aggregate_id, "aggregate_id")
        count = _limit(limit)
        with self._transaction(write=False) as cursor:
            if kind == "migration":
                cursor.execute("SELECT transition_id, from_state, to_state, evidence, created_at FROM metadata_migration_transitions WHERE execution_id = %s ORDER BY transition_id DESC LIMIT %s", (aggregate, count))
            elif kind in {"chat", "grant", "proposal", "operation", "result"}:
                cursor.execute("SELECT transition_id, from_state, to_state, reason, created_at FROM metadata_authority_transitions WHERE aggregate_kind = %s AND aggregate_id = %s ORDER BY transition_id DESC LIMIT %s", (kind, aggregate, count))
            else:
                raise MetadataStoreError("invalid_metadata", "aggregate_kind is invalid", status=400)
            rows = cursor.fetchall()
        if kind == "migration":
            return [{"transitionId": int(_row_value(row, "transition_id", 0)), "fromState": _row_value(row, "from_state", 1),
                     "toState": _row_value(row, "to_state", 2), "evidence": _json_value(_row_value(row, "evidence", 3)),
                     "createdAt": _row_value(row, "created_at", 4)} for row in rows]
        return [{"transitionId": int(_row_value(row, "transition_id", 0)), "fromState": _row_value(row, "from_state", 1),
                 "toState": _row_value(row, "to_state", 2), "reason": _row_value(row, "reason", 3),
                 "createdAt": _row_value(row, "created_at", 4)} for row in rows]

    def cleanup(self, *, before: datetime, limit: int = 1000) -> dict[str, int]:
        cutoff = _aware_datetime(before, "before")
        count = _limit(limit, maximum=10000)
        deleted: dict[str, int] = {}
        with self._transaction() as cursor:
            for name, sql in (
                ("results", "DELETE FROM metadata_query_result_references WHERE result_ref_id IN (SELECT result_ref_id FROM metadata_query_result_references WHERE state IN ('consumed', 'uncertain', 'expired') AND expires_at < %s ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED)"),
                ("plans", "DELETE FROM metadata_migration_plans WHERE plan_id IN (SELECT p.plan_id FROM metadata_migration_plans p LEFT JOIN metadata_migration_executions e USING (plan_id) WHERE e.execution_id IS NULL AND p.expires_at < %s ORDER BY p.expires_at LIMIT %s FOR UPDATE OF p SKIP LOCKED)"),
                ("chats", "DELETE FROM metadata_chats WHERE chat_id IN (SELECT chat_id FROM metadata_chats WHERE state = 'deleted' AND deleted_at < %s ORDER BY deleted_at LIMIT %s FOR UPDATE SKIP LOCKED)"),
            ):
                cursor.execute(sql, (cutoff, count))
                deleted[name] = max(0, int(cursor.rowcount))
        return deleted

    def _transition_chat(self, chat_id: str, allowed: set[str], target: str, reason: str, *, idempotent: bool = False) -> dict[str, Any]:
        chat = _uuid(chat_id, "chat_id")
        safe_reason = _bounded_text(reason, "reason", 256)
        with self._transaction() as cursor:
            row = self._lock_chat(cursor, chat)
            current = _row_value(row, "state", 1)
            if idempotent and current == target:
                return {"chatId": str(chat), "state": target, "transitionOwner": False}
            if current not in allowed:
                raise MetadataStoreError("chat_transition_invalid", "Chat cannot transition from its current state", status=409)
            cursor.execute(
                """UPDATE metadata_chats SET state = %s, updated_at = clock_timestamp(),
                          deleted_at = CASE WHEN %s = 'deleted' THEN clock_timestamp() ELSE deleted_at END
                   WHERE chat_id = %s""",
                (target, target, chat),
            )
            self._audit(cursor, _row_value(row, "application_id", 0), "chat", chat, current, target, safe_reason)
        return {"chatId": str(chat), "state": target, "transitionOwner": True}

    def _lock_chat(self, cursor: Any, chat_id: uuid.UUID) -> Any:
        cursor.execute("SELECT application_id, state FROM metadata_chats WHERE chat_id = %s FOR UPDATE", (chat_id,))
        row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("chat_not_found", "Chat was not found", status=404)
        return row

    def _application_for_chat(self, cursor: Any, chat_id: Any) -> str:
        cursor.execute("SELECT application_id FROM metadata_chats WHERE chat_id = %s", (chat_id,))
        row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("metadata_invariant", "Authority aggregate has no chat")
        return str(_row_value(row, "application_id", 0))

    def _audit(self, cursor: Any, application_id: str, kind: str, aggregate_id: Any, from_state: str | None, to_state: str, reason: str) -> None:
        cursor.execute(
            """INSERT INTO metadata_authority_transitions
               (application_id, aggregate_kind, aggregate_id, from_state, to_state, reason)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (application_id, kind, aggregate_id, from_state, to_state, reason),
        )

    def _lock_execution(self, cursor: Any, execution_id: uuid.UUID) -> Any:
        cursor.execute(
            """SELECT state, target_xid, target_identity, intended_result, commit_outcome,
                      reconciliation_status, reconciliation_evidence
               FROM metadata_migration_executions WHERE execution_id = %s FOR UPDATE""",
            (execution_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise MetadataStoreError("execution_not_found", "Migration execution was not found", status=404)
        return row

    def _migration_transition(self, cursor: Any, execution_id: uuid.UUID, from_state: str | None, to_state: str, evidence: Any) -> None:
        cursor.execute(
            "INSERT INTO metadata_migration_transitions (execution_id, from_state, to_state, evidence) VALUES (%s, %s, %s, %s::jsonb)",
            (execution_id, from_state, to_state, None if evidence is None else _json(evidence)),
        )

    def _touch_attempt(self, attempt_id: str, token: str, *, finish_state: str | None, result: Any, error: Any, lease_seconds: int | None) -> dict[str, Any]:
        attempt = _uuid(attempt_id, "attempt_id")
        with self._transaction() as cursor:
            cursor.execute("SELECT operation_id, state, claim_token_hash, lease_expires_at FROM metadata_operation_attempts WHERE attempt_id = %s FOR UPDATE", (attempt,))
            row = cursor.fetchone()
            if row is None or not secrets.compare_digest(str(_row_value(row, "claim_token_hash", 2)), _token_hash(token)):
                raise MetadataStoreError("invalid_claim", "Execution claim is invalid", status=409)
            if _row_value(row, "state", 1) != "running":
                if finish_state is not None and _row_value(row, "state", 1) == finish_state:
                    operation_id = _row_value(row, "operation_id", 0)
                    cursor.execute("SELECT state, result, error FROM metadata_operation_outcomes WHERE operation_id = %s", (operation_id,))
                    outcome = cursor.fetchone()
                    if outcome is not None and _json_value(_row_value(outcome, "result", 1)) == result and _json_value(_row_value(outcome, "error", 2)) == error:
                        return {"operationId": str(operation_id), "attemptId": str(attempt), "state": finish_state, "resolutionOwner": False}
                raise MetadataStoreError("operation_not_running", "Execution attempt is no longer running", status=409)
            operation_id = _row_value(row, "operation_id", 0)
            if finish_state is None:
                cursor.execute(
                    """UPDATE metadata_operation_attempts SET heartbeat_at = clock_timestamp(),
                              lease_expires_at = clock_timestamp() + (%s * interval '1 second')
                       WHERE attempt_id = %s AND lease_expires_at >= clock_timestamp()""",
                    (lease_seconds, attempt),
                )
                if cursor.rowcount != 1:
                    raise MetadataStoreError("operation_lease_expired", "Execution lease expired; reconcile without replay", status=409)
                return {"attemptId": str(attempt), "state": "running"}
            cursor.execute(
                """UPDATE metadata_operation_attempts SET state = %s, heartbeat_at = clock_timestamp(),
                          finished_at = clock_timestamp() WHERE attempt_id = %s
                       AND lease_expires_at >= clock_timestamp()""",
                (finish_state, attempt),
            )
            if cursor.rowcount != 1:
                raise MetadataStoreError("operation_lease_expired", "Execution lease expired; reconcile without replay", status=409)
            cursor.execute(
                "INSERT INTO metadata_operation_outcomes (outcome_id, operation_id, state, result, error) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)",
                (uuid.uuid4(), operation_id, finish_state, None if result is None else _json(result), None if error is None else _json(error)),
            )
            cursor.execute("UPDATE metadata_operations SET state = %s, updated_at = clock_timestamp() WHERE operation_id = %s", (finish_state, operation_id))
            cursor.execute("SELECT chat_id FROM metadata_operations WHERE operation_id = %s", (operation_id,))
            chat_id = _row_value(cursor.fetchone(), "chat_id", 0)
            self._audit(cursor, self._application_for_chat(cursor, chat_id), "operation", operation_id, "running", finish_state, "operation_finished")
        return {"operationId": str(operation_id), "attemptId": str(attempt), "state": finish_state}

    def _result_transition(self, delivery_id: str, token: str, required: str, target: str, *, scrub: bool = False) -> dict[str, Any]:
        delivery = _uuid(delivery_id, "delivery_id")
        with self._transaction() as cursor:
            cursor.execute("SELECT result_ref_id, state, reservation_token_hash FROM metadata_query_result_deliveries WHERE delivery_id = %s FOR UPDATE", (delivery,))
            row = cursor.fetchone()
            if row is None or not secrets.compare_digest(str(_row_value(row, "reservation_token_hash", 2)), _token_hash(token)):
                raise MetadataStoreError("invalid_result_reservation", "Query result reservation is invalid", status=409)
            if _row_value(row, "state", 1) != required:
                raise MetadataStoreError("result_delivery_changed", "Query result delivery state changed", status=409)
            result_ref = _row_value(row, "result_ref_id", 0)
            cursor.execute(
                """UPDATE metadata_query_result_deliveries SET state = %s,
                       dispatch_started_at = CASE WHEN %s = 'delivering' THEN clock_timestamp() ELSE dispatch_started_at END,
                       finished_at = CASE WHEN %s <> 'delivering' THEN clock_timestamp() ELSE finished_at END
                   WHERE delivery_id = %s""",
                (target, target, target, delivery),
            )
            reference_state = "ready" if target == "released" else target
            cursor.execute("UPDATE metadata_query_result_references SET state = %s WHERE result_ref_id = %s", (reference_state, result_ref))
            if scrub:
                cursor.execute("UPDATE metadata_query_result_payloads SET payload = '{}'::jsonb, byte_count = 2, scrubbed_at = clock_timestamp() WHERE result_ref_id = %s", (result_ref,))
            cursor.execute("SELECT c.application_id FROM metadata_query_result_references r JOIN metadata_chats c USING (chat_id) WHERE r.result_ref_id = %s", (result_ref,))
            application = _row_value(cursor.fetchone(), "application_id", 0)
            self._audit(cursor, application, "result", result_ref, required, reference_state, f"delivery_{target}")
        return {"deliveryId": str(delivery), "resultRefId": str(result_ref), "state": target}

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[Any]:
        connection = None
        cursor = None
        try:
            connection = self.connection_factory()
            cursor = connection.cursor()
            try:
                yield cursor
            except MetadataStoreError:
                connection.rollback()
                raise
            except Exception as exc:
                connection.rollback()
                raise _database_error(exc) from exc
            if write:
                try:
                    connection.commit()
                except Exception as exc:
                    raise MetadataStoreError(
                        "metadata_commit_uncertain",
                        "Server metadata commit outcome is uncertain; reconcile before retrying",
                        status=503,
                        retryable=False,
                    ) from exc
            else:
                connection.rollback()
        except MetadataStoreError:
            raise
        except Exception as exc:
            raise _database_error(exc) from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()


def _uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400) from exc


def canonical_review_digest(review_payload: dict[str, Any]) -> str:
    """Return the digest of the single canonical JSON representation used for review."""
    if not isinstance(review_payload, dict):
        raise MetadataStoreError("invalid_metadata", "review_payload must be an object", status=400)
    encoded = json.dumps(review_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in _HEX_DIGEST for character in value):
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _seconds(value: Any, field: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise MetadataStoreError("invalid_metadata", f"{field} is invalid", status=400)
    return value


def _limit(value: Any, maximum: int = 1000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise MetadataStoreError("invalid_metadata", "limit is invalid", status=400)
    return value


def _aware_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MetadataStoreError("invalid_metadata", f"{field} must be timezone-aware", status=400)
    return value.astimezone(timezone.utc)


def _target(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "profileId", "databaseName", "namespaceName", "profileFingerprint", "connectedTargetFingerprint",
    }:
        raise MetadataStoreError("invalid_metadata", "target must contain the exact target binding", status=400)
    return {
        "profileId": _bounded_text(value["profileId"], "profile_id", 256),
        "databaseName": _bounded_text(value["databaseName"], "database_name", 63),
        "namespaceName": _bounded_text(value["namespaceName"], "namespace_name", 63),
        "profileFingerprint": _digest(value["profileFingerprint"], "profile_fingerprint"),
        "connectedTargetFingerprint": _digest(value["connectedTargetFingerprint"], "connected_target_fingerprint"),
    }


def _chat_record(row: Any) -> dict[str, Any]:
    target_id = _row_value(row, "target_id", 9)
    target = None if target_id is None else {
        "targetId": str(target_id), "profileId": _row_value(row, "profile_id", 10),
        "databaseName": _row_value(row, "database_name", 11), "namespaceName": _row_value(row, "namespace_name", 12),
        "profileFingerprint": _row_value(row, "profile_fingerprint", 13),
        "connectedTargetFingerprint": _row_value(row, "connected_target_fingerprint", 14),
    }
    return {"chatId": str(_row_value(row, "chat_id", 0)), "applicationId": _row_value(row, "application_id", 1),
            "resourceKind": _row_value(row, "resource_kind", 2), "resourceId": _row_value(row, "resource_id", 3),
            "externalSessionId": _row_value(row, "external_session_id", 4), "state": _row_value(row, "state", 5),
            "createdAt": _row_value(row, "created_at", 6), "updatedAt": _row_value(row, "updated_at", 7),
            "deletedAt": _row_value(row, "deleted_at", 8), "target": target}


def _proposal_record(row: Any) -> dict[str, Any]:
    return {"proposalId": str(_row_value(row, "proposal_id", 0)), "chatId": str(_row_value(row, "chat_id", 1)),
            "capability": _row_value(row, "capability", 2), "policyRevision": int(_row_value(row, "policy_revision", 3)),
            "binding": _json_value(_row_value(row, "binding", 4)), "action": _json_value(_row_value(row, "action", 5)),
            "state": _row_value(row, "state", 6), "createdAt": _row_value(row, "created_at", 7),
            "expiresAt": _row_value(row, "expires_at", 8)}


def _operation_record(row: Any) -> dict[str, Any]:
    return {"operationId": str(_row_value(row, "operation_id", 0)),
            "proposalId": None if _row_value(row, "proposal_id", 1) is None else str(_row_value(row, "proposal_id", 1)),
            "chatId": str(_row_value(row, "chat_id", 2)), "capability": _row_value(row, "capability", 3),
            "state": _row_value(row, "state", 4), "createdAt": _row_value(row, "created_at", 5),
            "updatedAt": _row_value(row, "updated_at", 6),
            "attempt": None if _row_value(row, "attempt_id", 7) is None else {
                "attemptId": str(_row_value(row, "attempt_id", 7)), "workerId": _row_value(row, "worker_id", 8),
                "leaseExpiresAt": _row_value(row, "lease_expires_at", 9)},
            "outcome": None if _row_value(row, "outcome_state", 10) is None else {
                "state": _row_value(row, "outcome_state", 10), "result": _json_value(_row_value(row, "result", 11)),
                "error": _json_value(_row_value(row, "error", 12))}}


def _plan_record(row: Any, *, include_private: bool) -> dict[str, Any]:
    names = ("plan_id", "application_id", "resource_kind", "resource_id", "resource_revision", "layout_token",
             "profile_id", "database_name", "namespace_name", "profile_fingerprint", "connected_target_fingerprint",
             "live_fingerprint", "desired_fingerprint", "private_payload", "review_payload", "review_digest", "destructive",
             "state", "created_at", "expires_at")
    values = {name: _row_value(row, name, index) for index, name in enumerate(names)}
    record = {"planId": str(values["plan_id"]), "applicationId": values["application_id"],
              "resourceKind": values["resource_kind"], "resourceId": values["resource_id"],
              "resourceRevision": int(values["resource_revision"]), "layoutToken": values["layout_token"],
              "target": {"profileId": values["profile_id"], "databaseName": values["database_name"],
                         "namespaceName": values["namespace_name"], "profileFingerprint": values["profile_fingerprint"],
                         "connectedTargetFingerprint": values["connected_target_fingerprint"]},
              "liveFingerprint": values["live_fingerprint"], "desiredFingerprint": values["desired_fingerprint"],
              "reviewPayload": _json_value(values["review_payload"]), "reviewDigest": values["review_digest"],
              "destructive": values["destructive"], "state": values["state"],
              "createdAt": values["created_at"], "expiresAt": values["expires_at"]}
    if include_private:
        record["privatePayload"] = _json_value(values["private_payload"])
    return record


def _execution_record(row: Any) -> dict[str, Any]:
    def value(name: str, index: int) -> Any:
        return _row_value(row, name, index)
    return {"executionId": str(value("execution_id", 0)), "planId": str(value("plan_id", 1)),
            "state": value("state", 2), "confirmedReviewDigest": value("confirmed_review_digest", 3),
            "destructiveConfirmed": value("destructive_confirmed", 4), "targetXid": value("target_xid", 5),
            "targetIdentity": _json_value(value("target_identity", 6)), "intendedResult": _json_value(value("intended_result", 7)),
            "commitOutcome": value("commit_outcome", 8), "createdAt": value("created_at", 9), "updatedAt": value("updated_at", 10),
            "reconciliationStatus": value("reconciliation_status", 11),
            "reconciliationEvidence": _json_value(value("reconciliation_evidence", 12)),
            "sync": None if value("sync_id", 13) is None else {"syncId": str(value("sync_id", 13)),
                    "state": value("sync_state", 14), "receipt": _json_value(value("sync_receipt", 15))}}


def _token_hash(token: Any) -> str:
    if not isinstance(token, str) or not token:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _row_value(row: Any, name: str, index: int) -> Any:
    return row[name] if isinstance(row, dict) else row[index]


def _database_error(exc: Exception) -> MetadataStoreError:
    try:
        from psycopg import OperationalError
    except ImportError:
        OperationalError = ()
    if isinstance(exc, OperationalError):
        return MetadataStoreError("metadata_unavailable", "Server metadata PostgreSQL is unavailable", status=503, retryable=True)
    return MetadataStoreError("metadata_store_failed", "Server metadata rejected an internal operation", retryable=False)
