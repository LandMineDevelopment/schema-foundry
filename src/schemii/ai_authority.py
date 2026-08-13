from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import secrets
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .atomic_json import remove_file, write_json
from .file_lock import exclusive_file_lock


class AiAuthorityError(Exception):
    """Safe authority failure suitable for translation by an HTTP adapter."""

    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        error = {"code": code, "message": message}
        if details:
            error["details"] = copy.deepcopy(details)
        self.payload = {"error": error}

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)


class AiAuthority:
    """Persistent application-scoped authority for proposals, operations, and query results."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        application: str,
        *,
        max_entries: int = 1000,
        proposal_ttl: float = 300.0,
        claim_lease: float = 300.0,
        result_ttl: float = 300.0,
        operation_ttl: float = 24 * 60 * 60,
        operation_lease: float = 360.0,
        max_payload_bytes: int = 1024 * 1024,
        clock: Callable[[], float] = time.time,
    ):
        self.application = _identity(application, "application")
        self.max_entries = _positive_int(max_entries, "max_entries")
        self.proposal_ttl = _positive_number(proposal_ttl, "proposal_ttl")
        self.claim_lease = _positive_number(claim_lease, "claim_lease")
        self.result_ttl = _positive_number(result_ttl, "result_ttl")
        self.operation_ttl = _positive_number(operation_ttl, "operation_ttl")
        self.operation_lease = _positive_number(operation_lease, "operation_lease")
        self.max_payload_bytes = _positive_int(max_payload_bytes, "max_payload_bytes")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._clock = clock
        self.root = Path(root).expanduser().resolve() / self.application
        self.proposal_dir = self.root / "proposals"
        self.result_dir = self.root / "results"
        self.operation_dir = self.root / "operations"
        self.lock_path = self.root / ".lock"
        self._lock = threading.RLock()
        self._ensure_directories()

    def register_proposal(
        self, *, application: str, session_id: str, resource: str, access: str,
        action: dict[str, Any], binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_application(application)
        session_id = _identity(session_id, "session_id")
        resource = _identity(resource, "resource")
        access = _identity(access, "access")
        action = self._payload(action, "action", require_dict=True)
        binding = self._payload({} if binding is None else binding, "binding", require_dict=True)
        with self._store_lock():
            now = self._now_ms()
            self._prepare(now)
            self._require_capacity()
            proposal_id = self._new_id("proposal", self.proposal_dir)
            record = {
                "version": 1, "kind": "proposal", "id": proposal_id, "application": self.application,
                "sessionId": session_id, "resource": resource, "access": access,
                "action": action, "binding": binding, "state": "ready",
                "createdAtMs": now, "expiresAtMs": now + round(self.proposal_ttl * 1000),
                "claim": None, "consumedAtMs": None, "uncertainAtMs": None,
            }
            self._write(self.proposal_dir, record)
            return self._public_proposal(record)

    def list_proposals(
        self, *, application: str, session_id: str, resource: str, access: str,
        binding: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        context = self._proposal_context(application, session_id, resource, access, binding)
        with self._store_lock():
            self._prepare(self._now_ms())
            return [
                self._public_proposal(record) for record in self._records(self.proposal_dir, "proposal")
                if self._proposal_matches(record, context)
            ]

    def list_resource_proposals(
        self, *, application: str, session_id: str, resource: str, access: str,
    ) -> list[dict[str, Any]]:
        self._require_application(application)
        expected = (_identity(session_id, "session_id"), _identity(resource, "resource"), _identity(access, "access"))
        with self._store_lock():
            self._prepare(self._now_ms())
            return [
                self._public_proposal(record) for record in self._records(self.proposal_dir, "proposal")
                if (record["sessionId"], record["resource"], record["access"]) == expected
            ]

    def claim_proposal(
        self, proposal_id: str, *, application: str, session_id: str, resource: str,
        access: str, binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal_id = _record_id(proposal_id, "proposal")
        context = self._proposal_context(application, session_id, resource, access, binding)
        with self._store_lock():
            now = self._now_ms()
            self._prepare(now)
            record = self._read(self.proposal_dir, proposal_id, "proposal")
            self._require_proposal_binding(record, context)
            if record["state"] == "consumed":
                raise AiAuthorityError(409, "proposal_consumed", "Proposal has already been consumed")
            if record["state"] == "uncertain":
                raise AiAuthorityError(409, "proposal_outcome_uncertain", "Proposal outcome is uncertain; refresh authoritative state")
            if record["state"] == "claimed":
                raise AiAuthorityError(409, "proposal_claimed", "Proposal is already claimed")
            token = secrets.token_urlsafe(32)
            claim_expires = min(record["expiresAtMs"], now + round(self.claim_lease * 1000))
            record["state"] = "claimed"
            record["claim"] = {
                "tokenSha256": _token_hash(token), "claimedAtMs": now, "expiresAtMs": claim_expires,
            }
            self._write(self.proposal_dir, record)
            return {
                "proposal": self._public_proposal(record), "action": copy.deepcopy(record["action"]),
                "claimToken": token, "claimExpiresIn": max(0.0, (claim_expires - now) / 1000),
            }

    def finalize_proposal(
        self, proposal_id: str, claim_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        return self._complete_proposal(proposal_id, claim_token, application, session_id, "consumed")

    def release_proposal(
        self, proposal_id: str, claim_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        return self._complete_proposal(proposal_id, claim_token, application, session_id, "ready")

    def mark_proposal_uncertain(
        self, proposal_id: str, claim_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        return self._complete_proposal(proposal_id, claim_token, application, session_id, "uncertain")

    def create_operation(
        self, proposal_id: str, *, application: str, session_id: str, resource: str,
        access: str, binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal_id = _record_id(proposal_id, "proposal")
        context = self._proposal_context(application, session_id, resource, access, binding)
        with self._store_lock():
            now = self._now_ms()
            self._prepare(now)
            existing = self._operation_for_proposal(proposal_id)
            if existing is not None:
                self._require_operation_owner(existing, session_id)
                if (existing["resource"], existing["access"], existing["binding"]) != (context[1], context[2], context[3]):
                    raise AiAuthorityError(403, "proposal_binding_mismatch", "Operation is not valid for this authority context")
                return {**self._public_operation(existing), "executionOwner": False}
            proposal = self._read(self.proposal_dir, proposal_id, "proposal")
            self._require_proposal_binding(proposal, context)
            if proposal["state"] == "consumed":
                raise AiAuthorityError(409, "proposal_consumed", "Proposal has already been consumed")
            if proposal["state"] in {"claimed", "uncertain"}:
                raise AiAuthorityError(409, "proposal_outcome_uncertain", "Proposal outcome is uncertain; refresh authoritative state")
            operation_id = self._new_id("operation", self.operation_dir)
            operation = {
                "version": 1, "kind": "operation", "id": operation_id, "application": self.application,
                "proposalId": proposal_id, "sessionId": session_id, "resource": resource,
                "access": access, "binding": copy.deepcopy(proposal["binding"]),
                "action": copy.deepcopy(proposal["action"]), "state": "running",
                "createdAtMs": now, "updatedAtMs": now,
                "expiresAtMs": now + round(self.operation_ttl * 1000),
                "leaseExpiresAtMs": now + round(self.operation_lease * 1000),
                "result": None, "error": None,
            }
            proposal["expiresAtMs"] = operation["expiresAtMs"]
            proposal["state"] = "claimed"
            proposal["claim"] = {"tokenSha256": None, "claimedAtMs": now, "expiresAtMs": proposal["expiresAtMs"]}
            self._write(self.proposal_dir, proposal)
            self._write(self.operation_dir, operation)
            return {**self._public_operation(operation), "executionOwner": True}

    def operation(self, operation_id: str, *, application: str, session_id: str) -> dict[str, Any]:
        self._require_application(application)
        with self._store_lock():
            self._prepare(self._now_ms())
            record = self._read(self.operation_dir, _record_id(operation_id, "operation"), "operation")
            self._require_operation_owner(record, session_id)
            return self._public_operation(record)

    def operation_action(self, operation_id: str, *, application: str, session_id: str) -> dict[str, Any]:
        self._require_application(application)
        with self._store_lock():
            record = self._read(self.operation_dir, _record_id(operation_id, "operation"), "operation")
            self._require_operation_owner(record, session_id)
            return copy.deepcopy(record["action"])

    def operation_for_proposal(self, proposal_id: str, *, application: str, session_id: str) -> dict[str, Any]:
        self._require_application(application)
        with self._store_lock():
            self._prepare(self._now_ms())
            record = self._operation_for_proposal(_record_id(proposal_id, "proposal"))
            if record is None:
                raise AiAuthorityError(404, "operation_not_started", "Proposal operation has not started")
            self._require_operation_owner(record, session_id)
            return self._public_operation(record)

    def finish_operation(
        self, operation_id: str, *, application: str, session_id: str, state: str,
        result: Any = None, error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_application(application)
        if state not in {"succeeded", "failed", "uncertain"}:
            raise AiAuthorityError(400, "invalid_authority_input", "Operation terminal state is invalid")
        result = self._payload(result, "result") if result is not None else None
        error = self._payload(error, "error", require_dict=True) if error is not None else None
        with self._store_lock():
            now = self._now_ms()
            operation = self._read(self.operation_dir, _record_id(operation_id, "operation"), "operation")
            self._require_operation_owner(operation, session_id)
            if operation["state"] != "running":
                return self._public_operation(operation)
            operation.update({"state": state, "result": result, "error": error, "updatedAtMs": now, "leaseExpiresAtMs": None})
            proposal = self._read(self.proposal_dir, operation["proposalId"], "proposal")
            proposal["claim"] = None
            if state == "succeeded":
                proposal.update({"state": "consumed", "consumedAtMs": now})
            elif state == "failed":
                proposal.update({"state": "consumed", "consumedAtMs": now})
            else:
                proposal.update({"state": "uncertain", "uncertainAtMs": now})
            self._write(self.operation_dir, operation)
            self._write(self.proposal_dir, proposal)
            return self._public_operation(operation)

    def resolve_operation(
        self, operation_id: str, *, application: str, session_id: str, state: str,
        result: Any = None, error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve an interrupted operation from an application-owned durable receipt."""
        self._require_application(application)
        if state not in {"succeeded", "failed", "uncertain"}:
            raise AiAuthorityError(400, "invalid_authority_input", "Operation terminal state is invalid")
        result = self._payload(result, "result") if result is not None else None
        error = self._payload(error, "error", require_dict=True) if error is not None else None
        with self._store_lock():
            now = self._now_ms()
            operation = self._read(self.operation_dir, _record_id(operation_id, "operation"), "operation")
            self._require_operation_owner(operation, session_id)
            if operation["state"] not in {"running", "uncertain"}:
                return self._public_operation(operation)
            operation.update({"state": state, "result": result, "error": error, "updatedAtMs": now, "leaseExpiresAtMs": None})
            proposal = self._read(self.proposal_dir, operation["proposalId"], "proposal")
            proposal["claim"] = None
            if state in {"succeeded", "failed"}:
                proposal.update({"state": "consumed", "consumedAtMs": now})
            else:
                proposal.update({"state": "uncertain", "uncertainAtMs": now})
            self._write(self.operation_dir, operation)
            self._write(self.proposal_dir, proposal)
            return self._public_operation(operation)

    def register_query_result(
        self, *, application: str, session_id: str, resource: str, target: dict[str, Any],
        result: Any, binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_application(application, "result_binding_mismatch")
        session_id = _identity(session_id, "session_id")
        resource = _identity(resource, "resource")
        target = self._payload(target, "target", require_dict=True)
        result = self._payload(result, "result")
        binding = self._payload({} if binding is None else binding, "binding", require_dict=True)
        with self._store_lock():
            now = self._now_ms()
            self._prepare(now)
            self._require_capacity()
            result_id = self._new_id("result", self.result_dir)
            record = {
                "version": 1, "kind": "query_result", "id": result_id, "application": self.application,
                "sessionId": session_id, "resource": resource, "target": target, "result": result,
                "binding": binding, "state": "ready", "createdAtMs": now,
                "expiresAtMs": now + round(self.result_ttl * 1000), "reservation": None,
                "consumedAtMs": None,
            }
            self._write(self.result_dir, record)
            return self._public_result(record)

    def reserve_query_result(
        self, result_id: str, *, application: str, session_id: str, resource: str,
        target: dict[str, Any], binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result_id = _record_id(result_id, "result")
        context = self._result_context(application, session_id, resource, target, binding)
        with self._store_lock():
            self._prepare(self._now_ms())
            record = self._read(self.result_dir, result_id, "query_result")
            self._require_result_binding(record, context)
            if record["state"] == "consumed":
                raise AiAuthorityError(409, "result_consumed", "Query result has already been consumed")
            if record["state"] == "reserved":
                raise AiAuthorityError(409, "result_reserved", "Query result is already reserved")
            token = secrets.token_urlsafe(32)
            record["state"] = "reserved"
            record["reservation"] = {"tokenSha256": _token_hash(token), "reservedAtMs": self._now_ms()}
            self._write(self.result_dir, record)
            return {"reference": self._public_result(record), "result": copy.deepcopy(record["result"]), "reservationToken": token}

    def consume_query_result(
        self, result_id: str, reservation_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        return self._complete_result(result_id, reservation_token, application, session_id, "consumed")

    def release_query_result(
        self, result_id: str, reservation_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        return self._complete_result(result_id, reservation_token, application, session_id, "ready")

    def _complete_proposal(self, proposal_id: str, token: str, application: str, session_id: str, state: str) -> dict[str, Any]:
        self._require_application(application)
        token = _identity(token, "claim_token")
        with self._store_lock():
            now = self._now_ms()
            self._prepare(now)
            record = self._read(self.proposal_dir, _record_id(proposal_id, "proposal"), "proposal")
            self._require_proposal_owner(record, session_id)
            self._require_claim(record, token)
            record["state"] = state
            record["claim"] = None
            if state == "consumed":
                record["consumedAtMs"] = now
            elif state == "uncertain":
                record["uncertainAtMs"] = now
            self._write(self.proposal_dir, record)
            return self._public_proposal(record)

    def _complete_result(self, result_id: str, token: str, application: str, session_id: str, state: str) -> dict[str, Any]:
        self._require_application(application, "result_binding_mismatch")
        with self._store_lock():
            now = self._now_ms()
            self._prepare(now)
            record = self._read(self.result_dir, _record_id(result_id, "result"), "query_result")
            self._require_result_owner(record, session_id)
            self._require_reservation(record, _identity(token, "reservation_token"))
            record["state"] = state
            record["reservation"] = None
            if state == "consumed":
                record["consumedAtMs"] = now
            self._write(self.result_dir, record)
            return self._public_result(record)

    def _ensure_directories(self) -> None:
        for directory in (self.root, self.proposal_dir, self.result_dir, self.operation_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
        self.lock_path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.lock_path, 0o600)

    @contextmanager
    def _store_lock(self) -> Iterator[None]:
        with self._lock:
            with exclusive_file_lock(self.lock_path):
                yield

    def _now_ms(self) -> int:
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise RuntimeError("authority clock returned an invalid value")
        return round(float(now) * 1000)

    def _prepare(self, now: int) -> None:
        for directory, kind in ((self.proposal_dir, "proposal"), (self.result_dir, "query_result"), (self.operation_dir, "operation")):
            for record in self._records(directory, kind):
                if record["expiresAtMs"] <= now:
                    remove_file(directory / f"{record['id']}.json")
                    continue
                if kind == "proposal" and record["state"] == "claimed" and record["claim"] is not None:
                    claim_expiry = record["claim"]["expiresAtMs"]
                    if claim_expiry <= now and record["claim"].get("tokenSha256") is not None:
                        record.update({"state": "uncertain", "claim": None, "uncertainAtMs": now})
                        self._write(directory, record)
                if kind == "operation" and record["state"] == "running" and record["leaseExpiresAtMs"] <= now:
                    record.update({
                        "state": "uncertain", "updatedAtMs": now, "leaseExpiresAtMs": None,
                        "error": {"code": "execution_outcome_unknown", "message": "Operation outcome is uncertain; reload authoritative state"},
                    })
                    self._write(directory, record)
                    proposal = self._read(self.proposal_dir, record["proposalId"], "proposal")
                    proposal.update({"state": "uncertain", "claim": None, "uncertainAtMs": now})
                    self._write(self.proposal_dir, proposal)

    def _records(self, directory: Path, kind: str) -> list[dict[str, Any]]:
        records = []
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                raise AiAuthorityError(500, "authority_store_error", "AI authority storage contains an invalid record")
            records.append(self._decode(path, kind))
        return records

    def _read(self, directory: Path, record_id: str, kind: str) -> dict[str, Any]:
        path = directory / f"{record_id}.json"
        if not path.exists():
            code = "proposal_not_found" if kind == "proposal" else "result_not_found" if kind == "query_result" else "operation_not_found"
            label = "Proposal" if kind == "proposal" else "Query result" if kind == "query_result" else "Operation"
            raise AiAuthorityError(404, code, f"{label} was not found or has expired")
        if path.is_symlink():
            raise AiAuthorityError(500, "authority_store_error", "AI authority storage contains an invalid record")
        return self._decode(path, kind)

    def _decode(self, path: Path, kind: str) -> dict[str, Any]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AiAuthorityError(500, "authority_store_error", "AI authority storage could not be read") from error
        required = {
            "proposal": {"version", "kind", "id", "application", "sessionId", "resource", "access", "action", "binding", "state", "createdAtMs", "expiresAtMs", "claim", "consumedAtMs", "uncertainAtMs"},
            "query_result": {"version", "kind", "id", "application", "sessionId", "resource", "target", "result", "binding", "state", "createdAtMs", "expiresAtMs", "reservation", "consumedAtMs"},
            "operation": {"version", "kind", "id", "application", "proposalId", "sessionId", "resource", "access", "binding", "action", "state", "createdAtMs", "updatedAtMs", "expiresAtMs", "leaseExpiresAtMs", "result", "error"},
        }[kind]
        if not isinstance(record, dict) or set(record) != required or record.get("version") != 1 or record.get("kind") != kind or record.get("application") != self.application or path.name != f"{record.get('id')}.json":
            raise AiAuthorityError(500, "authority_store_error", "AI authority storage contains an invalid record")
        if (
            not isinstance(record.get("createdAtMs"), int) or not isinstance(record.get("expiresAtMs"), int)
            or record["expiresAtMs"] <= record["createdAtMs"]
        ):
            raise AiAuthorityError(500, "authority_store_error", "AI authority storage contains an invalid record")
        states = {"proposal": {"ready", "claimed", "consumed", "uncertain"}, "query_result": {"ready", "reserved", "consumed"}, "operation": {"running", "succeeded", "failed", "uncertain"}}[kind]
        if record.get("state") not in states:
            raise AiAuthorityError(500, "authority_store_error", "AI authority storage contains an invalid record")
        if kind == "proposal" and not isinstance(record.get("action"), dict):
            raise AiAuthorityError(500, "authority_store_error", "AI authority storage contains an invalid record")
        if kind == "operation" and (
            not isinstance(record.get("action"), dict) or not isinstance(record.get("updatedAtMs"), int)
            or (record["state"] == "running") != isinstance(record.get("leaseExpiresAtMs"), int)
        ):
            raise AiAuthorityError(500, "authority_store_error", "AI authority storage contains an invalid record")
        return record

    def _write(self, directory: Path, record: dict[str, Any]) -> None:
        write_json(directory / f"{record['id']}.json", record, mode=0o600, sort_keys=True)

    def _require_capacity(self) -> None:
        total = sum(len(list(directory.glob("*.json"))) for directory in (self.proposal_dir, self.result_dir))
        if total >= self.max_entries:
            raise AiAuthorityError(429, "authority_capacity", "AI authority registry is at capacity")

    @staticmethod
    def _new_id(prefix: str, directory: Path) -> str:
        while True:
            value = f"{prefix}_{secrets.token_urlsafe(24)}"
            if not (directory / f"{value}.json").exists():
                return value

    def _payload(self, value: Any, field: str, *, require_dict: bool = False) -> Any:
        if require_dict and not isinstance(value, dict):
            raise AiAuthorityError(400, "invalid_authority_input", f"{field} must be an object")
        try:
            encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
            copied = copy.deepcopy(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise AiAuthorityError(400, "invalid_authority_input", f"{field} must be JSON-compatible") from error
        if len(encoded) > self.max_payload_bytes:
            raise AiAuthorityError(413, "authority_payload_too_large", f"{field} exceeds the authority payload limit")
        return copied

    def _require_application(self, application: str, code: str = "proposal_binding_mismatch") -> None:
        if _identity(application, "application") != self.application:
            raise AiAuthorityError(403, code, "Authority belongs to another application")

    def _proposal_context(self, application: str, session_id: str, resource: str, access: str, binding: Any) -> tuple[Any, ...]:
        self._require_application(application)
        return (_identity(session_id, "session_id"), _identity(resource, "resource"), _identity(access, "access"), self._payload({} if binding is None else binding, "binding", require_dict=True))

    @staticmethod
    def _proposal_matches(record: dict[str, Any], context: tuple[Any, ...]) -> bool:
        return (record["sessionId"], record["resource"], record["access"], record["binding"]) == context

    def _require_proposal_binding(self, record: dict[str, Any], context: tuple[Any, ...]) -> None:
        if not self._proposal_matches(record, context):
            raise AiAuthorityError(403, "proposal_binding_mismatch", "Proposal is not valid for this authority context")

    @staticmethod
    def _require_proposal_owner(record: dict[str, Any], session_id: str) -> None:
        if record["sessionId"] != _identity(session_id, "session_id"):
            raise AiAuthorityError(403, "proposal_binding_mismatch", "Proposal is not valid for this authority context")

    def _result_context(self, application: str, session_id: str, resource: str, target: Any, binding: Any) -> tuple[Any, ...]:
        self._require_application(application, "result_binding_mismatch")
        return (_identity(session_id, "session_id"), _identity(resource, "resource"), self._payload(target, "target", require_dict=True), self._payload({} if binding is None else binding, "binding", require_dict=True))

    @staticmethod
    def _require_result_binding(record: dict[str, Any], context: tuple[Any, ...]) -> None:
        if (record["sessionId"], record["resource"], record["target"], record["binding"]) != context:
            raise AiAuthorityError(403, "result_binding_mismatch", "Query result is not valid for this authority context")

    @staticmethod
    def _require_result_owner(record: dict[str, Any], session_id: str) -> None:
        if record["sessionId"] != _identity(session_id, "session_id"):
            raise AiAuthorityError(403, "result_binding_mismatch", "Query result is not valid for this authority context")

    @staticmethod
    def _require_claim(record: dict[str, Any], token: str) -> None:
        claim = record.get("claim")
        expected = claim.get("tokenSha256") if isinstance(claim, dict) else None
        if record["state"] != "claimed" or not isinstance(expected, str) or not secrets.compare_digest(expected, _token_hash(token)):
            raise AiAuthorityError(409, "invalid_claim", "Proposal claim is missing, expired, or does not match")

    @staticmethod
    def _require_reservation(record: dict[str, Any], token: str) -> None:
        reservation = record.get("reservation")
        expected = reservation.get("tokenSha256") if isinstance(reservation, dict) else None
        if record["state"] != "reserved" or not isinstance(expected, str) or not secrets.compare_digest(expected, _token_hash(token)):
            raise AiAuthorityError(409, "invalid_result_reservation", "Query result reservation is missing or does not match")

    def _operation_for_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return next((record for record in self._records(self.operation_dir, "operation") if record["proposalId"] == proposal_id), None)

    @staticmethod
    def _require_operation_owner(record: dict[str, Any], session_id: str) -> None:
        if record["sessionId"] != _identity(session_id, "session_id"):
            raise AiAuthorityError(403, "proposal_binding_mismatch", "Operation is not valid for this AI session")

    @staticmethod
    def _public_proposal(record: dict[str, Any]) -> dict[str, Any]:
        return {"id": record["id"], "application": record["application"], "sessionId": record["sessionId"], "resource": record["resource"], "access": record["access"], "action": copy.deepcopy(record["action"]), "binding": copy.deepcopy(record["binding"]), "state": record["state"]}

    @staticmethod
    def _public_result(record: dict[str, Any]) -> dict[str, Any]:
        return {"id": record["id"], "application": record["application"], "sessionId": record["sessionId"], "resource": record["resource"], "target": copy.deepcopy(record["target"]), "binding": copy.deepcopy(record["binding"]), "state": record["state"]}

    @staticmethod
    def _public_operation(record: dict[str, Any]) -> dict[str, Any]:
        return {"id": record["id"], "proposalId": record["proposalId"], "state": record["state"], "result": copy.deepcopy(record["result"]), "error": copy.deepcopy(record["error"])}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _record_id(value: Any, prefix: str) -> str:
    value = _identity(value, f"{prefix}_id")
    if not value.startswith(f"{prefix}_") or not all(char.isalnum() or char in "_-" for char in value):
        raise AiAuthorityError(400, "invalid_authority_input", f"{prefix}_id is invalid")
    return value


def _identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise AiAuthorityError(400, "invalid_authority_input", f"{field} must be a non-empty trimmed string up to 256 characters")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return float(value)
