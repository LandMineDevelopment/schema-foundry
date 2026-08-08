---
name: target-selection
description: Use for every Schemii database, namespace, saved-schema, query, or migration request to select and verify the exact target.
---

# Exact Target Selection

1. Require the exact connection `profileId` and PostgreSQL `namespace` shown as selected in Schemii.
2. Treat profile, database, and namespace as separate identities. A familiar namespace name does not identify a database.
3. If the user changes profile, database, or namespace, discard earlier target assumptions and ask them to select the intended target in the UI.
4. Never preview or apply a saved schema against an unverified target.
5. Tool output is only a proposal. Do not claim selection was verified or an action ran unless the Schemii UI reports it.
