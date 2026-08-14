# Sustainable Boundaries Rewrite

This document is the durable execution record for the `architecture/sustainable-boundaries` branch. Update it as work proceeds. Do not mark an item complete until implementation, focused tests, full relevant verification, and documentation are complete.

## Objective

Establish coherent, durable interfaces among the Schemii and Schemer browsers, application servers, OpenCode, server-owned persistence, and PostgreSQL. Preserve the two applications and their separate domain models while unifying authority, concurrency, recovery, and target-identity contracts.

## Governing decisions

- Keep Schemii and Schemer as separate processes and products.
- Keep schema and dashboard documents in their domain-owned stores; do not force them into a generic repository.
- PostgreSQL is authoritative for live catalog and data state.
- OpenCode remains an untrusted typed-proposal generator and never receives database credentials or execution authority.
- Browser validation is usability support, never authorization.
- Use PostgreSQL, not SQLite, if transactional server metadata is required.
- A dedicated server-metadata PostgreSQL database/service is acceptable. It must use a narrowly scoped role, versioned migrations, explicit backup/retention behavior, and must not be confused with a user-selected target database.
- Prefer one durable execution state machine for equivalent write operations.
- Persisted target identity is server-generated and cryptographic; browsers do not reconstruct authority fingerprints.
- Compatibility code may be removed when a clear migration or prototype reset path exists. Do not preserve weak boundaries solely for legacy shape compatibility.
- Never weaken layout preservation, stale-plan checks, exact source fingerprints, or PostgreSQL role inheritance.

## Required durable notes

Maintain `docs/SUSTAINABLE_BOUNDARIES_NOTES.md` throughout execution.

After every substantial milestone, record:

1. What changed and why.
2. Important contracts or schema migrations introduced.
3. Tests run and exact results.
4. Open questions, known risks, and next action.
5. Any behavior intentionally removed or migrated.

Before context compaction or handing work to another agent, update the **Current handoff** section with:

- Branch and latest commit.
- Worktree status.
- Current phase and exact next step.
- Files actively being changed.
- Invariants that must not be violated.
- Commands already run and their results.
- Blockers or decisions still needed.

## Subagent communication protocol

Every delegated task must state:

- Research-only or implementation authority.
- Exact ownership boundary and files.
- Contracts/invariants that must remain true.
- Whether schema/data migrations are permitted.
- Required tests or verification.
- Expected return format: findings, edits, risks, and next recommendation.

Agents must not duplicate another active agent's scope. Research findings that affect architecture must be summarized in `SUSTAINABLE_BOUNDARIES_NOTES.md`, not left only in ephemeral chat output.

## Phase 0 — baseline and target design

- [x] Checkpoint and push pre-rewrite work to `main` (`692e6e6`).
- [x] Create `architecture/sustainable-boundaries`.
- [x] Add durable checklist and handoff notes.
- [x] Inventory all browser/server/OpenCode/PostgreSQL routes and persisted records in a versioned interface matrix.
- [x] Define target module ownership and dependency direction.
- [x] Decide deployment topology for dedicated server-metadata PostgreSQL.
- [x] Define failure behavior when metadata PostgreSQL is unavailable.
- [x] Define migration/bootstrap/upgrade strategy and local development defaults.
- [x] Add architecture decision records for authority storage and migration execution.

## Phase 1 — transactional server authority

- [ ] Design versioned PostgreSQL schema for chats, policies, grants, proposals, operations, query-result references, and execution receipts.
- [ ] Add a narrowly scoped metadata database role and connection configuration.
- [ ] Implement idempotent metadata migrations with startup verification.
- [ ] Implement transactional chat creation/deletion and policy updates.
- [ ] Implement atomic approval-grant plus operation creation.
- [ ] Implement unique one-operation-per-proposal ownership.
- [ ] Implement durable operation lifecycle and recovery without fixed unrenewed leases.
- [ ] Implement query-result reserve/consume/release with explicit uncertain delivery semantics.
- [ ] Add cleanup and retention for expired/terminal authority records and sensitive result payloads.
- [ ] Migrate or intentionally retire JSON authority/chat records.
- [ ] Add cross-process, crash-window, restart, and unavailable-metadata-DB tests.

## Phase 2 — common chat identity for both apps

- [ ] Introduce application/resource-aware chat identity.
- [ ] Move Schemer from title-bound authority to durable chat records.
- [ ] Keep OpenCode titles display-only.
- [ ] Add one-time import or explicit retirement for legacy Schemer sessions.
- [ ] Use one server-owned cryptographic target fingerprint contract.
- [ ] Remove browser-side authority fingerprint derivation.
- [ ] Align history, activity, deletion, proposal, operation, and policy routes with chat identity.
- [ ] Restore all still-valid pending server proposals from authority records.

## Phase 3 — durable migration execution

- [ ] Bind normal migration preview to exact saved schema ID/revision/layout token and target.
- [ ] Load intended schema server-side instead of accepting an authoritative browser schema document.
- [ ] Unify normal, AI, and view migration plan state where semantics overlap.
- [ ] Add durable single-owner `ready → applying → terminal/uncertain` execution.
- [ ] Record PostgreSQL transaction evidence before mutation.
- [ ] Handle lost commit responses without claiming rollback.
- [ ] Reconcile uncertain outcomes without replay.
- [ ] Add canonical review digests and strict durable-plan validation.
- [ ] Make plan retention/redaction explicit.
- [ ] Test concurrent apply, restart, commit-response loss, stale schema, stale catalog, and reconciliation.

## Phase 4 — PostgreSQL catalog and write safety

- [ ] Run full introspection in one read-only repeatable-read snapshot.
- [ ] Reject missing namespaces distinctly from empty namespaces.
- [ ] Inventory table metadata affected by reconstruction: owner, ACLs, comments, RLS/policies, rules, replica identity, statistics, storage, tablespace/access method, publications, security labels, and extension-owned state.
- [ ] Preserve supported metadata transactionally.
- [ ] Reject reconstruction when unsupported metadata would be lost.
- [ ] Improve role capability reporting, including inheritance and `SET ROLE` ability.
- [ ] Fix post-commit local-history/receipt error handling.
- [ ] Replace weak advisory-lock keys with a collision-resistant contract.
- [ ] Add live disposable PostgreSQL tests for all risky paths and cleanup assertions.

## Phase 5 — browser/server contracts

- [ ] Standardize structured API error envelopes.
- [ ] Add conditional deletion for schemas, dashboards, and profiles.
- [ ] Add explicit Schemii conflict quarantine and recovery UX.
- [ ] Separate Schemer draft query execution from exact saved-widget execution.
- [ ] Define focused successful-response validators in shared browser clients.
- [ ] Consolidate session bootstrap through one shared client contract.
- [ ] Tighten route-family path predicates to segment boundaries/templates.
- [ ] Add behavioral browser tests for retries, conflicts, stale responses, uncertain operations, and malformed success responses.

## Phase 6 — bounds, performance, and operability

- [ ] Add per-cell, per-row, total-result, nesting, and catalog-definition limits across all data paths.
- [ ] Add global PostgreSQL execution concurrency classes and backpressure.
- [ ] Measure before deciding on connection pooling.
- [ ] Batch bounded Schemer catalog hydration to avoid N+1 connections.
- [ ] Deduplicate identical dashboard queries within one refresh generation where safe.
- [ ] Preserve separate temporal snapshots; never claim cross-window consistency.
- [ ] Add summary-list and exact-resource endpoints where full-library reloads are wasteful.
- [ ] Use distinct PostgreSQL `application_name` values for Schemii, Schemer, and metadata authority.
- [ ] Add operational health/readiness for metadata DB, target DB degradation, and OpenCode separately.

## Phase 7 — module ownership and cleanup

- [ ] Split shared AI primitive validation from Schemii and Schemer action vocabularies.
- [ ] Introduce declarative per-application tool contract registries and parity tests.
- [ ] Extract Schemii and Schemer AI executors from HTTP handlers.
- [ ] Extract explicit PostgreSQL route policies and guards from stringly mixin hooks.
- [ ] Decompose `PostgresService` internally while preserving a deliberate facade where useful.
- [ ] Remove dead compatibility functions and stale title/fingerprint paths.
- [ ] Remove false Compose coupling between Schemer and Schemii health.
- [ ] Correct documentation to exactly match implemented capabilities.

## Phase 8 — final verification and delivery

- [ ] Run focused tests for every changed boundary.
- [ ] Run complete Python and JavaScript suites.
- [ ] Run formatting/static/syntax checks and `git diff --check`.
- [ ] Run Schemii and Schemer server/API smoke checks.
- [ ] Run disposable metadata PostgreSQL bootstrap/migration/restart checks.
- [ ] Run disposable target PostgreSQL read/write/migration/reconciliation checks.
- [ ] Verify no test objects or data remain.
- [ ] Verify saved layout snapshots remain equal for every pre-existing entry touched by synchronization tests.
- [ ] Review security, permissions, retention, backup, deployment, and rollback documentation.
- [ ] Review the complete branch diff and commits against `main`.

## Completion criteria

The rewrite is complete only when:

- Equivalent operations use coherent authority and recovery state machines.
- No browser or OpenCode field establishes server authority.
- Schemer and Schemii both use application-owned chat identity.
- Normal migrations cannot apply an obsolete saved design or falsely report rollback after uncertain commit.
- Destructive reconstruction preserves all supported PostgreSQL metadata or refuses to run.
- Server metadata transitions are transactional and restart-safe in PostgreSQL.
- All result paths have explicit memory/concurrency bounds.
- Documentation and behavior agree.
- Full verification and disposable PostgreSQL checks pass.
