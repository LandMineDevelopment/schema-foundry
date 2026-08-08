# Embedded AI Assistant

Schema Foundry can run a private, pinned OpenCode sidecar that provides model discovery, provider authentication, chat sessions, skills, and explicit proposal tools. AI is optional. The default `ui`, `local-db`, and `docker-db` modes do not start OpenCode or contact a model provider.

## Start An AI Mode

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

The launcher generates a new random OpenCode server password for the process environment. When running Compose directly, set `SCHEMA_FOUNDRY_OPENCODE_PASSWORD` to a strong random value and include `compose.ai.yaml`.

## Private Sidecar Boundary

Schema Foundry uses `ghcr.io/anomalyco/opencode:1.18.15` through a small derived image that contains the pinned custom-tool helper. In normal bridge mode, OpenCode has no published host port. The browser communicates only with same-origin `/api/ai/...` routes, and the Schema Foundry backend calls OpenCode using Basic authentication.

Linux host-database mode publishes OpenCode only on `127.0.0.1:4096` because the host-networked Schema Foundry container cannot resolve the private Compose service name. A strong generated password remains required.

OpenCode receives a read-only assistant workspace. Shell, filesystem reads and writes, external directories, web access, tasks, dynamic MCP, sharing, LSP, formatters, and unrelated skills are denied. Do not weaken these controls or expose OpenCode publicly.

## Connect A Provider

Open the AI panel, choose **Provider settings**, and select an authentication method reported by the pinned OpenCode server. Methods and models are discovered dynamically.

Supported UI flows include:

- API keys stored by OpenCode in its protected data volume.
- OAuth or device/browser authorization when the provider reports it.
- Subscription flows such as OpenAI ChatGPT Plus/Pro, GitHub Copilot, and GitLab Duo when offered by the installed OpenCode version and the user's plan.

There is no provider-independent subscription login. Provider availability and terms can change. Anthropic prohibits using Claude Pro/Max subscriptions through this type of integration, so Anthropic subscription OAuth is excluded; an Anthropic API key remains a separate supported provider credential when advertised.

API keys and callback codes are submitted to Schema Foundry's local backend and are never stored in browser storage or returned by the API. OpenCode stores provider credentials in plaintext JSON with restrictive file permissions inside `schema-foundry-opencode-data`. Protect volume access and backups.

OpenCode 1.18.15 offers a temporary anonymous catalog of zero-cost models. Schema Foundry shows the models verified to return responses and withholds anonymous models that currently return an empty response or upstream `401`. Availability can change without notice. Free-model prompts may be retained by their providers to improve models, so do not submit personal or confidential data.

For authenticated Zen access, open **Provider settings**, use the OpenCode Zen key link, create an account and API key, and paste the key into Schema Foundry. After connecting a provider, select any connected model from the chat panel. A model should support reliable tool calling to perform proposal actions.

## Configure Model Access

Every chat selects one disclosure level:

| Level | Information sent to the selected model provider |
| --- | --- |
| `Metadata` | Active design name, object counts, and redacted selected target metadata |
| `Schema` | Metadata plus bounded tables, columns, keys, checks, and relationships |
| `Data` | Schema context and results of explicitly permitted read-only SQL requests |

Passwords, local paths, session tokens, environment variables, and stored table rows are never added automatically. Context is bounded and treated as untrusted data in the system prompt.

Data access has a separate SQL policy:

- `Disabled` shows generated SQL but cannot execute it.
- `Ask each time` requires confirmation for each query.
- `Allow for session` executes proposals only after the user deliberately selects that setting; a new chat resets it to disabled.

Query results are bounded before being sent back to the model. PostgreSQL runs these queries in a read-only transaction, but a `SELECT` can invoke database functions with external side effects. Use a narrowly privileged role and review every generated statement.

## Live Agent Activity

While a response is running, the chat shows an animated activity timeline modeled after OpenCode's session UI. It can show provider connection, elapsed time, reasoning activity, retry countdowns, context compaction, allowlisted skill loading, and `schema_*` tool lifecycle states. Completed responses retain a collapsed run summary, collapsed reasoning, and compact tool cards.

The browser never connects to OpenCode directly. Schema Foundry subscribes to the private sidecar's session events, filters every event to the exact chat session, and emits a bounded same-origin NDJSON stream. The stream does not forward prompt or response text, reasoning text, tool inputs or outputs, SQL, action payloads, paths, attachments, metadata, provider response bodies, or events from another session. Final response content still arrives through the existing bounded message route and uses text-only rendering.

Animations respect the operating system's reduced-motion preference. Starting a new chat is disabled while a response is active, and late responses are rejected by a local request-generation guard.

## Explicit Tools And Skills

The embedded agent can load only these packaged skills:

- Schema Foundry help
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
- Prefill a connection profile without a password
- Open migration preview and apply review workflows

Tool output is inert structured data. It does not prove that an action ran.

## Confirmation And Migration Safety

Every schema mutation requires a separate browser confirmation. The proposal is bound to the active design and an in-memory schema snapshot; changing the design invalidates it. Schema saves must succeed before the UI marks a proposal applied, and existing table layout is preserved.

Connection proposals only prefill the existing profile form. The user must enter the password and use **Save & test**.

Migration proposals never bypass Schema Foundry's existing safety flow. The exact profile and namespace must still be selected, SQL must be previewed, destructive planning must be explicitly enabled, destructive steps require the separate checkbox, and apply retains expiry, profile, fingerprint, advisory-lock, timeout, transaction, and rollback checks.

Natural-language messages such as "yes", "confirm", or "apply" are never authorization.

## Persistent Volumes

AI mode adds these volumes:

- `schema-foundry-opencode-data`: provider auth, sessions, and OpenCode data
- `schema-foundry-opencode-config`: global OpenCode configuration
- `schema-foundry-opencode-state`: selected model and state
- `schema-foundry-opencode-cache`: recreatable provider/plugin cache

Never remove these volumes unless credential and chat deletion is intentional. Disconnecting a provider through the UI removes its stored OpenCode authentication entry.

## Limitations

- Final chat content uses a bounded synchronous request alongside a session-scoped live activity stream. Slow providers may take up to `SCHEMA_FOUNDRY_OPENCODE_TIMEOUT`, which defaults to 45 seconds, followed by an upstream session-abort attempt of at most five seconds.
- OpenCode provider APIs evolve quickly. The image is pinned so UI and proxy behavior do not change unexpectedly.
- OAuth callback behavior is provider-specific. Complete the displayed instructions and provide a callback code only when requested.
- Provider catalogs may list models that still require authentication or are temporarily unavailable. Schema Foundry filters anonymous free models known not to respond with the pinned OpenCode version and reports empty responses as errors.
- Arbitrary SQL writes, shell commands, filesystem tools, dynamic plugins, and dynamic MCP servers are intentionally unsupported.
