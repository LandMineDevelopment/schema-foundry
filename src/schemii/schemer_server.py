from __future__ import annotations

import json
import os
import re
import secrets
import hashlib
import math
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .ai_authority import AiAuthority, AiAuthorityError
from .ai_actions import normalize_schemer_action
from .ai_http import AiHttpRouter, ai_context_fingerprint, ai_session_prefix, authority_call, bounded_ai_query_result, issue_ai_proposals, list_bound_ai_sessions, require_ai_session_binding
from .dashboard_store import DashboardStore, DashboardStoreError
from .http_common import make_local_app_handler
from .opencode_service import OpenCodeService, OpenCodeServiceError
from .postgres_http import (
    POSTGRES_CATALOG_CAPABILITY,
    POSTGRES_CONSOLE_CAPABILITY,
    POSTGRES_PROFILE_CAPABILITY,
    POSTGRES_READ_SQL_CAPABILITY,
    POSTGRES_RELATION_QUERY_CAPABILITY,
    PostgresHttpMixin,
)
from .postgres_service import PostgresService, PostgresServiceError
from .schemer_ai import (
    SCHEMER_AI_SKILLS,
    SCHEMER_AI_SYSTEM_INSTRUCTIONS,
    SCHEMER_AI_TOOL_ACTION_TYPES,
    dashboard_context,
    validated_query_result,
)
from .widget_query import QueryValidationError, normalize_query


def _configured_ai_widget(service, action, operation_id, widget_count):
    source = action["source"]
    descriptor = service.inspect_relation(source["profileId"], source["database"], source["namespace"], source["relation"], source["kind"], source["fingerprint"])
    columns = [{key: column[key] for key in ("name", "type", "nullable", "ordinal")} for column in descriptor["columns"]]
    verified_source = {key: descriptor[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")} | {"columns": columns}
    try:
        query = normalize_query(action["query"], columns)
    except QueryValidationError as error:
        raise PostgresServiceError(400, "invalid_widget_query", str(error)) from error
    dimensions, measures = query["dimensions"], query["measures"]
    table = {"version": 1, "columns": [
        {"targetId": item["id"], "width": 180 if kind == "dimension" else 120, "hidden": False, "pinned": kind == "dimension", "label": item["label"]}
        for kind, values in (("dimension", dimensions), ("measure", measures)) for item in values
    ], "pageSize": 25}
    dimension_id = dimensions[0]["id"] if dimensions else None
    measure_ids = [item["id"] for item in measures]
    visualization = {"version": 1, "mode": action["visualizationMode"], "selections": {
        "kpi": {"measureIds": measure_ids}, "bar": {"dimensionId": dimension_id, "measureIds": measure_ids},
        "line": {"dimensionId": dimension_id, "measureIds": measure_ids}, "donut": {"dimensionId": dimension_id, "measureId": measure_ids[0]},
    }}
    detail = {"version": 1, "columns": [{"sourceColumn": item["name"], "label": item["name"], "width": 160, "hidden": False, "searchable": True, "numberFormat": {"style": "auto"}} for item in columns[:64]], "defaultSort": None, "rowIdentifier": None, "pageSize": 25}
    widget_id = f"widget_{hashlib.sha256(operation_id.encode()).hexdigest()[:20]}"
    if action["visualizationMode"] in {"bar", "line", "donut"} and not dimensions:
        raise PostgresServiceError(400, "invalid_visualization", "Chart widgets require at least one dimension")
    if action["visualizationMode"] == "kpi" and dimensions:
        raise PostgresServiceError(400, "invalid_visualization", "KPI widgets cannot persist grouped dimensions")
    selected_query = json.loads(json.dumps(query))
    if action["visualizationMode"] == "kpi": selected_query["dimensions"] = []
    elif action["visualizationMode"] in {"bar", "line", "donut"}:
        selected_query["dimensions"] = [dimensions[0]]
        selected_query["measures"] = [measures[0]] if action["visualizationMode"] == "donut" else measures
    selected_ids = {item["id"] for item in selected_query["dimensions"]} | {item["id"] for item in selected_query["measures"]}
    selected_query["sort"] = [item for item in selected_query["sort"] if item["targetId"] in selected_ids]
    result = service.execute_widget_query(source["profileId"], verified_source, selected_query)
    if action["visualizationMode"] in {"bar", "line", "donut"}:
        values = [row[index] for row in result.get("rows", []) for index in range(1, len(row))]
        try:
            numeric = [float(value) for value in values if value is not None and not isinstance(value, bool)]
        except (TypeError, ValueError, OverflowError):
            raise PostgresServiceError(409, "invalid_visualization", "Query result is not numeric enough for the selected chart")
        if len(numeric) != len([value for value in values if value is not None]) or any(not math.isfinite(value) for value in numeric):
            raise PostgresServiceError(409, "invalid_visualization", "Query result is not finite numeric data")
        if action["visualizationMode"] == "donut" and any(value is None for value in values):
            raise PostgresServiceError(409, "invalid_visualization", "Donut chart results cannot contain null values")
        if action["visualizationMode"] in {"bar", "donut"} and any(value < 0 for value in numeric) or action["visualizationMode"] == "donut" and not any(value > 0 for value in numeric):
            raise PostgresServiceError(409, "invalid_visualization", "Query result cannot render the selected non-negative chart")
        if action["visualizationMode"] == "line" and not numeric:
            raise PostgresServiceError(409, "invalid_visualization", "Line chart results require at least one finite non-null point")
    return {"id": widget_id, "kind": "aggregate_report", "title": action["title"], "layout": {"desktop": {"x": 0, "y": 0, "w": 4, "h": 3}, "mobile": {"order": widget_count, "h": 3}}, "configuration": {"source": verified_source, "query": query, "table": table, "visualization": visualization, **({"detail": detail} if columns else {})}}
from .schemer_examples import mercury_dashboard_from_service
from .server_runtime import begin_http_shutdown, parse_port, parse_proxy_setting, run_server, validate_static_directory


def _ai_catalog_sources(service: PostgresService, record: dict, target: dict[str, str] | None) -> list[dict]:
    candidates = []
    if target is not None:
        catalog = service.list_relations(target["profileId"], target["database"], target["namespace"])
        candidates.extend({
            "profileId": target["profileId"], "database": target["database"], "namespace": target["namespace"],
            "relation": relation["name"], "kind": relation["kind"],
        } for relation in catalog.get("relations", [])[:8])
    else:
        for widget in record["dashboard"]["widgets"]:
            source = widget.get("configuration", {}).get("source")
            if isinstance(source, dict):
                candidates.append({key: source.get(key) for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")})
    resolved = []
    seen = set()
    used_bytes = 0
    for candidate in candidates:
        key = tuple(candidate.get(field) for field in ("profileId", "database", "namespace", "relation", "kind", "fingerprint"))
        if key in seen or len(resolved) >= 8:
            continue
        seen.add(key)
        try:
            descriptor = service.inspect_relation(
                candidate["profileId"], candidate["database"], candidate["namespace"], candidate["relation"],
                candidate.get("kind"), candidate.get("fingerprint"),
            )
        except (KeyError, PostgresServiceError, ValueError):
            continue
        safe = {
            "profileId": descriptor["profileId"], "database": descriptor["database"], "namespace": descriptor["namespace"],
            "relation": descriptor["relation"], "kind": descriptor["kind"], "fingerprint": descriptor["fingerprint"],
            "columns": [
                {key: column[key] for key in ("name", "type", "nullable", "ordinal", "suggestions") if key in column}
                for column in descriptor.get("columns", [])
            ],
        }
        size = len(json.dumps(safe, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
        if size > 12 * 1024 or used_bytes + size > 28 * 1024:
            continue
        resolved.append(safe)
        used_bytes += size
    return resolved


def _paths() -> tuple[Path, Path, Path]:
    web_dir = Path(__file__).resolve().parent / "schemer_web"
    configured = os.environ.get("SCHEMER_CONFIG_DIR") or os.environ.get("SCHEMII_CONFIG_DIR", "~/.config/schemii")
    dashboard_dir = os.environ.get("SCHEMER_DASHBOARD_DIR", "~/.local/share/schemer/dashboards")
    return web_dir, Path(configured).expanduser().resolve(), Path(dashboard_dir).expanduser().resolve()


def make_handler(
    web_dir: Path,
    service: PostgresService,
    dashboard_store: DashboardStore,
    session_token: str,
    *,
    server_id: str,
    ai_authority: AiAuthority,
    ai_service: OpenCodeService | None = None,
    behind_loopback_proxy: bool = False,
):
    base_handler = make_local_app_handler(
        web_dir, service, session_token, server_id=server_id, behind_loopback_proxy=behind_loopback_proxy,
    )
    ai_router = AiHttpRouter(
        ai_service,
        lambda handler, current_service, session_id, body: handler._ai_message(current_service, session_id, body),
        lambda handler, current_service, session_id, proposal_id, operation, body: handler._ai_proposal(
            current_service, session_id, proposal_id, operation, body,
        ),
        lambda handler, current_service, session_id: handler._ai_history(current_service, session_id),
        lambda handler, current_service, session_id, operation_id: handler._ai_operation_status(
            current_service, session_id, operation_id,
        ),
    )

    class SchemerHandler(PostgresHttpMixin, base_handler):
        postgres_capabilities = frozenset({
            POSTGRES_PROFILE_CAPABILITY, POSTGRES_CATALOG_CAPABILITY, POSTGRES_RELATION_QUERY_CAPABILITY,
            POSTGRES_READ_SQL_CAPABILITY, POSTGRES_CONSOLE_CAPABILITY,
        })
        postgres_read_sql_policy = {
            "require_database": True,
            "require_profile_fingerprint": True,
            "context_fields": frozenset({"dashboardId", "expectedRevision"}),
            "allow_explain": False,
            "max_rows": 100,
            "max_columns": 50,
            "max_result_bytes": 256 * 1024,
        }
        postgres_relation_query_context_fields = frozenset({"dashboardId", "expectedRevision"})
        postgres_temporal_series_context_fields = frozenset({"dashboardId", "expectedRevision", "widgetId"})
        postgres_relation_detail_context_fields = frozenset({"dashboardId", "expectedRevision"})

        def _authorize_dashboard(self) -> bool:
            return self._authorize_local_api("Dashboard API", "Dashboard session token is missing or invalid")

        @contextmanager
        def _postgres_dashboard_revision_guard(self, body):
            dashboard_id = body.get("dashboardId")
            expected_revision = body.get("expectedRevision")
            if not isinstance(dashboard_id, str) or isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                raise PostgresServiceError(400, "validation_error", "SQL dashboard context is invalid")
            try:
                with dashboard_store.guard_revision(dashboard_id, expected_revision):
                    yield
            except DashboardStoreError as error:
                detail = error.payload["error"]
                raise PostgresServiceError(error.status, detail["code"], detail["message"]) from error

        _postgres_read_sql_guard = _postgres_dashboard_revision_guard
        _postgres_relation_query_guard = _postgres_dashboard_revision_guard
        _postgres_relation_detail_guard = _postgres_dashboard_revision_guard

        @contextmanager
        def _postgres_temporal_series_guard(self, body):
            dashboard_id = body.get("dashboardId")
            expected_revision = body.get("expectedRevision")
            widget_id = body.get("widgetId")
            if not isinstance(dashboard_id, str) or isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or not isinstance(widget_id, str):
                raise PostgresServiceError(400, "validation_error", "Temporal series dashboard context is invalid")
            try:
                with dashboard_store.guard_revision(dashboard_id, expected_revision) as record:
                    widget = next((item for item in record["dashboard"]["widgets"] if item["id"] == widget_id), None)
                    configuration = widget.get("configuration", {}) if widget else {}
                    visualization = configuration.get("visualization", {})
                    selection = visualization.get("selections", {}).get("line", {})
                    saved_query = configuration.get("query", {})
                    selected_dimension = selection.get("dimensionId")
                    selected_measures = selection.get("measureIds", [])
                    target_ids = {selected_dimension, *selected_measures}
                    expected_query = {
                        **saved_query,
                        "dimensions": [item for item in saved_query.get("dimensions", []) if item.get("id") == selected_dimension],
                        "measures": [item for item in saved_query.get("measures", []) if item.get("id") in selected_measures],
                        "sort": [item for item in saved_query.get("sort", []) if item.get("targetId") in target_ids],
                    }
                    if not widget or visualization.get("mode") != "line" or configuration.get("source") != body.get("source") or expected_query != body.get("query"):
                        raise PostgresServiceError(409, "temporal_series_changed", "The temporal series no longer matches the saved line widget")
                    yield
            except DashboardStoreError as error:
                detail = error.payload["error"]
                raise PostgresServiceError(error.status, detail["code"], detail["message"]) from error

        def _dashboard_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except DashboardStoreError as error:
                self.send_json(error.status, error.payload)
            except AiAuthorityError as error:
                self.send_json(error.status, error.to_dict())

        def _ai_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except OpenCodeServiceError as error:
                self.send_json(error.status, error.payload)
            except DashboardStoreError as error:
                self.send_json(error.status, error.payload)

        def _service_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except PostgresServiceError as error:
                self.send_json(error.status, error.to_dict())
            except DashboardStoreError as error:
                self.send_json(error.status, error.payload)
            except AiAuthorityError as error:
                self.send_json(error.status, error.to_dict())

        @staticmethod
        def _dashboard_id(path: str) -> str | None:
            prefix = "/api/dashboards/"
            return path[len(prefix):] if path.startswith(prefix) else None

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if self._handle_common_get(path):
                return
            if path == "/api/dashboards":
                if self._authorize_dashboard():
                    self._dashboard_call(lambda: {"dashboards": dashboard_store.list()})
                return
            if ai_router.handle_get(self, path):
                return
            dashboard_id = self._dashboard_id(path)
            if dashboard_id is not None:
                if self._authorize_dashboard():
                    self._dashboard_call(lambda: dashboard_store.get(dashboard_id))
                return
            if self._handle_postgres_get(parsed):
                return
            if path == "/":
                self.path = "/index.html"
            return super().do_GET()

        def do_HEAD(self):
            if urlparse(self.path).path == "/":
                self.path = "/index.html"
            return super().do_HEAD()

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/shutdown":
                if not self._authorize_shutdown():
                    return
                begin_http_shutdown(self, "schemer-shutdown")
                return
            if path == "/api/dashboards":
                if not self._authorize_dashboard():
                    return
                body = self._body_or_error()
                if body is not None:
                    self._dashboard_call(lambda: dashboard_store.create(body.get("title"), body.get("sourceId")), 201)
                return
            if path == "/api/examples/mercury/reset":
                if not self._authorize_dashboard():
                    return
                body = self._body_or_error()
                if body is None:
                    return
                if not isinstance(body, dict) or set(body) != {"expectedRevision"}:
                    return self.send_json(400, {"error": {"code": "validation_error", "message": "Mercury reset fields are invalid"}})
                try:
                    template = mercury_dashboard_from_service(service)
                    self.send_json(200, dashboard_store.restore_mercury(template, body.get("expectedRevision")))
                except (DashboardStoreError, PostgresServiceError) as error:
                    self.send_json(error.status, error.payload)
                return
            if ai_router.handle_post(self, path):
                return
            if self._handle_postgres_post(path):
                return
            self.send_json(404, {"error": "Unknown API path"})

        def _ai_message(self, current_ai_service: OpenCodeService, session_id: str, body: dict):
            allowed_fields = {"text", "model", "dashboardId", "accessLevel", "profileId", "database", "namespace", "expectedRevision", "resultRef"}
            if not isinstance(body, dict) or set(body) - allowed_fields:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "Message fields are invalid"}})
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or text != text.strip() or len(text.encode("utf-8")) > 16 * 1024 or "\x00" in text:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "text is invalid"}})
            access_level = body.get("accessLevel")
            if access_level not in {"metadata", "dashboard", "data"}:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "accessLevel is invalid"}})
            dashboard_id = body.get("dashboardId")
            if not isinstance(dashboard_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", dashboard_id):
                return self.send_json(400, {"error": {"code": "validation_error", "message": "dashboardId is invalid"}})
            base_fields = {"text", "model", "dashboardId", "accessLevel"}
            data_fields = base_fields | {"profileId", "database", "namespace"}
            if access_level != "data" and set(body) != base_fields:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "Data fields require data access"}})
            if access_level == "data" and set(body) not in (data_fields, data_fields | {"expectedRevision", "resultRef"}):
                return self.send_json(400, {"error": {"code": "validation_error", "message": "Data mode requires an exact target"}})
            target = None
            query_result = None
            reservation = None
            if access_level == "data":
                profile_id = body.get("profileId")
                if not isinstance(profile_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", profile_id):
                    return self.send_json(400, {"error": {"code": "validation_error", "message": "profileId is invalid"}})
                try:
                    database = PostgresService._validate_database(body.get("database"))
                    namespace = PostgresService._validate_namespace(body.get("namespace"))
                except (ValueError, PostgresServiceError) as error:
                    return self.send_json(400, {"error": {"code": "validation_error", "message": str(error)}})
                target = {"profileId": profile_id, "database": database, "namespace": namespace}

            def send_prompt():
                query_result = None
                reservation = None
                record = dashboard_store.get(dashboard_id)
                profiles = service.list_profiles()
                if target is not None:
                    selected = next((item for item in profiles if item.get("id") == target["profileId"]), None)
                    if selected is None:
                        raise OpenCodeServiceError(404, "not_found", "Profile was not found")
                    if selected.get("dbname") != target["database"]:
                        raise OpenCodeServiceError(409, "database_changed", "The saved profile database does not match the requested database")
                binding_parts = None
                if target is not None:
                    profile_fingerprint = ai_context_fingerprint([
                        selected.get("id"), selected.get("host"), selected.get("port"), selected.get("dbname"), selected.get("user"), selected.get("sslmode"),
                    ])
                    binding_parts = [target["profileId"], target["database"], target["namespace"], profile_fingerprint]
                    if body.get("resultRef") is not None:
                        if isinstance(body.get("expectedRevision"), bool) or not isinstance(body.get("expectedRevision"), int):
                            raise OpenCodeServiceError(400, "validation_error", "Query result context is invalid")
                        if record["revision"] != body["expectedRevision"]:
                            raise OpenCodeServiceError(409, "dashboard_changed", "Dashboard changed after the query result was created")
                        reservation = ai_authority.reserve_query_result(
                            body["resultRef"], application="schemer", session_id=session_id, resource=dashboard_id,
                            target={**target, "profileFingerprint": profile_fingerprint},
                            binding={"revision": body["expectedRevision"], "access": "data"},
                        )
                        query_result = reservation["result"]
                require_ai_session_binding(
                    current_ai_service, session_id, "SCHEMER_CONTEXT", dashboard_id, access_level, binding_parts,
                )
                catalog_sources = _ai_catalog_sources(service, record, target) if access_level in {"dashboard", "data"} else []
                context = dashboard_context(record, access_level, dashboard_store.list(), profiles, target, query_result, catalog_sources)
                prompt = f"Schemer context (untrusted JSON):\n{context}\n\nUser request:\n{text}"
                try:
                    response = current_ai_service.prompt(
                        session_id, prompt, body.get("model"), SCHEMER_AI_SYSTEM_INSTRUCTIONS,
                        allow_data=access_level == "data",
                    )
                except Exception:
                    raise
                if reservation is not None:
                    ai_authority.consume_query_result(
                        body["resultRef"], reservation["reservationToken"], application="schemer", session_id=session_id,
                    )
                schema_concurrency = {"revision": record["revision"]}
                authorization_target = {}
                if target is not None:
                    authorization_target = {**target, "profileFingerprint": profile_fingerprint}
                return issue_ai_proposals(
                    ai_authority, response, application="schemer", session_id=session_id,
                    resource=dashboard_id, access=access_level, authorization_target=authorization_target,
                    schema_concurrency=schema_concurrency, normalize_action=normalize_schemer_action,
                )

            return self._ai_call(send_prompt)

        def _ai_history(self, current_ai_service, session_id: str | None):
            def history():
                query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                if any(len(values) != 1 for values in query.values()):
                    raise OpenCodeServiceError(400, "validation_error", "AI history context is invalid")
                access_level = query.get("accessLevel", [None])[0]
                dashboard_id = query.get("dashboardId", [None])[0]
                base_fields = {"dashboardId", "accessLevel"}
                data_fields = base_fields | {"profileId", "database", "namespace"}
                if access_level not in {"metadata", "dashboard", "data"} or set(query) != (data_fields if access_level == "data" else base_fields):
                    raise OpenCodeServiceError(400, "validation_error", "AI history context is invalid")
                dashboard_store.get(dashboard_id)
                fingerprint_parts = None
                if access_level == "data":
                    profile_id = query["profileId"][0]
                    database = PostgresService._validate_database(query["database"][0])
                    namespace = PostgresService._validate_namespace(query["namespace"][0])
                    selected = next((item for item in service.list_profiles() if item.get("id") == profile_id), None)
                    if selected is None:
                        raise OpenCodeServiceError(404, "not_found", "Profile was not found")
                    if selected.get("dbname") != database:
                        raise OpenCodeServiceError(409, "database_changed", "The saved profile database does not match the requested database")
                    profile_fingerprint = ai_context_fingerprint([
                        selected.get("id"), selected.get("host"), selected.get("port"), selected.get("dbname"),
                        selected.get("user"), selected.get("sslmode"),
                    ])
                    fingerprint_parts = [profile_id, database, namespace, profile_fingerprint]
                expected = ai_session_prefix("SCHEMER_CONTEXT", dashboard_id, access_level, fingerprint_parts)
                if session_id is None:
                    return list_bound_ai_sessions(current_ai_service, expected)
                if not current_ai_service.session_identity(session_id)["title"].startswith(expected):
                    raise OpenCodeServiceError(404, "not_found", "AI session was not found")
                return current_ai_service.session_messages(session_id)

            return self._ai_call(history)

        def _ai_proposal(self, current_ai_service, session_id: str, proposal_id: str, operation: str, body: dict):
            if operation == "reconcile":
                def reconcile():
                    current = ai_authority.operation_for_proposal(proposal_id, application="schemer", session_id=session_id)
                    if current["state"] != "uncertain": return {"operation": current}
                    operation_record = ai_authority.operation_record(current["id"], application="schemer", session_id=session_id)
                    action = operation_record["action"]
                    if action.get("type") not in {"dashboard_create", "widget_create", "widget_rename", "widget_duplicate", "widget_delete"}: return {"operation": current}
                    receipt = dashboard_store.operation_receipt(operation_record["resource"], current["id"])
                    if receipt is None:
                        return {"operation": ai_authority.resolve_operation(current["id"], application="schemer", session_id=session_id, state="failed", error={"code": "operation_not_applied", "message": "No dashboard mutation receipt exists; request a fresh proposal"})}
                    return {"operation": ai_authority.resolve_operation(current["id"], application="schemer", session_id=session_id, state="succeeded", result=receipt)}
                return authority_call(self, reconcile)
            current_ai_service.verify_session(session_id)
            if operation == "execute":
                return self._ai_execute_proposal(current_ai_service, session_id, proposal_id, body)
            if operation in {"finalize", "release"}:
                if set(body) != {"claimToken"}:
                    return self.send_json(400, {"error": {"code": "validation_error", "message": "Proposal completion fields are invalid"}})
                callback = ai_authority.finalize_proposal if operation == "finalize" else ai_authority.release_proposal
                return authority_call(self, lambda: callback(
                    proposal_id, body.get("claimToken"), application="schemer", session_id=session_id,
                ))
            allowed = {"dashboardId", "accessLevel", "profileId", "database", "namespace"}
            if set(body) - allowed or body.get("accessLevel") not in {"metadata", "dashboard", "data"}:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "Proposal claim fields are invalid"}})
            dashboard_id = body.get("dashboardId")
            record = dashboard_store.get(dashboard_id)
            access_level = body["accessLevel"]
            schema_concurrency = {"revision": record["revision"]}
            authorization_target = {}
            binding_parts = None
            if access_level == "data":
                profile_id = body.get("profileId")
                database = PostgresService._validate_database(body.get("database"))
                namespace = PostgresService._validate_namespace(body.get("namespace"))
                selected = next((item for item in service.list_profiles() if item.get("id") == profile_id), None)
                if selected is None:
                    raise OpenCodeServiceError(404, "not_found", "Profile was not found")
                if selected.get("dbname") != database:
                    raise OpenCodeServiceError(409, "database_changed", "The saved profile database does not match the requested database")
                fingerprint = ai_context_fingerprint([
                    selected.get("id"), selected.get("host"), selected.get("port"), selected.get("dbname"),
                    selected.get("user"), selected.get("sslmode"),
                ])
                binding_parts = [profile_id, database, namespace, fingerprint]
                authorization_target = {
                    "profileId": profile_id, "database": database, "namespace": namespace,
                    "profileFingerprint": fingerprint,
                }
            require_ai_session_binding(
                current_ai_service, session_id, "SCHEMER_CONTEXT", dashboard_id, access_level, binding_parts,
            )
            return authority_call(self, lambda: ai_authority.claim_proposal(
                proposal_id, application="schemer", session_id=session_id, resource=dashboard_id,
                access=access_level, authorization_target=authorization_target,
                schema_concurrency=schema_concurrency,
            ))

        def _ai_operation_status(self, current_ai_service, session_id: str, operation_id: str):
            return authority_call(self, lambda: {"operation": ai_authority.operation(
                operation_id, application="schemer", session_id=session_id,
            )})

        def _ai_execute_proposal(self, current_ai_service, session_id: str, proposal_id: str, body: dict):
            allowed = {"dashboardId", "accessLevel", "profileId", "database", "namespace", "confirmation"}
            if set(body) - allowed or body.get("confirmation") != {"accepted": True, "mode": "explicit"}:
                return self.send_json(400, {"error": {"code": "validation_error", "message": "Proposal execution fields are invalid"}})
            dashboard_id = body.get("dashboardId")
            access = body.get("accessLevel")
            record = dashboard_store.get(dashboard_id)
            schema_concurrency = {"revision": record["revision"]}
            authorization_target = {}
            profile = None
            binding_parts = None
            if access == "data":
                profile = next((item for item in service.list_profiles() if item.get("id") == body.get("profileId")), None)
                if profile is None or profile.get("dbname") != body.get("database"):
                    return self.send_json(409, {"error": {"code": "database_changed", "message": "The PostgreSQL target changed"}})
                fingerprint = ai_context_fingerprint([
                    profile.get("id"), profile.get("host"), profile.get("port"), profile.get("dbname"), profile.get("user"), profile.get("sslmode"),
                ])
                binding_parts = [profile["id"], profile["dbname"], body.get("namespace"), fingerprint]
                authorization_target = {
                    "profileId": profile["id"], "database": profile["dbname"], "namespace": body.get("namespace"),
                    "profileFingerprint": fingerprint,
                }
            require_ai_session_binding(current_ai_service, session_id, "SCHEMER_CONTEXT", dashboard_id, access, binding_parts)
            try:
                operation = ai_authority.create_operation(
                    proposal_id, application="schemer", session_id=session_id, resource=dashboard_id,
                    access=access, authorization_target=authorization_target,
                    schema_concurrency=schema_concurrency,
                )
            except AiAuthorityError as error:
                return self.send_json(error.status, error.to_dict())
            execution_owner = operation.pop("executionOwner", False)
            if not execution_owner:
                return self.send_json(200, {"operation": operation})
            action = ai_authority.operation_record(operation["id"], application="schemer", session_id=session_id)["action"]
            try:
                action_type = action.get("type")
                if action_type == "read_query":
                    if profile is None or any(action.get(key) != authorization_target[key] for key in ("profileId", "database", "namespace")):
                        raise PostgresServiceError(409, "action_target_changed", "Query target no longer matches the proposal")
                    result = service.execute_read_only_sql(
                        profile["id"], authorization_target["namespace"], action.get("sql"), database=profile["dbname"],
                        expected_profile_fingerprint=service.profile_context_fingerprint(profile["id"]),
                        allow_explain=False, max_rows=100, max_columns=50, max_result_bytes=256 * 1024,
                    )
                    reference = ai_authority.register_query_result(
                        application="schemer", session_id=session_id, resource=dashboard_id, target=authorization_target,
                        binding={"revision": record["revision"], "access": "data"},
                        result=bounded_ai_query_result(result, max_rows=100, max_columns=50, max_bytes=48 * 1024),
                    )
                    result = {"kind": "sql_result", "display": result, "resultRef": reference["id"], "schemaConcurrency": schema_concurrency, "authorizationTarget": authorization_target}
                elif action_type == "dashboard_open":
                    target = dashboard_store.get(action.get("dashboardId"))
                    if target["revision"] != action.get("expectedRevision") or target["dashboard"]["title"] != action.get("title"):
                        raise DashboardStoreError(409, "dashboard_changed", "Target dashboard changed; request a fresh proposal")
                    result = {"kind": "client_command", "command": {"type": "open_dashboard", "dashboardId": target["id"], "revision": target["revision"]}}
                elif action_type == "dashboard_create":
                    result = dashboard_store.create_ai(operation["id"], action["title"])
                elif action_type in {"widget_create", "widget_rename", "widget_duplicate", "widget_delete"}:
                    if action["dashboardId"] != dashboard_id or action["expectedRevision"] != schema_concurrency["revision"]:
                        raise DashboardStoreError(409, "dashboard_changed", "Dashboard binding no longer matches the proposal")
                    prepared = None
                    if action_type == "widget_create" and "source" in action:
                        if access == "data" and any(action["source"].get(key) != authorization_target.get(key) for key in ("profileId", "database", "namespace")):
                            raise PostgresServiceError(409, "action_target_changed", "Widget source no longer matches the confirmed data target")
                        allowed_sources = _ai_catalog_sources(service, record, authorization_target if access == "data" else None)
                        identity = tuple(action["source"].get(key) for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint"))
                        if identity not in {tuple(item.get(key) for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")) for item in allowed_sources}:
                            raise PostgresServiceError(409, "action_target_changed", "Widget source is outside the bounded catalog context issued for this proposal")
                        with dashboard_store.guard_revision(dashboard_id, schema_concurrency["revision"]) as guarded:
                            prepared = _configured_ai_widget(service, action, operation["id"], len(guarded["dashboard"]["widgets"]))
                    result = dashboard_store.apply_ai_mutation(dashboard_id, operation["id"], schema_concurrency["revision"], action, prepared)
                else:
                    raise OpenCodeServiceError(409, "action_temporarily_unavailable", "This action is unavailable until its server execution adapter is installed")
            except (OpenCodeServiceError, DashboardStoreError, PostgresServiceError, AiAuthorityError) as error:
                payload = error.payload if hasattr(error, "payload") else error.to_dict()
                uncertain = isinstance(error, DashboardStoreError) and error.status >= 500
                finished = ai_authority.finish_operation(
                    operation["id"], application="schemer", session_id=session_id, state="uncertain" if uncertain else "failed", error=payload["error"],
                )
                return self.send_json(getattr(error, "status", 400), {"operation": finished})
            except Exception:
                finished = ai_authority.finish_operation(
                    operation["id"], application="schemer", session_id=session_id, state="uncertain",
                    error={"code": "execution_outcome_unknown", "message": "Operation outcome is uncertain; reload authoritative state"},
                )
                return self.send_json(500, {"operation": finished})
            finished = ai_authority.finish_operation(
                operation["id"], application="schemer", session_id=session_id, state="succeeded", result=result,
            )
            return self.send_json(200, {"operation": finished})

        def do_PUT(self):
            path = urlparse(self.path).path
            dashboard_id = self._dashboard_id(path)
            if dashboard_id is not None:
                if not self._authorize_dashboard():
                    return
                body = self._body_or_error()
                if body is not None:
                    self._dashboard_call(lambda: dashboard_store.save(dashboard_id, body))
                return
            if not self._handle_postgres_put(path):
                self.send_json(404, {"error": "Unknown API path"})

        def do_DELETE(self):
            path = urlparse(self.path).path
            if ai_router.handle_delete(self, path):
                return
            dashboard_id = self._dashboard_id(path)
            if dashboard_id is not None:
                if self._authorize_dashboard():
                    self._dashboard_call(lambda: dashboard_store.delete(dashboard_id))
                return
            if not self._handle_postgres_delete(path):
                self.send_json(404, {"error": "Unknown API path"})

    return SchemerHandler


def main() -> None:
    web_dir, config_dir, dashboard_dir = _paths()
    host = os.environ.get("SCHEMER_HOST", "127.0.0.1")
    behind_loopback_proxy = parse_proxy_setting(
        os.environ.get("SCHEMER_BEHIND_LOOPBACK_PROXY", "0"), "SCHEMER_BEHIND_LOOPBACK_PROXY",
    )
    port = parse_port(os.environ.get("SCHEMER_PORT", "8081"), "SCHEMER_PORT")
    try:
        ai_timeout = float(os.environ.get("SCHEMER_OPENCODE_TIMEOUT", "120"))
    except ValueError as exc:
        raise SystemExit("SCHEMER_OPENCODE_TIMEOUT must be a number") from exc
    if not 1 <= ai_timeout <= 300:
        raise SystemExit("SCHEMER_OPENCODE_TIMEOUT must be from 1 to 300 seconds")
    validate_static_directory(web_dir)
    service = PostgresService(config_dir)
    dashboard_store = DashboardStore(dashboard_dir)
    try:
        mercury_template = mercury_dashboard_from_service(service)
    except PostgresServiceError:
        mercury_template = None
    dashboard_store.initialize_once(mercury_template)
    if mercury_template is not None:
        dashboard_store.upgrade_mercury_example(mercury_template)
    ai_service = OpenCodeService(
        os.environ.get("SCHEMER_OPENCODE_URL", ""),
        os.environ.get("SCHEMER_OPENCODE_USERNAME", "opencode"),
        os.environ.get("SCHEMER_OPENCODE_PASSWORD", ""),
        ai_timeout,
        workspace="/workspace-schemer",
        custom_tools=set(SCHEMER_AI_TOOL_ACTION_TYPES),
        tool_action_types=SCHEMER_AI_TOOL_ACTION_TYPES,
        safe_skills=SCHEMER_AI_SKILLS,
        data_tools={"schemer_read_query"},
    )
    handler = make_handler(
        web_dir,
        service,
        dashboard_store,
        secrets.token_urlsafe(32),
        server_id=secrets.token_urlsafe(18),
        ai_authority=AiAuthority(config_dir / "ai_authority" / "v1", "schemer"),
        ai_service=ai_service,
        behind_loopback_proxy=behind_loopback_proxy,
    )
    run_server(host, port, handler, "Schemer", server_factory=ThreadingHTTPServer, shutdown_callback=service.close)


if __name__ == "__main__":
    main()
