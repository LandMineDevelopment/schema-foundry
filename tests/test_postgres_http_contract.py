import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import DashboardStore
from schemii.schema_store import SchemaStore
from schemii.schemer_server import make_handler as make_schemer_handler
from schemii.server import make_handler as make_schemii_handler
from tests.http_test_support import FakePostgresService, RunningHttpServer


class PostgresHttpContractTests(unittest.TestCase):
    def test_shared_profile_and_catalog_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factories = {
                "schemii": lambda service: make_schemii_handler(
                    ROOT / "src/schemii/web", service, SchemaStore(root / "schemas"),
                    "session-token", server_id="schemii-contract",
                ),
                "schemer": lambda service: make_schemer_handler(
                    ROOT / "src/schemii/schemer_web", service, DashboardStore(root / "dashboards"),
                    "session-token", server_id="schemer-contract",
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
                    finally:
                        running.close()


if __name__ == "__main__":
    unittest.main()
