from __future__ import annotations

import copy
import json
import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


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


@dataclass
class _Proposal:
    proposal_id: str
    application: str
    session_id: str
    resource: str
    access: str
    action: dict[str, Any]
    binding: dict[str, Any]
    created_at: float
    expires_at: float
    state: str = "ready"
    claim_token: str | None = None
    claim_expires_at: float | None = None


@dataclass
class _QueryResult:
    result_id: str
    application: str
    session_id: str
    resource: str
    target: dict[str, Any]
    result: Any
    binding: dict[str, Any]
    created_at: float
    expires_at: float
    state: str = "ready"
    reservation_token: str | None = None


class AiAuthority:
    """Thread-safe, process-local authority for AI proposals and query results."""

    def __init__(
        self,
        *,
        max_entries: int = 1000,
        proposal_ttl: float = 300.0,
        claim_lease: float = 30.0,
        result_ttl: float = 120.0,
        max_payload_bytes: int = 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_entries = _positive_int(max_entries, "max_entries")
        self.proposal_ttl = _positive_number(proposal_ttl, "proposal_ttl")
        self.claim_lease = _positive_number(claim_lease, "claim_lease")
        self.result_ttl = _positive_number(result_ttl, "result_ttl")
        self.max_payload_bytes = _positive_int(max_payload_bytes, "max_payload_bytes")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._clock = clock
        self._lock = threading.RLock()
        self._proposals: dict[str, _Proposal] = {}
        self._results: dict[str, _QueryResult] = {}

    def register_proposal(
        self,
        *,
        application: str,
        session_id: str,
        resource: str,
        access: str,
        action: dict[str, Any],
        binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        application = _identity(application, "application")
        session_id = _identity(session_id, "session_id")
        resource = _identity(resource, "resource")
        access = _identity(access, "access")
        action_copy = self._payload(action, "action", require_dict=True)
        binding_copy = self._payload({} if binding is None else binding, "binding", require_dict=True)
        with self._lock:
            now = self._now()
            self._prepare(now)
            self._require_capacity()
            proposal_id = self._new_id("proposal", self._proposals)
            proposal = _Proposal(
                proposal_id, application, session_id, resource, access,
                action_copy, binding_copy, now, now + self.proposal_ttl,
            )
            self._proposals[proposal_id] = proposal
            return self._public_proposal(proposal)

    def list_proposals(
        self,
        *,
        application: str,
        session_id: str,
        resource: str,
        access: str,
        binding: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        context = self._proposal_context(application, session_id, resource, access, binding)
        with self._lock:
            self._prepare(self._now())
            return [
                self._public_proposal(item)
                for item in self._proposals.values()
                if self._proposal_matches(item, context)
            ]

    def claim_proposal(
        self,
        proposal_id: str,
        *,
        application: str,
        session_id: str,
        resource: str,
        access: str,
        binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal_id = _identity(proposal_id, "proposal_id")
        context = self._proposal_context(application, session_id, resource, access, binding)
        with self._lock:
            now = self._now()
            self._prepare(now)
            proposal = self._proposal(proposal_id)
            self._require_proposal_binding(proposal, context)
            if proposal.state == "consumed":
                raise AiAuthorityError(409, "proposal_consumed", "Proposal has already been consumed")
            if proposal.state == "claimed":
                raise AiAuthorityError(409, "proposal_claimed", "Proposal is already claimed")
            token = secrets.token_urlsafe(32)
            proposal.state = "claimed"
            proposal.claim_token = token
            proposal.claim_expires_at = min(proposal.expires_at, now + self.claim_lease)
            return {
                "proposal": self._public_proposal(proposal),
                "action": copy.deepcopy(proposal.action),
                "claimToken": token,
                "claimExpiresIn": max(0.0, proposal.claim_expires_at - now),
            }

    def finalize_proposal(
        self, proposal_id: str, claim_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        proposal_id = _identity(proposal_id, "proposal_id")
        claim_token = _identity(claim_token, "claim_token")
        with self._lock:
            self._prepare(self._now())
            proposal = self._proposal(proposal_id)
            self._require_proposal_owner(proposal, application, session_id)
            self._require_claim(proposal, claim_token)
            proposal.state = "consumed"
            proposal.claim_token = None
            proposal.claim_expires_at = None
            return self._public_proposal(proposal)

    def release_proposal(
        self, proposal_id: str, claim_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        proposal_id = _identity(proposal_id, "proposal_id")
        claim_token = _identity(claim_token, "claim_token")
        with self._lock:
            self._prepare(self._now())
            proposal = self._proposal(proposal_id)
            self._require_proposal_owner(proposal, application, session_id)
            self._require_claim(proposal, claim_token)
            proposal.state = "ready"
            proposal.claim_token = None
            proposal.claim_expires_at = None
            return self._public_proposal(proposal)

    def register_query_result(
        self,
        *,
        application: str,
        session_id: str,
        resource: str,
        target: dict[str, Any],
        result: Any,
        binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        application = _identity(application, "application")
        session_id = _identity(session_id, "session_id")
        resource = _identity(resource, "resource")
        target_copy = self._payload(target, "target", require_dict=True)
        result_copy = self._payload(result, "result")
        binding_copy = self._payload({} if binding is None else binding, "binding", require_dict=True)
        with self._lock:
            now = self._now()
            self._prepare(now)
            self._require_capacity()
            result_id = self._new_id("result", self._results)
            record = _QueryResult(
                result_id, application, session_id, resource, target_copy,
                result_copy, binding_copy, now, now + self.result_ttl,
            )
            self._results[result_id] = record
            return self._public_result(record)

    def reserve_query_result(
        self,
        result_id: str,
        *,
        application: str,
        session_id: str,
        resource: str,
        target: dict[str, Any],
        binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result_id = _identity(result_id, "result_id")
        context = self._result_context(application, session_id, resource, target, binding)
        with self._lock:
            self._prepare(self._now())
            record = self._result(result_id)
            self._require_result_binding(record, context)
            if record.state == "consumed":
                raise AiAuthorityError(409, "result_consumed", "Query result has already been consumed")
            if record.state == "reserved":
                raise AiAuthorityError(409, "result_reserved", "Query result is already reserved")
            token = secrets.token_urlsafe(32)
            record.state = "reserved"
            record.reservation_token = token
            return {
                "reference": self._public_result(record),
                "result": copy.deepcopy(record.result),
                "reservationToken": token,
            }

    def consume_query_result(
        self, result_id: str, reservation_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._prepare(self._now())
            record = self._result(_identity(result_id, "result_id"))
            self._require_result_owner(record, application, session_id)
            self._require_reservation(record, _identity(reservation_token, "reservation_token"))
            record.state = "consumed"
            record.reservation_token = None
            return self._public_result(record)

    def release_query_result(
        self, result_id: str, reservation_token: str, *, application: str, session_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._prepare(self._now())
            record = self._result(_identity(result_id, "result_id"))
            self._require_result_owner(record, application, session_id)
            self._require_reservation(record, _identity(reservation_token, "reservation_token"))
            record.state = "ready"
            record.reservation_token = None
            return self._public_result(record)

    def _now(self) -> float:
        now = self._clock()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise RuntimeError("authority clock returned an invalid value")
        return float(now)

    def _prepare(self, now: float) -> None:
        self._proposals = {key: item for key, item in self._proposals.items() if item.expires_at > now}
        self._results = {key: item for key, item in self._results.items() if item.expires_at > now}
        for proposal in self._proposals.values():
            if proposal.state == "claimed" and proposal.claim_expires_at is not None and proposal.claim_expires_at <= now:
                proposal.state = "ready"
                proposal.claim_token = None
                proposal.claim_expires_at = None

    def _require_capacity(self) -> None:
        if len(self._proposals) + len(self._results) >= self.max_entries:
            raise AiAuthorityError(429, "authority_capacity", "AI authority registry is at capacity")

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

    @staticmethod
    def _new_id(prefix: str, records: dict[str, Any]) -> str:
        while True:
            value = f"{prefix}_{secrets.token_urlsafe(24)}"
            if value not in records:
                return value

    @staticmethod
    def _public_proposal(proposal: _Proposal) -> dict[str, Any]:
        return {
            "id": proposal.proposal_id,
            "application": proposal.application,
            "sessionId": proposal.session_id,
            "resource": proposal.resource,
            "access": proposal.access,
            "action": copy.deepcopy(proposal.action),
            "binding": copy.deepcopy(proposal.binding),
            "state": proposal.state,
        }

    @staticmethod
    def _public_result(record: _QueryResult) -> dict[str, Any]:
        return {
            "id": record.result_id,
            "application": record.application,
            "sessionId": record.session_id,
            "resource": record.resource,
            "target": copy.deepcopy(record.target),
            "binding": copy.deepcopy(record.binding),
            "state": record.state,
        }

    def _proposal_context(self, application: str, session_id: str, resource: str, access: str, binding: Any) -> tuple[Any, ...]:
        return (
            _identity(application, "application"), _identity(session_id, "session_id"),
            _identity(resource, "resource"), _identity(access, "access"),
            self._payload({} if binding is None else binding, "binding", require_dict=True),
        )

    @staticmethod
    def _proposal_matches(proposal: _Proposal, context: tuple[Any, ...]) -> bool:
        return (proposal.application, proposal.session_id, proposal.resource, proposal.access, proposal.binding) == context

    def _require_proposal_binding(self, proposal: _Proposal, context: tuple[Any, ...]) -> None:
        if not self._proposal_matches(proposal, context):
            raise AiAuthorityError(403, "proposal_binding_mismatch", "Proposal is not valid for this authority context")

    @staticmethod
    def _require_proposal_owner(proposal: _Proposal, application: str, session_id: str) -> None:
        if (proposal.application, proposal.session_id) != (
            _identity(application, "application"), _identity(session_id, "session_id"),
        ):
            raise AiAuthorityError(403, "proposal_binding_mismatch", "Proposal is not valid for this authority context")

    def _result_context(self, application: str, session_id: str, resource: str, target: Any, binding: Any) -> tuple[Any, ...]:
        return (
            _identity(application, "application"), _identity(session_id, "session_id"),
            _identity(resource, "resource"), self._payload(target, "target", require_dict=True),
            self._payload({} if binding is None else binding, "binding", require_dict=True),
        )

    @staticmethod
    def _require_result_binding(record: _QueryResult, context: tuple[Any, ...]) -> None:
        if (record.application, record.session_id, record.resource, record.target, record.binding) != context:
            raise AiAuthorityError(403, "result_binding_mismatch", "Query result is not valid for this authority context")

    @staticmethod
    def _require_result_owner(record: _QueryResult, application: str, session_id: str) -> None:
        if (record.application, record.session_id) != (
            _identity(application, "application"), _identity(session_id, "session_id"),
        ):
            raise AiAuthorityError(403, "result_binding_mismatch", "Query result is not valid for this authority context")

    def _proposal(self, proposal_id: str) -> _Proposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise AiAuthorityError(404, "proposal_not_found", "Proposal was not found or has expired")
        return proposal

    def _result(self, result_id: str) -> _QueryResult:
        record = self._results.get(result_id)
        if record is None:
            raise AiAuthorityError(404, "result_not_found", "Query result was not found or has expired")
        return record

    @staticmethod
    def _require_claim(proposal: _Proposal, token: str) -> None:
        if proposal.state != "claimed" or not secrets.compare_digest(proposal.claim_token or "", token):
            raise AiAuthorityError(409, "invalid_claim", "Proposal claim is missing, expired, or does not match")

    @staticmethod
    def _require_reservation(record: _QueryResult, token: str) -> None:
        if record.state != "reserved" or not secrets.compare_digest(record.reservation_token or "", token):
            raise AiAuthorityError(409, "invalid_result_reservation", "Query result reservation is missing or does not match")


def _identity(value: Any, field: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip() or len(value) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
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
