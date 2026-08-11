import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose creating a new empty Schemer dashboard. Schemer generates its logical ID and layout after confirmation.",
  args: { title: tool.schema.string().trim().min(1).max(128) },
  async execute(args) {
    return "SCHEMER_ACTION:" + JSON.stringify({ type: "dashboard_create", ...args, requiresConfirmation: true })
  },
})
