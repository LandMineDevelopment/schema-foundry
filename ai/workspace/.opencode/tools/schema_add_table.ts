import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose adding a table to the saved schema for an exact profile and namespace; does not execute the change.",
  args: {
    profileId: tool.schema.string().min(1).max(128).optional(),
    namespace: tool.schema.string().min(1).max(63).optional(),
    name: tool.schema.string().trim().min(1).max(63),
    purpose: tool.schema.string().trim().min(1).max(500),
  },
  async execute(args) {
    return "SCHEMA_FOUNDRY_ACTION:" + JSON.stringify({ type: "add_table", ...args, requiresConfirmation: true })
  },
})
