const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function response(ok, status, payload) {
  return {
    ok,
    status,
    json: async () => payload,
    clone() { return response(ok, status, payload); }
  };
}

async function main() {
  const calls = [];
  const queue = [
    response(true, 200, { token: "first" }),
    response(false, 403, { error: { code: "invalid_session", message: "expired" } }),
    response(true, 200, { token: "second" }),
    response(true, 200, { ok: true })
  ];
  let token = null;
  const context = vm.createContext({
    window: {},
    fetch: async (path, options = {}) => {
      calls.push([path, options]);
      return queue.shift();
    },
    Error,
    TypeError
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), context);
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/postgres-client.js", "utf8"), context);
  const session = context.window.SchemiiShared.createSessionClient({
    getToken: () => token,
    setToken: value => { token = value; }
  });
  const postgres = context.window.SchemiiShared.createPostgresClient({ sessionClient: session });
  assert.deepEqual(await postgres.request("/api/postgres/profiles", { headers: { "X-Test": "yes" } }), { ok: true });
  assert.deepEqual(calls.map(call => call[0]), ["/api/session", "/api/postgres/profiles", "/api/session", "/api/postgres/profiles"]);
  assert.equal(calls[3][1].headers["X-Schemii-Token"], "second");
  assert.equal(calls[3][1].headers["X-Test"], "yes");
  assert.equal(token, "second");
  await assert.rejects(() => postgres.request("https://example.com"), /allowed local application API/);
  assert.equal(calls.length, 4, "disallowed paths must be rejected before fetch");

  const existing = { previous: true };
  const orderContext = vm.createContext({
    window: { SchemiiShared: existing },
    document: {},
    HTMLElement: class {},
    fetch: async () => response(true, 200, {}),
    setTimeout,
    clearTimeout,
    requestAnimationFrame() {}
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/ui-components.js", "utf8"), orderContext);
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), orderContext);
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/postgres-client.js", "utf8"), orderContext);
  assert.equal(orderContext.window.SchemiiShared.previous, true);
  assert.equal(typeof orderContext.window.SchemiiShared.createIconButton, "function");
  assert.equal(typeof orderContext.window.SchemiiShared.createPostgresClient, "function");
  console.log("Shared session client tests passed");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
