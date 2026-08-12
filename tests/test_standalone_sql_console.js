const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "src/schemii/web/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "src/schemii/web/index.html"), "utf8");

assert.match(source, /crypto\.randomUUID\(\)[\s\S]*console\/executions[\s\S]*writeGrantId: null/, "read runs must use caller-owned execution IDs and the Console route");
assert.match(source, /console\/executions\/\$\{encodeURIComponent\(executionId\)\}[\s\S]*method: "DELETE"/, "Cancel must request server-side PostgreSQL cancellation");
assert.match(source, /result\.statements\.map\(\(statement, index\) => \(\{[\s\S]*kind: "result"/, "each ordered statement response must become a result tab");
assert.match(source, /function standaloneSqlTabContent[\s\S]*statement\.columns[\s\S]*statement\.rows/, "result tabs must render server-returned columns and rows");
assert.match(source, /profileId: selected\.id[\s\S]*database: selected\.dbname[\s\S]*namespace: postgresState\.namespace/, "execution must retain the exact selected target identity");
assert.match(html, /id="standalone-sql-write-toggle"[^>]*disabled/, "write mode must remain unavailable until ephemeral grants are implemented");
assert.match(html, /id="run-all-standalone-sql"[\s\S]*id="run-standalone-sql"/, "the editor must expose distinct Run all and Run actions");
assert.match(html, /id="standalone-sql-view-menu"[\s\S]*id="standalone-sql-view"[\s\S]*id="standalone-sql-view-list"/, "the Console header must expose an attached query menu");
assert.doesNotMatch(html, /Order review|Customer lookup|Operations scratchpad/, "prototype Console views must not ship in the live workspace");
assert.match(source, /function nextStandaloneSqlViewName[\s\S]*function addStandaloneSqlView[\s\S]*function removeStandaloneSqlView[\s\S]*resultTabs\.length/, "browser-local Console views must support safe creation and removal");
assert.match(source, /data-select-sql-view[\s\S]*data-rename-sql-view[\s\S]*data-remove-sql-view[\s\S]*data-add-sql-view/, "each query menu row must support selection, rename, removal, and a final add action");
assert.match(source, /function uniqueStandaloneSqlViewName[\s\S]*finishStandaloneSqlViewRename/, "query views must support collision-safe inline renaming");
assert.match(html, /standalone-sql-result-head[\s\S]*id="cancel-standalone-sql"/, "Cancel must be shown in the Results pane header");
assert.doesNotMatch(html.match(/standalone-sql-editor-content[\s\S]*?<\/footer>/)?.[0] || "", /cancel-standalone-sql/, "Cancel must not remain in the editor footer");
assert.match(source, /filter\(tab => tab\.pinned\)[\s\S]*\.\.\.newTabs/, "new executions must preserve pinned tabs and replace unpinned tabs");
assert.match(source, /data-pin-result-tab[\s\S]*tab\.pinned = !tab\.pinned/, "result tabs must support pinning and unpinning");
assert.match(source, /function uniqueStandaloneSqlTabLabel[\s\S]*toLocaleLowerCase[\s\S]*\(\$\{suffix\}\)/, "tab names must be unique case-insensitively with deterministic suffixes");
assert.match(source, /standaloneSqlResultLabels\(viewState, result\.statements\.length\)/, "automatic result names must account for retained tabs");
assert.match(source, /data-rename-result-tab[\s\S]*data-result-tab-name[\s\S]*finishStandaloneSqlTabRename/, "result tabs must expose inline renaming");
assert.doesNotMatch(source, /Preparing a synthetic PostgreSQL response|finishStandaloneSqlRun\(sql\)/, "the standalone Run path must not synthesize results");

const scannerStart = source.indexOf("function standaloneSqlStatementRanges");
const scannerEnd = source.indexOf("function standaloneSqlTabContent", scannerStart);
assert.notEqual(scannerStart, -1, "Console statement scanner is missing");
assert.notEqual(scannerEnd, -1, "Console statement scanner end marker is missing");
const context = vm.createContext({});
vm.runInContext(`${source.slice(scannerStart, scannerEnd)}\nglobalThis.sqlForRun = standaloneSqlForRun;`, context);

const sql = "SELECT ';' AS quoted; -- divider ;\nSELECT $$body;value$$ AS body;\nSELECT 3;";
const secondCursor = sql.indexOf("body;");
assert.equal(context.sqlForRun(sql, secondCursor, secondCursor), "-- divider ;\nSELECT $$body;value$$ AS body", "caret Run must respect comments, strings, and dollar quotes");
const thirdStart = sql.indexOf("SELECT 3");
assert.equal(context.sqlForRun(sql, thirdStart, thirdStart + 8), "SELECT 3", "selected text must run exactly after trimming");
const whitespace = sql.lastIndexOf("\n");
assert.match(context.sqlForRun(sql, whitespace, whitespace), /SELECT \$\$body;value\$\$/, "whitespace after a statement must choose the preceding statement");
assert.equal(context.sqlForRun(sql, 0, 0, true), sql, "Run all must submit the complete trimmed editor script");

const labelsStart = source.indexOf("function uniqueStandaloneSqlTabLabel");
const labelsEnd = source.indexOf("function renderStandaloneSqlResultTabs", labelsStart);
vm.runInContext(`${source.slice(labelsStart, labelsEnd)}\nglobalThis.uniqueLabel = uniqueStandaloneSqlTabLabel; globalThis.resultLabels = standaloneSqlResultLabels;`, context);
const labelState = { resultTabs: [{ id: "one", label: "Result 1" }, { id: "named", label: "Customers" }] };
assert.deepEqual(Array.from(context.resultLabels(labelState, 2)), ["Result 2", "Result 3"], "automatic names must continue after retained result names");
assert.equal(context.uniqueLabel(labelState, "result 1"), "result 1 (2)", "rename collisions must be resolved case-insensitively");
assert.equal(context.uniqueLabel(labelState, " Customers ", "named"), "Customers", "a tab may keep its own normalized name");

console.log("Standalone SQL Console contracts passed");
