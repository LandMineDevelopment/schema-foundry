# AI Agent Setup Guide

This guide is for an AI coding or terminal agent helping a user install, start, verify, or connect Schemii. Read `README.md` for user-facing instructions and `agent_guide.md` before changing application code or saved schemas.

## Default Goal

Unless the user explicitly requests another mode, start the default tutorial stack. It includes a private seeded PostgreSQL container, a saved linked bookstore project, and a separate local-only example design.

Linux or macOS:

```bash
./start.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Use the installation-specific URL and Compose project printed by the launcher. Verify `GET /` and `GET /api/session` return HTTP 200. The session response includes a secret token and a non-secret per-process `serverId`; never print or persist the token. Verify the printed Compose project shows healthy `schemii` and `postgres` services.

## Prerequisite Checks

Run these before diagnosing application code:

```bash
docker version
docker compose version
docker compose config
```

On Windows, Docker Desktop must be running in Linux container mode. Launchers derive an installation-specific host port and print the URL; set `SCHEMII_HOST_PORT` only when a fixed port is required. Do not change the bridge-mode container port.

## Select One Mode

### UI Only

Use this for evaluation, offline design, SQL import, and JSON or SQL export:

```bash
./start.sh ui
```

No PostgreSQL service should exist in this mode.

The first explicit UI-only startup installs the local Event Studio example. It must not create a PostgreSQL profile or contact PostgreSQL.

### Linux Host PostgreSQL

Use this when PostgreSQL runs on Linux host loopback:

```bash
./start.sh local-db
```

Use profile host `127.0.0.1` or `localhost`. This mode uses `compose.local-db.yaml`, host networking, and the installation-specific loopback port printed by the launcher.

### Included Docker PostgreSQL

This is the default evaluation mode and includes the seeded Mercury Books tutorial:

```bash
./start.sh docker-db
```

Use profile host `postgres`, port `5432`, database `schemii`, user `schemii`, password `schemii-local`, and SSL mode `disable`. The defaults are for local evaluation only. Copy `.env.example` to `.env` before first startup to customize them.

The one-shot seed service creates `bookstore` only when the namespace is absent, including on older included-database volumes, and otherwise leaves it unchanged. The application then verifies the exact configured database, introspects only `bookstore`, and saves the linked example with a custom layout. It does not apply a migration.

### Docker Desktop Host PostgreSQL

On Windows or macOS, start UI mode and use profile host `host.docker.internal`. Do not use `127.0.0.1` from a bridge-mode container.

### Existing Docker PostgreSQL

Prefer a shared user-defined network and use the PostgreSQL service name or network alias. Do not expose PostgreSQL to the LAN merely to make the connection work.

## Optional Embedded AI Modes

AI is opt-in and must never be added to the default tutorial or UI-only launch without the user's request.

```bash
./start.sh ai
./start.sh ai-local-db
./start.sh ai-docker-db
```

The launcher generates the internal OpenCode password. Direct Compose operation must set `SCHEMII_OPENCODE_PASSWORD` and include `compose.ai.yaml`; Linux host-database AI also includes `compose.ai.local-db.yaml`.

Read `docs/AI_ASSISTANT.md` before changing the embedded agent. Keep OpenCode pinned, private, Basic-authenticated, and restricted to the packaged read-only workspace, explicit tools, and six allowlisted skills. Never expose raw OpenCode file, shell, PTY, auth, config, plugin, MCP, or permission endpoints through Schemii.

Live chat activity uses the narrowly scoped `GET /api/ai/sessions/{sessionId}/activity` NDJSON route. The backend verifies the local session, subscribes to OpenCode's private event stream, filters the exact session ID, and emits only normalized status, reasoning-state, allowlisted skill/tool-state, compaction, and connection records. Do not widen this into a raw event proxy.

Persistent history uses authenticated `GET /api/ai/sessions` and `GET /api/ai/sessions/{sessionId}/messages` routes. Keep listing and message counts bounded. Never expose raw OpenCode session records: strip injected schema context, raw tool inputs and outputs, paths, metadata, provider details, and historical action payloads so restored proposals remain inert.

Provider credentials must flow browser -> local Schemii API -> private OpenCode. Never return credentials, put them in browser storage, print them, commit them, or mount host OpenCode credentials automatically. Every write proposal requires browser confirmation; SQL data access follows the user's current disclosure and SQL-policy settings.

## Data Safety

Schemii stores Docker data in these named volumes:

- `schemii-config` contains profiles, passwords, and migration history.
- `schemii-schemas` contains saved designs and user-owned layout.
- `schemii-postgres` exists only for the included PostgreSQL mode.

Launchers scope these volume keys under a path-derived Compose project, so multiple installation directories do not share containers, database data, profiles, designs, or browser ports. Use the instance printed by the launcher in lifecycle commands. `SCHEMII_INSTANCE` and `SCHEMII_HOST_PORT` are explicit overrides; do not point two active installations at the same values.

Direct Compose does not derive an instance from the directory. Set a unique `SCHEMII_INSTANCE` before every direct Compose command and keep it stable for that installation; otherwise direct commands use the legacy `schemii` project and can operate on another copy's volumes.

Never run `docker compose down --volumes`, `docker volume rm`, or any equivalent destructive command without explicit user approval. Normal `docker compose down` and mode switches retain named volumes.

Deleting either example does not trigger automatic recreation after its first-run marker is written. The authenticated **? > Restore examples** action creates only missing fixed example IDs and never overwrites existing layout. In database mode, restoration verifies the configured profile/database/namespace before introspection. Compare parsed layouts before and after any restoration test and require equality for all records that existed before the request.

The browser's **Shut down Schemii** control sends an authenticated local `POST /api/shutdown` after flushing pending design saves. It stops only the Schemii process; use the exact Compose file set with `docker compose down` when PostgreSQL or OpenCode sidecars must also stop. Do not call the shutdown endpoint while introspection, SQL, AI, preview, or migration apply work is active.

Do not print profile passwords, commit `.env`, or copy runtime schema records into the repository. API profile responses must remain redacted.

Before migrating or rewriting saved schema JSON, follow `.opencode/skills/preserve-schemii-layout/SKILL.md`. Stop the server, back up files, snapshot parsed layout, perform the minimal write, compare layout equality, and only then restart.

## Connection Verification

Use the UI's **Save & test** action when possible. For API verification, first obtain `/api/session`, then send the token only to the local Schemii PostgreSQL endpoint. A connection test is non-destructive. Do not introspect, preview, or apply against an unverified profile, database, and namespace.

For the included database, confirm health first:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml ps
```

## Startup Verification

The launchers wait for the Schemii container health check. Confirm the selected stack reports `healthy` using Docker only:

```bash
docker compose ps
```

Then open the local URL and verify the application loads. HTTP command-line checks are optional and must not be treated as a host prerequisite. For local-db mode, include both Compose files in lifecycle commands. For docker-db mode, include `compose.postgres.yaml`. Confirm the UI is bound only to loopback.

## Troubleshooting Order

1. Confirm Docker is running and Compose parses the selected files.
2. Confirm port 8080 is free or choose another bridge-mode host port.
3. Confirm the Schemii container is running and inspect `docker compose logs schemii`.
4. Confirm the profile host matches the selected mode.
5. Confirm PostgreSQL is listening, healthy, and permits the configured user and source.
6. Change application code only after environment and routing failures are excluded.

## Completion Report

Tell the user which mode is running, the local URL, whether PostgreSQL was started or contacted, which persistent volumes are in use, and which checks passed. Disclose any uncommitted repository changes. Never include passwords or session tokens.
