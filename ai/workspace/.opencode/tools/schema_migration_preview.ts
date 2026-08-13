import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a read-only migration preview against an exact listed PostgreSQL profile and namespace. This never applies SQL.",
  args: {
    profileId: tool.schema.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/),
    namespace: tool.schema.string().trim().min(1).max(63),
    destructivePolicy: tool.schema.enum(["reject", "allow-preview"]),
    purpose: tool.schema.string().trim().min(1).max(500),
  },
  async execute(args) {
    return "SCHEMII_ACTION:" + JSON.stringify({ type: "migration_preview", ...args, readOnly: true, requiresConfirmation: true })
  },
})
