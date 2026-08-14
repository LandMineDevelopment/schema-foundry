# Sustainable Boundaries Notes

Durable working notes for `architecture/sustainable-boundaries`. Keep this file current during implementation and before context compaction or agent handoff.

## Current handoff

- **Branch:** `architecture/sustainable-boundaries`
- **Base checkpoint:** `main` at `692e6e6`, pushed to `origin/main`
- **Current phase:** Phase 0 — baseline and target design
- **Next action:** inventory current persisted authority/plan records and design the dedicated PostgreSQL metadata schema and deployment topology
- **Active files:** `docs/SUSTAINABLE_BOUNDARIES_CHECKLIST.md`, `docs/SUSTAINABLE_BOUNDARIES_NOTES.md`
- **Worktree:** checklist/notes are newly added on the architecture branch
- **Invariants:** PostgreSQL, not SQLite, for transactional server metadata; browser and OpenCode never execute DB work; exact target/profile role; preserve schema/dashboard layout; stale conflicts fail closed; uncertain writes are never retried blindly
- **Latest verification before branch creation:** 286 Python tests passed with 5 expected skips; all JavaScript tests and syntax checks passed; `git diff --check` passed; live Schemii container was rebuilt and healthy
- **Blockers:** none

## Decisions

### 2026-08-13 — Authority persistence technology

The user explicitly rejected SQLite. If transactional persistence is required, use a PostgreSQL instance/database dedicated to server metadata. This database is application infrastructure and must remain distinct from user-selected target databases.

Questions the implementation must answer without weakening fail-closed behavior:

- Is metadata PostgreSQL a dedicated Compose service or a dedicated database/role in an included service?
- How do UI-only/external-target launch modes obtain metadata storage?
- What remains available when metadata PostgreSQL is down?
- How are bootstrap migrations versioned and serialized?
- What backup/retention expectations apply to chats, proposals, and receipts?

### 2026-08-13 — Rewrite freedom

This is a prototype architecture branch. Prefer the strongest coherent design over compatibility-only indirection. Preserve user data or provide an explicit migration/reset path, but do not retain weak title-bound or multi-file authority solely to minimize diffs.

## Baseline findings

- Strong core trust model: OpenCode proposes; server authorizes and executes; PostgreSQL owns live truth; domain stores own intended state and layout.
- Highest-risk inconsistencies: non-transactional authority transitions, Schemer title-bound chats, normal migration uncertainty/binding, unsafe table reconstruction metadata loss, missing delete preconditions, and unbounded connection/result paths.
- Appropriate shared boundaries: local HTTP/session, profile/connection/catalog mechanics, relation source, widget query compiler, atomic persistence primitives, OpenCode transport, authority concepts, and shared browser shells.
- Domain boundaries to preserve: Schemii schema/migration semantics and Schemer dashboard/widget/query semantics.

## Milestone log

### Baseline checkpoint

- Committed all previous work on `main` as `692e6e6 Rework AI authority and PostgreSQL actions`.
- Pushed `main` to `origin/main`.
- Created `architecture/sustainable-boundaries`.
- Added this durable checklist and handoff protocol.

## Verification log

- Pre-branch full Python: 286 passed, 5 skipped.
- Pre-branch all `tests/test_*.js`: passed.
- Pre-branch Python compile, JavaScript syntax, and `git diff --check`: passed.
- Live `schemii-schemii-1`: rebuilt from checkpoint worktree and healthy on `127.0.0.1:8080`.

## Open risks

- A dedicated metadata database adds startup, migration, backup, and failure-mode responsibilities.
- Multiple app processes need one migration lock and application-scoped data isolation.
- Metadata DB outage must not accidentally fall back to weaker JSON authority.
- Prototype migration from existing JSON authority/chat state needs an explicit decision: import, archive, or reset.
- Normal migration and AI migration consolidation must preserve layout synchronization and stale-catalog guarantees.
