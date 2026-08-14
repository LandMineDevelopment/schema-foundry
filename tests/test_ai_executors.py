import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.schemer_ai_executor import SchemerAiExecutor
from schemii.schemii_ai_executor import SchemiiAiExecutor


class StoreDouble:
    def __init__(self, record):
        self.record = record

    def get(self, resource_id):
        return self.record


class ExecutorContractTests(unittest.TestCase):
    def test_schemii_client_command_executes_without_http_handler_state(self):
        store = StoreDouble({"id": "schema_one", "revision": 3, "layoutToken": "a" * 64, "schema": {"projectName": "Demo"}})
        executor = SchemiiAiExecutor(object(), store, object(), mutation_types=set(), has_access=lambda *_: False, policy_binding=lambda *_args, **_kwargs: {})
        result = executor.execute(
            {"type": "open_project", "schemaId": "schema_one", "projectName": "Demo"},
            "chat", "schema_one", store.record, None, {}, {"revision": 3, "layoutToken": "a" * 64},
            "operation", "metadata", session_binding="binding", server_id="server", console_policy=None,
            proposal_envelope=lambda *_: {},
        )
        self.assertEqual(result["command"], {"type": "open_schema", "schemaId": "schema_one", "revision": 3, "layoutToken": "a" * 64})

    def test_schemer_client_command_executes_without_http_handler_state(self):
        dashboard = {"id": "dashboard_one", "revision": 4, "dashboard": {"title": "Demo"}}
        executor = SchemerAiExecutor(object(), StoreDouble(dashboard), object(), catalog_sources=lambda *_: [], configured_widget=lambda *_: {})
        result = executor.execute(
            {"type": "dashboard_open", "dashboardId": "dashboard_one", "expectedRevision": 4, "title": "Demo"},
            "operation", chat={"id": "chat", "dashboardId": "dashboard_one", "accessLevel": "metadata"},
            record=dashboard, profile=None, schema_concurrency={"revision": 4}, authorization_target={},
        )
        self.assertEqual(result["command"], {"type": "open_dashboard", "dashboardId": "dashboard_one", "revision": 4})


if __name__ == "__main__":
    unittest.main()
