# AI Agent Setup Guide

This guide is for coding or terminal agents helping a user install, start, verify, update, or connect Schemii. Read `agent_guide.md` before changing application code or saved schemas. Use `README.md` as the user-facing setup source of truth.

## Default Goal

Unless the user requests another mode, use the complete launcher stack:

```bash
bash ./start.sh
```

PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The default `ai-docker-db` mode includes Schemii, private PostgreSQL, private OpenCode, the linked Mercury Books tutorial, and the local Event Studio design. The launcher prints the instance name and loopback URL. Do not assume port 8080.

## Help A User Install Docker

If `docker` is missing, direct the user to the matching official guide:

- Windows: <https://docs.docker.com/desktop/setup/install/windows-install/>
- macOS: <https://docs.docker.com/desktop/setup/install/mac-install/>
- Linux Engine: <https://docs.docker.com/engine/install/>
- Linux Compose plugin: <https://docs.docker.com/compose/install/linux/>
- Linux permissions/rootless guidance: <https://docs.docker.com/engine/install/linux-postinstall/>

Windows Docker Desktop must use Linux containers. On all platforms, Docker must be started before Schemii. Verify:

```bash
docker version
docker compose version
docker info
```

Treat a Linux Docker socket permission error separately from a stopped daemon. Docker access is effectively administrator-level access. Never weaken socket permissions broadly and never mount the Docker socket into Schemii.

The first launch downloads and builds several images and packages, requires internet access, and can take several minutes. Registry, proxy, DNS, or firewall failures are setup failures, not application-code defects.

## Use The Launchers

Do not replace a launcher command with partial direct Compose commands. Launchers provide:

- Mode-specific Compose file selection
- A generated internal OpenCode credential
- Per-installation project, image, volume, and port isolation
- Free-port selection and existing-port reuse
- PostgreSQL, OpenCode, and Schemii readiness checks
- Safe legacy-container reuse and an explicit stop when only ambiguous legacy volumes remain

Rerun the same launcher command to start or update an installation. For Git updates:

```bash
git pull --ff-only
bash ./start.sh
```

Do not move or rename an installation directory without explaining that its derived instance identity changes. Use a stable `SCHEMII_INSTANCE` when directory moves are expected.

If the launcher finds legacy config and schema volumes without a legacy container, follow its explicit choice: set `SCHEMII_INSTANCE=schemii` to reuse them, or set another unique name for a separate installation. Never hide or bypass this warning by deleting the volumes.

## Modes

| Mode | Services | PostgreSQL profile host |
| --- | --- | --- |
| Default / `ai-docker-db` | UI, included PostgreSQL, OpenCode | `postgres` |
| `ui` | UI only | Docker Desktop host: `host.docker.internal` |
| `docker-db` | UI, included PostgreSQL | `postgres` |
| `ai` | UI, OpenCode | Docker Desktop host: `host.docker.internal` |
| `local-db` | Linux host-network UI | `127.0.0.1` |
| `ai-local-db` | Linux host-network UI and loopback OpenCode | `127.0.0.1` |

Base Compose does not add a Linux `host.docker.internal` mapping. Use a Linux host-network mode for PostgreSQL bound to host loopback. Existing Docker PostgreSQL requires an explicit shared private network override and its service name or network alias.

## Verify Startup

Use the instance and URL printed by the launcher.

1. Confirm `schemii`, `postgres`, and `opencode` are healthy in default mode.
2. Confirm `example-seed` exited with status 0.
3. Open the printed URL in a browser.
4. Verify the affected UI or API behavior.

Instance-aware Docker status:

```bash
docker ps -a --filter "label=com.docker.compose.project=<instance>"
```

For logs, use the exact container name returned above:

```bash
docker logs <container-name>
```

Do not use unqualified `docker compose ps` or `docker compose logs` for a launcher-created instance. Those commands can target the legacy project instead.

HTTP clients are optional host tools. Never print or persist the body from `/api/session`; it contains a secret token. If API verification is necessary, keep the token in memory, send it only to the matching loopback Schemii API, and redact it from all output.

## Data Safety

Launcher-created volumes are scoped under the printed instance:

- `schemii-config`: profiles, stored passwords, migration history, example state
- `schemii-schemas`: saved designs and user-owned layout
- `schemii-postgres`: included database data
- `schemii-opencode-data`: provider credentials and chats
- `schemii-opencode-config` and `schemii-opencode-state`: OpenCode configuration/state
- `schemii-opencode-cache`: recreatable cache
- `schemer-dashboards`: saved Schemer dashboards when Schemer is enabled

List the exact volumes with:

```bash
docker volume ls --filter "label=com.docker.compose.project=<instance>"
```

Never run `docker compose down --volumes`, `docker volume rm`, or equivalent destructive commands without explicit approval. Disclose that volume deletion can remove designs, layouts, profiles/passwords, migration history, PostgreSQL data, provider credentials, chat history, and AI state.

Schemer AI must join the exact same Compose project and OpenCode service as Schemii. Use `compose.schemer.yaml` with `compose.schemer.ai.yaml`; do not start a second OpenCode service or copy provider credentials. Provider authentication is shared globally, while `/workspace` and `/workspace-schemer` keep application chats, skills, and proposal tools separate.

For a complete user-requested uninstall, use `uninstall.sh` or `uninstall.ps1`. The scripts inventory and remove all detected Schemii projects for the current Docker user and then remove their own repository. They require typing `UNINSTALL` unless the user explicitly requests unattended `--yes`/`-Yes` operation. Never run an uninstaller merely to troubleshoot startup, and back up requested data first.

The browser shutdown action saves pending design edits and stops only Schemii. Sidecars can remain running. To stop all project containers without deleting volumes, use Docker Desktop or stop containers selected by the exact project label.

## Examples And Layout

First-run markers prevent deleted examples from reappearing automatically. **? > Restore examples** restores missing designs and connection records. It never replaces an existing saved schema or layout. It can refresh the reserved tutorial profile password from current environment settings, which invalidates current plans; re-preview afterward.

Before generated schema writes, introspection synchronization, or server restarts after schema changes, follow `.opencode/skills/preserve-schemii-layout/SKILL.md`. Compare parsed layouts for every pre-existing record before and after the operation.

## Connection Verification

Use **Save & test** when possible. Before any introspection, SQL, preview, or apply:

1. Verify the exact profile.
2. Verify the database returned by PostgreSQL.
3. Verify the exact namespace.
4. Verify the matching saved schema record.

Do not preview or apply against an inferred target.

## Embedded AI Boundary

Read `docs/AI_ASSISTANT.md` before changing AI behavior. Keep OpenCode pinned, private, Basic-authenticated, and restricted to its read-only workspace, six allowlisted skills, and explicit proposal tools.

Never expose or mount host OpenCode files, the Docker socket, raw OpenCode endpoints, shell, filesystem writes, PTY, dynamic MCP, sharing, tasks, LSP, or formatter access.

Provider credentials flow browser -> local Schemii API -> private OpenCode. Never return, print, commit, or put them in browser storage. Model interaction remains user-initiated. Do not send prompts, connect a paid provider, or widen model disclosure without the user's request.

## Troubleshooting Order

1. Run `docker info` and `docker compose version`.
2. Read the launcher error and Docker build output.
3. Inspect containers using the printed project label.
4. Inspect the exact failing container with `docker logs`.
5. Confirm the profile host matches the selected mode.
6. Confirm PostgreSQL health, access rules, and role permissions.
7. Change application code only after environment and routing failures are excluded.

A new launcher instance chooses a free instance-specific UI port unless `SCHEMII_HOST_PORT` is fixed. An existing instance reuses its prior port; if another process has taken it, stop that process or deliberately select a new port. Do not assume a port-8080 collision caused a launcher failure.

## Completion Report

Report the selected mode, printed URL, instance name, services started, whether PostgreSQL or a model provider was contacted, persistent volumes in use, verification performed, and uncommitted changes. Never include passwords, provider credentials, or session tokens.
