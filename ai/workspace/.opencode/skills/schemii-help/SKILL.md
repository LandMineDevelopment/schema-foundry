---
name: schemii-help
description: Use for help with Schemii concepts, workflow, saved schemas, introspection, design, query preview, migration preview, and confirmations.
---

# Schemii Help

Schemii designs saved PostgreSQL schemas, introspects live catalogs, previews read-only data queries, and previews or applies migrations. Explain workflows using these boundaries:

- PostgreSQL is authoritative for current live database state.
- The saved schema selected for the exact profile, database, and namespace is authoritative for intended state.
- Introspection may refresh semantic content but must preserve user-owned canvas layout.
- A tool call emits an inert action proposal for the application. The application validates target state and asks for any required approval or confirmation.
- Project create/open and saved-connection open operations use logical IDs and confirmed Schemii UI actions; they never grant filesystem or credential access.
- Schema mutation and project-creation proposals are temporarily unavailable; direct those requests to Schemii's normal UI.
- Chat responses and tool output do not prove that an action completed.
- The no-argument launcher uses `ai-docker-db` and includes OpenCode. Explicit `ui`, `local-db`, and `docker-db` modes omit the sidecar.

When help turns into an action request, load the relevant safety skill and use the narrow proposal tool.
