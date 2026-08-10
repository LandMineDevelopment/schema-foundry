const assert = require("node:assert/strict");
const fs = require("node:fs");

const source = fs.readFileSync("src/schemii/schemer_web/app.js", "utf8");
const html = fs.readFileSync("src/schemii/schemer_web/index.html", "utf8");
const styles = fs.readFileSync("src/schemii/schemer_web/styles.css", "utf8");
const schemiiStyles = fs.readFileSync("src/schemii/web/styles.css", "utf8");
const client = fs.readFileSync("src/schemii/shared_web/postgres-client.js", "utf8");

assert.match(html, /<title>Schemer<\/title>/, "Schemer needs its own browser identity");
assert.match(html, /\/shared\/postgres-client\.js/, "Schemer must load the shared PostgreSQL browser client");
assert.match(source, /SchemiiShared\.createPostgresClient/, "Schemer must instantiate the shared PostgreSQL client");
assert.doesNotMatch(source, /\bfetch\s*\(/, "Schemer must not duplicate PostgreSQL request handling");
assert.match(source, /replaceChildren/, "connection metadata must render through DOM APIs");
assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML|eval\(/, "connection metadata must not be interpreted as code or HTML");
assert.match(source, /dragstart|dragover|dragend/, "the initial dashboard must support widget rearrangement");
assert.match(styles, /^@import url\("\/shared\/theme\.css"\);/, "Schemer must use the shared visual theme");
assert.match(schemiiStyles, /^@import url\("\/shared\/theme\.css"\);/, "Schemii must use the same visual theme");
assert.match(client, /path\.startsWith\("\/api\/postgres\/"\)/, "the shared client must restrict PostgreSQL requests to the local API");
assert.match(client, /X-Schemii-Token/, "the shared client must preserve the existing session contract");

console.log("Schemer shared connection and dashboard contracts passed");
