from __future__ import annotations

import json
import mimetypes
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from .postgres_service import PostgresService, PostgresServiceError


MAX_BODY_SIZE = 5 * 1024 * 1024
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
SHARED_WEB_DIR = Path(__file__).resolve().parent / "shared_web"


def is_local_request(client_host: str, host_header: str, origin: str | None, behind_loopback_proxy: bool) -> bool:
    host = host_header.rsplit(":", 1)[0].strip("[]").lower()
    return (
        (behind_loopback_proxy or client_host in {"127.0.0.1", "::1"})
        and host in {"localhost", "127.0.0.1", "::1"}
        and (not origin or urlparse(origin).hostname in {"localhost", "127.0.0.1", "::1"})
    )


def make_local_app_handler(
    web_dir: Path,
    postgres_service: PostgresService,
    session_token: str,
    *,
    server_id: str,
    behind_loopback_proxy: bool = False,
):
    class LocalAppHandler(SimpleHTTPRequestHandler):
        service = postgres_service

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_dir), **kwargs)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
            super().end_headers()

        def send_json(self, status: int, payload):
            content = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _send_shared_asset(self, path: str) -> bool:
            prefix = "/shared/"
            if not path.startswith(prefix):
                return False
            name = path[len(prefix):]
            if not name or "/" in name or "\\" in name:
                self.send_error(404, "File not found")
                return True
            asset = SHARED_WEB_DIR / name
            if not asset.is_file():
                self.send_error(404, "File not found")
                return True
            content = asset.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return True

        def _handle_common_get(self, path: str) -> bool:
            if path == "/api/session":
                if not self._is_local_request():
                    self.send_json(403, {"error": {"code": "forbidden", "message": "Session requires a local origin"}})
                else:
                    self.send_json(200, {"token": session_token, "serverId": server_id})
                return True
            return self._send_shared_asset(path)

        def _is_local_request(self) -> bool:
            return is_local_request(
                self.client_address[0],
                self.headers.get("Host", ""),
                self.headers.get("Origin"),
                behind_loopback_proxy,
            )

        def _authorize_postgres(self) -> bool:
            if not self._is_local_request():
                self.send_json(403, {"error": {"code": "forbidden", "message": "PostgreSQL API requires a local origin"}})
                return False
            if self.headers.get("X-Schemii-Token") != session_token:
                self.send_json(403, {"error": {"code": "invalid_session", "message": "PostgreSQL session token is missing or invalid"}})
                return False
            return True

        def _authorize_shutdown(self) -> bool:
            if not self._is_local_request():
                self.send_json(403, {"error": {"code": "forbidden", "message": "Shutdown requires a local origin"}})
                return False
            if self.headers.get("X-Schemii-Token") != session_token:
                self.send_json(403, {"error": {"code": "invalid_session", "message": "Shutdown session token is missing or invalid"}})
                return False
            return True

        def _read_json(self, maximum: int = MAX_BODY_SIZE):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Invalid content length") from exc
            if length <= 0 or length > maximum:
                raise ValueError("Request body is empty or too large")
            if self.headers.get_content_type() != "application/json":
                raise TypeError("Content-Type must be application/json")
            try:
                return json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid JSON") from exc

        def _body_or_error(self, maximum: int = MAX_BODY_SIZE):
            try:
                body = self._read_json(maximum)
            except TypeError as error:
                self.send_json(415, {"error": {"code": "invalid_content_type", "message": str(error)}})
                return None
            except ValueError as error:
                self.send_json(400, {"error": {"code": "invalid_request", "message": str(error)}})
                return None
            if not isinstance(body, dict):
                self.send_json(400, {"error": {"code": "invalid_request", "message": "Request body must be an object"}})
                return None
            return body

        def _service_call(self, callback, status: int = 200):
            try:
                self.send_json(status, callback())
            except PostgresServiceError as error:
                self.send_json(error.status, error.to_dict())

    return LocalAppHandler
