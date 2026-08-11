const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function field(value = "") { return { value }; }

async function main() {
  const context = vm.createContext({
    window: {},
    Option: class Option { constructor(text, value) { this.text = text; this.value = value; } },
    TypeError,
    encodeURIComponent
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/profile-manager.js", "utf8"), context);
  const shared = context.window.SchemiiShared;
  const fields = {
    id: field(), name: field(), host: field(), port: field(), database: field(),
    user: field(), password: field("secret"), sslmode: field(), timeout: field()
  };
  const form = shared.createProfileForm({ fields, defaults: { name: "Analytics" } });
  form.fill();
  assert.equal(fields.name.value, "Analytics");
  assert.equal(fields.password.value, "");
  Object.assign(fields.name, { value: " Local " });
  Object.assign(fields.host, { value: " db " });
  Object.assign(fields.port, { value: "5433" });
  Object.assign(fields.database, { value: " demo " });
  Object.assign(fields.user, { value: " reader " });
  Object.assign(fields.password, { value: " unchanged " });
  Object.assign(fields.sslmode, { value: "require" });
  Object.assign(fields.timeout, { value: "12" });
  assert.deepEqual(JSON.parse(JSON.stringify(form.read())), {
    name: "Local", host: "db", port: 5433, dbname: "demo", user: "reader",
    password: " unchanged ", sslmode: "require", timeout: 12
  });

  const calls = [];
  const repository = shared.createProfileRepository({ postgresClient: { request: async (path, options) => {
    calls.push([path, options]);
    if (path.endsWith("/namespaces")) return { namespaces: ["public"] };
    if (path === "/api/postgres/profiles") return { profiles: [] };
    return { id: "saved" };
  } } });
  assert.deepEqual(Array.from(await repository.list()), []);
  await repository.save("profile one", { name: "Demo" });
  assert.equal(calls[1][0], "/api/postgres/profiles/profile%20one");
  assert.deepEqual(Array.from(await repository.namespaces("profile one")), ["public"]);

  const select = {
    value: "", disabled: true, options: [],
    replaceChildren(...options) { this.options = options; }
  };
  assert.equal(shared.initializeNamespaceSelect(select, ["one", "two"], { preferred: "two" }), "two");
  assert.equal(select.value, "two");
  assert.equal(select.disabled, false);
  assert.equal(shared.initializeNamespaceSelect(select, []), null);
  assert.equal(select.disabled, true);
  console.log("Shared profile manager tests passed");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
