const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const html = fs.readFileSync("src/schemii/web/index.html", "utf8");
const styles = fs.readFileSync("src/schemii/web/styles.css", "utf8");
const start = source.indexOf("const AI_SCHEMA_ACTIONS");
const end = source.indexOf("async function applyAiSchemaAction", start);
assert.notEqual(start, -1, "AI action validation marker is missing");
assert.notEqual(end, -1, "AI action validation end marker is missing");

const context = vm.createContext({ TextEncoder });
vm.runInContext(`
  let activeSchemaId = "schema_current";
  const TABLE_WIDTH = 270;
  const COLORS = ["amber", "blue"];
  let nextId = 0;
  function uid(prefix) { nextId += 1; return prefix + "_" + nextId; }
  function readSchemaLibrary() {
    return { schemas: [{ id: "schema_orders", schema: { projectName: "Orders", tables: [], relationships: [] } }] };
  }
  function relationshipColumnPairs(relationship) {
    const fromColumnIds = relationship.fromColumnIds ?? [relationship.fromColumnId];
    const toColumnIds = relationship.toColumnIds ?? [relationship.toColumnId];
    return fromColumnIds.map((fromColumnId, index) => ({ fromColumnId, toColumnId: toColumnIds[index] }));
  }
  ${source.slice(start, end)}
  globalThis.validateAiSchemaAction = validateAiSchemaAction;
  globalThis.validateAiNavigationAction = validateAiNavigationAction;
  globalThis.applyAiPopulation = applyAiPopulation;
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
const population = {
  type: "populate_schema",
  purpose: "Library teaching schema",
  tables: [
    { name: "authors", purpose: "Authors", columns: [{ name: "id", type: "serial", primary: true }, { name: "name", type: "text", nullable: false }] },
    { name: "books", purpose: "Books", columns: [{ name: "id", type: "uuid", primary: true }, { name: "author_id", type: "integer", nullable: false }, { name: "title", type: "text", nullable: false }] }
  ],
  relationships: [{ fromTableName: "books", fromColumnName: "author_id", toTableName: "authors", toColumnName: "id", onDelete: "RESTRICT", onUpdate: "CASCADE" }],
  requiresConfirmation: true
};
const validatedPopulation = context.validateAiSchemaAction({ tables: [], relationships: [] }, population);
assert.equal(validatedPopulation.ok, true);
assert.equal(validatedPopulation.tables.length, 2);
assert.equal(validatedPopulation.relationships.length, 1);
const populatedSchema = { tables: [], relationships: [] };
context.applyAiPopulation(populatedSchema, validatedPopulation, { startX: 100, startY: 80, gridColumns: 2 });
assert.equal(populatedSchema.tables.length, 2);
assert.equal(populatedSchema.relationships.length, 1);
assert.notEqual(populatedSchema.tables[0].id, populatedSchema.tables[1].id);
assert.notEqual(populatedSchema.tables[0].x, populatedSchema.tables[1].x);
const appliedRelation = populatedSchema.relationships[0];
const appliedBooks = populatedSchema.tables.find(table => table.name === "books");
const appliedAuthors = populatedSchema.tables.find(table => table.name === "authors");
assert.equal(appliedRelation.fromTableId, appliedBooks.id);
assert.equal(appliedRelation.fromColumnId, appliedBooks.columns.find(column => column.name === "author_id").id);
assert.equal(appliedRelation.toTableId, appliedAuthors.id);
assert.equal(appliedRelation.toColumnId, appliedAuthors.columns.find(column => column.name === "id").id);
assert.match(context.validateAiSchemaAction({ tables: [], relationships: [] }, { ...population, path: "/tmp/schema" }).error, /unsupported fields/);
const noPrimary = structuredClone(population);
noPrimary.tables[0].columns[0].primary = false;
noPrimary.relationships = noPrimary.relationships.filter(relation => relation.toTableName !== "authors");
assert.equal(context.validateAiSchemaAction({ tables: [], relationships: [] }, noPrimary).ok, true, "PostgreSQL permits keyless tables when no foreign key references them");
const keylessJunction = structuredClone(population);
keylessJunction.tables.push({ name: "book_tags", purpose: "Keyless staging junction", columns: [{ name: "book_id", type: "uuid", nullable: false }, { name: "tag_id", type: "uuid", nullable: false }] });
keylessJunction.tables.push({ name: "tags", purpose: "Tags", columns: [{ name: "id", type: "uuid", primary: true }] });
keylessJunction.relationships.push(
  { fromTableName: "book_tags", fromColumnName: "book_id", toTableName: "books", toColumnName: "id", onDelete: "CASCADE", onUpdate: "CASCADE" },
  { fromTableName: "book_tags", fromColumnName: "tag_id", toTableName: "tags", toColumnName: "id", onDelete: "CASCADE", onUpdate: "CASCADE" }
);
assert.equal(context.validateAiSchemaAction({ tables: [], relationships: [] }, keylessJunction).ok, true, "keyless source/junction tables remain buildable PostgreSQL");
const badTarget = structuredClone(population);
badTarget.relationships[0].toColumnName = "name";
badTarget.tables[0].columns[1].type = "integer";
assert.match(context.validateAiSchemaAction({ tables: [], relationships: [] }, badTarget).error, /primary or unique/);
const badType = structuredClone(population);
badType.tables[1].columns[1].type = "bigint";
assert.match(context.validateAiSchemaAction({ tables: [], relationships: [] }, badType).error, /mismatched column types/);
assert.equal(context.validateAiNavigationAction({ type: "create_project", projectName: "Orders v2", requiresConfirmation: true }).ok, true);
assert.equal(context.validateAiNavigationAction({ type: "open_project", schemaId: "schema_orders", projectName: "Orders", requiresConfirmation: true }).ok, true);
assert.equal(context.validateAiNavigationAction({ type: "open_connection", profileId: "local", name: "Local", database: "demo", namespace: "public", requiresConfirmation: true }).ok, true);
assert.match(context.validateAiNavigationAction({ type: "open_project", schemaId: "../../secret", projectName: "Orders" }).error, /ID is invalid/);
assert.match(context.validateAiNavigationAction({ type: "open_connection", profileId: "local", name: "Local", database: "demo", password: "secret" }).error, /unsupported fields/);
assert.match(context.validateAiNavigationAction({ type: "create_project", projectName: "Demo", path: "/tmp/demo" }).error, /unsupported fields/);

const connectionMetadataStart = source.indexOf("function postgresConnectionType");
const connectionMetadataEnd = source.indexOf("async function loadSchemaLibraryConnections", connectionMetadataStart);
const connectionContext = vm.createContext({ postgresState: { profiles: [
  { id: "local", name: "Local", host: "127.0.0.1", dbname: "demo" },
  { id: "docker", name: "Docker", host: "postgres", dbname: "app" },
  { id: "remote", name: "Reporting", host: "db.example", dbname: "reports" }
] } });
vm.runInContext(`${source.slice(connectionMetadataStart, connectionMetadataEnd)}\nthis.postgresConnectionType = postgresConnectionType; this.schemaLibraryConnection = schemaLibraryConnection;`, connectionContext);
assert.equal(connectionContext.postgresConnectionType(connectionContext.postgresState.profiles[0]), "Local DB");
assert.equal(connectionContext.postgresConnectionType(connectionContext.postgresState.profiles[1]), "Docker DB");
assert.equal(connectionContext.postgresConnectionType(connectionContext.postgresState.profiles[2]), "Remote DB");
assert.equal(connectionContext.schemaLibraryConnection({ projectName: "Draft" }).type, "Local project");
const linkedConnection = connectionContext.schemaLibraryConnection({ postgres: { sourceProfileId: "docker", database: "app", namespace: "public" } });
assert.equal(linkedConnection.type, "Docker DB");
assert.equal(linkedConnection.identity, "Docker (docker) · app.public");
const libraryLoader = source.slice(source.indexOf("async function loadSchemaLibraryConnections"), source.indexOf("function renderSchemaLibrary"));
assert.match(libraryLoader, /\/api\/postgres\/profiles/, "schema library must load redacted saved-profile metadata");
assert.doesNotMatch(libraryLoader, /namespaces|password/, "opening the schema library must not contact PostgreSQL or expose credentials");

assert.match(source, /if \(!confirm\(`Confirm action:/, "write actions must require explicit confirmation");
assert.match(source, /elements\.aiSqlPolicy\.value === "ask" && !confirm\(/, "ask-mode SQL must require confirmation");
assert.match(source, /elements\.aiSqlPolicy\.value === "allow-session" && aiState\.sqlPolicyDeliberatelySelected/, "session SQL must require a deliberate user setting");
assert.match(source, /context\.accessLevel === "data" && elements\.aiAccessSelect\.value === "data"/, "SQL actions must require both captured and current data access");
assert.match(source, /context\.accessLevel !== "data" \|\| elements\.aiAccessSelect\.value !== "data"/, "query execution must reject stale data access");
assert.match(source, /elements\.postgresProfilePassword\.value = ""/, "connection proposals must clear the password field");
const navigationHandler = source.slice(source.indexOf("async function confirmAiNavigationAction"), source.indexOf("function detailAiActionError"));
assert.match(navigationHandler, /if \(!confirm\(`Create and open local project/, "project creation must require confirmation");
assert.match(navigationHandler, /if \(!confirm\(`Open local project/, "project opening must require confirmation");
assert.match(navigationHandler, /credentials already stored on this server/, "connection opening must explain database contact before confirmation");
assert.match(navigationHandler, /profiles\.find\(item => item\.id === validated\.payload\.profileId\)/, "connection opening must resolve an exact saved profile ID");
assert.match(navigationHandler, /postgresState\.namespaces\.includes\(requestedNamespace\)/, "a proposed namespace must be verified against the live connection");
assert.match(navigationHandler, /openSchema\(validated\.record\.id, \{ fit: false \}\)/, "agent project opening must preserve the saved viewport");
const authUi = source.slice(source.indexOf("function buildAiAuthForm"), source.indexOf("function loadAiStatus"));
assert.doesNotMatch(authUi, /localStorage|sessionStorage/, "provider credentials must not use browser storage");
assert.match(source, /path\.startsWith\("\/api\/ai\/"\)/, "AI requests must be restricted to the local API");
assert.doesNotMatch(source.slice(source.indexOf("async function aiRequest"), source.indexOf("async function checkPostgresDrift")), /fetch\((?!path|"\/api\/session")/, "AI request code must not fetch external URLs");
const targetResolverStart = source.indexOf("function currentAiPostgresTarget");
const targetResolverEnd = source.indexOf("async function executeAiReadQuery", targetResolverStart);
const targetContext = vm.createContext({
  postgresState: { selectedProfileId: null, namespace: "" },
  schema: { postgres: { sourceProfileId: "tutorial", namespace: "bookstore" } }
});
vm.runInContext(`${source.slice(targetResolverStart, targetResolverEnd)}\nthis.currentAiPostgresTarget = currentAiPostgresTarget;`, targetContext);
assert.equal(targetContext.currentAiPostgresTarget().profileId, "tutorial", "AI queries must use the design's linked profile when the connection dialog has not been opened");
assert.equal(targetContext.currentAiPostgresTarget().namespace, "bookstore", "AI queries must use the design's linked namespace when the connection dialog has not been opened");
targetContext.postgresState.selectedProfileId = "reporting";
targetContext.postgresState.namespace = "analytics";
assert.equal(targetContext.currentAiPostgresTarget().profileId, "reporting", "an explicitly selected profile must override the linked design profile");
assert.equal(targetContext.currentAiPostgresTarget().namespace, "analytics", "an explicitly selected namespace must override the linked design namespace");
const queryExecutor = source.slice(source.indexOf("async function executeAiReadQuery"), source.indexOf("async function ensureAiSession"));
assert.match(queryExecutor, /currentTarget\.profileId !== context\.profileId/, "actions must recheck the effective PostgreSQL profile");
assert.match(queryExecutor, /currentTarget\.namespace !== context\.namespace/, "actions must recheck the effective PostgreSQL namespace");
assert.match(queryExecutor, /Tool error for SQL:/, "failed SQL must be returned to the assistant for correction");
assert.match(queryExecutor, /await sendAiMessage\(text, "tool"\)/, "failed SQL feedback must continue through the bounded assistant context");
assert.match(source, /JSON\.stringify\(schema\) !== context\.schemaSnapshot/, "schema proposals must recheck their base schema");
const schemaApply = source.slice(source.indexOf("async function applyAiSchemaAction"), source.indexOf("const AI_MODEL_STORAGE_KEY"));
assert.match(schemaApply, /validated\.type === "populate_schema"/, "complete schema proposals must apply through one atomic action");
assert.match(schemaApply, /applyAiPopulation\(schema, validated/, "atomic population must create every validated table and relationship");
assert.equal((schemaApply.match(/await persistCurrentSchema\(\)/g) ?? []).length, 1, "an atomic schema population must save once");
assert.match(source, /card\.querySelectorAll\("\.ai-action-error"\).*remove/, "repeated review attempts must replace prior validation errors");
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
assert.match(styles, /\.schema-library-connection/, "saved schema cards must display connection ownership");
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
assert.match(activityRenderer, /event\.type === "part"\) setStage\("model", "Model started", "completed"\)/, "the first model output must complete the model-started stage");
assert.match(activityRenderer, /body\.textContent = part\.text/, "reasoning must render as text rather than HTML");
assert.doesNotMatch(activityRenderer, /innerHTML|insertAdjacentHTML|eval\(/, "agent visualizations must not interpret model output as code or HTML");
assert.match(source, /requestGeneration !== aiState\.requestGeneration/, "stale agent responses must not enter a reset conversation");
const newChat = source.slice(source.indexOf("async function startNewAiChat"), source.indexOf("function formatAiHistoryDate"));
assert.doesNotMatch(newChat, /DELETE|delete_session/, "starting a new chat must preserve the prior persistent session");
const historyUi = source.slice(source.indexOf("function renderAiHistory"), source.indexOf("function updateAiAccessDisclosure"));
assert.match(historyUi, /actions: \[\]/, "restored messages must not recreate historical actionable proposals");
assert.match(historyUi, /aiState\.sessionId = sessionId/, "opening a saved chat must restore its persistent session ID");
assert.match(historyUi, /method: "DELETE"/, "chat history must provide explicit session deletion");
assert.doesNotMatch(historyUi, /innerHTML|insertAdjacentHTML|eval\(/, "chat history must render untrusted content as text");
assert.match(html, /id="ai-history-dialog"/, "chat history dialog is missing");
assert.match(styles, /@keyframes ai-dot-wave/, "agent progress animation is missing");
assert.match(styles, /prefers-reduced-motion[\s\S]*\.ai-progress-grid i[\s\S]*animation: none/, "agent animations must respect reduced motion");

const preferenceStart = source.indexOf("const AI_MODEL_STORAGE_KEY");
const preferenceEnd = source.indexOf("function setAiPanelOpen", preferenceStart);
assert.notEqual(preferenceStart, -1, "AI model preference marker is missing");
const storageValues = new Map();
const preferenceContext = vm.createContext({
  localStorage: {
    getItem: key => storageValues.get(key) ?? null,
    setItem: (key, value) => storageValues.set(key, value)
  }
});
vm.runInContext(`${source.slice(preferenceStart, preferenceEnd)}\nthis.normalizeStoredAiModel = normalizeStoredAiModel; this.storedAiModel = storedAiModel; this.rememberAiModel = rememberAiModel;`, preferenceContext);
const selectedModel = JSON.stringify({ providerId: "openai", modelId: "gpt-5.4/mini" });
preferenceContext.rememberAiModel(selectedModel);
assert.equal(preferenceContext.storedAiModel(), selectedModel, "last selected model must survive a page reload");
assert.equal(preferenceContext.normalizeStoredAiModel(JSON.stringify({ providerId: "openai", modelId: "gpt", key: "secret" })), "", "model preference must reject credential-like extra fields");
assert.equal(preferenceContext.normalizeStoredAiModel(JSON.stringify({ providerId: "openai", modelId: "gpt\nsecret" })), "", "model preference must reject control characters");

const disclosureStart = source.indexOf("function updateAiAccessDisclosure");
const disclosureEnd = source.indexOf("elements.tablesLayer.addEventListener", disclosureStart);
const disclosureElements = {
  aiAccessSelect: { value: "data" }, aiSqlPolicy: { value: "allow-session" },
  aiSqlPolicyWrap: { hidden: false }, aiFunctionCaveat: { hidden: false }, aiAccessDisclosure: { textContent: "" }
};
const disclosureContext = vm.createContext({ elements: disclosureElements, aiState: { sqlPolicyDeliberatelySelected: true } });
vm.runInContext(`${source.slice(disclosureStart, disclosureEnd)}\nthis.updateAiAccessDisclosure = updateAiAccessDisclosure;`, disclosureContext);
disclosureContext.updateAiAccessDisclosure();
assert.equal(disclosureElements.aiSqlPolicy.value, "allow-session", "remaining in data access must preserve deliberate SQL policy");
disclosureElements.aiAccessSelect.value = "schema";
disclosureContext.updateAiAccessDisclosure();
assert.equal(disclosureElements.aiSqlPolicy.value, "disabled", "leaving data access must disable SQL execution");
assert.equal(disclosureContext.aiState.sqlPolicyDeliberatelySelected, false, "leaving data access must revoke session SQL approval");

console.log("AI chat safety and action validation tests passed");
