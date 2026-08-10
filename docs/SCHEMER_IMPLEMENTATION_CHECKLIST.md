# Schemer Implementation Checklist

This file is the durable implementation sequence for Schemer. Use it after a lost connection, context compaction, or a new chat.

## Approval Protocol

- Do not begin an unchecked phase until the user has reviewed and explicitly approved its implementation plan.
- Before each phase, present the smallest proposed scope, affected files, data/API model, user interaction, tests, compatibility risks, and explicit non-goals.
- If discoveries materially change an approved phase, stop and request approval for the revised plan.
- Mark a phase complete only after its acceptance criteria and required verification pass.
- Keep PostgreSQL values parameterized, identifiers quoted, query execution read-only, and credentials server-side.
- Preserve user-owned dashboard layout and Schemii canvas layout during all synchronization work.

## Product Rules

- Each widget reads from exactly one PostgreSQL table, view, or materialized view.
- Schemer does not create joins. Cross-table composition belongs in PostgreSQL views managed through Schemii.
- PostgreSQL is authoritative for relation definitions and live data.
- A widget query supports zero or more dimensions and one or more measures.
- Aggregate reports support any number of group-by columns and metric columns.
- Dashboard slicers affect only widgets with explicit, type-compatible bindings.
- Drill-through details use the same source relation, slicers, widget filters, clicked dimensions, and measure-specific filters as the aggregate.
- Generated aggregation SQL, parameters, source definitions, and detail SQL remain inspectable.
- Visualization changes must not silently discard dimensions, measures, filters, formatting, or detail configuration.

## Phase 0: Shared Foundation

- [x] Create `feature/schemer-dashboard` from clean `main`.
- [x] Add a separate Schemer Python server and Docker service.
- [x] Share `PostgresService` between Schemii and Schemer.
- [x] Extract shared PostgreSQL HTTP routes for profiles, testing, namespaces, catalog reads, introspection, table previews, and read-only SQL.
- [x] Extract the shared browser PostgreSQL client.
- [x] Extract shared visual tokens and common shell styles.
- [x] Share the persistent profile volume between containers.
- [x] Add cross-process profile-store locking.
- [x] Add an initial responsive dashboard preview with functional connection selection and basic widget rearrangement.
- [x] Verify both apps expose identical saved profile identities and Schemer can execute a live read-only query.

## Phase 1: Dashboard Storage And Modes

Approval required before implementation.

- [x] Define a versioned dashboard record with stable dashboard and widget IDs.
- [x] Store dashboard title, source references, slicers, widgets, layout, and viewport independently from PostgreSQL profiles.
- [x] Add atomic, owner-only dashboard file persistence and validation.
- [x] Add dashboard create, open, rename, duplicate, archive, and delete workflows.
- [x] Add explicit View and Edit modes.
- [x] Render widgets as uniform responsive tiles; reserve create, animated center-overlap swap, keyboard reorder, duplicate, and delete actions for Edit mode.
- [x] Expand tiles from their dashboard position using their own header; split 50/50 into a filtered population table only after metric-level selection.
- [x] Add stale-write protection for concurrent browser tabs.
- [x] Add persistence, malformed-record, stale-write, and layout-preservation tests.

Acceptance criteria: dashboard layout and metadata survive restart; View mode cannot accidentally mutate layout; stale tabs cannot overwrite current records.

## Phase 2: Single-Relation Source Catalog

Approval required before implementation.

- [x] Add a relation browser for an exact profile, database, namespace, and table/view/materialized-view identity.
- [x] Return relation kind, columns, PostgreSQL types, nullability, and stable catalog fingerprint.
- [x] Enforce exactly one source relation per widget.
- [x] Reject hidden joins, cross-relation column references, and stale relation fingerprints.
- [x] Suggest likely dimension, measure, date, and identifier columns from PostgreSQL types without silently choosing them.
- [ ] Provide bounded read-only source-row preview.
- [ ] Add catalog-change and missing-column handling.

Acceptance criteria: a widget can select one verified relation and inspect its columns; no Schemer API or UI can define a join.

## Phase 3: Multi-Measure Query Model

Approval required before implementation.

- [ ] Define versioned query configuration with `dimensions[]`, `measures[]`, `filters[]`, `sort[]`, and optional limit.
- [ ] Support zero or more dimensions and one or more measures.
- [ ] Support count rows, count, count distinct, sum, average, minimum, and maximum.
- [ ] Give every measure a stable ID, label, source column, aggregation, distinct setting, null behavior, and number format.
- [ ] Allow independent display ordering for dimensions and measures.
- [ ] Generate deterministic PostgreSQL SQL from validated catalog identities.
- [ ] Quote identifiers and parameterize every filter value.
- [ ] Enforce read-only transactions, statement timeouts, result limits, and bounded responses.
- [ ] Preserve measure lineage needed by drill-through.
- [ ] Add query-generation tests for multiple dimensions, multiple measures, nulls, sorting, and invalid/stale fields.

Acceptance criteria: one query can return any configured set of group-by columns and metric columns without accepting interpolated SQL values.

## Phase 4: Aggregate Report Widget

Approval required before implementation.

- [ ] Add Aggregate Report as a first-class table widget.
- [ ] Render any number of group-by columns followed by any number of metric columns.
- [ ] Add column reorder, resize, hide/show, pinning, labels, and per-measure formatting.
- [ ] Add sorting by dimensions or measures.
- [ ] Add bounded pagination or windowing for large grouped results.
- [ ] Preserve the complete query configuration when columns are hidden.
- [ ] Make aggregate rows and metric cells eligible for drill-through.
- [ ] Defer pivots, subtotals, and grand totals unless separately approved.

Acceptance criteria: users can build and persist a grouped table such as publisher + format with orders, units, revenue, and average value metrics.

## Phase 5: Hot-Swappable Visualizations

Approval required before implementation.

- [ ] Add compact in-widget controls for KPI, bar, line, donut, and aggregate table modes.
- [ ] Add quick selectors for dimensions, visible measures, aggregation, and sort.
- [ ] Keep deeper source, filter, formatting, interaction, and detail settings in an editor drawer.
- [ ] Support multiple measures in KPI groups, grouped bars, lines, and aggregate tables.
- [ ] Require one selected measure for donut charts while retaining unshown measures.
- [ ] Offer explicit compatibility guidance when a visualization lacks required roles.
- [ ] Apply smart suggestions without silently rewriting query configuration.
- [ ] Restore all prior dimensions and measures when switching back to a compatible visualization.
- [ ] Add hot-swap round-trip tests proving no configuration loss.

Acceptance criteria: compatible visualizations switch immediately, and incompatible switches explain the missing role without deleting configuration.

## Phase 6: Dashboard Slicers

Approval required before implementation.

- [ ] Define dashboard-owned slicers with stable IDs, labels, types, values, and defaults.
- [ ] Support text/category, boolean, numeric range, date range, and relative-date slicers in the initial design.
- [ ] Add explicit slicer-to-widget column bindings.
- [ ] Validate PostgreSQL type compatibility for every binding.
- [ ] Suggest same-name compatible columns but require confirmation.
- [ ] Show exactly which widgets are affected and why others are not applicable.
- [ ] Parameterize slicer values in every widget query.
- [ ] Apply slicers consistently to charts, aggregate reports, KPIs, and detail reports.
- [ ] Add clear/reset behavior and visible active-filter state.
- [ ] Add mixed-relation and incompatible-binding tests.

Acceptance criteria: changing a slicer refreshes every explicitly bound widget and leaves unbound widgets visibly unaffected.

## Phase 7: Drill-Through Drawer And Detail Reports

Approval required before implementation.

- [ ] Add a right-side detail drawer that does not destroy dashboard context.
- [ ] Combine dashboard slicers, widget filters, clicked dimensions, clicked series, and measure-specific filters.
- [ ] Define reusable detail reports tied to the same source relation as their widgets.
- [ ] Configure detail columns, labels, formats, default sort, and row identifier.
- [ ] Add bounded pagination, sorting, searching, and column visibility.
- [ ] Make KPI, chart marks, aggregate rows, and aggregate metric cells drillable.
- [ ] Display active filter chips and matching-row count.
- [ ] Distinguish dashboard refresh time from live detail-query time.
- [ ] Add full-screen report navigation without losing the originating dashboard filters.
- [ ] Defer export and individual record panels unless separately approved.

Acceptance criteria: clicking a chart mark or aggregate cell opens the logically matching underlying rows with complete filter lineage.

## Phase 8: Query And Source Transparency

Approval required before implementation.

- [ ] Add a Data Lineage action to every widget and detail report.
- [ ] Show exact profile label, database, namespace, relation, relation kind, and fingerprint.
- [ ] Show the PostgreSQL table/view/materialized-view definition when permitted.
- [ ] Show generated aggregation SQL separately from bound parameter values.
- [ ] Show generated detail SQL and its parameter values.
- [ ] Show active slicers, widget filters, query duration, result row count, truncation, and refresh time.
- [ ] Add copy-query controls that do not include credentials.
- [ ] Add optional read-only `EXPLAIN` later only after separate approval.
- [ ] Add redaction and untrusted-definition rendering tests.

Acceptance criteria: users can explain where every displayed value came from without exposing passwords or interpolating parameter values.

## Phase 9: Schemii View Workflow

Approval required before implementation.

- [ ] Design first-class create/edit workflows for ordinary PostgreSQL views.
- [ ] Preview view SQL, resulting columns, and bounded rows read-only.
- [ ] Show dependencies on tables, views, functions, and namespaces.
- [ ] Preview `CREATE OR REPLACE VIEW` and destructive replacement implications through the migration planner.
- [ ] Detect removed, renamed, reordered, or type-changed output columns that can break Schemer widgets.
- [ ] Surface affected Schemer relation fingerprints without coupling Schemii to dashboard storage internals.
- [ ] Preserve Schemii canvas layout during view introspection and migration work.
- [ ] Plan materialized-view creation and refresh as a separately approved extension.

Acceptance criteria: users can build join-backed analytics views in Schemii, safely migrate them, and consume them in Schemer as one flat relation.

## Phase 10: Hardening And Advanced Capabilities

Approval required before each separately scoped capability.

- [ ] Query-result caching and refresh policies.
- [ ] Point-in-time consistency strategy for aggregates and details.
- [ ] Materialized-view refresh controls.
- [ ] Pivot tables, subtotals, and grand totals.
- [ ] Safe calculated and filtered measures.
- [ ] Previous-period comparisons and window calculations.
- [ ] Dashboard/report export.
- [ ] Sharing, roles, row-level access, and embedding.
- [ ] Scheduling and delivery.
- [ ] Performance budgets, cancellation, concurrency limits, and observability.

## Required Verification For Every Phase

- [ ] Focused Python and JavaScript tests for changed behavior.
- [ ] Complete Python test suite.
- [ ] Complete JavaScript test suite.
- [ ] Python compile check and both application JavaScript syntax checks.
- [ ] Compose configuration validation for affected service combinations.
- [ ] Local Schemii and Schemer server smoke tests for `/`, `/api/session`, and affected routes.
- [ ] Desktop and mobile render checks for frontend changes.
- [ ] Disposable PostgreSQL verification for catalog, query, view, or migration changes.
- [ ] `git diff --check`.
