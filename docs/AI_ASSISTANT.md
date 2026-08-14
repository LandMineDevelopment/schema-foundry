# Embedded AI Assistant

Schemii's default launcher runs a private, pinned OpenCode sidecar that provides model discovery, provider authentication, chat sessions, skills, and explicit proposal tools. Schemer can join that same sidecar through `compose.schemer.ai.yaml`. The sidecar starts with the default `ai-docker-db` stack, but no model request is made until the user sends a chat message. Explicit `ui`, `local-db`, and `docker-db` modes omit OpenCode.

## Start An AI Mode

The default complete stack is:

```bash
./start.sh
```

UI and AI, without PostgreSQL:

```bash
./start.sh ai
```

AI with PostgreSQL on Linux host loopback:

```bash
./start.sh ai-local-db
```

AI with the included PostgreSQL container:

```bash
./start.sh ai-docker-db
```

Windows PowerShell supports `ai` and `ai-docker-db` through `start.ps1`. On Windows or macOS, use `ai` and profile host `host.docker.internal` for a PostgreSQL server on the host.

The launcher generates one cryptographically random OpenCode server password per instance, stores it in the owner-only persistent instance credential directory, and reuses that file across restarts. The Compose model mounts it as `opencode_password`; it is not rendered into Compose or injected by the launcher as a password environment variable. For direct Compose, create the five secret files documented in `README.md`, set `SCHEMII_CREDENTIAL_DIR` to their absolute owner-only directory, and include `compose.ai.yaml`.

The current launchers start Schemii only. To run Schemii and Schemer with the included PostgreSQL database and one shared AI sidecar, use the advanced Compose combination documented in `README.md` and `docs/AI_AGENT_SETUP.md`:

In a POSIX shell, set the instance and ports with `export SCHEMII_INSTANCE=my-schemii SCHEMII_HOST_PORT=18080 SCHEMER_HOST_PORT=18081`. In PowerShell, set the same values with `$env:SCHEMII_INSTANCE = "my-schemii"`, `$env:SCHEMII_HOST_PORT = "18080"`, and `$env:SCHEMER_HOST_PORT = "18081"`. Then run:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.ai.yaml -f compose.schemer.yaml -f compose.schemer.ai.yaml up --build -d
```

Replace the example instance and ports with stable, collision-free values. To add Schemer to an existing direct-Compose project, use that project's exact `SCHEMII_INSTANCE`; otherwise Compose creates a separate project and separate instance-scoped volumes.

## Private Sidecar Boundary

Schemii uses `ghcr.io/anomalyco/opencode:1.18.15` through a small derived image that contains the pinned custom-tool helper. In normal bridge mode, OpenCode has no published host port. The browser communicates only with same-origin `/api/ai/...` routes, and the Schemii backend calls OpenCode using Basic authentication.

Linux `ai-local-db` mode maps OpenCode's container port 4096 to an installation-specific host port bound only to `127.0.0.1`, because the host-networked Schemii container cannot resolve the private Compose service name. The launcher prints only the Schemii URL; OpenCode remains an internal, Basic-authenticated service and must not be opened directly.

OpenCode receives a read-only assistant workspace. Shell, filesystem reads and writes, external directories, web access, tasks, dynamic MCP, sharing, LSP, formatters, and unrelated skills are denied. Do not weaken these controls or expose OpenCode publicly.

When Schemer AI is enabled, the same sidecar also receives a separate read-only `/workspace-schemer`. Schemii sessions remain restricted to `/workspace`; Schemer sessions remain restricted to `/workspace-schemer`. Each workspace has its own default-deny instructions, skills, proposal tools, and chat history. Provider authentication is intentionally global to the one OpenCode data directory, so the applications share provider connections without sharing conversations or action capabilities.

## Connect A Provider

Open the left-side AI drawer in either application, choose **Provider settings**, and select an authentication method reported by the pinned OpenCode server. Schemii and Schemer use the same assistant controls and provider UI; methods and models are discovered dynamically.

Supported UI flows include:

- API keys stored by OpenCode in its protected data volume.
- OAuth or device/browser authorization when the provider reports it.
- Subscription flows such as OpenAI ChatGPT Plus/Pro, GitHub Copilot, and GitLab Duo when offered by the installed OpenCode version and the user's plan.

There is no provider-independent subscription login. Provider availability and terms can change. Anthropic prohibits using Claude Pro/Max subscriptions through this type of integration, so Anthropic subscription OAuth is excluded; an Anthropic API key remains a separate supported provider credential when advertised.

API keys and callback codes are submitted to the active application's local backend and are never stored in browser storage or returned by either API. OpenCode stores provider credentials in plaintext JSON with restrictive file permissions inside `schemii-opencode-data`. Protect volume access and backups.

OpenCode 1.18.15 offers a temporary anonymous catalog of zero-cost models. Schemii and Schemer fetch that catalog whenever the assistant or provider settings opens, show every valid model OpenCode currently advertises, disable models OpenCode marks non-active, and use OpenCode's current default when no still-valid local preference exists. There is no application-maintained model-ID allowlist or blacklist. Catalog membership does not guarantee that an anonymous upstream has capacity: a listed model can still time out or return an empty response, after which the apps refresh discovery and report the provider failure. Availability can change without notice. Free-model prompts may be retained by their providers to improve models, so do not submit personal or confidential data.

For authenticated Zen access, open **Provider settings**, use the OpenCode Zen key link, create an account and API key, and paste the key into the active application. After connecting a provider, select any connected model from the chat panel. A model should support reliable tool calling to perform proposal actions.

Each application remembers its last selected provider/model in its own origin's browser storage and restores it whenever that model remains available. This preference contains only provider and model identifiers; API keys, OAuth callbacks, and subscription tokens are never written to browser storage. A model may need to be selected once in each app, but provider credentials do not need to be entered again.

## Persistent Chat History

OpenCode stores chat messages in the Docker-managed `schemii-opencode-data` volume. Schemii stores each chat's application-owned identity, fixed schema and PostgreSQL target, capabilities, and approval-policy revision in its protected config volume. It accepts only sessions associated with the sidecar's fixed `/workspace`; host OpenCode data is neither mounted nor shown, and records imported from another workspace are rejected. **New chat** starts a separate conversation without deleting the previous session. Open **Chat history** to list conversations by bounded title and timestamp, restore one, continue its existing session, or permanently delete it after confirmation.

Schemer uses the same persistent volume but accepts only `/workspace-schemer` sessions. Schemii and Schemer history, message, activity, proposal, operation-status, and deletion routes resolve authority from application-owned metadata PostgreSQL chat records; repeated browser context and OpenCode titles do not establish authority. Legacy JSON and title-bound authority is archived as inert evidence and is never imported as executable state. Provider authentication remains shared because OpenCode stores it globally rather than per workspace.

Restored history is intentionally narrower than OpenCode's raw records. Each application returns at most 100 messages and a bounded amount of text through its same-origin authenticated routes. It strips injected context, raw tool inputs and outputs, paths, metadata, provider response details, and action payloads. Historical proposals are never restored as interactive actions; ask the assistant for a fresh proposal against the current design or dashboard and database target.

## Configure Model Access

Schemii always supplies metadata: the active design name and counts, up to 50 local project names/logical IDs/counts/connection types and targets, and up to 50 saved connection names/logical IDs/database names. No permission is needed for this most restrictive context.

The permissions dropdown contains five independent checkboxes. Each capability also has **Ask every time**, **Ask once per chat**, and **Automatic** approval modes. Once-per-chat grants are durable only for that chat and policy revision; any policy change clears them. Automatic execution is initiated only by the server. Raw SQL, destructive saved-design actions, and destructive migration preview/apply retain per-action confirmation regardless of the broader mode. **Schema changes** adds bounded schema context and enables saved-design changes and migration previews. **Data read** enables server-generated, bounded reads of one exact relation; the model supplies no SQL. **Data write** enables structured insertion and ordinary-view creation previews plus server-issued applies. **Raw read** allows model-authored read-only SQL proposals. **Raw write** allows bounded model-authored SQL scripts as inert proposals. Schemii alone passes an authorized proposal-bound script to the server-owned Console transaction engine; the model and browser never send that SQL to PostgreSQL.

Schemer uses `Metadata`, `Dashboard`, and `Data`. Metadata includes active and available dashboard identities. Dashboard adds redacted widget configuration and a bounded set of live-verified source/column descriptors without connection metadata, filter literals, or rows. Data also adds the exact redacted profile/database/namespace target and enables inert analytic-query proposals. Rows are included only after the user confirms the displayed SQL.

Passwords, profile hosts/users, local paths, session tokens, environment variables, and stored table rows are never added automatically. Namespace lists are not fetched while building model context. Context is bounded and treated as untrusted data in the system prompt.

Raw SQL always requires fresh per-action confirmation because read-only statements can invoke externally effectful functions and raw writes execute caller-reviewed scripts. Structured relation reads may use the configured chat approval frequency.

Query results are bounded before being sent back to the model. The server stores the bounded result under an expiring, one-use opaque reference bound to the application, chat session, saved resource revision, and exact PostgreSQL target. Follow-up messages submit only that reference; browser-supplied rows are never accepted as query provenance. PostgreSQL runs these queries in a read-only transaction, but a `SELECT` can invoke database functions with external side effects. Use a narrowly privileged role and review every generated statement.

Schemer does not offer session-wide SQL approval: every analytic query requires a new confirmation. Changing the dashboard, disclosure level, profile, database, or namespace starts a separately bound conversation. Data-mode history cannot be viewed outside that exact target context.

## Live Agent Activity

Both applications use the same left-side assistant drawer and runtime. While a response is running, the chat shows an animated 25-dot activity timeline modeled after OpenCode's session UI. It can show provider connection, elapsed time, reasoning activity, retry countdowns, context compaction, allowlisted skill loading, and app-injected tool lifecycle states. Completed responses retain a collapsed run summary, collapsed reasoning, and compact tool cards. Drawer, composer, history, provider settings, keyboard focus, mobile sizing, and reduced-motion behavior are shared; each application still injects its own context, tools, skills, and action policy.

The browser never connects to OpenCode directly. Each local backend subscribes to the private sidecar's session events, filters every event to the exact application workspace and chat session, and emits a bounded same-origin NDJSON stream. The stream does not forward prompt or response text, reasoning text, tool inputs or outputs, SQL, action payloads, paths, attachments, metadata, provider response bodies, or events from another session. Final response content still arrives through the existing bounded message route and uses text-only rendering.

Animations respect the operating system's reduced-motion preference. Starting, restoring, or browsing chats is disabled while a response is active, and late responses are rejected by a local request-generation guard.

## Explicit Tools And Skills

The embedded agent can load only these packaged skills:

- Schemii help
- Connection setup
- Target selection
- Schema design and layout preservation
- Read-only query safety
- Migration safety
- PostgreSQL write safety

The currently enabled Schemii tools can propose:

- Read-only raw SQL
- Open an exact listed project
- Prefill a connection profile without a password
- Create a local project
- Populate the active saved design
- Add or rename tables
- Add or update columns
- Delete a table or column with dependent-object review
- Add a foreign-key relationship between exact saved columns
- Open an exact listed PostgreSQL connection and namespace
- Generate a read-only migration preview against an exact listed target
- Preview a structured row insertion into one exact table
- Preview creation of one expected-absent ordinary view

Schemii always supplies bounded metadata without requiring permission. The assistant panel groups independent **Schema changes**, **Data read**, **Data write**, **Raw read**, and **Raw write** capabilities in a compact permissions dropdown. Each server tool is unavailable unless its matching capability is enabled. Checking several creates one exact-target chat with those tool sets, and each capability stores its approval frequency. Every proposal records the policy revision under which it was issued; stale proposals cannot inherit a later, more permissive policy. Target identity remains immutable, so adding target-dependent capabilities to a targetless chat requires a new chat.

Authorized schema mutations execute in the Schemii backend, not in the browser. Before issuance, Schemii applies the exact mutation to an owned copy and, for a matching connection-bound design, performs a non-persisting PostgreSQL migration preflight. Each proposal is bound to the exact saved schema revision and layout token, uses operation-derived stable IDs, writes the semantic change and operation receipt atomically with one revision increment, and preserves established positions, colors, layer viewport, views, functions, PostgreSQL metadata, and unrelated objects. After a successful save, the server creates a fresh durable migration preview bound to the new revision/layout token and issues its apply proposal. Preview failure is reported separately and never mislabels the committed design save as failed. A duplicate execute or restart returns the stored receipt instead of applying the change again.

Connection-opening proposals are bound to the server's current saved profile fingerprint, database, and namespace. AI PostgreSQL operations inherit the exact saved profile role selected by the user; Schemii does not substitute credentials or reduce that role's database privileges. AI migration, structured-insert, and ordinary-view creation previews revalidate that exact target and inspect PostgreSQL read-only. A model can emit only the preview proposal. Schemii persists the reviewed plan and issues the separate apply proposal itself.

When an AI preview is eligible to apply, the server also issues a separate apply proposal that the model cannot create. Apply requires another explicit confirmation and uses only the durable server-stored plan. The proposal stores PostgreSQL authorization target and saved-schema concurrency as separate contracts, and its canonical review digest must match the durable plan at apply. The backend holds the exact saved revision and layout binding through the transaction and rechecks the profile and live catalog under the namespace mutation lock. Structured inserts use quoted identifiers and one bound JSON parameter; model-authored write SQL is never executed. The transaction ID is durably recorded before either insert or view DDL runs, so a lost commit response is reconciled with PostgreSQL transaction status and is never retried speculatively. Successful view creation narrowly synchronizes only the new view and preserves all existing layout and unrelated saved objects.

Schemer's separate agent can load only Schemer help, dashboard safety, layout safety, and query safety. Its enabled tools can create a dashboard; open an exact listed dashboard; create, rename, duplicate, or delete widgets; and, in data mode only, emit an inert read-query proposal bound to the supplied dashboard revision, profile, database, and namespace. Dashboard mutations execute through server-owned adapters with deterministic IDs, atomic operation receipts, one revision increment, stale-revision rejection, restart reconciliation, and exact preservation of unrelated widget order, desktop/mobile layout, viewport, source, query, and presentation. Complete widget creation re-verifies the exact relation fingerprint, normalizes and executes its structured single-relation visualization projection, and persists only after successful validation. Confirmed analytic SQL may join relations, but widget configuration remains single-relation. Schemii tools remain unavailable in the Schemer workspace and prompt policy.

The Schemer browser adapter sends the confirmed database, namespace, SQL, dashboard revision, exact chat session, and a local redacted-profile fingerprint to `/api/postgres/profiles/{profileId}/sql`. The backend rejects missing or unknown fields, holds the dashboard revision guard throughout execution, rejects profile changes after confirmation, verifies the saved and connected database plus namespace, executes as the exact saved profile role selected by the user, disables `EXPLAIN`, and returns complete JSON values within 100 rows, 50 columns, and 256 KiB. It also stores a model-facing projection capped at 48 KiB and returns an opaque result reference. A data-mode follow-up can consume that reference once only while the same dashboard revision, chat, disclosure, and PostgreSQL target remain current. Results are rejected in every other disclosure mode. The namespace is a default search path rather than a security boundary, so users should select a profile whose PostgreSQL privileges match the data they intend the assistant to access. Server-side confirmation, target, revision, transaction, and result limits confine each approved action, but they do not reduce the selected role's PostgreSQL privileges.

Tool output is inert structured data. It does not prove that an action ran.

## Live Free-Model Contract Tests

The normal test suite never contacts a model provider. With an AI mode already running, maintainers can explicitly test up to three active anonymous free OpenCode models:

```bash
SCHEMII_RUN_LIVE_AI_TESTS=1 python3 tests/live_ai_smoke.py --schema-id <saved-schema-id>
```

The runner discovers the current anonymous zero-cost catalog instead of assuming fixed model IDs. It sends metadata-only context through disposable chat sessions and checks project creation, password-free connection setup, migration refusal without an exact target, packaged-skill use before proposal tools, typed tool-call proposals, inert confirmation-gated actions, and unchanged saved-schema records. It never confirms an action or calls a PostgreSQL endpoint. It retries only transient transport or provider failures. Provider availability and output remain nondeterministic, so this opt-in check is not part of the default unit suite.

Free-model providers may retain these prompts and the bounded Schemii metadata context. Run this only with non-confidential saved design and project metadata. Use `--model <model-id>` to select a particular discovered free model, `--max-models 1` to reduce provider calls, or `--attempts 1` to disable the single retry.

## Confirmation And Migration Safety

Every model action is validated and canonicalized before being replaced by an expiring server-issued proposal envelope bound to the application, exact chat session, resource, disclosure level, immutable authorization target, and separate saved revision/layout concurrency snapshot. Confirmation starts one persistent operation keyed by the proposal ID. Concurrency is checked before the first execution; retries use immutable operation ownership and target identity, so a successful schema revision change does not invalidate its receipt. Concurrent or repeated requests observe that same operation instead of repeating its effect; lost responses reconcile through the full internal operation record. Success and known pre-mutation failure are terminal. Only explicit commit-unknown evidence or an interrupted running lease becomes `uncertain`. Records use application-scoped inter-process locking and atomic JSON writes in the protected config volume.

Initial example-schema generation uses one `populate_schema` action rather than separate table cards. Schemii validates table and column counts, names, types, declared keys, defaults, relationship endpoints, type compatibility, referenced uniqueness, referential actions, and unsupported fields before showing confirmation. PostgreSQL-valid keyless tables are allowed; only foreign-key targets must be primary or unique. Approval applies the validated batch atomically, lays out only the new tables, preserves all existing table layout, and saves once. Proposals originate only from typed tool calls; response text is never interpreted as an action or fallback manifest.

New-connection proposals only prefill the existing profile form. The user must enter the password and use **Save & test**.

Project navigation accepts only logical schema IDs, never paths. Creation saves an empty named project before switching. Opening a project saves pending current changes first and preserves the opened project's stored table layout and viewport.

Saved-connection opening accepts only an exact listed profile ID. On review Schemii refreshes redacted profile metadata, verifies its current name and database, and explains that confirmation will contact PostgreSQL using credentials already stored server-side. Only after confirmation does Schemii connect and load namespaces; an optional proposed namespace is selected only if PostgreSQL returns it. This action does not reveal credentials, introspect or import a schema, run SQL, preview a migration, or authorize apply.

Migration proposals never bypass Schemii's existing safety flow. AI can open a fresh preview only; it cannot emit a standalone apply proposal. The exact profile and namespace must still be selected, SQL must be previewed, destructive planning must be explicitly enabled, destructive steps require the separate checkbox, and apply uses the exact server-issued plan reviewed in the migration dialog with expiry, profile, fingerprint, advisory-lock, timeout, transaction, and rollback checks.

Structured insert proposals accept 1 to 100 rows with one consistent set of up to 50 columns. Preview verifies the exact table, all live columns, insert privilege, generated columns, identity behavior, and a catalog fingerprint. Apply locks and rechecks the table, converts values through PostgreSQL's table row type, and commits the batch atomically. Constraint, trigger, conversion, or permission failures roll back the complete batch. Update, delete, and arbitrary write SQL remain limited to the normal SQL Console write workflow.

AI view creation supports one expected-absent ordinary `CREATE VIEW` statement targeting the exact selected namespace and relation. It does not support `OR REPLACE`, materialized or temporary views, deletion, or kind conversion; those remain in the Views workspace. Apply rechecks absence under the namespace mutation lock, commits transactionally, and then performs narrow, receipt-backed saved-view synchronization.

Natural-language messages such as "yes", "confirm", or "apply" are never authorization. Only confirmation controls on the preview and separately server-issued apply proposal authorize these workflows.

## Persistent Volumes

AI mode adds these volumes:

- `schemii-opencode-data`: provider auth, sessions, and OpenCode data
- `schemii-opencode-config`: global OpenCode configuration
- `schemii-opencode-state`: selected model and state
- `schemii-opencode-cache`: recreatable provider/plugin cache

Never remove these volumes unless credential and chat deletion is intentional. Use the history dialog to delete an individual chat. Disconnecting a provider through the UI removes its stored OpenCode authentication entry.

Normal launcher restarts, image rebuilds, and container recreation reuse `schemii-opencode-data`, so OpenAI, GitHub Copilot, GitLab Duo, Zen, and API-key connections do not require authentication again. Reauthentication is required only when the provider expires or revokes its credential, the user disconnects it, or the persistent data volume is removed.

Disconnecting a provider from either application removes the one shared OpenCode authentication record and therefore disconnects it from both. Schemer states this explicitly before disconnect confirmation. Neither application mounts or reads the OpenCode credential volume directly.

## Limitations

- Final chat content uses a bounded synchronous request alongside a session-scoped live activity stream. Slow providers may take up to the active backend's timeout, followed by an upstream session-abort attempt of at most five seconds. Native Schemii uses `SCHEMII_OPENCODE_TIMEOUT`, defaults to 300 seconds, and accepts `1`–`300`. Native Schemer uses `SCHEMER_OPENCODE_TIMEOUT`, defaults to 120 seconds, and accepts the same range; `compose.schemer.ai.yaml` maps Schemer from the shared `SCHEMII_OPENCODE_TIMEOUT`. OpenCode's provider request timeout defaults to 300 seconds.
- OpenCode provider APIs evolve quickly. The image is pinned so UI and proxy behavior do not change unexpectedly.
- OAuth callback behavior is provider-specific. Complete the displayed instructions and provide a callback code only when requested.
- Provider catalogs may list models that still require authentication, are temporarily unavailable, time out, or return an empty response. The applications validate and bound OpenCode's advertised catalog but do not maintain a model-ID allowlist or blacklist; provider failures are reported to the user.
- Arbitrary AI-authored SQL writes, shell commands, filesystem tools, dynamic plugins, and dynamic MCP servers are intentionally unsupported.
