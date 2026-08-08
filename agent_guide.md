# AI Agent Guide

## Scope

This repository is a standalone PostgreSQL schema design, introspection, migration-preview, and migration-apply application. Keep changes focused on generic PostgreSQL behavior, saved schema records, browser layout, tests, and project documentation.

For installation, launch modes, Docker networking, persistent-volume safety, and setup verification, follow `docs/AI_AGENT_SETUP.md`.

## Sources Of Truth

Use this order when behavior differs:

1. The user's explicit request.
2. The live PostgreSQL catalog for current database state.
3. The saved schema record selected for that exact profile, database, and namespace.
4. Application code and tests.
5. `README.md` for operating guidance.

Never preview or apply a saved schema against an unverified database or namespace.

## Working Method

1. Inspect the relevant source, tests, saved schema, and `git status` before proposing changes.
2. State the smallest correct implementation and identify data, compatibility, locking, permission, and destructive-operation risks.
3. Ask before making a destructive schema or data change when intent is not explicit.
4. Implement the approved scope without modifying unrelated user changes.
5. Add or update focused tests for behavior changes.
6. Update documentation in the same change when setup, configuration, API behavior, migration semantics, or verification changes.
7. Run focused checks, the complete test suite, and `git diff --check` before delivery.

## PostgreSQL Design

- Treat PostgreSQL as authoritative for the live catalog.
- Prefer declarative primary keys, foreign keys, unique constraints, checks, and indexes over application-only validation.
- Quote identifiers and parameterize values. Do not interpolate user values into SQL.
- Preserve exact namespace and object ownership relationships during introspection and migration planning.
- Account for existing rows before type changes, `NOT NULL` additions, table rewrites, or constraint validation.
- Use explicit source and target time zones for timestamp conversions.
- Keep introspection and data-preview operations read-only.
- Require narrowly scoped database roles rather than broad administrative credentials.

## Layout Preservation

Canvas positions, colors, and viewport state are user-owned data. Introspection may update semantic schema content but must not regenerate or normalize established layout.

For any generated schema JSON write or introspection synchronization, load and follow `.opencode/skills/preserve-foundry-layout/SKILL.md`. Resolve the schema directory from `SCHEMA_FOUNDRY_SCHEMA_DIR`, falling back to `~/.local/share/schema-foundry/schemas`; do not assume schemas live inside the repository.

Treat `layout_conflict` as a hard-refresh requirement. Never bypass the layout token guard or use a stale browser tab to overwrite a current layout.

## Safe Migrations

- Identify the exact profile, database, namespace, and saved schema before preview.
- Review generated SQL, warnings, unsupported objects, data movement, locks, and destructive steps.
- Require an explicit destructive preview choice and apply confirmation for destructive plans.
- Re-preview after any design, profile, or live catalog change.
- Keep apply transactional and preserve stale-plan fingerprint checks.
- Verify rollback behavior for failed steps and test risky changes on disposable data first.
- Require zero unexpected steps and warnings when a saved schema is expected to match PostgreSQL.

## Verification

Run at least:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src
node --check src/schema_foundry/web/app.js
git diff --check
```

For server or API changes, also start one local server, fetch `/`, `/api/session`, and the affected API routes, then stop it. For PostgreSQL changes, verify against a disposable target and confirm no test objects or data remain.

For schema-file synchronization, compare parsed layout snapshots before and after the write and require equality for every pre-existing layout entry.

## Documentation And Commits

- Keep `README.md` aligned with current setup, environment variables, storage, API behavior, and safety guarantees.
- Do not claim planned behavior is implemented.
- Keep commits small and focused. Do not include secrets, local profiles, migration history, caches, virtual environments, or runtime schema data.
- Commit or push only when the user explicitly requests it.
- Before committing, inspect `git status`, the intended diff, recent commit style, and staged content; stage only intended files.
