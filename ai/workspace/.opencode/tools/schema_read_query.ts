import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a read-only PostgreSQL query for an exact Schemii profile and namespace. Raw SQL always requires UI approval.",
  args: {
    profileId: tool.schema.string().min(1).max(128).describe("Exact selected connection profile ID"),
    namespace: tool.schema.string().min(1).max(63).describe("Exact selected PostgreSQL namespace"),
    sql: tool.schema.string().trim().min(1).max(10000).describe("One read-only SQL statement; no writes or transaction control"),
    purpose: tool.schema.string().trim().min(1).max(500).describe("Why this query is needed and what it will inspect"),
  },
  async execute(args) {
    return "SCHEMII_ACTION:" + JSON.stringify({
      action: "schema_read_query",
      ...args,
      readOnly: true,
      requiresApproval: true,
    })
  },
})
