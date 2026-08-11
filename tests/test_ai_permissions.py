import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.opencode_service import CUSTOM_TOOLS, PROMPT_TOOLS, SAFE_SKILLS, TOOL_ACTION_TYPES
from schemii.schemer_ai import SCHEMER_AI_ACTION_PREFIX, SCHEMER_AI_SKILLS, SCHEMER_AI_TOOL_ACTION_TYPES


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
        self.assertIn('"127.0.0.1:${SCHEMII_OPENCODE_HOST_PORT:-4096}:4096"', local_override)
        self.assertNotIn('"0.0.0.0:', local_override)
        self.assertIn("OPENCODE_DISABLE_EXTERNAL_SKILLS: 1", ai_compose)
        self.assertIn("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS: 1", ai_compose)
        self.assertIn("condition: service_healthy", ai_compose)
        self.assertIn("http://127.0.0.1:4096/global/health", ai_compose)
        self.assertIn("Authorization: Basic $$credentials", ai_compose)

    def test_schemer_agent_has_separate_default_deny_workspace(self):
        root = ROOT / "ai/schemer-workspace"
        config = json.loads((root / "opencode.json").read_text())
        permission = config["permission"]
        tools = {path.stem for path in (root / ".opencode/tools").glob("schemer_*.ts")}
        skills = {path.parent.name for path in (root / ".opencode/skills").glob("*/SKILL.md")}
        self.assertEqual(tools, set(SCHEMER_AI_TOOL_ACTION_TYPES))
        self.assertEqual(skills, SCHEMER_AI_SKILLS)
        self.assertEqual({name for name in tools if permission.get(name) == "allow"}, tools)
        self.assertEqual({name for name, value in permission["skill"].items() if value == "allow"}, skills)
        self.assertEqual(permission["*"], "deny")
        for denied in ("bash", "shell", "read", "edit", "write", "apply_patch", "glob", "grep", "list", "webfetch", "websearch", "task", "mcp"):
            self.assertEqual(permission[denied], "deny")
        for tool_name, action_type in SCHEMER_AI_TOOL_ACTION_TYPES.items():
            source = (root / f".opencode/tools/{tool_name}.ts").read_text()
            self.assertIn(f'type: "{action_type}"', source)
            self.assertIn(SCHEMER_AI_ACTION_PREFIX, source)
            self.assertIn("requiresConfirmation: true", source)
            self.assertNotRegex(source.lower(), r"password|filesystem|shell")
            if tool_name == "schemer_read_query":
                self.assertIn("readOnly: true", source)
                self.assertIn("database:", source)
                self.assertIn("sql:", source)
            else:
                self.assertNotRegex(source.lower(), r"sql:")
            if tool_name == "schemer_widget_create":
                self.assertIn("source:", source)
                self.assertIn("query:", source)
                self.assertIn("visualizationMode", source)
                self.assertIn("must be supplied together", source)
                self.assertIn('aggregation: tool.schema.literal("count_rows")', source)
                self.assertIn('column: tool.schema.null()', source)
                self.assertNotRegex(source, r"widgetId:|layout:")

    def test_schemer_reuses_private_opencode_credentials_without_mounting_provider_data(self):
        overlay = (ROOT / "compose.schemer.ai.yaml").read_text()
        ai_compose = (ROOT / "compose.ai.yaml").read_text()
        self.assertIn("SCHEMER_OPENCODE_URL: http://opencode:4096", overlay)
        self.assertIn("SCHEMER_OPENCODE_USERNAME: ${SCHEMII_OPENCODE_USERNAME", overlay)
        self.assertIn("SCHEMER_OPENCODE_PASSWORD: ${SCHEMII_OPENCODE_PASSWORD", overlay)
        self.assertIn("SCHEMER_OPENCODE_TIMEOUT: ${SCHEMII_OPENCODE_TIMEOUT:-120}", overlay)
        self.assertIn("SCHEMII_OPENCODE_TIMEOUT: ${SCHEMII_OPENCODE_TIMEOUT:-120}", ai_compose)
        self.assertIn("./ai/schemer-workspace:/workspace-schemer:ro", overlay)
        self.assertIn("condition: service_healthy", overlay)
        self.assertNotIn("schemii-opencode-data:/", overlay)


if __name__ == "__main__":
    unittest.main()
