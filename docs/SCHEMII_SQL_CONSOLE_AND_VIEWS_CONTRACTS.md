# Schemii SQL Console And Views Contracts

Status: Phase 3 design approved. Phase 4 SQL Console read and write execution implemented; Views backend remains approval-gated.

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
- Existing Schemii migration `preview()` and `apply()`: all view DDL planning and application.

Do not introduce another connection implementation, SQL parser, profile store, HTTP session mechanism, migration executor, or generic application framework.

### Capability Split

The current relation-query capability is too broad for Schemii Views. Split the safe exact-source operations from Schemer analytics:

- `relation_read`: relation verification and bounded row preview for tables, views, and materialized views.
- `relation_query`: Schemer aggregate, detail, and temporal execution.
- `console`: shared execution registry and read/write policy hooks.
- `view_actions`: Schemii-only materialized-view refresh and future narrowly scoped view operations.

Schemii mounts `relation_read`, `console`, schema migration, catalog, and profiles. Schemer mounts `relation_read`, `relation_query`, its read-only console policy, catalog, and profiles. Schemer does not gain Schemii migration or write capabilities.

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

The live PostgreSQL catalog is authoritative for relation kind, definition, ownership, output columns, dependencies, and materialized status. Extend the shared catalog result additively with:

- owner role name and whether the current role can alter or refresh;
- ordered direct dependencies and dependents with exact identity and kind;
- bounded definition availability and existing 64 KiB definition cap;
- materialized population status and concurrent-refresh eligibility when PostgreSQL exposes it;
- the existing stable semantic fingerprint.

Permission-denied metadata is represented as unavailable with a reason, not fabricated or silently omitted. Catalog and preview operations are read-only and bounded.

Phase 5A implements inspection only through the existing shared `/relations` and `/relation` routes. Inspection uses one repeatable-read, read-only snapshot bound to the resolved relation OID. It adds owner and current-role permissions, including PostgreSQL 17 `MAINTAIN` refresh permission, non-recursive direct dependencies and dependents (each deterministically limited to 500), materialized population and concurrent-refresh eligibility, and an explicit unsupported column-provenance envelope. These operational fields and transient live OIDs are not part of the semantic relation fingerprint. This phase does not refresh relations or write layout or schema records.

The approved frontend surfaces these contracts as follows:

- Browse Views lists exact live objects.
- Relations mode navigates upstream, selected, and downstream objects.
- Column Flow shows server-derived column provenance when available and an explicit unavailable state otherwise.
- Impact mode summarizes affected relations from the verified dependency graph.
- The right inspector displays the exact catalog snapshot and becomes read-only when permissions do not allow changes.

## View Editing And Migration

An editable draft is design state, not live catalog state. It carries the exact source fingerprint from which editing began. Save draft persists through the normal schema-record revision guard. Commit changes means migration preview, never direct DDL.

Workflow:

1. Re-inspect the exact relation and compare the expected relation fingerprint.
2. Return `relation_changed` and require refresh when kind, columns, or definition changed.
3. Build the desired Schemii schema record with the edited full definition.
4. Call the existing migration `preview()` with exact profile, database, namespace, desired schema, and destructive choice.
5. Present generated SQL, warnings, dependencies, locks, unsupported operations, and destructive steps.
6. Apply only the opaque current `planId` through existing transactional `apply()`.
7. Recheck stored profile fingerprint and live schema fingerprint at apply; return `profile_changed` or `stale_plan` on mismatch.

Ordinary views use existing `CREATE OR REPLACE VIEW` planning when PostgreSQL permits it. Output removal, rename, reorder, or type changes must be identified before replacement.

Materialized view definition changes are explicit destructive recreation where replacement is unavailable. They require destructive preview selection and apply confirmation. No UI labels recreation as an ordinary save.

### Materialized Refresh

Refresh is separate from migration apply:

```text
POST /api/postgres/profiles/{profileId}/relation/refresh
```

The request carries exact canonical source identity, `concurrently`, and an explicit confirmation. The server rechecks database, kind, fingerprint, role permission, population state, and concurrent-refresh eligibility immediately before execution. It uses the shared execution registry for timeout, cancellation, concurrency, and result status.

Refresh never changes saved widget or view definitions. Standard refresh warns that reads may block; concurrent refresh explains its PostgreSQL unique-index requirement and longer resource use.

## Views Layout Persistence

Keep one Schemii schema record, revision, and layout token. Do not create a second store or independent revision that could commit semantic and visual state out of order.

Upgrade `schema.layout` to version 2:

```json
{
  "version": 2,
  "layers": {
    "tables": {"objects": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}},
    "views": {"objects": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}}
  }
}
```

Version-1 `tables` and `view` fields migrate losslessly into `layers.tables`. View objects use stable saved Schemii view IDs as keys and retain exact namespace/name/kind/live OID metadata for refresh matching. Match existing objects first by live OID, then exact namespace/name/kind. A relation fingerprint is not a layout key because semantic edits must not discard position.

Both layers' positions, colors, and viewport are user-owned. Introspection updates semantic content without regenerating established layout. The existing schema `revision`, protocol-2 layout token, `schema_conflict`, and `layout_conflict` cover the complete version-2 layout. Either conflict requires refresh; no stale tab may overwrite either layer.

`schema_layout_token()` continues hashing the complete layout and therefore naturally covers version 2. `is_wholesale_layout_change()` must be upgraded to inspect established objects in both `layers.tables` and `layers.views`, with tests proving that table-only, view-only, and combined layout replacement attempts cannot bypass the current token guard. Existing version-1 records remain readable and writable during migration; the first successful save normalizes them to version 2 without changing parsed table layout.

## Schemer Compatibility

- Shared catalog additions are backward-compatible and do not rewrite saved Schemer source fingerprints or column snapshots.
- The legacy read-only `/sql` route remains available with Schemer's current database, profile-fingerprint, dashboard-revision, role, and result-limit policy.
- Breaking view changes alter the existing relation fingerprint. Schemer then returns `relation_changed` and requires explicit source reselection.
- Schemer retains `dashboardId`, expected revision, widget projection, temporal manifest, and non-privileged-role guards.
- Schemii write grants are not mounted by Schemer.
- Shared execution/cancellation mechanics may be reused by Schemer only through its read-only policy.

## Frontend Inspection Map

Phase 3 adds no runtime frontend behavior. The current browser-local prototype at `http://127.0.0.1:8080/` demonstrates the workflows governed by these contracts:

1. SQL Console target header maps to exact target binding.
2. Write toggle maps to ephemeral write grants.
3. Run/Cancel maps to execution registry and cancellation.
4. Views right inspector maps to exact relation catalog snapshots.
5. Save draft maps to schema revision persistence.
6. Commit changes maps to migration preview/apply.
7. Relations, Column Flow, and Impact map to dependency and provenance catalog data.

Backend phases must make these states live without changing their safety meaning.

## Acceptance Decisions

- Shared foundations are extended once and scoped through capabilities and policies.
- One submitted Console script is one all-or-nothing transaction.
- Write mode requires an exact ephemeral grant and selected-role permission.
- Real cancellation uses an execution registry and PostgreSQL cancellation.
- Live catalog fingerprints guard Views inspection, editing, preview, refresh, and apply.
- View DDL uses existing migration preview/apply only.
- Views layout is a separate layer inside the existing versioned Schemii layout and conflict guard.
- Schemer keeps its stronger dashboard and source boundaries.
