import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.schema_store import SchemaStore, SchemaStoreError
from schemii.ai_authority import AiAuthority
from schemii.ai_chat_store import AiChatStore
from schemii.postgres_service import PostgresService, PostgresServiceError
from schemii.server import CONTENT_SECURITY_POLICY, _is_local_request, make_handler
from tests.http_test_support import FakePostgresService, RunningHttpServer
from tests.test_ai_postgres_writes import TARGET, WriteConnection


class FakeAIService:
    def __init__(self):
        self.calls = []
        self.session_title = ""
        self.prompt_response = {"text": "Proposed.", "parts": [{"type": "text", "text": "Proposed."}], "actions": []}

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
        self.session_title = title or ""
        return {"id": "ses_1", "title": title or ""}

    def list_sessions(self):
        self.calls.append(("list_sessions",))
        return {"sessions": [{"id": "ses_1", "title": self.session_title or "Schema chat", "updatedAt": 1234}]}

    def session_messages(self, session_id):
        self.calls.append(("session_messages", session_id))
        return {"messages": [{"role": "user", "text": "Add events"}]}

    def delete_session(self, session_id):
        self.calls.append(("delete_session", session_id))
        return {"deleted": True}

    def prompt(self, session_id, text, model, system, *, allow_data=False, allow_write=False, allow_structured_data=False, allow_raw_write=False, allow_schema=True):
        self.calls.append(("prompt", session_id, text, model, system, allow_data, allow_write, allow_structured_data, allow_raw_write, allow_schema))
        return self.prompt_response

    def verify_session(self, session_id):
        self.calls.append(("verify_session", session_id))
        return session_id

    def session_identity(self, session_id):
        self.calls.append(("session_identity", session_id))
        return {"id": session_id, "title": self.session_title}

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


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = FakePostgresService()
        self.ai_service = FakeAIService()
        self.example_installer = FakeExampleInstaller()
        self.store = SchemaStore(Path(self.temporary_directory.name) / "schemas")
        self.chat_store = AiChatStore(Path(self.temporary_directory.name) / "chats")
        handler = make_handler(
            ROOT / "src" / "schemii" / "web", self.service, self.store, "session-token",
            server_id="server-start-id",
            ai_authority=AiAuthority(Path(self.temporary_directory.name) / "authority", "schemii"),
            ai_chat_store=self.chat_store,
            ai_service=self.ai_service,
            example_installer=self.example_installer,
        )
        self.http = RunningHttpServer(handler)
        self.server = self.http.server
        self.thread = self.http.thread

    def tearDown(self):
        self.http.close()
        self.temporary_directory.cleanup()

    def request(self, path, method="GET", payload=None, content_type="application/json", authorized=False, headers=None):
        return self.http.request(path, method, payload, content_type, authorized, headers)

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
            ("execute_read_only_sql", "local", "public", "SELECT 1 AS answer", {
                "database": None, "expected_profile_fingerprint": None, "allow_explain": True, "max_rows": 500,
                "max_columns": 100, "max_result_bytes": 1024 * 1024,
            }),
        )
        exact_payload = {"database": "demo", **payload}
        self.assertEqual(self.request(sql_path, "POST", exact_payload, authorized=True)[0], 200)
        self.assertEqual(self.request(sql_path, "POST", {**payload, "extra": True}, authorized=True)[0], 400)

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

        relation_path = "/api/postgres/profiles/local/relations?database=demo&namespace=public"
        self.assertEqual(self.request(relation_path)[0], 403)
        self.assertEqual(self.request(relation_path, authorized=True)[0], 200)
        self.assertIn(("list_relations", "local", "demo", "public"), self.service.calls)
        inspect_path = "/api/postgres/profiles/local/relation?database=demo&namespace=public&relation=events"
        self.assertEqual(self.request(inspect_path)[0], 403)
        self.assertEqual(self.request(inspect_path, authorized=True)[0], 200)
        self.assertIn(("inspect_relation", "local", "demo", "public", "events", None, None), self.service.calls)
        verify_path = inspect_path + "&expectedKind=table&expectedFingerprint=" + "a" * 64
        self.assertEqual(self.request(verify_path, authorized=True)[0], 200)
        self.assertIn(("inspect_relation", "local", "demo", "public", "events", "table", "a" * 64), self.service.calls)
        for route in ("preview", "verify", "query", "detail"):
            path = f"/api/postgres/profiles/local/relation/{route}"
            self.assertEqual(self.request(path, "POST", {}, authorized=True)[0], 404)

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
        self.assertIn(("preview", "local", "public", schema, True, True), self.service.calls)
        self.assertIn(("apply", "local", "plan_one", True), self.service.calls)

    def test_exact_view_preview_apply_and_post_commit_schema_sync(self):
        record = {
            "id": "schema_one",
            "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [],
                "views": [{
                    "id": "view_summary", "name": "summary", "namespace": "public",
                    "materialized": False, "definition": 'CREATE VIEW "public"."summary" AS SELECT 1',
                }],
                "layout": {"version": 2, "layers": {"views": {"objects": {"view_summary": {"x": 10, "y": 20}}}}},
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            },
        }
        saved = self.store.save("schema_one", record, expected_layout_token=None, layout_protocol=None)
        self.service.view_layout_token = saved["layoutToken"]
        preview = {
            "schemaId": "schema_one", "expectedSchemaRevision": saved["revision"], "layoutToken": saved["layoutToken"],
            "database": "demo", "namespace": "public", "relation": "summary",
            "operation": "upsert",
            "expectation": {"kind": "view", "fingerprint": "a" * 64},
            "desired": {"kind": "view", "definition": 'CREATE VIEW "public"."summary" AS SELECT 2'},
            "allowDestructive": False,
        }
        preview_path = "/api/postgres/profiles/local/views/preview"
        self.assertEqual(self.request(preview_path, "POST", preview)[0], 403)
        self.assertEqual(self.request(preview_path, "POST", {**preview, "extra": True}, authorized=True)[0], 400)
        self.assertEqual(self.request(preview_path, "POST", preview, authorized=True)[0], 200)
        self.assertIn((
            "preview_view_mutation", "local", "demo", "public", "summary", "upsert", preview["expectation"], preview["desired"], False,
            {"schemaId": "schema_one", "expectedSchemaRevision": saved["revision"], "layoutToken": saved["layoutToken"], "savedViewId": "view_summary"},
        ), self.service.calls)

        apply_path = "/api/postgres/profiles/local/view-plans/plan_view/apply"
        self.assertEqual(self.request(apply_path, "POST", {}, authorized=True)[0], 400)
        status, body, _ = self.request(apply_path, "POST", {"confirmDestructive": False}, authorized=True)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertTrue(result["applied"])
        self.assertEqual(result["schemaSync"]["status"], "saved")
        binding_call = self.service.calls.index(("view_mutation_binding", "local", "plan_view"))
        apply_call = self.service.calls.index(("apply_view_mutation", "local", "plan_view", False))
        self.assertLess(binding_call, apply_call)
        current = self.store.get("schema_one")
        self.assertEqual(current["schema"]["views"][0]["queryDefinition"], "SELECT 2")
        self.assertEqual(current["schema"]["layout"], record["schema"]["layout"])

    def test_apply_revalidates_binding_before_postgres_mutation(self):
        record = {
            "id": "schema_one", "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [],
                "views": [{"id": "view_summary", "name": "summary", "namespace": "public", "definition": "old"}],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            },
        }
        saved = self.store.save("schema_one", record, expected_layout_token=None, layout_protocol=None)
        self.service.view_layout_token = saved["layoutToken"]
        changed = self.store.get("schema_one")
        changed["schema"]["projectName"] = "Concurrent edit"
        self.store.save("schema_one", changed, expected_layout_token=saved["layoutToken"], layout_protocol="2")

        status, body, _ = self.request(
            "/api/postgres/profiles/local/view-plans/plan_view/apply", "POST",
            {"confirmDestructive": False}, authorized=True,
        )
        self.assertEqual(status, 409)
        result = json.loads(body)
        self.assertEqual(result["error"]["code"], "schema_conflict")
        self.assertEqual(sum(call[0] == "apply_view_mutation" for call in self.service.calls), 0)

    def test_apply_sync_appends_new_expected_absent_view(self):
        record = {
            "id": "schema_one", "custom": {"keep": True}, "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [], "views": [],
                "layout": {"version": 2, "layers": {"views": {"objects": {}, "viewport": {"x": 3, "y": 4, "zoom": 1}}}},
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            },
        }
        saved = self.store.save("schema_one", record, expected_layout_token=None, layout_protocol=None)
        self.service.view_layout_token = saved["layoutToken"]
        self.service.view_expected_absent = True
        self.service.view_expectation = {"absent": True}
        self.service.view_saved_id = None

        status, body, _ = self.request(
            "/api/postgres/profiles/local/view-plans/plan_view/apply", "POST",
            {"confirmDestructive": False}, authorized=True,
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["schemaSync"]["status"], "saved")
        current = self.store.get("schema_one")
        self.assertEqual([item["name"] for item in current["schema"]["views"]], ["summary"])
        self.assertEqual(current["schema"]["layout"], record["schema"]["layout"])
        self.assertEqual(current["custom"], record["custom"])

    def test_apply_delete_removes_exact_saved_view_and_preserves_layout(self):
        layout = {"version": 2, "layers": {"views": {"objects": {"view_summary": {"x": 10, "y": 20}}, "viewport": {"x": 3, "y": 4, "zoom": 1}}}}
        record = {
            "id": "schema_one", "custom": {"keep": True}, "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [],
                "views": [
                    {"id": "view_summary", "name": "summary", "namespace": "public", "materialized": True},
                    {"id": "view_other", "name": "other", "namespace": "public"},
                ],
                "layout": layout,
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            },
        }
        saved = self.store.save("schema_one", record, expected_layout_token=None, layout_protocol=None)
        self.service.view_layout_token = saved["layoutToken"]
        self.service.view_operation = "delete"
        self.service.view_expectation = {"kind": "materialized_view", "fingerprint": "a" * 64}

        status, body, _ = self.request(
            "/api/postgres/profiles/local/view-plans/plan_view/apply", "POST",
            {"confirmDestructive": True}, authorized=True,
        )

        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["operation"], "delete")
        self.assertEqual(result["schemaSync"]["status"], "saved")
        current = self.store.get("schema_one")
        self.assertEqual([item["id"] for item in current["schema"]["views"]], ["view_other"])
        self.assertEqual(current["schema"]["layout"], layout)
        self.assertEqual(current["custom"], record["custom"])

    def test_post_commit_schema_sync_distinguishes_conflict_from_storage_error(self):
        record = {
            "id": "schema_one", "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [],
                "views": [{"id": "view_summary", "name": "summary", "namespace": "public", "definition": "old"}],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            },
        }
        saved = self.store.save("schema_one", record, expected_layout_token=None, layout_protocol=None)
        self.service.view_layout_token = saved["layoutToken"]
        original = self.store.sync_view_after_mutation
        try:
            for error_status, expected_status in ((409, "conflict"), (500, "storage_error")):
                with self.subTest(expected_status=expected_status):
                    self.store.sync_view_after_mutation = lambda *args, status=error_status, **kwargs: (_ for _ in ()).throw(
                        SchemaStoreError(status, "schema_conflict" if status == 409 else "schema_store_error", "sync failed")
                    )
                    status, body, _ = self.request(
                        "/api/postgres/profiles/local/view-plans/plan_view/apply", "POST",
                        {"confirmDestructive": False}, authorized=True,
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)["schemaSync"]["status"], expected_status)
        finally:
            self.store.sync_view_after_mutation = original

    def test_schema_route_rejects_invalid_content_type(self):
        payload = {
            "id": "schema_one",
            "schema": {"projectName": "Demo", "tables": [], "relationships": [], "functions": []},
        }
        self.assertEqual(
            self.request("/api/schemas/schema_one", "PUT", payload, content_type="text/plain", authorized=True)[0],
            415,
        )

    def test_schema_crud_routes_require_local_session(self):
        path = "/api/schemas/schema_one"
        record = {
            "id": "schema_one",
            "schema": {"projectName": "Demo", "tables": [], "relationships": [], "functions": []},
        }
        for request in (
            ("/api/schemas", "GET", None),
            (path, "PUT", record),
            (path, "DELETE", None),
        ):
            with self.subTest(method=request[1]):
                status, body, _ = self.request(*request)
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body), {"error": {
                    "code": "invalid_session",
                    "message": "Schema API session token is missing or invalid",
                }})

        status, saved_body, _ = self.request(
            path, "PUT", record, authorized=True,
            headers={"X-Schemii-Layout-Protocol": "2"},
        )
        self.assertEqual(status, 200)
        saved = json.loads(saved_body)
        self.assertEqual(saved["saved"], "schema_one")
        self.assertIn("layoutToken", saved)

        status, list_body, _ = self.request("/api/schemas", authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in json.loads(list_body)["schemas"]], ["schema_one"])

        status, delete_body, _ = self.request(path, "DELETE", authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(delete_body), {"deleted": "schema_one"})

    def test_ai_routes_require_session_and_forward_auth_and_sessions(self):
        self.store.save(
            "schema_one", {"id": "schema_one", "schema": {"projectName": "Schema chat", "tables": [], "relationships": [], "functions": []}},
            expected_layout_token=None, layout_protocol=None,
        )
        requests = [
            ("/api/ai/auth/api", "POST", {"providerId": "anthropic", "key": "secret"}),
            ("/api/ai/auth/oauth/authorize", "POST", {"providerId": "anthropic", "method": 1, "inputs": {"region": "us"}}),
            ("/api/ai/auth/oauth/callback", "POST", {"providerId": "anthropic", "method": 1, "code": "code"}),
            ("/api/ai/sessions", "POST", {"schemaId": "schema_one", "accessLevel": "metadata", "model": {"providerID": "anthropic", "modelID": "claude"}}),
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

    def test_ai_history_routes_require_session_and_return_normalized_history(self):
        self.store.save(
            "schema_one", {"id": "schema_one", "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            }},
            expected_layout_token=None, layout_protocol=None,
        )
        self.ai_service.session_title = "SCHEMII_CONTEXT:schema_one:schema Schema chat"
        query = "?schemaId=schema_one&accessLevel=schema"
        for path in (f"/api/ai/sessions{query}", f"/api/ai/sessions/ses_1/messages{query}"):
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
        self.chat_store.create("ses_1", "schema_one", {}, ["schema"], {
            permission: "every_action" for permission in ("schema", "structured", "write", "rawread", "rawwrite")
        })

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
            "accessLevel": "schema",
        }
        self.ai_service.session_title = "SCHEMII_CONTEXT:schema_one:schema Demo chat"

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
        self.assertIn("configured approval policy", call[4].lower())
        self.assertIn("executes every action in schemii's backend", call[4].lower())
        self.assertIn("enable its matching permission checkbox", call[4].lower())
        self.assertIn("do not claim the capability is unsupported", call[4].lower())
        self.assertNotIn("schema_migration_apply", call[4].lower())
        self.assertFalse(call[5])
        self.assertFalse(call[6])
        self.assertFalse(call[7])
        self.assertFalse(call[8])
        self.assertTrue(call[9])

        payload["accessLevel"] = "data"
        payload.update({"profileId": "local", "database": "demo", "namespace": "public"})
        self.assertEqual(self.request("/api/ai/sessions/ses_1/messages", "POST", payload, authorized=True)[0], 409)

    def test_ai_proposal_execution_is_idempotent_and_one_use(self):
        self.store.save(
            "schema_one",
            {"id": "schema_one", "schema": {"projectName": "Demo", "tables": [], "relationships": [], "functions": []}},
            expected_layout_token=None, layout_protocol=None,
        )
        self.ai_service.session_title = "SCHEMII_CONTEXT:schema_one:schema Demo chat"
        self.ai_service.prompt_response = {
            "text": "Review.", "parts": [{"type": "text", "text": "Review."}],
            "actions": [{"type": "connection_setup", "name": "Demo", "host": "127.0.0.1", "port": 5432, "database": "demo", "user": "reader", "sslmode": "prefer", "requiresPasswordEntry": True, "requiresConfirmation": True}],
        }
        message = {
            "text": "Add events", "model": {"providerID": "anthropic", "modelID": "claude"},
            "schemaId": "schema_one", "accessLevel": "schema",
        }
        status, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)
        self.assertEqual(status, 200)
        response = json.loads(body)
        self.assertNotIn("actions", response)
        proposal = response["proposals"][0]
        path = f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}"
        execute_body = {"schemaId": "schema_one", "accessLevel": "schema", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"}}
        status, body, _ = self.request(path + "/execute", "POST", execute_body, authorized=True)
        self.assertEqual(status, 200)
        operation = json.loads(body)["operation"]
        self.assertEqual(operation["state"], "succeeded")
        self.assertEqual(operation["result"]["command"]["type"], "prefill_postgres_profile")
        repeated = json.loads(self.request(path + "/execute", "POST", execute_body, authorized=True)[1])["operation"]
        self.assertEqual(repeated["id"], operation["id"])
        reconciled = json.loads(self.request(path + "/reconcile", "POST", execute_body, authorized=True)[1])["operation"]
        self.assertEqual(reconciled["id"], operation["id"])

    def test_sibling_schema_proposals_are_one_atomic_batch(self):
        saved = self.store.save(
            "schema_one", {"id": "schema_one", "schema": {"projectName": "Demo", "tables": [{
                "id": "table_one", "name": "events", "columns": [{"id": "column_id", "name": "id", "type": "uuid", "primary": True, "nullable": False, "unique": True, "default": ""}],
                "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [], "x": 10, "y": 20, "color": "#f4b942",
            }], "relationships": [], "functions": []}}, expected_layout_token=None, layout_protocol=None,
        )
        self.ai_service.session_title = "SCHEMII_CONTEXT:schema_one:schema Demo chat"
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [
            {"type": "add_column", "tableId": "table_one", "name": "name", "columnType": "text", "nullable": False, "requiresConfirmation": True},
            {"type": "add_column", "tableId": "table_one", "name": "description", "columnType": "text", "nullable": True, "requiresConfirmation": True},
        ]}
        message = {"text": "Add columns", "model": {}, "schemaId": "schema_one", "accessLevel": "schema"}
        response = json.loads(self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)[1])
        self.assertEqual(len(response["proposals"]), 1)
        proposal = response["proposals"][0]
        self.assertEqual(proposal["action"]["type"], "schema_batch")
        self.assertEqual([item["name"] for item in proposal["action"]["actions"]], ["name", "description"])
        execute = {"schemaId": "schema_one", "accessLevel": "schema", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"}}
        operation = json.loads(self.request(f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute", "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(operation["state"], "succeeded")
        after = self.store.get("schema_one")
        self.assertEqual(after["revision"], saved["revision"] + 1)
        self.assertEqual([column["name"] for column in after["schema"]["tables"][0]["columns"]], ["id", "name", "description"])
        self.assertEqual((after["schema"]["tables"][0]["x"], after["schema"]["tables"][0]["y"], after["schema"]["tables"][0]["color"]), (10, 20, "#f4b942"))

    def test_ai_proposal_claim_rejects_changed_schema_revision(self):
        original = self.store.save(
            "schema_one",
            {"id": "schema_one", "schema": {"projectName": "Demo", "tables": [], "relationships": [], "functions": []}},
            expected_layout_token=None, layout_protocol=None,
        )
        self.ai_service.session_title = "SCHEMII_CONTEXT:schema_one:schema Demo chat"
        self.ai_service.prompt_response = {
            "text": "Review.", "parts": [], "actions": [{"type": "open_project", "schemaId": "schema_one", "projectName": "Demo", "requiresConfirmation": True}],
        }
        message = {"text": "Add events", "model": {}, "schemaId": "schema_one", "accessLevel": "schema"}
        _, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)
        proposal_id = json.loads(body)["proposals"][0]["proposalId"]
        changed = self.store.get("schema_one")
        changed["schema"]["projectName"] = "Changed"
        self.store.save("schema_one", changed, expected_layout_token=None, layout_protocol=None)
        path = f"/api/ai/sessions/ses_1/proposals/{proposal_id}/claim"
        status, body, _ = self.request(path, "POST", {"schemaId": "schema_one", "accessLevel": "schema"}, authorized=True)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "proposal_binding_mismatch")

    def test_schema_preflight_does_not_persist_and_post_save_preview_uses_new_binding(self):
        saved = self.store.save(
            "schema_one", {"id": "schema_one", "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            }}, expected_layout_token=None, layout_protocol=None,
        )
        self.service.profiles = [{"id": "local", "name": "Local", "host": "127.0.0.1", "port": 5432, "dbname": "demo", "user": "reader"}]
        from schemii.ai_http import ai_context_fingerprint
        fingerprint = self.service.profile_context_fingerprint("local")
        self.ai_service.session_title = f"SCHEMII_CONTEXT:schema_one:schema-write:{ai_context_fingerprint(['local', 'demo', 'public', fingerprint])} Demo"
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "add_table", "name": "events", "purpose": "Events", "columns": [{"name": "id", "type": "uuid"}], "requiresConfirmation": True,
        }]}
        message = {"text": "Add events", "model": {}, "schemaId": "schema_one", "accessLevel": "schema-write", "profileId": "local", "database": "demo", "namespace": "public"}
        response = json.loads(self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)[1])
        self.assertEqual(self.store.get("schema_one")["revision"], saved["revision"])
        self.assertTrue(response["proposals"][0]["preflight"]["migration"]["previewOnly"])
        proposal = response["proposals"][0]
        execute = {"policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"}}
        operation = json.loads(self.request(f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute", "POST", execute, authorized=True)[1])["operation"]
        result = operation["result"]
        self.assertEqual(result["revision"], saved["revision"] + 1)
        self.assertEqual(result["migrationPreview"]["schemaBinding"]["revision"], result["revision"])
        self.assertEqual(result["migrationPreview"]["schemaBinding"]["layoutToken"], result["layoutToken"])
        self.assertEqual(result["migrationPreview"]["applyProposal"]["action"]["type"], "migration_apply")

    def test_ai_connection_open_and_migration_preview_use_exact_profile(self):
        self.store.save(
            "schema_one", {"id": "schema_one", "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            }},
            expected_layout_token=None, layout_protocol=None,
        )
        self.service.profiles = [{
            "id": "local", "name": "Local", "host": "127.0.0.1", "port": 5432, "dbname": "demo", "user": "reader", "sslmode": "prefer",
        }]
        self.ai_service.session_title = "SCHEMII_CONTEXT:schema_one:schema Demo chat"
        message = {"text": "Open local", "model": {}, "schemaId": "schema_one", "accessLevel": "schema"}
        execute = {"schemaId": "schema_one", "accessLevel": "schema", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"}}

        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "open_connection", "profileId": "local", "name": "Local", "database": "demo", "namespace": "public", "requiresConfirmation": True,
        }]}
        _, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)
        proposal = json.loads(body)["proposals"][0]
        self.assertIn("profileFingerprint", proposal["action"])
        path = f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute"
        operation = json.loads(self.request(path, "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(operation["result"]["command"]["type"], "select_postgres_profile")
        self.assertIn(("list_namespaces", "local"), self.service.calls)

        from schemii.ai_http import ai_context_fingerprint
        fingerprint = self.service.profile_context_fingerprint("local")
        self.chat_store.delete("ses_1")
        self.ai_service.session_title = f"SCHEMII_CONTEXT:schema_one:schema-read-write:{ai_context_fingerprint(['local', 'demo', 'public', fingerprint])} Demo chat"
        message = {
            "text": "Preview migration", "model": {}, "schemaId": "schema_one", "accessLevel": "schema-read-write",
            "profileId": "local", "database": "demo", "namespace": "public",
        }
        execute = {
            "schemaId": "schema_one", "accessLevel": "schema-read-write", "profileId": "local", "database": "demo",
            "namespace": "public", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"},
        }
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "migration_preview", "profileId": "local", "namespace": "public", "destructivePolicy": "reject",
            "purpose": "Review", "readOnly": True, "requiresConfirmation": True,
        }]}
        _, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)
        proposal = json.loads(body)["proposals"][0]
        path = f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute"
        operation = json.loads(self.request(path, "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(operation["result"]["kind"], "migration_plan")
        self.assertEqual(operation["result"]["schemaBinding"]["schemaId"], "schema_one")
        apply_proposal = operation["result"]["applyProposal"]
        apply_path = f"/api/ai/sessions/ses_1/proposals/{apply_proposal['proposalId']}/execute"
        applied = json.loads(self.request(apply_path, "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(applied["result"]["kind"], "migration_applied")
        self.assertTrue(any(call[0] == "apply_ai_migration" and call[-1] is True for call in self.service.calls))
        self.assertTrue(any(call[0] == "preview_ai_migration" and call[2:5] == ("local", "demo", "public") for call in self.service.calls))

    def test_ai_insert_uses_confirmed_preview_then_server_issued_apply(self):
        saved = self.store.save(
            "schema_one", {"id": "schema_one", "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [], "views": [],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            }}, expected_layout_token=None, layout_protocol=None,
        )
        self.service.profiles = [{
            "id": "local", "name": "Local", "host": "127.0.0.1", "port": 5432,
            "dbname": "demo", "user": "reader", "sslmode": "prefer",
        }]
        from schemii.ai_http import ai_context_fingerprint
        fingerprint = self.service.profile_context_fingerprint("local")
        self.ai_service.session_title = f"SCHEMII_CONTEXT:schema_one:data:{ai_context_fingerprint(['local', 'demo', 'public', fingerprint])} Demo chat"
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "insert_rows_preview", "profileId": "local", "namespace": "public", "relation": "events",
            "rows": [{"name": "launch"}, {"name": "review"}], "purpose": "Seed events",
            "readOnly": True, "requiresConfirmation": True,
        }]}
        message = {
            "text": "Insert events", "model": {}, "schemaId": "schema_one", "accessLevel": "data",
            "profileId": "local", "database": "demo", "namespace": "public",
        }
        status, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)
        self.assertEqual(status, 200)
        preview_proposal = json.loads(body)["proposals"][0]
        execute = {
            "schemaId": "schema_one", "accessLevel": "data", "profileId": "local", "database": "demo",
            "namespace": "public", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"},
        }
        preview_path = f"/api/ai/sessions/ses_1/proposals/{preview_proposal['proposalId']}/execute"
        preview_operation = json.loads(self.request(preview_path, "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(preview_operation["result"]["kind"], "postgres_write_plan")
        preview_call = next(call for call in self.service.calls if call[0] == "preview_ai_insert_rows")
        self.assertEqual(set(preview_call[-1]), {"schemaId", "revision", "layoutToken"})
        apply_proposal = preview_operation["result"]["applyProposal"]
        self.assertEqual(apply_proposal["action"]["type"], "postgres_write_apply")
        self.assertNotIn("rows", apply_proposal["action"])
        self.assertEqual(apply_proposal["action"]["reviewedPlan"]["rows"], [{"name": "launch"}, {"name": "review"}])
        apply_path = f"/api/ai/sessions/ses_1/proposals/{apply_proposal['proposalId']}/execute"
        applied = json.loads(self.request(apply_path, "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(applied["result"]["kind"], "rows_inserted")
        self.assertEqual(self.store.get("schema_one")["revision"], saved["revision"])
        self.assertTrue(any(call[0] == "preview_ai_insert_rows" for call in self.service.calls))
        self.assertTrue(any(call[0] == "apply_ai_postgres_write" for call in self.service.calls))

    def test_ai_raw_write_executes_only_through_server_console_transaction(self):
        self.store.save(
            "schema_one", {"id": "schema_one", "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [],
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            }}, expected_layout_token=None, layout_protocol=None,
        )
        self.service.profiles = [{
            "id": "local", "name": "Local", "host": "127.0.0.1", "port": 5432,
            "dbname": "demo", "user": "writer", "sslmode": "prefer",
        }]
        from schemii.ai_http import ai_context_fingerprint
        fingerprint = self.service.profile_context_fingerprint("local")
        access = "schema-structured-write-rawread-rawwrite"
        self.ai_service.session_title = f"SCHEMII_CONTEXT:schema_one:{access}:{ai_context_fingerprint(['local', 'demo', 'public', fingerprint])} Demo chat"
        sql = "UPDATE events SET active = true; DELETE FROM events WHERE expired"
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "raw_write", "profileId": "local", "namespace": "public", "sql": sql,
            "purpose": "Refresh events", "requiresConfirmation": True,
        }]}
        message = {"text": "Refresh events", "model": {}, "schemaId": "schema_one", "accessLevel": access, "profileId": "local", "database": "demo", "namespace": "public"}
        execute = {"schemaId": "schema_one", "accessLevel": access, "profileId": "local", "database": "demo", "namespace": "public", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"}}
        proposal = json.loads(self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)[1])["proposals"][0]
        self.assertEqual(proposal["action"]["sql"], sql)
        operation = json.loads(self.request(f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute", "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(operation["state"], "succeeded")
        grant_call = next(call for call in self.service.calls if call[0] == "create_console_write_grant")
        execution_call = next(call for call in self.service.calls if call[0] == "execute_console")
        revoke_call = next(call for call in self.service.calls if call[0] == "revoke_console_write_grant")
        self.assertTrue(grant_call[2]["confirmed"])
        self.assertEqual(execution_call[2]["sql"], sql)
        self.assertEqual(execution_call[2]["mode"], "write")
        self.assertEqual(revoke_call[2], execution_call[2]["writeGrantId"])

    def test_ai_pre_mutation_service_failure_is_failed_not_uncertain(self):
        self.test_ai_insert_uses_confirmed_preview_then_server_issued_apply()
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "insert_rows_preview", "profileId": "local", "namespace": "public", "relation": "events",
            "rows": [{"name": "later"}], "purpose": "Seed events", "readOnly": True, "requiresConfirmation": True,
        }]}
        fingerprint = self.service.profile_context_fingerprint("local")
        from schemii.ai_http import ai_context_fingerprint
        self.ai_service.session_title = f"SCHEMII_CONTEXT:schema_one:data:{ai_context_fingerprint(['local', 'demo', 'public', fingerprint])} Demo chat"
        message = {"text": "Insert later", "model": {}, "schemaId": "schema_one", "accessLevel": "data", "profileId": "local", "database": "demo", "namespace": "public"}
        execute = {"schemaId": "schema_one", "accessLevel": "data", "profileId": "local", "database": "demo", "namespace": "public", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"}}
        proposal = json.loads(self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)[1])["proposals"][0]
        preview = json.loads(self.request(f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute", "POST", execute, authorized=True)[1])["operation"]
        apply_proposal = preview["result"]["applyProposal"]

        def fail_before_mutation(*args):
            raise PostgresServiceError(500, "plan_store_error", "Plan could not be validated")

        self.service.apply_ai_postgres_write = fail_before_mutation
        status, body, _ = self.request(f"/api/ai/sessions/ses_1/proposals/{apply_proposal['proposalId']}/execute", "POST", execute, authorized=True)
        operation = json.loads(body)["operation"]
        self.assertEqual(status, 500)
        self.assertEqual(operation["state"], "failed")
        self.assertEqual(operation["error"]["code"], "plan_store_error")

    def test_ai_view_creation_uses_server_apply_and_preserves_layout(self):
        layout = {"layers": {"tables": {"viewport": {"x": 12, "y": 34, "zoom": 0.8}, "objects": {}}, "views": {"viewport": {"x": 5, "y": 6, "zoom": 1}, "objects": {}}}}
        saved = self.store.save(
            "schema_one", {"id": "schema_one", "schema": {
                "projectName": "Demo", "tables": [], "relationships": [], "functions": [], "views": [], "layout": layout,
                "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
            }}, expected_layout_token=None, layout_protocol=None,
        )
        self.service.view_layout_token = saved["layoutToken"]
        self.service.view_expected_absent = True
        self.service.view_saved_id = None
        self.service.profiles = [{
            "id": "local", "name": "Local", "host": "127.0.0.1", "port": 5432,
            "dbname": "demo", "user": "reader", "sslmode": "prefer",
        }]
        from schemii.ai_http import ai_context_fingerprint
        fingerprint = self.service.profile_context_fingerprint("local")
        self.ai_service.session_title = f"SCHEMII_CONTEXT:schema_one:data:{ai_context_fingerprint(['local', 'demo', 'public', fingerprint])} Demo chat"
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "create_view_preview", "profileId": "local", "namespace": "public", "relation": "summary",
            "definition": 'CREATE VIEW "public"."summary" AS SELECT 1', "purpose": "Create summary",
            "readOnly": True, "requiresConfirmation": True,
        }]}
        message = {
            "text": "Create summary", "model": {}, "schemaId": "schema_one", "accessLevel": "data",
            "profileId": "local", "database": "demo", "namespace": "public",
        }
        _, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, authorized=True)
        proposal = json.loads(body)["proposals"][0]
        execute = {
            "schemaId": "schema_one", "accessLevel": "data", "profileId": "local", "database": "demo",
            "namespace": "public", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"},
        }
        preview_path = f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute"
        preview = json.loads(self.request(preview_path, "POST", execute, authorized=True)[1])["operation"]["result"]
        apply_proposal = preview["applyProposal"]
        self.assertNotIn("definition", apply_proposal["action"])
        apply_path = f"/api/ai/sessions/ses_1/proposals/{apply_proposal['proposalId']}/execute"
        operation = json.loads(self.request(apply_path, "POST", execute, authorized=True)[1])["operation"]
        self.assertEqual(operation["result"]["kind"], "view_created")
        after = self.store.get("schema_one")
        self.assertEqual(after["schema"]["layout"], layout)
        self.assertEqual(after["schema"]["views"][0]["name"], "summary")
        self.assertEqual(after["revision"], saved["revision"] + 1)

    def test_ai_message_metadata_omits_schema_and_rejects_unknown_schema(self):
        record = {"id": "schema_one", "schema": {"projectName": "Demo", "tables": [{"id": "t", "name": "secret_table", "columns": []}], "relationships": [], "functions": []}}
        self.store.save("schema_one", record, expected_layout_token=None, layout_protocol=None)
        payload = {
            "text": "Describe it", "model": {"providerID": "anthropic", "modelID": "claude"},
            "schemaId": "schema_one", "accessLevel": "metadata",
        }
        self.ai_service.session_title = "SCHEMII_CONTEXT:schema_one:metadata Demo chat"
        self.assertEqual(self.request("/api/ai/sessions/ses_1/messages", "POST", payload, authorized=True)[0], 200)
        self.assertNotIn("secret_table", self.ai_service.calls[-1][2])
        payload["schemaId"] = "missing"
        self.assertEqual(self.request("/api/ai/sessions/ses_1/messages", "POST", payload, authorized=True)[0], 409)


class RealAiWriteHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.ai_service = FakeAIService()
        self.connections = []
        self.http = None
        self._start()
        self.service.save_profile("local", {
            "name": "Local", "host": "127.0.0.1", "port": 5432, "dbname": "demo",
            "user": "reader", "password": "secret", "sslmode": "prefer", "timeout": 5,
        })
        self.saved = self.store.save("schema_one", {"id": "schema_one", "schema": {
            "projectName": "Demo", "tables": [], "relationships": [], "functions": [], "views": [],
            "postgres": {"sourceProfileId": "local", "database": "demo", "namespace": "public"},
        }}, expected_layout_token=None, layout_protocol=None)
        from schemii.ai_http import ai_context_fingerprint
        fingerprint = self.service.profile_context_fingerprint("local")
        self.ai_service.session_title = f"SCHEMII_CONTEXT:schema_one:data:{ai_context_fingerprint(['local', 'demo', 'public', fingerprint])} Demo chat"

    def tearDown(self):
        if self.http is not None:
            self.http.close()
        self.service.close()
        self.temporary_directory.cleanup()

    def _connect(self, **kwargs):
        connection = WriteConnection()
        self.connections.append(connection)
        return connection

    def _start(self):
        self.service = PostgresService(self.root / "config", connect_factory=self._connect)
        self.service._inspect_ai_insert_target = lambda *args: TARGET
        self.store = SchemaStore(self.root / "schemas")
        self.authority = AiAuthority(self.root / "authority", "schemii")
        handler = make_handler(
            ROOT / "src" / "schemii" / "web", self.service, self.store, "session-token",
            server_id="real-ai-write", ai_authority=self.authority,
            ai_chat_store=AiChatStore(self.root / "chats"), ai_service=self.ai_service,
            example_installer=FakeExampleInstaller(),
        )
        self.http = RunningHttpServer(handler)

    def _restart(self):
        self.http.close()
        self.service.close()
        self._start()

    def request(self, path, method="GET", payload=None):
        return self.http.request(path, method, payload, authorized=True)

    def test_real_durable_insert_plan_applies_after_http_restart(self):
        self.ai_service.prompt_response = {"text": "Review.", "parts": [], "actions": [{
            "type": "insert_rows_preview", "profileId": "local", "namespace": "public", "relation": "events",
            "rows": [{"name": "launch"}, {"name": "review"}], "purpose": "Seed events",
            "readOnly": True, "requiresConfirmation": True,
        }]}
        message = {"text": "Insert events", "model": {}, "schemaId": "schema_one", "accessLevel": "data", "profileId": "local", "database": "demo", "namespace": "public"}
        execute = {"schemaId": "schema_one", "accessLevel": "data", "profileId": "local", "database": "demo", "namespace": "public", "policyRevision": 1, "confirmation": {"accepted": True, "mode": "every_action"}}
        proposal = json.loads(self.request("/api/ai/sessions/ses_1/messages", "POST", message)[1])["proposals"][0]
        preview = json.loads(self.request(f"/api/ai/sessions/ses_1/proposals/{proposal['proposalId']}/execute", "POST", execute)[1])["operation"]
        apply_proposal = preview["result"]["applyProposal"]
        plan_id = apply_proposal["action"]["planId"]
        plan_path = self.root / "config" / "ai_migration_plans" / f"{plan_id}.json"
        stored = json.loads(plan_path.read_text())
        self.assertEqual(stored["schemaBinding"], {"schemaId": "schema_one", "revision": self.saved["revision"], "layoutToken": self.saved["layoutToken"]})
        self.assertNotIn("target", stored["schemaBinding"])

        self._restart()
        status, body, _ = self.request(f"/api/ai/sessions/ses_1/proposals/{apply_proposal['proposalId']}/execute", "POST", execute)
        operation = json.loads(body)["operation"]
        self.assertEqual(status, 200)
        self.assertEqual(operation["state"], "succeeded")
        self.assertEqual(operation["result"]["insertedRowCount"], 2)
        inserts = [(sql, params) for sql, params in self.connections[-1].executed if sql.startswith("INSERT INTO")]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(json.loads(inserts[0][1][0]), [{"name": "launch"}, {"name": "review"}])
        self.assertEqual(self.service._read_ai_plan(plan_id)["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
