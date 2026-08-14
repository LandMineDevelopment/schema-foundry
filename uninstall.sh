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

credential_root="${SCHEMII_CREDENTIAL_ROOT:-${XDG_DATA_HOME:-${HOME:?HOME is required}/.local/share}/schemii/credentials}"
volume_suffixes=(
  schemii-config schemii-schemas schemii-postgres schemii-metadata-postgres
  schemii-opencode-data schemii-opencode-config schemii-opencode-state schemii-opencode-cache
  schemer-dashboards
)

valid_project() { [[ "$1" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; }
known_volume() {
  local candidate="$1" suffix
  for suffix in "${volume_suffixes[@]}"; do
    [[ "$candidate" == "$suffix" ]] && return 0
  done
  return 1
}
credential_matches() {
  local project="$1" directory marker="" extra="" had_lf=0
  directory="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"
  [[ -f "$directory/instance" ]] || return 1
  exec 3< "$directory/instance"
  if IFS= read -r marker <&3; then had_lf=1; fi
  if [[ "$had_lf" == "1" ]] && { IFS= read -r extra <&3 || [[ -n "$extra" ]]; }; then
    exec 3<&-
    return 1
  fi
  exec 3<&-
  [[ "$marker" == "$project" ]]
}

approved_projects=()
orphan_volume_projects=()
orphan_volume_counts=()
orphan_volume_keys=()
owned_image_references=()
owned_image_ids=()
add_approved_project() {
  local candidate="$1" existing
  for existing in "${approved_projects[@]}"; do
    [[ "$existing" == "$candidate" ]] && return 0
  done
  approved_projects+=("$candidate")
}
record_orphan_volume() {
  local project="$1" logical_name="$2" key="$1:$2" index
  for index in "${!orphan_volume_keys[@]}"; do
    [[ "${orphan_volume_keys[$index]}" == "$key" ]] && return 0
  done
  orphan_volume_keys+=("$key")
  for index in "${!orphan_volume_projects[@]}"; do
    if [[ "${orphan_volume_projects[$index]}" == "$project" ]]; then
      orphan_volume_counts[$index]=$(( ${orphan_volume_counts[$index]} + 1 ))
      return 0
    fi
  done
  orphan_volume_projects+=("$project")
  orphan_volume_counts+=(1)
}
record_owned_image() {
  local reference="$1" image_id="$2" index
  for index in "${!owned_image_references[@]}"; do
    if [[ "${owned_image_references[$index]}" == "$reference" ]]; then
      owned_image_ids[$index]="$image_id"
      return 0
    fi
  done
  owned_image_references+=("$reference")
  owned_image_ids+=("$image_id")
}

all_container_ids=()
while IFS= read -r container_id; do
  [[ -n "$container_id" ]] && all_container_ids+=("$container_id")
done < <(docker ps -aq)
for container_id in "${all_container_ids[@]}"; do
  [[ -n "$container_id" ]] || continue
  labels="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.service" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container_id" 2>/dev/null || true)"
  IFS='|' read -r project service working_dir <<< "$labels"
  if valid_project "$project" && [[ "$service" == "schemii" || "$service" == "schemer" ]] \
    && [[ "$working_dir" == "$repo_dir" ]]; then
    add_approved_project "$project"
  fi
done

all_volumes=()
while IFS= read -r volume; do
  [[ -n "$volume" ]] && all_volumes+=("$volume")
done < <(docker volume ls -q)
for volume in "${all_volumes[@]}"; do
  [[ -n "$volume" ]] || continue
  labels="$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}' "$volume" 2>/dev/null || true)"
  IFS='|' read -r project logical_name <<< "$labels"
  if valid_project "$project" && known_volume "$logical_name" \
    && [[ "$volume" == "${project}_${logical_name}" ]]; then
    record_orphan_volume "$project" "$logical_name"
  fi
done
for index in "${!orphan_volume_projects[@]}"; do
  project="${orphan_volume_projects[$index]}"
  if (( orphan_volume_counts[index] >= 2 )) || credential_matches "$project"; then
    add_approved_project "$project"
  fi
done

project_list=()
while IFS= read -r project; do
  [[ -n "$project" ]] && project_list+=("$project")
done < <(printf '%s\n' "${approved_projects[@]}" | sort)

printf 'This permanently removes:\n'
printf '  - every verified Schemii Docker container and network\n'
printf '  - all verified Schemii designs, profiles, passwords, migration history, PostgreSQL data, AI credentials, and chats\n'
printf '  - safely attributable project-scoped Schemii images\n'
printf '  - each verified instance credential directory\n'
printf '  - repository: %s\n' "$repo_dir"
if [[ ${#project_list[@]} -gt 0 ]]; then
  printf 'Detected Schemii instances:\n'
  for project in "${project_list[@]}"; do printf '  - %s\n' "$project"; done
else
  printf 'Detected Schemii instances: none\n'
fi
printf 'Unrelated or ambiguously owned Docker projects, images, and volumes are not removed.\n'

if [[ "$assume_yes" != "1" ]]; then
  printf 'Type UNINSTALL to continue: '
  IFS= read -r confirmation
  if [[ "$confirmation" != "UNINSTALL" ]]; then
    printf 'Uninstall cancelled. Nothing was removed.\n'
    exit 1
  fi
fi

for project in "${project_list[@]}"; do
  owned_container_ids=()
  for container_id in "${all_container_ids[@]}"; do
    [[ -n "$container_id" ]] || continue
    labels="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}|{{ index .Config.Labels "com.docker.compose.project.working_dir" }}|{{.Image}}|{{.Config.Image}}' "$container_id" 2>/dev/null || true)"
    IFS='|' read -r resource_project working_dir image_id image_reference <<< "$labels"
    if [[ "$resource_project" == "$project" && "$working_dir" == "$repo_dir" ]]; then
      owned_container_ids+=("$container_id")
      case "$image_reference" in
        "schemii:$project"|"schemii-metadata-postgres:$project"|"schemii-opencode:1.18.15-$project")
          [[ -n "$image_id" ]] && record_owned_image "$image_reference" "$image_id"
          ;;
      esac
    fi
  done
  if [[ ${#owned_container_ids[@]} -gt 0 ]]; then
    docker rm -f "${owned_container_ids[@]}"
  fi

  network_ids=()
  while IFS= read -r network_id; do
    [[ -n "$network_id" ]] && network_ids+=("$network_id")
  done < <(docker network ls -q --filter "label=com.docker.compose.project=$project")
  for network_id in "${network_ids[@]}"; do
    [[ -n "$network_id" ]] || continue
    labels="$(docker network inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.network" }}|{{.Name}}' "$network_id" 2>/dev/null || true)"
    IFS='|' read -r resource_project logical_name resource_name <<< "$labels"
    if [[ "$resource_project" == "$project" && "$logical_name" == "default" && "$resource_name" == "${project}_default" ]]; then
      docker network rm "$network_id"
    fi
  done

  for volume in "${all_volumes[@]}"; do
    [[ -n "$volume" ]] || continue
    labels="$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}|{{ index .Labels "com.docker.compose.volume" }}|{{.Name}}' "$volume" 2>/dev/null || true)"
    IFS='|' read -r resource_project logical_name resource_name <<< "$labels"
    if [[ "$resource_project" == "$project" ]] && known_volume "$logical_name" \
      && [[ "$resource_name" == "${project}_${logical_name}" && "$volume" == "$resource_name" ]]; then
      docker volume rm "$volume"
    fi
  done

  credential_dir="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"
  if credential_matches "$project"; then
    rm -rf -- "$credential_dir"
  fi
done

for index in "${!owned_image_references[@]}"; do
  image_reference="${owned_image_references[$index]}"
  image_id="${owned_image_ids[$index]}"
  current_id="$(docker image inspect --format '{{.Id}}' "$image_reference" 2>/dev/null || true)"
  image_users_output=""
  if [[ -n "$image_id" && "$current_id" == "$image_id" ]] \
    && image_users_output="$(docker ps -aq --filter "ancestor=$image_id")"; then
    :
  else
    image_users_output=unknown
  fi
  if [[ -z "$image_users_output" ]]; then
    docker image rm "$image_reference"
  fi
done

repo_parent="$(dirname -- "$repo_dir")"
repo_name="$(basename -- "$repo_dir")"
printf 'Verified Docker resources removed. Removing repository %s\n' "$repo_dir"
cd -- "$repo_parent"
rm -rf -- "$repo_name"
printf 'Schemii has been uninstalled.\n'
