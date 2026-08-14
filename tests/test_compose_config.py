import os
import shutil
import subprocess
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
        result = subprocess.run(
            command,
            cwd=ROOT,
            env={**os.environ, "SCHEMII_OPENCODE_PASSWORD": "compose-config-test-only"},
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


if __name__ == "__main__":
    unittest.main()
