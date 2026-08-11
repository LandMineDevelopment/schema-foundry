const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class ClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  toggle(value, enabled) { if (enabled) this.add(value); else this.remove(value); }
  contains(value) { return this.values.has(value); }
}

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.dataset = {};
    this.attributes = {};
    this.classList = new ClassList();
    this.disabled = false;
    this.innerHTML = "";
  }
  set className(value) { this._className = value; value.split(/\s+/).filter(Boolean).forEach(item => this.classList.add(item)); }
  get className() { return this._className || ""; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
}

const document = { body: new Element("body"), createElement: tag => new Element(tag) };
const context = vm.createContext({
  window: { innerWidth: 1000, innerHeight: 800 }, document, HTMLElement: Element,
  getComputedStyle: () => ({ textOverflow: "clip", webkitLineClamp: "none" }),
  setTimeout, clearTimeout, requestAnimationFrame: callback => callback(), TypeError
});
vm.runInContext(fs.readFileSync("src/schemii/shared_web/ui-components.js", "utf8"), context);
const shared = context.window.SchemiiShared;

const button = shared.createIconButton({ icon: "refresh", label: "Refresh", dataset: { action: "refresh" } });
assert.equal(button.type, "button");
assert.equal(button.getAttribute("aria-label"), "Refresh");
assert.equal(button.dataset.action, "refresh");
assert.match(button.innerHTML, /<svg/);
const originalMarkup = button.innerHTML;
shared.setControlLoading(button, true, { loadingLabel: "Checking" });
assert.equal(button.innerHTML, originalMarkup, "loading must preserve icon markup");
assert.equal(button.getAttribute("aria-busy"), "true");
assert.equal(button.disabled, true);
shared.setControlLoading(button, false, { label: "Refresh" });
assert.equal(button.getAttribute("aria-busy"), null);
assert.equal(button.disabled, false);
assert.equal(button.dataset.tooltip, "Refresh");

const status = new Element();
shared.setControlStatus(status, "Failed", { state: "error", hideWhenEmpty: true });
assert.equal(status.classList.contains("error"), true);
assert.equal(status.hidden, false);
assert.throws(() => shared.createIconButton({ icon: "missing", label: "Missing" }), TypeError);
console.log("Shared UI component tests passed");
