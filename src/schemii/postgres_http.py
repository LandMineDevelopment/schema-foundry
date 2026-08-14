from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, ContextManager, FrozenSet
from urllib.parse import parse_qs

from .postgres_console import ConsolePolicy


PROFILE_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(?:/(namespaces|relations|relation|fingerprint|test|introspect|preview|deletion-impact))?$")
DATA_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/data$")
SQL_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/sql$")
CONSOLE_EXECUTIONS_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/console/executions$")
CONSOLE_EXECUTION_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/console/executions/([^/]+)$")
CONSOLE_WRITE_GRANTS_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/console/write-grants$")
CONSOLE_WRITE_GRANT_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/console/write-grants/([^/]+)$")
RELATION_PREVIEW_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/preview$")
RELATION_VERIFY_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/verify$")
RELATION_VERIFY_BATCH_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/verify-batch$")
RELATION_QUERY_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/query$")
RELATION_TEMPORAL_SERIES_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/temporal-series$")
RELATION_DETAIL_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/detail$")
SAVED_WIDGET_QUERY_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/saved-widgets/aggregate$")
SAVED_WIDGET_DETAIL_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/saved-widgets/detail$")
POSTGRES_PROFILE_CAPABILITY = "profiles"
POSTGRES_CATALOG_CAPABILITY = "catalog"
POSTGRES_SCHEMA_CAPABILITY = "schema"
POSTGRES_RELATION_QUERY_CAPABILITY = "relation_query"
POSTGRES_READ_SQL_CAPABILITY = "read_sql"
POSTGRES_CONSOLE_CAPABILITY = "console"
POSTGRES_CONSOLE_WRITE_CAPABILITY = "console_write"


@dataclass(frozen=True)
class ReadSqlRoutePolicy:
    require_database: bool = False
    require_profile_fingerprint: bool = False
    context_fields: FrozenSet[str] = frozenset()
    ai_context_fields: FrozenSet[str] = frozenset()
    allow_explain: bool = True
    max_rows: int = 500
    max_columns: int = 100
    max_result_bytes: int = 1024 * 1024


RouteGuard = Callable[[Any, dict[str, Any]], ContextManager[Any]]
RouteResultHandler = Callable[[Any, dict[str, Any], dict[str, Any]], dict[str, Any]]
SavedWidgetHandler = Callable[[Any, str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class PostgresRoutePolicy:
    application: str
    capabilities: FrozenSet[str]
    read_sql: ReadSqlRoutePolicy = field(default_factory=ReadSqlRoutePolicy)
    relation_query_context_fields: FrozenSet[str] = frozenset()
    temporal_series_context_fields: FrozenSet[str] = frozenset()
    relation_detail_context_fields: FrozenSet[str] = frozenset()
    read_sql_guard: RouteGuard | None = None
    read_sql_result: RouteResultHandler | None = None
    relation_query_guard: RouteGuard | None = None
    temporal_series_guard: RouteGuard | None = None
    relation_detail_guard: RouteGuard | None = None
    saved_widget_query: SavedWidgetHandler | None = None
    saved_widget_detail: SavedWidgetHandler | None = None


DEFAULT_POSTGRES_ROUTE_POLICY = PostgresRoutePolicy(
    "shared",
    frozenset({
        POSTGRES_PROFILE_CAPABILITY, POSTGRES_CATALOG_CAPABILITY, POSTGRES_SCHEMA_CAPABILITY,
        POSTGRES_RELATION_QUERY_CAPABILITY, POSTGRES_READ_SQL_CAPABILITY, POSTGRES_CONSOLE_CAPABILITY,
    }),
)


class PostgresHttpMixin:
    """Shared profile, catalog, and read-only query routes for local apps."""

    postgres_route_policy = DEFAULT_POSTGRES_ROUTE_POLICY
    postgres_console_policy = ConsolePolicy()

    def _has_postgres_capability(self, capability: str) -> bool:
        return capability in self.postgres_route_policy.capabilities

    def _postgres_service_call(self, callback, status: int = 200):
        path = getattr(self, "path", "").split("?", 1)[0]
        if "/console/executions" in path:
            return self._service_call(callback, status)
        execution_class = "read" if any(part in path for part in (
            "/data", "/sql", "/relation/preview", "/relation/query", "/relation/detail",
            "/relation/temporal-series", "/saved-widgets/",
        )) else "catalog"

        def admitted():
            execution = getattr(self.service, "execution", None)
            if execution is None:
                return callback()
            with execution(execution_class):
                return callback()

        return self._service_call(admitted, status)

    def _postgres_profile_dependency_impact(self, profile_id: str) -> dict[str, list[dict]]:
        return {"schemas": [], "dashboards": [], "activeChats": [], "plans": [], "operations": []}

    def _profile_deletion_impact(self, profile_id: str) -> dict:
        profile_fingerprint = self.service.profile_context_fingerprint(profile_id)
        impact = self._postgres_profile_dependency_impact(profile_id)
        encoded = json.dumps(impact, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return {
            "profileId": profile_id,
            "profileFingerprint": profile_fingerprint,
            "impact": impact,
            "impactFingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }

    def _handle_postgres_get(self, parsed) -> bool:
        path = parsed.path
        if path == "/api/postgres/profiles" and self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            if self._authorize_postgres():
                self._postgres_service_call(lambda: {"profiles": self.service.list_profiles()})
            return True
        data_match = DATA_PATH.fullmatch(path)
        if data_match and self._has_postgres_capability(POSTGRES_SCHEMA_CAPABILITY):
            if not self._authorize_postgres():
                return True
            query = parse_qs(parsed.query)
            try:
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["50"])[0])
            except ValueError:
                self.send_json(400, {"error": {"code": "validation_error", "message": "offset and limit must be integers"}})
                return True
            self._postgres_service_call(lambda: self.service.preview_table_data(
                data_match.group(1), query.get("namespace", [None])[0], query.get("table", [None])[0], offset, limit
            ))
            return True
        profile_match = PROFILE_PATH.fullmatch(path)
        action = profile_match.group(2) if profile_match else None
        catalog_action = action in {"namespaces", "relations", "relation"}
        schema_action = action == "fingerprint"
        if profile_match and (
            (catalog_action and self._has_postgres_capability(POSTGRES_CATALOG_CAPABILITY))
            or (schema_action and self._has_postgres_capability(POSTGRES_SCHEMA_CAPABILITY))
            or (action == "deletion-impact" and self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY))
        ):
            if not self._authorize_postgres():
                return True
            if profile_match.group(2) == "deletion-impact":
                self._postgres_service_call(lambda: self._profile_deletion_impact(profile_match.group(1)))
            elif profile_match.group(2) == "namespaces":
                self._postgres_service_call(lambda: {"namespaces": self.service.list_namespaces(profile_match.group(1))})
            elif profile_match.group(2) == "relations":
                query = parse_qs(parsed.query)
                self._postgres_service_call(lambda: self.service.list_relations(
                    profile_match.group(1), query.get("database", [None])[0], query.get("namespace", [None])[0]
                ))
            elif profile_match.group(2) == "relation":
                query = parse_qs(parsed.query)
                self._postgres_service_call(lambda: self.service.inspect_relation(
                    profile_match.group(1), query.get("database", [None])[0], query.get("namespace", [None])[0],
                    query.get("relation", [None])[0], query.get("expectedKind", [None])[0],
                    query.get("expectedFingerprint", [None])[0]
                ))
            else:
                namespace = parse_qs(parsed.query).get("namespace", [None])[0]
                self._postgres_service_call(lambda: self.service.catalog_status(profile_match.group(1), namespace))
            return True
        return False

    def _handle_postgres_post(self, path: str) -> bool:
        if path == "/api/postgres/profiles" and self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            if not self._authorize_postgres():
                return True
            body = self._body_or_error()
            if body is not None:
                self._postgres_service_call(lambda: self.service.save_profile(None, body), 201)
            return True
        sql_match = SQL_PATH.fullmatch(path)
        console_match = CONSOLE_EXECUTIONS_PATH.fullmatch(path)
        write_grants_match = CONSOLE_WRITE_GRANTS_PATH.fullmatch(path)
        relation_preview_match = RELATION_PREVIEW_PATH.fullmatch(path)
        relation_verify_match = RELATION_VERIFY_PATH.fullmatch(path)
        relation_verify_batch_match = RELATION_VERIFY_BATCH_PATH.fullmatch(path)
        relation_query_match = RELATION_QUERY_PATH.fullmatch(path)
        relation_temporal_series_match = RELATION_TEMPORAL_SERIES_PATH.fullmatch(path)
        relation_detail_match = RELATION_DETAIL_PATH.fullmatch(path)
        saved_widget_query_match = SAVED_WIDGET_QUERY_PATH.fullmatch(path)
        saved_widget_detail_match = SAVED_WIDGET_DETAIL_PATH.fullmatch(path)
        profile_match = PROFILE_PATH.fullmatch(path)
        if sql_match and not self._has_postgres_capability(POSTGRES_READ_SQL_CAPABILITY):
            return False
        if console_match and not self._has_postgres_capability(POSTGRES_CONSOLE_CAPABILITY):
            return False
        if write_grants_match and not self._has_postgres_capability(POSTGRES_CONSOLE_WRITE_CAPABILITY):
            return False
        if any((relation_preview_match, relation_verify_match, relation_verify_batch_match, relation_query_match, relation_temporal_series_match, relation_detail_match, saved_widget_query_match, saved_widget_detail_match)) and not self._has_postgres_capability(POSTGRES_RELATION_QUERY_CAPABILITY):
            return False
        if profile_match and profile_match.group(2) == "test" and not self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            return False
        if profile_match and profile_match.group(2) == "introspect" and not self._has_postgres_capability(POSTGRES_SCHEMA_CAPABILITY):
            return False
        if not sql_match and not console_match and not write_grants_match and not relation_preview_match and not relation_verify_match and not relation_verify_batch_match and not relation_query_match and not relation_temporal_series_match and not relation_detail_match and not saved_widget_query_match and not saved_widget_detail_match and not (profile_match and profile_match.group(2) in {"test", "introspect"}):
            return False
        if not self._authorize_postgres():
            return True
        body = self._body_or_error()
        if body is None:
            return True
        if saved_widget_query_match:
            adapter = self.postgres_route_policy.saved_widget_query
            if set(body) != {"dashboardId", "expectedRevision", "widgetId"} or adapter is None:
                self.send_json(400, {"error": {"code": "validation_error", "message": "Saved widget aggregate fields are invalid"}})
            else:
                self._postgres_service_call(lambda: adapter(self, saved_widget_query_match.group(1), body))
        elif saved_widget_detail_match:
            fields = {"dashboardId", "expectedRevision", "widgetId", "selection", "offset", "limit", "sort", "searches"}
            adapter = self.postgres_route_policy.saved_widget_detail
            if set(body) != fields or adapter is None:
                self.send_json(400, {"error": {"code": "validation_error", "message": "Saved widget detail fields are invalid"}})
            else:
                self._postgres_service_call(lambda: adapter(self, saved_widget_detail_match.group(1), body))
        elif write_grants_match:
            grant_fields = {"consoleId", "database", "namespace", "confirmed"}
            if not isinstance(body, dict) or set(body) != grant_fields:
                self.send_json(400, {"error": {"code": "validation_error", "message": "Console write grant request fields are invalid"}})
            else:
                self._postgres_service_call(lambda: self.service.create_console_write_grant(
                    write_grants_match.group(1), body, self.postgres_session_binding, self.postgres_server_id,
                ), 201)
        elif console_match:
            console_fields = {"executionId", "consoleId", "database", "namespace", "sql", "mode", "writeGrantId"}
            if not isinstance(body, dict) or set(body) != console_fields:
                self.send_json(400, {"error": {"code": "validation_error", "message": "Console execution request fields are invalid"}})
            else:
                self._postgres_service_call(lambda: self.service.execute_console(
                    console_match.group(1), body, self.postgres_session_binding, self.postgres_server_id,
                    self.postgres_console_policy,
                ))
        elif relation_detail_match:
            base_fields = {"source", "query", "selection", "detail", "offset", "limit", "sort", "searches"}
            contextual_fields = base_fields | set(self.postgres_route_policy.relation_detail_context_fields)
            if not isinstance(body, dict) or set(body) not in (base_fields, contextual_fields):
                self.send_json(400, {"error": {"code": "validation_error", "message": "detail request fields are invalid"}})
            else:
                def execute_detail():
                    guard = self.postgres_route_policy.relation_detail_guard
                    if guard is None or set(body) == base_fields:
                        return self.service.execute_relation_detail(
                            relation_detail_match.group(1), body["source"], body["query"], body["selection"],
                            body["detail"], body["offset"], body["limit"], body["sort"], body["searches"],
                        )
                    with guard(self, body):
                        return self.service.execute_relation_detail(
                            relation_detail_match.group(1), body["source"], body["query"], body["selection"],
                            body["detail"], body["offset"], body["limit"], body["sort"], body["searches"],
                        )
                self._postgres_service_call(execute_detail)
        elif relation_temporal_series_match:
            manifest_fields = {"source", "query", "action", "refreshGeneration"}
            window_fields = manifest_fields | {"series", "windowStart"}
            context = set(self.postgres_route_policy.temporal_series_context_fields)
            fields = set(body) if isinstance(body, dict) else set()
            expected_fields = manifest_fields if isinstance(body, dict) and body.get("action") == "manifest" else window_fields if isinstance(body, dict) and body.get("action") == "window" else set()
            valid_fields = (expected_fields | context,) if context else (expected_fields,)
            if not expected_fields or fields not in valid_fields:
                self.send_json(400, {"error": {"code": "validation_error", "message": "temporal series request fields are invalid"}})
            else:
                def execute_temporal_series():
                    call = lambda: self.service.execute_temporal_series(
                        relation_temporal_series_match.group(1), body["source"], body["query"], body["action"], body["refreshGeneration"],
                        body.get("series"), body.get("windowStart"),
                    )
                    guard = self.postgres_route_policy.temporal_series_guard
                    if guard is None:
                        return call()
                    with guard(self, body):
                        return call()
                self._postgres_service_call(execute_temporal_series)
        elif relation_query_match:
            base_fields = {"source", "query"}
            contextual_fields = base_fields | set(self.postgres_route_policy.relation_query_context_fields)
            if not isinstance(body, dict) or set(body) not in (base_fields, contextual_fields):
                self.send_json(400, {"error": {"code": "validation_error", "message": "query request fields are invalid"}})
            else:
                def execute_query():
                    guard = self.postgres_route_policy.relation_query_guard
                    if guard is None or set(body) == base_fields:
                        return self.service.execute_widget_query(relation_query_match.group(1), body["source"], body["query"])
                    with guard(self, body):
                        return self.service.execute_widget_query(relation_query_match.group(1), body["source"], body["query"])
                self._postgres_service_call(execute_query)
        elif relation_verify_batch_match:
            if not isinstance(body, dict) or set(body) != {"sources"}:
                self.send_json(400, {"error": {"code": "validation_error", "message": "batch verification fields are invalid"}})
            else:
                self._postgres_service_call(lambda: self.service.verify_relation_sources(
                    relation_verify_batch_match.group(1), body["sources"],
                ))
        elif relation_verify_match:
            self._postgres_service_call(lambda: self.service.verify_relation_source(
                relation_verify_match.group(1), body.get("source")
            ))
        elif relation_preview_match:
            self._postgres_service_call(lambda: self.service.preview_relation_rows(
                relation_preview_match.group(1), body.get("source"), body.get("offset", 0), body.get("limit", 20)
            ))
        elif sql_match:
            policy = self.postgres_route_policy.read_sql
            allowed_fields = {"database", "namespace", "sql"} if policy.require_database else {"namespace", "sql"}
            if policy.require_profile_fingerprint:
                allowed_fields.add("profileFingerprint")
            allowed_fields |= set(policy.context_fields)
            ai_fields = set(policy.ai_context_fields)
            compatible_fields = {"database", "namespace", "sql"}
            valid_fields = (
                isinstance(body, dict)
                and (
                    set(body) == allowed_fields or set(body) == allowed_fields | ai_fields
                    or (not policy.require_database and set(body) == compatible_fields)
                )
            )
            if not valid_fields:
                self.send_json(400, {"error": {"code": "validation_error", "message": "SQL request fields are invalid"}})
            else:
                def execute_sql():
                    def execute():
                        ai_request = bool(ai_fields) and ai_fields <= set(body)
                        result = self.service.execute_read_only_sql(
                            sql_match.group(1), body.get("namespace"), body.get("sql"), database=body.get("database"),
                            expected_profile_fingerprint=body.get("profileFingerprint"),
                            allow_explain=policy.allow_explain and not ai_request, max_rows=policy.max_rows,
                            max_columns=policy.max_columns, max_result_bytes=policy.max_result_bytes,
                        )
                        result_handler = self.postgres_route_policy.read_sql_result
                        return result_handler(self, body, result) if result_handler is not None and ai_request else result
                    guard = self.postgres_route_policy.read_sql_guard if ai_fields <= set(body) else None
                    if guard is None:
                        return execute()
                    with guard(self, body):
                        return execute()
                self._postgres_service_call(execute_sql)
        elif profile_match.group(2) == "test":
            self._postgres_service_call(lambda: self.service.test_profile(profile_match.group(1)))
        else:
            self._postgres_service_call(lambda: self.service.introspect(profile_match.group(1), body.get("namespace")))
        return True

    def _handle_postgres_put(self, path: str) -> bool:
        if not self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            return False
        profile_match = PROFILE_PATH.fullmatch(path)
        if not profile_match or profile_match.group(2) is not None:
            return False
        if not self._authorize_postgres():
            return True
        body = self._body_or_error()
        if body is not None:
            self._postgres_service_call(lambda: self.service.save_profile(profile_match.group(1), body))
        return True

    def _handle_postgres_delete(self, path: str) -> bool:
        write_grant_match = CONSOLE_WRITE_GRANT_PATH.fullmatch(path)
        if write_grant_match and self._has_postgres_capability(POSTGRES_CONSOLE_WRITE_CAPABILITY):
            if self._authorize_postgres():
                self._postgres_service_call(lambda: self.service.revoke_console_write_grant(
                    write_grant_match.group(1), write_grant_match.group(2),
                    self.postgres_session_binding, self.postgres_server_id,
                ))
            return True
        console_match = CONSOLE_EXECUTION_PATH.fullmatch(path)
        if console_match and self._has_postgres_capability(POSTGRES_CONSOLE_CAPABILITY):
            if self._authorize_postgres():
                self._postgres_service_call(lambda: self.service.cancel_console(
                    console_match.group(1), console_match.group(2), self.postgres_session_binding, self.postgres_server_id,
                ))
            return True
        if not self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            return False
        profile_match = PROFILE_PATH.fullmatch(path)
        if not profile_match or profile_match.group(2) is not None:
            return False
        if self._authorize_postgres():
            body = self._body_or_error()
            if body is None:
                return True
            if set(body) != {"profileFingerprint", "impactFingerprint"}:
                self.send_json(400, {"error": {"code": "validation_error", "message": "Profile deletion fields are invalid"}})
                return True

            def delete_profile():
                current = self._profile_deletion_impact(profile_match.group(1))
                if body["profileFingerprint"] != current["profileFingerprint"]:
                    from .postgres_common import ConflictError
                    raise ConflictError("profile_changed", "The PostgreSQL profile changed after deletion was reviewed")
                if body["impactFingerprint"] != current["impactFingerprint"]:
                    from .postgres_common import ConflictError
                    raise ConflictError("profile_dependencies_changed", "Profile dependencies changed after deletion was reviewed")
                return self.service.delete_profile(profile_match.group(1), current["profileFingerprint"])

            self._postgres_service_call(delete_profile)
        return True
