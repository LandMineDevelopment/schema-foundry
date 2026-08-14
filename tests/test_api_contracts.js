const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const context = vm.createContext({ window: {}, Error, TypeError, Object, Array });
vm.runInContext(fs.readFileSync("src/schemii/shared_web/api-contracts.js", "utf8"), context);
const contracts = context.window.SchemiiShared;

assert.equal(contracts.validateSessionResponse({ token: "token", serverId: "server" }).token, "token");
assert.equal(contracts.validateProfilesResponse({ profiles: [{ id: "local" }] }).profiles.length, 1);
assert.equal(contracts.validateCatalogResponse({ namespaces: ["public"] }, "namespaces").namespaces[0], "public");
assert.equal(contracts.validateCatalogResponse({ relations: [{ name: "orders", kind: "table" }] }, "relations").relations[0].name, "orders");
assert.equal(contracts.validatePlanResponse({ id: "plan", steps: [], warnings: [], destructive: false }).id, "plan");
assert.equal(contracts.validateOperationResponse({ operation: { id: "operation", state: "running" } }).operation.state, "running");
assert.equal(contracts.validateResourceSummariesResponse({ resources: [{ id: "schema" }] }).resources.length, 1);
assert.equal(contracts.validateResourceSummariesResponse({ summaries: [{ id: "dashboard" }] }).summaries.length, 1);
assert.equal(contracts.validateDeleteResponse({ deleted: "schema" }).deleted, "schema");
assert.equal(contracts.validateShutdownResponse({ shuttingDown: true }).shuttingDown, true);

for (const [validator, payload] of [
  [contracts.validateSessionResponse, { token: "" }],
  [contracts.validateSessionResponse, { token: "token" }],
  [contracts.validateProfilesResponse, { profiles: null }],
  [value => contracts.validateCatalogResponse(value, "relations"), { relations: [{ kind: "table" }] }],
  [contracts.validatePlanResponse, { id: "plan", steps: [] }],
  [contracts.validateOperationResponse, { operation: { id: "operation" } }],
  [contracts.validateResourceSummariesResponse, { resources: [{}] }],
  [contracts.validateDeleteResponse, { deleted: "" }],
  [contracts.validateShutdownResponse, { shuttingDown: false }],
]) {
  assert.throws(() => validator(payload), error => error.code === "invalid_api_response");
}

const postgresPath = contracts.createApiPathPredicate("/api/postgres");
assert.equal(postgresPath("/api/postgres/profiles"), true);
assert.equal(postgresPath("/api/postgres/profiles?active=true"), true);
assert.equal(postgresPath("/api/postgresql/profiles"), false);
assert.equal(postgresPath("/api/postgres-evil/profiles"), false);
assert.equal(postgresPath("https://example.com/api/postgres/profiles"), false);

assert.equal(contracts.postgresResponseValidator("/api/postgres/profiles"), contracts.validateProfilesResponse);
assert.equal(typeof contracts.postgresResponseValidator("/api/postgres/profiles/id/relations?namespace=public"), "function");
assert.equal(contracts.postgresResponseValidator("/api/postgres/profiles-extra"), null);

console.log("Shared API contract tests passed");
