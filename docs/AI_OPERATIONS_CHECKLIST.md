# AI Operations Foundation Checklist

## Durable Authority

- [x] Store proposals, operations, and query-result references in application-scoped config directories.
- [x] Use inter-process locks, atomic replacement, directory synchronization, and restrictive permissions.
- [x] Hash claim and result-reservation tokens at rest.
- [x] Preserve consumed and uncertain tombstones until expiry.
- [x] Make expired claims and interrupted operations fail closed as `uncertain`.

## Server Execution

- [x] Canonicalize enabled actions before proposal issuance.
- [x] Key one durable operation to each proposal ID.
- [x] Guarantee one execution owner across concurrent requests.
- [x] Return existing operation state for duplicate execute requests.
- [x] Provide execute, status, and lost-response reconciliation routes.
- [x] Execute AI SQL only from the canonical proposal stored by the server.
- [x] Separate SQL completion from optional model analysis.
- [x] Reject privileged PostgreSQL roles and `EXPLAIN` for AI SQL.
- [x] Keep uncertain model-result delivery reserved rather than redisclosing rows.

## Browser Boundary

- [x] Remove browser claim/finalize/release coordination from active execution paths.
- [x] Require explicit confirmation before every enabled operation.
- [x] Remove session-wide AI SQL approval.
- [x] Poll a concurrently running operation instead of repeating it.
- [x] Accept only allow-listed client commands and result kinds.

## Temporarily Disabled Actions

- [x] Disable Schemii schema mutation, project creation, connection opening, and migration-preview tools.
- [x] Disable Schemer dashboard and widget mutation tools.
- [x] Keep ordinary application UI workflows available.

## Re-Enable Criteria

Each disabled action requires all of the following before its tool is restored:

- [ ] Strict server-side action normalization with unknown-field rejection.
- [ ] Application-owned execution against authoritative saved state.
- [ ] Exact revision and target binding; Schemii mutations also require layout-token binding.
- [ ] Deterministic generated IDs or another idempotent operation key.
- [ ] Atomic persistence with one resource revision increment.
- [ ] Lost-response reconciliation against exact intended state.
- [ ] An `uncertain` outcome when reconciliation cannot prove success or no effect.
- [ ] Focused concurrent-execute, restart, stale-binding, and response-loss tests.
- [ ] Byte-for-byte preservation tests for unrelated layout, viewport, widgets, and configuration.
- [ ] Updated user-facing capability documentation.

Schemii saved-schema mutation and local project-creation adapters satisfy these criteria. Connection opening, migration preview, and Schemer mutation tools remain disabled.
