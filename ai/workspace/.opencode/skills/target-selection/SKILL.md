---
name: target-selection
description: Use for every Schemii database, namespace, saved-schema, query, or migration request to select and verify the exact target.
---

# Exact Target Selection

Project creation proposals are temporarily unavailable. Direct creation requests to Schemii's normal project controls.

1. Use an exact listed connection `profileId`. If it is not selected yet, propose opening it with `schema_connection_open`; Schemii must confirm and verify it before any live action.
2. Treat profile, database, and namespace as separate identities. A familiar namespace name does not identify a database.
3. If the user changes profile, database, or namespace, discard earlier target assumptions and ask them to select the intended target in the UI or approve an exact connection-open proposal.
4. Never preview or apply a saved schema against an unverified target.
5. Tool output is only a proposal. Do not claim selection was verified or an action ran unless the Schemii UI reports it.
