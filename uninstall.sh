#!/usr/bin/env bash
set -euo pipefail

assume_yes=0
case "${1:-}" in
  --yes|-y) assume_yes=1 ;;
  "") ;;
  *) printf 'Usage: bash ./uninstall.sh [--yes]\n' >&2; exit 2 ;;
esac

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "$repo_dir" == "/" || "$repo_dir" == "${HOME:-}" \
  || ! -f "$repo_dir/compose.yaml" || ! -f "$repo_dir/start.sh" \
  || ! -d "$repo_dir/src/schemii" ]]; then
  printf 'Refusing to remove %s because it is not a recognized Schemii repository.\n' "$repo_dir" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker was not found. Install or restore Docker first so Schemii containers and volumes can be removed safely.\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'Docker is unavailable or access was denied. Start Docker and run docker info before uninstalling Schemii.\n' >&2
  exit 1
fi

projects=""
add_project() {
  local project="$1"
  [[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || return 0
  case ";$projects;" in
    *";$project;"*) ;;
    *) projects="${projects}${projects:+;}${project}" ;;
  esac
}

while IFS= read -r project; do
  [[ -n "$project" ]] && add_project "$project"
done < <(docker ps -a --filter label=com.docker.compose.service=schemii --format '{{.Label "com.docker.compose.project"}}')

volume_suffixes=(
  schemii-config schemii-schemas schemii-postgres schemii-metadata-postgres
  schemii-opencode-data schemii-opencode-config schemii-opencode-state schemii-opencode-cache
)
while IFS= read -r volume; do
  for suffix in "${volume_suffixes[@]}"; do
    if [[ "$volume" == *_"$suffix" ]]; then
      add_project "${volume%_"$suffix"}"
      break
    fi
  done
done < <(docker volume ls --format '{{.Name}}')

project_list=()
if [[ -n "$projects" ]]; then
  old_ifs="$IFS"
  IFS=';'
  read -r -a project_list <<< "$projects"
  IFS="$old_ifs"
fi

printf 'This permanently removes:\n'
printf '  - every detected Schemii Docker container and network\n'
printf '  - all detected Schemii designs, profiles, passwords, migration history, PostgreSQL data, AI credentials, and chats\n'
printf '  - Schemii-built images\n'
printf '  - repository: %s\n' "$repo_dir"
if [[ -n "$projects" ]]; then
  printf 'Detected Schemii instances:\n'
  for project in "${project_list[@]}"; do printf '  - %s\n' "$project"; done
else
  printf 'Detected Schemii instances: none\n'
fi
printf 'Unrelated Docker projects, images, and volumes are not removed.\n'

if [[ "$assume_yes" != "1" ]]; then
  printf 'Type UNINSTALL to continue: '
  IFS= read -r confirmation
  if [[ "$confirmation" != "UNINSTALL" ]]; then
    printf 'Uninstall cancelled. Nothing was removed.\n'
    exit 1
  fi
fi

if [[ -n "$projects" ]]; then
  for project in "${project_list[@]}"; do
    container_ids=( $(docker ps -aq --filter "label=com.docker.compose.project=$project") )
    if [[ ${#container_ids[@]} -gt 0 ]]; then
      docker rm -f "${container_ids[@]}"
    fi
    network_ids=( $(docker network ls -q --filter "label=com.docker.compose.project=$project") )
    if [[ ${#network_ids[@]} -gt 0 ]]; then
      docker network rm "${network_ids[@]}"
    fi
    for suffix in "${volume_suffixes[@]}"; do
      volume="${project}_${suffix}"
      if docker volume inspect "$volume" >/dev/null 2>&1; then
        docker volume rm "$volume"
      fi
    done
    for image in "schemii:$project" "schemii-opencode:1.18.15-$project"; do
      if docker image inspect "$image" >/dev/null 2>&1; then
        docker image rm "$image"
      fi
    done
  done
fi
for image in schemii:local schemii-opencode:1.18.15-local; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    docker image rm "$image"
  fi
done

repo_parent="$(dirname -- "$repo_dir")"
repo_name="$(basename -- "$repo_dir")"
printf 'Docker resources removed. Removing repository %s\n' "$repo_dir"
cd -- "$repo_parent"
rm -rf -- "$repo_name"
printf 'Schemii has been uninstalled.\n'
