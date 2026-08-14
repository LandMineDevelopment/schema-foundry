import json
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import DashboardStore
from schemii.ai_authority import AiAuthority
from schemii.schema_store import SchemaStore
from schemii.schemer_server import make_handler as make_schemer_handler
from schemii.server import make_handler as make_schemii_handler
from tests.http_test_support import FakePostgresService, RunningHttpServer
from tests.fake_metadata_authority import FakeSchemiiAuthority


class PostgresHttpContractTests(unittest.TestCase):
    def test_shared_profile_and_catalog_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factories = {
                "schemii": lambda service: make_schemii_handler(
                    ROOT / "src/schemii/web", service, SchemaStore(root / "schemas"),
                    "session-token", server_id="schemii-contract", ai_authority=FakeSchemiiAuthority(),
                ),
                "schemer": lambda service: make_schemer_handler(
                    ROOT / "src/schemii/schemer_web", service, DashboardStore(root / "dashboards"),
                    "session-token", server_id="schemer-contract", ai_authority=AiAuthority(root / "authority", "schemer"),
                ),
            }
            for name, factory in factories.items():
                with self.subTest(application=name):
                    service = FakePostgresService(
                        profiles=[{"id": "shared", "name": "Shared"}],
                        namespaces=["public"],
                        relations=[{"name": "orders", "kind": "table"}],
                        test_result={"ok": True, "database": "demo"},
                    )
                    running = RunningHttpServer(factory(service))
                    try:
                        self.assertEqual(running.request("/api/postgres/profiles")[0], 403)
                        status, body, _ = running.request("/api/postgres/profiles", authorized=True)
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["profiles"][0]["id"], "shared")
                        self.assertEqual(running.request("/api/postgres/profiles/shared/namespaces", authorized=True)[0], 200)
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/relations?database=demo&namespace=public", authorized=True,
                        )[0], 200)
                        status, body, _ = running.request(
                            "/api/postgres/profiles/shared/relation?database=demo&namespace=public&relation=orders", authorized=True,
                        )
                        self.assertEqual(status, 200)
                        self.assertEqual(json.loads(body)["definition"], {"status": "unavailable", "reason": "not_supported"})
                        self.assertNotIn("password", body.decode())
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/test", "POST", {}, authorized=True,
                        )[0], 200)
                        execution_id = str(uuid4())
                        console_id = str(uuid4())
                        request = {
                            "executionId": execution_id, "consoleId": console_id, "database": "demo",
                            "namespace": "public", "sql": "SELECT 1", "mode": "read", "writeGrantId": None,
                        }
                        status, body, _ = running.request(
                            "/api/postgres/profiles/shared/console/executions", "POST", request, authorized=True,
                        )
                        self.assertEqual(status, 200)
                        self.assertFalse(json.loads(body)["committed"])
                        call = service.calls[-1]
                        self.assertEqual(call[:3], ("execute_console", "shared", request))
                        self.assertNotEqual(call[3], "session-token")
                        self.assertEqual(call[4], f"{name}-contract")
                        self.assertEqual(call[5].allow_write, name == "schemii")
                        self.assertEqual(running.request(
                            f"/api/postgres/profiles/shared/console/executions/{execution_id}",
                            "DELETE", authorized=True,
                        )[0], 200)
                        invalid = dict(request)
                        invalid["extra"] = True
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/console/executions", "POST", invalid, authorized=True,
                        )[0], 400)
                        self.assertEqual(running.request(
                            "/api/postgres/profiles/shared/console/executions", "POST", [], authorized=True,
                        )[0], 400)
                        grant_request = {
                            "consoleId": console_id, "database": "demo", "namespace": "public", "confirmed": True,
                        }
                        status, body, _ = running.request(
                            "/api/postgres/profiles/shared/console/write-grants", "POST", grant_request, authorized=True,
                        )
                        if name == "schemii":
                            self.assertEqual(status, 201)
                            grant_id = json.loads(body)["writeGrantId"]
                            self.assertEqual(service.calls[-1][:3], ("create_console_write_grant", "shared", grant_request))
                            self.assertEqual(running.request(
                                f"/api/postgres/profiles/shared/console/write-grants/{grant_id}", "DELETE", authorized=True,
                            )[0], 200)
                            self.assertEqual(service.calls[-1][0], "revoke_console_write_grant")
                        else:
                            self.assertEqual(status, 404)
                            self.assertEqual(running.request(
                                f"/api/postgres/profiles/shared/console/write-grants/{uuid4()}", "DELETE", authorized=True,
                            )[0], 404)
                    finally:
                        running.close()


if __name__ == "__main__":
    unittest.main()
