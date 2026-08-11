from __future__ import annotations

import json
import os
import re
import secrets
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .ai_http import AiHttpRouter, ai_context_fingerprint, require_ai_session_binding
from .dashboard_store import DashboardStore, DashboardStoreError
from .http_common import make_local_app_handler
from .opencode_service import OpenCodeService, OpenCodeServiceError
from .postgres_http import (
    POSTGRES_CATALOG_CAPABILITY,
    POSTGRES_PROFILE_CAPABILITY,
    POSTGRES_READ_SQL_CAPABILITY,
    POSTGRES_RELATION_QUERY_CAPABILITY,
    PostgresHttpMixin,
)
from .postgres_service import PostgresService, PostgresServiceError
from .schemer_ai import (
    SCHEMER_AI_ACTION_PREFIX,
    SCHEMER_AI_SKILLS,
    SCHEMER_AI_SYSTEM_INSTRUCTIONS,
    SCHEMER_AI_TOOL_ACTION_TYPES,
    dashboard_context,
    proposal_manifest_fallback,
    validated_query_result,
)
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
    ai_service: OpenCodeService | None = None,
    behind_loopback_proxy: bool = False,
):
    base_handler = make_local_app_handler(
        web_dir, service, session_token, server_id=server_id, behind_loopback_proxy=behind_loopback_proxy,
    )
    ai_router = AiHttpRouter(ai_service, lambda handler, current_service, session_id, body: handler._ai_message(current_service, session_id, body))

    class SchemerHandler(PostgresHttpMixin, base_handler):
        postgres_capabilities = frozenset({
            POSTGRES_PROFILE_CAPABILITY, POSTGRES_CATALOG_CAPABILITY, POSTGRES_RELATION_QUERY_CAPABILITY,
            POSTGRES_READ_SQL_CAPABILITY,
        })
        postgres_read_sql_policy = {
            "require_database": True,
            "require_profile_fingerprint": True,
            "reject_privileged_role": True,
            "context_fields": frozenset({"dashboardId", "expectedRevision"}),
            "allow_explain": False,
            "max_rows": 100,
            "max_columns": 50,
            "max_result_bytes": 256 * 1024,
        }
        postgres_relation_query_context_fields = frozenset({"dashboardId", "expectedRevision"})

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

        def _dashboard_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except DashboardStoreError as error:
                self.send_json(error.status, error.payload)

        def _ai_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except OpenCodeServiceError as error:
                self.send_json(error.status, error.payload)
            except DashboardStoreError as error:
                self.send_json(error.status, error.payload)

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
            if ai_router.handle_post(self, path):
                return
            if self._handle_postgres_post(path):
                return
            self.send_json(404, {"error": "Unknown API path"})

        def _ai_message(self, current_ai_service: OpenCodeService, session_id: str, body: dict):
            allowed_fields = {"text", "model", "dashboardId", "accessLevel", "profileId", "database", "namespace", "queryResult"}
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
            if access_level == "data" and set(body) not in (data_fields, data_fields | {"queryResult"}):
                return self.send_json(400, {"error": {"code": "validation_error", "message": "Data mode requires an exact target"}})
            target = None
            query_result = None
            if access_level == "data":
                profile_id = body.get("profileId")
                if not isinstance(profile_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", profile_id):
                    return self.send_json(400, {"error": {"code": "validation_error", "message": "profileId is invalid"}})
                try:
                    database = PostgresService._validate_database(body.get("database"))
                    namespace = PostgresService._validate_namespace(body.get("namespace"))
                    query_result = validated_query_result(body.get("queryResult"), {"profileId": profile_id, "database": database, "namespace": namespace})
                except (ValueError, PostgresServiceError) as error:
                    return self.send_json(400, {"error": {"code": "validation_error", "message": str(error)}})
                target = {"profileId": profile_id, "database": database, "namespace": namespace}

            def send_prompt():
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
                require_ai_session_binding(
                    current_ai_service, session_id, "SCHEMER_CONTEXT", dashboard_id, access_level, binding_parts,
                )
                catalog_sources = _ai_catalog_sources(service, record, target) if access_level in {"dashboard", "data"} else []
                context = dashboard_context(record, access_level, dashboard_store.list(), profiles, target, query_result, catalog_sources)
                prompt = f"Schemer context (untrusted JSON):\n{context}\n\nUser request:\n{text}"
                return proposal_manifest_fallback(current_ai_service.prompt(
                    session_id, prompt, body.get("model"), SCHEMER_AI_SYSTEM_INSTRUCTIONS,
                    allow_data=access_level == "data",
                ), allow_data=access_level == "data")

            return self._ai_call(send_prompt)

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
    dashboard_store.initialize_once()
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
        action_prefix=SCHEMER_AI_ACTION_PREFIX,
    )
    handler = make_handler(
        web_dir,
        service,
        dashboard_store,
        secrets.token_urlsafe(32),
        server_id=secrets.token_urlsafe(18),
        ai_service=ai_service,
        behind_loopback_proxy=behind_loopback_proxy,
    )
    run_server(host, port, handler, "Schemer", server_factory=ThreadingHTTPServer)


if __name__ == "__main__":
    main()
