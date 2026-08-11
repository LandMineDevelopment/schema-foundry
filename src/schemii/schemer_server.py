from __future__ import annotations

import os
import secrets
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .dashboard_store import DashboardStore, DashboardStoreError
from .http_common import make_local_app_handler
from .postgres_http import (
    POSTGRES_CATALOG_CAPABILITY,
    POSTGRES_PROFILE_CAPABILITY,
    POSTGRES_RELATION_QUERY_CAPABILITY,
    PostgresHttpMixin,
)
from .postgres_service import PostgresService
from .server_runtime import begin_http_shutdown, parse_port, parse_proxy_setting, run_server, validate_static_directory


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
    behind_loopback_proxy: bool = False,
):
    base_handler = make_local_app_handler(
        web_dir, service, session_token, server_id=server_id, behind_loopback_proxy=behind_loopback_proxy,
    )

    class SchemerHandler(PostgresHttpMixin, base_handler):
        postgres_capabilities = frozenset({
            POSTGRES_PROFILE_CAPABILITY, POSTGRES_CATALOG_CAPABILITY, POSTGRES_RELATION_QUERY_CAPABILITY,
        })

        def _authorize_dashboard(self) -> bool:
            return self._authorize_local_api("Dashboard API", "Dashboard session token is missing or invalid")

        def _dashboard_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
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
            if self._handle_postgres_post(path):
                return
            self.send_json(404, {"error": "Unknown API path"})

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
    validate_static_directory(web_dir)
    service = PostgresService(config_dir)
    dashboard_store = DashboardStore(dashboard_dir)
    dashboard_store.initialize_once()
    handler = make_handler(
        web_dir,
        service,
        dashboard_store,
        secrets.token_urlsafe(32),
        server_id=secrets.token_urlsafe(18),
        behind_loopback_proxy=behind_loopback_proxy,
    )
    run_server(host, port, handler, "Schemer", server_factory=ThreadingHTTPServer)


if __name__ == "__main__":
    main()
