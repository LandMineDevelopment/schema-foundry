from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_UPSTREAM_BODY = 8 * 1024 * 1024
MAX_TEXT_SIZE = 64 * 1024
MAX_PROMPT_SIZE = 96 * 1024
MAX_PARTS = 100
MAX_ACTIONS = 20
MAX_ACTION_SIZE = 32 * 1024
MAX_TOOL_OUTPUT_SIZE = 256 * 1024
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
ACTION_PREFIX = "SCHEMA_FOUNDRY_ACTION:"
CUSTOM_TOOLS = {
    "schema_read_query",
    "schema_add_table",
    "schema_rename_table",
    "schema_add_column",
    "schema_update_column",
    "schema_delete_element",
    "schema_add_relationship",
    "schema_connection_setup",
    "schema_migration_preview",
    "schema_migration_apply",
}
SAFE_SKILLS = {
    "schema-foundry-help",
    "connection-setup",
    "migration-safety",
    "schema-design-layout",
    "read-only-query-safety",
    "target-selection",
}
# These OpenCode 1.18.15 models currently return an empty response or upstream
# 401 with its synthetic anonymous `public` credential. A real Zen key may work.
UNUSABLE_ANONYMOUS_MODELS = {"north-mini-code-free", "ling-3.0-tiny-free"}
PROMPT_TOOLS = {
    **{name: True for name in CUSTOM_TOOLS},
    "skill": True,
    "bash": False,
    "shell": False,
    "read": False,
    "write": False,
    "edit": False,
    "apply_patch": False,
    "glob": False,
    "grep": False,
    "list": False,
    "webfetch": False,
    "websearch": False,
    "task": False,
}


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _open_without_redirects(request, timeout):
    return build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _reject_json_constant(value):
    raise ValueError(value)


class OpenCodeServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = {"error": {"code": code, "message": message}}


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise OpenCodeServiceError(400, "validation_error", f"{label} is invalid")
    return value


def _model(value: Any, *, optional: bool = False) -> dict[str, str] | None:
    if value is None and optional:
        return None
    if not isinstance(value, dict):
        raise OpenCodeServiceError(400, "validation_error", "model must contain providerId and modelId")
    if set(value) == {"providerID", "modelID"}:
        provider_id, model_id = value.get("providerID"), value.get("modelID")
    elif set(value) == {"providerId", "modelId"}:
        provider_id, model_id = value.get("providerId"), value.get("modelId")
    else:
        raise OpenCodeServiceError(400, "validation_error", "model must contain providerId and modelId")
    return {
        "providerID": _identifier(provider_id, "providerId"),
        "modelID": _bounded_text(model_id, 256, "modelId"),
    }


def _bounded_text(value: Any, maximum: int, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or value != value.strip() or len(value.encode("utf-8")) > maximum:
        raise OpenCodeServiceError(400, "validation_error", f"{label} is invalid")
    if "\x00" in value:
        raise OpenCodeServiceError(400, "validation_error", f"{label} is invalid")
    return value


class OpenCodeService:
    """Small, fixed-surface client for the OpenCode 1.18.15 HTTP API."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 30,
        *,
        request=Request,
        opener=_open_without_redirects,
    ):
        self.enabled = bool(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._request_factory = request
        self._opener = opener
        if not self.enabled:
            self._authorization = ""
            return
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("OpenCode URL must be an HTTP(S) base URL without credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("OpenCode URL must not contain a query or fragment")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("OpenCode timeout must be from 1 to 300 seconds")
        if not isinstance(username, str) or not username or ":" in username or any(ord(char) < 32 or ord(char) == 127 for char in username):
            raise ValueError("OpenCode username is invalid")
        if not isinstance(password, str):
            raise ValueError("OpenCode credentials must be strings")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._authorization = f"Basic {token}"

    def _request(self, method: str, path: str, payload: Any = None, *, timeout: float | None = None) -> Any:
        if not self.enabled:
            raise OpenCodeServiceError(503, "ai_disabled", "Embedded AI is not configured")
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json", "Authorization": self._authorization}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = self._request_factory(self.base_url + path, data=data, headers=headers, method=method)
        try:
            response = self._opener(request, timeout=self.timeout if timeout is None else timeout)
            with response:
                raw = response.read(MAX_UPSTREAM_BODY + 1)
        except HTTPError as exc:
            exc.close()
            status = 502 if exc.code >= 500 else 400
            raise OpenCodeServiceError(status, "opencode_error", "OpenCode rejected the request") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise OpenCodeServiceError(502, "opencode_unavailable", "OpenCode is unavailable") from exc
        if len(raw) > MAX_UPSTREAM_BODY:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an oversized response")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid response") from exc

    def health(self) -> dict[str, Any]:
        result = self._request("GET", "/global/health")
        if not isinstance(result, dict) or result.get("healthy") is not True or not isinstance(result.get("version"), str):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid health response")
        return {"healthy": True, "version": result["version"][:64]}

    def providers(self) -> dict[str, Any]:
        result = self._request("GET", "/provider")
        if not isinstance(result, dict):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned invalid providers")
        providers = []
        model_count = 0
        for provider in result.get("all", [])[:200] if isinstance(result.get("all"), list) else []:
            if not isinstance(provider, dict) or not isinstance(provider.get("id"), str):
                continue
            models = []
            source_models = provider.get("models", {})
            opencode_authenticated = provider["id"] != "opencode" or any(
                isinstance(model, dict)
                and isinstance(model.get("cost"), dict)
                and isinstance(model["cost"].get("input"), (int, float))
                and model["cost"]["input"] > 0
                for model in source_models.values()
            ) if isinstance(source_models, dict) else provider["id"] != "opencode"
            if isinstance(source_models, dict):
                for model in list(source_models.values())[:200]:
                    if model_count >= 5000:
                        break
                    if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                        continue
                    if provider["id"] == "opencode" and not opencode_authenticated and model["id"] in UNUSABLE_ANONYMOUS_MODELS:
                        continue
                    models.append({
                        "id": model["id"][:128],
                        "name": str(model.get("name", model["id"]))[:256],
                        "toolCall": bool(model.get("tool_call")),
                        "status": str(model.get("status", "active"))[:32],
                    })
                    model_count += 1
            normalized_provider = {"id": provider["id"][:128], "name": str(provider.get("name", provider["id"]))[:256], "models": models}
            if provider["id"] == "opencode":
                normalized_provider["authenticated"] = opencode_authenticated
            providers.append(normalized_provider)
        defaults = result.get("default", {})
        safe_defaults = {}
        if isinstance(defaults, dict):
            safe_defaults = {
                key[:128]: value[:128]
                for key, value in list(defaults.items())[:200]
                if isinstance(key, str) and isinstance(value, str)
            }
        connected = [item[:128] for item in result.get("connected", [])[:200] if isinstance(item, str)] if isinstance(result.get("connected"), list) else []
        return {"providers": providers, "default": safe_defaults, "connected": connected}

    def auth_methods(self) -> dict[str, list[dict[str, Any]]]:
        result = self._request("GET", "/provider/auth")
        if not isinstance(result, dict):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned invalid authentication methods")
        normalized: dict[str, list[dict[str, Any]]] = {}
        for provider_id, methods in list(result.items())[:200]:
            if not isinstance(provider_id, str) or not isinstance(methods, list):
                continue
            normalized[provider_id[:128]] = []
            for method_index, method in enumerate(methods[:20]):
                if not isinstance(method, dict) or method.get("type") not in {"api", "oauth"}:
                    continue
                if provider_id == "anthropic" and method.get("type") == "oauth":
                    continue
                item: dict[str, Any] = {
                    "id": method_index,
                    "type": method["type"],
                    "label": str(method.get("label", method["type"]))[:256],
                    "name": str(method.get("label", method["type"]))[:256],
                }
                prompts = []
                for prompt in method.get("prompts", [])[:20] if isinstance(method.get("prompts"), list) else []:
                    if not isinstance(prompt, dict) or prompt.get("type") not in {"text", "select"}:
                        continue
                    safe_prompt = {
                        "type": prompt["type"], "key": str(prompt.get("key", ""))[:128],
                        "message": str(prompt.get("message", ""))[:512],
                    }
                    if isinstance(prompt.get("placeholder"), str):
                        safe_prompt["placeholder"] = prompt["placeholder"][:256]
                    when = prompt.get("when")
                    if isinstance(when, dict) and when.get("op") in {"eq", "neq"}:
                        safe_prompt["when"] = {
                            "key": str(when.get("key", ""))[:128], "op": when["op"],
                            "value": str(when.get("value", ""))[:256],
                        }
                    if prompt["type"] == "select" and isinstance(prompt.get("options"), list):
                        safe_prompt["options"] = [
                            {"label": str(option.get("label", ""))[:256], "value": str(option.get("value", ""))[:256], "hint": str(option.get("hint", ""))[:256]}
                            for option in prompt["options"][:50] if isinstance(option, dict)
                        ]
                    prompts.append(safe_prompt)
                if prompts:
                    item["prompts"] = prompts
                    item["inputs"] = [
                        {
                            "id": prompt.get("key", ""),
                            "name": prompt.get("key", ""),
                            "label": prompt.get("message", ""),
                            "type": prompt.get("type", "text"),
                            "required": "when" not in prompt,
                            **({"when": prompt["when"]} if "when" in prompt else {}),
                            **({"options": prompt["options"]} if "options" in prompt else {}),
                        }
                        for prompt in prompts if prompt.get("key")
                    ]
                normalized[provider_id[:128]].append(item)
        return normalized

    def skills(self) -> list[dict[str, str]]:
        result = self._request("GET", "/skill")
        if not isinstance(result, list):
            return []
        return [
            {
                "name": str(item.get("name", ""))[:128],
                "description": str(item.get("description", ""))[:512],
            }
            for item in result[:100]
            if isinstance(item, dict) and item.get("name") in SAFE_SKILLS
        ]

    def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"available": False, "enabled": False, "healthy": False, "providers": [], "authMethods": {}, "skills": []}
        health = self.health()
        discovery = self.providers()
        connected = set(discovery.pop("connected", []))
        auth_methods = self.auth_methods()
        auth_methods.setdefault("opencode", [{
            "id": 0,
            "type": "api",
            "label": "OpenCode Zen API key",
            "name": "OpenCode Zen API key",
            "helpUrl": "https://opencode.ai/auth",
            "helpLabel": "Create a free OpenCode Zen API key",
        }])
        discovery["providers"] = [
            provider for provider in discovery["providers"]
            if provider["id"] in connected or provider["id"] in auth_methods
        ]
        for provider in discovery["providers"]:
            provider["connected"] = provider["id"] in connected
        discovery["default"] = {
            provider_id: model_id for provider_id, model_id in discovery["default"].items()
            if provider_id in connected or provider_id in auth_methods
        }
        return {
            "available": True,
            "enabled": True,
            **health,
            **discovery,
            "authMethods": auth_methods,
            "skills": self.skills(),
        }

    def set_api_key(self, provider_id: Any, key: Any, inputs: Any = None) -> dict[str, bool]:
        provider_id = _identifier(provider_id, "providerId")
        key = _bounded_text(key, 16 * 1024, "key")
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, dict) or len(inputs) > 20 or any(not isinstance(name, str) or not isinstance(value, str) or len(value) > 4096 for name, value in inputs.items()):
            raise OpenCodeServiceError(400, "validation_error", "inputs are invalid")
        credential = {"type": "api", "key": key}
        if inputs:
            credential["metadata"] = inputs
        result = self._request("PUT", f"/auth/{quote(provider_id, safe='')}", credential)
        if result is not True:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid authentication response")
        return {"saved": True}

    def delete_api_key(self, provider_id: Any) -> dict[str, bool]:
        provider_id = _identifier(provider_id, "providerId")
        result = self._request("DELETE", f"/auth/{quote(provider_id, safe='')}")
        if result is not True:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid authentication response")
        return {"deleted": True}

    def oauth_authorize(self, provider_id: Any, method: Any, inputs: Any) -> dict[str, str] | None:
        provider_id = _identifier(provider_id, "providerId")
        if isinstance(method, bool) or not isinstance(method, int) or not 0 <= method <= 100:
            raise OpenCodeServiceError(400, "validation_error", "method is invalid")
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, dict) or len(inputs) > 20 or any(not isinstance(key, str) or not isinstance(value, str) or len(value) > 4096 for key, value in inputs.items()):
            raise OpenCodeServiceError(400, "validation_error", "inputs are invalid")
        result = self._request("POST", f"/provider/{quote(provider_id, safe='')}/oauth/authorize", {"method": method, "inputs": inputs})
        if result is None:
            return None
        if not isinstance(result, dict) or result.get("method") not in {"auto", "code"} or not isinstance(result.get("url"), str):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid OAuth response")
        return {"url": result["url"][:8192], "method": result["method"], "instructions": str(result.get("instructions", ""))[:4096]}

    def oauth_callback(self, provider_id: Any, method: Any, code: Any = None) -> dict[str, bool]:
        provider_id = _identifier(provider_id, "providerId")
        if isinstance(method, bool) or not isinstance(method, int) or not 0 <= method <= 100:
            raise OpenCodeServiceError(400, "validation_error", "method is invalid")
        code = _bounded_text(code, 16 * 1024, "code", optional=True)
        payload = {"method": method}
        if code is not None:
            payload["code"] = code
        if self._request("POST", f"/provider/{quote(provider_id, safe='')}/oauth/callback", payload) is not True:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid OAuth response")
        return {"authenticated": True}

    def create_session(self, title: Any = None, model: Any = None) -> dict[str, Any]:
        title = _bounded_text(title, 256, "title", optional=True)
        _model(model, optional=True)
        payload = {} if title is None else {"title": title}
        result = self._request("POST", "/session", payload)
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid session")
        return {"id": result["id"][:128], "title": str(result.get("title", title or ""))[:256]}

    def delete_session(self, session_id: Any) -> dict[str, bool]:
        session_id = _identifier(session_id, "sessionId")
        if self._request("DELETE", f"/session/{quote(session_id, safe='')}") is not True:
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid session response")
        return {"deleted": True}

    def prompt(self, session_id: Any, text: Any, model: Any, system: Any, *, allow_data: bool = False) -> dict[str, Any]:
        session_id = _identifier(session_id, "sessionId")
        text = _bounded_text(text, MAX_PROMPT_SIZE, "text")
        model = _model(model)
        system = _bounded_text(system, MAX_TEXT_SIZE, "system")
        if not isinstance(allow_data, bool):
            raise OpenCodeServiceError(400, "validation_error", "allow_data is invalid")
        prompt_tools = dict(PROMPT_TOOLS)
        prompt_tools["schema_read_query"] = allow_data
        payload = {
            "model": model,
            "system": system,
            "tools": prompt_tools,
            "parts": [{"type": "text", "text": text}],
        }
        try:
            result = self._request("POST", f"/session/{quote(session_id, safe='')}/message", payload)
        except OpenCodeServiceError as error:
            try:
                self._request("POST", f"/session/{quote(session_id, safe='')}/abort", timeout=min(self.timeout, 5))
            except OpenCodeServiceError:
                pass
            if error.code == "opencode_unavailable":
                raise OpenCodeServiceError(
                    504,
                    "provider_timeout",
                    "The AI provider did not respond. Connect another provider or try a different model.",
                ) from error
            raise
        return self._normalize_message(result)

    @staticmethod
    def _normalize_message(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict) or not isinstance(result.get("parts"), list):
            raise OpenCodeServiceError(502, "opencode_error", "OpenCode returned an invalid message")
        parts = []
        actions = []
        text_items = []
        text_size = 0
        tool_output_size = 0
        for part in result["parts"][:MAX_PARTS]:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"text", "reasoning"} and isinstance(part.get("text"), str):
                remaining = MAX_TEXT_SIZE - text_size
                value = part["text"].encode("utf-8")[:remaining].decode("utf-8", "ignore")
                text_size += len(value.encode("utf-8"))
                parts.append({"type": part["type"], "text": value})
                if part["type"] == "text":
                    text_items.append(value)
                continue
            if part.get("type") != "tool" or part.get("tool") not in CUSTOM_TOOLS:
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            output = state.get("output")
            safe_part = {"type": "tool", "tool": part["tool"], "status": str(state.get("status", "unknown"))[:32]}
            if isinstance(output, str):
                remaining = MAX_TOOL_OUTPUT_SIZE - tool_output_size
                safe_output = output.encode("utf-8")[:min(MAX_ACTION_SIZE, remaining)].decode("utf-8", "ignore")
                tool_output_size += len(safe_output.encode("utf-8"))
                safe_part["output"] = safe_output
                if output.startswith(ACTION_PREFIX) and len(actions) < MAX_ACTIONS and len(output.encode("utf-8")) <= MAX_ACTION_SIZE:
                    try:
                        action = json.loads(
                            output[len(ACTION_PREFIX):],
                            parse_constant=_reject_json_constant,
                        )
                    except (json.JSONDecodeError, RecursionError, ValueError):
                        action = None
                    if isinstance(action, dict):
                        actions.append(action)
            parts.append(safe_part)
        text = "\n".join(text_items).encode("utf-8")[:MAX_TEXT_SIZE].decode("utf-8", "ignore")
        if not text and not actions:
            raise OpenCodeServiceError(
                502,
                "provider_empty_response",
                "The AI provider returned an empty response. Try another free model.",
            )
        return {"text": text, "parts": parts, "actions": actions}
