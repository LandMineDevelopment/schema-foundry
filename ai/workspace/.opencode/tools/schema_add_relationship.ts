import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a PostgreSQL foreign-key relationship between exact saved-schema columns; does not execute the change.",
  args: {
    profileId: tool.schema.string().min(1).max(128).optional(),
    namespace: tool.schema.string().min(1).max(63).optional(),
    fromTableId: tool.schema.string().min(1).max(128).optional(),
    fromColumnId: tool.schema.string().min(1).max(128).optional(),
    toTableId: tool.schema.string().min(1).max(128).optional(),
    toColumnId: tool.schema.string().min(1).max(128).optional(),
    fromTableName: tool.schema.string().trim().min(1).max(63),
    fromColumnName: tool.schema.string().trim().min(1).max(63),
    toTableName: tool.schema.string().trim().min(1).max(63),
    toColumnName: tool.schema.string().trim().min(1).max(63),
    constraintName: tool.schema.string().trim().min(1).max(63).optional(),
    onDelete: tool.schema.enum(["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]),
    onUpdate: tool.schema.enum(["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]),
  },
  async execute(args) {
    return "SCHEMII_ACTION:" + JSON.stringify({ type: "add_relationship", ...args, requiresConfirmation: true })
  },
})
