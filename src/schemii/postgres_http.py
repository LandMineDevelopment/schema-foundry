from __future__ import annotations

import re
from urllib.parse import parse_qs


PROFILE_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(?:/(namespaces|relations|relation|fingerprint|test|introspect|preview))?$")
DATA_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/data$")
SQL_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/sql$")
RELATION_PREVIEW_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/relation/preview$")


class PostgresHttpMixin:
    """Shared profile, catalog, and read-only query routes for local apps."""

    def _handle_postgres_get(self, parsed) -> bool:
        path = parsed.path
        if path == "/api/postgres/profiles":
            if self._authorize_postgres():
                self._service_call(lambda: {"profiles": self.service.list_profiles()})
            return True
        data_match = DATA_PATH.fullmatch(path)
        if data_match:
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
        if profile_match and profile_match.group(2) in {"namespaces", "relations", "relation", "fingerprint"}:
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
        if path == "/api/postgres/profiles":
            if not self._authorize_postgres():
                return True
            body = self._body_or_error()
            if body is not None:
                self._service_call(lambda: self.service.save_profile(None, body), 201)
            return True
        sql_match = SQL_PATH.fullmatch(path)
        relation_preview_match = RELATION_PREVIEW_PATH.fullmatch(path)
        profile_match = PROFILE_PATH.fullmatch(path)
        if not sql_match and not relation_preview_match and not (profile_match and profile_match.group(2) in {"test", "introspect"}):
            return False
        if not self._authorize_postgres():
            return True
        body = self._body_or_error()
        if body is None:
            return True
        if relation_preview_match:
            self._service_call(lambda: self.service.preview_relation_rows(
                relation_preview_match.group(1), body.get("source"), body.get("offset", 0), body.get("limit", 20)
            ))
        elif sql_match:
            self._service_call(lambda: self.service.execute_read_only_sql(sql_match.group(1), body.get("namespace"), body.get("sql")))
        elif profile_match.group(2) == "test":
            self._service_call(lambda: self.service.test_profile(profile_match.group(1)))
        else:
            self._service_call(lambda: self.service.introspect(profile_match.group(1), body.get("namespace")))
        return True

    def _handle_postgres_put(self, path: str) -> bool:
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
        profile_match = PROFILE_PATH.fullmatch(path)
        if not profile_match or profile_match.group(2) is not None:
            return False
        if self._authorize_postgres():
            self._service_call(lambda: self.service.delete_profile(profile_match.group(1)))
        return True
