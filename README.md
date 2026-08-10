# Schemii

Schemii is a visual PostgreSQL workspace for programmers who want to understand and change a database without losing sight of either the diagram or the SQL. It combines schema design, live database inspection, data exploration, migration planning, and an optional AI assistant in one local browser application.

## What Makes Schemii Different

- **Design and reality stay separate and explicit.** PostgreSQL remains authoritative for live database state, while saved Schemii designs describe the intended schema.
- **Migrations are reviewed, not guessed.** Schemii compares the selected design with an exact verified database and namespace, shows the generated SQL and warnings, and requires separate confirmation before apply.
- **Your diagram remains yours.** Table positions, colors, and viewport state are saved independently and preserved when live schema semantics are refreshed.
- **Live tools are built into the canvas.** Inspect PostgreSQL objects, preview table rows, and run bounded read-only SQL without leaving the design workspace.
- **PostgreSQL features are represented directly.** Work with primary and foreign keys, composite keys, unique and check constraints, indexes, views, functions, triggers, identity columns, generated columns, and more.
- **AI is constrained and reviewable.** The private OpenCode sidecar can explain designs and propose structured actions, but it has no shell or filesystem access and cannot bypass Schemii confirmations or migration safety.
- **It is useful immediately.** The default stack includes a populated relational PostgreSQL tutorial and a separate local-only design with deliberately organized layouts.

Schemii runs locally, binds its UI to host loopback, and stores designs, profiles, database data, and AI state in installation-specific Docker volumes. It has no account requirement, CDN assets, or telemetry built into the application.

## Install Docker

Docker is the only software required to run Schemii. Python, Node.js, PostgreSQL tools, and Git are not required.

- **Windows:** Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/). Use the WSL 2 backend and Linux containers, then start Docker Desktop.
- **macOS:** Install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) for Apple silicon or Intel, then start Docker Desktop.
- **Linux:** Install [Docker Engine](https://docs.docker.com/engine/install/) and the [Docker Compose plugin](https://docs.docker.com/compose/install/linux/). Start the Docker service. If Docker reports a socket permission error, follow Docker's [Linux post-install steps](https://docs.docker.com/engine/install/linux-postinstall/) or use rootless Docker. Docker access is effectively administrator-level access.

Confirm both commands work in a new terminal:

```bash
docker version
docker compose version
```

If either command fails, finish the matching Docker installation above before starting Schemii.

## Download Schemii

### Without Git

1. Download the [Schemii source ZIP](https://github.com/LandMineDevelopment/schemii/archive/refs/heads/main.zip).
2. Extract it.
3. Open a terminal in the extracted `schemii-main` directory.

Keep the extracted directory in the same location. Its path identifies this Schemii installation and its Docker volumes.

### With Git

```bash
git clone https://github.com/LandMineDevelopment/schemii.git
cd schemii
```

## Start Schemii

Linux or macOS:

```bash
bash ./start.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The launcher checks Docker, builds the application, starts the complete private Compose stack, waits for health checks, and prints the local URL. It opens the browser automatically unless opening is disabled.

The first start downloads several container images and build dependencies. It requires internet access and may take several minutes. Later starts are normally much faster.

No account is required to start Schemii. Anonymous AI models may be available, but AI use can require provider authentication when anonymous models are unavailable. No model request is made until the user sends a chat message.

### Default Stack

The no-argument launcher starts:

- Schemii UI and local API
- A private PostgreSQL 17 tutorial database
- A private OpenCode agent sidecar
- A one-shot tutorial seed service

Only the Schemii UI is published to host loopback. PostgreSQL and OpenCode are not exposed to the LAN.

### Other Modes

| Goal | Linux or macOS | Windows PowerShell |
| --- | --- | --- |
| Complete default stack | `bash ./start.sh` | `powershell -ExecutionPolicy Bypass -File .\start.ps1` |
| Local design only | `bash ./start.sh ui` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode ui` |
| Tutorial PostgreSQL without AI | `bash ./start.sh docker-db` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode docker-db` |
| AI without included PostgreSQL | `bash ./start.sh ai` | `powershell -ExecutionPolicy Bypass -File .\start.ps1 -Mode ai` |
| Linux host PostgreSQL without AI | `bash ./start.sh local-db` | Not supported; use `ui` and `host.docker.internal` |
| Linux host PostgreSQL with AI | `bash ./start.sh ai-local-db` | Not supported; use `ai` and `host.docker.internal` |

Set `SCHEMII_NO_OPEN=1` on Linux/macOS or use `-NoOpen` on PowerShell to suppress browser opening.

## First Steps

The first default start creates two saved examples:

- **Mercury Books: PostgreSQL tutorial** is linked to the live `bookstore` namespace. Its nine tables include 80 books, 150 customers, 500 orders, and more than 1,200 linked order items for realistic exploration, alongside generated and identity columns, composite keys, checks, JSONB, B-tree and GIN indexes, functions, a trigger, and a view.
- **Event Studio: Local design example** is a seven-table design that demonstrates local modeling, relationships, checks, indexes, composite keys, and SQL/JSON export without a database connection.

Use the folder button to switch designs, the disk button to save, and the PostgreSQL tool to inspect data or preview migration SQL. The four-page introduction can be reopened from the **?** menu.

Deleting an example remains respected across restarts. Use **? > Restore examples** to reinstall missing examples. Existing saved designs and layouts are not replaced. In included-database mode, restoration can refresh the reserved tutorial connection password from current `.env` settings, so re-preview any open migration afterward.

### Schemer Dashboard Workspace

Schemer is an analytics workspace served separately from Schemii while reusing the same Python `PostgresService`, PostgreSQL HTTP routes, browser API client, profile store, and visual theme. Dashboard widgets render as uniform responsive tiles. Clicking a tile expands it from its dashboard position into an app-wide detail view using the widget's own header. Expanded KPI bars show their date buckets and values. Activating a number, bar, series, status value, or table row creates a 50/50 split with explicit applied filters and the matching population table. `View SQL` is available on both widgets and population inspectors; static preview results state that no SQL ran rather than presenting a fabricated query. Edit mode supports persisted, animated drag-and-drop swaps when the dragged widget center overlaps another widget, keyboard-accessible earlier/later movement, widget creation, duplication, and deletion without freeform positioning or resizing.

The Data sources dialog manages shared PostgreSQL connection profiles only. In dashboard Edit mode, each tile has an **Edit** action that opens that widget's configuration editor. Its Source section browses tables, views, and materialized views for that widget alone. Schemer sends the exact profile, configured database, and namespace to the shared PostgreSQL catalog route; the server verifies `current_database()` before returning relation identities and rejects mismatches with `database_changed`. Selecting a relation loads its normalized kind, ordered columns, PostgreSQL display types, nullability, and full deterministic catalog fingerprint. Fingerprints include semantic relation metadata and view definitions while excluding transient OIDs and timestamps. Groupings and filters will extend this same widget editor as their query-model phases are implemented.

In a widget editor, a verified relation can be assigned only to that widget. Version-1 dashboards remain compatible with empty widget configurations, while sourced widgets persist exactly one `source` object containing profile, database, namespace, relation, kind, and fingerprint. The validator rejects source arrays, joins, SQL, incomplete identities, unsupported kinds, malformed fingerprints, and unknown fields. Assignments can be cleared, and sourced tiles display their exact database, namespace, and relation.

Schemer verifies every persisted widget source against live PostgreSQL when a dashboard opens and when the catalog is refreshed. Verification requires the saved relation kind and fingerprint; mismatches return `relation_changed` and block the widget rather than silently adopting new metadata. Missing or unreachable sources are also blocked. The strict singular source shape has no join or cross-relation column-reference fields, and the dashboard validator rejects attempts to add them.

Relation columns include advisory role suggestions derived from PostgreSQL type categories. Numeric values are suggested as measures, temporal values as dates, text/enums/booleans as dimensions, and UUID or conservatively named `id`/`*_id` values as identifiers. Suggestions are displayed as labels only: they are not persisted, do not select a role, and are excluded from relation fingerprints.

The relation detail pane can request a 20-row source preview. The dedicated preview API requires the complete verified source identity, rechecks kind and fingerprint in the same read-only transaction used for selection, applies the configured statement timeout, quotes every identifier, selects only verified columns from one relation, parameterizes offset and limit, and caps requests at 50 rows. It never accepts joins or caller SQL; preview order is explicitly reported as unstable.

New source assignments also persist a semantic column snapshot containing only name, PostgreSQL type, nullability, and ordinal; older identity-only sources remain valid. Live verification compares that snapshot with PostgreSQL and reports missing relations plus named missing, added, and changed columns in the widget editor. Changed sources stay blocked until the user explicitly reselects the live relation; Schemer never rewrites the saved fingerprint or snapshot automatically.

```bash
docker compose -f compose.yaml -f compose.postgres.yaml -f compose.schemer.yaml up --build -d
```

Open Schemii at `http://127.0.0.1:8080/` and Schemer at `http://127.0.0.1:8081/`. Saved PostgreSQL profiles are shared through the existing `schemii-config` volume; passwords remain server-side and are never returned to either browser. Versioned dashboard records are stored separately in the owner-only `schemer-dashboards` volume and survive container replacement or restart. Deleting that volume permanently deletes the saved dashboards. Direct launches use `SCHEMER_DASHBOARD_DIR`, which defaults to `~/.local/share/schemer/dashboards`.

Schemer saves edits automatically using revision checks. If another browser tab saves the same dashboard first, the stale tab is blocked rather than overwriting the newer record and must reload the current dashboard.

## Everyday Use

Rerun the same launcher command to start or update an installation. The launcher reuses its saved designs, profiles, database, AI credentials, and chats.

The launcher prints an **Instance** name and URL. Separate installation directories receive separate instance names, ports, containers, images, and volumes. Do not move or rename an installation directory unless you intentionally want a new derived instance or have set a stable `SCHEMII_INSTANCE` environment variable.

When upgrading an older installation that has legacy volumes but no remaining container, the launcher stops instead of opening an empty-looking instance. Follow its displayed command to reuse the legacy `schemii` data, or choose a unique `SCHEMII_INSTANCE` for a separate installation.

Use **? > Shut down Schemii** to save pending design changes and stop the UI process. PostgreSQL and OpenCode may remain running so the next UI start is fast. To stop every container, use Docker Desktop's Containers view, or stop containers with the printed instance label:

```bash
docker stop $(docker ps -q --filter "label=com.docker.compose.project=<instance>")
```

PowerShell:

```powershell
docker ps -q --filter "label=com.docker.compose.project=<instance>" | ForEach-Object { docker stop $_ }
```

Starting Schemii again restores those containers without deleting data.

### Update A Git Checkout

```bash
git pull --ff-only
bash ./start.sh
```

PowerShell:

```powershell
git pull --ff-only
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

For a ZIP installation, extract the new files over the same installation directory and rerun the launcher. Back up important data first.

## Docker Data And Backups

The default stack stores data in instance-scoped Docker volumes:

- `schemii-config`: PostgreSQL profiles, stored profile passwords, migration history, and example state
- `schemii-schemas`: saved designs and canvas layouts
- `schemii-postgres`: included PostgreSQL data
- `schemii-opencode-data`: provider credentials and chat sessions
- `schemii-opencode-config` and `schemii-opencode-state`: OpenCode configuration and state
- `schemii-opencode-cache`: recreatable cache

List the exact volumes for the launcher-printed instance:

```bash
docker volume ls --filter "label=com.docker.compose.project=<instance>"
```

Back up the config, schemas, PostgreSQL database, and non-cache OpenCode volumes before upgrades or migration work. Use `pg_dump` for important PostgreSQL data.

Database backup on Linux/macOS, using the printed instance:

```bash
postgres_id=$(docker ps -q --filter "label=com.docker.compose.project=<instance>" --filter "label=com.docker.compose.service=postgres")
docker exec "$postgres_id" pg_dump -U schemii -d schemii > schemii-postgres.sql
```

PowerShell:

```powershell
$postgresId = docker ps -q --filter "label=com.docker.compose.project=<instance>" --filter "label=com.docker.compose.service=postgres"
docker exec $postgresId pg_dump -U schemii -d schemii > schemii-postgres.sql
```

If `.env` changes the user or database, substitute those values. To archive a stopped named volume, repeat this command for `<instance>_schemii-config`, `<instance>_schemii-schemas`, `<instance>_schemii-opencode-data`, `<instance>_schemii-opencode-config`, and `<instance>_schemii-opencode-state`:

```bash
docker run --rm -v <volume-name>:/source:ro -v "$PWD":/backup alpine:3.20 tar -czf /backup/<volume-name>.tgz -C /source .
```

On PowerShell, replace `"$PWD"` with an absolute directory accepted by Docker Desktop. Keep backups outside the installation directory before replacing source files.

Never run `docker compose down --volumes` or remove project volumes unless permanent deletion is intended. Doing so deletes saved designs, profiles and passwords, migration history, PostgreSQL data, provider credentials, chats, and AI state.

To remove only the included PostgreSQL database, stop the instance, remove only `<instance>_schemii-postgres`, and use explicit `ui` or `ai` mode afterward. The default launcher recreates and reseeds a missing included database.

## Uninstall Schemii

Back up anything important first. The uninstaller permanently removes **all Schemii instances owned by the current Docker user**, including their containers, networks, saved designs, layouts, profiles and passwords, migration history, PostgreSQL data, provider credentials, chats, state volumes, and Schemii-built images. It then removes the repository containing the uninstall script.

It does not uninstall Docker and does not use broad Docker prune commands or remove unrelated Docker projects.

Linux or macOS:

```bash
bash ./uninstall.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

The script lists detected Schemii instances and requires typing `UNINSTALL`. Docker must be installed and running so the script can verify resource removal before deleting the repository. For deliberate unattended use, pass `--yes` on Linux/macOS or `-Yes` on PowerShell.

## PostgreSQL Connections

Open the PostgreSQL tool, create a connection, and use **Save & test** before selecting a namespace or introspecting.

| PostgreSQL location | Launch mode | Profile host |
| --- | --- | --- |
| Included tutorial container | Default or `docker-db` | `postgres` |
| Linux host bound to loopback | `local-db` or `ai-local-db` | `127.0.0.1` |
| Windows/macOS host through Docker Desktop | `ui` or `ai` | `host.docker.internal` |
| Existing container on the same private Docker network | Custom Compose override | Service name or network alias |
| Remote or managed PostgreSQL | Any bridge mode | Server DNS name or IP address |

Inside normal Docker bridge mode, `127.0.0.1` refers to the Schemii container, not the host. Base Compose does not add a Linux `host.docker.internal` mapping; use a Linux host-network mode for a loopback-bound Linux PostgreSQL server.

For remote databases, prefer `sslmode=verify-full` with trusted certificates and use a narrowly privileged role. Inspection needs catalog and target-schema access. Migration apply additionally needs only the DDL privileges required by the reviewed plan.

### Included Database Settings

The included profile is created automatically. Its evaluation defaults are:

| Field | Value |
| --- | --- |
| Host | `postgres` |
| Port | `5432` |
| Database | `schemii` |
| User | `schemii` |
| Password | `schemii-local` |
| SSL mode | `disable` |

To customize these values before the first database start:

Linux/macOS:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

Edit `.env`, then run the normal Schemii launcher. Do not replace the launcher with a partial Compose command.

## Embedded AI Assistant

The default stack starts the pinned OpenCode sidecar and waits for its authenticated health check. Provider authentication, model selection, disclosure levels, confirmation boundaries, chat persistence, and limitations are documented in [`docs/AI_ASSISTANT.md`](docs/AI_ASSISTANT.md).

OpenCode runs in a read-only workspace with shell, filesystem, web, dynamic MCP, and unrelated tools denied. Proposal tools produce inert actions. Schemii validates actions and requires UI confirmation before writes, navigation, database contact, or migration workflows.

## Troubleshooting

### Docker is not found

Install Docker using the operating-system link in [Install Docker](#install-docker), reopen the terminal, and run `docker version` and `docker compose version`.

### Docker is installed but unavailable

Start Docker Desktop or the Linux Docker service. If `docker info` reports permission denied on Linux, follow Docker's post-install instructions or configure rootless Docker.

### Startup fails

1. Read the launcher error immediately above the failure.
2. Confirm `docker info` and `docker compose version` work.
3. Rerun the same launcher command. A new instance chooses an installation-specific free UI port unless `SCHEMII_HOST_PORT` is fixed. An existing instance reuses its prior port; if another process took it, stop that process or set a new `SCHEMII_HOST_PORT`.
4. In Docker Desktop, inspect the containers under the launcher-printed instance.

First startup needs internet access to download images and packages. Registry, proxy, DNS, or firewall failures can prevent image downloads.

### PostgreSQL connection fails

Confirm that the profile host matches the table in [PostgreSQL Connections](#postgresql-connections), then use **Test** in Schemii. Do not expose PostgreSQL to the LAN merely to make container networking work.

### Agent is unavailable

The default launcher includes OpenCode. Explicit `ui`, `local-db`, and `docker-db` modes do not. Rerun the default launcher and confirm the `opencode` container is healthy in Docker Desktop.

## Configuration

Most users do not need configuration. These launcher and Compose variables are the commonly useful overrides:

| Variable | Purpose |
| --- | --- |
| `SCHEMII_INSTANCE` | Stable lowercase instance name; keep unique between installations |
| `SCHEMII_HOST_PORT` | Fixed loopback browser port instead of automatic selection |
| `SCHEMII_NO_OPEN` | Set to `1` to suppress browser opening on Linux/macOS |
| `SCHEMII_POSTGRES_DB` | Included PostgreSQL database name |
| `SCHEMII_POSTGRES_USER` | Included PostgreSQL user |
| `SCHEMII_POSTGRES_PASSWORD` | Included PostgreSQL password |
| `SCHEMII_OPENCODE_TIMEOUT` | AI request timeout, default `45` seconds |

Native application variables include `SCHEMII_HOST`, `SCHEMII_PORT`, `SCHEMII_CONFIG_DIR`, `SCHEMII_SCHEMA_DIR`, and `SCHEMII_BEHIND_LOOPBACK_PROXY`. Container launchers set these automatically.

Direct Compose operation is advanced. It does not derive an instance or free port. Always set stable, unique `SCHEMII_INSTANCE` and `SCHEMII_HOST_PORT` values and include the complete file set for the intended mode. AI Compose also requires a strong, stable `SCHEMII_OPENCODE_PASSWORD`. Prefer the launchers for routine installation, updates, and mode changes.

## Migration Safety

1. Select and verify the exact profile, database, and namespace.
2. Introspect first while preserving existing canvas layout.
3. Preview and review every SQL step, warning, lock, rewrite, and destructive operation.
4. Include destructive planning only when intended, then provide the separate apply confirmation.
5. Re-preview after any design, profile, namespace, or live-catalog change.
6. Back up important data and test risky changes against disposable or staging data first.

Apply uses a namespace-scoped PostgreSQL advisory transaction lock, timeouts, stale-plan fingerprints, and one transaction. Failed steps roll back. Partitioned tables can be introspected but require manual migrations.

## Developer Setup

End users do not need this section. Contributors need Python 3.10 or newer and Node.js in addition to Docker.

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Run the checks:

```bash
python3 -m unittest discover -s tests
python3 -m compileall -q src
node --check src/schemii/web/app.js
for test_file in tests/test_*.js; do node "$test_file" || exit 1; done
git diff --check
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests
python -m compileall -q src
node --check src/schemii/web/app.js
Get-ChildItem tests/test_*.js | ForEach-Object { node $_.FullName; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
git diff --check
```

The opt-in live model contract test is documented in [`docs/AI_ASSISTANT.md`](docs/AI_ASSISTANT.md). Database integration tests must use disposable targets and leave no test objects or data behind.

## API

The browser uses same-origin local APIs for saved designs, PostgreSQL, examples, AI, and shutdown. PostgreSQL, AI, example-restoration, and shutdown routes require a local origin plus the `X-Schemii-Token` returned by `/api/session`. Schema writes additionally use revision and layout-token checks.

See `src/schemii/server.py` and the focused server tests for the current route contract. Do not expose these APIs beyond the loopback-only application boundary.

## Agent Instructions

An AI coding or terminal agent must read [`agent_guide.md`](agent_guide.md) and [`docs/AI_AGENT_SETUP.md`](docs/AI_AGENT_SETUP.md) before changing or operating Schemii. Saved-schema synchronization must follow [`.opencode/skills/preserve-schemii-layout/SKILL.md`](.opencode/skills/preserve-schemii-layout/SKILL.md).

## License

Schemii is released under the permissive [MIT License](LICENSE).
