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

    def test_launchers_do_not_open_duplicate_browser_tabs(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn('curl --fail --silent --max-time 1 "$url"', shell)
        self.assertIn('"$was_ready" != "1"', shell)
        self.assertIn("SCHEMII_NO_OPEN", shell)
        self.assertIn("Invoke-WebRequest -Uri $url -TimeoutSec 1", powershell)
        self.assertIn("-not $NoOpen -and -not $wasReady", powershell)

    def test_compose_allows_a_clean_browser_shutdown_to_remain_stopped(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("restart: on-failure", compose)
        self.assertNotIn("restart: unless-stopped", compose)

    def test_container_runtime_is_cross_platform_and_self_checking(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn("healthcheck:", compose)
        self.assertIn("urllib.request.urlopen", compose)
        self.assertNotIn("host-gateway", compose)
        self.assertIn(".State.Health.Status", shell)
        self.assertIn(".State.Health.Status", powershell)
        self.assertIn("docker run --rm python:3.12-slim", shell)
        self.assertIn("docker run --rm python:3.12-slim", powershell)
        self.assertNotIn("od -An", shell)
        self.assertNotIn("RandomNumberGenerator]::Fill", powershell)
        self.assertNotIn("[Convert]::ToHexString", powershell)

    def test_ai_navigation_tools_accept_only_logical_ids_and_public_labels(self):
        tools = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "ai" / "workspace" / ".opencode" / "tools").glob("schema_*_open.ts")))
        create_tool = (ROOT / "ai" / "workspace" / ".opencode" / "tools" / "schema_project_create.ts").read_text(encoding="utf-8")
        instructions = (ROOT / "ai" / "workspace" / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("schemaId", tools)
        self.assertIn("profileId", tools)
        self.assertNotRegex(tools, r"\b(?:password|path|url|host|shell|command)\b")
        self.assertIn("needs no schemaId or availableProjects entry", create_tool)
        self.assertIn("immediately call `schema_project_create`", instructions)

    def test_ai_population_tool_requires_complete_tables_and_name_based_relationships(self):
        populate = (ROOT / "ai" / "workspace" / ".opencode" / "tools" / "schema_populate.ts").read_text(encoding="utf-8")
        add_table = (ROOT / "ai" / "workspace" / ".opencode" / "tools" / "schema_add_table.ts").read_text(encoding="utf-8")
        relationship = (ROOT / "ai" / "workspace" / ".opencode" / "tools" / "schema_add_relationship.ts").read_text(encoding="utf-8")

        self.assertIn('type: "populate_schema"', populate)
        self.assertIn("columns: tool.schema.array(column).min(1)", populate)
        self.assertIn("relationships: tool.schema.array(relationship)", populate)
        self.assertIn("columns: tool.schema.array(column).min(1)", add_table)
        for field in ("fromTableName", "fromColumnName", "toTableName", "toColumnName"):
            self.assertIn(field, relationship)


if __name__ == "__main__":
    unittest.main()
