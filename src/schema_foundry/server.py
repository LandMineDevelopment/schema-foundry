from __future__ import annotations

import json
import os
import re
import secrets
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .postgres_service import PostgresService, PostgresServiceError
from .schema_store import SchemaStore, SchemaStoreError


MAX_BODY_SIZE = 5 * 1024 * 1024
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
PROFILE_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})(?:/(namespaces|fingerprint|test|introspect|preview))?$")
APPLY_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/plans/([A-Za-z0-9_-]+)/apply$")
DATA_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/data$")
SQL_PATH = re.compile(r"^/api/postgres/profiles/([A-Za-z0-9][A-Za-z0-9_-]{0,63})/sql$")


def _paths() -> tuple[Path, Path, Path]:
    web_dir = Path(__file__).resolve().parent / "web"
    config_dir = Path(os.environ.get("SCHEMA_FOUNDRY_CONFIG_DIR", "~/.config/schema-foundry")).expanduser().resolve()
    schema_dir = Path(os.environ.get("SCHEMA_FOUNDRY_SCHEMA_DIR", "~/.local/share/schema-foundry/schemas")).expanduser().resolve()
    return web_dir, config_dir, schema_dir


def _is_local_request(client_host: str, host_header: str, origin: str | None, behind_loopback_proxy: bool) -> bool:
    host = host_header.rsplit(":", 1)[0].strip("[]").lower()
    return (
        (behind_loopback_proxy or client_host in {"127.0.0.1", "::1"})
        and host in {"localhost", "127.0.0.1", "::1"}
        and (not origin or urlparse(origin).hostname in {"localhost", "127.0.0.1", "::1"})
    )


def make_handler(
    web_dir: Path,
    service: PostgresService,
    store: SchemaStore,
    session_token: str,
    *,
    behind_loopback_proxy: bool = False,
):
    class SchemaFoundryHandler(SimpleHTTPRequestHandler):
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

        def _is_local_request(self) -> bool:
            return _is_local_request(
                self.client_address[0],
                self.headers.get("Host", ""),
                self.headers.get("Origin"),
                behind_loopback_proxy,
            )

        def _authorize_postgres(self) -> bool:
            if not self._is_local_request():
                self.send_json(403, {"error": {"code": "forbidden", "message": "PostgreSQL API requires a local origin"}})
                return False
            if self.headers.get("X-Schema-Foundry-Token") != session_token:
                self.send_json(403, {"error": {"code": "invalid_session", "message": "PostgreSQL session token is missing or invalid"}})
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

        def _schema_call(self, callback):
            try:
                self.send_json(200, callback())
            except SchemaStoreError as error:
                self.send_json(error.status, error.payload)

        def _schema_id(self) -> str | None:
            path = unquote(urlparse(self.path).path)
            prefix = "/api/schemas/"
            return path[len(prefix):] if path.startswith(prefix) else None

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/session":
                if not self._is_local_request():
                    return self.send_json(403, {"error": {"code": "forbidden", "message": "Session requires a local origin"}})
                return self.send_json(200, {"token": session_token})
            if path == "/api/schemas":
                return self._schema_call(lambda: {"schemas": store.list()})
            if path == "/api/postgres/profiles":
                if self._authorize_postgres():
                    return self._service_call(lambda: {"profiles": service.list_profiles()})
                return
            if path == "/api/postgres/history":
                if not self._authorize_postgres():
                    return
                query = parse_qs(parsed.query)
                try:
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError:
                    return self.send_json(400, {"error": {"code": "validation_error", "message": "History limit must be an integer"}})
                return self._service_call(lambda: {"history": service.list_history(query.get("profileId", [None])[0], limit)})
            data_match = DATA_PATH.fullmatch(path)
            if data_match:
                if not self._authorize_postgres():
                    return
                query = parse_qs(parsed.query)
                try:
                    offset = int(query.get("offset", ["0"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                except ValueError:
                    return self.send_json(400, {"error": {"code": "validation_error", "message": "offset and limit must be integers"}})
                return self._service_call(lambda: service.preview_table_data(
                    data_match.group(1), query.get("namespace", [None])[0], query.get("table", [None])[0], offset, limit
                ))
            profile_match = PROFILE_PATH.fullmatch(path)
            if profile_match and profile_match.group(2) in {"namespaces", "fingerprint"}:
                if not self._authorize_postgres():
                    return
                if profile_match.group(2) == "namespaces":
                    return self._service_call(lambda: {"namespaces": service.list_namespaces(profile_match.group(1))})
                namespace = parse_qs(parsed.query).get("namespace", [None])[0]
                return self._service_call(lambda: service.catalog_status(profile_match.group(1), namespace))
            if path == "/":
                self.path = "/index.html"
            return super().do_GET()

        def do_HEAD(self):
            if urlparse(self.path).path == "/":
                self.path = "/index.html"
            return super().do_HEAD()

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/postgres/profiles":
                if not self._authorize_postgres():
                    return
                body = self._body_or_error()
                if body is not None:
                    return self._service_call(lambda: service.save_profile(None, body), 201)
                return
            sql_match = SQL_PATH.fullmatch(path)
            apply_match = APPLY_PATH.fullmatch(path)
            profile_match = PROFILE_PATH.fullmatch(path)
            if not sql_match and not apply_match and not (profile_match and profile_match.group(2) in {"test", "introspect", "preview"}):
                return self.send_json(404, {"error": "Unknown API path"})
            if not self._authorize_postgres():
                return
            body = self._body_or_error(20 * 1024 * 1024 if profile_match and profile_match.group(2) == "preview" else MAX_BODY_SIZE)
            if body is None:
                return
            if sql_match:
                return self._service_call(lambda: service.execute_read_only_sql(sql_match.group(1), body.get("namespace"), body.get("sql")))
            if apply_match:
                return self._service_call(lambda: service.apply(apply_match.group(1), apply_match.group(2), body.get("confirmDestructive", False)))
            profile_id, action = profile_match.groups()
            if action == "test":
                return self._service_call(lambda: service.test_profile(profile_id))
            if action == "introspect":
                return self._service_call(lambda: service.introspect(profile_id, body.get("namespace")))
            return self._service_call(lambda: service.preview(
                profile_id, body.get("namespace"), body.get("schema"), body.get("allowDestructive", False)
            ))

        def do_PUT(self):
            path = urlparse(self.path).path
            profile_match = PROFILE_PATH.fullmatch(path)
            if profile_match and profile_match.group(2) is None:
                if not self._authorize_postgres():
                    return
                body = self._body_or_error()
                if body is not None:
                    return self._service_call(lambda: service.save_profile(profile_match.group(1), body))
                return
            schema_id = self._schema_id()
            if schema_id is None:
                return self.send_json(404, {"error": "Unknown schema path"})
            body = self._body_or_error()
            if body is not None:
                return self._schema_call(lambda: store.save(
                    schema_id,
                    body,
                    expected_layout_token=self.headers.get("X-Schema-Foundry-Layout-Token"),
                    layout_protocol=self.headers.get("X-Schema-Foundry-Layout-Protocol"),
                ))

        def do_DELETE(self):
            path = urlparse(self.path).path
            profile_match = PROFILE_PATH.fullmatch(path)
            if profile_match and profile_match.group(2) is None:
                if self._authorize_postgres():
                    return self._service_call(lambda: service.delete_profile(profile_match.group(1)))
                return
            schema_id = self._schema_id()
            if schema_id is None:
                return self.send_json(404, {"error": "Unknown schema path"})
            return self._schema_call(lambda: store.delete(schema_id))

    return SchemaFoundryHandler


def main() -> None:
    web_dir, config_dir, schema_dir = _paths()
    host = os.environ.get("SCHEMA_FOUNDRY_HOST", "127.0.0.1")
    proxy_setting = os.environ.get("SCHEMA_FOUNDRY_BEHIND_LOOPBACK_PROXY", "0")
    if proxy_setting not in {"0", "1"}:
        raise SystemExit("SCHEMA_FOUNDRY_BEHIND_LOOPBACK_PROXY must be 0 or 1")
    try:
        port = int(os.environ.get("SCHEMA_FOUNDRY_PORT", "8080"))
    except ValueError as exc:
        raise SystemExit("SCHEMA_FOUNDRY_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("SCHEMA_FOUNDRY_PORT must be from 1 to 65535")
    if not web_dir.is_dir():
        raise SystemExit(f"Static web directory does not exist: {web_dir}")
    service = PostgresService(config_dir)
    store = SchemaStore(schema_dir)
    handler = make_handler(
        web_dir,
        service,
        store,
        secrets.token_urlsafe(32),
        behind_loopback_proxy=proxy_setting == "1",
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Schema Foundry running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
