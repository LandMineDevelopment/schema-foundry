import ast
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "schemii"
WEB = SRC / "web"
sys.path.insert(0, str(ROOT / "src"))

from schemii.server import _paths


class StandaloneRuntimeTests(unittest.TestCase):
    def test_backend_has_no_outbound_clients_or_process_execution(self):
        forbidden_modules = {
            "aiohttp", "ftplib", "httpx", "paramiko", "requests", "smtplib",
            "socket", "subprocess", "telnetlib", "urllib.request", "xmlrpc.client",
        }
        violations = []
        for path in SRC.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    if path.name == "opencode_service.py" and name == "urllib.request":
                        continue
                    if any(name == forbidden or name.startswith(f"{forbidden}.") for forbidden in forbidden_modules):
                        violations.append(f"{path.name}:{node.lineno}: {name}")
        self.assertEqual(violations, [], "Unexpected outbound/process imports: " + ", ".join(violations))

    def test_browser_assets_use_only_local_resources_and_api_calls(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        javascript = (WEB / "app.js").read_text(encoding="utf-8")

        resource_urls = re.findall(r'''(?:src|href)=["']([^"']+)["']''', html)
        self.assertTrue(resource_urls)
        self.assertTrue(all("://" not in url and not url.startswith("//") for url in resource_urls))

        css_urls = re.findall(r'''url\(["']?([^"')]+)''', css)
        self.assertTrue(all(url.startswith("data:") or "://" not in url for url in css_urls))
        self.assertNotRegex(javascript, r"\b(?:WebSocket|EventSource|sendBeacon|importScripts)\s*\(")

        literal_fetches = re.findall(r'''fetch\(\s*(["'`])([^"'`]+)\1''', javascript)
        self.assertTrue(literal_fetches)
        self.assertTrue(all(target.startswith("/api/") for _, target in literal_fetches))
        self.assertIn('!path.startsWith("/api/postgres/")', javascript)

    def test_storage_paths_are_absolute_and_independent_of_launch_directory(self):
        with patch.dict(
            "os.environ",
            {"SCHEMII_CONFIG_DIR": "relative-config", "SCHEMII_SCHEMA_DIR": "relative-schemas"},
        ):
            _, config_dir, schema_dir = _paths()
        self.assertTrue(config_dir.is_absolute())
        self.assertTrue(schema_dir.is_absolute())

    def test_ai_provider_credentials_use_a_stable_persistent_volume(self):
        compose = (ROOT / "compose.ai.yaml").read_text(encoding="utf-8")
        launcher = (ROOT / "start.sh").read_text(encoding="utf-8")

        self.assertIn("schemii-opencode-data:/opencode/data", compose)
        self.assertIn("schemii-opencode-data:", compose)
        self.assertIn("XDG_DATA_HOME: /opencode/data", compose)
        self.assertNotIn("~/.local/share/opencode", compose)
        self.assertNotIn("${HOME}", compose)
        self.assertNotIn("down --volumes", launcher)
        self.assertNotIn("volume rm", launcher)


if __name__ == "__main__":
    unittest.main()
