from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .errors import MetadataStoreError
from .migrator import MetadataMigrator, validate_applied_migrations
from .validation import bounded_json, identity


_TERMINAL_OPERATION_STATES = {"succeeded", "failed", "uncertain"}


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
        return {"chatId": str(chat), "revision": revision, "policyVersionId": str(policy_id)}

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
        return {"operationId": str(operation_id), "state": "ready", "executionOwner": True}

    def claim_operation(self, operation_id: str, worker_id: str) -> dict[str, Any]:
        operation = _uuid(operation_id, "operation_id")
        worker = identity(worker_id, "worker_id")
        token = secrets.token_urlsafe(32)
        attempt = uuid.uuid4()
        with self._transaction() as cursor:
            cursor.execute("SELECT state FROM metadata_operations WHERE operation_id = %s FOR UPDATE", (operation,))
            row = cursor.fetchone()
            if row is None:
                raise MetadataStoreError("operation_not_found", "Operation was not found", status=404)
            if _row_value(row, "state", 0) != "ready":
                raise MetadataStoreError("operation_not_claimable", "Operation is not ready for execution", status=409)
            cursor.execute(
                "INSERT INTO metadata_operation_attempts (attempt_id, operation_id, worker_id, claim_token_hash) VALUES (%s, %s, %s, %s)",
                (attempt, operation, worker, _token_hash(token)),
            )
            cursor.execute("UPDATE metadata_operations SET state = 'running', updated_at = clock_timestamp() WHERE operation_id = %s", (operation,))
        return {"attemptId": str(attempt), "claimToken": token, "state": "running"}

    def heartbeat_operation(self, attempt_id: str, claim_token: str) -> dict[str, Any]:
        return self._touch_attempt(attempt_id, claim_token, finish_state=None, result=None, error=None)

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
        return self._touch_attempt(attempt_id, claim_token, finish_state=state, result=safe_result, error=safe_error)

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
        return {"deliveryId": str(delivery), "reservationToken": token, "payload": _json_value(_row_value(row, "payload", 4)), "state": "reserved"}

    def begin_result_delivery(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "reserved", "delivering")

    def consume_result(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "delivering", "consumed", scrub=True)

    def release_result(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "reserved", "released")

    def mark_result_uncertain(self, delivery_id: str, reservation_token: str) -> dict[str, Any]:
        return self._result_transition(delivery_id, reservation_token, "delivering", "uncertain", scrub=True)

    def _touch_attempt(self, attempt_id: str, token: str, *, finish_state: str | None, result: Any, error: Any) -> dict[str, Any]:
        attempt = _uuid(attempt_id, "attempt_id")
        with self._transaction() as cursor:
            cursor.execute("SELECT operation_id, state, claim_token_hash FROM metadata_operation_attempts WHERE attempt_id = %s FOR UPDATE", (attempt,))
            row = cursor.fetchone()
            if row is None or not secrets.compare_digest(str(_row_value(row, "claim_token_hash", 2)), _token_hash(token)):
                raise MetadataStoreError("invalid_claim", "Execution claim is invalid", status=409)
            if _row_value(row, "state", 1) != "running":
                raise MetadataStoreError("operation_not_running", "Execution attempt is no longer running", status=409)
            operation_id = _row_value(row, "operation_id", 0)
            if finish_state is None:
                cursor.execute("UPDATE metadata_operation_attempts SET heartbeat_at = clock_timestamp() WHERE attempt_id = %s", (attempt,))
                return {"attemptId": str(attempt), "state": "running"}
            cursor.execute(
                "UPDATE metadata_operation_attempts SET state = %s, heartbeat_at = clock_timestamp(), finished_at = clock_timestamp() WHERE attempt_id = %s",
                (finish_state, attempt),
            )
            cursor.execute(
                "INSERT INTO metadata_operation_outcomes (outcome_id, operation_id, state, result, error) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)",
                (uuid.uuid4(), operation_id, finish_state, None if result is None else _json(result), None if error is None else _json(error)),
            )
            cursor.execute("UPDATE metadata_operations SET state = %s, updated_at = clock_timestamp() WHERE operation_id = %s", (finish_state, operation_id))
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
