# Shared Resources Audit

Implementation status: completed on 2026-08-10. The original findings are
retained below as the rationale for the resulting module boundaries.

Implemented resources:

- `relation_source.py` owns the canonical persisted relation identity and
  column-snapshot contract with one trimmed 512-character type limit.
- Schemer refresh uses exact-database relation listing followed by saved-source
  verification instead of full namespace introspection.
- `postgres_http.py` mounts explicit profile, catalog, schema-design, and
  relation-query capabilities per application.
- `shared_web/session-client.js`, `postgres-client.js`, and
  `profile-manager.js` own authenticated transport and stable profile/form
  contracts.
- `shared_web/ui-components.js` owns delegated tooltips, truncation detection,
  status controls, loading controls, and details-menu behavior.
- `shared_web/theme.css` owns the common semantic control, focus, popup, dialog,
  danger, shadow, radius, and overlay tokens.
- `postgres_common.py`, `postgres_connections.py`, and `postgres_catalog.py`
  reduce the `PostgresService` facade while preserving its public API and
  instance-level call graph. Migration planning stays in the facade because it
  is Schemii-specific and has no second application consumer.
- `atomic_json.py` supplies durable same-directory JSON replacement while each
  store retains validation, locking, revision, and serialization policy.
- `server_runtime.py` supplies strict environment parsing, static-directory
  validation, HTTP lifecycle, and response-before-thread shutdown behavior.
- `tests/http_test_support.py` and direct shared JavaScript tests cover common
  server contracts, clients, profile behavior, UI behavior, and persistence.

Schemii and Schemer should consolidate shared behavior through focused modules,
not through a single common application framework. The priorities below favor
correctness, least privilege, and high-value duplication removal while keeping
application-specific workflows independent.

## Priority 0: Correctness and Security

### Canonical persisted relation-source contract

Relation-source validation is duplicated in `dashboard_store.py` and
`postgres_service.py`. The accepted PostgreSQL type-string lengths have also
drifted: dashboard validation accepts up to 512 characters while execution
validation permits only 256.

Create one parser and validator used by storage, execution, and API boundaries.
This prevents a valid saved source from later becoming unusable.

### Lightweight Schemer source verification

Schemer refresh currently invokes full schema introspection even though it only
needs to verify and list eligible relations. Add a narrowly scoped service
method that does not load Schemii-only metadata such as columns, constraints,
indexes, and comments.

This should reduce catalog load, improve refresh performance, and lower the
database permissions required by Schemer.

### Capability-scoped PostgreSQL routes

`postgres_http.py` currently exposes the union of Schemii and Schemer
capabilities to both applications. Compose routes into explicit groups:

- Profile routes
- Catalog routes
- Schema-design routes
- Relation-query routes

Each application should mount only the groups it needs.

## Priority 1: High-Value Frontend Sharing

### Shared PostgreSQL profile manager

The largest frontend duplication is profile form population, payload creation,
profile rendering, save/test/delete behavior, namespace loading, and secret
handling.

Extract the reusable state/controller and profile form behavior, potentially to
`shared_web/profile-manager.js`. Keep application-specific modal placement,
status messaging, and post-connect behavior separate.

### Authenticated JSON client

Both browser applications repeat fetch setup, JSON and error parsing, CSRF
handling, authentication-expiry behavior, and logout redirection.

Build this beneath `shared_web/postgres-client.js`, then reuse it for
non-PostgreSQL APIs. Endpoint-specific response interpretation should remain in
each application.

### Shared UI primitives

Extend the existing shared UI layer with reusable dialog, menu, form-row,
validation-message, focus-ring, and loading-button behavior. Consolidate
duplicated colors, shadows, control heights, radii, and z-index values into
`shared_web/theme.css`.

Share behavior and design tokens rather than complete page layouts.

### Delegated tooltip behavior

Replace per-control tooltip setup with one document-level controller using
`data-tooltip`. It should centrally support focus, hover, viewport clamping, and
truncated-text detection while preserving explicit accessible labels.

### Shared namespace initialization

`postgres-client.js` replaces `window.SchemiiShared`, while `ui-components.js`
merges into it. Make all shared scripts use the same merge-safe initialization
pattern to eliminate the script-order dependency.

## Priority 2: Backend Structure

### Split `PostgresService` internally

Keep `PostgresService` as the public facade, but move its implementation into
focused modules for:

- Profiles and connections
- Catalog inspection
- Schema migration
- Relation queries
- Formatting

This reduces merge contention without requiring callers to change.

### Shared atomic JSON persistence

Extract secure directory creation, temporary-file writes, `fsync`, replacement,
locking, and permission handling. Keep profile, dashboard, and schema
serialization policies separate.

### Shared server runtime

Reuse argument parsing, signal handling, shutdown, browser launch, and startup
error handling from `server.py` and `schemer_server.py`. Keep route assembly and
application state explicit in each server.

### Shared test contracts

Parameterize common profile and catalog HTTP tests against both servers. Share
PostgreSQL connection fakes, request-handler doubles, temporary-server
harnesses, and persistence-safety assertions. Add direct JavaScript tests for
`ui-components.js` and `postgres-client.js`.

## Keep Application-Specific

Do not generalize these areas now:

- Schemii canvas, layout-token conflict handling, migration planning, SQL
  editor, and inspector
- Schemer dashboard composition, widget dragging and resizing, chart rendering,
  drill-through reports, and dashboard persistence semantics
- A universal top bar, universal grid, shared application state container, or
  single combined server process
- Visually similar dialogs whose workflows and safety consequences differ

## Recommended Delivery Order

1. Fix relation-source validation drift and add focused tests.
2. Add lightweight source verification and capability-scoped routes.
3. Harden shared namespace initialization and add direct shared-JavaScript
   tests.
4. Extract the profile manager and authenticated JSON transport.
5. Consolidate visual tokens and small UI primitives.
6. Decompose `PostgresService`, persistence mechanics, and server runtime.
