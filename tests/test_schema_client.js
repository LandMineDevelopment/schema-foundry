const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "src/schemii/web/app.js"), "utf8");

assert.doesNotMatch(source, /fetch\([^\n]*\/api\/schemas|fetch\(`\/api\/schemas/, "schema requests must not bypass the authenticated client");

const reload = source.slice(source.indexOf("async function reloadActiveSchemaRecord"), source.indexOf("function standaloneSqlTarget"));
assert.match(reload, /sharedSessionClient\.json\(`\/api\/schemas\/\$\{encodeURIComponent\(activeSchemaId\)\}`/, "active schema refresh must use the exact-resource session client route");
assert.match(reload, /createApiPathPredicate\("\/api\/schemas"\)/, "active schema refresh must allow only schema resource paths");

const save = source.slice(source.indexOf("async function putRecordFile"), source.indexOf("function saveRecordFile"));
assert.match(save, /sharedSessionClient\.json\(path/, "schema saves must use the session client");
assert.match(save, /allowPath: candidate => candidate === path/, "schema saves must allow only their exact encoded path");
assert.match(save, /"X-Schemii-Layout-Protocol": "2"/, "schema saves must preserve the layout protocol header");
assert.match(save, /"X-Schemii-Layout-Token": record\.layoutToken/, "schema saves must preserve the layout token header");

const initialize = source.slice(source.indexOf("async function initializeSchemaLibrary"), source.indexOf("async function persistSchemaRecord"));
assert.match(initialize, /sharedSessionClient\.json\("\/api\/schemas"/, "schema initialization must use the session client");
assert.match(initialize, /allowPath: path => path === "\/api\/schemas"/, "schema initialization must allow only the exact list path");

const deletion = source.slice(source.indexOf("async function deleteSavedSchema"), source.indexOf("function formatSavedDate"));
assert.match(deletion, /sharedSessionClient\.json\(path, \{ method: "DELETE", body: JSON\.stringify\(\{ expectedRevision: record\.revision, layoutToken: record\.layoutToken \}\) \}/, "schema deletion must carry revision and layout preconditions");
assert.match(deletion, /allowPath: candidate => candidate === path/, "schema deletion must allow only its exact encoded path");

const quarantine = source.slice(source.indexOf("function reportSaveError"), source.indexOf("function captureHistoryState"));
assert.match(quarantine, /schemaSaveQuarantine = \{ schemaId: activeSchemaId, schema: clone\(schema\)/, "a schema conflict must preserve an immutable local recovery snapshot");
assert.match(quarantine, /clearTimeout\(saveTimer\)[\s\S]*schema-conflict-banner/, "a schema conflict must freeze scheduled autosave and expose recovery");
assert.match(quarantine, /schemaSaveQuarantine\?\.schemaId === schemaId[\s\S]*schema_save_quarantined/, "queued saves must not replay a stale schema after quarantine");
assert.match(source, /export-conflicted-schema[\s\S]*schemaForStorage\(schemaSaveQuarantine\.schema[\s\S]*refresh-conflicted-schema[\s\S]*validateSchemaRecord/, "quarantined local edits must remain exportable until explicit authoritative refresh");

const clientDeclaration = source.indexOf("const sharedSessionClient =");
assert.ok(clientDeclaration > source.indexOf("async function putRecordFile"), "schema functions may be declared before the session client");
assert.ok(clientDeclaration < source.lastIndexOf("initializeSchemaLibrary().finally"), "the session client must initialize before schema startup invokes those functions");

console.log("Authenticated schema client contract tests passed");
