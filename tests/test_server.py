import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.schema_store import SchemaStore
from schemii.server import CONTENT_SECURITY_POLICY, ThreadingHTTPServer, _is_local_request, _proposal_manifest_fallback, make_handler


class FakePostgresService:
    def __init__(self):
        self.calls = []
        self.profiles = []

    def list_profiles(self):
        return self.profiles

    def save_profile(self, profile_id, body):
        self.calls.append(("save_profile", profile_id, body))
        saved = {"id": profile_id or "pg_created", **{key: value for key, value in body.items() if key != "password"}}
        self.profiles = [saved]
        return saved

    def delete_profile(self, profile_id):
        self.calls.append(("delete_profile", profile_id))
        return {"deleted": profile_id}

    def list_history(self, profile_id, limit):
        self.calls.append(("list_history", profile_id, limit))
        return [{"id": "history_one"}]

    def preview_table_data(self, profile_id, namespace, table, offset, limit):
        self.calls.append(("preview_table_data", profile_id, namespace, table, offset, limit))
        return {"columns": [], "rows": [], "offset": offset, "nextOffset": offset, "hasMore": False}

    def execute_read_only_sql(self, profile_id, namespace, statement):
        self.calls.append(("execute_read_only_sql", profile_id, namespace, statement))
        return {"columns": [{"name": "answer"}], "rows": [[1]], "rowCount": 1, "truncated": False}

    def list_namespaces(self, profile_id):
        self.calls.append(("list_namespaces", profile_id))
        return ["public"]

    def catalog_status(self, profile_id, namespace):
        self.calls.append(("catalog_status", profile_id, namespace))
        return {"profileId": profile_id, "namespace": namespace, "fingerprint": "live"}

    def test_profile(self, profile_id):
        self.calls.append(("test_profile", profile_id))
        return {"ok": True}

    def introspect(self, profile_id, namespace):
        self.calls.append(("introspect", profile_id, namespace))
        return {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}

    def preview(self, profile_id, namespace, schema, allow_destructive):
        self.calls.append(("preview", profile_id, namespace, schema, allow_destructive))
        return {"id": "plan_one", "steps": [], "warnings": []}

    def apply(self, profile_id, plan_id, confirm_destructive):
        self.calls.append(("apply", profile_id, plan_id, confirm_destructive))
        return {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}


class FakeAIService:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append(("status",))
        return {"enabled": True, "healthy": True, "version": "1.18.15", "providers": [], "authMethods": {}}

    def set_api_key(self, provider_id, key, inputs=None):
        self.calls.append(("set_api_key", provider_id, key, inputs))
        return {"saved": True}

    def delete_api_key(self, provider_id):
        self.calls.append(("delete_api_key", provider_id))
        return {"deleted": True}

    def oauth_authorize(self, provider_id, method, inputs):
        self.calls.append(("oauth_authorize", provider_id, method, inputs))
        return {"url": "https://login.example", "method": "code", "instructions": "Enter code"}

    def oauth_callback(self, provider_id, method, code=None):
        self.calls.append(("oauth_callback", provider_id, method, code))
        return {"authenticated": True}

    def create_session(self, title=None, model=None):
        self.calls.append(("create_session", title, model))
        return {"id": "ses_1", "title": title or ""}

    def list_sessions(self):
        self.calls.append(("list_sessions",))
        return {"sessions": [{"id": "ses_1", "title": "Schema chat", "updatedAt": 1234}]}

    def session_messages(self, session_id):
        self.calls.append(("session_messages", session_id))
        return {"messages": [{"role": "user", "text": "Add events"}]}

    def delete_session(self, session_id):
        self.calls.append(("delete_session", session_id))
        return {"deleted": True}

    def prompt(self, session_id, text, model, system, *, allow_data=False):
        self.calls.append(("prompt", session_id, text, model, system, allow_data))
        return {"text": "Proposed.", "parts": [{"type": "text", "text": "Proposed."}], "actions": []}

    def verify_session(self, session_id):
        self.calls.append(("verify_session", session_id))
        return session_id

    def activity(self, session_id):
        self.calls.append(("activity", session_id))
        yield {"type": "connection", "state": "connected"}
        yield {"type": "session", "state": "busy"}
        yield {"type": "part", "kind": "tool", "key": "prt_1", "tool": "schema_add_table", "state": "running"}
        yield {"type": "session", "state": "idle"}


class FakeExampleInstaller:
    def __init__(self):
        self.calls = []

    def restore(self):
        self.calls.append(("restore",))
        return {"installed": ["schemii_example_local"], "preserved": [], "completed": ["local"], "errors": []}


class QuietHandlerMixin:
    def log_message(self, format, *args):
        pass


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = FakePostgresService()
        self.ai_service = FakeAIService()
        self.example_installer = FakeExampleInstaller()
        self.store = SchemaStore(Path(self.temporary_directory.name) / "schemas")
        handler = make_handler(
            ROOT / "src" / "schemii" / "web", self.service, self.store, "session-token",
            server_id="server-start-id",
            ai_service=self.ai_service,
            example_installer=self.example_installer,
        )
        quiet_handler = type("QuietSchemiiHandler", (QuietHandlerMixin, handler), {})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), quiet_handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary_directory.cleanup()

    def request(self, path, method="GET", payload=None, content_type="application/json", authorized=False, headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(f"{self.base_url}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", content_type)
        if authorized:
            request.add_header("X-Schemii-Token", "session-token")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urlopen(request) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            try:
                return error.code, error.read(), error.headers
            finally:
                error.close()

    def test_static_and_session_routes(self):
        status, body, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Schemii", body)
        self.assertEqual(headers["Content-Security-Policy"], CONTENT_SECURITY_POLICY)
        self.assertEqual(self.request("/.git/config")[0], 404)
        self.assertEqual(self.request("/src/schemii/server.py")[0], 404)

        status, body, _ = self.request("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"token": "session-token", "serverId": "server-start-id"})

    def test_example_restore_requires_session_and_returns_inert_install_summary(self):
        self.assertEqual(self.request("/api/examples/restore", "POST")[0], 403)
        status, body, _ = self.request("/api/examples/restore", "POST", authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["installed"], ["schemii_example_local"])
        self.assertEqual(self.example_installer.calls, [("restore",)])

    def test_shutdown_requires_local_session_and_stops_server_after_response(self):
        status, body, _ = self.request("/api/shutdown", "POST")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_session")
        self.assertTrue(self.thread.is_alive())

        status, body, _ = self.request(
            "/api/shutdown", "POST", authorized=True,
            headers={"Origin": "https://example.com"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "forbidden")
        self.assertTrue(self.thread.is_alive())

        status, body, _ = self.request("/api/shutdown", "POST", authorized=True)
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body), {"shuttingDown": True})
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def test_loopback_proxy_mode_still_requires_a_local_host_and_origin(self):
        self.assertFalse(_is_local_request("172.17.0.1", "localhost:8080", None, False))
        self.assertTrue(_is_local_request("172.17.0.1", "localhost:8080", None, True))
        self.assertFalse(_is_local_request("172.17.0.1", "example.com", None, True))
        self.assertFalse(_is_local_request("172.17.0.1", "localhost:8080", "https://example.com", True))

    def test_profile_routes_require_session_and_redact_passwords(self):
        self.assertEqual(self.request("/api/postgres/profiles")[0], 403)
        profile = {
            "name": "Local", "host": "127.0.0.1", "port": 5432,
            "dbname": "demo", "user": "developer", "password": "secret",
            "sslmode": "prefer", "timeout": 5,
        }
        status, body, _ = self.request("/api/postgres/profiles", "POST", profile, authorized=True)
        self.assertEqual(status, 201)
        self.assertNotIn("password", json.loads(body))
        status, body, _ = self.request("/api/postgres/profiles", authorized=True)
        self.assertEqual(status, 200)
        self.assertNotIn("password", json.loads(body)["profiles"][0])

        self.assertEqual(self.request("/api/postgres/profiles/local", "PUT", profile, authorized=True)[0], 200)
        self.assertEqual(self.request("/api/postgres/profiles/local", "DELETE", authorized=True)[0], 200)

    def test_data_and_sql_routes_validate_and_forward(self):
        data_path = "/api/postgres/profiles/local/data?namespace=public&table=events&offset=50&limit=25"
        self.assertEqual(self.request(data_path)[0], 403)
        self.assertEqual(self.request(data_path, authorized=True)[0], 200)
        self.assertEqual(
            self.service.calls[-1],
            ("preview_table_data", "local", "public", "events", 50, 25),
        )
        status, body, _ = self.request(
            "/api/postgres/profiles/local/data?namespace=public&table=events&offset=nope",
            authorized=True,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "validation_error")

        sql_path = "/api/postgres/profiles/local/sql"
        payload = {"namespace": "public", "sql": "SELECT 1 AS answer"}
        self.assertEqual(self.request(sql_path, "POST", payload)[0], 403)
        self.assertEqual(self.request(sql_path, "POST", payload, authorized=True)[0], 200)
        self.assertEqual(
            self.service.calls[-1],
            ("execute_read_only_sql", "local", "public", "SELECT 1 AS answer"),
        )

    def test_introspection_profile_and_history_routes_forward_contracts(self):
        for path in (
            "/api/postgres/profiles/local/namespaces",
            "/api/postgres/profiles/local/fingerprint?namespace=public",
            "/api/postgres/history?profileId=local&limit=25",
        ):
            self.assertEqual(self.request(path)[0], 403)
            self.assertEqual(self.request(path, authorized=True)[0], 200)

        self.assertIn(("list_namespaces", "local"), self.service.calls)
        self.assertIn(("catalog_status", "local", "public"), self.service.calls)
        self.assertIn(("list_history", "local", 25), self.service.calls)

    def test_test_introspect_preview_and_apply_routes_forward_contracts(self):
        schema = {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}
        requests = [
            ("/api/postgres/profiles/local/test", {}),
            ("/api/postgres/profiles/local/introspect", {"namespace": "public"}),
            ("/api/postgres/profiles/local/preview", {"namespace": "public", "schema": schema, "allowDestructive": True}),
            ("/api/postgres/profiles/local/plans/plan_one/apply", {"confirmDestructive": True}),
        ]
        for path, payload in requests:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, "POST", payload)[0], 403)
                self.assertEqual(self.request(path, "POST", payload, authorized=True)[0], 200)

        self.assertIn(("test_profile", "local"), self.service.calls)
        self.assertIn(("introspect", "local", "public"), self.service.calls)
        self.assertIn(("preview", "local", "public", schema, True), self.service.calls)
        self.assertIn(("apply", "local", "plan_one", True), self.service.calls)

    def test_schema_route_rejects_invalid_content_type(self):
        payload = {
            "id": "schema_one",
            "schema": {"projectName": "Demo", "tables": [], "relationships": [], "functions": []},
        }
        self.assertEqual(
            self.request("/api/schemas/schema_one", "PUT", payload, content_type="text/plain")[0],
            415,
        )

    def test_ai_routes_require_session_and_forward_auth_and_sessions(self):
        requests = [
            ("/api/ai/auth/api", "POST", {"providerId": "anthropic", "key": "secret"}),
            ("/api/ai/auth/oauth/authorize", "POST", {"providerId": "anthropic", "method": 1, "inputs": {"region": "us"}}),
            ("/api/ai/auth/oauth/callback", "POST", {"providerId": "anthropic", "method": 1, "code": "code"}),
            ("/api/ai/sessions", "POST", {"title": "Schema chat", "model": {"providerID": "anthropic", "modelID": "claude"}}),
            ("/api/ai/auth/anthropic", "DELETE", None),
            ("/api/ai/sessions/ses_1", "DELETE", None),
        ]
        self.assertEqual(self.request("/api/ai/status")[0], 403)
        self.assertEqual(self.request("/api/ai/status", authorized=True)[0], 200)
        for path, method, payload in requests:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, method, payload)[0], 403)
                self.assertIn(self.request(path, method, payload, authorized=True)[0], {200, 201})

        self.assertIn(("set_api_key", "anthropic", "secret", None), self.ai_service.calls)
        self.assertIn(("oauth_authorize", "anthropic", 1, {"region": "us"}), self.ai_service.calls)
        self.assertIn(("oauth_callback", "anthropic", 1, "code"), self.ai_service.calls)
        self.assertIn(("delete_api_key", "anthropic"), self.ai_service.calls)
        self.assertIn(("delete_session", "ses_1"), self.ai_service.calls)

    def test_schema_manifest_fallback_is_bounded_inert_and_hidden_from_chat_text(self):
        action = {
            "type": "populate_schema",
            "purpose": "Teaching example",
            "tables": [{"name": "authors", "purpose": "Authors", "columns": [{"name": "id", "type": "uuid", "primary": True}]}],
            "relationships": [],
            "requiresConfirmation": True,
        }
        visible = "Prepared a complete teaching schema."
        manifest = "SCHEMII_PROPOSALS:" + json.dumps([action])
        response = {"text": f"{visible}\n{manifest}", "parts": [{"type": "text", "text": f"{visible}\n{manifest}"}], "actions": []}

        repaired = _proposal_manifest_fallback(response)

        self.assertEqual(repaired["actions"], [action])
        self.assertEqual(repaired["text"], visible)
        self.assertEqual(repaired["parts"], [{"type": "text", "text": visible}])
        self.assertNotIn("SCHEMII_PROPOSALS", json.dumps(repaired["parts"]))
        project = {**response, "text": 'SCHEMII_PROPOSALS:[{"type":"create_project","projectName":"Demo","requiresConfirmation":true}]'}
        self.assertEqual(_proposal_manifest_fallback(project)["actions"][0]["type"], "create_project")
        mixed = {**response, "text": 'SCHEMII_PROPOSALS:[{"type":"unknown_action"}]'}
        self.assertIs(_proposal_manifest_fallback(mixed), mixed)
        query = {**response, "text": 'SCHEMII_PROPOSALS:[{"type":"schema_read_query","sql":"SELECT 1"}]'}
        self.assertIs(_proposal_manifest_fallback(query), query)
        self.assertEqual(_proposal_manifest_fallback(query, allow_data=True)["actions"][0]["type"], "schema_read_query")
        existing = {**response, "actions": [{"type": "add_table"}]}
        self.assertIs(_proposal_manifest_fallback(existing), existing)

    def test_ai_history_routes_require_session_and_return_normalized_history(self):
        for path in ("/api/ai/sessions", "/api/ai/sessions/ses_1/messages"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 403)
                status, body, _ = self.request(path, authorized=True)
                self.assertEqual(status, 200)
                self.assertNotIn("secret", body.decode().lower())

        self.assertIn(("list_sessions",), self.ai_service.calls)
        self.assertIn(("session_messages", "ses_1"), self.ai_service.calls)

    def test_ai_activity_stream_requires_session_and_returns_only_normalized_events(self):
        path = "/api/ai/sessions/ses_1/activity"
        self.assertEqual(self.request(path)[0], 403)

        status, body, headers = self.request(path, authorized=True)

        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/x-ndjson")
        self.assertEqual([json.loads(line) for line in body.decode().splitlines()], [
            {"type": "connection", "state": "connected"},
            {"type": "session", "state": "busy"},
            {"type": "part", "kind": "tool", "key": "prt_1", "tool": "schema_add_table", "state": "running"},
            {"type": "session", "state": "idle"},
        ])
        self.assertIn(("verify_session", "ses_1"), self.ai_service.calls)
        self.assertIn(("activity", "ses_1"), self.ai_service.calls)

    def test_ai_message_loads_schema_and_sends_bounded_redacted_context(self):
        self.service.profiles = [{
            "id": "local", "name": "Local", "host": "db.internal", "port": 5432,
            "dbname": "demo", "user": "admin", "password": "profile-secret",
        }, {
            "id": "reporting", "name": "Reporting", "host": "reports.internal", "port": 5432,
            "dbname": "reports", "user": "reader", "password": "other-secret",
        }]
        record = {
            "id": "schema_one",
            "configPath": "/home/user/private.json",
            "schema": {
                "projectName": "Demo\x01 project",
                "tables": [{
                    "id": "table_events", "name": "events", "password": "table-secret",
                    "columns": [{"id": "column_id", "name": "id", "type": "uuid", "nullable": False, "rows": ["row-secret"]}],
                    "primaryKey": {"id": "pk_events", "name": "events_pkey", "columnIds": ["column_id"], "definition": "PRIMARY KEY (id)"},
                }],
                "relationships": [], "functions": [], "views": [],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public", "configPath": "/private"},
                "rows": [{"password": "row-secret"}],
            },
        }
        self.store.save("schema_one", record, expected_layout_token=None, layout_protocol=None)
        self.store.save("schema_two", {"id": "schema_two", "schema": {"projectName": "Orders", "tables": [], "relationships": [], "functions": [], "configPath": "/secret"}}, expected_layout_token=None, layout_protocol=None)
        model = {"providerID": "anthropic", "modelID": "claude"}
        payload = {
            "text": "Add an audit column", "model": model, "schemaId": "schema_one",
            "accessLevel": "schema", "profileId": "local", "namespace": "public",
        }

        status, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", payload, authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["text"], "Proposed.")
        call = self.ai_service.calls[-1]
        self.assertEqual(call[0:2], ("prompt", "ses_1"))
        self.assertEqual(call[3], model)
        context_and_text = call[2]
        self.assertIn('"accessLevel":"schema"', context_and_text)
        self.assertIn('"database":"demo"', context_and_text)
        self.assertIn('"primaryKey"', context_and_text)
        self.assertIn('"schemaId":"schema_two"', context_and_text)
        self.assertIn('"projectName":"Orders"', context_and_text)
        self.assertIn('"connection":{"type":"local-project"}', context_and_text)
        self.assertIn('"type":"remote-db"', context_and_text)
        self.assertIn('"profileId":"reporting"', context_and_text)
        self.assertIn('"database":"reports"', context_and_text)
        self.assertIn("Add an audit column", context_and_text)
        for secret in ("profile-secret", "other-secret", "table-secret", "row-secret", "db.internal", "reports.internal", "admin", "reader", "/home/user", "/private", "configPath"):
            self.assertNotIn(secret, context_and_text)
        self.assertFalse(any(item[0] == "list_namespaces" for item in self.service.calls))
        self.assertIn("proposals are not executed", call[4].lower())
        self.assertIn("creation does not require an existing project id", call[4].lower())
        self.assertIn("call schema_project_create", call[4].lower())
        self.assertFalse(call[5])

        payload["accessLevel"] = "data"
        self.assertEqual(self.request("/api/ai/sessions/ses_1/messages", "POST", payload, authorized=True)[0], 200)
        self.assertTrue(self.ai_service.calls[-1][5])
        self.assertNotIn("row-secret", self.ai_service.calls[-1][2])

    def test_ai_message_metadata_omits_schema_and_rejects_unknown_schema(self):
        record = {"id": "schema_one", "schema": {"projectName": "Demo", "tables": [{"id": "t", "name": "secret_table", "columns": []}], "relationships": [], "functions": []}}
        self.store.save("schema_one", record, expected_layout_token=None, layout_protocol=None)
        payload = {
            "text": "Describe it", "model": {"providerID": "anthropic", "modelID": "claude"},
            "schemaId": "schema_one", "accessLevel": "metadata",
        }
        self.assertEqual(self.request("/api/ai/sessions/ses_1/messages", "POST", payload, authorized=True)[0], 200)
        self.assertNotIn("secret_table", self.ai_service.calls[-1][2])
        payload["schemaId"] = "missing"
        self.assertEqual(self.request("/api/ai/sessions/ses_1/messages", "POST", payload, authorized=True)[0], 404)


if __name__ == "__main__":
    unittest.main()
