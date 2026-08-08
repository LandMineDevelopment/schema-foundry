import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Immediately propose creating a new local Schemii project, schema, or design when requested. The project must not already exist and needs no schemaId or availableProjects entry. Schemii generates its logical ID and saves it only after UI confirmation.",
  args: {
    projectName: tool.schema.string().trim().min(1).max(256),
  },
  async execute(args) {
    return "SCHEMII_ACTION:" + JSON.stringify({ type: "create_project", ...args, requiresConfirmation: true })
  },
})
