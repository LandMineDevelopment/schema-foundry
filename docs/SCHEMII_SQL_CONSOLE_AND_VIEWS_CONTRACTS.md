# Schemii SQL Console And Views Contracts

Status: SQL Console read/write execution and the live Schemii Views catalog, inspection, ordinary-view lifecycle, and materialized-view creation/recreation/deletion are implemented. Kind conversion and materialized refresh remain unsupported.

This document defines the execution, catalog, persistence, conflict, and migration boundaries for Schemii's independent SQL Console and graphical Views layer. It extends focused shared infrastructure used by Schemii and Schemer without combining their application workflows or weakening their distinct revision guards.

## Design Standard

Choose the best coherent long-term design for the product outcome, constrained by simplicity, maintainability, reuse, capability isolation, migration cost, and operational safety. A smaller diff is not preferable when it creates duplicate connection logic, incompatible contracts, or a weaker user workflow.

Share stable foundations with multiple concrete consumers. Keep Schemii schema design, write authorization, migration, and layout ownership separate from Schemer dashboard revisions, widget projections, and analytic execution policy.

## Shared Boundaries

Extend these existing modules rather than adding Console- or Views-specific copies:

- `postgres_connections.py`: profile-backed PostgreSQL connections and value conversion.
- `postgres_common.py`: validation, identifier quoting, fingerprints, and structured service errors.
- `postgres_catalog.py`: exact relation inspection, semantic columns, definitions, and relation fingerprints.
- `postgres_service.py`: public PostgreSQL service facade and transactional execution.
- `postgres_http.py`: capability-scoped `/api/postgres/...` routes and per-application policies.
- `relation_source.py`: exact persisted relation identity and semantic column snapshot.
- `shared_web/session-client.js` and `shared_web/postgres-client.js`: authenticated browser transport.
- Dedicated Schemii `preview_view_mutation()` and `apply_view_mutation()`: saved-schema-bound view DDL planning and application, isolated from general schema plans.

Do not introduce another connection implementation, SQL parser, profile store, HTTP session mechanism, migration executor, or generic application framework.

### Capability Split

Schemii mounts the shared profile, catalog, schema, read-SQL, and Console capabilities, including its write-enabled Console policy. Schemer separately mounts relation analytics and its own revision-bound policies. The two view mutation routes are implemented directly by the Schemii server and are not mounted by Schemer. There is currently no `view_actions` capability or materialized-refresh route.

The existing `POST /api/postgres/profiles/{profileId}/sql` route remains a backward-compatible, single-statement, read-only adapter over the shared executor. Existing Schemii table tools, AI actions, and Schemer analytic SQL do not have to migrate in the same release as the standalone Console. The adapter retains each application's current policy and guards; it never accepts write grants.

## Exact Target Contract

Every Console and Views request binds to:

- authenticated local HTTP session token and current server ID;
- saved `profileId` from the route;
- explicit `database` equal to the saved profile database;
- explicit `namespace` that exists on the connected database;
- connected `current_database()` equal to `database`;
- the saved PostgreSQL role, without privilege elevation.

Exact relation operations additionally carry canonical source identity:

```json
{
  "profileId": "local",
  "database": "bookstore",
  "namespace": "bookstore",
  "relation": "order_summary",
  "operation": "upsert",
  "kind": "view",
  "fingerprint": "64-lowercase-hex-characters",
  "columns": []
}
```

Unknown fields are rejected. The server re-inspects relation kind, semantic columns, and fingerprint before execution. It never substitutes an inferred profile, database, namespace, relation, or kind.

Credentials remain server-side. Browser responses, saved query history, logs, layout records, and schema records never contain passwords.

## SQL Console

### Execution Resource

Use a shared execution registry so cancellation and concurrency have one implementation. Schemii and Schemer supply different policies to it.

```text
POST   /api/postgres/profiles/{profileId}/console/executions
DELETE /api/postgres/profiles/{profileId}/console/executions/{executionId}
```

The browser creates a UUID `executionId` before POST so a parallel DELETE can cancel the active PostgreSQL connection. Each browser-local query view owns one stable `consoleId`; renaming that view does not change its identity. The POST remains open until completion and returns the final result. Registry entries are process-local, session-bound, short-lived, and removed after completion.

Request:

```json
{
  "executionId": "uuid",
  "consoleId": "uuid",
  "database": "bookstore",
  "namespace": "bookstore",
  "sql": "SELECT 1;",
  "mode": "read",
  "writeGrantId": null
}
```

`writeGrantId` must be absent or null in read mode and must be current in write mode. Unknown fields are rejected.

### Script And Transaction Semantics

- Normal Run submits the selected text when present, otherwise the top-level statement containing the caret. Run all submits the complete editor. Either action may submit 1-20 top-level PostgreSQL statements, with at most 100,000 total characters.
- Extend the existing quote/comment-aware statement scanner; do not add a second parser or infer safety from browser parsing.
- The server owns one transaction for the complete script.
- Read mode executes the complete transaction with `SET TRANSACTION READ ONLY`.
- Write mode executes the complete transaction read-write using only the saved role's permissions.
- All statements succeed and commit together, or any failure rolls back all statements.
- Explicit transaction control (`BEGIN`, `COMMIT`, `ROLLBACK`, savepoint control, and transaction mode changes) is rejected because transaction ownership is server-side.
- Commands PostgreSQL cannot execute in the server-owned transaction are unsupported and return a clear error; the server never silently changes to partial auto-commit.
- PostgreSQL functions remain capable of external or non-transactional side effects. The UI must disclose this limitation before write mode and where read queries can invoke functions.

Each statement returns one ordered result entry:

```json
{
  "executionId": "uuid",
  "target": {"profileId": "local", "database": "bookstore", "namespace": "bookstore"},
  "mode": "read",
  "committed": false,
  "statements": [
    {"index": 0, "command": "SELECT", "columns": [{"name": "value"}], "rows": [[1]], "rowCount": 1, "truncated": false, "notices": []}
  ],
  "limits": {"maxStatements": 20, "maxRowsPerResult": 500, "maxColumnsPerResult": 100, "maxResponseBytes": 1048576, "statementTimeoutMs": 30000}
}
```

For write mode, `committed` is true only after successful commit. Command results include PostgreSQL command name and affected row count. No response may imply a commit before it occurs.

The browser presents ordered statement entries as result tabs owned by the current query view. New execution results replace only unpinned tabs; pinned tabs remain browser-local reference data. Tab labels are unique within the query view and may be renamed without changing execution or authorization identity. Pinned or renamed results never authorize execution and never alter server transaction semantics. Cancellation remains attached to the active Results pane and applies only to the current registered `executionId`.

Browser-local query views are created, selected, renamed, and removed through the Console header menu. A new query view receives a new `consoleId`, begins read-only, and has no grant. Renaming preserves its `consoleId`; removing it revokes its grant and removes its browser-local SQL and results. Removing the final view creates a fresh blank read-only view.

### Write Grants

Write authorization is an ephemeral Schemii-only server resource:

```text
POST   /api/postgres/profiles/{profileId}/console/write-grants
DELETE /api/postgres/profiles/{profileId}/console/write-grants/{writeGrantId}
```

Grant creation requires the exact active query view's `consoleId`, `database`, `namespace`, and a deliberate browser confirmation flag. The returned opaque grant binds to the current HTTP session, server ID, console ID, profile ID, database, namespace, and current stored-profile fingerprint. Write mode and its grant are owned by that query view, not by a result tab or the Console workspace globally.

A grant is invalid after:

- explicit revocation when the owning query view is removed, the Console closes, or that query view's write mode is disabled;
- target or saved-profile change;
- browser refresh because the browser loses the opaque grant ID;
- HTTP session or server replacement;
- server restart because grants are process-local;
- five minutes without a write execution or fifteen minutes absolute lifetime.

The server never infers write authorization from SQL text, a browser toggle, or possession of the local session token alone.

Switching query views reflects the selected view's own authorization state. A query view without a current grant remains read-only even when another query view has write mode enabled. Normal Run and Run all use the same grant requirement; selected SQL, caret selection, pinned results, result names, query names, and saved-history entries cannot broaden a grant. The UI revokes every query-view grant on target change or Console close and attempts revocation when the page is leaving, while server expiry remains authoritative if browser delivery is interrupted.

### Limits, Cancellation, And Concurrency

- 30-second PostgreSQL statement timeout by default.
- 500 rows and 100 columns per tabular result.
- 1 MiB serialized response across all statement results.
- At most 50 notices and 8 KiB total notice text.
- One active execution per `consoleId` and four active Console executions per server process.
- Duplicate active `executionId` returns `execution_conflict`.
- Exceeding concurrency returns `execution_busy` without opening another connection.
- DELETE uses the registered connection cancellation facility, returns whether cancellation was requested, and never reports rollback complete until the POST finishes.
- Closing the browser may abort response delivery but server timeout/cancellation remains authoritative.

### Errors And Audit Metadata

Use the existing error envelope. Add focused codes:

- `write_grant_required`, `write_grant_expired`, `write_grant_target_changed`;
- `execution_conflict`, `execution_busy`, `execution_not_found`, `execution_cancelled`;
- `unsupported_transaction_control`, `script_too_large`, `too_many_statements`.

Preserve safe SQLSTATE and bounded primary PostgreSQL messages where available. Never return credentials, connection strings, server stack traces, or raw diagnostic fields.

Server logs may record execution ID, timestamp, mode, exact non-secret target identity, statement count, elapsed time, outcome, SQLSTATE, command names, and row counts. Do not log SQL text, parameters, row values, passwords, or browser-saved history.

## Views Catalog And Inspection

The live PostgreSQL catalog is authoritative. Views first derives its target only from the active saved schema record:

- `schemaId` is the active record ID;
- `profileId`, `database`, and `namespace` exactly equal `schema.postgres.sourceProfileId`, `schema.postgres.database`, and `schema.postgres.namespace`;
- `expectedSchemaRevision` is the record's current positive integer `revision`;
- `layoutToken` is the record's current 64-character layout hash.

Without that complete binding, the Views workspace does not query PostgreSQL. Browser requests also reject responses whose profile, database, namespace, relation, or kind differs from the binding/request, and generation guards discard stale catalog and descriptor responses.

### GET APIs

```text
GET /api/postgres/profiles/{profileId}/relations?database={database}&namespace={namespace}
GET /api/postgres/profiles/{profileId}/relation?database={database}&namespace={namespace}&relation={relation}&expectedKind={kind}[&expectedFingerprint={fingerprint}]
```

Both routes require the authenticated local PostgreSQL session. Each uses a repeatable-read, read-only transaction and verifies `current_database()` against `database`. The catalog response is:

```json
{
  "profileId": "local",
  "database": "bookstore",
  "namespace": "bookstore",
  "relations": [{"name": "order_summary", "kind": "view"}]
}
```

The shared catalog may contain tables, ordinary views, and materialized views; the Schemii Views browser retains only `view` and `materialized_view`. The descriptor returns exact target identity and kind plus:

- ordered semantic columns (`name`, PostgreSQL display `type`, `nullable`, `ordinal`, and advisory suggestions);
- a stable 64-character semantic fingerprint covering identity, ordered columns, catalog kind, and view definition;
- `definition`: `{status:"available", format:"query", sql}` or `{status:"unavailable", reason:"not_permitted"|"too_large"|"not_supported"}` with a 64 KiB cap;
- `owner`: an available role name or explicit `not_permitted` envelope;
- advisory current-role permissions: `canSelect`, `canAlter`, and materialized-only `canRefresh`;
- direct dependencies and dependents, each as an available envelope of exact database/namespace/relation/kind identities, capped at 500 with `truncated`;
- `materialized`: population and qualifying unique-index-based concurrent-refresh eligibility for materialized views, or `not_applicable` for other kinds;
- `columnProvenance`: always `{status:"unavailable", reason:"not_supported"}`.

`expectedKind` and optional `expectedFingerprint` cause `relation_changed` if live metadata differs. Foreign tables may occur in lineage as `foreign_table`, but the descriptor endpoint accepts only tables, views, and materialized views; the Views UI therefore displays foreign-table lineage as inspection unavailable. PostgreSQL dependency metadata is relation-level only. No output-column mappings are inferred.

The live frontend:

- lists exact live objects in a right-side sibling pane;
- applies case-insensitive typed search to already loaded cards without rerendering the workspace;
- combines search with All, Views, and Materialized kind filters;
- shows the selected view, exact output fields, direct lineage, and a compact impact summary;
- expands supported same-namespace source descriptors in place and respects reduced motion;
- opens an idempotent read-only relation inspector and disables inspection for unsupported or cross-namespace lineage;
- makes a definition read-only when it is unavailable or advisory `canAlter` is false.

## View Editing And Migration

Definition drafts remain browser-local and are not inserted into `schema.views` before preview. Duplicate regenerates the statement with the new identity. Commit means preview then apply; neither the Console nor `/plans/{id}/apply` accepts a view-mutation plan.

### Preview API

```text
POST /api/postgres/profiles/{profileId}/views/preview
```

The JSON object must contain exactly:

```json
{
  "schemaId": "schema_one",
  "expectedSchemaRevision": 7,
  "layoutToken": "64-lowercase-hex-characters",
  "database": "bookstore",
  "namespace": "bookstore",
  "relation": "order_summary",
  "expectation": {"kind": "view", "fingerprint": "64-lowercase-hex-characters"},
  "desired": {"kind": "view", "definition": "CREATE VIEW bookstore.order_summary AS SELECT 1"},
  "allowDestructive": false
}
```

For creation, `expectation` is exactly `{"absent":true}`. For replacement or deletion, it is exactly `kind` plus `fingerprint`. `operation` is `upsert` or `delete`. Upsert requires `desired` with exactly `kind` plus one non-empty, single SQL statement whose `CREATE [OR REPLACE] VIEW` or `CREATE MATERIALIZED VIEW` kind and namespace/name identity match the request. Delete omits `desired`. Unknown fields are rejected.

Before planning, the schema store checks the exact schema ID, revision, complete layout token, saved PostgreSQL target, and stable saved-view identity. Creation requires no matching saved item; replacement and deletion require exactly one matching item of the expected kind. Preview then opens a repeatable-read read-only transaction, verifies the database and live expectation, and rechecks the stored profile fingerprint after inspection. Plans are versioned, opaque, process-local, profile-bound, atomically claimed during apply, and expire after 15 minutes by default. The public plan includes the operation, reviewed steps, warnings, and whether it is destructive, but not its private saved-schema binding, profile fingerprint, or preservation manifest.

Implemented operations:

- absent ordinary view: normalized to `CREATE VIEW`;
- existing ordinary view: normalized to `CREATE OR REPLACE VIEW`;
- absent materialized view: `CREATE MATERIALIZED VIEW`;
- existing materialized view: reviewed transactional recreation preserving supported owner, ACL, relation/column comments, indexes/comments, reloptions, tablespace, access method, and populated/unpopulated intent;
- ordinary/materialized kind conversion in either direction: rejected with `view_kind_conversion_unsupported`;
- existing ordinary or materialized view deletion: reviewed non-`CASCADE` drop with exact identity/fingerprint revalidation.

PostgreSQL validates ordinary replacement output-column compatibility during apply. Preview returns a warning rather than claiming to pre-detect every output removal, rename, reorder, or type change. Generated view mutation SQL never adds `CASCADE`.

### Apply API

```text
POST /api/postgres/profiles/{profileId}/view-plans/{planId}/apply
```

The request body is exactly `{"confirmDestructive":false}` with a boolean value. Before opening PostgreSQL, the server resolves the plan, revalidates the schema revision/layout/target binding, and holds that schema's in-process lock through PostgreSQL apply and narrow saved-schema synchronization. It rejects expired/wrong-profile plans, changed profiles, and missing destructive confirmation.

Apply uses one transaction and the saved PostgreSQL role. With default service settings it applies a 5-second `lock_timeout`, a 30-second `statement_timeout`, and a transaction-scoped advisory lock keyed to the namespace. For an existing ordinary view it executes `SELECT * FROM qualified_view LIMIT 0` before catalog reinspection; this takes access-share locks on the view and referenced relations, blocks conflicting target DDL, and does not request access-exclusive locks on base relations. PostgreSQL 17 rejects `LOCK TABLE` for materialized views, so apply uses `REFRESH MATERIALIZED VIEW ... WITH NO DATA` transactionally to acquire `AccessExclusiveLock`; rollback restores the original population state if apply fails. While that lock is held, Schemii rechecks the semantic fingerprint, supported metadata fingerprint, and direct dependents. Recreation executes under the original owner and restores reviewed metadata before commit. Stored rows are discarded and repopulated when the original view was populated; unpopulated views remain unpopulated. User triggers, extra rules, security labels, invalid indexes, non-permanent storage, non-owner grant histories, unavailable/truncated lineage, and any direct dependent block recreation. Delete verifies absence after the reviewed non-`CASCADE` drop. Any pre-commit failure rolls back all steps and returns `relation_changed`, the relevant conflict, or `apply_failed`. The consumed plan is removed after a successful commit.

### Post-Commit Saved-Schema Sync

After commit, the server appends a deterministic saved item for expected-absent creation, updates the exact stable saved item for replacement, or removes that exact semantic item for deletion. It preserves unrelated views and schema content and the complete layout byte-for-byte as parsed. Deletion intentionally retains any layout object formerly associated with the removed semantic item because layout is user-owned data. Sync increments the schema revision and returns the unchanged layout token.

A post-commit identity/revision conflict returns `schemaSync.status: "conflict"`; a write failure returns `schemaSync.status: "storage_error"`. These statuses do not roll back an already committed PostgreSQL transaction. The UI says PostgreSQL committed, reloads the active schema/catalog, and never retries the plan automatically.

### Materialized Refresh

No materialized-view refresh endpoint or UI control is implemented. `canRefresh` and `concurrentRefreshEligible` are inspection metadata only; they do not authorize or initiate refresh.

## Views Layout Persistence

Keep one Schemii schema record, revision, and layout token. Do not create a second store or independent revision that could commit semantic and visual state out of order.

The implemented storage serializer normalizes saved layouts to version 2 while preserving existing extension fields:

```json
{
  "version": 2,
  "layers": {
    "tables": {"objects": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}},
    "views": {"objects": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}}
  }
}
```

Version-1 `tables` and `view` fields migrate into `layers.tables`. Existing version-2 `layers.views`, including object records, viewport, and extension fields, is preserved by browser table-layout serialization. The current live Views workspace does not position cards on a saved canvas or update the Views viewport.

All stored layout is user-owned. The schema `revision`, protocol-2 layout token, `schema_conflict`, and `layout_conflict` cover the complete version-2 layout. Either conflict requires refresh; no stale tab may overwrite either layer. View mutation preview/apply requires the complete current token, and post-commit synchronization does not regenerate, normalize, or otherwise change layout.

`schema_layout_token()` hashes the complete layout. `is_wholesale_layout_change()` checks established objects and viewports in both `layers.tables` and `layers.views`; tests cover table-only, view-only, combined, and viewport replacement attempts. Existing version-1 records remain readable, and the next browser save writes version 2 without changing the parsed table layout.

## Schemer Compatibility

- Shared catalog additions are backward-compatible and do not rewrite saved Schemer source fingerprints or column snapshots.
- The legacy read-only `/sql` route remains available with Schemer's current database, profile-fingerprint, dashboard-revision, role, and result-limit policy.
- Breaking view changes alter the existing relation fingerprint. Schemer then returns `relation_changed` and requires explicit source reselection.
- Schemer retains `dashboardId`, expected revision, widget projection, temporal manifest, and non-privileged-role guards.
- Schemii write grants are not mounted by Schemer.
- Shared execution/cancellation mechanics may be reused by Schemer only through its read-only policy.

## Tutorial v4 Coverage

The Mercury Books seed's v4 reconciliation adds four ordinary views (`book_catalog`, `order_summary`, `low_stock_books`, and `customer_order_totals`) and one materialized view (`monthly_sales`) with a qualifying unique `sales_month` index. It verifies the nine base tables, compares live definitions with temporary canonical definitions, creates missing reserved objects, and does not use `CREATE OR REPLACE`, refresh, or drop in the v4 reconciliation block. Recognized v4 objects with modified definitions are preserved; index restoration is skipped when modified `monthly_sales` is not compatible.

For an exact legacy-v3 upgrade, canonical `order_summary` with its legacy reserved comment is adopted and relabeled for v4. A legacy-v3 `order_summary` carrying that old reserved comment but a modified definition instead triggers the reserved-object collision error. This is intentional collision safety, not modified-object preservation.

## Acceptance Decisions

- Shared foundations are extended once and scoped through capabilities and policies.
- One submitted Console script is one all-or-nothing transaction.
- Write mode requires an exact ephemeral grant and selected-role permission.
- Real cancellation uses an execution registry and PostgreSQL cancellation.
- Live catalog fingerprints guard Views inspection, replacement preview, and apply; expected absence guards creation.
- View DDL uses only the dedicated Schemii view preview/apply plan resource.
- Views layout is a separate layer inside the existing versioned Schemii layout and conflict guard.
- Schemer keeps its stronger dashboard and source boundaries.
- Existing materialized changes, kind conversion, deletion, `CASCADE`, and materialized refresh are not implemented.
