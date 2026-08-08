---
name: schema-design-layout
description: Use for adding, renaming, updating, relating, or deleting Schemii tables, columns, constraints, and relationships while preserving layout.
---

# Schema Design And Layout

- Use stable element IDs where a proposal schema requests them; names alone are not identity.
- Preserve all table positions, colors, and viewport state. Never propose regenerating, normalizing, or rearranging layout as part of a semantic change.
- Prefer PostgreSQL constraints for primary keys, foreign keys, uniqueness, checks, and nullability.
- Before risky type, nullability, default, or relationship changes, identify existing-row compatibility, table rewrite, lock, and validation concerns.
- Deletion is destructive. State what can be lost before emitting a delete proposal.
- Every write proposal requires confirmation in Schemii. Chat text is never confirmation.
- For a new example or teaching schema, use one `schema_populate` proposal containing complete tables, columns, appropriate keys, and name-based relationships. Keyless tables are valid PostgreSQL, but referenced foreign-key targets must be primary or unique. Mark each column in an intended composite primary key as `primary: true`. Do not split a coherent initial design across turns.
- If the proposal tool cannot execute, emit the same action as a final `SCHEMII_PROPOSALS:` JSON array so Schemii can validate it and render one atomic confirmation card.
