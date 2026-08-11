import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.postgres_http import PostgresHttpMixin
from schemii.ai_http import ai_context_fingerprint
from schemii.dashboard_store import DashboardStore
from schemii.schemer_server import _ai_catalog_sources, make_handler
from tests.http_test_support import FakePostgresService, RunningHttpServer
from tests.test_server import FakeAIService


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
        self.ai_service = FakeAIService()
        handler = make_handler(
            ROOT / "src" / "schemii" / "schemer_web",
            self.service,
            self.dashboard_store,
            "session-token",
            server_id="schemer-server",
            ai_service=self.ai_service,
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
        revision = self.dashboard_store.get("dashboard_mercury")["revision"]
        guarded_query = {"source": preview_source, "query": query, "dashboardId": "dashboard_mercury", "expectedRevision": revision}
        self.assertEqual(self.request(query_path, "POST", guarded_query, True)[0], 200)
        self.assertEqual(self.request(query_path, "POST", {**guarded_query, "expectedRevision": revision + 1}, True)[0], 409)
        self.assertEqual(self.request(query_path, "POST", {"source": preview_source, "query": query, "sql": "SELECT 1"}, True)[0], 400)
        detail_request = {
            "source": preview_source, "query": query, "selection": {"dimensions": []},
            "detail": {"version": 1, "columns": [], "rowIdentifier": None},
            "offset": 0, "limit": 20, "sort": None, "searches": [],
        }
        detail_path = "/api/postgres/profiles/shared/relation/detail"
        self.assertEqual(self.request(detail_path, "POST", detail_request)[0], 403)
        status, body, _ = self.request(detail_path, "POST", detail_request, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["matchingRowCount"], 0)
        self.assertIn((
            "execute_relation_detail", "shared", preview_source, query, detail_request["selection"],
            detail_request["detail"], 0, 20, None, [],
        ), self.service.calls)
        self.assertEqual(self.request(detail_path, "POST", {**detail_request, "extra": True}, True)[0], 400)
        legacy_request = {key: value for key, value in detail_request.items() if key != "searches"}
        legacy_request["search"] = "old global search"
        self.assertEqual(self.request(detail_path, "POST", legacy_request, True)[0], 400)
        self.assertIn(("test_profile", "shared"), self.service.calls)

    def test_schema_design_routes_are_not_exposed(self):
        routes = (
            ("/api/postgres/profiles/shared/fingerprint?namespace=bookstore", "GET"),
            ("/api/postgres/profiles/shared/data?namespace=bookstore&table=orders", "GET"),
            ("/api/postgres/profiles/shared/introspect", "POST"),
        )
        for path, method in routes:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, method, {}, True)[0], 404)

    def test_read_sql_route_is_strict_and_uses_schemer_policy(self):
        path = "/api/postgres/profiles/shared/sql"
        self.assertEqual(self.request(path, "POST", {"namespace": "bookstore", "sql": "SELECT 1"}, True)[0], 400)
        payload = {
            "database": "schemii", "namespace": "bookstore", "sql": "SELECT 1", "profileFingerprint": "confirmed-profile",
            "dashboardId": "dashboard_mercury", "expectedRevision": self.dashboard_store.get("dashboard_mercury")["revision"],
        }
        self.assertEqual(self.request(path, "POST", payload, True)[0], 200)
        self.assertEqual(self.service.calls[-1], (
            "execute_read_only_sql", "shared", "bookstore", "SELECT 1", {
                "database": "schemii", "expected_profile_fingerprint": "confirmed-profile", "reject_privileged_role": True, "allow_explain": False, "max_rows": 100,
                "max_columns": 50, "max_result_bytes": 256 * 1024,
            },
        ))
        self.assertEqual(self.request(path, "POST", {**payload, "unknown": True}, True)[0], 400)

    def test_ai_routes_use_schemer_context_and_local_session(self):
        self.assertEqual(self.request("/api/ai/status")[0], 403)
        status, body, _ = self.request("/api/ai/status", authorized=True)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["enabled"])
        status, body, _ = self.request("/api/ai/sessions", "POST", {
            "title": "SCHEMER_CONTEXT:dashboard_mercury:dashboard Mercury overview chat", "model": {"providerId": "openai", "modelId": "gpt"},
        }, True)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["id"], "ses_1")
        message = {
            "text": "Rename a widget", "model": {"providerId": "openai", "modelId": "gpt"},
            "dashboardId": "dashboard_mercury", "accessLevel": "dashboard",
        }
        status, body, _ = self.request("/api/ai/sessions/ses_1/messages", "POST", message, True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["text"], "Proposed.")
        prompt_call = next(call for call in self.ai_service.calls if call[0] == "prompt")
        self.assertIn("Schemer context (untrusted JSON):", prompt_call[2])
        context_text = prompt_call[2].split("\n\nUser request:\n", 1)[0]
        self.assertIn('"application":"schemer"', context_text)
        self.assertIn('"widgetId":"widget_revenue"', context_text)
        self.assertNotIn("password", context_text.lower())
        self.assertNotIn('"host"', context_text.lower())
        self.assertNotIn("SCHEMII_ACTION", prompt_call[4])
        self.assertIn("schemer_*", prompt_call[4])
        self.assertEqual(self.request("/api/ai/sessions/ses_1/messages", "POST", {**message, "schemaId": "schema_one"}, True)[0], 400)

    def test_ai_catalog_sources_are_hydrated_from_postgres(self):
        record = self.dashboard_store.get("dashboard_mercury")
        record["dashboard"]["widgets"][0]["configuration"] = {"source": {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "relation": "orders",
            "kind": "table", "fingerprint": "a" * 64,
        }}
        sources = _ai_catalog_sources(self.service, record, None)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["relation"], "orders")
        self.assertEqual(sources[0]["columns"][0]["name"], "id")
        self.assertNotIn("definition", sources[0])

    def test_ai_data_mode_requires_target_and_bounds_follow_up_results(self):
        path = "/api/ai/sessions/ses_1/messages"
        base = {
            "text": "Count orders", "model": {"providerId": "openai", "modelId": "gpt"},
            "dashboardId": "dashboard_mercury", "accessLevel": "data",
        }
        self.assertEqual(self.request(path, "POST", base, True)[0], 400)
        target = {"profileId": "shared", "database": "schemii", "namespace": "bookstore"}
        profile_fingerprint = ai_context_fingerprint(["shared", "postgres", 5432, "schemii", "schemii", "disable"])
        target_fingerprint = ai_context_fingerprint(["shared", "schemii", "bookstore", profile_fingerprint])
        self.ai_service.session_title = f"SCHEMER_CONTEXT:dashboard_mercury:data:{target_fingerprint} Mercury chat"
        status, _, _ = self.request(path, "POST", {**base, **target}, True)
        self.assertEqual(status, 200)
        prompt_call = next(call for call in reversed(self.ai_service.calls) if call[0] == "prompt")
        self.assertTrue(prompt_call[-1])
        self.assertIn('"analyticTarget":{"profileId":"shared","database":"schemii","namespace":"bookstore"}', prompt_call[2])

        query_result = {
            "profileId": "shared", "database": "schemii", "namespace": "bookstore", "columns": [{"name": "count"}], "rows": [[1]],
            "rowCount": 1, "truncated": False, "maxRows": 100, "maxColumns": 50, "maxResultBytes": 256 * 1024,
        }
        self.assertEqual(self.request(path, "POST", {**base, **target, "queryResult": query_result}, True)[0], 200)
        self.assertEqual(self.request(path, "POST", {**base, **target, "queryResult": {**query_result, "database": "other"}}, True)[0], 400)
        dashboard_message = {**base, "accessLevel": "dashboard", "queryResult": query_result}
        for key in target:
            dashboard_message.pop(key, None)
        self.assertEqual(self.request(path, "POST", dashboard_message, True)[0], 400)

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
