---
name: schema-foundry-help
description: Use for help with Schema Foundry concepts, workflow, saved schemas, introspection, design, query preview, migration preview, and confirmations.
---

# Schema Foundry Help

Schema Foundry designs saved PostgreSQL schemas, introspects live catalogs, previews read-only data queries, and previews or applies migrations. Explain workflows using these boundaries:

- PostgreSQL is authoritative for current live database state.
- The saved schema selected for the exact profile, database, and namespace is authoritative for intended state.
- Introspection may refresh semantic content but must preserve user-owned canvas layout.
- A tool call emits an inert action proposal for the application. The application validates target state and asks for any required approval or confirmation.
- Chat responses and tool output do not prove that an action completed.
- Default UI mode has no OpenCode sidecar and makes no OpenCode calls. AI launch modes explicitly add the sidecar.

When help turns into an action request, load the relevant safety skill and use the narrow proposal tool.
