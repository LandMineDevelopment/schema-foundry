import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def markdown_files(self):
        return [
            path for path in ROOT.rglob("*.md")
            if ".git" not in path.parts and "node_modules" not in path.parts
        ]

    def test_all_local_markdown_links_resolve(self):
        failures = []
        for path in self.markdown_files():
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]*\]\(([^)]+)\)", text):
                target = target.strip().split("#", 1)[0]
                if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                    continue
                destination = (path.parent / target).resolve()
                if not destination.exists():
                    failures.append(f"{path.relative_to(ROOT)} -> {target}")
        self.assertEqual(failures, [])

    def test_setup_docs_use_launcher_first_current_defaults(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        setup = (ROOT / "docs/AI_AGENT_SETUP.md").read_text(encoding="utf-8")
        assistant = (ROOT / "docs/AI_ASSISTANT.md").read_text(encoding="utf-8")

        self.assertIn("Docker is the only software required", readme)
        self.assertIn("### Without Git", readme)
        self.assertIn("bash ./start.sh", readme)
        self.assertIn("first start downloads", readme)
        self.assertIn("default `ai-docker-db` mode", setup)
        self.assertIn("Do not assume port 8080", setup)
        self.assertIn("no model request is made until the user sends", assistant)
        for stale in (
            "The default trial starts only Schemii",
            "not started by default",
            "base Compose setup also maps",
            "Run docker compose logs schemii",
        ):
            self.assertNotIn(stale, "\n".join((readme, setup, assistant)))

    def test_agent_guides_match_networking_and_verification_contracts(self):
        guide = (ROOT / "agent_guide.md").read_text(encoding="utf-8")
        connection = (ROOT / "ai/workspace/.opencode/skills/connection-setup/SKILL.md").read_text(encoding="utf-8")
        help_skill = (ROOT / "ai/workspace/.opencode/skills/schemii-help/SKILL.md").read_text(encoding="utf-8")
        layout = (ROOT / ".opencode/skills/preserve-schemii-layout/SKILL.md").read_text(encoding="utf-8")

        self.assertIn('for test_file in tests/test_*.js; do node "$test_file" || exit 1; done', guide)
        self.assertIn("Base Compose does not add that mapping on Linux", connection)
        self.assertIn("no-argument launcher uses `ai-docker-db`", help_skill)
        self.assertIn("For a local-only design", layout)
        self.assertIn("Skip migration preview for a confirmed local-only design", layout)


if __name__ == "__main__":
    unittest.main()
