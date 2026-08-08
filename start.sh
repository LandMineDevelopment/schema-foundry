#!/usr/bin/env bash
set -euo pipefail

mode="${1:-ui}"
port="${SCHEMA_FOUNDRY_HOST_PORT:-8080}"

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker is required. Install Docker Desktop or Docker Engine with Compose.\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'Docker is installed but not running. Start Docker and try again.\n' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose is required. Install the Docker Compose plugin and try again.\n' >&2
  exit 1
fi

case "$mode" in
  ui)
    compose=(docker compose)
    ;;
  local-db)
    if [[ "$(uname -s)" != "Linux" ]]; then
      printf 'local-db mode is Linux-only. On Docker Desktop, use ui mode and profile host.docker.internal.\n' >&2
      exit 1
    fi
    compose=(docker compose -f compose.yaml -f compose.local-db.yaml)
    port=8080
    ;;
  docker-db)
    compose=(docker compose -f compose.yaml -f compose.postgres.yaml)
    ;;
  *)
    printf 'Usage: ./start.sh [ui|local-db|docker-db]\n' >&2
    exit 2
    ;;
esac

"${compose[@]}" up --build -d --remove-orphans
url="http://127.0.0.1:${port}/"
printf '\nSchema Foundry is ready at %s\n' "$url"
printf 'Mode: %s\n' "$mode"
printf 'Saved data remains in Docker named volumes.\n'

if [[ "${SCHEMA_FOUNDRY_NO_OPEN:-0}" != "1" ]]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  fi
fi
