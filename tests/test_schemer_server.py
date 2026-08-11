import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_http import PostgresHttpMixin
from schemii.dashboard_store import DashboardStore
from schemii.schemer_server import make_handler
from tests.http_test_support import FakePostgresService, RunningHttpServer


class SchemerServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.service = FakePostgresService(
            profiles=[{
                "id": "shared", "name": "Shared", "host": "postgres", "port": 5432,
                "dbname": "schemii", "user": "schemii", "sslmode": "disable", "timeout": 10,
            }],
            namespaces=["bookstore", "public"],
            relations=[{"name": "orders", "kind": "table"}],
            descriptor={
                "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
                "kind": "table", "columns": [{"name": "id", "type": "bigint", "nullable": False, "ordinal": 1, "suggestions": ["dimension", "identifier"]}],
                "fingerprint": "catalog-fingerprint",
                "definition": {"status": "unavailable", "reason": "not_supported"},
            },
            preview_rows=[{"id": 1}],
            test_result={"ok": True, "database": "schemii"},
        )
        self.dashboard_store = DashboardStore(Path(self.temporary_directory.name) / "dashboards")
        self.dashboard_store.initialize_once()
        handler = make_handler(
            ROOT / "src" / "schemii" / "schemer_web",
            self.service,
            self.dashboard_store,
            "session-token",
            server_id="schemer-server",
        )
        self.assertTrue(issubclass(handler, PostgresHttpMixin))
        self.http = RunningHttpServer(handler)

    def tearDown(self):
        self.http.close()
        self.temporary_directory.cleanup()

    def request(self, path, method="GET", payload=None, authorized=False):
        return self.http.request(path, method, payload, authorized=authorized)

    def test_static_shared_assets_and_session(self):
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Schemer", body)
        self.assertEqual(self.request("/shared/theme.css")[0], 200)
        self.assertEqual(self.request("/shared/postgres-client.js")[0], 200)
        self.assertEqual(self.request("/shared/ui-components.js")[0], 200)
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
        relation_path = "/api/postgres/profiles/shared/relations?database=schemii&namespace=bookstore"
        status, body, _ = self.request(relation_path, authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["relations"][0], {"name": "orders", "kind": "table"})
        inspect_path = "/api/postgres/profiles/shared/relation?database=schemii&namespace=bookstore&relation=orders"
        status, body, _ = self.request(inspect_path, authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["columns"][0]["type"], "bigint")
        self.assertEqual(json.loads(body)["columns"][0]["suggestions"], ["dimension", "identifier"])
        self.assertEqual(json.loads(body)["definition"], {"status": "unavailable", "reason": "not_supported"})
        self.assertNotIn("password", body.decode())
        self.assertEqual(self.request("/api/postgres/profiles/shared/test", "POST", {}, True)[0], 200)
        self.assertIn(("list_namespaces", "shared"), self.service.calls)
        self.assertIn(("list_relations", "shared", "schemii", "bookstore"), self.service.calls)
        self.assertIn(("inspect_relation", "shared", "schemii", "bookstore", "orders", None, None), self.service.calls)
        preview_source = {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
            "kind": "table", "fingerprint": "a" * 64,
        }
        preview_path = "/api/postgres/profiles/shared/relation/preview"
        status, body, _ = self.request(preview_path, "POST", {"source": preview_source, "limit": 20}, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["rows"], [{"id": 1}])
        self.assertIn(("preview_relation_rows", "shared", preview_source, 0, 20), self.service.calls)
        verify_path = "/api/postgres/profiles/shared/relation/verify"
        status, body, _ = self.request(verify_path, "POST", {"source": preview_source}, True)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["matches"])
        self.assertIn(("verify_relation_source", "shared", preview_source), self.service.calls)
        query = {"version": 1, "measures": []}
        query_path = "/api/postgres/profiles/shared/relation/query"
        self.assertEqual(self.request(query_path, "POST", {"source": preview_source, "query": query})[0], 403)
        status, body, _ = self.request(query_path, "POST", {"source": preview_source, "query": query}, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sql"], "SELECT count(*)")
        self.assertIn(("execute_widget_query", "shared", preview_source, query), self.service.calls)
        self.assertEqual(self.request(query_path, "POST", {"source": preview_source, "query": query, "sql": "SELECT 1"}, True)[0], 400)
        detail_request = {
            "source": preview_source, "query": query, "selection": {"dimensions": []},
            "detail": {"version": 1, "columns": [], "rowIdentifier": None},
            "offset": 0, "limit": 20, "sort": None, "search": "",
        }
        detail_path = "/api/postgres/profiles/shared/relation/detail"
        self.assertEqual(self.request(detail_path, "POST", detail_request)[0], 403)
        status, body, _ = self.request(detail_path, "POST", detail_request, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["matchingRowCount"], 0)
        self.assertIn((
            "execute_relation_detail", "shared", preview_source, query, detail_request["selection"],
            detail_request["detail"], 0, 20, None, "",
        ), self.service.calls)
        self.assertEqual(self.request(detail_path, "POST", {**detail_request, "extra": True}, True)[0], 400)
        self.assertIn(("test_profile", "shared"), self.service.calls)

    def test_schema_design_routes_are_not_exposed(self):
        routes = (
            ("/api/postgres/profiles/shared/fingerprint?namespace=bookstore", "GET"),
            ("/api/postgres/profiles/shared/data?namespace=bookstore&table=orders", "GET"),
            ("/api/postgres/profiles/shared/introspect", "POST"),
            ("/api/postgres/profiles/shared/sql", "POST"),
        )
        for path, method in routes:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, method, {}, True)[0], 404)

    def test_profile_writes_use_shared_router_and_redact_password(self):
        profile = {
            "name": "Analytics", "host": "postgres", "port": 5432, "dbname": "schemii",
            "user": "reader", "password": "secret", "sslmode": "disable", "timeout": 10,
        }
        status, body, _ = self.request("/api/postgres/profiles", "POST", profile, True)
        self.assertEqual(status, 201)
        self.assertNotIn("password", json.loads(body))
        self.assertEqual(self.service.calls[-1], ("save_profile", None, profile))

    def test_dashboard_routes_require_session_and_reject_stale_updates(self):
        self.assertEqual(self.request("/api/dashboards")[0], 403)
        status, body, _ = self.request("/api/dashboards", authorized=True)
        self.assertEqual(status, 200)
        record = json.loads(body)["dashboards"][0]
        record["dashboard"]["title"] = "Updated dashboard"
        record["dashboard"]["widgets"][0]["configuration"] = {"source": {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore",
            "relation": "orders", "kind": "table", "fingerprint": "a" * 64,
        }}
        status, body, _ = self.request(f"/api/dashboards/{record['id']}", "PUT", record, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], record["revision"] + 1)
        self.assertEqual(json.loads(body)["dashboard"]["widgets"][0]["configuration"]["source"]["relation"], "orders")
        self.assertEqual(self.request(f"/api/dashboards/{record['id']}", "PUT", record, True)[0], 409)

        status, body, _ = self.request("/api/dashboards", "POST", {"title": "New dashboard"}, True)
        self.assertEqual(status, 201)
        created = json.loads(body)
        self.assertEqual(created["dashboard"]["widgets"], [])
        self.assertEqual(self.request(f"/api/dashboards/{created['id']}", "DELETE", authorized=True)[0], 200)


if __name__ == "__main__":
    unittest.main()
