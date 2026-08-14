import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ComposeConfigTests(unittest.TestCase):
    COMBINATIONS = (
        ("ui",),
        ("local-db", "compose.local-db.yaml"),
        ("docker-db", "compose.postgres.yaml"),
        ("ai", "compose.ai.yaml"),
        ("ai-local-db", "compose.local-db.yaml", "compose.ai.yaml", "compose.ai.local-db.yaml"),
        ("ai-docker-db", "compose.postgres.yaml", "compose.ai.yaml"),
        ("schemer", "compose.postgres.yaml", "compose.schemer.yaml"),
        ("schemer-ai", "compose.postgres.yaml", "compose.ai.yaml", "compose.schemer.yaml", "compose.schemer.ai.yaml"),
    )

    @classmethod
    def setUpClass(cls):
        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker Compose is unavailable")
        result = subprocess.run(
            ["docker", "compose", "version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("Docker Compose is unavailable")

    def compose_config(self, *overrides):
        command = ["docker", "compose", "-f", "compose.yaml"]
        for override in overrides:
            command.extend(("-f", override))
        command.extend(("config", "--format", "json"))
        with tempfile.TemporaryDirectory() as directory:
            for name in (
                "metadata_bootstrap_password", "metadata_migration_password",
                "metadata_schemii_password", "metadata_schemer_password", "opencode_password",
            ):
                (Path(directory) / name).write_text(f"compose-test-{name}\n", encoding="utf-8")
            result = subprocess.run(
                command,
                cwd=ROOT,
                env={**os.environ, "SCHEMII_CREDENTIAL_DIR": directory},
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_all_supported_compose_combinations_are_valid(self):
        for combination in self.COMBINATIONS:
            with self.subTest(mode=combination[0]):
                config = self.compose_config(*combination[1:])
                self.assertIn('"metadata-postgres"', config)
                self.assertIn('"metadata-migrate"', config)

    def test_metadata_database_is_private_except_for_host_network_modes(self):
        bridge_config = self.compose_config()
        host_config = self.compose_config("compose.local-db.yaml")

        self.assertNotIn('"published": "5433"', bridge_config)
        self.assertIn('"host_ip": "127.0.0.1"', host_config)
        self.assertIn('"published": "5433"', host_config)

    def test_rendered_config_contains_secret_files_not_secret_values(self):
        config = self.compose_config("compose.ai.yaml", "compose.schemer.yaml", "compose.schemer.ai.yaml")
        self.assertNotIn("compose-test-", config)
        self.assertNotIn("PGPASSWORD", config)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD:", config)
        self.assertIn("SCHEMII_METADATA_PASSWORD_FILE", config)
        self.assertIn("/run/secrets/opencode_password", config)


if __name__ == "__main__":
    unittest.main()
