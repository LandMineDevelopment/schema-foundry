import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "schemii"
WEB = SRC / "web"
sys.path.insert(0, str(ROOT / "src"))

from schemii.server import _paths


class StandaloneRuntimeTests(unittest.TestCase):
    def run_shell_launcher(self, mode="ui", docker_script=None, system_tools=False):
        with tempfile.TemporaryDirectory() as directory:
            if docker_script is not None:
                docker = Path(directory) / "docker"
                docker.write_text("#!/bin/sh\n" + docker_script, encoding="utf-8")
                docker.chmod(0o755)
            return subprocess.run(
                ["/bin/bash", str(ROOT / "start.sh"), mode],
                cwd=ROOT,
                env={**os.environ, "PATH": directory + (":/usr/bin:/bin" if system_tools else "")},
                capture_output=True,
                text=True,
                timeout=10,
            )

    def test_launcher_help_does_not_require_docker(self):
        result = self.run_shell_launcher("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Complete UI, tutorial PostgreSQL, and AI stack", result.stdout)
        self.assertIn("#install-docker", result.stdout)

    def test_powershell_launcher_help_when_powershell_is_available(self):
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            self.skipTest("PowerShell is not installed")
        result = subprocess.run(
            [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "start.ps1"), "-Help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Complete UI, tutorial PostgreSQL, and AI stack", result.stdout)
        self.assertIn("#install-docker", result.stdout)

    def test_launcher_prerequisite_errors_link_to_install_help(self):
        missing = self.run_shell_launcher()
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Docker was not found", missing.stderr)
        self.assertIn("#install-docker", missing.stderr)

        unavailable = self.run_shell_launcher(docker_script='[ "$1" = "info" ] && exit 1\nexit 0\n')
        self.assertNotEqual(unavailable.returncode, 0)
        self.assertIn("daemon is unavailable or your user lacks permission", unavailable.stderr)
        self.assertIn("docker info", unavailable.stderr)

        no_compose = self.run_shell_launcher(docker_script='[ "$1" = "info" ] && exit 0\n[ "$1 $2 $3" = "compose version " ] && exit 1\nexit 1\n')
        self.assertNotEqual(no_compose.returncode, 0)
        self.assertIn("Docker Compose was not found", no_compose.stderr)
        self.assertIn("docs.docker.com/compose/install", no_compose.stderr)

    def test_launcher_stops_for_ambiguous_legacy_volumes(self):
        docker_script = '''
case "$*" in
  info|"compose version"|"volume inspect schemii_schemii-config"|"volume inspect schemii_schemii-schemas") exit 0 ;;
  ps*) exit 0 ;;
  *) exit 1 ;;
esac
'''
        result = self.run_shell_launcher(docker_script=docker_script, system_tools=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Legacy Schemii data volumes were found", result.stderr)
        self.assertIn("SCHEMII_INSTANCE=schemii", result.stderr)
        self.assertIn("SCHEMII_INSTANCE=schemii-dev", result.stderr)

    def test_readme_has_beginner_docker_and_no_git_paths(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for link in (
            "desktop/setup/install/windows-install",
            "desktop/setup/install/mac-install",
            "engine/install/",
            "compose/install/linux/",
        ):
            self.assertIn(link, readme)
        self.assertIn("### Without Git", readme)
        self.assertIn("bash ./start.sh", readme)
        self.assertIn("first start downloads", readme)

    def test_uninstallers_are_scoped_confirmed_and_avoid_prune(self):
        shell = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        for source in (shell, powershell):
            self.assertIn("UNINSTALL", source)
            self.assertIn("com.docker.compose.project", source)
            self.assertIn("schemii-opencode-data", source)
            self.assertIn("schemii-postgres", source)
            self.assertNotIn("system prune", source)
            self.assertNotIn("volume prune", source)
        self.assertIn('! -f "$repo_dir/compose.yaml"', shell)
        self.assertIn("not a recognized Schemii repository", powershell)

    def test_shell_uninstaller_removes_only_discovered_resources_and_its_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "schemii-copy"
            (repository / "src/schemii").mkdir(parents=True)
            shutil.copy2(ROOT / "uninstall.sh", repository / "uninstall.sh")
            (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (repository / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            binary = root / "bin"
            binary.mkdir()
            log = root / "docker.log"
            docker = binary / "docker"
            docker.write_text('''#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$1 $2" in
  "info ") exit 0 ;;
  "ps -a") printf 'demo-one\n' ;;
  "volume ls") printf 'demo-one_schemii-config\ndemo-one_schemii-schemas\nother_data\n' ;;
  "ps -aq") printf 'container-one\ncontainer-two\n' ;;
  "network ls") printf 'network-one\n' ;;
  *) exit 0 ;;
esac
''', encoding="utf-8")
            docker.chmod(0o755)

            result = subprocess.run(
                ["/bin/bash", str(repository / "uninstall.sh"), "--yes"],
                cwd=repository,
                env={**os.environ, "PATH": f"{binary}:/usr/bin:/bin", "DOCKER_LOG": str(log)},
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(repository.exists())
            calls = log.read_text(encoding="utf-8")
            self.assertIn("rm -f container-one container-two", calls)
            self.assertIn("network rm network-one", calls)
            self.assertIn("volume rm demo-one_schemii-config", calls)
            self.assertIn("volume rm demo-one_schemii-schemas", calls)
            self.assertNotIn("other_data:/", calls)

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
        postgres_client = (ROOT / "src/schemii/shared_web/postgres-client.js").read_text(encoding="utf-8")

        resource_urls = re.findall(r'''(?:src|href)=["']([^"']+)["']''', html)
        self.assertTrue(resource_urls)
        self.assertTrue(all("://" not in url and not url.startswith("//") for url in resource_urls))

        css_urls = re.findall(r'''url\(["']?([^"')]+)''', css)
        self.assertTrue(all(url.startswith("data:") or "://" not in url for url in css_urls))
        self.assertNotRegex(javascript, r"\b(?:WebSocket|EventSource|sendBeacon|importScripts)\s*\(")

        literal_fetches = re.findall(r'''fetch\(\s*(["'`])([^"'`]+)\1''', javascript)
        self.assertTrue(literal_fetches)
        self.assertTrue(all(target.startswith("/api/") for _, target in literal_fetches))
        self.assertIn('path.startsWith("/api/postgres/")', postgres_client)

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

    def test_launchers_default_to_isolated_tutorial_instances(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")
        postgres_compose = (ROOT / "compose.postgres.yaml").read_text(encoding="utf-8")

        self.assertIn('mode="${1:-ai-docker-db}"', shell)
        self.assertIn('[string]$Mode = "ai-docker-db"', powershell)
        for source in (shell, powershell):
            self.assertIn("SCHEMII_INSTANCE", source)
            self.assertIn("--project-name", source)
            self.assertIn("SCHEMII_HOST_PORT", source)
            self.assertIn("Legacy Schemii data volumes were found", source)
        self.assertIn("service_completed_successfully", postgres_compose)
        self.assertIn("/seed/001_bookstore.sql:ro", postgres_compose)
        self.assertIn("SCHEMII_EXAMPLES: all", postgres_compose)

    def test_compose_allows_a_clean_browser_shutdown_to_remain_stopped(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("restart: on-failure", compose)
        self.assertNotIn("restart: unless-stopped", compose)

    def test_schemer_is_a_separate_service_with_shared_profiles(self):
        compose = (ROOT / "compose.schemer.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        package = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("target: schemer-runtime", compose)
        self.assertIn('127.0.0.1:${SCHEMER_HOST_PORT:-8081}:8081', compose)
        self.assertIn("schemii-config:/data/config", compose)
        self.assertIn("schemer-dashboards:/data/dashboards", compose)
        self.assertIn("SCHEMER_DASHBOARD_DIR: /data/dashboards", compose)
        self.assertIn("FROM runtime AS schemer-runtime", dockerfile)
        self.assertIn("/data/config /data/schemas /data/dashboards", dockerfile)
        self.assertIn('schemer = "schemii.schemer_server:main"', package)

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
        instructions = (ROOT / "ai" / "workspace" / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("schemaId", tools)
        self.assertNotRegex(tools, r"\b(?:password|path|url|host|shell|command)\b")
        self.assertFalse((ROOT / "ai" / "workspace" / ".opencode" / "tools" / "schema_project_create.ts").exists())

    def test_ai_schema_mutation_tools_are_fail_closed_until_server_adapters_exist(self):
        tool_dir = ROOT / "ai" / "workspace" / ".opencode" / "tools"
        for name in ("schema_populate.ts", "schema_add_table.ts", "schema_add_relationship.ts"):
            self.assertFalse((tool_dir / name).exists())


if __name__ == "__main__":
    unittest.main()
