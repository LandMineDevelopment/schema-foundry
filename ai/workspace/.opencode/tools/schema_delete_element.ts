import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose deleting one saved-schema element by stable ID; destructive and never executed by this tool.",
  args: {
    profileId: tool.schema.string().min(1).max(128).optional(),
    namespace: tool.schema.string().min(1).max(63).optional(),
    elementType: tool.schema.enum(["table", "column"]),
    tableId: tool.schema.string().min(1).max(128),
    columnId: tool.schema.string().min(1).max(128).optional(),
    reason: tool.schema.string().trim().min(1).max(500),
  },
  async execute(args) {
    if (args.elementType === "column" && !args.columnId) {
      throw new Error("columnId is required when deleting a column")
    }
    return "SCHEMII_ACTION:" + JSON.stringify({ type: "delete_element", ...args, destructive: true, requiresConfirmation: true })
  },
})
