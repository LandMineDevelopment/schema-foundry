const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "src/schemii/web/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "src/schemii/web/index.html"), "utf8");

assert.match(source, /crypto\.randomUUID\(\)[\s\S]*console\/executions[\s\S]*mode: writeMode \? "write" : "read"[\s\S]*writeGrantId,/, "runs must use caller-owned execution IDs and the current view's mode and grant");
assert.match(source, /console\/executions\/\$\{encodeURIComponent\(executionId\)\}[\s\S]*method: "DELETE"/, "Cancel must request server-side PostgreSQL cancellation");
assert.match(source, /result\.statements\.map\(\(statement, index\) => \(\{[\s\S]*kind: "result"/, "each ordered statement response must become a result tab");
assert.match(source, /function standaloneSqlTabContent[\s\S]*statement\.columns[\s\S]*statement\.rows/, "result tabs must render server-returned columns and rows");
assert.match(source, /profileId: selected\.id[\s\S]*database: selected\.dbname[\s\S]*namespace: postgresState\.namespace/, "execution must retain the exact selected target identity");
assert.match(html, /id="standalone-sql-write-toggle"[^>]*disabled/, "write mode must be unavailable until an exact target is selected");
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
assert.match(source, /console\/write-grants[\s\S]*confirmed: true[\s\S]*grant\.writeGrantId/, "write mode must create a confirmed grant for the exact query and target");
assert.match(source, /function revokeStandaloneSqlWriteGrant[\s\S]*method: "DELETE"[\s\S]*function revokeAllStandaloneSqlWriteGrants/, "write grants must have explicit single-view and all-view revocation paths");
assert.match(source, /writeMode: false, writeGrantId: null[\s\S]*function removeStandaloneSqlView[\s\S]*await revokeStandaloneSqlWriteGrant\(removed\)/, "new views must be read-only and removal must revoke before deleting");
assert.match(source, /targetChanged && standaloneSqlState\.open[\s\S]*revokeAllStandaloneSqlWriteGrants\(true\)[\s\S]*renderStandaloneSqlTarget\(target\)/, "target synchronization must revoke all old grants before rendering the new target");
assert.match(source, /async function closeStandaloneSqlWorkspace[\s\S]*await revokeAllStandaloneSqlWriteGrants\(\)[\s\S]*standaloneSqlState\.open = false/, "Console close must revoke all grants before visually closing");
assert.match(source, /const viewState = currentStandaloneSqlView\(\);[\s\S]*const writeMode = Boolean\(viewState\.writeMode && viewState\.writeGrantId\)[\s\S]*consoleId: viewState\.consoleId/, "execution authorization must come only from the captured owning view");
assert.match(source, /write_grant_required[\s\S]*write_grant_expired[\s\S]*write_grant_target_changed[\s\S]*clearStandaloneSqlWriteGrant\(viewState\)/, "stale write authorization errors must clear the owning view's grant");
assert.match(source, /if \(result\.committed\) void checkPostgresDrift\(\)/, "committed writes must recheck linked PostgreSQL catalog drift");
assert.match(source, /pagehide[\s\S]*keepalive: true/, "page unload must best-effort revoke grants while server expiry remains authoritative");
assert.match(html, /Successful scripts commit transactionally[\s\S]*external or nontransactional side effects/, "write confirmation must disclose commit and function side-effect semantics");
assert.doesNotMatch(html, /Phase 1 remains|visual prototype and executes nothing|future write-enabled/, "prototype write-mode copy must not remain");

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
