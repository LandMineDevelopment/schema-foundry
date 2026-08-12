const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const markup = fs.readFileSync("src/schemii/web/index.html", "utf8");
const styles = fs.readFileSync("src/schemii/web/styles.css", "utf8");

function extract(name, nextName) {
  const start = source.indexOf(`function ${name}`);
  const end = source.indexOf(`function ${nextName}`, start);
  assert.notEqual(start, -1, `${name} is missing`);
  assert.notEqual(end, -1, `${nextName} boundary is missing`);
  return source.slice(start, end);
}

const context = vm.createContext({});
vm.runInContext(`
function escapeHtml(value) { return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); }
${extract("selectViewsTarget", "verifyViewsCatalog")}
${extract("verifyViewsCatalog", "verifyViewsDescriptor")}
${extract("verifyViewsDescriptor", "validViewsDefinition")}
${extract("validViewsDefinition", "validViewsMaterialized")}
${extract("validViewsMaterialized", "validViewsLineage")}
${extract("validViewsLineage", "viewsUnavailableReason")}
${extract("viewsUnavailableReason", "renderLiveLineageItems")}
${extract("renderLiveLineageItems", "renderLiveViewsState")}
${extract("renderLiveDefinition", "renderLiveColumnFlow")}
${extract("renderLiveColumnFlow", "renderLiveImpact")}
${extract("renderLiveImpact", "renderLiveInspector")}
${extract("renderLiveInspector", "liveViewCard")}
globalThis.helpers = { selectViewsTarget, verifyViewsCatalog, verifyViewsDescriptor, renderLiveDefinition, renderLiveColumnFlow, renderLiveImpact, renderLiveInspector, renderLiveLineageItems };`, context);

const profiles = [{ id: "first", dbname: "one" }, { id: "linked", dbname: "two" }];
assert.equal(context.helpers.selectViewsTarget(profiles, "linked", null, "current", ["current", "public"]).profile.id, "linked");
assert.equal(context.helpers.selectViewsTarget(profiles, "missing", { sourceProfileId: "linked", database: "two", namespace: "linked_ns" }, "", ["linked_ns", "public"]).namespace, "linked_ns");
assert.equal(context.helpers.selectViewsTarget(profiles, null, null, "missing", ["private", "public"]).namespace, "public");

const target = { profileId: "linked", database: "two", namespace: "public" };
assert.equal(context.helpers.verifyViewsCatalog({ ...target, relations: [{ name: "sales", kind: "view" }] }, target), true);
assert.equal(context.helpers.verifyViewsCatalog({ ...target, relations: [{ name: "sales", kind: "sequence" }] }, target), false);

const relation = { name: "sales", kind: "materialized_view" };
const descriptor = {
  ...target,
  relation: "sales",
  kind: "materialized_view",
  fingerprint: "a".repeat(64),
  columns: [{ name: "total", type: "numeric", nullable: false, ordinal: 1 }],
  definition: { status: "available", format: "query", sql: "SELECT '<unsafe>' AS total" },
  owner: { status: "available", name: "reporter" },
  permissions: { canSelect: true, canAlter: false, canRefresh: true },
  dependencies: { status: "available", items: [{ database: "two", namespace: "sales", relation: "orders", kind: "table", liveOid: 41 }], truncated: false },
  dependents: { status: "available", items: [], truncated: false },
  materialized: { status: "available", populated: true, concurrentRefreshEligible: false },
  columnProvenance: { status: "unavailable", reason: "not_supported" },
};
assert.equal(context.helpers.verifyViewsDescriptor(descriptor, target, relation), true);
assert.equal(context.helpers.verifyViewsDescriptor({ ...descriptor, fingerprint: "abc" }, target, relation), false);
assert.equal(context.helpers.verifyViewsDescriptor({ ...descriptor, dependencies: { status: "available", items: [{ ...descriptor.dependencies.items[0], database: "other" }], truncated: false } }, target, relation), false);

const inspector = context.helpers.renderLiveInspector(descriptor);
assert.match(inspector, /reporter/);
assert.match(inspector, /Select: Allowed/);
assert.match(inspector, /Alter: Not allowed/);
assert.match(inspector, /Refresh: Allowed/);
assert.match(inspector, /Populated/);
assert.match(inspector, /Concurrent refresh: Not eligible/);
assert.doesNotMatch(inspector, /\[object Object\]/);
assert.match(context.helpers.renderLiveDefinition(descriptor), /&lt;unsafe&gt;/);
assert.match(context.helpers.renderLiveColumnFlow(descriptor), /Not supported/);
assert.match(context.helpers.renderLiveLineageItems(descriptor.dependencies, "Upstream"), /sales\.orders/);
const impact = context.helpers.renderLiveImpact(descriptor);
assert.match(impact, /sales\.orders/);
assert.match(impact, /No direct downstream reported/);
assert.doesNotMatch(impact, /No direct items reported/);

assert.match(source, /&expectedKind=\$\{encodeURIComponent\(relation\.kind\)\}/);
assert.match(source, /catalogGeneration/);
assert.match(source, /inspectionGeneration/);
assert.match(source, /swapViewsSidePanel\(liveCatalogPanel\(\), null, true\)/);
assert.doesNotMatch(source, /renderTwinCanvasConcept|renderLineageFocusConcept|prototypeCatalogPanel|prototypeViewDefinition/);
assert.doesNotMatch(source, /customer_lifetime|daily_revenue|finance_dashboard|source_table/);
assert.doesNotMatch(markup, /prototype-view-(?:editor|commit)-dialog/);
assert.match(markup, /aria-label="Live PostgreSQL views catalog"/);
assert.match(styles, /@media \(prefers-reduced-motion: reduce\)[\s\S]*views-prototype-workspace/);

console.log("Live read-only Views behavior tests passed");
