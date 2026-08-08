const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schema_foundry/web/app.js", "utf8");
const html = fs.readFileSync("src/schema_foundry/web/index.html", "utf8");
const styles = fs.readFileSync("src/schema_foundry/web/styles.css", "utf8");
const start = source.indexOf("const AI_SCHEMA_ACTIONS");
const end = source.indexOf("async function applyAiSchemaAction", start);
assert.notEqual(start, -1, "AI action validation marker is missing");
assert.notEqual(end, -1, "AI action validation end marker is missing");

const context = vm.createContext({ TextEncoder });
vm.runInContext(`
  function relationshipColumnPairs(relationship) {
    const fromColumnIds = relationship.fromColumnIds ?? [relationship.fromColumnId];
    const toColumnIds = relationship.toColumnIds ?? [relationship.toColumnId];
    return fromColumnIds.map((fromColumnId, index) => ({ fromColumnId, toColumnId: toColumnIds[index] }));
  }
  ${source.slice(start, end)}
  globalThis.validateAiSchemaAction = validateAiSchemaAction;
`, context);

const schema = {
  tables: [
    { id: "users", name: "users", columns: [{ id: "user_id", name: "id", type: "uuid", primary: true, unique: true }], uniqueConstraints: [] },
    { id: "orders", name: "orders", columns: [{ id: "owner_id", name: "owner_id", type: "uuid" }], uniqueConstraints: [] }
  ],
  relationships: []
};

assert.equal(context.validateAiSchemaAction(schema, { type: "add_table", payload: { name: "events", columns: [{ name: "id", type: "uuid" }] } }).ok, true);
assert.match(context.validateAiSchemaAction(schema, { type: "add_table", payload: { name: "users" } }).error, /already exists/);
assert.equal(context.validateAiSchemaAction(schema, { type: "add_column", payload: { tableId: "orders", column: { name: "total", type: "numeric" } } }).ok, true);
assert.match(context.validateAiSchemaAction(schema, { type: "update_column", payload: { tableId: "orders", columnId: "owner_id", changes: { nullable: "yes" } } }).error, /true or false/);
assert.equal(context.validateAiSchemaAction(schema, { type: "add_relationship", payload: { fromTableId: "orders", fromColumnId: "owner_id", toTableId: "users", toColumnId: "user_id" } }).ok, true);
assert.match(context.validateAiSchemaAction(schema, { type: "delete_element", payload: { tableId: "orders", elementType: "column", columnId: "owner_id" } }).error, /at least one column/);

assert.match(source, /if \(!confirm\(`Confirm action:/, "write actions must require explicit confirmation");
assert.match(source, /elements\.aiSqlPolicy\.value === "ask" && !confirm\(/, "ask-mode SQL must require confirmation");
assert.match(source, /elements\.aiSqlPolicy\.value === "allow-session" && aiState\.sqlPolicyDeliberatelySelected/, "session SQL must require a deliberate user setting");
assert.match(source, /elements\.postgresProfilePassword\.value = ""/, "connection proposals must clear the password field");
assert.doesNotMatch(source, /localStorage|sessionStorage/, "provider secrets must not use browser storage");
assert.match(source, /path\.startsWith\("\/api\/ai\/"\)/, "AI requests must be restricted to the local API");
assert.doesNotMatch(source.slice(source.indexOf("async function aiRequest"), source.indexOf("async function checkPostgresDrift")), /fetch\((?!path|"\/api\/session")/, "AI request code must not fetch external URLs");
assert.match(source, /postgresState\.selectedProfileId !== context\.profileId/, "actions must recheck the selected profile");
assert.match(source, /JSON\.stringify\(schema\) !== context\.schemaSnapshot/, "schema proposals must recheck their base schema");
const messageRenderer = source.slice(source.indexOf("function appendAiMessage"), source.indexOf("function aiActionSummary"));
assert.match(messageRenderer, /body\.textContent =/, "chat text must render with textContent");
assert.doesNotMatch(messageRenderer, /innerHTML/, "chat text must not render as HTML");
assert.match(html, /id="ai-provider[^" ]*"|id="ai-providers"/, "provider settings UI is missing");
assert.doesNotMatch(html, /id="ai[^\n]+value="[^\n]*(?:key|token|secret)/i, "provider secrets must not be embedded in HTML");
const panelState = source.slice(source.indexOf("function setAiPanelOpen"), source.indexOf("function setAiBusy"));
assert.match(panelState, /mainLayout\.classList\.toggle\("ai-open", open\)/, "AI chat must replace the left tool rail");
assert.match(panelState, /toolRail\.inert = open/, "hidden diagram tools must not remain keyboard-accessible");
assert.doesNotMatch(panelState, /inspector|mobile-open|inspector-dismissed/, "AI chat must not change right inspector state");
assert.match(styles, /\.ai-panel \{[^}]*left: 0;[^}]*translate3d\(-100%/, "AI chat must dock from the left");
assert.match(styles, /\.main-layout\.ai-open \.tool-rail/, "AI chat must visually replace the left tool rail");
assert.match(source, /aiInput\.disabled = busy \|\| !aiState\.available \|\| !elements\.aiModelSelect\.value/, "chat input must remain disabled until a provider model is connected");
assert.match(source, /Connect a provider in settings to start chatting/, "chat must explain how to enable a provider");
assert.match(source, /anonymousFreeAccess \? "Free access"/, "anonymous free providers must be identified accurately");
assert.match(source, /help\.rel = "noopener noreferrer"/, "provider key links must not control the local application window");
assert.match(source, /application\/x-ndjson|readAiActivity/, "chat must consume the local agent activity stream");
assert.match(source, /new TextDecoder\(\)/, "agent activity must parse bounded streamed records incrementally");
const activityRenderer = source.slice(source.indexOf("function beginAiActivity"), source.indexOf("function aiActionSummary"));
assert.match(activityRenderer, /AI_TOOL_LABELS\[event\.tool\]/, "live tool activity must use fixed local labels");
assert.match(activityRenderer, /AI_SKILL_LABELS\[event\.skill\]/, "live skill activity must use fixed local labels");
assert.match(activityRenderer, /body\.textContent = part\.text/, "reasoning must render as text rather than HTML");
assert.doesNotMatch(activityRenderer, /innerHTML|insertAdjacentHTML|eval\(/, "agent visualizations must not interpret model output as code or HTML");
assert.match(source, /requestGeneration !== aiState\.requestGeneration/, "stale agent responses must not enter a reset conversation");
assert.match(styles, /@keyframes ai-dot-wave/, "agent progress animation is missing");
assert.match(styles, /prefers-reduced-motion[\s\S]*\.ai-progress-grid i[\s\S]*animation: none/, "agent animations must respect reduced motion");

console.log("AI chat safety and action validation tests passed");
