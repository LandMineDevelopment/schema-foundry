#!/usr/bin/env bash
set -euo pipefail

requested="${1:-ai-docker-db}"
credential_action=""
case "$requested" in
  credentials-backup|credentials-restore|credentials-rotate)
    credential_action="$requested"
    mode="ui"
    ;;
  *) mode="$requested" ;;
esac

if [[ "$mode" == "help" || "$mode" == "--help" || "$mode" == "-h" ]]; then
  printf '%s\n' \
    'Usage: bash ./start.sh [mode]' \
    '' \
    'Modes:' \
    '  ai-docker-db  Complete UI, tutorial PostgreSQL, and AI stack (default)' \
    '  ui            Local schema design only' \
    '  docker-db     UI and tutorial PostgreSQL without AI' \
    '  ai            UI and AI without included PostgreSQL' \
    '  local-db      Linux host PostgreSQL without AI' \
    '  ai-local-db   Linux host PostgreSQL with AI' \
    '' \
    'Credential lifecycle:' \
    '  credentials-backup <directory>' \
    '  credentials-restore <directory>' \
    '  credentials-rotate' \
    '' \
    'Uninstall: bash ./uninstall.sh' \
    'Setup help: https://github.com/LandMineDevelopment/schemii#install-docker'
  exit 0
fi
case "$mode" in
  ui|local-db|docker-db|ai|ai-local-db|ai-docker-db) ;;
  *)
    printf 'Unknown mode: %s\nRun bash ./start.sh --help for available modes.\n' "$mode" >&2
    exit 2
    ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker was not found. Install and start Docker, then reopen this terminal.\n' >&2
  printf 'Instructions: https://github.com/LandMineDevelopment/schemii#install-docker\n' >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  printf 'Docker is installed, but the daemon is unavailable or your user lacks permission.\n' >&2
  printf 'Start Docker Desktop or the Linux Docker service, then run: docker info\n' >&2
  printf 'Instructions: https://github.com/LandMineDevelopment/schemii#docker-is-installed-but-unavailable\n' >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  printf 'Docker Compose was not found. Update Docker Desktop or install the Compose plugin.\n' >&2
  printf 'Instructions: https://docs.docker.com/compose/install/\n' >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

project="${SCHEMII_INSTANCE:-}"
if [[ -z "$project" ]]; then
  legacy_containers=( $(docker ps -aq --filter label=com.docker.compose.project=schemii --filter label=com.docker.compose.service=schemii) )
  legacy_working_dir=""
  if [[ ${#legacy_containers[@]} -gt 0 ]]; then
    legacy_working_dir="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "${legacy_containers[0]}" 2>/dev/null || true)"
  fi
  if [[ "$legacy_working_dir" == "$script_dir" ]]; then
    project="schemii"
  elif [[ ${#legacy_containers[@]} -eq 0 ]] \
    && docker volume inspect schemii_schemii-config >/dev/null 2>&1 \
    && docker volume inspect schemii_schemii-schemas >/dev/null 2>&1; then
    printf 'Legacy Schemii data volumes were found without a container that identifies their installation directory.\n' >&2
    printf 'To reuse that data, run: SCHEMII_INSTANCE=schemii bash ./start.sh %s\n' "$mode" >&2
    printf 'To start a separate installation, choose a unique name, for example: SCHEMII_INSTANCE=schemii-dev bash ./start.sh %s\n' "$mode" >&2
    exit 2
  else
    read -r instance_key _ <<< "$(printf '%s' "$script_dir" | cksum)"
    project="schemii-${instance_key}"
  fi
fi
if [[ ! "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
  printf 'SCHEMII_INSTANCE must contain only lowercase letters, numbers, hyphens, or underscores.\n' >&2
  exit 2
fi

credential_root="${SCHEMII_CREDENTIAL_ROOT:-${XDG_DATA_HOME:-${HOME:?HOME is required}/.local/share}/schemii/credentials}"
credential_dir="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"
if [[ "$credential_dir" != /* ]]; then
  printf 'SCHEMII_CREDENTIAL_DIR must be an absolute path.\n' >&2
  exit 2
fi
credential_files=(metadata_bootstrap_password metadata_migration_password metadata_schemii_password metadata_schemer_password opencode_password)
credential_transaction="$credential_dir/.credential-transaction"
credential_lock="${credential_dir}.lock"
credential_lock_token="$$-${RANDOM:-0}-$(date +%s)"
temporary_dir=""
read_lock_marker() {
  local value=""
  [[ -f "$1" ]] && IFS= read -r value < "$1" || true
  printf '%s' "$value"
}
release_credential_lock() {
  if [[ -d "$credential_lock" ]] \
      && [[ "$(read_lock_marker "$credential_lock/token")" == "$credential_lock_token" ]]; then
    rm -rf -- "$credential_lock"
  fi
}
cleanup_launcher() {
  [[ -z "$temporary_dir" ]] || rm -rf -- "$temporary_dir"
  release_credential_lock
}
trap cleanup_launcher EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
credential_parent="$(dirname -- "$credential_dir")"
(umask 077; mkdir -p "$credential_parent")
for lock_attempt in {1..60}; do
  if (umask 077; mkdir "$credential_lock") 2>/dev/null; then
    chmod 700 "$credential_lock"
    printf '%s\n' "$$" > "$credential_lock/pid"
    printf '%s\n' "$credential_lock_token" > "$credential_lock/token"
    chmod 600 "$credential_lock/pid" "$credential_lock/token"
    break
  fi
  [[ -d "$credential_lock" && ! -L "$credential_lock" ]] || {
    printf 'Credential lock path is not a directory; refusing to continue: %s\n' "$credential_lock" >&2
    exit 1
  }
  lock_pid="$(read_lock_marker "$credential_lock/pid")"
  observed_lock_token="$(read_lock_marker "$credential_lock/token")"
  if [[ "$lock_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$lock_pid" 2>/dev/null; then
    stale_lock="${credential_lock}.stale.${credential_lock_token}"
    if [[ "$(read_lock_marker "$credential_lock/token")" == "$observed_lock_token" ]] \
        && mv -- "$credential_lock" "$stale_lock" 2>/dev/null; then
      rm -rf -- "$stale_lock"
      continue
    fi
  elif [[ "$lock_attempt" -ge 3 && ! "$lock_pid" =~ ^[0-9]+$ ]]; then
    stale_lock="${credential_lock}.stale.${credential_lock_token}"
    if [[ "$(read_lock_marker "$credential_lock/token")" == "$observed_lock_token" ]] \
        && mv -- "$credential_lock" "$stale_lock" 2>/dev/null; then
      rm -rf -- "$stale_lock"
      continue
    fi
  fi
  sleep 1
done
if [[ ! -d "$credential_lock" ]] \
    || [[ "$(read_lock_marker "$credential_lock/token")" != "$credential_lock_token" ]]; then
  printf 'Timed out waiting for another launcher credential operation for %s.\n' "$project" >&2
  exit 1
fi
generate_secret() {
  docker run --rm python:3.12-slim python -c 'import secrets; print(secrets.token_hex(32))'
}
read_single_line() {
  local path="$1" name="$2"
  local value="" extra="" had_lf=0
  [[ -f "$path" ]] || { printf '%s is missing.\n' "$name" >&2; return 1; }
  exec 3< "$path"
  if IFS= read -r value <&3; then had_lf=1; fi
  if [[ "$had_lf" == "1" ]] && { IFS= read -r extra <&3 || [[ -n "$extra" ]]; }; then
    exec 3<&-
    printf '%s must contain exactly one nonempty line.\n' "$name" >&2
    return 1
  fi
  if [[ -z "$value" || "$value" == *$'\r'* ]]; then
    exec 3<&-
    printf '%s must contain exactly one nonempty line.\n' "$name" >&2
    return 1
  fi
  exec 3<&-
  printf '%s' "$value"
}
read_credential() {
  local path="$1" name="$2"
  local value="" extra="" had_lf=0
  [[ -f "$path" ]] || { printf '%s is missing.\n' "$name" >&2; return 1; }
  exec 3< "$path"
  if IFS= read -r value <&3; then had_lf=1; fi
  if [[ "$had_lf" == "1" ]] && { IFS= read -r extra <&3 || [[ -n "$extra" ]]; }; then
    exec 3<&-
    printf '%s must be one line containing 16-256 characters from [A-Za-z0-9_-].\n' "$name" >&2
    return 1
  fi
  if [[ ! "$value" =~ ^[A-Za-z0-9_-]{16,256}$ ]]; then
    exec 3<&-
    printf '%s must be one line containing 16-256 characters from [A-Za-z0-9_-].\n' "$name" >&2
    return 1
  fi
  exec 3<&-
  printf '%s' "$value"
}
write_secret() {
  local path="$1" value="$2"
  [[ "$value" =~ ^[A-Za-z0-9_-]{16,256}$ ]] || { printf 'Refusing to write an invalid credential.\n' >&2; return 1; }
  (umask 077; printf '%s\n' "$value" > "$path")
  chmod 600 "$path"
}
write_marker() {
  local path="$1" value="$2"
  [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || return 1
  (umask 077; printf '%s\n' "$value" > "$path")
  chmod 600 "$path"
}
replace_secret() {
  local path="$1" value="$2" temporary
  temporary="$(mktemp "$credential_dir/.credential.XXXXXX")"
  write_secret "$temporary" "$value"
  # Preserve the file identity so existing Compose secret bind mounts observe
  # the update. The transaction directory permits recovery from interruption.
  cp "$temporary" "$path"
  chmod 600 "$path"
  rm -f -- "$temporary"
}
container_environment() {
  local container="$1" variable="$2"
  [[ -n "$container" ]] || return 0
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" 2>/dev/null \
    | while IFS= read -r item; do
        [[ "$item" == "$variable="* ]] && printf '%s' "${item#*=}" && break
      done
}
metadata_volume="${project}_schemii-metadata-postgres"
legacy_metadata=0
if docker volume inspect "$metadata_volume" >/dev/null 2>&1; then legacy_metadata=1; fi
if [[ ! -d "$credential_dir" ]]; then
  (umask 077; mkdir -p "$credential_dir")
fi
chmod 700 "$credential_dir"
if [[ ! -f "$credential_dir/instance" ]]; then
  write_marker "$credential_dir/instance" "$project"
elif [[ "$(read_single_line "$credential_dir/instance" 'credential instance marker')" != "$project" ]]; then
  printf 'Credential directory belongs to a different instance; refusing to use it.\n' >&2
  exit 2
fi

if [[ "$legacy_metadata" == "1" && ! -f "$credential_dir/metadata_migration_password" ]]; then
  legacy_metadata_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
  legacy_metadata_container="${legacy_metadata_containers[0]:-}"
  bootstrap_password="$(container_environment "$legacy_metadata_container" POSTGRES_PASSWORD)"
  migration_password="$(container_environment "$legacy_metadata_container" SCHEMII_METADATA_MIGRATION_PASSWORD)"
  schemii_password="$(container_environment "$legacy_metadata_container" SCHEMII_METADATA_SCHEMII_PASSWORD)"
  schemer_password="$(container_environment "$legacy_metadata_container" SCHEMII_METADATA_SCHEMER_PASSWORD)"
  bootstrap_password="${bootstrap_password:-schemii-metadata-bootstrap-local}"
  migration_password="${migration_password:-schemii-metadata-migration-local}"
  schemii_password="${schemii_password:-schemii-metadata-runtime-local}"
  schemer_password="${schemer_password:-schemer-metadata-runtime-local}"
  write_secret "$credential_dir/metadata_bootstrap_password" "$bootstrap_password"
  write_secret "$credential_dir/metadata_migration_password" "$migration_password"
  write_secret "$credential_dir/metadata_schemii_password" "$schemii_password"
  write_secret "$credential_dir/metadata_schemer_password" "$schemer_password"
  printf 'WARNING: Existing metadata volume %s was found without managed credentials.\n' "$metadata_volume" >&2
  printf 'Historical credentials were preserved. Back them up; legacy rotation may first require the reviewed bootstrap-owned function. The volume was not reset.\n' >&2
fi
for secret_name in "${credential_files[@]}"; do
  secret_path="$credential_dir/$secret_name"
  if [[ ! -f "$secret_path" ]]; then
    write_secret "$secret_path" "$(generate_secret)"
  fi
  read_credential "$secret_path" "$secret_name" >/dev/null
  chmod 600 "$secret_path"
done
export SCHEMII_CREDENTIAL_DIR="$credential_dir"

metadata_psql() {
  local container="$1" authentication_password="$2" sql="$3"
  local pgpass=/tmp/schemii-credential-operation.pgpass
  if ! printf '%s\n' "$authentication_password" | docker exec -i -u postgres "$container" sh -c \
      'set -eu; umask 077; IFS= read -r password; printf "127.0.0.1:5432:schemii_metadata:schemii_metadata_migration:%s\n" "$password" > /tmp/schemii-credential-operation.pgpass'; then
    return 1
  fi
  if ! printf '%s\n' "$sql" | docker exec -i -u postgres -e PGPASSFILE="$pgpass" "$container" \
      psql --quiet --set ON_ERROR_STOP=1 --host 127.0.0.1 --username schemii_metadata_migration \
      --dbname schemii_metadata >/dev/null; then
    docker exec -u postgres "$container" rm -f "$pgpass" >/dev/null 2>&1 || true
    return 1
  fi
  docker exec -u postgres "$container" rm -f "$pgpass" >/dev/null
}
metadata_authenticates() {
  metadata_psql "$1" "$2" 'SELECT 1;'
}
wait_for_metadata() {
  local container="$1"
  for _ in {1..30}; do
    if docker exec -u postgres "$container" pg_isready --quiet --host 127.0.0.1 --port 5432 --dbname schemii_metadata; then
      return 0
    fi
    sleep 1
  done
  printf 'Metadata PostgreSQL did not become ready within 30 seconds.\n' >&2
  return 1
}
update_metadata_passwords() {
  local container="$1" authentication_password="$2" migration_password="$3" schemii_password="$4" schemer_password="$5" sql
  sql="$({
    printf '%s\n' "\\prompt '' migration_password" "$migration_password"
    printf '%s\n' "\\prompt '' schemii_password" "$schemii_password"
    printf '%s\n' "\\prompt '' schemer_password" "$schemer_password"
    printf '%s\n' "SELECT schemii_admin.rotate_metadata_passwords(:'migration_password', :'schemii_password', :'schemer_password');"
  })"
  metadata_psql "$container" "$authentication_password" "$sql"
}
restart_credential_consumers() {
  local metadata_container="$1"
  local -a dependent_ids=()
  docker restart "$metadata_container" >/dev/null || return 1
  dependent_ids=( $(docker ps -q --filter "label=com.docker.compose.project=$project") )
  for dependent_id in "${dependent_ids[@]}"; do
    if [[ "$dependent_id" != "$metadata_container" ]]; then
      docker restart "$dependent_id" >/dev/null || return 1
    fi
  done
}
replace_from_transaction() {
  local side="$1" name value
  for name in "${credential_files[@]}"; do
    value="$(read_credential "$credential_transaction/$side/$name" "$side $name")" || return 1
    replace_secret "$credential_dir/$name" "$value" || return 1
  done
}
rollback_credential_transaction() {
  local metadata_container="$1" old_migration new_migration old_schemii old_schemer
  old_migration="$(read_credential "$credential_transaction/old/metadata_migration_password" 'old migration credential')" || return 1
  new_migration="$(read_credential "$credential_transaction/new/metadata_migration_password" 'new migration credential')" || return 1
  old_schemii="$(read_credential "$credential_transaction/old/metadata_schemii_password" 'old Schemii credential')" || return 1
  old_schemer="$(read_credential "$credential_transaction/old/metadata_schemer_password" 'old Schemer credential')" || return 1
  docker start "$metadata_container" >/dev/null || return 1
  wait_for_metadata "$metadata_container" || return 1
  if metadata_authenticates "$metadata_container" "$new_migration"; then
    update_metadata_passwords "$metadata_container" "$new_migration" "$old_migration" "$old_schemii" "$old_schemer" || return 1
  elif ! metadata_authenticates "$metadata_container" "$old_migration"; then
    printf 'Neither staged metadata credential authenticates; transaction recovery requires administrator review.\n' >&2
    return 1
  fi
  replace_from_transaction old || return 1
  restart_credential_consumers "$metadata_container" || return 1
  wait_for_metadata "$metadata_container" || return 1
  metadata_authenticates "$metadata_container" "$old_migration" || return 1
  rm -rf -- "$credential_transaction"
}
run_credential_transaction() {
  local metadata_container="$1" old_migration new_migration new_schemii new_schemer
  old_migration="$(read_credential "$credential_transaction/old/metadata_migration_password" 'old migration credential')" || return 1
  new_migration="$(read_credential "$credential_transaction/new/metadata_migration_password" 'new migration credential')" || return 1
  new_schemii="$(read_credential "$credential_transaction/new/metadata_schemii_password" 'new Schemii credential')" || return 1
  new_schemer="$(read_credential "$credential_transaction/new/metadata_schemer_password" 'new Schemer credential')" || return 1
  wait_for_metadata "$metadata_container" || return 1
  update_metadata_passwords "$metadata_container" "$old_migration" "$new_migration" "$new_schemii" "$new_schemer" || return 1
  replace_from_transaction new || return 1
  restart_credential_consumers "$metadata_container" || return 1
  wait_for_metadata "$metadata_container" || return 1
  metadata_authenticates "$metadata_container" "$new_migration" || return 1
  rm -rf -- "$credential_transaction"
}
stage_credential_transaction() {
  local source="$1" name staging
  staging="$(mktemp -d "$credential_dir/.credential-transaction-stage.XXXXXX")"
  mkdir "$staging/old" "$staging/new"
  for name in "${credential_files[@]}"; do
    write_secret "$staging/old/$name" "$(read_credential "$credential_dir/$name" "$name")"
    write_secret "$staging/new/$name" "$(read_credential "$source/$name" "new $name")"
  done
  write_marker "$staging/instance" "$project"
  mv "$staging" "$credential_transaction"
}

for stale_transaction_stage in "$credential_dir"/.credential-transaction-stage.*; do
  [[ -e "$stale_transaction_stage" ]] && rm -rf -- "$stale_transaction_stage"
done
if [[ -d "$credential_transaction" ]]; then
  recovery_marker="$(read_single_line "$credential_transaction/instance" 'credential transaction marker')" || exit 1
  [[ "$recovery_marker" == "$project" ]] || { printf 'Credential transaction belongs to another instance; refusing recovery.\n' >&2; exit 1; }
  recovery_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
  recovery_container="${recovery_containers[0]:-}"
  [[ -n "$recovery_container" ]] || { printf 'An incomplete credential transaction needs its metadata container for recovery.\n' >&2; exit 1; }
  printf 'Recovering an incomplete credential transaction for %s.\n' "$project" >&2
  rollback_credential_transaction "$recovery_container" || { printf 'Automatic credential rollback failed; staged old/new values remain in %s.\n' "$credential_transaction" >&2; exit 1; }
fi

if [[ -n "$credential_action" ]]; then
  case "$credential_action" in
    credentials-backup)
      destination="${2:-}"
      [[ -n "$destination" ]] || { printf 'Usage: bash ./start.sh credentials-backup <directory>\n' >&2; exit 2; }
      mkdir -p "$destination/$project"
      chmod 700 "$destination/$project"
      for secret_name in instance "${credential_files[@]}"; do
        cp "$credential_dir/$secret_name" "$destination/$project/$secret_name"
        chmod 600 "$destination/$project/$secret_name"
      done
      release_credential_lock
      printf 'Credential backup created at %s. Protect it like a password vault.\n' "$destination/$project"
      exit 0
      ;;
    credentials-restore)
      source_dir="${2:-}"
      [[ -n "$source_dir" ]] || { printf 'Usage: bash ./start.sh credentials-restore <directory>\n' >&2; exit 2; }
      [[ -d "$source_dir/$project" ]] && source_dir="$source_dir/$project"
      backup_marker="$(read_single_line "$source_dir/instance" 'backup instance marker')" || exit 2
      [[ "$backup_marker" == "$project" ]] || { printf 'Backup instance marker does not exactly match %s.\n' "$project" >&2; exit 2; }
      for secret_name in "${credential_files[@]}"; do
        read_credential "$source_dir/$secret_name" "backup $secret_name" >/dev/null || exit 2
      done
      if [[ "$legacy_metadata" == "1" ]]; then
        restore_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
        metadata_container="${restore_containers[0]:-}"
        [[ -n "$metadata_container" ]] || { printf 'Start the instance before restoring credentials for its existing metadata volume. No files were changed.\n' >&2; exit 2; }
        docker start "$metadata_container" >/dev/null
        stage_credential_transaction "$source_dir"
        if ! run_credential_transaction "$metadata_container"; then
          printf 'Credential restore failed; rolling back PostgreSQL, files, and containers.\n' >&2
          rollback_credential_transaction "$metadata_container" || { printf 'Automatic rollback failed; staged old/new values remain in %s.\n' "$credential_transaction" >&2; exit 1; }
          exit 1
        fi
      else
        for secret_name in "${credential_files[@]}"; do replace_secret "$credential_dir/$secret_name" "$(read_credential "$source_dir/$secret_name" "backup $secret_name")"; done
      fi
      release_credential_lock
      printf 'Credentials restored for %s and dependent containers restarted.\n' "$project"
      exit 0
      ;;
    credentials-rotate)
      rotate_containers=( $(docker ps -q --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
      metadata_container="${rotate_containers[0]:-}"
      [[ -n "$metadata_container" ]] || { printf 'Start the instance before rotating credentials. No files were changed.\n' >&2; exit 2; }
      temporary_dir="$(mktemp -d "$credential_dir/.new.XXXXXX")"
      write_secret "$temporary_dir/metadata_bootstrap_password" "$(read_credential "$credential_dir/metadata_bootstrap_password" metadata_bootstrap_password)"
      for secret_name in metadata_migration_password metadata_schemii_password metadata_schemer_password opencode_password; do write_secret "$temporary_dir/$secret_name" "$(generate_secret)"; done
      stage_credential_transaction "$temporary_dir"
      rm -rf -- "$temporary_dir"
      temporary_dir=""
      if ! run_credential_transaction "$metadata_container"; then
        printf 'Credential rotation failed; rolling back PostgreSQL, files, and containers.\n' >&2
        rollback_credential_transaction "$metadata_container" || { printf 'Automatic rollback failed; staged old/new values remain in %s.\n' "$credential_transaction" >&2; exit 1; }
        exit 1
      fi
      release_credential_lock
      printf 'Credentials rotated for %s and dependent containers restarted.\n' "$project"
      exit 0
      ;;
  esac
fi
release_credential_lock
if [[ "$project" == "schemii" ]]; then
  default_port=8080
  default_opencode_port=4096
  default_metadata_port=5433
else
  read -r instance_number _ <<< "$(printf '%s' "$project" | cksum)"
  default_port=$((12000 + instance_number % 30000))
  default_opencode_port=$((42000 + instance_number % 20000))
  default_metadata_port=$((20000 + instance_number % 20000))
fi
port="${SCHEMII_HOST_PORT:-$default_port}"
opencode_port="${SCHEMII_OPENCODE_HOST_PORT:-$default_opencode_port}"
metadata_port="${SCHEMII_METADATA_HOST_PORT:-$default_metadata_port}"
project_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=schemii) )
opencode_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=opencode) )
metadata_containers=( $(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter label=com.docker.compose.service=metadata-postgres) )
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }
if [[ ${#project_containers[@]} -gt 0 && -z "${SCHEMII_HOST_PORT:-}" ]]; then
  existing_port="$(docker inspect --format '{{with index .HostConfig.PortBindings "8080/tcp"}}{{(index . 0).HostPort}}{{end}}' "${project_containers[0]}" 2>/dev/null || true)"
  if [[ -z "$existing_port" ]]; then
    while IFS= read -r value; do
      [[ "$value" == SCHEMII_PORT=* ]] && existing_port="${value#SCHEMII_PORT=}"
    done < <(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${project_containers[0]}" 2>/dev/null || true)
  fi
  [[ "$existing_port" =~ ^[0-9]+$ ]] && port="$existing_port"
elif [[ ${#project_containers[@]} -eq 0 ]]; then
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
if [[ ${#metadata_containers[@]} -gt 0 && -z "${SCHEMII_METADATA_HOST_PORT:-}" ]]; then
  existing_metadata_port="$(docker inspect --format '{{with index .HostConfig.PortBindings "5432/tcp"}}{{(index . 0).HostPort}}{{end}}' "${metadata_containers[0]}" 2>/dev/null || true)"
  [[ "$existing_metadata_port" =~ ^[0-9]+$ ]] && metadata_port="$existing_metadata_port"
elif [[ ${#metadata_containers[@]} -eq 0 && -z "${SCHEMII_METADATA_HOST_PORT:-}" ]]; then
  while port_in_use "$metadata_port" || [[ "$metadata_port" == "$port" || "$metadata_port" == "$opencode_port" ]]; do
    metadata_port=$((metadata_port + 1))
    [[ "$metadata_port" -le 41999 ]] || metadata_port=20000
  done
fi
export SCHEMII_HOST_PORT="$port"
export SCHEMII_OPENCODE_HOST_PORT="$opencode_port"
export SCHEMII_METADATA_HOST_PORT="$metadata_port"
export SCHEMII_IMAGE="${SCHEMII_IMAGE:-schemii:${project}}"
export SCHEMII_METADATA_IMAGE="${SCHEMII_METADATA_IMAGE:-schemii-metadata-postgres:${project}}"
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

printf 'Starting Schemii instance %s in %s mode.\n' "$project" "$mode"
printf 'The first start downloads images and dependencies and may take several minutes.\n'
"${compose[@]}" up --build -d --remove-orphans
container_id="$("${compose[@]}" ps -q schemii)"
if [[ -z "$container_id" ]]; then
  printf 'Schemii did not start. Review the Docker Compose output above.\n' >&2
  exit 1
fi
container_name="$(docker inspect --format '{{.Name}}' "$container_id" 2>/dev/null || true)"
container_name="${container_name#/}"
for _ in {1..60}; do
  health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
  [[ "$health" == "healthy" ]] && break
  if [[ "$health" == "unhealthy" ]]; then
    printf 'Schemii failed its container health check. Run docker logs %s for details.\n' "${container_name:-$container_id}" >&2
    exit 1
  fi
  sleep 1
done
if [[ "${health:-}" != "healthy" ]]; then
  printf 'Schemii did not become ready within 60 seconds after the build. Run docker logs %s for details.\n' "${container_name:-$container_id}" >&2
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
