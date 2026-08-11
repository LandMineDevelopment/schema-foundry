from __future__ import annotations

import json
import re
from typing import Any, Callable

from .opencode_service import OpenCodeService, OpenCodeServiceError


AI_MAX_BODY_SIZE = 128 * 1024
AI_AUTH_PATH = re.compile(r"^/api/ai/auth/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})$")
AI_SESSION_PATH = re.compile(r"^/api/ai/sessions/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})(?:/(messages))?$")
AI_ACTIVITY_PATH = re.compile(r"^/api/ai/sessions/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/activity$")


def ai_context_fingerprint(parts: list[Any]) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    value = 1469598103934665603
    for character in encoded:
        value ^= ord(character)
        value = value * 1099511628211 & ((1 << 64) - 1)
    return f"{value:016x}"


def require_ai_session_binding(
    service: OpenCodeService,
    session_id: str,
    prefix: str,
    resource_id: str,
    access_level: str,
    fingerprint_parts: list[Any] | None = None,
) -> None:
    suffix = f":{ai_context_fingerprint(fingerprint_parts)}" if fingerprint_parts is not None else ""
    expected = f"{prefix}:{resource_id}:{access_level}{suffix} "
    if not service.session_identity(session_id)["title"].startswith(expected):
        raise OpenCodeServiceError(
            409,
            "session_context_changed",
            "The AI conversation belongs to a different resource, disclosure level, or data target",
        )


class AiHttpRouter:
    """Shared same-origin router for the fixed embedded OpenCode surface."""

    def __init__(
        self,
        service: OpenCodeService | None,
        message_handler: Callable[[Any, OpenCodeService, str, dict[str, Any]], Any],
    ):
        self.service = service
        self.message_handler = message_handler

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
        if path not in {"/api/ai/status", "/api/ai/sessions"} and not session_match and not activity_match:
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
            handler._ai_call(service.list_sessions)
        elif activity_match:
            self._activity_stream(handler, service, activity_match.group(1))
        elif session_match and session_match.group(2) == "messages":
            handler._ai_call(lambda: service.session_messages(session_match.group(1)))
        else:
            handler.send_json(404, {"error": "Unknown API path"})
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
            handler._ai_call(lambda: service.create_session(body.get("title"), body.get("model")), 201)
        else:
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
            handler._ai_call(lambda: service.delete_session(session_match.group(1)))
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
