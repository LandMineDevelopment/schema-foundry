#!/usr/bin/env bash
set -euo pipefail

mode="${1:-ui}"
port="${SCHEMII_HOST_PORT:-8080}"

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
  ai)
    compose=(docker compose -f compose.yaml -f compose.ai.yaml)
    ;;
  ai-local-db)
    if [[ "$(uname -s)" != "Linux" ]]; then
      printf 'ai-local-db mode is Linux-only. Use ai mode with host.docker.internal on Docker Desktop.\n' >&2
      exit 1
    fi
    compose=(docker compose -f compose.yaml -f compose.local-db.yaml -f compose.ai.yaml -f compose.ai.local-db.yaml)
    port=8080
    ;;
  ai-docker-db)
    compose=(docker compose -f compose.yaml -f compose.postgres.yaml -f compose.ai.yaml)
    ;;
  *)
    printf 'Usage: ./start.sh [ui|local-db|docker-db|ai|ai-local-db|ai-docker-db]\n' >&2
    exit 2
    ;;
esac

url="http://127.0.0.1:${port}/"
was_ready=0
if [[ "${SCHEMII_NO_OPEN:-0}" != "1" ]] && command -v curl >/dev/null 2>&1; then
  if curl --fail --silent --max-time 1 "$url" >/dev/null 2>&1; then
    was_ready=1
  fi
fi

if [[ "$mode" == ai* && -z "${SCHEMII_OPENCODE_PASSWORD:-}" ]]; then
  SCHEMII_OPENCODE_PASSWORD="$(docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(32))')"
  export SCHEMII_OPENCODE_PASSWORD
fi

"${compose[@]}" up --build -d --remove-orphans
container_id="$("${compose[@]}" ps -q schemii)"
if [[ -z "$container_id" ]]; then
  printf 'Schemii did not start. Review the Docker Compose output above.\n' >&2
  exit 1
fi
for _ in {1..60}; do
  health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
  [[ "$health" == "healthy" ]] && break
  if [[ "$health" == "unhealthy" ]]; then
    printf 'Schemii failed its container health check. Run docker compose logs schemii for details.\n' >&2
    exit 1
  fi
  sleep 1
done
if [[ "${health:-}" != "healthy" ]]; then
  printf 'Schemii did not become ready within 60 seconds. Run docker compose logs schemii for details.\n' >&2
  exit 1
fi
printf '\nSchemii is ready at %s\n' "$url"
printf 'Mode: %s\n' "$mode"
printf 'Saved data remains in Docker named volumes.\n'

if [[ "${SCHEMII_NO_OPEN:-0}" != "1" && "$was_ready" != "1" ]]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  fi
fi
