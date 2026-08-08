import base64
import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schema_foundry.opencode_service import CUSTOM_TOOLS, OpenCodeService, OpenCodeServiceError


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size=-1):
        return self.body[:size]


class Opener:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return Response(payload)


def request_json(request):
    return None if request.data is None else json.loads(request.data)


class OpenCodeServiceTests(unittest.TestCase):
    def service(self, opener):
        return OpenCodeService("http://127.0.0.1:4096/", "opencode", "secret", 12, opener=opener)

    def test_status_uses_fixed_discovery_paths_and_redacts_provider_secrets(self):
        opener = Opener(
            {"healthy": True, "version": "1.18.15"},
            {
                "all": [
                    {
                        "id": "anthropic", "name": "Anthropic", "key": "provider-secret",
                        "options": {"apiKey": "option-secret"},
                        "models": {"claude": {"id": "claude", "name": "Claude", "tool_call": True, "headers": {"Authorization": "secret"}}},
                    },
                    {"id": "opencode", "name": "OpenCode", "models": {
                        "deepseek-v4-flash-free": {"id": "deepseek-v4-flash-free", "name": "DeepSeek Free", "cost": {"input": 0}},
                        "north-mini-code-free": {"id": "north-mini-code-free", "name": "North Free", "cost": {"input": 0}},
                    }},
                ],
                "default": {"anthropic": "claude", "opencode": "deepseek-v4-flash-free"}, "connected": ["anthropic", "opencode"],
            },
            {"anthropic": [{"type": "api", "label": "API key"}]},
            [{"name": "schema-foundry-help", "description": "Product help"}],
        )
        result = self.service(opener).status()

        self.assertEqual([call[0].full_url for call in opener.calls], [
            "http://127.0.0.1:4096/global/health",
            "http://127.0.0.1:4096/provider",
            "http://127.0.0.1:4096/provider/auth",
            "http://127.0.0.1:4096/skill",
        ])
        expected_auth = "Basic " + base64.b64encode(b"opencode:secret").decode("ascii")
        self.assertTrue(all(call[0].headers["Authorization"] == expected_auth for call in opener.calls))
        self.assertEqual(result["version"], "1.18.15")
        self.assertEqual(result["providers"][0]["models"][0]["id"], "claude")
        self.assertTrue(result["providers"][0]["connected"])
        zen = next(provider for provider in result["providers"] if provider["id"] == "opencode")
        self.assertTrue(zen["connected"])
        self.assertFalse(zen["authenticated"])
        self.assertEqual([model["id"] for model in zen["models"]], ["deepseek-v4-flash-free"])
        self.assertEqual(result["authMethods"]["opencode"][0]["helpUrl"], "https://opencode.ai/auth")
        self.assertEqual(result["authMethods"]["anthropic"][0]["id"], 0)
        self.assertEqual(result["skills"][0]["name"], "schema-foundry-help")
        self.assertNotIn("secret", json.dumps(result).lower())

    def test_auth_oauth_and_session_methods_pin_upstream_shapes(self):
        opener = Opener(True, True, {"url": "https://login.example/", "method": "code", "instructions": "Enter code"}, True, {"id": "ses_1", "title": "Chat", "directory": "/secret/path"}, True)
        service = self.service(opener)

        self.assertEqual(service.set_api_key("anthropic", "api-secret"), {"saved": True})
        self.assertEqual(service.delete_api_key("anthropic"), {"deleted": True})
        service.oauth_authorize("anthropic", 1, {"region": "us"})
        service.oauth_callback("anthropic", 1, "callback-code")
        self.assertEqual(service.create_session("Chat", {"providerID": "openrouter", "modelID": "anthropic/claude"}), {"id": "ses_1", "title": "Chat"})
        self.assertEqual(service.delete_session("ses_1"), {"deleted": True})

        self.assertEqual([(call[0].method, call[0].full_url.removeprefix("http://127.0.0.1:4096"), request_json(call[0])) for call in opener.calls], [
            ("PUT", "/auth/anthropic", {"type": "api", "key": "api-secret"}),
            ("DELETE", "/auth/anthropic", None),
            ("POST", "/provider/anthropic/oauth/authorize", {"method": 1, "inputs": {"region": "us"}}),
            ("POST", "/provider/anthropic/oauth/callback", {"method": 1, "code": "callback-code"}),
            ("POST", "/session", {"title": "Chat"}),
            ("DELETE", "/session/ses_1", None),
        ])

    def test_authenticated_zen_catalog_keeps_all_models(self):
        opener = Opener({
            "all": [{"id": "opencode", "name": "OpenCode Zen", "models": {
                "north-mini-code-free": {"id": "north-mini-code-free", "name": "North Free", "cost": {"input": 0}},
                "paid": {"id": "paid", "name": "Paid", "cost": {"input": 1}},
            }}],
            "default": {"opencode": "north-mini-code-free"},
            "connected": ["opencode"],
        })

        provider = self.service(opener).providers()["providers"][0]

        self.assertTrue(provider["authenticated"])
        self.assertEqual([model["id"] for model in provider["models"]], ["north-mini-code-free", "paid"])

    def test_prompt_enables_only_foundry_tools_and_normalizes_actions(self):
        valid = {"kind": "add_table", "table": {"name": "events"}}
        opener = Opener({"info": {"path": {"cwd": "/secret"}}, "parts": [
            {"type": "text", "text": "I propose a table."},
            {"type": "tool", "tool": "schema_add_table", "state": {"status": "completed", "output": "SCHEMA_FOUNDRY_ACTION:" + json.dumps(valid)}},
            {"type": "tool", "tool": "schema_add_table", "state": {"status": "completed", "output": " SCHEMA_FOUNDRY_ACTION:{}"}},
            {"type": "tool", "tool": "bash", "state": {"status": "completed", "output": "SCHEMA_FOUNDRY_ACTION:{}"}},
        ]})
        result = self.service(opener).prompt(
            "ses_1", "Create events", {"providerID": "anthropic", "modelID": "claude"}, "Fixed system",
            allow_data=True,
        )

        payload = request_json(opener.calls[0][0])
        self.assertEqual(opener.calls[0][0].full_url, "http://127.0.0.1:4096/session/ses_1/message")
        self.assertEqual(payload["parts"], [{"type": "text", "text": "Create events"}])
        self.assertTrue(all(payload["tools"][tool] for tool in CUSTOM_TOOLS))
        self.assertTrue(payload["tools"]["skill"])
        self.assertTrue(all(payload["tools"][tool] is False for tool in ("bash", "read", "write", "edit", "webfetch", "task")))
        self.assertEqual(result["text"], "I propose a table.")
        self.assertEqual(result["actions"], [valid])
        self.assertEqual(len(result["parts"]), 3)
        self.assertNotIn("cwd", json.dumps(result))

    def test_disabled_invalid_ids_and_upstream_failures_are_sanitized(self):
        self.assertEqual(OpenCodeService("", "opencode", "").status()["enabled"], False)
        opener = Opener(HTTPError("http://upstream/private", 401, "bad", {}, BytesIO(b'{"key":"leaked"}')))
        service = self.service(opener)
        with self.assertRaises(OpenCodeServiceError) as error:
            service.set_api_key("anthropic", "top-secret")
        self.assertEqual(error.exception.payload["error"]["message"], "OpenCode rejected the request")
        self.assertNotIn("secret", str(error.exception.payload))
        with self.assertRaises(OpenCodeServiceError):
            service.delete_session("../../global/health")

        unavailable = self.service(Opener(URLError("connection detail with password")))
        with self.assertRaises(OpenCodeServiceError) as error:
            unavailable.health()
        self.assertEqual(error.exception.payload["error"]["code"], "opencode_unavailable")

    def test_prompt_timeout_has_bounded_abort_and_clear_error(self):
        opener = Opener(TimeoutError("provider stalled"), TimeoutError("abort stalled"))
        service = self.service(opener)

        with self.assertRaises(OpenCodeServiceError) as error:
            service.prompt("ses_1", "Hello", {"providerID": "openai", "modelID": "gpt"}, "Fixed system")

        self.assertEqual(error.exception.status, 504)
        self.assertEqual(error.exception.code, "provider_timeout")
        self.assertEqual([call[1] for call in opener.calls], [12, 5])
        self.assertEqual(opener.calls[1][0].full_url, "http://127.0.0.1:4096/session/ses_1/abort")

    def test_empty_provider_response_is_rejected(self):
        service = self.service(Opener({"parts": []}))

        with self.assertRaises(OpenCodeServiceError) as error:
            service.prompt("ses_1", "Hello", {"providerID": "opencode", "modelID": "north-mini-code-free"}, "Fixed system")

        self.assertEqual(error.exception.code, "provider_empty_response")


if __name__ == "__main__":
    unittest.main()
