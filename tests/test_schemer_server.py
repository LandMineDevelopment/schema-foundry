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

from schemii.postgres_http import PostgresHttpMixin
from schemii.schemer_server import ThreadingHTTPServer, make_handler


class FakePostgresService:
    def __init__(self):
        self.calls = []
        self.profiles = [{
            "id": "shared", "name": "Shared", "host": "postgres", "port": 5432,
            "dbname": "schemii", "user": "schemii", "sslmode": "disable", "timeout": 10,
        }]

    def list_profiles(self):
        return self.profiles

    def save_profile(self, profile_id, body):
        saved = {"id": profile_id or "created", **{key: value for key, value in body.items() if key != "password"}}
        self.profiles = [saved]
        self.calls.append(("save_profile", profile_id))
        return saved

    def delete_profile(self, profile_id):
        self.calls.append(("delete_profile", profile_id))
        return {"deleted": profile_id}

    def list_namespaces(self, profile_id):
        self.calls.append(("list_namespaces", profile_id))
        return ["bookstore", "public"]

    def catalog_status(self, profile_id, namespace):
        self.calls.append(("catalog_status", profile_id, namespace))
        return {"profileId": profile_id, "namespace": namespace, "fingerprint": "live"}

    def test_profile(self, profile_id):
        self.calls.append(("test_profile", profile_id))
        return {"ok": True, "database": "schemii"}

    def introspect(self, profile_id, namespace):
        self.calls.append(("introspect", profile_id, namespace))
        return {"tables": []}

    def preview_table_data(self, profile_id, namespace, table, offset, limit):
        self.calls.append(("preview_table_data", profile_id, namespace, table, offset, limit))
        return {"columns": [], "rows": [], "nextOffset": offset, "hasMore": False}

    def execute_read_only_sql(self, profile_id, namespace, statement):
        self.calls.append(("execute_read_only_sql", profile_id, namespace, statement))
        return {"columns": [{"name": "answer"}], "rows": [[1]], "rowCount": 1, "truncated": False}


class QuietHandlerMixin:
    def log_message(self, format, *args):
        pass


class SchemerServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = FakePostgresService()
        handler = make_handler(
            ROOT / "src" / "schemii" / "schemer_web",
            self.service,
            "session-token",
            server_id="schemer-server",
        )
        self.assertTrue(issubclass(handler, PostgresHttpMixin))
        quiet_handler = type("QuietSchemerHandler", (QuietHandlerMixin, handler), {})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), quiet_handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary_directory.cleanup()

    def request(self, path, method="GET", payload=None, authorized=False):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(f"{self.base_url}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if authorized:
            request.add_header("X-Schemii-Token", "session-token")
        try:
            with urlopen(request) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            try:
                return error.code, error.read(), error.headers
            finally:
                error.close()

    def test_static_shared_assets_and_session(self):
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Schemer", body)
        self.assertEqual(self.request("/shared/theme.css")[0], 200)
        self.assertEqual(self.request("/shared/postgres-client.js")[0], 200)
        self.assertEqual(self.request("/shared/../server.py")[0], 404)
        status, body, _ = self.request("/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"token": "session-token", "serverId": "schemer-server"})

    def test_shared_connection_routes_match_schemii_contract(self):
        self.assertEqual(self.request("/api/postgres/profiles")[0], 403)
        status, body, _ = self.request("/api/postgres/profiles", authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["profiles"][0]["id"], "shared")
        self.assertEqual(self.request("/api/postgres/profiles/shared/namespaces", authorized=True)[0], 200)
        self.assertEqual(self.request("/api/postgres/profiles/shared/test", "POST", {}, True)[0], 200)
        self.assertIn(("list_namespaces", "shared"), self.service.calls)
        self.assertIn(("test_profile", "shared"), self.service.calls)

    def test_profile_writes_use_shared_router_and_redact_password(self):
        profile = {
            "name": "Analytics", "host": "postgres", "port": 5432, "dbname": "schemii",
            "user": "reader", "password": "secret", "sslmode": "disable", "timeout": 10,
        }
        status, body, _ = self.request("/api/postgres/profiles", "POST", profile, True)
        self.assertEqual(status, 201)
        self.assertNotIn("password", json.loads(body))
        self.assertEqual(self.service.calls[-1], ("save_profile", None))


if __name__ == "__main__":
    unittest.main()
