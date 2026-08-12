from __future__ import annotations

import re
from urllib.parse import parse_qs

from .postgres_console import ConsolePolicy


PROFILE_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(?:/(namespaces|relations|relation|fingerprint|test|introspect|preview))?$")
DATA_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/data$")
SQL_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/sql$")
CONSOLE_EXECUTIONS_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/console/executions$")
CONSOLE_EXECUTION_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/console/executions/([^/]+)$")
CONSOLE_WRITE_GRANTS_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/console/write-grants$")
CONSOLE_WRITE_GRANT_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/console/write-grants/([^/]+)$")
RELATION_PREVIEW_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/preview$")
RELATION_VERIFY_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/verify$")
RELATION_QUERY_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/query$")
RELATION_TEMPORAL_SERIES_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/temporal-series$")
RELATION_DETAIL_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/detail$")
POSTGRES_PROFILE_CAPABILITY = "profiles"
POSTGRES_CATALOG_CAPABILITY = "catalog"
POSTGRES_SCHEMA_CAPABILITY = "schema"
POSTGRES_RELATION_QUERY_CAPABILITY = "relation_query"
POSTGRES_READ_SQL_CAPABILITY = "read_sql"
POSTGRES_CONSOLE_CAPABILITY = "console"
POSTGRES_CONSOLE_WRITE_CAPABILITY = "console_write"


class PostgresHttpMixin:
    """Shared profile, catalog, and read-only query routes for local apps."""

    postgres_capabilities = frozenset({
        POSTGRES_PROFILE_CAPABILITY,
        POSTGRES_CATALOG_CAPABILITY,
        POSTGRES_SCHEMA_CAPABILITY,
        POSTGRES_RELATION_QUERY_CAPABILITY,
        POSTGRES_READ_SQL_CAPABILITY,
        POSTGRES_CONSOLE_CAPABILITY,
    })
    postgres_read_sql_policy = {
        "require_database": False,
        "require_profile_fingerprint": False,
        "reject_privileged_role": False,
        "context_fields": frozenset(),
        "allow_explain": True,
        "max_rows": 500,
        "max_columns": 100,
        "max_result_bytes": 1024 * 1024,
    }
    postgres_relation_query_context_fields = frozenset()
    postgres_temporal_series_context_fields = frozenset()
    postgres_relation_detail_context_fields = frozenset()
    postgres_console_policy = ConsolePolicy()

    def _has_postgres_capability(self, capability: str) -> bool:
        return capability in self.postgres_capabilities

    def _handle_postgres_get(self, parsed) -> bool:
        path = parsed.path
        if path == "/api/postgres/profiles" and self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            if self._authorize_postgres():
                self._service_call(lambda: {"profiles": self.service.list_profiles()})
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
            self._service_call(lambda: self.service.preview_table_data(
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
        ):
            if not self._authorize_postgres():
                return True
            if profile_match.group(2) == "namespaces":
                self._service_call(lambda: {"namespaces": self.service.list_namespaces(profile_match.group(1))})
            elif profile_match.group(2) == "relations":
                query = parse_qs(parsed.query)
                self._service_call(lambda: self.service.list_relations(
                    profile_match.group(1), query.get("database", [None])[0], query.get("namespace", [None])[0]
                ))
            elif profile_match.group(2) == "relation":
                query = parse_qs(parsed.query)
                self._service_call(lambda: self.service.inspect_relation(
                    profile_match.group(1), query.get("database", [None])[0], query.get("namespace", [None])[0],
                    query.get("relation", [None])[0], query.get("expectedKind", [None])[0],
                    query.get("expectedFingerprint", [None])[0]
                ))
            else:
                namespace = parse_qs(parsed.query).get("namespace", [None])[0]
                self._service_call(lambda: self.service.catalog_status(profile_match.group(1), namespace))
            return True
        return False

    def _handle_postgres_post(self, path: str) -> bool:
        if path == "/api/postgres/profiles" and self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            if not self._authorize_postgres():
                return True
            body = self._body_or_error()
            if body is not None:
                self._service_call(lambda: self.service.save_profile(None, body), 201)
            return True
        sql_match = SQL_PATH.fullmatch(path)
        console_match = CONSOLE_EXECUTIONS_PATH.fullmatch(path)
        write_grants_match = CONSOLE_WRITE_GRANTS_PATH.fullmatch(path)
        relation_preview_match = RELATION_PREVIEW_PATH.fullmatch(path)
        relation_verify_match = RELATION_VERIFY_PATH.fullmatch(path)
        relation_query_match = RELATION_QUERY_PATH.fullmatch(path)
        relation_temporal_series_match = RELATION_TEMPORAL_SERIES_PATH.fullmatch(path)
        relation_detail_match = RELATION_DETAIL_PATH.fullmatch(path)
        profile_match = PROFILE_PATH.fullmatch(path)
        if sql_match and not self._has_postgres_capability(POSTGRES_READ_SQL_CAPABILITY):
            return False
        if console_match and not self._has_postgres_capability(POSTGRES_CONSOLE_CAPABILITY):
            return False
        if write_grants_match and not self._has_postgres_capability(POSTGRES_CONSOLE_WRITE_CAPABILITY):
            return False
        if any((relation_preview_match, relation_verify_match, relation_query_match, relation_temporal_series_match, relation_detail_match)) and not self._has_postgres_capability(POSTGRES_RELATION_QUERY_CAPABILITY):
            return False
        if profile_match and profile_match.group(2) == "test" and not self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            return False
        if profile_match and profile_match.group(2) == "introspect" and not self._has_postgres_capability(POSTGRES_SCHEMA_CAPABILITY):
            return False
        if not sql_match and not console_match and not write_grants_match and not relation_preview_match and not relation_verify_match and not relation_query_match and not relation_temporal_series_match and not relation_detail_match and not (profile_match and profile_match.group(2) in {"test", "introspect"}):
            return False
        if not self._authorize_postgres():
            return True
        body = self._body_or_error()
        if body is None:
            return True
        if write_grants_match:
            grant_fields = {"consoleId", "database", "namespace", "confirmed"}
            if not isinstance(body, dict) or set(body) != grant_fields:
                self.send_json(400, {"error": {"code": "validation_error", "message": "Console write grant request fields are invalid"}})
            else:
                self._service_call(lambda: self.service.create_console_write_grant(
                    write_grants_match.group(1), body, self.postgres_session_binding, self.postgres_server_id,
                ), 201)
        elif console_match:
            console_fields = {"executionId", "consoleId", "database", "namespace", "sql", "mode", "writeGrantId"}
            if not isinstance(body, dict) or set(body) != console_fields:
                self.send_json(400, {"error": {"code": "validation_error", "message": "Console execution request fields are invalid"}})
            else:
                self._service_call(lambda: self.service.execute_console(
                    console_match.group(1), body, self.postgres_session_binding, self.postgres_server_id,
                    self.postgres_console_policy,
                ))
        elif relation_detail_match:
            base_fields = {"source", "query", "selection", "detail", "offset", "limit", "sort", "searches"}
            contextual_fields = base_fields | set(self.postgres_relation_detail_context_fields)
            if not isinstance(body, dict) or set(body) not in (base_fields, contextual_fields):
                self.send_json(400, {"error": {"code": "validation_error", "message": "detail request fields are invalid"}})
            else:
                def execute_detail():
                    guard = getattr(self, "_postgres_relation_detail_guard", None)
                    if guard is None or set(body) == base_fields:
                        return self.service.execute_relation_detail(
                            relation_detail_match.group(1), body["source"], body["query"], body["selection"],
                            body["detail"], body["offset"], body["limit"], body["sort"], body["searches"],
                        )
                    with guard(body):
                        return self.service.execute_relation_detail(
                            relation_detail_match.group(1), body["source"], body["query"], body["selection"],
                            body["detail"], body["offset"], body["limit"], body["sort"], body["searches"],
                        )
                self._service_call(execute_detail)
        elif relation_temporal_series_match:
            manifest_fields = {"source", "query", "action", "refreshGeneration"}
            window_fields = manifest_fields | {"series", "windowStart"}
            context = set(self.postgres_temporal_series_context_fields)
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
                    guard = getattr(self, "_postgres_temporal_series_guard", None)
                    if guard is None:
                        return call()
                    with guard(body):
                        return call()
                self._service_call(execute_temporal_series)
        elif relation_query_match:
            base_fields = {"source", "query"}
            contextual_fields = base_fields | set(self.postgres_relation_query_context_fields)
            if not isinstance(body, dict) or set(body) not in (base_fields, contextual_fields):
                self.send_json(400, {"error": {"code": "validation_error", "message": "query request fields are invalid"}})
            else:
                def execute_query():
                    guard = getattr(self, "_postgres_relation_query_guard", None)
                    if guard is None or set(body) == base_fields:
                        return self.service.execute_widget_query(relation_query_match.group(1), body["source"], body["query"])
                    with guard(body):
                        return self.service.execute_widget_query(relation_query_match.group(1), body["source"], body["query"])
                self._service_call(execute_query)
        elif relation_verify_match:
            self._service_call(lambda: self.service.verify_relation_source(
                relation_verify_match.group(1), body.get("source")
            ))
        elif relation_preview_match:
            self._service_call(lambda: self.service.preview_relation_rows(
                relation_preview_match.group(1), body.get("source"), body.get("offset", 0), body.get("limit", 20)
            ))
        elif sql_match:
            policy = self.postgres_read_sql_policy
            allowed_fields = {"database", "namespace", "sql"} if policy["require_database"] else {"namespace", "sql"}
            if policy.get("require_profile_fingerprint"):
                allowed_fields.add("profileFingerprint")
            allowed_fields |= set(policy.get("context_fields", ()))
            compatible_fields = {"database", "namespace", "sql"}
            valid_fields = (
                isinstance(body, dict)
                and (set(body) == allowed_fields or (not policy["require_database"] and set(body) == compatible_fields))
            )
            if not valid_fields:
                self.send_json(400, {"error": {"code": "validation_error", "message": "SQL request fields are invalid"}})
            else:
                def execute_sql():
                    guard = getattr(self, "_postgres_read_sql_guard", None)
                    if guard is None:
                        return self.service.execute_read_only_sql(
                            sql_match.group(1), body.get("namespace"), body.get("sql"), database=body.get("database"),
                            expected_profile_fingerprint=body.get("profileFingerprint"),
                            reject_privileged_role=policy.get("reject_privileged_role", False),
                            allow_explain=policy["allow_explain"], max_rows=policy["max_rows"],
                            max_columns=policy["max_columns"], max_result_bytes=policy["max_result_bytes"],
                        )
                    with guard(body):
                        return self.service.execute_read_only_sql(
                            sql_match.group(1), body.get("namespace"), body.get("sql"), database=body.get("database"),
                            expected_profile_fingerprint=body.get("profileFingerprint"),
                            reject_privileged_role=policy.get("reject_privileged_role", False),
                            allow_explain=policy["allow_explain"], max_rows=policy["max_rows"],
                            max_columns=policy["max_columns"], max_result_bytes=policy["max_result_bytes"],
                        )
                self._service_call(execute_sql)
        elif profile_match.group(2) == "test":
            self._service_call(lambda: self.service.test_profile(profile_match.group(1)))
        else:
            self._service_call(lambda: self.service.introspect(profile_match.group(1), body.get("namespace")))
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
            self._service_call(lambda: self.service.save_profile(profile_match.group(1), body))
        return True

    def _handle_postgres_delete(self, path: str) -> bool:
        write_grant_match = CONSOLE_WRITE_GRANT_PATH.fullmatch(path)
        if write_grant_match and self._has_postgres_capability(POSTGRES_CONSOLE_WRITE_CAPABILITY):
            if self._authorize_postgres():
                self._service_call(lambda: self.service.revoke_console_write_grant(
                    write_grant_match.group(1), write_grant_match.group(2),
                    self.postgres_session_binding, self.postgres_server_id,
                ))
            return True
        console_match = CONSOLE_EXECUTION_PATH.fullmatch(path)
        if console_match and self._has_postgres_capability(POSTGRES_CONSOLE_CAPABILITY):
            if self._authorize_postgres():
                self._service_call(lambda: self.service.cancel_console(
                    console_match.group(1), console_match.group(2), self.postgres_session_binding, self.postgres_server_id,
                ))
            return True
        if not self._has_postgres_capability(POSTGRES_PROFILE_CAPABILITY):
            return False
        profile_match = PROFILE_PATH.fullmatch(path)
        if not profile_match or profile_match.group(2) is not None:
            return False
        if self._authorize_postgres():
            self._service_call(lambda: self.service.delete_profile(profile_match.group(1)))
        return True
