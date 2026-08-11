import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


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

    def preview(self, profile_id, namespace, schema, allow_destructive):
        self.calls.append(("preview", profile_id, namespace, schema, allow_destructive))
        return {"id": "plan_one", "steps": [], "warnings": []}

    def apply(self, profile_id, plan_id, confirm_destructive):
        self.calls.append(("apply", profile_id, plan_id, confirm_destructive))
        return {"projectName": "demo.public", "tables": [], "relationships": [], "functions": []}
