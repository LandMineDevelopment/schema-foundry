# Sustainable Boundaries Interface Matrix

This is the Phase 0 inventory of authority-relevant persistence and wire contracts. It records migration disposition for the architecture rewrite.

## Persistence

| Current record | Location | Current owner | Rewrite disposition |
| --- | --- | --- | --- |
| PostgreSQL profiles | `postgres_profiles.json` | Shared profile service | Preserve; version and migrate losslessly. Keep credentials outside browser/OpenCode. |
| Migration history | `migration_history.json` | PostgreSQL service | Preserve as non-authoritative audit history; move to metadata PostgreSQL later. |
| Schemii chats/policies/grants | `ai_chats/v1/*.json` | Schemii | Archive executable authority; optionally import validated chats as read-only history with grants removed. |
| AI proposals | `ai_authority/v1/{app}/proposals/*.json` | Shared authority | Archive/reset active authority. Do not infer atomic state across files. |
| AI operations | `ai_authority/v1/{app}/operations/*.json` | Shared authority | Archive/reset; reconcile only through an explicit offline recovery tool and domain receipts. |
| AI query results | `ai_authority/v1/{app}/results/*.json` | Shared authority | Do not import payloads/reservations; expire and archive metadata only if needed. |
| AI migration/write plans | `ai_migration_plans/*.json` | PostgreSQL service | Archive/reset active plans; do not replay. |
| Normal migration/view plans | Process memory | PostgreSQL service | Reset; replaced by durable plan store. |
| Console grants/executions | Process memory | Console service | Reset by design. |
| Schemii schemas | schema directory | Schemii domain store | Preserve exactly, including unknown fields, semantic IDs, receipts, and complete layout. |
| Schemer dashboards | dashboard directory | Schemer domain store | Preserve exactly, including widget order, source snapshots, queries, viewport, and layouts. |
| OpenCode sessions/credentials | OpenCode volumes | OpenCode | Preserve opaque upstream data. Store only verified external session references in metadata DB. |

## AI wire contracts

Current shared routes include status/auth, session creation/list/history/activity/delete, messages, proposal claim/finalize/release/execute/reconcile, operation status, and Schemii policy GET/PUT.

Target changes:

- Application chat ID becomes distinct from external OpenCode session ID.
- Both applications use durable chat ownership; titles are display-only.
- Browser does not resend resource, target, capability, or policy authority after chat creation.
- Legacy claim/finalize/release routes are removed after consumers migrate to operation execution.
- Query-result delivery adds explicit pre-dispatch reservation and post-dispatch uncertain states.
- Pending valid proposals are restored from metadata authority, never reconstructed from model history.

## Migration wire contracts

Normal preview accepts the exact saved schema ID, revision, layout token, namespace, and destructive-preview choice; the server loads the desired schema. Normal, view, and overlapping AI writes persist UUID plans in metadata PostgreSQL. Apply accepts only `reviewDigest` and `confirmDestructive`; resource and target authority comes from the durable plan.

Target contract:

```json
{
  "schemaId": "schema_...",
  "expectedRevision": 7,
  "layoutToken": "...",
  "profileId": "pg_...",
  "database": "organization",
  "namespace": "public",
  "allowDestructive": false
}
```

The server loads intended state, persists a canonical reviewed plan, and returns `planId`, `reviewDigest`, and bounded review data. Apply submits only plan identity, matching digest, and destructive confirmation. Status/reconcile routes expose the one durable execution.

Plan status is available at `GET /api/postgres/migration-plans/{planId}/status`; execution status and explicit reconciliation use `GET /api/postgres/migration-executions/{executionId}/status` and `POST /api/postgres/migration-executions/{executionId}/reconcile`. Reconcile has an empty JSON body and checks `pg_xact_status` without replaying SQL. Interrupted `applying` records are first durably promoted to reconcile-only `uncertain`; committed XIDs require the persisted intended result for automatic success, otherwise status remains `manual_required`.

## Resource deletion

Delete contracts use optimistic preconditions:

- Schema: revision plus layout token.
- Dashboard: revision.
- Profile: context fingerprint plus server-generated dependency impact.

Schema deletion sends `expectedRevision` and `layoutToken`; dashboard deletion sends `expectedRevision`. Profile deletion first fetches `GET /api/postgres/profiles/{id}/deletion-impact`, reviews the server-visible schemas, dashboards, active chats, plans, and operations, then sends the returned `profileFingerprint` and `impactFingerprint` in the `DELETE` body. Either digest changing produces a stale conflict. Profile deletion never deletes dependent resources.

## Schemer query execution

The `relation/query` and `relation/detail` routes remain caller-structured draft execution with optional dashboard revision context. `saved-widgets/aggregate` and `saved-widgets/detail` require dashboard ID, revision, and widget ID; the server loads the saved source, structured query, detail configuration, and visualization projection. Temporal execution retains its exact saved line-widget guard.

Target distinction:

- Draft relation execution remains caller-structured and target-verified.
- Saved-widget execution requires dashboard/widget identity and server reconstruction.
- Documentation and route names do not imply saved-widget authority for draft execution.

## HTTP errors and browser contracts

Every API failure, including unknown routes and legacy handler failures, is normalized to `{ "error": { "code": "...", "message": "...", "retryable": false, "details": {} } }`; `retryable` and `details` are omitted when not applicable. Unexpected failures use generic text rather than exception or credential material. Shared browser validators reject malformed session, profile, catalog, plan, operation, schema, dashboard, deletion-impact, aggregate, and detail successes. Session bootstrap is single-flight, aborting one waiter does not cancel other waiters, and an invalid session response is retried at most once without allowing stale responses to clear a newer token.
