import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a migration preview against an exact verified profile and namespace; never applies SQL.",
  args: {
    profileId: tool.schema.string().min(1).max(128),
    namespace: tool.schema.string().min(1).max(63),
    destructivePolicy: tool.schema.enum(["reject", "allow-preview"]),
    purpose: tool.schema.string().trim().min(1).max(500),
  },
  async execute(args) {
    return "SCHEMII_ACTION:" + JSON.stringify({ type: "migration_preview", ...args, readOnly: true })
  },
})
