import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.opencode_service import CUSTOM_TOOLS, PROMPT_TOOLS, SAFE_SKILLS, TOOL_ACTION_TYPES


class AiPermissionContractTests(unittest.TestCase):
    def test_embedded_agent_is_default_deny_with_exact_tool_and_skill_allowlists(self):
        config = json.loads((ROOT / "ai/workspace/opencode.json").read_text())
        permission = config["permission"]
        tool_files = {path.stem for path in (ROOT / "ai/workspace/.opencode/tools").glob("schema_*.ts")}
        skill_dirs = {path.parent.name for path in (ROOT / "ai/workspace/.opencode/skills").glob("*/SKILL.md")}

        self.assertEqual(permission["*"], "deny")
        self.assertEqual(tool_files, CUSTOM_TOOLS)
        self.assertEqual(set(TOOL_ACTION_TYPES), CUSTOM_TOOLS)
        self.assertEqual({name for name in CUSTOM_TOOLS if permission.get(name) == "allow"}, CUSTOM_TOOLS)
        self.assertEqual(skill_dirs, SAFE_SKILLS)
        self.assertEqual(permission["skill"]["*"], "deny")
        self.assertEqual({name for name, value in permission["skill"].items() if value == "allow"}, SAFE_SKILLS)
        for denied in ("bash", "shell", "read", "edit", "write", "apply_patch", "glob", "grep", "list", "webfetch", "websearch", "task", "mcp"):
            self.assertEqual(permission[denied], "deny")
            self.assertFalse(PROMPT_TOOLS.get(denied, False))
        self.assertEqual(config["share"], "disabled")
        self.assertFalse(config["snapshot"])
        self.assertFalse(config["formatter"])
        self.assertFalse(config["lsp"])
        self.assertEqual(config["mcp"], {})

    def test_tool_outputs_match_backend_action_registry_and_confirmation_contract(self):
        for tool_name, action_type in TOOL_ACTION_TYPES.items():
            source = (ROOT / f"ai/workspace/.opencode/tools/{tool_name}.ts").read_text()
            field = "action" if tool_name == "schema_read_query" else "type"
            self.assertRegex(source, rf'{field}:\s*"{re.escape(action_type)}"')
            self.assertNotRegex(source, re.compile(r"^\s*password\s*:", re.MULTILINE | re.IGNORECASE))
            self.assertNotRegex(source.lower(), r'filesystem|shell|webfetch')
            if tool_name == "schema_read_query":
                self.assertIn("readOnly: true", source)
                self.assertIn("requiresApproval: true", source)
            elif tool_name == "schema_migration_preview":
                self.assertIn("readOnly: true", source)
            else:
                self.assertIn("requiresConfirmation: true", source)

    def test_compose_keeps_workspace_read_only_and_opencode_private_by_default(self):
        ai_compose = (ROOT / "compose.ai.yaml").read_text()
        local_override = (ROOT / "compose.ai.local-db.yaml").read_text()
        self.assertIn("./ai/workspace:/workspace:ro", ai_compose)
        self.assertNotRegex(ai_compose, r'ports:\s*\n\s*-\s*["\']?[^\n]*4096')
        self.assertIn('"127.0.0.1:4096:4096"', local_override)
        self.assertIn("OPENCODE_DISABLE_EXTERNAL_SKILLS: 1", ai_compose)
        self.assertIn("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS: 1", ai_compose)


if __name__ == "__main__":
    unittest.main()
