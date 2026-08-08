const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const html = fs.readFileSync("src/schemii/web/index.html", "utf8");
const css = fs.readFileSync("src/schemii/web/styles.css", "utf8");

const storageStart = source.indexOf("function onboardingStorageValue");
const storageEnd = source.indexOf("function rememberOnboardingPreference");
assert.notEqual(storageStart, -1, "onboarding storage helpers are missing");
assert.notEqual(storageEnd, -1, "onboarding storage helper end marker is missing");

const values = new Map();
const context = vm.createContext({
  localStorage: {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); }
  }
});
vm.runInContext(`
  const ONBOARDING_DISABLED_KEY = "schemii.onboarding.disabled.v1";
  const ONBOARDING_SERVER_KEY = "schemii.onboarding.server.v1";
  let onboardingSeenServerId = null;
  ${source.slice(storageStart, storageEnd)}
  globalThis.shouldShowOnboarding = shouldShowOnboarding;
`, context);

assert.equal(context.shouldShowOnboarding("start-one"), true, "a new server start should show onboarding");
assert.equal(values.get("schemii.onboarding.server.v1"), "start-one");
assert.equal(context.shouldShowOnboarding("start-one"), false, "refreshes during one server run must not reshow onboarding");
assert.equal(context.shouldShowOnboarding("start-two"), true, "a later server start should show onboarding again");
values.set("schemii.onboarding.disabled.v1", "1");
assert.equal(context.shouldShowOnboarding("start-three"), false, "the persistent opt-out must suppress future onboarding");

assert.equal((html.match(/data-onboarding-page=/g) || []).length, 4, "the introduction should have four pages");
assert.equal((html.match(/class="onboarding-screenshot/g) || []).length, 4, "every introduction page needs a screenshot-style preview");
assert.match(html, /id="onboarding-dont-show"[^>]+type="checkbox"/, "the future-start opt-out is missing");
assert.match(html, /id="onboarding-back"[^>]+aria-label="Previous introduction page"/, "the back arrow needs an accessible label");
assert.match(html, /id="show-onboarding-button"/, "the introduction must be reopenable from the Help menu");
assert.match(html, /id="shutdown-button"/, "the browser shutdown control is missing");
assert.match(css, /\.onboarding-dialog[^}]+max-height:/, "the introduction must fit within the viewport");
assert.match(css, /@media \(max-width: 540px\)[\s\S]+\.onboarding-dialog/, "the introduction needs mobile layout rules");

const shutdownStart = source.indexOf("async function shutdownSchemii");
const shutdownEnd = source.indexOf("function schemaForStorage");
const shutdown = source.slice(shutdownStart, shutdownEnd);
assert.ok(shutdown.indexOf("await flushPendingSave()") < shutdown.indexOf('fetch("/api/shutdown"'), "shutdown must save pending edits before stopping the server");
assert.match(shutdown, /"X-Schemii-Token": postgresState\.token/, "shutdown must send the local session token");
assert.match(source, /initializeSchemaLibrary\(\)\.finally[\s\S]+initializeOnboarding\(\)/, "onboarding must initialize after the workspace");

console.log("Onboarding and browser shutdown tests passed");
