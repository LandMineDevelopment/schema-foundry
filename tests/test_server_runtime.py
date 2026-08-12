import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.server_runtime import begin_http_shutdown, parse_port, parse_proxy_setting, run_server, validate_static_directory


class ServerRuntimeTests(unittest.TestCase):
    def test_environment_parsers_are_strict(self):
        self.assertTrue(parse_proxy_setting("1", "PROXY"))
        self.assertFalse(parse_proxy_setting("0", "PROXY"))
        self.assertEqual(parse_port("8080", "PORT"), 8080)
        for value in ("yes", ""):
            with self.subTest(proxy=value), self.assertRaises(SystemExit):
                parse_proxy_setting(value, "PROXY")
        for value in ("nope", "0", "65536"):
            with self.subTest(port=value), self.assertRaises(SystemExit):
                parse_port(value, "PORT")

    def test_static_directory_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            validate_static_directory(Path(directory))
            with self.assertRaises(SystemExit):
                validate_static_directory(Path(directory) / "missing")

    def test_server_is_announced_and_closed_on_keyboard_interrupt(self):
        events = []

        class Server:
            def __init__(self, address, handler):
                events.append((address, handler))

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                events.append("closed")

        with patch("sys.stdout", new_callable=io.StringIO) as output:
            run_server("127.0.0.1", 8080, object, "Demo", server_factory=Server)
        self.assertIn("Demo running at http://127.0.0.1:8080/", output.getvalue())
        self.assertEqual(events[-1], "closed")

    def test_server_runs_shutdown_callback_before_close(self):
        events = []

        class Server:
            def __init__(self, address, handler):
                pass

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                events.append("closed")

        run_server(
            "127.0.0.1", 8080, object, "Demo", server_factory=Server,
            shutdown_callback=lambda: events.append("shutdown"),
        )
        self.assertEqual(events, ["shutdown", "closed"])

    def test_shutdown_response_is_flushed_before_thread_start(self):
        events = []

        class Writer:
            def flush(self):
                events.append("flush")

        class Server:
            def shutdown(self):
                events.append("shutdown")

        class Handler:
            server = Server()
            wfile = Writer()

            def send_json(self, status, payload):
                events.append((status, payload))

        begin_http_shutdown(Handler(), "test-shutdown")
        self.assertEqual(events[:2], [(202, {"shuttingDown": True}), "flush"])


if __name__ == "__main__":
    unittest.main()
