import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

const column = tool.schema.object({
  name: tool.schema.string().trim().min(1).max(63), type: tool.schema.string().trim().min(1).max(128),
  primary: tool.schema.boolean().optional(), nullable: tool.schema.boolean().optional(), unique: tool.schema.boolean().optional(),
  default: tool.schema.string().max(1000).optional(),
})

export default tool({
  description: "Propose adding one complete table to the active saved schema.",
  args: { name: tool.schema.string().trim().min(1).max(63), purpose: tool.schema.string().trim().min(1).max(500), columns: tool.schema.array(column).min(1).max(50) },
  async execute(args) { return "SCHEMII_ACTION:" + JSON.stringify({ type: "add_table", ...args, requiresConfirmation: true }) },
})
