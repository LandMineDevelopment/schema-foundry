from __future__ import annotations

import os
import secrets
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .http_common import make_local_app_handler
from .postgres_http import PostgresHttpMixin
from .postgres_service import PostgresService


def _paths() -> tuple[Path, Path]:
    web_dir = Path(__file__).resolve().parent / "schemer_web"
    configured = os.environ.get("SCHEMER_CONFIG_DIR") or os.environ.get("SCHEMII_CONFIG_DIR", "~/.config/schemii")
    return web_dir, Path(configured).expanduser().resolve()


def make_handler(
    web_dir: Path,
    service: PostgresService,
    session_token: str,
    *,
    server_id: str,
    behind_loopback_proxy: bool = False,
):
    base_handler = make_local_app_handler(
        web_dir, service, session_token, server_id=server_id, behind_loopback_proxy=behind_loopback_proxy,
    )

    class SchemerHandler(PostgresHttpMixin, base_handler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if self._handle_common_get(parsed.path) or self._handle_postgres_get(parsed):
                return
            if parsed.path == "/":
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
                shutdown_thread = threading.Thread(target=self.server.shutdown, name="schemer-shutdown", daemon=True)
                self.send_json(202, {"shuttingDown": True})
                self.wfile.flush()
                shutdown_thread.start()
                return
            if self._handle_postgres_post(path):
                return
            self.send_json(404, {"error": "Unknown API path"})

        def do_PUT(self):
            if not self._handle_postgres_put(urlparse(self.path).path):
                self.send_json(404, {"error": "Unknown API path"})

        def do_DELETE(self):
            if not self._handle_postgres_delete(urlparse(self.path).path):
                self.send_json(404, {"error": "Unknown API path"})

    return SchemerHandler


def main() -> None:
    web_dir, config_dir = _paths()
    host = os.environ.get("SCHEMER_HOST", "127.0.0.1")
    proxy_setting = os.environ.get("SCHEMER_BEHIND_LOOPBACK_PROXY", "0")
    if proxy_setting not in {"0", "1"}:
        raise SystemExit("SCHEMER_BEHIND_LOOPBACK_PROXY must be 0 or 1")
    try:
        port = int(os.environ.get("SCHEMER_PORT", "8081"))
    except ValueError as exc:
        raise SystemExit("SCHEMER_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("SCHEMER_PORT must be from 1 to 65535")
    if not web_dir.is_dir():
        raise SystemExit(f"Static web directory does not exist: {web_dir}")
    service = PostgresService(config_dir)
    handler = make_handler(
        web_dir,
        service,
        secrets.token_urlsafe(32),
        server_id=secrets.token_urlsafe(18),
        behind_loopback_proxy=proxy_setting == "1",
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Schemer running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
