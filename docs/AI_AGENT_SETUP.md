# AI Agent Setup Guide

This guide is for an AI coding or terminal agent helping a user install, start, verify, or connect Schema Foundry. Read `README.md` for user-facing instructions and `agent_guide.md` before changing application code or saved schemas.

## Default Goal

Unless the user explicitly requests PostgreSQL, start UI-only mode. It requires no database, account, profile, or environment file and must not contact PostgreSQL.

Linux or macOS:

```bash
./start.sh ui
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode ui
```

The expected URL is `http://127.0.0.1:8080/`. Verify `GET /` and `GET /api/session` return HTTP 200. Verify `docker compose ps` shows only `schema-foundry` in UI-only mode.

## Prerequisite Checks

Run these before diagnosing application code:

```bash
docker version
docker compose version
docker compose config
```

On Windows, Docker Desktop must be running in Linux container mode. If port 8080 is occupied, set `SCHEMA_FOUNDRY_HOST_PORT` before starting bridge-mode stacks. Do not change the container port.

## Select One Mode

### UI Only

Use this for evaluation, offline design, SQL import, and JSON or SQL export:

```bash
./start.sh ui
```

No PostgreSQL service should exist in this mode.

### Linux Host PostgreSQL

Use this when PostgreSQL runs on Linux host loopback:

```bash
./start.sh local-db
```

Use profile host `127.0.0.1` or `localhost`. This mode uses `compose.local-db.yaml`, host networking, and an application bind of `127.0.0.1:8080`.

### Included Docker PostgreSQL

Use this for a disposable local PostgreSQL instance:

```bash
./start.sh docker-db
```

Use profile host `postgres`, port `5432`, database `schema_foundry`, user `schema_foundry`, password `schema-foundry-local`, and SSL mode `disable`. The defaults are for local evaluation only. Copy `.env.example` to `.env` before first startup to customize them.

### Docker Desktop Host PostgreSQL

On Windows or macOS, start UI mode and use profile host `host.docker.internal`. Do not use `127.0.0.1` from a bridge-mode container.

### Existing Docker PostgreSQL

Prefer a shared user-defined network and use the PostgreSQL service name or network alias. Do not expose PostgreSQL to the LAN merely to make the connection work.

## Data Safety

Schema Foundry stores Docker data in these named volumes:

- `schema-foundry-config` contains profiles, passwords, and migration history.
- `schema-foundry-schemas` contains saved designs and user-owned layout.
- `schema-foundry-postgres` exists only for the included PostgreSQL mode.

Never run `docker compose down --volumes`, `docker volume rm`, or any equivalent destructive command without explicit user approval. Normal `docker compose down` and mode switches retain named volumes.

Do not print profile passwords, commit `.env`, or copy runtime schema records into the repository. API profile responses must remain redacted.

Before migrating or rewriting saved schema JSON, follow `.opencode/skills/preserve-foundry-layout/SKILL.md`. Stop the server, back up files, snapshot parsed layout, perform the minimal write, compare layout equality, and only then restart.

## Connection Verification

Use the UI's **Save & test** action when possible. For API verification, first obtain `/api/session`, then send the token only to the local Schema Foundry PostgreSQL endpoint. A connection test is non-destructive. Do not introspect, preview, or apply against an unverified profile, database, and namespace.

For the included database, confirm health first:

```bash
docker compose -f compose.yaml -f compose.postgres.yaml ps
```

## Startup Verification

Check the selected stack and local routes:

```bash
docker compose ps
curl --fail --output /dev/null http://127.0.0.1:8080/
curl --fail --output /dev/null http://127.0.0.1:8080/api/session
```

For local-db mode, include both Compose files in lifecycle commands. For docker-db mode, include `compose.postgres.yaml`. Confirm the UI is bound only to loopback.

## Troubleshooting Order

1. Confirm Docker is running and Compose parses the selected files.
2. Confirm port 8080 is free or choose another bridge-mode host port.
3. Confirm the Schema Foundry container is running and inspect `docker compose logs schema-foundry`.
4. Confirm the profile host matches the selected mode.
5. Confirm PostgreSQL is listening, healthy, and permits the configured user and source.
6. Change application code only after environment and routing failures are excluded.

## Completion Report

Tell the user which mode is running, the local URL, whether PostgreSQL was started or contacted, which persistent volumes are in use, and which checks passed. Disclose any uncommitted repository changes. Never include passwords or session tokens.
