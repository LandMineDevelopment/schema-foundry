import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

const changes = tool.schema.object({
  name: tool.schema.string().trim().min(1).max(63).optional(),
  type: tool.schema.string().trim().min(1).max(128).optional(),
  nullable: tool.schema.boolean().optional(),
  default: tool.schema.string().trim().max(1000).nullable().optional(),
}).refine((value) => Object.keys(value).length > 0, "At least one column change is required")

export default tool({
  description: "Propose updating one saved-schema column selected by stable table and column IDs; does not execute the change.",
  args: {
    profileId: tool.schema.string().min(1).max(128).optional(),
    namespace: tool.schema.string().min(1).max(63).optional(),
    tableId: tool.schema.string().min(1).max(128),
    columnId: tool.schema.string().min(1).max(128),
    changes,
  },
  async execute(args) {
    return "SCHEMA_FOUNDRY_ACTION:" + JSON.stringify({ type: "update_column", ...args, requiresConfirmation: true })
  },
})
