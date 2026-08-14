import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


class QuietHandlerMixin:
    def log_message(self, format, *args):
        pass


class RunningHttpServer:
    def __init__(self, handler, token="session-token"):
        quiet_handler = type(f"Quiet{handler.__name__}", (QuietHandlerMixin, handler), {})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), quiet_handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.token = token

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method="GET", payload=None, content_type="application/json", authorized=False, headers=None, timeout=5):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(f"{self.base_url}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", content_type)
        if authorized:
            request.add_header("X-Schemii-Token", self.token)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read(), response.headers
        except HTTPError as error:
            try:
                return error.code, error.read(), error.headers
            finally:
                error.close()


class FakePostgresService:
    def __init__(self, *, profiles=None, namespaces=None, relations=None, descriptor=None, preview_rows=None, test_result=None):
        self.calls = []
        self.profiles = list(profiles or [])
        self.namespaces = list(namespaces or ["public"])
        self.relations = list(relations or [])
        self.descriptor = descriptor
        self.preview_rows = list(preview_rows or [])
        self.test_result = dict(test_result or {"ok": True})
        self.view_layout_token = "0" * 64
        self.view_expected_absent = False
        self.view_operation = "upsert"
        self.view_expectation = {"kind": "view", "fingerprint": "a" * 64}
        self.view_saved_id = "view_summary"
        self.ai_write_results = {}

    def list_profiles(self):
        return self.profiles

    def profile_context_fingerprint(self, profile_id):
        profile = next(item for item in self.profiles if item["id"] == profile_id)
        from schemii.ai_http import ai_context_fingerprint
        return ai_context_fingerprint([profile_id, profile.get("host"), profile.get("port"), profile.get("dbname"), profile.get("user"), profile.get("sslmode")])

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

    def execute_read_only_sql(self, profile_id, namespace, statement, **policy):
        self.calls.append(("execute_read_only_sql", profile_id, namespace, statement, policy))
        return {"columns": [{"name": "answer"}], "rows": [[1]], "rowCount": 1, "truncated": False}

    def execute_console(self, profile_id, body, binding, server_id, policy=None):
        self.calls.append(("execute_console", profile_id, body, binding, server_id, policy))
        return {
            "executionId": body["executionId"],
            "target": {"profileId": profile_id, "database": body["database"], "namespace": body["namespace"]},
            "mode": "read", "committed": False, "statements": [], "limits": {},
        }

    def cancel_console(self, profile_id, execution_id, binding, server_id):
        self.calls.append(("cancel_console", profile_id, execution_id, binding, server_id))
        return {"requested": True}

    def create_console_write_grant(self, profile_id, body, binding, server_id):
        self.calls.append(("create_console_write_grant", profile_id, body, binding, server_id))
        return {"writeGrantId": str(uuid4())}

    def revoke_console_write_grant(self, profile_id, grant_id, binding, server_id):
        self.calls.append(("revoke_console_write_grant", profile_id, grant_id, binding, server_id))
        return {"revoked": True}

    def list_namespaces(self, profile_id):
        self.calls.append(("list_namespaces", profile_id))
        return self.namespaces

    def list_relations(self, profile_id, database, namespace):
        self.calls.append(("list_relations", profile_id, database, namespace))
        return {"profileId": profile_id, "database": database, "namespace": namespace, "relations": self.relations}

    def inspect_relation(self, profile_id, database, namespace, relation, expected_kind=None, expected_fingerprint=None):
        self.calls.append(("inspect_relation", profile_id, database, namespace, relation, expected_kind, expected_fingerprint))
        return self.descriptor or {
            "profileId": profile_id, "database": database, "namespace": namespace, "relation": relation,
            "kind": "table", "columns": [], "fingerprint": "live",
            "definition": {"status": "unavailable", "reason": "not_supported"},
        }

    def preview_relation_rows(self, profile_id, source, offset, limit):
        self.calls.append(("preview_relation_rows", profile_id, source, offset, limit))
        return {
            **source, "columns": [], "rows": self.preview_rows, "offset": offset,
            "nextOffset": offset + len(self.preview_rows), "hasMore": False, "stableOrder": False,
        }

    def verify_relation_source(self, profile_id, source):
        self.calls.append(("verify_relation_source", profile_id, source))
        return {"status": "verified", "matches": True, **source, "missingColumns": [], "addedColumns": [], "changedColumns": []}

    def execute_widget_query(self, profile_id, source, query):
        self.calls.append(("execute_widget_query", profile_id, source, query))
        return {"columns": [{"label": "Rows"}], "rows": [[1]], "sql": "SELECT count(*)", "parameters": []}

    def execute_temporal_series(self, profile_id, source, query, action, refresh_generation, series=None, window_start=None):
        self.calls.append(("execute_temporal_series", profile_id, source, query, action, refresh_generation, series, window_start))
        return {"seriesVersion": 1, "action": action, "refreshGeneration": refresh_generation}

    def execute_relation_detail(self, profile_id, source, query, selection, detail, offset, limit, sort, searches):
        self.calls.append(("execute_relation_detail", profile_id, source, query, selection, detail, offset, limit, sort, searches))
        return {"columns": [], "rows": [], "matchingRowCount": 0, "offset": offset, "limit": limit, "hasMore": False}

    def catalog_status(self, profile_id, namespace):
        self.calls.append(("catalog_status", profile_id, namespace))
        return {"profileId": profile_id, "namespace": namespace, "fingerprint": "live"}

    def test_profile(self, profile_id):
        self.calls.append(("test_profile", profile_id))
        return self.test_result

    def introspect(self, profile_id, namespace):
        self.calls.append(("introspect", profile_id, namespace))
        return {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}

    def preview(self, profile_id, namespace, schema, allow_destructive, *, persist=True):
        self.calls.append(("preview", profile_id, namespace, schema, allow_destructive, persist))
        return {"id": "plan_one" if persist else None, "previewOnly": not persist, "steps": [], "warnings": []}

    def preview_ai_migration(self, operation_id, profile_id, database, namespace, schema, allow_destructive, schema_binding):
        self.calls.append(("preview_ai_migration", operation_id, profile_id, database, namespace, schema, allow_destructive, schema_binding))
        return {"id": None, "previewOnly": True, "applyPlanId": "ai_plan_one", "destructive": False, "steps": [], "warnings": [], "liveFingerprint": "live"}

    def apply_ai_migration(self, operation_id, plan_id, profile_id, database, namespace, expected_destructive, confirm_destructive):
        self.calls.append(("apply_ai_migration", operation_id, plan_id, profile_id, database, namespace, expected_destructive, confirm_destructive))
        return {"kind": "migration_applied", "operationId": operation_id, "planId": plan_id, "refreshedSchema": {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}}

    def reconcile_ai_migration(self, plan_id, profile_id):
        self.calls.append(("reconcile_ai_migration", plan_id, profile_id))
        return {"kind": "migration_applied", "planId": plan_id}

    def update_ai_migration_result(self, plan_id, result):
        self.calls.append(("update_ai_migration_result", plan_id, result))
        return result

    def preview_ai_insert_rows(self, operation_id, profile_id, database, namespace, relation, rows, schema_binding):
        self.calls.append(("preview_ai_insert_rows", operation_id, profile_id, database, namespace, relation, rows, schema_binding))
        return {"id": None, "previewOnly": True, "applyPlanId": "ai_plan_insert", "planDigest": "a" * 64, "kind": "insert_rows", "rowCount": len(rows), "columns": list(rows[0]), "rows": rows, "steps": [], "warnings": []}

    def preview_ai_create_view(self, operation_id, profile_id, database, namespace, relation, definition, schema_binding):
        self.calls.append(("preview_ai_create_view", operation_id, profile_id, database, namespace, relation, definition, schema_binding))
        return {"id": None, "previewOnly": True, "applyPlanId": "ai_plan_view", "planDigest": "b" * 64, "kind": "create_view", "steps": [{"action": "create", "objectType": "view", "name": relation, "sql": definition + ";", "destructive": False}], "warnings": []}

    def apply_ai_postgres_write(self, operation_id, plan_id, profile_id, database, namespace, relation, expected_kind, expected_review_digest):
        self.calls.append(("apply_ai_postgres_write", operation_id, plan_id, profile_id, database, namespace, relation, expected_kind, expected_review_digest))
        target = {"profileId": profile_id, "database": database, "namespace": namespace, "relation": relation}
        if expected_kind == "insert_rows":
            result = {"kind": "rows_inserted", "operationId": operation_id, "planId": plan_id, "target": target, "insertedRowCount": 2}
            self.ai_write_results[plan_id] = result
            return result
        result = {
            "kind": "view_created", "operationId": operation_id, "planId": plan_id, "target": target,
            "schemaBinding": {"schemaId": "schema_one", "revision": 1, "layoutToken": self.view_layout_token},
            "descriptor": {**target, "kind": "view", "fingerprint": "b" * 64},
            "desiredDefinition": f'CREATE VIEW "{namespace}"."{relation}" AS SELECT 1', "queryDefinition": "SELECT 1",
        }
        self.ai_write_results[plan_id] = result
        return result

    def reconcile_ai_postgres_write(self, plan_id, profile_id):
        self.calls.append(("reconcile_ai_postgres_write", plan_id, profile_id))
        return self.ai_write_results.get(plan_id, {"kind": "rows_inserted", "planId": plan_id, "insertedRowCount": 2})

    def update_ai_postgres_write_result(self, plan_id, result):
        self.calls.append(("update_ai_postgres_write_result", plan_id, result))
        self.ai_write_results[plan_id] = result
        return result

    def apply(self, profile_id, plan_id, confirm_destructive):
        self.calls.append(("apply", profile_id, plan_id, confirm_destructive))
        return {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}

    def preview_view_mutation(self, profile_id, database, namespace, relation, operation, expectation, desired, allow_destructive, schema_binding):
        self.calls.append(("preview_view_mutation", profile_id, database, namespace, relation, operation, expectation, desired, allow_destructive, schema_binding))
        return {"id": "plan_view", "operation": operation, "destructive": operation == "delete", "steps": [], "warnings": []}

    def apply_view_mutation(self, profile_id, plan_id, confirm_destructive):
        self.calls.append(("apply_view_mutation", profile_id, plan_id, confirm_destructive))
        common = {
            "applied": True, "planId": plan_id,
            "operation": self.view_operation,
            "schemaBinding": {"schemaId": "schema_one", "expectedSchemaRevision": 1, "layoutToken": self.view_layout_token, "savedViewId": self.view_saved_id},
            "expectedAbsent": self.view_expected_absent,
        }
        if self.view_operation == "delete":
            return {**common, "deleted": {
                "profileId": profile_id, "database": "demo", "namespace": "public", "relation": "summary", "kind": self.view_expectation["kind"],
            }}
        return {**common,
            "descriptor": {
                "profileId": profile_id, "database": "demo", "namespace": "public", "relation": "summary",
                "kind": "view", "fingerprint": "b" * 64,
            },
            "desiredDefinition": 'CREATE OR REPLACE VIEW "public"."summary" AS SELECT 2',
            "queryDefinition": "SELECT 2",
        }

    def view_mutation_binding(self, profile_id, plan_id):
        self.calls.append(("view_mutation_binding", profile_id, plan_id))
        return {
            "schemaBinding": {"schemaId": "schema_one", "expectedSchemaRevision": 1, "layoutToken": self.view_layout_token, "savedViewId": self.view_saved_id},
            "database": "demo", "namespace": "public", "relation": "summary",
            "operation": self.view_operation, "expectation": self.view_expectation,
        }
