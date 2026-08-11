from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


def parse_proxy_setting(value: str, variable: str) -> bool:
    if value not in {"0", "1"}:
        raise SystemExit(f"{variable} must be 0 or 1")
    return value == "1"


def parse_port(value: str, variable: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise SystemExit(f"{variable} must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"{variable} must be from 1 to 65535")
    return port


def validate_static_directory(path: Path) -> None:
    if not path.is_dir():
        raise SystemExit(f"Static web directory does not exist: {path}")


def run_server(
    host: str,
    port: int,
    handler: type,
    application_name: str,
    *,
    server_factory: Callable[..., Any] = ThreadingHTTPServer,
) -> None:
    server = server_factory((host, port), handler)
    print(f"{application_name} running at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def begin_http_shutdown(handler: Any, thread_name: str) -> None:
    shutdown_thread = threading.Thread(target=handler.server.shutdown, name=thread_name, daemon=True)
    handler.send_json(202, {"shuttingDown": True})
    handler.wfile.flush()
    shutdown_thread.start()
