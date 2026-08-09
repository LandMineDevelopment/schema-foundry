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
assert.match(html, /tour-foreign-column[^>]*>[\s\S]*aria-label="Foreign key"[\s\S]*owner_id/, "the relationship source must use the real foreign-key icon on projects.owner_id");
assert.match(html, /tour-referenced-column[^>]*>[\s\S]*aria-label="Primary key"[\s\S]*id/, "the relationship target must use the real primary-key icon on users.id");
assert.match(html, /tour-relationship-desktop" d="M59 64[^>]+41 42"/, "the desktop relationship must terminate on the exact table-row edges");
assert.match(html, /tour-relationship-mobile" d="M57 85[^>]+47 56"/, "the mobile relationship must terminate on the exact table-row edges");
assert.match(html, /tour-shot-rail"><i><\/i><i><\/i><span class="tour-relationship-tool"/, "only the relationship tool should replace a placeholder toolbar box");
assert.match(html, /<circle cx="6" cy="7" r="2\.5"\/><circle cx="18" cy="17" r="2\.5"\/>/, "the introduction must use the real relationship tool icon");
assert.match(html, /click the foreign-key column first, then click the referenced primary or unique key/, "the relationship instructions must use the real click order");
assert.match(html, /data-relationship-demo-target="tool"[\s\S]+data-relationship-demo-target="target"[\s\S]+data-relationship-demo-target="source"/, "the relationship preview must expose the tool and both column targets");
assert.match(html, /id="relationship-demo-toggle"[^>]*>Pause demo<\/button>/, "the relationship demonstration needs a pause control");
assert.match(source, /RELATIONSHIP_DEMO_STEPS[\s\S]+target: "tool"[\s\S]+target: "source"[\s\S]+target: "target"[\s\S]+state: "editor"[\s\S]+target: "save"[\s\S]+state: "complete"/, "the relationship demonstration must select both columns, open the editor, and save");
assert.match(html, /tour-relationship-editor[\s\S]+Create relationship[\s\S]+projects → users[\s\S]+projects_owner_id_fkey[\s\S]+owner_id · uuid[\s\S]+id · uuid[\s\S]+Save relationship/, "the relationship demonstration must mirror the real creation editor");
assert.match(css, /demo-editor-open \.tour-relationship-editor-backdrop \{ visibility: visible; opacity: 1;/, "the relationship editor must appear after the referenced column is selected");
assert.match(css, /demo-source-selected \.tour-foreign-column[^}]+background: #2a2415[^}]+inset 3px 0 var\(--accent\)/, "the selected foreign-key column must match the production source highlight");
assert.match(css, /demo-relationship-complete \.tour-relationship \{ opacity: 1; \}/, "the connection must appear after both columns are selected");
assert.match(css, /\.tour-relationship path \{[^}]+stroke-dasharray: none;/, "the tutorial connection must be solid like the application connection");
assert.match(html, /Review &amp; confirm/, "the assistant preview should use the real confirmation label");
assert.match(html, /data-postgres-demo-target="tool"[\s\S]+data-postgres-demo-target="profile"[\s\S]+data-postgres-demo-target="import"[\s\S]+data-postgres-demo-target="preview"/, "the PostgreSQL demonstration must use the rail, profile, import, and preview controls");
assert.match(html, /tour-migration-preview[\s\S]+REVIEWED DATABASE CHANGE[\s\S]+Migration preview[\s\S]+0 destructive[\s\S]+CREATE TABLE[\s\S]+Review before apply/, "the PostgreSQL demonstration must show reviewed SQL and safety details");
assert.match(html, /id="postgres-demo-toggle"[^>]*>Pause demo<\/button>/, "the PostgreSQL demonstration needs a pause control");
assert.match(source, /POSTGRES_DEMO_STEPS[\s\S]+target: "tool"[\s\S]+target: "profile"[\s\S]+target: "import"[\s\S]+target: "preview"/, "the PostgreSQL animation must open, connect, import, and preview in order");
assert.match(source, /Preview complete\. Replaying without applying changes/, "the PostgreSQL demonstration must not imply that preview applies a migration");
assert.match(html, /data-assistant-demo-target="tool"[\s\S]+data-assistant-demo-target="composer"[\s\S]+data-assistant-demo-target="send"/, "the assistant demonstration must use the rail, composer, and send controls");
assert.match(html, /tour-assistant-user[\s\S]+Create a small library schema\.[\s\S]+tour-assistant-response[\s\S]+authors, books, and loans[\s\S]+tour-assistant-proposal/, "the assistant demonstration must show a quick conversation and reviewable proposal");
assert.match(html, /id="assistant-demo-toggle"[^>]*>Pause demo<\/button>/, "the assistant demonstration needs a pause control");
assert.match(source, /ASSISTANT_DEMO_STEPS[\s\S]+target: "tool"[\s\S]+target: "composer"[\s\S]+typePrompt: true[\s\S]+target: "send"[\s\S]+state: "working"[\s\S]+state: "complete"/, "the assistant animation must open, type, send, work, and respond in order");
assert.match(source, /ASSISTANT_DEMO_PROMPT\.slice\(0, index\)/, "the assistant prompt must be typed progressively");
assert.match(css, /\.tour-assistant-panel \{[^}]+translate3d\(-100%,0,0\)[^}]+transform \.25s cubic-bezier\(\.22,1,\.36,1\)/, "the tutorial assistant must use the production left-slide transition");
assert.match(html, /Click any table to open its inspector on the right/, "page two must explain how to open the inspector");
assert.match(html, /data-tools icon in the inspector header/, "page two must explain how live data tools open relative to the inspector");
assert.match(html, /Table data[\s\S]+read-only[\s\S]+SQL console/, "page two must identify both live data views");
assert.match(html, /use maximize to temporarily cover the inspector, use minimize to close data tools/, "page two must explain data-view layout controls");
assert.doesNotMatch(html, /tour-(?:table-tag|visual-tag)|<b>[123]<\/b>/, "the animated demonstration must not retain its obsolete numbered cues");
assert.match(html, /tour-demo-playback[\s\S]+onboarding-screenshot inspector-tour-shot/, "playback annotations must sit above the animated window");
assert.match(html, /id="tour-demo-toggle"[^>]*>Pause demo<\/button>/, "the animated demonstration needs a pause control");
assert.match(html, /tour-sql-editor[^>]*>[\s\S]*SELECT \*[\s\S]*FROM "public"\."orders"[\s\S]*LIMIT 100;[\s\S]*tour-sql-actions[\s\S]*Run query/, "the SQL console must mirror the real editor and action layout");
const tourDataMarkup = html.slice(html.indexOf('<div class="tour-data-row">'), html.indexOf('</section>', html.indexOf('<div class="tour-data-row">')));
assert.equal((tourDataMarkup.match(/<th>/g) || []).length, 5, "the Table data preview must show several columns");
assert.equal((tourDataMarkup.match(/<tr>/g) || []).length, 9, "the Table data preview must show several PostgreSQL rows plus its header");
assert.match(html, /class="tour-demo-cursor"[\s\S]*<span>Left click<\/span>/, "the demonstration needs a visible mouse and click tooltip");
assert.match(source, /const INSPECTOR_DEMO_STEPS = \[[\s\S]+target: "table", click: "Left click"/, "the demonstration must begin by clicking a table");
assert.match(source, /target: "inspector-header", click: "Left click"[\s\S]+target: "inspector-header", click: "Right click"/, "the demonstration must show both inspector-header gestures");
assert.match(source, /target: "sql-header", click: "Left click"[\s\S]+target: "sql-header", click: "Right click"/, "the demonstration must show both SQL console header gestures");
assert.match(source, /target: "data-header", click: "Left click"[\s\S]+target: "data-header", click: "Right click"/, "the demonstration must show both Table data header gestures");
for (const target of ["maximize", "minimize", "data-toggle", "inspector-close"]) {
  assert.match(source, new RegExp(`target: "${target}", click: "Left click"`), `the demonstration must use the ${target} button`);
}
assert.match(source, /prefers-reduced-motion: reduce/, "the demonstration must respect reduced-motion preferences");
assert.match(source, /top > demoBounds\.height \* \.7/, "lower controls must flip click tooltips above the cursor");
assert.match(source, /target\.matches\("\.tour-inspector-head, \.tour-data-tools header, \.tour-sql-console"\)[^\n]+classList\.add\("demo-hover"\)/, "the scripted cursor must highlight selectable headers while hovering");
assert.match(css, /\.tour-inspector-head:hover, \.tour-inspector-head\.demo-hover \{ background: #202833;[^}]+inset/, "the tutorial inspector header must use a clear hover highlight");
assert.match(css, /\.tour-data-tools header:hover, \.tour-data-tools header\.demo-hover \{ background: #202833;[^}]+inset/, "the tutorial Table data header must use a clear hover highlight");
assert.match(css, /\.tour-inspector-demo \.tour-sql-console:hover, \.tour-inspector-demo \.tour-sql-console\.demo-hover \{ background: #202833;[^}]+inset/, "the tutorial SQL console hover must override its active-pane color");
assert.match(css, /\.tour-demo-status \{[^}]+font-size: 10px;/, "the instructions above the demonstration must remain readable");
assert.match(css, /\.tour-inspector \{[^}]+height: 100%[^}]+height \.3s cubic-bezier\(\.22,1,\.36,1\)[^}]+opacity \.22s ease-out[^}]+transform \.26s cubic-bezier\(\.22,1,\.36,1\)/, "the tutorial inspector must use the production inspector transition timing");
assert.match(css, /demo-inspector-collapsed \.tour-inspector \{ height: 50px;/, "the tutorial inspector must collapse to its production-style header tile");
assert.match(css, /demo-inspector-collapsed \.tour-inspector-body[^}]+translate3d\(0,-6px,0\)[^}]+opacity \.16s ease[^}]+transform \.22s ease/, "the collapsed tutorial body must match the production fade and lift");
assert.match(css, /\.tour-data-tools \{[^}]+translate3d\(calc\(100% \+ 12px\), 0, 0\)[^}]+right \.3s cubic-bezier\(\.22,1,\.36,1\)[^}]+opacity \.2s ease[^}]+transform \.24s cubic-bezier\(\.2,\.8,\.2,1\)/, "data tools must slide in with the production transition");
assert.match(css, /\.tour-data-tools \{[^}]+calc\(39% - 24px\)/, "the data tools must sit one background-grid cell from the inspector in the desktop preview");
assert.match(css, /@media \(max-width: 540px\)[\s\S]+\.tour-data-tools \{ right: calc\(46% \+ 1px\)/, "the data tools must sit close to the inspector in the mobile preview");
assert.match(css, /\.tour-data-pane, \.tour-sql-pane[^}]+flex-basis \.34s cubic-bezier\(\.22,1,\.36,1\)/, "Table data and SQL console must exchange space with production timing");
assert.doesNotMatch(css, /\.tour-data-tools[^}]+scale\(/, "data tools must not use the old non-production scale animation");
assert.match(source, /target: "maximize"[\s\S]+state: \{ dataMaximized: true \}[\s\S]+target: "minimize"[\s\S]+state: \{ dataMaximized: false \}/, "the maximize button must restore through the production restore/minimize control");
assert.match(html, /id="onboarding-dont-show"[^>]+type="checkbox"/, "the future-start opt-out is missing");
assert.match(html, /id="onboarding-back"[^>]+aria-label="Previous introduction page"/, "the back arrow needs an accessible label");
assert.match(html, /id="show-onboarding-button"/, "the introduction must be reopenable from the Help menu");
assert.match(html, /id="shutdown-button"/, "the browser shutdown control is missing");
assert.match(css, /\.onboarding-dialog[^}]+max-height:/, "the introduction must fit within the viewport");
assert.match(css, /\.onboarding-dialog \{[^}]+height: min\(720px, calc\(100vh - 32px\)\)/, "the introduction must keep one stable height across pages");
assert.match(css, /\.onboarding-panel \{[^}]+grid-template-rows: auto minmax\(0, 1fr\) auto;[^}]+height: 100%/, "the onboarding footer must stay anchored while page content scrolls");
assert.match(css, /\.onboarding-next \{[^}]+width: 80px;/, "the Next and Finish states must not move the footer controls");
assert.match(css, /@media \(max-width: 540px\)[\s\S]+\.onboarding-dialog/, "the introduction needs mobile layout rules");

const shutdownStart = source.indexOf("async function shutdownSchemii");
const shutdownEnd = source.indexOf("function schemaForStorage");
const shutdown = source.slice(shutdownStart, shutdownEnd);
assert.ok(shutdown.indexOf("await flushPendingSave()") < shutdown.indexOf('fetch("/api/shutdown"'), "shutdown must save pending edits before stopping the server");
assert.match(shutdown, /"X-Schemii-Token": postgresState\.token/, "shutdown must send the local session token");
assert.match(source, /initializeSchemaLibrary\(\)\.finally[\s\S]+initializeOnboarding\(\)/, "onboarding must initialize after the workspace");

console.log("Onboarding and browser shutdown tests passed");
