from __future__ import annotations

import json
import re
from typing import Any, Callable

from .ai_authority import AiAuthorityError
from .metadata import MetadataStoreError
from .opencode_service import OpenCodeService, OpenCodeServiceError


AI_MAX_BODY_SIZE = 128 * 1024
AI_AUTH_PATH = re.compile(r"^/api/ai/auth/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})$")
AI_SESSION_PATH = re.compile(r"^/api/ai/sessions/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})(?:/(messages))?$")
AI_ACTIVITY_PATH = re.compile(r"^/api/ai/sessions/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/activity$")
AI_PROPOSAL_PATH = re.compile(
    r"^/api/ai/sessions/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/proposals/"
    r"([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/(claim|finalize|release|execute|reconcile)$"
)
AI_OPERATION_PATH = re.compile(
    r"^/api/ai/sessions/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/operations/"
    r"([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/status$"
)
AI_POLICY_PATH = re.compile(r"^/api/ai/sessions/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/policy$")


def ai_context_fingerprint(parts: list[Any]) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    value = 1469598103934665603
    for character in encoded:
        value ^= ord(character)
        value = value * 1099511628211 & ((1 << 64) - 1)
    return f"{value:016x}"


class AiHttpRouter:
    """Shared same-origin router for the fixed embedded OpenCode surface."""

    def __init__(
        self,
        service: OpenCodeService | None,
        message_handler: Callable[[Any, OpenCodeService, str, dict[str, Any]], Any],
        proposal_handler: Callable[[Any, OpenCodeService, str, str, str, dict[str, Any]], Any] | None = None,
        history_handler: Callable[[Any, OpenCodeService, str | None], Any] | None = None,
        operation_handler: Callable[[Any, OpenCodeService, str, str], Any] | None = None,
        session_handler: Callable[[Any, OpenCodeService, dict[str, Any]], Any] | None = None,
        activity_handler: Callable[[Any, OpenCodeService, str], Any] | None = None,
        delete_session_handler: Callable[[Any, OpenCodeService, str], Any] | None = None,
        policy_handler: Callable[[Any, OpenCodeService, str, dict[str, Any] | None], Any] | None = None,
        proposal_operations: frozenset[str] | None = None,
    ):
        self.service = service
        self.message_handler = message_handler
        self.proposal_handler = proposal_handler
        self.history_handler = history_handler
        self.operation_handler = operation_handler
        self.session_handler = session_handler
        self.activity_handler = activity_handler
        self.delete_session_handler = delete_session_handler
        self.policy_handler = policy_handler
        self.proposal_operations = proposal_operations or frozenset({"claim", "finalize", "release", "execute", "reconcile"})

    @staticmethod
    def _authorize(handler) -> bool:
        return handler._authorize_local_api("AI API", "AI session token is missing or invalid")

    def _require_service(self, handler) -> OpenCodeService | None:
        if self.service is None:
            handler.send_json(503, {"error": {"code": "ai_disabled", "message": "Embedded AI is not configured"}})
            return None
        return self.service

    def handle_get(self, handler, path: str) -> bool:
        session_match = AI_SESSION_PATH.fullmatch(path)
        activity_match = AI_ACTIVITY_PATH.fullmatch(path)
        operation_match = AI_OPERATION_PATH.fullmatch(path)
        policy_match = AI_POLICY_PATH.fullmatch(path)
        if path not in {"/api/ai/status", "/api/ai/sessions"} and not session_match and not activity_match and not operation_match and not policy_match:
            return False
        if not self._authorize(handler):
            return True
        if path == "/api/ai/status" and self.service is None:
            handler.send_json(200, {"available": False, "enabled": False, "healthy": False, "providers": [], "authMethods": {}, "skills": []})
            return True
        service = self._require_service(handler)
        if service is None:
            return True
        if path == "/api/ai/status":
            handler._ai_call(service.status)
        elif path == "/api/ai/sessions":
            if self.history_handler is None:
                handler._ai_call(service.list_sessions)
            else:
                self.history_handler(handler, service, None)
        elif activity_match:
            if self.activity_handler is None:
                self._activity_stream(handler, service, activity_match.group(1))
            else:
                self.activity_handler(handler, service, activity_match.group(1))
        elif operation_match and self.operation_handler is not None:
            self.operation_handler(handler, service, operation_match.group(1), operation_match.group(2))
        elif policy_match and self.policy_handler is not None:
            self.policy_handler(handler, service, policy_match.group(1), None)
        elif session_match and session_match.group(2) == "messages":
            if self.history_handler is None:
                handler._ai_call(lambda: service.session_messages(session_match.group(1)))
            else:
                self.history_handler(handler, service, session_match.group(1))
        else:
            handler.send_json(404, {"error": "Unknown API path"})
        return True

    def handle_put(self, handler, path: str) -> bool:
        policy_match = AI_POLICY_PATH.fullmatch(path)
        if not policy_match or self.policy_handler is None:
            return False
        if not self._authorize(handler):
            return True
        service = self._require_service(handler)
        if service is None:
            return True
        body = handler._body_or_error(AI_MAX_BODY_SIZE)
        if body is not None:
            if not isinstance(body, dict):
                handler.send_json(400, {"error": {"code": "validation_error", "message": "AI policy request must be an object"}})
            else:
                self.policy_handler(handler, service, policy_match.group(1), body)
        return True

    def handle_post(self, handler, path: str) -> bool:
        if not path.startswith("/api/ai/"):
            return False
        if not self._authorize(handler):
            return True
        service = self._require_service(handler)
        if service is None:
            return True
        body = handler._body_or_error(AI_MAX_BODY_SIZE)
        if body is None:
            return True
        if not isinstance(body, dict):
            handler.send_json(400, {"error": {"code": "validation_error", "message": "AI request body must be an object"}})
            return True
        if path == "/api/ai/auth/api":
            handler._ai_call(lambda: service.set_api_key(body.get("providerId"), body.get("key"), body.get("inputs")))
        elif path == "/api/ai/auth/oauth/authorize":
            handler._ai_call(lambda: service.oauth_authorize(body.get("providerId"), body.get("method"), body.get("inputs")))
        elif path == "/api/ai/auth/oauth/callback":
            handler._ai_call(lambda: service.oauth_callback(body.get("providerId"), body.get("method"), body.get("code")))
        elif path == "/api/ai/sessions":
            if self.session_handler is None:
                handler._ai_call(lambda: service.create_session(body.get("title"), body.get("model")), 201)
            else:
                self.session_handler(handler, service, body)
        else:
            proposal_match = AI_PROPOSAL_PATH.fullmatch(path)
            if proposal_match and self.proposal_handler is not None and proposal_match.group(3) in self.proposal_operations:
                self.proposal_handler(
                    handler, service, proposal_match.group(1), proposal_match.group(2), proposal_match.group(3), body,
                )
                return True
            session_match = AI_SESSION_PATH.fullmatch(path)
            if not session_match or session_match.group(2) != "messages":
                handler.send_json(404, {"error": "Unknown API path"})
            else:
                self.message_handler(handler, service, session_match.group(1), body)
        return True

    def handle_delete(self, handler, path: str) -> bool:
        auth_match = AI_AUTH_PATH.fullmatch(path)
        session_match = AI_SESSION_PATH.fullmatch(path)
        if not auth_match and not (session_match and session_match.group(2) is None):
            return False
        if not self._authorize(handler):
            return True
        service = self._require_service(handler)
        if service is None:
            return True
        if auth_match:
            handler._ai_call(lambda: service.delete_api_key(auth_match.group(1)))
        else:
            if self.delete_session_handler is None:
                handler._ai_call(lambda: service.delete_session(session_match.group(1)))
            else:
                self.delete_session_handler(handler, service, session_match.group(1))
        return True

    @staticmethod
    def _activity_stream(handler, service: OpenCodeService, session_id: str) -> None:
        try:
            service.verify_session(session_id)
        except OpenCodeServiceError as error:
            handler.send_json(error.status, error.payload)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        try:
            for event in service.activity(session_id):
                handler.wfile.write(json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n")
                handler.wfile.flush()
        except OpenCodeServiceError:
            try:
                handler.wfile.write(b'{"type":"connection","state":"disconnected"}\n')
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass


def issue_ai_proposals(authority, response, *, application, session_id, resource, access, authorization_target, schema_concurrency, normalize_action=None, batch_action_types=None, policy_binding=None, preflight=None):
    """Replace model-authored actions with server-issued, context-bound envelopes."""
    if not isinstance(response, dict):
        return response
    normalized_actions = []
    for action in response.get("actions", []):
        if not isinstance(action, dict):
            continue
        if normalize_action is not None:
            try:
                action = normalize_action(action, access)
            except (TypeError, ValueError):
                continue
        normalized_actions.append(action)
    batch_types = set(batch_action_types or ())
    batched = [action for action in normalized_actions if action.get("type") in batch_types]
    actions = [action for action in normalized_actions if action.get("type") not in batch_types]
    if len(batched) == 1:
        actions.append(batched[0])
    elif batched:
        actions.append({"type": "schema_batch", "actions": batched, "requiresConfirmation": True})
    issued = []
    for action in actions:
        diagnostics = preflight(action) if preflight is not None else None
        binding = policy_binding(action) if policy_binding is not None else {}
        if hasattr(authority, "create_proposal"):
            proposal = authority.create_proposal(
                session_id, action, binding, authorization_target, schema_concurrency,
            )
        else:
            proposal = authority.register_proposal(
                application=application, session_id=session_id, resource=resource, access=access,
                action=action, authorization_target=authorization_target,
                schema_concurrency=schema_concurrency, policy_binding=binding,
            )
        envelope = {"proposalId": proposal["id"], "action": proposal["action"], "policyBinding": proposal["policyBinding"]}
        if diagnostics is not None:
            envelope["preflight"] = diagnostics
        issued.append(envelope)
    result = dict(response)
    result.pop("actions", None)
    result["proposals"] = issued
    return result


def authority_call(handler, callback, status: int = 200):
    try:
        handler.send_json(status, callback())
    except (AiAuthorityError, MetadataStoreError) as error:
        handler.send_json(error.status, error.to_dict())


def bounded_ai_query_result(result: dict[str, Any], *, max_rows: int, max_columns: int, max_bytes: int) -> dict[str, Any]:
    columns = result.get("columns", [])[:max_columns]
    rows = [row[:len(columns)] for row in result.get("rows", [])[:max_rows] if isinstance(row, list)]
    bounded = {
        "columns": columns, "rows": rows, "rowCount": len(rows),
        "truncated": bool(result.get("truncated") or len(result.get("rows", [])) > len(rows) or len(result.get("columns", [])) > len(columns)),
    }
    while rows and len(json.dumps(bounded, allow_nan=False, separators=(",", ":")).encode("utf-8")) > max_bytes:
        rows.pop()
        bounded["rowCount"] = len(rows)
        bounded["truncated"] = True
    if len(json.dumps(bounded, allow_nan=False, separators=(",", ":")).encode("utf-8")) > max_bytes:
        raise AiAuthorityError(413, "authority_payload_too_large", "Query result metadata exceeds the AI disclosure limit")
    return bounded
