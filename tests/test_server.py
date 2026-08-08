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

from schema_foundry.schema_store import SchemaStore
from schema_foundry.server import CONTENT_SECURITY_POLICY, ThreadingHTTPServer, _is_local_request, make_handler


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


class QuietHandlerMixin:
    def log_message(self, format, *args):
        pass


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = FakePostgresService()
        self.store = SchemaStore(Path(self.temporary_directory.name) / "schemas")
        handler = make_handler(ROOT / "src" / "schema_foundry" / "web", self.service, self.store, "session-token")
        quiet_handler = type("QuietSchemaFoundryHandler", (QuietHandlerMixin, handler), {})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), quiet_handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary_directory.cleanup()

    def request(self, path, method="GET", payload=None, content_type="application/json", authorized=False):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(f"{self.base_url}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", content_type)
        if authorized:
            request.add_header("X-Schema-Foundry-Token", "session-token")
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
        self.assertIn(b"Schema Foundry", body)
        self.assertEqual(headers["Content-Security-Policy"], CONTENT_SECURITY_POLICY)
        self.assertEqual(self.request("/.git/config")[0], 404)
        self.assertEqual(self.request("/src/schema_foundry/server.py")[0], 404)

        status, body, _ = self.request("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"token": "session-token"})

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


if __name__ == "__main__":
    unittest.main()
