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
        if os.name == "nt":
            self.skipTest("POSIX shell launcher is tested on POSIX runners")
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
            self.assertIn("com.docker.compose.project.working_dir", source)
            self.assertIn("com.docker.compose.volume", source)
            self.assertIn("com.docker.compose.network", source)
            self.assertIn("schemii-opencode-data", source)
            self.assertIn("schemii-postgres", source)
            self.assertIn("schemer-dashboards", source)
            self.assertNotIn('"schemer:local"', source)
            self.assertNotIn("system prune", source)
            self.assertNotIn("volume prune", source)
        self.assertIn('! -f "$repo_dir/compose.yaml"', shell)
        self.assertIn("not a recognized Schemii repository", powershell)

    def test_shell_launch_scripts_support_bash_3_2(self):
        bash_4_only = re.compile(r"\b(?:mapfile|readarray)\b|\b(?:declare|local)\s+-A\b")
        for name in ("start.sh", "uninstall.sh"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, bash_4_only, name)

    @unittest.skipIf(os.name == "nt", "POSIX shell uninstaller is tested on POSIX runners")
    def test_shell_uninstaller_removes_only_label_verified_owned_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "schemii copy"
            (repository / "src/schemii").mkdir(parents=True)
            shutil.copy2(ROOT / "uninstall.sh", repository / "uninstall.sh")
            (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (repository / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            binary = root / "bin"
            binary.mkdir()
            log = root / "docker.log"
            credentials = root / "credential data"
            (credentials / "owned-app").mkdir(parents=True)
            (credentials / "owned-app/instance").write_text("owned-app\n", encoding="utf-8")
            docker = binary / "docker"
            docker.write_text('''#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
for argument do last=$argument; done
case "$*" in
  info) exit 0 ;;
  "ps -aq") printf 'owned-app-container\nowned-schemer-container\nforeign-schemer-container\nspoof-container\n' ;;
  "ps -aq --filter ancestor="*) exit 0 ;;
  "volume ls -q") printf '%s\n' \
    owned-app_schemii-config owned-app_schemii-schemas owned-app_schemii-postgres \
    owned-schemer_schemer-dashboards owned-schemer_schemii-config \
    orphaned_schemii-config orphaned_schemii-schemas \
    collision_schemii-config foreign_schemer-dashboards ;;
  inspect*"{{.Config.Image}}"*owned-app-container)
    printf 'owned-app|%s|sha256:owned-app|schemii:owned-app\n' "$REPOSITORY" ;;
  inspect*"{{.Config.Image}}"*owned-schemer-container)
    printf 'owned-schemer|%s|sha256:shared-schemer|schemer:local\n' "$REPOSITORY" ;;
  inspect*"{{.Config.Image}}"*foreign-schemer-container)
    printf 'foreign|/tmp/unrelated|sha256:foreign|schemer:local\n' ;;
  inspect*"{{.Config.Image}}"*spoof-container)
    printf 'owned-app|/tmp/unrelated|sha256:spoof|schemii:owned-app\n' ;;
  "inspect --format "*owned-app-container)
    printf 'owned-app|schemii|%s\n' "$REPOSITORY" ;;
  "inspect --format "*owned-schemer-container)
    printf 'owned-schemer|schemer|%s\n' "$REPOSITORY" ;;
  "inspect --format "*foreign-schemer-container)
    printf 'foreign|schemer|/tmp/unrelated\n' ;;
  "inspect --format "*spoof-container)
    printf 'owned-app|schemer|/tmp/unrelated\n' ;;
  "volume inspect --format "*owned-app_schemii-postgres)
    case "$*" in
      *"{{.Name}}"*) printf 'someone-else|schemii-postgres|owned-app_schemii-postgres\n' ;;
      *) printf 'someone-else|schemii-postgres\n' ;;
    esac ;;
  "volume inspect --format "*"{{.Name}}"*)
    project=${last%%_*}; logical=${last#*_}
    printf '%s|%s|%s\n' "$project" "$logical" "$last" ;;
  "volume inspect --format "*)
    project=${last%%_*}; logical=${last#*_}
    printf '%s|%s\n' "$project" "$logical" ;;
  "network ls -q --filter label=com.docker.compose.project=owned-app") printf 'owned-network\nspoof-network\n' ;;
  "network ls -q --filter label=com.docker.compose.project="*) exit 0 ;;
  "network inspect --format "*owned-network) printf 'owned-app|default|owned-app_default\n' ;;
  "network inspect --format "*spoof-network) printf 'someone-else|default|owned-app_default\n' ;;
  "image inspect --format "*"schemii:owned-app") printf 'sha256:owned-app\n' ;;
  *) exit 0 ;;
esac
''', encoding="utf-8")
            docker.chmod(0o755)

            result = subprocess.run(
                ["/bin/bash", str(repository / "uninstall.sh"), "--yes"],
                cwd=repository,
                env={
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "DOCKER_LOG": str(log),
                    "REPOSITORY": str(repository),
                    "SCHEMII_CREDENTIAL_ROOT": str(credentials),
                },
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(repository.exists())
            self.assertFalse((credentials / "owned-app").exists())
            calls = log.read_text(encoding="utf-8")
            self.assertIn("rm -f owned-app-container", calls)
            self.assertIn("rm -f owned-schemer-container", calls)
            self.assertNotIn("rm -f foreign-schemer-container", calls)
            self.assertNotIn("rm -f spoof-container", calls)
            self.assertIn("network rm owned-network", calls)
            self.assertNotIn("network rm spoof-network", calls)
            self.assertIn("volume rm owned-app_schemii-config", calls)
            self.assertIn("volume rm owned-schemer_schemer-dashboards", calls)
            self.assertIn("volume rm orphaned_schemii-config", calls)
            self.assertIn("volume rm orphaned_schemii-schemas", calls)
            self.assertNotIn("volume rm owned-app_schemii-postgres", calls)
            self.assertNotIn("volume rm collision_schemii-config", calls)
            self.assertNotIn("volume rm foreign_schemer-dashboards", calls)
            self.assertIn("image rm schemii:owned-app", calls)
            self.assertNotIn("image rm schemer:local", calls)

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
        session_client = (ROOT / "src/schemii/shared_web/session-client.js").read_text(encoding="utf-8")

        resource_urls = re.findall(r'''(?:src|href)=["']([^"']+)["']''', html)
        self.assertTrue(resource_urls)
        self.assertTrue(all("://" not in url and not url.startswith("//") for url in resource_urls))

        css_urls = re.findall(r'''url\(["']?([^"')]+)''', css)
        self.assertTrue(all(url.startswith("data:") or "://" not in url for url in css_urls))
        self.assertNotRegex(javascript, r"\b(?:WebSocket|EventSource|sendBeacon|importScripts)\s*\(")

        literal_fetches = re.findall(r'''fetch\(\s*(["'`])([^"'`]+)\1''', javascript)
        self.assertTrue(all(target.startswith("/api/") for _, target in literal_fetches))
        self.assertIn('sessionPath = "/api/session"', session_client)
        self.assertIn("validatePath(path, allowPath)", session_client)
        self.assertIn("await fetch(path,", session_client)
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
        self.assertIn('credential_dir="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"', launcher)
        self.assertNotIn("SCHEMII_OPENCODE_PASSWORD=", launcher)

    def test_metadata_and_opencode_credentials_are_file_mounted(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        ai_compose = (ROOT / "compose.ai.yaml").read_text(encoding="utf-8")
        launcher = (ROOT / "start.sh").read_text(encoding="utf-8")
        for source in (compose, ai_compose):
            self.assertNotIn("PGPASSWORD:", source)
            self.assertNotIn("metadata-runtime-local", source)
        self.assertIn("POSTGRES_PASSWORD_FILE", compose)
        self.assertIn("SCHEMII_METADATA_PASSWORD_FILE", compose)
        self.assertIn("OPENCODE_SERVER_PASSWORD_FILE", ai_compose)
        self.assertIn("chmod 700", launcher)
        self.assertIn("chmod 600", launcher)
        self.assertIn("Existing metadata volume", launcher)
        self.assertNotIn("volume rm", launcher)
        self.assertIn("DAC_OVERRIDE", compose)
        runtime_entrypoint = (ROOT / "docker/runtime-secret-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("--bounding-set=-all", runtime_entrypoint)

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

        self.assertIn('requested="${1:-ai-docker-db}"', shell)
        self.assertIn('[string]$Mode = "ai-docker-db"', powershell)
        for source in (shell, powershell):
            self.assertIn("SCHEMII_INSTANCE", source)
            self.assertIn("--project-name", source)
            self.assertIn("SCHEMII_HOST_PORT", source)
            self.assertIn("SCHEMII_METADATA_HOST_PORT", source)
            self.assertIn("Legacy Schemii data volumes were found", source)
        self.assertIn("service_completed_successfully", postgres_compose)
        self.assertIn("/seed/001_bookstore.sql:ro", postgres_compose)
        self.assertIn("SCHEMII_EXAMPLES: all", postgres_compose)

    def test_metadata_postgres_is_dedicated_migrated_and_role_scoped(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        local = (ROOT / "compose.local-db.yaml").read_text(encoding="utf-8")
        schemer = (ROOT / "compose.schemer.yaml").read_text(encoding="utf-8")
        roles = (ROOT / "docker/metadata/001_roles.sh").read_text(encoding="utf-8")
        rotation = (ROOT / "docker/metadata/002_rotation_function.sql").read_text(encoding="utf-8")
        package = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("schemii-metadata-postgres:/var/lib/postgresql/data", compose)
        self.assertIn('["python", "-m", "schemii.metadata_migrate"]', compose)
        self.assertIn("service_completed_successfully", compose)
        self.assertNotRegex(compose, r'(?m)^    ports:.*\n(?:.*\n){0,3}.*metadata-postgres')
        self.assertIn('127.0.0.1:${SCHEMII_METADATA_HOST_PORT:-5433}:5432', local)
        self.assertIn("host=127.0.0.1 port=${SCHEMII_METADATA_HOST_PORT:-5433}", local)
        self.assertIn("schemii_metadata_schemii", compose)
        self.assertIn("schemii_metadata_schemer", schemer)
        self.assertIn("schemii_metadata_owner NOLOGIN", roles)
        self.assertNotIn("CREATEROLE", roles + rotation)
        self.assertNotIn("ADMIN OPTION", roles + rotation)
        self.assertIn("SECURITY DEFINER", rotation)
        self.assertIn("SET search_path = pg_catalog", rotation)
        self.assertIn("OWNER TO schemii_metadata_bootstrap", rotation)
        self.assertIn("REVOKE ALL ON FUNCTION", rotation)
        self.assertIn("TO schemii_metadata_migration", rotation)
        self.assertIn("^[A-Za-z0-9_-]+$", rotation)
        self.assertIn("octet_length", rotation)
        self.assertIn("ALTER ROLE schemii_metadata_bootstrap NOLOGIN", rotation)
        self.assertEqual(rotation.count("EXECUTE format('ALTER ROLE schemii_metadata_"), 3)
        self.assertNotIn("ALTER ROLE schemii_metadata_", (ROOT / "start.sh").read_text(encoding="utf-8"))
        self.assertNotIn("ALTER ROLE schemii_metadata_", (ROOT / "start.ps1").read_text(encoding="utf-8"))
        self.assertIn("options='-c role=schemii_metadata_owner'", compose)
        self.assertIn("ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner", roles)
        self.assertIn("schemii_admin.rotate_metadata_passwords", rotation)
        self.assertIn("002_rotation_function.sql:/docker-entrypoint-initdb.d/002_rotation_function.sql:ro", compose)
        self.assertEqual(compose.count("002_rotation_function.sql:/docker-entrypoint-initdb.d/002_rotation_function.sql:ro"), 1)
        self.assertNotIn("postgresql://schemii_metadata_", compose + schemer + local)
        self.assertIn("metadata/migrations/*.sql", package)

    def test_credential_lifecycle_is_marker_bound_and_recoverable(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")
        for source in (shell, powershell):
            self.assertIn(".credential-transaction", source)
            self.assertIn("Backup instance marker", source)
            self.assertIn("16-256 characters from [A-Za-z0-9_-]", source)
        self.assertIn("rollback_credential_transaction", shell)
        self.assertIn("Undo-CredentialTransaction", powershell)
        self.assertIn("wait_for_metadata", shell)
        self.assertIn("Wait-MetadataReady", powershell)
        self.assertIn('cp "$temporary" "$path"', shell)
        self.assertIn("WriteAllBytes($Target", powershell)
        self.assertIn('write_secret "$temporary_dir/metadata_bootstrap_password"', shell)
        self.assertIn('$newValues["metadata_bootstrap_password"] = Read-CredentialValue', powershell)

    def test_credential_lifecycle_is_cross_process_locked_and_waits_before_forward_update(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn('credential_lock="${credential_dir}.lock"', shell)
        self.assertIn('mkdir "$credential_lock"', shell)
        self.assertIn('kill -0 "$lock_pid"', shell)
        self.assertIn("Timed out waiting for another launcher credential operation", shell)
        self.assertLess(shell.index("release_credential_lock\nif [[ \"$project\""), shell.index('"${compose[@]}" up'))
        self.assertRegex(shell, re.compile(r'run_credential_transaction\(\).*?wait_for_metadata .*?update_metadata_passwords', re.S))

        self.assertIn("[System.IO.FileShare]::None", powershell)
        self.assertIn("Exit-CredentialLock", powershell)
        self.assertLess(powershell.index("finally {\n    Exit-CredentialLock\n}\nif ($project"), powershell.index("& docker @upArgs"))
        self.assertRegex(powershell, re.compile(r'function Complete-CredentialTransaction.*?Wait-MetadataReady .*?Invoke-MetadataPasswordUpdate', re.S))

    @unittest.skipIf(os.name == "nt", "POSIX stale lock recovery is tested on POSIX runners")
    def test_shell_credential_lock_recovers_after_owner_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text(
                '#!/bin/sh\ncase "$*" in info|"compose version") exit 0 ;; *) exit 1 ;; esac\n',
                encoding="utf-8",
            )
            docker.chmod(0o755)
            project = "schemii-lock-test"
            credentials = root / "credentials" / project
            credentials.mkdir(parents=True, mode=0o700)
            (credentials / "instance").write_text(f"{project}\n", encoding="utf-8")
            for name in (
                "metadata_bootstrap_password", "metadata_migration_password",
                "metadata_schemii_password", "metadata_schemer_password", "opencode_password",
            ):
                path = credentials / name
                path.write_text("a" * 32 + "\n", encoding="utf-8")
                path.chmod(0o600)
            lock = Path(f"{credentials}.lock")
            lock.mkdir(mode=0o700)
            (lock / "pid").write_text("99999999\n", encoding="utf-8")
            (lock / "token").write_text("crashed-owner\n", encoding="utf-8")
            backup = root / "backup"

            result = subprocess.run(
                ["/bin/bash", str(ROOT / "start.sh"), "credentials-backup", str(backup)],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "SCHEMII_INSTANCE": project,
                    "SCHEMII_CREDENTIAL_DIR": str(credentials),
                },
                capture_output=True,
                text=True,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(lock.exists())
            self.assertTrue((backup / project / "metadata_migration_password").is_file())

    def test_windows_credential_acls_are_recursive_verified_and_fail_closed(self):
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn("WindowsIdentity]::GetCurrent().User", powershell)
        self.assertIn("SetAccessRuleProtection($true, $false)", powershell)
        self.assertIn("Set-Acl -LiteralPath", powershell)
        self.assertIn("Get-Acl -LiteralPath", powershell)
        self.assertIn("Credential ACL verification failed closed", powershell)
        self.assertIn("Protect-CredentialTree $credentialDirectory", powershell)
        self.assertIn("Protect-CredentialTree $backupDirectory", powershell)
        self.assertIn("if ($runningOnWindows) { Protect-CredentialTree $sourceDirectory }", powershell)
        self.assertIn("Protect-CredentialPath $staging $true", powershell)
        self.assertNotIn("icacls.exe", powershell)

    def test_container_secret_consumers_enforce_one_credential_format(self):
        paths = (
            ROOT / "docker/metadata/001_roles.sh",
            ROOT / "docker/metadata/secret-entrypoint.sh",
            ROOT / "docker/runtime-secret-entrypoint.sh",
            ROOT / "ai/secret-entrypoint.sh",
        )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertIn("[!A-Za-z0-9_-]", source)
            self.assertIn('"${#', source)

    def test_compose_allows_a_clean_browser_shutdown_to_remain_stopped(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        schemii_service = compose.split("  schemii:\n", 1)[1].split("\nvolumes:", 1)[0]
        self.assertIn("restart: on-failure", schemii_service)
        self.assertNotIn("restart: unless-stopped", schemii_service)

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
        self.assertIn("RandomNumberGenerator]::Fill", powershell)
        self.assertIn("[Convert]::ToHexString", powershell)

    def test_ai_navigation_tools_accept_only_logical_ids_and_public_labels(self):
        tools = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "ai" / "workspace" / ".opencode" / "tools").glob("schema_*_open.ts")))
        instructions = (ROOT / "ai" / "workspace" / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("schemaId", tools)
        self.assertNotRegex(tools, r"\b(?:password|path|url|host|shell|command)\b")
        project_create = (ROOT / "ai" / "workspace" / ".opencode" / "tools" / "schema_project_create.ts").read_text(encoding="utf-8")
        self.assertNotRegex(project_create, r"\b(?:password|path|url|host|shell|command|schemaId)\b")

    def test_ai_schema_mutation_tools_return_fixed_acknowledgements(self):
        tool_dir = ROOT / "ai" / "workspace" / ".opencode" / "tools"
        for name in ("schema_populate.ts", "schema_add_table.ts", "schema_add_relationship.ts"):
            source = (tool_dir / name).read_text(encoding="utf-8")
            self.assertIn('return "Proposal arguments received."', source)
            self.assertNotIn("SCHEMII_ACTION:", source)
            self.assertNotRegex(source, r"\b(?:password|path|url|host|shell|command)\b")


if __name__ == "__main__":
    unittest.main()
