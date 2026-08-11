import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose renaming one exact widget without changing its source, query, presentation, or layout.",
  args: {
    dashboardId: tool.schema.string().trim().min(1).max(128),
    expectedRevision: tool.schema.number().int().min(0),
    widgetId: tool.schema.string().trim().min(1).max(128),
    currentTitle: tool.schema.string().trim().min(1).max(128),
    title: tool.schema.string().trim().min(1).max(128),
  },
  async execute(args) {
    return "SCHEMER_ACTION:" + JSON.stringify({ type: "widget_rename", ...args, requiresConfirmation: true })
  },
})
