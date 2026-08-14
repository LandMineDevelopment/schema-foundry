import hashlib
import os
import secrets
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("SCHEMII_RUN_DOCKER_INTEGRATION") == "1", "Docker credential integration is opt-in")
class MetadataCredentialIntegrationTests(unittest.TestCase):
    def run_command(self, command, env, check=True):
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True,
            timeout=300,
        )
        if check and result.returncode:
            self.fail(f"{command!r} failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def test_catalog_privileges_and_live_rotate_restore(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker is unavailable")
        project = f"schemii-credential-test-{secrets.token_hex(4)}"
        with tempfile.TemporaryDirectory() as credential_dir, tempfile.TemporaryDirectory() as backup_dir:
            credential_path = Path(credential_dir)
            credential_path.chmod(0o700)
            (credential_path / "instance").write_text(f"{project}\n", encoding="utf-8")
            for name in (
                "metadata_bootstrap_password", "metadata_migration_password",
                "metadata_schemii_password", "metadata_schemer_password", "opencode_password",
            ):
                path = credential_path / name
                path.write_text(f"{secrets.token_hex(32)}\n", encoding="utf-8")
                path.chmod(0o600)
            env = {
                **os.environ,
                "SCHEMII_CREDENTIAL_DIR": credential_dir,
                "SCHEMII_INSTANCE": project,
                "SCHEMII_NO_OPEN": "1",
            }
            compose = ["docker", "compose", "--project-name", project, "-f", "compose.yaml"]
            try:
                self.run_command(compose + ["up", "--build", "-d", "--wait", "metadata-postgres"], env)
                container = self.run_command(compose + ["ps", "-q", "metadata-postgres"], env).stdout.strip()
                psql = [
                    "docker", "exec", "-u", "postgres", "-e",
                    "PGPASSFILE=/tmp/schemii-metadata-secrets/metadata_migration_password.pgpass",
                    container, "psql", "--quiet", "--tuples-only", "--no-align",
                    "--host", "127.0.0.1", "--username", "schemii_metadata_migration",
                    "--dbname", "schemii_metadata", "--command",
                ]
                catalog_sql = """
SELECT rolname || ':' || rolcreaterole || ':' || rolcanlogin
FROM pg_roles WHERE rolname IN ('schemii_metadata_bootstrap', 'schemii_metadata_migration') ORDER BY rolname;
SELECT count(*) FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member
WHERE member.rolname='schemii_metadata_migration' AND m.admin_option;
SELECT p.prosecdef || ':' || r.rolname || ':' || p.proconfig[1]
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
WHERE n.nspname='schemii_admin' AND p.proname='rotate_metadata_passwords';
SELECT EXISTS (SELECT 1 FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
               WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE') || ':' ||
       has_function_privilege('schemii_metadata_migration', p.oid, 'EXECUTE')
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='schemii_admin' AND p.proname='rotate_metadata_passwords';
"""
                catalog = self.run_command(psql + [catalog_sql], env).stdout.splitlines()
                self.assertEqual(catalog, [
                    "schemii_metadata_bootstrap:true:false",
                    "schemii_metadata_migration:false:true",
                    "0",
                    "true:schemii_metadata_bootstrap:search_path=pg_catalog",
                    "false:true",
                ])
                rejected = self.run_command(
                    psql + ["SELECT schemii_admin.rotate_metadata_passwords('short','short','short')"],
                    env, check=False,
                )
                self.assertNotEqual(rejected.returncode, 0)
                rejected_characters = self.run_command(
                    psql + ["SELECT schemii_admin.rotate_metadata_passwords('invalid-password!','invalid-password!','invalid-password!')"],
                    env, check=False,
                )
                self.assertNotEqual(rejected_characters.returncode, 0)

                migration_file = credential_path / "metadata_migration_password"
                bootstrap_file = credential_path / "metadata_bootstrap_password"
                original_digest = hashlib.sha256(migration_file.read_bytes()).digest()
                bootstrap_digest = hashlib.sha256(bootstrap_file.read_bytes()).digest()
                self.run_command(["bash", "./start.sh", "credentials-backup", backup_dir], env)
                self.assertFalse(Path(f"{credential_dir}.lock").exists())

                wrapper_dir = Path(backup_dir) / "bin"
                wrapper_dir.mkdir()
                docker_wrapper = wrapper_dir / "docker"
                docker_wrapper.write_text(
                    "#!/bin/sh\n"
                    "if [ \"${1:-}\" = restart ] && [ ! -e \"$SCHEMII_TEST_RESTART_FAILED\" ]; then\n"
                    "  : > \"$SCHEMII_TEST_RESTART_FAILED\"\n"
                    "  exit 70\n"
                    "fi\n"
                    "exec \"$SCHEMII_TEST_REAL_DOCKER\" \"$@\"\n",
                    encoding="utf-8",
                )
                docker_wrapper.chmod(0o700)
                failure_env = {
                    **env,
                    "PATH": f"{wrapper_dir}{os.pathsep}{env['PATH']}",
                    "SCHEMII_TEST_REAL_DOCKER": shutil.which("docker"),
                    "SCHEMII_TEST_RESTART_FAILED": str(Path(backup_dir) / "restart-failed"),
                }
                failed_rotation = self.run_command(["bash", "./start.sh", "credentials-rotate"], failure_env, check=False)
                self.assertNotEqual(failed_rotation.returncode, 0, failed_rotation.stdout + failed_rotation.stderr)
                self.assertEqual(hashlib.sha256(migration_file.read_bytes()).digest(), original_digest)
                self.assertFalse((credential_path / ".credential-transaction").exists())
                self.assertEqual(self.run_command(psql + ["SELECT current_user"], env).stdout.strip(), "schemii_metadata_migration")

                self.run_command(["bash", "./start.sh", "credentials-rotate"], env)
                self.assertFalse(Path(f"{credential_dir}.lock").exists())
                self.assertNotEqual(hashlib.sha256(migration_file.read_bytes()).digest(), original_digest)
                self.assertEqual(hashlib.sha256(bootstrap_file.read_bytes()).digest(), bootstrap_digest)
                self.assertEqual(
                    self.run_command(psql + ["SELECT current_user"], env).stdout.strip(),
                    "schemii_metadata_migration",
                )
                backup_marker = Path(backup_dir) / project / "instance"
                backup_marker.write_text(f"{project}-other\n", encoding="utf-8")
                rejected_restore = self.run_command(["bash", "./start.sh", "credentials-restore", backup_dir], env, check=False)
                self.assertNotEqual(rejected_restore.returncode, 0)
                self.assertNotEqual(hashlib.sha256(migration_file.read_bytes()).digest(), original_digest)
                backup_marker.write_text(f"{project}\n", encoding="utf-8")
                self.run_command(["bash", "./start.sh", "credentials-restore", backup_dir], env)
                self.assertFalse(Path(f"{credential_dir}.lock").exists())
                self.assertEqual(hashlib.sha256(migration_file.read_bytes()).digest(), original_digest)
                authenticated = self.run_command(psql + ["SELECT current_user"], env).stdout.strip()
                self.assertEqual(authenticated, "schemii_metadata_migration")
            finally:
                self.run_command(compose + ["down", "--volumes", "--remove-orphans"], env, check=False)


if __name__ == "__main__":
    unittest.main()
