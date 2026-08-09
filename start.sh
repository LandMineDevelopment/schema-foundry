#!/usr/bin/env bash
set -euo pipefail

mode="${1:-docker-db}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

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

project="${SCHEMII_INSTANCE:-}"
if [[ -z "$project" ]]; then
  legacy_containers=( $(docker ps -aq --filter label=com.docker.compose.project=schemii --filter label=com.docker.compose.service=schemii) )
  legacy_working_dir=""
  if [[ ${#legacy_containers[@]} -gt 0 ]]; then
    legacy_working_dir="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "${legacy_containers[0]}" 2>/dev/null || true)"
  fi
  if [[ "$legacy_working_dir" == "$script_dir" ]]; then
    project="schemii"
  else
    read -r instance_key _ <<< "$(printf '%s' "$script_dir" | cksum)"
    project="schemii-${instance_key}"
  fi
fi
if [[ ! "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  printf 'SCHEMII_INSTANCE must contain only lowercase letters, numbers, hyphens, or underscores.\n' >&2
  exit 2
fi
if [[ "$project" == "schemii" ]]; then
  default_port=8080
  default_opencode_port=4096
else
  read -r instance_number _ <<< "$(printf '%s' "$project" | cksum)"
  default_port=$((12000 + instance_number % 30000))
  default_opencode_port=$((42000 + instance_number % 20000))
fi
port="${SCHEMII_HOST_PORT:-$default_port}"
opencode_port="${SCHEMII_OPENCODE_HOST_PORT:-$default_opencode_port}"
project_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=schemii) )
opencode_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=opencode) )
if [[ ${#project_containers[@]} -gt 0 && -z "${SCHEMII_HOST_PORT:-}" ]]; then
  existing_port="$(docker inspect --format '{{with index .HostConfig.PortBindings "8080/tcp"}}{{(index . 0).HostPort}}{{end}}' "${project_containers[0]}" 2>/dev/null || true)"
  if [[ -z "$existing_port" ]]; then
    while IFS= read -r value; do
      [[ "$value" == SCHEMII_PORT=* ]] && existing_port="${value#SCHEMII_PORT=}"
    done < <(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${project_containers[0]}" 2>/dev/null || true)
  fi
  [[ "$existing_port" =~ ^[0-9]+$ ]] && port="$existing_port"
elif [[ ${#project_containers[@]} -eq 0 ]]; then
  port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }
  if [[ -z "${SCHEMII_HOST_PORT:-}" ]]; then
    while port_in_use "$port"; do
      port=$((port + 1))
      [[ "$port" -le 41999 ]] || port=12000
    done
  fi
  if [[ -z "${SCHEMII_OPENCODE_HOST_PORT:-}" ]]; then
    while port_in_use "$opencode_port"; do
      opencode_port=$((opencode_port + 1))
      [[ "$opencode_port" -le 61999 ]] || opencode_port=42000
    done
  fi
fi
if [[ ${#opencode_containers[@]} -gt 0 && -z "${SCHEMII_OPENCODE_HOST_PORT:-}" ]]; then
  existing_opencode_port="$(docker inspect --format '{{with index .HostConfig.PortBindings "4096/tcp"}}{{(index . 0).HostPort}}{{end}}' "${opencode_containers[0]}" 2>/dev/null || true)"
  [[ "$existing_opencode_port" =~ ^[0-9]+$ ]] && opencode_port="$existing_opencode_port"
fi
export SCHEMII_HOST_PORT="$port"
export SCHEMII_OPENCODE_HOST_PORT="$opencode_port"
export SCHEMII_IMAGE="${SCHEMII_IMAGE:-schemii:${project}}"
export SCHEMII_OPENCODE_IMAGE="${SCHEMII_OPENCODE_IMAGE:-schemii-opencode:1.18.15-${project}}"
compose_base=(docker compose --project-name "$project" --project-directory "$script_dir" -f "$script_dir/compose.yaml")

case "$mode" in
  ui)
    compose=("${compose_base[@]}")
    ;;
  local-db)
    if [[ "$(uname -s)" != "Linux" ]]; then
      printf 'local-db mode is Linux-only. On Docker Desktop, use ui mode and profile host.docker.internal.\n' >&2
      exit 1
    fi
    compose=("${compose_base[@]}" -f "$script_dir/compose.local-db.yaml")
    ;;
  docker-db)
    compose=("${compose_base[@]}" -f "$script_dir/compose.postgres.yaml")
    ;;
  ai)
    compose=("${compose_base[@]}" -f "$script_dir/compose.ai.yaml")
    ;;
  ai-local-db)
    if [[ "$(uname -s)" != "Linux" ]]; then
      printf 'ai-local-db mode is Linux-only. Use ai mode with host.docker.internal on Docker Desktop.\n' >&2
      exit 1
    fi
    compose=("${compose_base[@]}" -f "$script_dir/compose.local-db.yaml" -f "$script_dir/compose.ai.yaml" -f "$script_dir/compose.ai.local-db.yaml")
    ;;
  ai-docker-db)
    compose=("${compose_base[@]}" -f "$script_dir/compose.postgres.yaml" -f "$script_dir/compose.ai.yaml")
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
printf 'Instance: %s\n' "$project"
printf 'Saved data remains in Docker named volumes.\n'

if [[ "${SCHEMII_NO_OPEN:-0}" != "1" && "$was_ready" != "1" ]]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  fi
fi
