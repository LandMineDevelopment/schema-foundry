import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose applying one current migration preview to its exact verified target. UI confirmation is always required.",
  args: {
    profileId: tool.schema.string().min(1).max(128),
    namespace: tool.schema.string().min(1).max(63),
    previewId: tool.schema.string().min(1).max(128),
    planFingerprint: tool.schema.string().min(16).max(256),
  },
  async execute(args) {
    return "SCHEMA_FOUNDRY_ACTION:" + JSON.stringify({ type: "migration_apply", ...args, requiresConfirmation: true })
  },
})
