# Embedded AI Assistant

Schemii's default launcher runs a private, pinned OpenCode sidecar that provides model discovery, provider authentication, chat sessions, skills, and explicit proposal tools. The sidecar starts with the default `ai-docker-db` stack, but no model request is made until the user sends a chat message. Explicit `ui`, `local-db`, and `docker-db` modes omit OpenCode.

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

The launcher generates a new random OpenCode server password for the process environment. When running Compose directly, set `SCHEMII_OPENCODE_PASSWORD` to a strong random value and include `compose.ai.yaml`.

## Private Sidecar Boundary

Schemii uses `ghcr.io/anomalyco/opencode:1.18.15` through a small derived image that contains the pinned custom-tool helper. In normal bridge mode, OpenCode has no published host port. The browser communicates only with same-origin `/api/ai/...` routes, and the Schemii backend calls OpenCode using Basic authentication.

Linux host-database mode publishes OpenCode only on `127.0.0.1:4096` because the host-networked Schemii container cannot resolve the private Compose service name. A strong generated password remains required.

OpenCode receives a read-only assistant workspace. Shell, filesystem reads and writes, external directories, web access, tasks, dynamic MCP, sharing, LSP, formatters, and unrelated skills are denied. Do not weaken these controls or expose OpenCode publicly.

## Connect A Provider

Open the AI panel, choose **Provider settings**, and select an authentication method reported by the pinned OpenCode server. Methods and models are discovered dynamically.

Supported UI flows include:

- API keys stored by OpenCode in its protected data volume.
- OAuth or device/browser authorization when the provider reports it.
- Subscription flows such as OpenAI ChatGPT Plus/Pro, GitHub Copilot, and GitLab Duo when offered by the installed OpenCode version and the user's plan.

There is no provider-independent subscription login. Provider availability and terms can change. Anthropic prohibits using Claude Pro/Max subscriptions through this type of integration, so Anthropic subscription OAuth is excluded; an Anthropic API key remains a separate supported provider credential when advertised.

API keys and callback codes are submitted to Schemii's local backend and are never stored in browser storage or returned by the API. OpenCode stores provider credentials in plaintext JSON with restrictive file permissions inside `schemii-opencode-data`. Protect volume access and backups.

OpenCode 1.18.15 offers a temporary anonymous catalog of zero-cost models. Schemii shows the models verified to return responses and withholds anonymous models that currently return an empty response or upstream `401`. Availability can change without notice. Free-model prompts may be retained by their providers to improve models, so do not submit personal or confidential data.

For authenticated Zen access, open **Provider settings**, use the OpenCode Zen key link, create an account and API key, and paste the key into Schemii. After connecting a provider, select any connected model from the chat panel. A model should support reliable tool calling to perform proposal actions.

Schemii remembers the last selected provider/model in browser storage and restores it whenever that model remains available. This preference contains only provider and model identifiers; API keys, OAuth callbacks, and subscription tokens are never written to browser storage.

## Persistent Chat History

OpenCode stores chat sessions in the Docker-managed `schemii-opencode-data` volume. Schemii accepts only sessions associated with the sidecar's fixed `/workspace`; host OpenCode data is neither mounted nor shown, and records imported from another workspace are rejected. **New chat** starts a separate conversation without deleting the previous session. Open **Chat history** to list conversations by bounded title and timestamp, restore one, continue its existing session, or permanently delete it after confirmation.

Restored history is intentionally narrower than OpenCode's raw records. Schemii returns at most 100 messages and a bounded amount of text through same-origin authenticated routes. It strips injected schema context, raw tool inputs and outputs, paths, metadata, provider response details, and action payloads. Historical schema, SQL, connection, and migration proposals are never restored as interactive actions; ask the assistant for a fresh proposal against the current design and database target.

## Configure Model Access

Every chat selects one disclosure level:

| Level | Information sent to the selected model provider |
| --- | --- |
| `Metadata` | Active design name and counts, up to 50 local project names/logical IDs/counts/connection types and targets, and up to 50 saved connection names/logical IDs/database names |
| `Schema` | Metadata plus bounded tables, columns, keys, checks, and relationships |
| `Data` | Schema context and results of explicitly permitted read-only SQL requests |

Passwords, profile hosts/users, local paths, session tokens, environment variables, and stored table rows are never added automatically. Namespace lists are not fetched while building model context. Context is bounded and treated as untrusted data in the system prompt.

Data access has a separate SQL policy:

- `Disabled` shows generated SQL but cannot execute it.
- `Ask each time` requires confirmation for each query.
- `Allow for session` executes proposals only after the user deliberately selects that setting; a new chat resets it to disabled.

Query results are bounded before being sent back to the model. PostgreSQL runs these queries in a read-only transaction, but a `SELECT` can invoke database functions with external side effects. Use a narrowly privileged role and review every generated statement.

## Live Agent Activity

While a response is running, the chat shows an animated activity timeline modeled after OpenCode's session UI. It can show provider connection, elapsed time, reasoning activity, retry countdowns, context compaction, allowlisted skill loading, and `schema_*` tool lifecycle states. Completed responses retain a collapsed run summary, collapsed reasoning, and compact tool cards.

The browser never connects to OpenCode directly. Schemii subscribes to the private sidecar's session events, filters every event to the exact chat session, and emits a bounded same-origin NDJSON stream. The stream does not forward prompt or response text, reasoning text, tool inputs or outputs, SQL, action payloads, paths, attachments, metadata, provider response bodies, or events from another session. Final response content still arrives through the existing bounded message route and uses text-only rendering.

Animations respect the operating system's reduced-motion preference. Starting, restoring, or browsing chats is disabled while a response is active, and late responses are rejected by a local request-generation guard.

## Explicit Tools And Skills

The embedded agent can load only these packaged skills:

- Schemii help
- Connection setup
- Target selection
- Schema design and layout preservation
- Read-only query safety
- Migration safety

The explicit tools can propose:

- Read-only raw SQL
- Add or rename a table
- Add or update a column
- Delete a table or column
- Add a foreign-key relationship
- Atomically populate an active design with complete tables, columns, keys, and relationships
- Create a local project or open an exact listed project
- Open an exact listed saved PostgreSQL connection
- Prefill a connection profile without a password
- Open migration preview and apply review workflows

Tool output is inert structured data. It does not prove that an action ran.

## Live Free-Model Contract Tests

The normal test suite never contacts a model provider. With an AI mode already running, maintainers can explicitly test up to three active anonymous free OpenCode models:

```bash
SCHEMII_RUN_LIVE_AI_TESTS=1 python3 tests/live_ai_smoke.py --schema-id <saved-schema-id>
```

The runner discovers the current anonymous zero-cost catalog instead of assuming fixed model IDs. It sends metadata-only context through disposable chat sessions and checks project creation, password-free connection setup, migration refusal without an exact target, packaged-skill use before proposal tools on models that advertise tool calling, validated fallback manifests on models that do not, inert confirmation-gated actions, and unchanged saved-schema records. It never confirms an action or calls a PostgreSQL endpoint. It retries only transient transport or provider failures. Provider availability and output remain nondeterministic, so this opt-in check is not part of the default unit suite.

Free-model providers may retain these prompts and the bounded Schemii metadata context. Run this only with non-confidential saved design and project metadata. Use `--model <model-id>` to select a particular discovered free model, `--max-models 1` to reduce provider calls, or `--attempts 1` to disable the single retry.

## Confirmation And Migration Safety

Every schema mutation requires a separate browser confirmation. The proposal is bound to the active design and an in-memory schema snapshot; changing the design invalidates it. Schema saves must succeed before the UI marks a proposal applied, and existing table layout is preserved.

Initial example-schema generation uses one `populate_schema` action rather than separate table cards. Schemii validates table and column counts, names, types, declared keys, defaults, relationship endpoints, type compatibility, referenced uniqueness, referential actions, and unsupported fields before showing confirmation. PostgreSQL-valid keyless tables are allowed; only foreign-key targets must be primary or unique. Approval applies the validated batch atomically, lays out only the new tables, preserves all existing table layout, and saves once. If a provider fails to execute a custom proposal tool, Schemii accepts only a bounded `SCHEMII_PROPOSALS:` fallback manifest containing this same inert action; the browser performs identical validation and confirmation.

New-connection proposals only prefill the existing profile form. The user must enter the password and use **Save & test**.

Project navigation accepts only logical schema IDs, never paths. Creation saves an empty named project before switching. Opening a project saves pending current changes first and preserves the opened project's stored table layout and viewport.

Saved-connection opening accepts only an exact listed profile ID. On review Schemii refreshes redacted profile metadata, verifies its current name and database, and explains that confirmation will contact PostgreSQL using credentials already stored server-side. Only after confirmation does Schemii connect and load namespaces; an optional proposed namespace is selected only if PostgreSQL returns it. This action does not reveal credentials, introspect or import a schema, run SQL, preview a migration, or authorize apply.

Migration proposals never bypass Schemii's existing safety flow. The exact profile and namespace must still be selected, SQL must be previewed, destructive planning must be explicitly enabled, destructive steps require the separate checkbox, and apply retains expiry, profile, fingerprint, advisory-lock, timeout, transaction, and rollback checks.

Natural-language messages such as "yes", "confirm", or "apply" are never authorization.

## Persistent Volumes

AI mode adds these volumes:

- `schemii-opencode-data`: provider auth, sessions, and OpenCode data
- `schemii-opencode-config`: global OpenCode configuration
- `schemii-opencode-state`: selected model and state
- `schemii-opencode-cache`: recreatable provider/plugin cache

Never remove these volumes unless credential and chat deletion is intentional. Use the history dialog to delete an individual chat. Disconnecting a provider through the UI removes its stored OpenCode authentication entry.

Normal launcher restarts, image rebuilds, and container recreation reuse `schemii-opencode-data`, so OpenAI, GitHub Copilot, GitLab Duo, Zen, and API-key connections do not require authentication again. Reauthentication is required only when the provider expires or revokes its credential, the user disconnects it, or the persistent data volume is removed.

## Limitations

- Final chat content uses a bounded synchronous request alongside a session-scoped live activity stream. Slow providers may take up to `SCHEMII_OPENCODE_TIMEOUT`, which defaults to 45 seconds, followed by an upstream session-abort attempt of at most five seconds.
- OpenCode provider APIs evolve quickly. The image is pinned so UI and proxy behavior do not change unexpectedly.
- OAuth callback behavior is provider-specific. Complete the displayed instructions and provide a callback code only when requested.
- Provider catalogs may list models that still require authentication or are temporarily unavailable. Schemii filters anonymous free models known not to respond with the pinned OpenCode version and reports empty responses as errors.
- Arbitrary SQL writes, shell commands, filesystem tools, dynamic plugins, and dynamic MCP servers are intentionally unsupported.
