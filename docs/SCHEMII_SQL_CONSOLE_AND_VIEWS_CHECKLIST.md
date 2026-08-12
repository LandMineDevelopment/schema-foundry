# Schemii SQL Console And Views Checklist

This checklist governs the UI-first design and later implementation of two Schemii features: an independent raw SQL console and first-class PostgreSQL view/materialized-view management.

## Working Agreement

- [x] Create `feature/schemii-sql-console-views` from clean `main`.
- [x] Replace the completed Schemer implementation checklist with this focused roadmap.
- [ ] Obtain explicit approval before starting each phase below.
- [x] During look-and-feel phases, use static or browser-local prototype state only; do not add execution or persistence APIs.
- [x] Use browser render checks only while iterating on visual prototypes; defer test creation and test-suite execution until an explicit verification milestone.
- [ ] Run the complete required verification before completing each backend phase and before merging the branch.
- [x] Preserve saved canvas layout, profiles, database data, and unrelated user-owned state throughout implementation.

## Product Decisions To Lock

- [ ] Approve where the SQL Console entry appears in the tool rail and how its independent workspace opens/closes.
- [ ] Approve the console header, target identity, editor, result, error, history, and empty states on desktop and mobile.
- [ ] Approve the write-mode toggle placement, wording, warning color, and confirmation interaction.
- [x] Decide whether one Run action may contain multiple SQL statements. Decision: allow up to 20 top-level statements.
- [x] Decide whether successful write statements auto-commit individually or run as one submitted transaction. Decision: one submitted script is one all-or-nothing server-owned transaction.
- [ ] Approve the Tables / Views layer switch and whether each layer keeps an independent viewport.
- [ ] Approve ordinary-view and materialized-view card treatments, dependency lines, empty states, and editor layouts. Direction selected: lineage-focused workspace with a searchable right-side sibling pane; remaining states still require review.
- [ ] Approve how destructive materialized-view replacement and refresh actions are presented.

## Phase 1: SQL Console Look And Feel

Approval required before application code changes.

- [x] Add a dedicated SQL Console tool that is not attached to a selected table, inspector, or table-data pane.
- [x] Prototype a full workspace with an exact profile, database, and namespace identity in its header.
- [x] Prototype a raw SQL editor with Run, cancel, clear, and copy controls.
- [x] Prototype separate result-table, command-summary, loading, empty, and PostgreSQL error states.
- [x] Keep write mode off by default and visually distinguish the read-only state.
- [x] Add a write-mode toggle that requires deliberate confirmation before enabling.
- [x] Show a persistent high-visibility warning in the console header while write mode is enabled.
- [x] Show that write mode resets when the target changes, the console closes, or the page reloads.
- [x] Validate keyboard flow, focus treatment, scrolling, resizing, and responsive/mobile behavior.
- [x] Use synthetic local data only; do not call PostgreSQL or save console state in this phase.

Acceptance criteria: the user approves the complete console workflow and all visible states before API or execution work begins.

## Phase 2: Views Layer Look And Feel

Approval required before application code changes.

- [x] Prototype a clear Tables / Views layer switch outside the existing table canvas.
- [x] Keep the existing table graphical view unchanged while the Views layer is active.
- [ ] Give the Views layer its own cards, dependency connectors, viewport, zoom, pan, selection, and empty state.
- [x] Visually distinguish ordinary views from materialized views without relying on color alone.
- [ ] Prototype create, inspect, edit, duplicate, and delete entry points for ordinary views.
- [ ] Prototype create, inspect, replace, refresh, and delete entry points for materialized views.
- [ ] Prototype a view editor with name, namespace, SQL definition, output-column snapshot, dependencies, and bounded row preview.
- [ ] Show read-only definitions when ownership or PostgreSQL permissions do not permit editing.
- [ ] Show stale-definition, dependency, validation, destructive-replacement, and migration-preview states.
- [ ] Validate desktop and mobile behavior with dense and empty catalogs.
- [x] Use synthetic local data only; do not introspect, migrate, refresh, or persist views in this phase.

Selected direction: concept B's selected-view lineage and raw-definition focus, with the relation catalog/search hidden in a right-side sibling pane until requested. Browser render checks cover desktop and mobile layout, pane open/close, filtering, selection, and ordinary/materialized editor templates.

Acceptance criteria: the user approves layer navigation, card language, dependency presentation, and all editor states before catalog or migration work begins.

## Phase 3: Contracts And Safety Design

Approval required before backend implementation.

- [x] Define exact profile, database, namespace, role, and session binding for every console and view request.
- [x] Keep console execution server-side and credentials out of browser responses and saved history.
- [x] Enforce read-only PostgreSQL transactions while write mode is off, regardless of browser state.
- [x] Require an explicit write-enabled request contract while write mode is on; never infer write permission from SQL text alone.
- [x] Reset write authorization on target changes, console close, refresh, server restart, and session replacement.
- [x] Define statement timeout, cancellation, result-size, response-size, and concurrent-execution limits.
- [x] Define transaction, auto-commit, multi-statement, notices, row-count, and partial-failure semantics.
- [x] Rely on the selected PostgreSQL role's permissions; do not elevate privileges for the console.
- [x] Define safe audit metadata without recording credentials or sensitive SQL parameters unintentionally.
- [x] Define versioned saved records for view-layer layout and viewport state, separate from the table layer.
- [x] Treat the live PostgreSQL catalog as authoritative for view kind, definition, dependencies, ownership, and output columns.
- [x] Define stale-catalog fingerprints and conflict responses for view edits and materialized-view actions.
- [x] Route view changes through migration preview and confirmation rather than ad hoc DDL execution.

Contract source of truth: `docs/SCHEMII_SQL_CONSOLE_AND_VIEWS_CONTRACTS.md`.

Acceptance criteria: request contracts, transaction boundaries, permission behavior, destructive operations, and persisted layout ownership are documented and approved.

## Phase 4: SQL Console Backend

Approval required before implementation.

- [x] Add capability-scoped SQL console routes using the shared PostgreSQL profile and session infrastructure.
- [x] Verify the connected database before every execution.
- [x] Execute with read-only transaction enforcement unless the request carries current write authorization.
- [x] Allow PostgreSQL statements permitted by the selected role while write mode is explicitly enabled, including insert, update, delete, DDL, and function calls.
- [x] Implement cancellation, timeouts, bounded tabular results, command summaries, notices, and PostgreSQL error details.
- [x] Ensure failed multi-statement or transactional submissions follow the approved rollback semantics.
- [x] Invalidate or recheck linked catalog fingerprints after schema-changing statements.
- [x] Add focused service, route, authorization, transaction, timeout, cancellation, and browser tests for the read-only execution slice.
- [x] Run selected SQL or the caret statement by default, provide explicit Run all, and present ordered results as pinnable browser-local tabs.
- [x] Keep browser-local query views independently named and removable, with stable per-view `consoleId` values and uniquely named, renameable result tabs.
- [x] Make write mode and its grant per query view; new views start read-only, rename preserves authorization identity, and removal revokes the owning grant.
- [x] Revoke every query-view grant on target change or Console close, and ensure switching views cannot reuse another view's grant.
- [x] Apply the same current-grant requirement to selection/caret Run and Run all; pinned results, renamed tabs, query names, and history must not affect authorization.
- [x] Verify writes and rollback behavior against disposable PostgreSQL data and remove all test objects afterward.

Acceptance criteria: read-only mode rejects writes server-side; write mode executes only with explicit current authorization and selected-role permission; failures cannot leave an ambiguous transaction state.

## Phase 5: View And Materialized-View Backend

Approval required before implementation.

- [ ] Extend catalog introspection for ordinary views and materialized views, including definitions, ownership, output columns, dependencies, and stable fingerprints.
- [ ] Add bounded read-only preview for an exact view identity.
- [ ] Add versioned persistence for Views-layer positions and viewport without changing table-layer layout.
- [ ] Preserve established view-card layout across introspection refreshes.
- [ ] Add ordinary-view create and `CREATE OR REPLACE VIEW` migration planning.
- [ ] Detect output-column removal, rename, reorder, and type changes before replacement.
- [ ] Model materialized-view changes as explicit destructive recreation when PostgreSQL cannot replace them in place.
- [ ] Add explicit materialized-view refresh controls with permission, lock, and duration warnings.
- [ ] Show dependencies and affected objects before destructive or cascading operations.
- [ ] Require stale-fingerprint revalidation before preview and apply.
- [ ] Keep migration apply transactional wherever PostgreSQL permits and surface non-transactional limitations explicitly.
- [ ] Add catalog, migration, dependency, stale-state, permission, layout-preservation, and disposable-PostgreSQL tests.

Acceptance criteria: users can safely create, inspect, edit, preview, and apply ordinary-view changes; materialized-view recreation and refresh are explicit, permission-bound, and never presented as harmless replacement.

## Phase 6: Integration And Delivery

Approval required before final integration.

- [ ] Reconcile SQL-console schema changes with Schemii drift detection and hard-refresh requirements.
- [ ] Verify Views-layer changes remain compatible with Schemer relation fingerprints and require explicit source reselection after breaking catalog changes.
- [ ] Add documentation for write-mode risk, role permissions, transaction behavior, view migration semantics, and materialized-view locks.
- [ ] Complete accessibility, keyboard, reduced-motion, desktop, and mobile verification.
- [ ] Run focused Python and JavaScript tests.
- [ ] Run the complete Python and JavaScript suites.
- [ ] Run Python compilation, browser JavaScript syntax checks, and `git diff --check`.
- [ ] Smoke-test `/`, `/api/session`, and all affected routes on local servers.
- [ ] Verify read-only and write-enabled behavior against disposable PostgreSQL targets.
- [ ] Compare table-layer and view-layer layout snapshots before and after synchronization.
- [ ] Confirm no test objects, test rows, credentials, runtime profiles, or generated data remain.

Acceptance criteria: both approved features are documented, verified end to end, safe under stale state and restricted permissions, and ready for merge without altering unrelated user-owned data.
