"""PostgreSQL profile, introspection, preview, and apply service.

The module deliberately has no HTTP dependency.  ``PostgresService`` methods
return JSON-serializable values and raise ``PostgresServiceError`` for a thin
HTTP adapter (such as server.py) to translate into responses.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .atomic_json import write_json
from .file_lock import exclusive_file_lock
from .postgres_common import (
    ConflictError,
    NotFoundError,
    PostgresServiceError,
    ValidationError,
    canonical_fingerprint,
    postgres_error_diagnostic,
    quote_identifier,
)
from .postgres_catalog import PostgresCatalogMixin
from .postgres_connections import PostgresConnectionMixin
from .postgres_safety import namespace_lock_keys
from .postgres_console import ConsolePolicy, PostgresConsole, single_sql_statement, top_level_semicolons
from .postgres_concurrency import PostgresExecutionController, postgres_execution
from .postgres_migrations import PostgresMigrationFacade
from .relation_source import RelationSourceValidationError, normalize_relation_source
from .result_limits import ResultLimitError, ResultLimiter, ResultLimits
from .widget_query import (
    QueryValidationError,
    compile_detail_query,
    compile_query,
    compile_temporal_series_manifest,
    compile_temporal_series_window,
    normalize_detail_request,
    normalize_query,
    normalize_temporal_series,
)

def _profile_context_fingerprint(profile_id: str, profile: dict[str, Any]) -> str:
    encoded = json.dumps(
        [profile_id, profile.get("host"), profile.get("port"), profile.get("dbname"), profile.get("user"), profile.get("sslmode")],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    value = 1469598103934665603
    for character in encoded:
        value ^= ord(character)
        value = value * 1099511628211 & ((1 << 64) - 1)
    return f"{value:016x}"

PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
SQL_IDENTIFIER_RE = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
SQL_QUALIFIED_RE = rf'{SQL_IDENTIFIER_RE}(?:\s*\.\s*{SQL_IDENTIFIER_RE})?'
SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
COLORS = ("#f4b942", "#65a9ff", "#9b82f4", "#59c894", "#ef7c8e", "#e58d4c")
def _safe_sql_query_failure(exc: Exception) -> str:
    sqlstate = getattr(exc, "sqlstate", None)
    primary = getattr(getattr(exc, "diag", None), "message_primary", None)
    if not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate) or not isinstance(primary, str):
        return "Read-only SQL query failed"
    primary = " ".join(primary.split())[:500]
    return f"Read-only SQL query failed: {primary}" if primary else "Read-only SQL query failed"


def _quote_literal(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValidationError("SQL literal must be a non-empty string")
    return "'" + value.replace("'", "''") + "'"


def _semantic_id(kind: str, *parts: Any) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return f"pg_{kind}_{hashlib.sha256(encoded.encode()).hexdigest()[:20]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


SERIES_BUCKET_SECONDS = (60, 300, 900, 3600, 21600, 86400, 604800, 2419200, 31536000)
SERIES_WINDOW_BUCKETS = 48
SERIES_MAX_TIMELINE_BUCKETS = 5000
MAX_RECONSTRUCTION_METADATA_ITEMS = 1000


def _series_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("temporal series timestamp is invalid") from exc
        if parsed.tzinfo is None:
            raise ValidationError("temporal series timestamps must include a UTC offset")
        return parsed.astimezone(timezone.utc)
    raise ValidationError("temporal series timestamp is invalid")


def _series_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _series_key(secret: bytes, profile_id: str, profile: dict[str, Any], source: dict[str, Any], query: dict[str, Any], refresh_generation: str, descriptor: dict[str, Any]) -> str:
    payload = [
        _profile_context_fingerprint(profile_id, profile),
        source,
        query,
        refresh_generation,
        descriptor,
    ]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hmac.new(secret, encoded.encode(), hashlib.sha256).hexdigest()


_top_level_semicolons = top_level_semicolons
_single_sql_statement = single_sql_statement


def _normalized_sql_whitespace(value: str) -> str:
    output = []
    quote = None
    dollar_quote = None
    pending_space = False
    index = 0
    while index < len(value):
        character = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if dollar_quote:
            if value.startswith(dollar_quote, index):
                output.append(dollar_quote)
                index += len(dollar_quote)
                dollar_quote = None
            else:
                output.append(character)
                index += 1
            continue
        if quote:
            output.append(character)
            if character == quote:
                if following == quote:
                    output.append(following)
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if character.isspace():
            pending_space = True
            index += 1
            continue
        if pending_space and output:
            output.append(" ")
        pending_space = False
        if character in {"'", '"'}:
            quote = character
        elif character == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", value[index:])
            if match:
                dollar_quote = match.group(0)
                output.append(dollar_quote)
                index += len(dollar_quote)
                continue
        output.append(character)
        index += 1
    return "".join(output).strip().rstrip(";").strip()


def _normalized_type(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    timestamptz = re.fullmatch(r"timestamptz(\(\d+\))?", normalized)
    if timestamptz:
        return f"timestamp{timestamptz.group(1) or ''} with time zone"
    aliases = (
        (r"^varchar\b", "character varying"),
        (r"^char\b", "character"),
        (r"^decimal\b", "numeric"),
        (r"^int2\b", "smallint"),
        (r"^int4\b", "integer"),
        (r"^int8\b", "bigint"),
        (r"^bool\b", "boolean"),
        (r"^float4\b", "real"),
        (r"^float8\b", "double precision"),
    )
    for pattern, replacement in aliases:
        normalized = re.sub(pattern, replacement, normalized)
    timestamp = re.fullmatch(r"timestamp(\(\d+\))?", normalized)
    if timestamp:
        return f"timestamp{timestamp.group(1) or ''} without time zone"
    return normalized


def _timestamp_timezone_kind(value: str) -> str | None:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    if re.fullmatch(r"timestamptz(?:\(\d+\))?", normalized):
        return "with"
    match = re.fullmatch(r"timestamp(?:\(\d+\))?(?: (with|without) time zone)?", normalized)
    if not match:
        return None
    return "with" if match.group(1) == "with" else "without"


def _sql_fragment(value: str, label: str) -> str:
    fragment = value.strip()
    if _top_level_semicolons(fragment):
        raise ValidationError(f"{label} must not contain multiple SQL statements")
    return fragment


def _identifier_value(value: str) -> str:
    value = value.strip()
    return value[1:-1].replace('""', '"') if value.startswith('"') else value


def _qualified_value(value: str) -> tuple[str | None, str]:
    parts = re.findall(SQL_IDENTIFIER_RE, value)
    if len(parts) == 1:
        return None, _identifier_value(parts[0])
    if len(parts) == 2:
        return _identifier_value(parts[0]), _identifier_value(parts[1])
    raise ValidationError("SQL definition has an invalid qualified name")


def _require_definition_identity(definition: str, kind: str, namespace: str, name: str, table_name: str | None = None) -> None:
    if kind == "routine":
        match = re.match(rf"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+({SQL_QUALIFIED_RE})\s*\(", definition, re.I)
        identity = _qualified_value(match.group(1)) if match else None
        expected = (namespace, name)
    elif kind == "view":
        match = re.match(rf"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+({SQL_QUALIFIED_RE})\s+AS\b", definition, re.I)
        identity = _qualified_value(match.group(1)) if match else None
        expected = (namespace, name)
    elif kind == "index":
        match = re.match(rf"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+({SQL_IDENTIFIER_RE})\s+ON\s+(?:ONLY\s+)?({SQL_QUALIFIED_RE})\b", definition, re.I)
        identity = (_identifier_value(match.group(1)), _qualified_value(match.group(2))) if match else None
        expected = (name, (namespace, table_name))
    else:
        match = re.match(rf"^CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+({SQL_IDENTIFIER_RE})[\s\S]*?\sON\s+({SQL_QUALIFIED_RE})\b", definition, re.I)
        identity = (_identifier_value(match.group(1)), _qualified_value(match.group(2))) if match else None
        expected = (name, (namespace, table_name))
    if identity != expected:
        target = f"{namespace}.{table_name or name}"
        raise ValidationError(f"{kind.title()} definition must target {target} with matching metadata")


def _is_sequence_default(value: Any) -> bool:
    return isinstance(value, str) and bool(re.match(r"^\s*nextval\s*\(", value, re.I))


class PostgresService(PostgresConnectionMixin, PostgresCatalogMixin):
    """Backend engine for PostgreSQL-backed schema profiles and changes."""

    def __init__(
        self,
        config_dir: str | os.PathLike[str],
        *,
        connect_factory: Callable[..., Any] | None = None,
        plan_ttl_seconds: int = 900,
        lock_timeout_ms: int = 5000,
        statement_timeout_ms: int = 30000,
        application_name: str = "schemii",
        execution_controller: PostgresExecutionController | None = None,
        clock: Callable[[], float] = time.time,
    ):
        if not isinstance(plan_ttl_seconds, int) or plan_ttl_seconds < 1:
            raise ValueError("plan_ttl_seconds must be a positive integer")
        if not isinstance(lock_timeout_ms, int) or lock_timeout_ms < 1:
            raise ValueError("lock_timeout_ms must be a positive integer")
        if not isinstance(statement_timeout_ms, int) or statement_timeout_ms < 1:
            raise ValueError("statement_timeout_ms must be a positive integer")
        if not isinstance(application_name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", application_name):
            raise ValueError("application_name is invalid")
        self.config_dir = Path(config_dir)
        self.profile_path = self.config_dir / "postgres_profiles.json"
        self.profile_lock_path = self.config_dir / ".postgres_profiles.lock"
        self.history_path = self.config_dir / "migration_history.json"
        self.legacy_ai_plan_dir = self.config_dir / "ai_migration_plans"
        self.ai_plan_archive_dir = self.config_dir / "retired_ai_migration_plans"
        self._connect_factory = connect_factory
        self._plan_ttl = plan_ttl_seconds
        self._lock_timeout_ms = lock_timeout_ms
        self._statement_timeout_ms = statement_timeout_ms
        self._application_name = application_name
        self._execution_controller = execution_controller or PostgresExecutionController()
        self._result_limiter = ResultLimiter(ResultLimits())
        self._target_health: dict[str, dict[str, Any]] = {}
        self._clock = clock
        self._temporal_series_secret = secrets.token_bytes(32)
        self._lock = threading.RLock()
        self._migration_coordinator = None
        self._migrations = PostgresMigrationFacade()
        self._console = PostgresConsole(self)
        self._ensure_config_dir()

    def set_migration_coordinator(self, coordinator: Any) -> None:
        self._migration_coordinator = coordinator
        self._migrations.set_coordinator(coordinator)

    def profile_context_fingerprint(self, profile_id: str) -> str:
        return _profile_context_fingerprint(profile_id, self._profile(profile_id))

    def execute_console(self, profile_id: str, payload: Any, binding: str, server_id: str, policy: ConsolePolicy | None = None) -> dict[str, Any]:
        with self.execution("console"):
            return self._console.execute(profile_id, payload, binding, server_id, policy or ConsolePolicy(statement_timeout_ms=self._statement_timeout_ms))

    def cancel_console(self, profile_id: str, execution_id: Any, binding: str, server_id: str) -> dict[str, bool]:
        return self._console.cancel(profile_id, execution_id, binding, server_id)

    def create_console_write_grant(self, profile_id: str, payload: Any, binding: str, server_id: str) -> dict[str, Any]:
        return self._console.create_write_grant(profile_id, payload, binding, server_id)

    def revoke_console_write_grant(self, profile_id: str, grant_id: Any, binding: str, server_id: str) -> dict[str, bool]:
        return self._console.revoke_write_grant(profile_id, grant_id, binding, server_id)

    def close(self) -> None:
        self._console.close()
        self._execution_controller.close()

    def execution(self, execution_class: str):
        return self._execution_controller.execution(execution_class)

    def execution_metrics(self) -> dict[str, Any]:
        return self._execution_controller.snapshot()

    def _record_target_connection(self, profile: dict[str, Any], healthy: bool) -> None:
        profile_id = profile.get("id")
        if not isinstance(profile_id, str):
            profile_id = next((key for key, value in self._read_profiles().items() if value == profile), None)
        if isinstance(profile_id, str):
            with self._lock:
                self._target_health[profile_id] = {
                    "status": "available" if healthy else "degraded",
                    "observedAt": _utc_now(),
                }

    def target_readiness(self) -> dict[str, Any]:
        profiles = self.list_profiles()
        with self._lock:
            observed = {item["id"]: dict(self._target_health.get(item["id"], {"status": "unknown"})) for item in profiles}
        return {
            "required": False,
            "status": "degraded" if any(item["status"] == "degraded" for item in observed.values()) else "available",
            "configured": len(profiles),
            "profiles": observed,
        }

    @staticmethod
    def _limit_error(exc: ResultLimitError) -> PostgresServiceError:
        return PostgresServiceError(422, exc.code, exc.message, exc.details)

    def _limited_rows(
        self, rows: list[Any], aliases: list[str], *, max_rows: int,
        envelope: Callable[[list[list[Any]]], Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._result_limiter.rows(rows, aliases, max_rows=max_rows, envelope=envelope)
        except ResultLimitError as exc:
            raise self._limit_error(exc) from exc

    def _limited_records(
        self, rows: list[Any], aliases: list[str], *, max_rows: int,
        envelope: Callable[[list[dict[str, Any]]], Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._result_limiter.records(rows, aliases, max_rows=max_rows, envelope=envelope)
        except ResultLimitError as exc:
            raise self._limit_error(exc) from exc

    # ---- profiles -------------------------------------------------------

    def _ensure_config_dir(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.config_dir, 0o700)
        if self.profile_path.exists():
            os.chmod(self.profile_path, 0o600)
        if self.history_path.exists():
            os.chmod(self.history_path, 0o600)
        # Legacy JSON plans are retained only as inert evidence. They are never
        # loaded into the durable execution lifecycle.
        if self.legacy_ai_plan_dir.exists():
            self.ai_plan_archive_dir.mkdir(mode=0o700, exist_ok=True)
            for path in self.legacy_ai_plan_dir.glob("*.json"):
                destination = self.ai_plan_archive_dir / f"{path.stem}.retired.json"
                if not destination.exists():
                    os.replace(path, destination)
            try:
                self.legacy_ai_plan_dir.rmdir()
            except OSError:
                pass
    def _read_profiles(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if not self.profile_path.exists():
                return {}
            try:
                data = json.loads(self.profile_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PostgresServiceError(500, "profile_store_error", "Profile store could not be read") from exc
            if not isinstance(data, dict) or not isinstance(data.get("profiles", {}), dict):
                raise PostgresServiceError(500, "profile_store_error", "Profile store is invalid")
            return data.get("profiles", {})

    @contextmanager
    def _profile_store_lock(self):
        with exclusive_file_lock(self.profile_lock_path):
            yield

    def _write_profiles(self, profiles: dict[str, dict[str, Any]]) -> None:
        self._ensure_config_dir()
        try:
            write_json(self.profile_path, {"profiles": profiles}, mode=0o600, sort_keys=True)
        except OSError as exc:
            raise PostgresServiceError(500, "profile_store_error", "Profile store could not be written") from exc

    @staticmethod
    def _validate_profile_id(profile_id: Any) -> str:
        if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
            raise ValidationError("Profile ID must be 1-64 letters, numbers, underscores, or hyphens")
        return profile_id

    @staticmethod
    def _text(payload: dict[str, Any], key: str, maximum: int, *, host: bool = False) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or value != value.strip() or not value or len(value) > maximum:
            raise ValidationError(f"{key} must be a non-empty trimmed string up to {maximum} characters")
        if "\x00" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValidationError(f"{key} contains invalid characters")
        if host and any(char.isspace() for char in value):
            raise ValidationError("host must not contain whitespace")
        return value

    def _validated_profile(self, payload: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("Profile payload must be an object")
        allowed = {"name", "host", "port", "dbname", "user", "password", "sslmode", "timeout"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValidationError(f"Unknown profile field: {sorted(unknown)[0]}")
        merged = dict(existing or {})
        merged.update(payload)
        result = {
            "name": self._text(merged, "name", 128),
            "host": self._text(merged, "host", 255, host=True),
            "dbname": self._text(merged, "dbname", 128),
            "user": self._text(merged, "user", 128),
        }
        port = merged.get("port", 5432)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValidationError("port must be an integer from 1 to 65535")
        result["port"] = port
        sslmode = merged.get("sslmode", "prefer")
        if not isinstance(sslmode, str) or sslmode not in SSL_MODES:
            raise ValidationError("sslmode is invalid")
        result["sslmode"] = sslmode
        timeout = merged.get("timeout", 10)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise ValidationError("timeout must be an integer from 1 to 120 seconds")
        result["timeout"] = timeout
        password = merged.get("password", "")
        if not isinstance(password, str) or len(password) > 4096 or "\x00" in password:
            raise ValidationError("password is invalid")
        if existing is not None and payload.get("password") == "":
            password = existing.get("password", "")
        result["password"] = password
        return result

    @staticmethod
    def _redact(profile_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": profile_id,
            **{key: value for key, value in profile.items() if key != "password"},
            "contextFingerprint": _profile_context_fingerprint(profile_id, profile),
        }

    def list_profiles(self) -> list[dict[str, Any]]:
        profiles = self._read_profiles()
        return [self._redact(key, profiles[key]) for key in sorted(profiles, key=lambda item: (profiles[item]["name"], item))]

    def save_profile(self, profile_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            with self._profile_store_lock():
                profiles = self._read_profiles()
                if profile_id is None:
                    profile_id = "pg_" + secrets.token_hex(8)
                    existing = None
                else:
                    profile_id = self._validate_profile_id(profile_id)
                    existing = profiles.get(profile_id)
                profile = self._validated_profile(payload, existing)
                profiles[profile_id] = profile
                self._write_profiles(profiles)
            return self._redact(profile_id, profile)

    def delete_profile(self, profile_id: str, expected_fingerprint: str | None = None) -> dict[str, str]:
        profile_id = self._validate_profile_id(profile_id)
        with self._lock:
            with self._profile_store_lock():
                profiles = self._read_profiles()
                if profile_id not in profiles:
                    raise NotFoundError("Profile was not found")
                current_fingerprint = _profile_context_fingerprint(profile_id, profiles[profile_id])
                if not isinstance(expected_fingerprint, str) or expected_fingerprint != current_fingerprint:
                    raise ConflictError("profile_changed", "The PostgreSQL profile changed after deletion was reviewed")
                del profiles[profile_id]
                self._write_profiles(profiles)
        return {"deleted": profile_id}

    def _read_history(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.history_path.exists():
                return []
            try:
                payload = json.loads(self.history_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PostgresServiceError(500, "history_store_error", "Migration history could not be read") from exc
            if not isinstance(payload, list):
                raise PostgresServiceError(500, "history_store_error", "Migration history is invalid")
            return payload

    def _append_history(self, entry: dict[str, Any]) -> None:
        with self._lock:
            history = self._read_history()
            history.append(entry)
            history.sort(key=lambda item: (item.get("appliedAt", ""), item.get("id", "")))
            history = history[-1000:]
            try:
                write_json(self.history_path, history, mode=0o600)
            except OSError as exc:
                raise PostgresServiceError(500, "history_store_error", "Migration history could not be written") from exc

    def list_history(self, profile_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if profile_id is not None:
            profile_id = self._validate_profile_id(profile_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValidationError("History limit must be from 1 to 500")
        history = self._read_history()
        if profile_id is not None:
            history = [entry for entry in history if entry.get("profileId") == profile_id]
        return list(reversed(history[-limit:]))

    def _profile(self, profile_id: str) -> dict[str, Any]:
        profile_id = self._validate_profile_id(profile_id)
        profile = self._read_profiles().get(profile_id)
        if profile is None:
            raise NotFoundError("Profile was not found")
        return profile

    def _validate_relation_source(self, profile_id: str, source: Any) -> tuple[str, str, str, str, str, list[dict[str, Any]] | None]:
        try:
            normalized = normalize_relation_source(source, expected_profile_id=profile_id)
        except RelationSourceValidationError as exc:
            raise ValidationError(str(exc)) from exc
        return (
            normalized["database"], normalized["namespace"], normalized["relation"],
            normalized["kind"], normalized["fingerprint"], normalized.get("columns"),
        )

    @staticmethod
    def _query_provenance(profile_id: str, profile: dict[str, Any], descriptor: dict[str, Any]) -> dict[str, Any]:
        return {
            "profile": {"id": profile_id, "label": profile["name"]},
            "relation": {
                "database": descriptor["database"],
                "namespace": descriptor["namespace"],
                "name": descriptor["relation"],
                "kind": descriptor["kind"],
                "fingerprint": descriptor["fingerprint"],
                "columns": [
                    {key: column[key] for key in ("name", "type", "nullable", "ordinal")}
                    for column in descriptor["columns"]
                ],
                "definition": dict(descriptor["definition"]),
            },
        }

    @postgres_execution("catalog")
    def verify_relation_source(self, profile_id: str, source: Any) -> dict[str, Any]:
        database, namespace, relation, kind, fingerprint, expected_columns = self._validate_relation_source(profile_id, source)
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION READ ONLY")
            try:
                descriptor = self._inspect_relation_connection(
                    connection, profile_id, database, namespace, relation, None, None
                )
            except NotFoundError:
                return {
                    "status": "missing", "matches": False, "profileId": profile_id, "database": database,
                    "namespace": namespace, "relation": relation, "missingColumns": [column["name"] for column in expected_columns or []],
                    "addedColumns": [], "changedColumns": [],
                }
            current_columns = {column["name"]: column for column in descriptor["columns"]}
            saved_columns = {column["name"]: column for column in expected_columns or []}
            missing_columns = sorted(set(saved_columns) - set(current_columns))
            added_columns = sorted(set(current_columns) - set(saved_columns)) if expected_columns is not None else []
            changed_columns = []
            for name in sorted(set(saved_columns) & set(current_columns)):
                saved = saved_columns[name]
                current = current_columns[name]
                changes = [field for field in ("type", "nullable", "ordinal") if saved[field] != current[field]]
                if changes:
                    changed_columns.append({"name": name, "changes": changes})
            matches = descriptor["kind"] == kind and descriptor["fingerprint"] == fingerprint
            return {
                "status": "verified" if matches else "changed", "matches": matches,
                "profileId": profile_id, "database": database, "namespace": namespace, "relation": relation,
                "expectedKind": kind, "currentKind": descriptor["kind"],
                "expectedFingerprint": fingerprint, "currentFingerprint": descriptor["fingerprint"],
                "missingColumns": missing_columns, "addedColumns": added_columns, "changedColumns": changed_columns,
            }
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relation source could not be verified") from exc
        finally:
            self._close(connection)

    @postgres_execution("catalog")
    def verify_relation_sources(self, profile_id: str, sources: Any) -> dict[str, Any]:
        if not isinstance(sources, list) or not 1 <= len(sources) <= 50:
            raise ValidationError("sources must contain from 1 to 50 relation sources")
        normalized = [self._validate_relation_source(profile_id, source) for source in sources]
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            results = []
            for source, (database, namespace, relation, kind, fingerprint, expected_columns) in zip(sources, normalized):
                try:
                    descriptor = self._inspect_relation_connection(
                        connection, profile_id, database, namespace, relation, None, None,
                    )
                except NotFoundError:
                    results.append({
                        "source": source, "status": "missing", "matches": False,
                        "missingColumns": [column["name"] for column in expected_columns or []],
                        "addedColumns": [], "changedColumns": [],
                    })
                    continue
                current_columns = {column["name"]: column for column in descriptor["columns"]}
                saved_columns = {column["name"]: column for column in expected_columns or []}
                changed = [{
                    "name": name,
                    "changes": [field for field in ("type", "nullable", "ordinal") if saved_columns[name][field] != current_columns[name][field]],
                } for name in sorted(set(saved_columns) & set(current_columns))]
                changed = [item for item in changed if item["changes"]]
                matches = descriptor["kind"] == kind and descriptor["fingerprint"] == fingerprint
                results.append({
                    "source": source, "status": "verified" if matches else "changed", "matches": matches,
                    "expectedKind": kind, "currentKind": descriptor["kind"],
                    "expectedFingerprint": fingerprint, "currentFingerprint": descriptor["fingerprint"],
                    "missingColumns": sorted(set(saved_columns) - set(current_columns)),
                    "addedColumns": sorted(set(current_columns) - set(saved_columns)) if expected_columns is not None else [],
                    "changedColumns": changed,
                })
            connection.rollback()
            return {"results": results, "snapshot": "repeatable_read"}
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relation sources could not be verified") from exc
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            self._close(connection)

    @postgres_execution("read")
    def preview_relation_rows(
        self, profile_id: str, source: Any, offset: int = 0, limit: int = 20
    ) -> dict[str, Any]:
        database, namespace, relation, kind, fingerprint, _ = self._validate_relation_source(profile_id, source)
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10_000_000:
            raise ValidationError("offset must be an integer from 0 to 10000000")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValidationError("limit must be an integer from 1 to 50")
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION READ ONLY")
            self._execute_statement(connection, f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'")
            descriptor = self._inspect_relation_connection(
                connection, profile_id, database, namespace, relation, kind, fingerprint
            )
            column_names = [column["name"] for column in descriptor["columns"]]
            relation_sql = f"{quote_identifier(namespace)}.{quote_identifier(relation)}"
            column_sql = ", ".join(quote_identifier(name) for name in column_names)
            rows = self._execute_rows(
                connection, f"SELECT {column_sql} FROM {relation_sql} LIMIT %s OFFSET %s", (limit + 1, offset)
            )
            has_more = len(rows) > limit
            limited = self._limited_records(rows, column_names, max_rows=limit)
            page = limited["rows"]
            return {
                **descriptor,
                "rows": page,
                "offset": offset,
                "nextOffset": offset + len(page),
                "hasMore": has_more or limited["truncated"],
                "stableOrder": False,
                "truncated": has_more or limited["truncated"],
                "limitEvents": limited["limitEvents"],
            }
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "data_preview_failed", "PostgreSQL relation rows could not be read") from exc
        finally:
            self._close(connection)

    @postgres_execution("read")
    def execute_widget_query(self, profile_id: str, source: Any, query: Any) -> dict[str, Any]:
        database, namespace, relation, kind, fingerprint, source_columns = self._validate_relation_source(profile_id, source)
        if source_columns is None:
            raise ValidationError("widget query requires a current source column snapshot")
        try:
            normalized_query = normalize_query(query, source_columns)
        except QueryValidationError as exc:
            raise ValidationError(str(exc)) from exc
        profile = self._profile(profile_id)
        if profile["dbname"] != database:
            raise PostgresServiceError(409, "database_changed", "The saved profile database does not match the widget source")
        connection = self._connect_profile(profile)
        started = time.perf_counter()
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            self._execute_statement(connection, f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'")
            relation_sql = f"{quote_identifier(namespace)}.{quote_identifier(relation)}"
            self._execute_statement(connection, f"LOCK TABLE {relation_sql} IN ACCESS SHARE MODE")
            current_database = self._execute_rows(connection, "SELECT current_database() AS database")[0]["database"]
            if current_database != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            descriptor = self._inspect_relation_connection(
                connection, profile_id, database, namespace, relation, kind, fingerprint
            )
            try:
                normalized_query = normalize_query(normalized_query, descriptor["columns"])
            except QueryValidationError as exc:
                raise ValidationError(str(exc)) from exc
            compiled = compile_query(source, normalized_query, quote_identifier)
            rows = self._execute_rows(connection, compiled["sql"], tuple(compiled["parameters"]))
            limit = normalized_query["limit"]
            truncated = len(rows) > limit
            page = rows[:limit]
            limited = self._limited_rows(rows, compiled["aliases"], max_rows=limit)
            result_rows = limited["rows"]
            connection.rollback()
            return {
                "source": {key: source[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
                "queryVersion": 2,
                "columns": compiled["columns"],
                "rows": result_rows,
                "rowCount": len(result_rows),
                "limit": limit,
                "truncated": truncated or limited["truncated"],
                "limitEvents": limited["limitEvents"],
                "sql": compiled["sql"],
                "parameters": [self._json_cell(value) for value in compiled["parameters"]],
                "queryDurationMs": max(0, round((time.perf_counter() - started) * 1000)),
                "queriedAt": _utc_now(),
                "provenance": self._query_provenance(profile_id, profile, descriptor),
                "lineage": {
                    "dimensions": [{"id": item["id"], "sourceColumn": item["column"]} for item in normalized_query["dimensions"]],
                    "measures": [{"id": item["id"], "sourceColumn": item["column"], "aggregation": item["aggregation"], "distinct": item["distinct"]} for item in normalized_query["measures"]],
                    "filterGroups": [{
                        "id": group["id"],
                        "conditions": [{"id": item["id"], "sourceColumn": item["column"], "operator": item["operator"]} for item in group["conditions"]],
                    } for group in normalized_query["filters"]],
                },
            }
        except PostgresServiceError:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            raise PostgresServiceError(422, "aggregate_query_failed", "Aggregate query failed") from exc
        finally:
            self._close(connection)

    @postgres_execution("read")
    def execute_temporal_series(
        self, profile_id: str, source: Any, query: Any, action: Any, refresh_generation: Any,
        series: Any = None, window_start: Any = None,
    ) -> dict[str, Any]:
        database, namespace, relation, kind, fingerprint, source_columns = self._validate_relation_source(profile_id, source)
        if source_columns is None:
            raise ValidationError("temporal series requires a current source column snapshot")
        if action not in {"manifest", "window"}:
            raise ValidationError("temporal series action is invalid")
        if not isinstance(refresh_generation, str) or not 1 <= len(refresh_generation) <= 128:
            raise ValidationError("temporal series refresh generation is invalid")
        try:
            normalized_query = normalize_temporal_series(query, source_columns)
        except QueryValidationError as exc:
            raise ValidationError(str(exc)) from exc
        profile = self._profile(profile_id)
        if profile["dbname"] != database:
            raise PostgresServiceError(409, "database_changed", "The saved profile database does not match the widget source")
        connection = self._connect_profile(profile)
        started = time.perf_counter()
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            self._execute_statement(connection, "SET LOCAL TIME ZONE 'UTC'")
            self._execute_statement(connection, f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'")
            relation_sql = f"{quote_identifier(namespace)}.{quote_identifier(relation)}"
            self._execute_statement(connection, f"LOCK TABLE {relation_sql} IN ACCESS SHARE MODE")
            current_database = self._execute_rows(connection, "SELECT current_database() AS database")[0]["database"]
            if current_database != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            descriptor = self._inspect_relation_connection(
                connection, profile_id, database, namespace, relation, kind, fingerprint
            )
            try:
                normalized_query = normalize_temporal_series(query, descriptor["columns"])
            except QueryValidationError as exc:
                raise ValidationError(str(exc)) from exc

            if action == "manifest":
                if series is not None or window_start is not None:
                    raise ValidationError("temporal series manifest fields are invalid")
                compiled = compile_temporal_series_manifest(source, normalized_query, quote_identifier)
                rows = self._execute_rows(connection, compiled["sql"], tuple(compiled["parameters"]))
                bounds = rows[0] if rows else {"__schemer_min": None, "__schemer_max": None}
                minimum_raw = bounds.get("__schemer_min")
                maximum_raw = bounds.get("__schemer_max")
                point_count = int(bounds.get("__schemer_points") or 0)
                if minimum_raw is None or maximum_raw is None:
                    temporal = {
                        "dimensionId": normalized_query["dimensions"][0]["id"],
                        "sourceType": normalized_query["temporalSourceType"],
                        "interpretation": "utc",
                        "bucketSeconds": SERIES_BUCKET_SECONDS[0],
                        "windowBucketCount": min(SERIES_WINDOW_BUCKETS, normalized_query["limit"]),
                        "pointLimit": normalized_query["limit"],
                        "refreshGeneration": refresh_generation,
                        "expiresAtEpoch": math.ceil(self._clock() + self._plan_ttl),
                        "alignedStart": None,
                        "alignedEndExclusive": None,
                    }
                    series_identity = {**temporal}
                    series_identity["key"] = _series_key(self._temporal_series_secret, profile_id, profile, source, normalized_query, refresh_generation, temporal)
                    result_columns = compile_temporal_series_window(
                        source, normalized_query, quote_identifier, SERIES_BUCKET_SECONDS[0],
                        datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(seconds=SERIES_BUCKET_SECONDS[0]),
                        SERIES_WINDOW_BUCKETS,
                    )["columns"]
                    empty = True
                    domain = {"min": None, "max": None}
                else:
                    minimum = _series_datetime(minimum_raw)
                    maximum = _series_datetime(maximum_raw)
                    minimum_epoch = minimum.timestamp()
                    maximum_epoch = maximum.timestamp()

                    def aligned_bucket_count(bucket: int) -> int:
                        return math.floor(maximum_epoch / bucket) - math.floor(minimum_epoch / bucket) + 1

                    minimum_bucket = 86400 if normalized_query["temporalSourceType"].lower() == "date" else 60
                    bucket_limit = SERIES_MAX_TIMELINE_BUCKETS if point_count <= normalized_query["limit"] else normalized_query["limit"]
                    bucket_seconds = next((item for item in SERIES_BUCKET_SECONDS if item >= minimum_bucket and aligned_bucket_count(item) <= bucket_limit), None)
                    if bucket_seconds is None:
                        raw_bucket = max(minimum_bucket, math.ceil((maximum_epoch - minimum_epoch + 1) / max(1, bucket_limit)))
                        bucket_seconds = math.ceil(raw_bucket / minimum_bucket) * minimum_bucket if minimum_bucket == 86400 else raw_bucket
                        increment = minimum_bucket if minimum_bucket == 86400 else 1
                        while aligned_bucket_count(bucket_seconds) > bucket_limit:
                            bucket_seconds += increment
                    aligned_start = datetime.fromtimestamp(math.floor(minimum_epoch / bucket_seconds) * bucket_seconds, timezone.utc)
                    aligned_end = datetime.fromtimestamp((math.floor(maximum_epoch / bucket_seconds) + 1) * bucket_seconds, timezone.utc)
                    temporal = {
                        "dimensionId": normalized_query["dimensions"][0]["id"],
                        "sourceType": normalized_query["temporalSourceType"],
                        "interpretation": "utc",
                        "bucketSeconds": bucket_seconds,
                        "windowBucketCount": min(SERIES_WINDOW_BUCKETS, normalized_query["limit"]),
                        "pointLimit": normalized_query["limit"],
                        "refreshGeneration": refresh_generation,
                        "expiresAtEpoch": math.ceil(self._clock() + self._plan_ttl),
                        "alignedStart": _series_iso(aligned_start),
                        "alignedEndExclusive": _series_iso(aligned_end),
                    }
                    series_identity = {**temporal}
                    series_identity["key"] = _series_key(self._temporal_series_secret, profile_id, profile, source, normalized_query, refresh_generation, temporal)
                    result_columns = compile_temporal_series_window(
                        source, normalized_query, quote_identifier, bucket_seconds, aligned_start,
                        min(aligned_end, aligned_start + timedelta(seconds=bucket_seconds * temporal["windowBucketCount"])),
                        temporal["windowBucketCount"],
                    )["columns"]
                    empty = False
                    domain = {"min": _series_iso(minimum), "max": _series_iso(maximum)}
                connection.rollback()
                return {
                    "seriesVersion": 1,
                    "series": series_identity,
                    "domain": domain,
                    "empty": empty,
                    "columns": result_columns,
                    "refreshGeneration": refresh_generation,
                    "sql": compiled["sql"],
                    "parameters": [self._json_cell(value) for value in compiled["parameters"]],
                    "queryDurationMs": max(0, round((time.perf_counter() - started) * 1000)),
                    "queriedAt": _utc_now(),
                    "provenance": self._query_provenance(profile_id, profile, descriptor),
                    "lineage": {
                        "dimensions": [{"id": item["id"], "sourceColumn": item["column"]} for item in normalized_query["dimensions"]],
                        "measures": [{"id": item["id"], "sourceColumn": item["column"], "aggregation": item["aggregation"], "distinct": item["distinct"]} for item in normalized_query["measures"]],
                        "filterGroups": [{
                            "id": group["id"],
                            "conditions": [{"id": item["id"], "sourceColumn": item["column"], "operator": item["operator"]} for item in group["conditions"]],
                        } for group in normalized_query["filters"]],
                    },
                }

            required_series_fields = {
                "key", "dimensionId", "sourceType", "interpretation", "bucketSeconds",
                "windowBucketCount", "pointLimit", "refreshGeneration", "expiresAtEpoch", "alignedStart", "alignedEndExclusive",
            }
            if not isinstance(series, dict) or set(series) != required_series_fields:
                raise ValidationError("temporal series window descriptor is invalid")
            if series.get("dimensionId") != normalized_query["dimensions"][0]["id"] or series.get("sourceType") != normalized_query["temporalSourceType"] or series.get("interpretation") != "utc":
                raise ValidationError("temporal series window descriptor does not match the query")
            bucket_seconds = series.get("bucketSeconds")
            window_bucket_count = series.get("windowBucketCount")
            if isinstance(bucket_seconds, bool) or not isinstance(bucket_seconds, int) or bucket_seconds < 60:
                raise ValidationError("temporal series bucket is invalid")
            if isinstance(window_bucket_count, bool) or not isinstance(window_bucket_count, int) or not 1 <= window_bucket_count <= SERIES_WINDOW_BUCKETS:
                raise ValidationError("temporal series window size is invalid")
            if series.get("pointLimit") != normalized_query["limit"]:
                raise ValidationError("temporal series point limit does not match the query")
            if series.get("refreshGeneration") != refresh_generation:
                raise ValidationError("temporal series refresh generation is stale")
            expires_at = series.get("expiresAtEpoch")
            if isinstance(expires_at, bool) or not isinstance(expires_at, int) or expires_at < self._clock():
                raise PostgresServiceError(409, "temporal_series_expired", "The temporal series manifest expired; refresh the widget")
            aligned_start = _series_datetime(series.get("alignedStart"))
            aligned_end = _series_datetime(series.get("alignedEndExclusive"))
            if aligned_end <= aligned_start:
                raise ValidationError("temporal series domain is invalid")
            bucket_count = round((aligned_end - aligned_start).total_seconds() / bucket_seconds)
            if bucket_count < 1 or bucket_count > SERIES_MAX_TIMELINE_BUCKETS or aligned_start + timedelta(seconds=bucket_count * bucket_seconds) != aligned_end:
                raise ValidationError("temporal series domain is invalid or too large")
            temporal = {key: series[key] for key in required_series_fields if key != "key"}
            if not hmac.compare_digest(str(series.get("key")), _series_key(self._temporal_series_secret, profile_id, profile, source, normalized_query, refresh_generation, temporal)):
                raise PostgresServiceError(409, "temporal_series_stale", "The temporal series manifest is stale; refresh the widget")
            requested_start = _series_datetime(window_start)
            window_seconds = bucket_seconds * window_bucket_count
            elapsed = (requested_start - aligned_start).total_seconds()
            if requested_start < aligned_start or requested_start >= aligned_end or elapsed % window_seconds != 0:
                raise ValidationError("temporal series window is outside or misaligned with its domain")
            requested_end = min(aligned_end, requested_start + timedelta(seconds=window_seconds))
            maximum_rows = math.ceil((requested_end - requested_start).total_seconds() / bucket_seconds)
            compiled = compile_temporal_series_window(
                source, normalized_query, quote_identifier, bucket_seconds, requested_start, requested_end, maximum_rows
            )
            rows = self._execute_rows(connection, compiled["sql"], tuple(compiled["parameters"]))
            if len(rows) > maximum_rows:
                raise PostgresServiceError(422, "temporal_window_too_dense", "The temporal series window returned more than one row per bucket")
            temporal_rows = [{**row, compiled["aliases"][0]: _series_iso(_series_datetime(row.get(compiled["aliases"][0])))} for row in rows]
            limited = self._limited_rows(temporal_rows, compiled["aliases"], max_rows=maximum_rows)
            result_rows = limited["rows"]
            connection.rollback()
            return {
                "seriesVersion": 1,
                "seriesKey": series["key"],
                "range": {"start": _series_iso(requested_start), "endExclusive": _series_iso(requested_end)},
                "columns": compiled["columns"],
                "rows": result_rows,
                "rowCount": len(result_rows),
                "truncated": limited["truncated"],
                "limitEvents": limited["limitEvents"],
                "refreshGeneration": refresh_generation,
                "sql": compiled["sql"],
                "parameters": [self._json_cell(value) for value in compiled["parameters"]],
                "queryDurationMs": max(0, round((time.perf_counter() - started) * 1000)),
                "queriedAt": _utc_now(),
                "provenance": self._query_provenance(profile_id, profile, descriptor),
                "lineage": {
                    "dimensions": [{"id": item["id"], "sourceColumn": item["column"]} for item in normalized_query["dimensions"]],
                    "measures": [{"id": item["id"], "sourceColumn": item["column"], "aggregation": item["aggregation"], "distinct": item["distinct"]} for item in normalized_query["measures"]],
                    "filterGroups": [{
                        "id": group["id"],
                        "conditions": [{"id": item["id"], "sourceColumn": item["column"], "operator": item["operator"]} for item in group["conditions"]],
                    } for group in normalized_query["filters"]],
                },
            }
        except PostgresServiceError:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            raise PostgresServiceError(422, "temporal_series_failed", "Temporal series query failed") from exc
        finally:
            self._close(connection)

    @postgres_execution("read")
    def execute_relation_detail(
        self,
        profile_id: str,
        source: Any,
        query: Any,
        selection: Any,
        detail: Any,
        offset: Any,
        limit: Any,
        sort: Any,
        searches: Any,
    ) -> dict[str, Any]:
        database, namespace, relation, kind, fingerprint, source_columns = self._validate_relation_source(profile_id, source)
        if source_columns is None:
            raise ValidationError("detail query requires a current source column snapshot")
        try:
            normalized_query = normalize_query(query, source_columns)
            normalized_request = normalize_detail_request(
                selection, detail, offset, limit, sort, searches, normalized_query, source_columns
            )
        except QueryValidationError as exc:
            raise ValidationError(str(exc)) from exc
        profile = self._profile(profile_id)
        if profile["dbname"] != database:
            raise PostgresServiceError(409, "database_changed", "The saved profile database does not match the detail source")
        connection = self._connect_profile(profile)
        started = time.perf_counter()
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            self._execute_statement(connection, f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'")
            relation_sql = f"{quote_identifier(namespace)}.{quote_identifier(relation)}"
            self._execute_statement(connection, f"LOCK TABLE {relation_sql} IN ACCESS SHARE MODE")
            current_database = self._execute_rows(connection, "SELECT current_database() AS database")[0]["database"]
            if current_database != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            descriptor = self._inspect_relation_connection(
                connection, profile_id, database, namespace, relation, kind, fingerprint
            )
            try:
                normalized_query = normalize_query(normalized_query, descriptor["columns"])
                normalized_request = normalize_detail_request(
                    normalized_request["selection"], normalized_request["detail"],
                    normalized_request["offset"], normalized_request["limit"], normalized_request["sort"],
                    normalized_request["searches"], normalized_query, descriptor["columns"],
                )
            except QueryValidationError as exc:
                raise ValidationError(str(exc)) from exc
            compiled = compile_detail_query(source, normalized_query, normalized_request, descriptor["columns"], quote_identifier)
            count_rows = self._execute_rows(connection, compiled["countSql"], tuple(compiled["countParameters"]))
            matching_row_count = int(count_rows[0]["__schemer_count"]) if count_rows else 0
            rows = self._execute_rows(connection, compiled["sql"], tuple(compiled["parameters"]))
            limited = self._limited_rows(rows, compiled["aliases"], max_rows=normalized_request["limit"])
            result_rows = limited["rows"]
            connection.rollback()
            duration_ms = max(0, round((time.perf_counter() - started) * 1000))
            return {
                "source": {key: source[key] for key in ("profileId", "database", "namespace", "relation", "kind", "fingerprint")},
                "queryVersion": 2,
                "detailVersion": 1,
                "columns": compiled["columns"],
                "rows": result_rows,
                "matchingRowCount": matching_row_count,
                "offset": normalized_request["offset"],
                "limit": normalized_request["limit"],
                "hasMore": normalized_request["offset"] + len(result_rows) < matching_row_count,
                "truncated": limited["truncated"],
                "limitEvents": limited["limitEvents"],
                "sql": compiled["sql"],
                "parameters": [self._json_cell(value) for value in compiled["parameters"]],
                "countSql": compiled["countSql"],
                "countParameters": [self._json_cell(value) for value in compiled["countParameters"]],
                "queryDurationMs": duration_ms,
                "queriedAt": _utc_now(),
                "provenance": self._query_provenance(profile_id, profile, descriptor),
            }
        except PostgresServiceError:
            try:
                connection.rollback()
            except Exception:
                pass
            raise
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            raise PostgresServiceError(422, "detail_query_failed", _safe_sql_query_failure(exc)) from exc
        finally:
            self._close(connection)

    @postgres_execution("read")
    def preview_table_data(
        self,
        profile_id: str,
        namespace: str,
        table_name: str,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        namespace = self._validate_namespace(namespace)
        table_name = self._validate_relation_name(table_name)
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10_000_000:
            raise ValidationError("offset must be an integer from 0 to 10000000")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValidationError("limit must be an integer from 1 to 50")

        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION READ ONLY")
            columns = self._execute_rows(connection, """
                SELECT a.attname AS column_name,
                       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                       NOT a.attnotnull AS nullable,
                       a.attnum AS ordinal
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_attribute a
                  ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p')
                ORDER BY a.attnum
            """, (namespace, table_name))
            if not columns:
                raise NotFoundError(f"Table {namespace}.{table_name} was not found")
            primary_rows = self._execute_rows(connection, """
                SELECT a.attname AS column_name
                FROM pg_catalog.pg_constraint con
                JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN unnest(con.conkey) WITH ORDINALITY key(attnum, ord) ON true
                JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = key.attnum
                WHERE n.nspname = %s AND c.relname = %s AND con.contype = 'p'
                ORDER BY key.ord
            """, (namespace, table_name))
            primary_key = [row["column_name"] for row in primary_rows]
            order_sql = " ORDER BY " + ", ".join(quote_identifier(name) for name in primary_key) if primary_key else ""
            table_sql = f"{quote_identifier(namespace)}.{quote_identifier(table_name)}"
            rows = self._execute_rows(
                connection,
                f"SELECT * FROM {table_sql}{order_sql} LIMIT %s OFFSET %s",
                (limit + 1, offset),
            )
            has_more = len(rows) > limit
            column_names = [column["column_name"] for column in columns]
            limited = self._limited_records(rows, column_names, max_rows=limit)
            page = limited["rows"]
            return {
                "namespace": namespace,
                "table": table_name,
                "columns": [
                    {
                        "name": column["column_name"],
                        "type": column["data_type"],
                        "nullable": bool(column["nullable"]),
                        "primary": column["column_name"] in primary_key,
                    }
                    for column in columns
                ],
                "primaryKey": primary_key,
                "rows": page,
                "offset": offset,
                "nextOffset": offset + len(page),
                "hasMore": has_more or limited["truncated"],
                "truncated": has_more or limited["truncated"],
                "limitEvents": limited["limitEvents"],
                "stableOrder": bool(primary_key),
            }
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "data_preview_failed", "PostgreSQL table data could not be read") from exc
        finally:
            self._close(connection)

    @postgres_execution("read")
    def execute_read_only_sql(
        self,
        profile_id: str,
        namespace: str,
        statement: Any,
        *,
        database: Any = None,
        expected_profile_fingerprint: Any = None,
        allow_explain: bool = True,
        max_rows: int = 500,
        max_columns: int = 100,
        max_result_bytes: int = 1024 * 1024,
    ) -> dict[str, Any]:
        namespace = self._validate_namespace(namespace)
        profile = self._profile(profile_id)
        if expected_profile_fingerprint is not None and expected_profile_fingerprint != _profile_context_fingerprint(profile_id, profile):
            raise PostgresServiceError(409, "profile_changed", "The saved PostgreSQL profile changed after query confirmation")
        database = profile["dbname"] if database is None else self._validate_database(database)
        if profile["dbname"] != database:
            raise PostgresServiceError(409, "database_changed", "The saved profile database does not match the requested database")
        if not isinstance(allow_explain, bool):
            raise ValueError("allow_explain must be boolean")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (max_rows, max_columns, max_result_bytes)):
            raise ValueError("SQL result limits must be positive integers")
        if not isinstance(statement, str) or not statement.strip():
            raise ValidationError("sql must be a non-empty string")
        if "\x00" in statement or len(statement) > 100_000:
            raise ValidationError("sql must be at most 100000 characters and contain no null bytes")
        statement = _single_sql_statement(statement, "SQL query")
        allowed_prefixes = "SELECT|WITH|VALUES|TABLE" + ("|EXPLAIN" if allow_explain else "")
        if not re.match(rf"^\s*(?:{allowed_prefixes})\b", statement, re.I):
            suffix = ", or EXPLAIN" if allow_explain else ""
            raise ValidationError(f"Only read-only SELECT, WITH, VALUES, or TABLE{suffix} queries are allowed")

        connection = self._connect_profile(profile)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'")
            cursor.execute("SELECT current_database() AS database")
            current_rows = cursor.fetchall()
            current_database = current_rows[0]["database"] if current_rows and isinstance(current_rows[0], dict) else current_rows[0][0]
            if current_database != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s) AS exists", (namespace,))
            namespace_rows = cursor.fetchall()
            namespace_exists = namespace_rows[0]["exists"] if namespace_rows and isinstance(namespace_rows[0], dict) else namespace_rows[0][0]
            if not namespace_exists:
                raise NotFoundError("Namespace was not found")
            cursor.execute(
                "SELECT pg_catalog.set_config('search_path', %s, true)",
                (f"pg_catalog, {quote_identifier(namespace)}",),
            )
            cursor.execute(statement)
            if cursor.description is None:
                raise ValidationError("The SQL query did not return a result set")
            names = [item.name if hasattr(item, "name") else item[0] for item in cursor.description]
            if len(names) > max_columns:
                raise PostgresServiceError(422, "sql_result_too_wide", f"SQL result exceeds the {max_columns}-column limit")
            fetchmany = getattr(cursor, "fetchmany", None)
            raw_rows = fetchmany(max_rows + 1) if fetchmany else cursor.fetchall()[:max_rows + 1]
            truncated = len(raw_rows) > max_rows
            limits = ResultLimits(
                max_cell_bytes=self._result_limiter.limits.max_cell_bytes,
                max_row_bytes=self._result_limiter.limits.max_row_bytes,
                max_result_bytes=max_result_bytes,
                max_nesting=self._result_limiter.limits.max_nesting,
                max_collection_items=self._result_limiter.limits.max_collection_items,
            )
            limiter = ResultLimiter(limits)
            base = {
                "profileId": profile_id,
                "database": database,
                "namespace": namespace,
                "columns": [{"name": name} for name in names],
                "rows": [],
                "rowCount": 0,
                "truncated": truncated,
                "maxRows": max_rows,
                "maxColumns": max_columns,
                "maxResultBytes": max_result_bytes,
            }
            try:
                limited = limiter.rows(
                    raw_rows, names, max_rows=max_rows,
                    envelope=lambda values: {**base, "rows": values, "rowCount": len(values)},
                )
            except ResultLimitError as exc:
                raise self._limit_error(exc) from exc
            base["rows"] = limited["rows"]
            base["rowCount"] = len(limited["rows"])
            base["truncated"] = truncated or limited["truncated"]
            base["limitEvents"] = limited["limitEvents"]
            if len(json.dumps(base, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > max_result_bytes:
                raise PostgresServiceError(422, "sql_result_too_large", "SQL result metadata exceeds the byte limit")
            return base
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(422, "sql_query_failed", _safe_sql_query_failure(exc)) from exc
        finally:
            if cursor is not None:
                close = getattr(cursor, "close", None)
                if close:
                    close()
            rollback = getattr(connection, "rollback", None)
            if rollback:
                rollback()
            self._close(connection)

    # ---- introspection --------------------------------------------------

    @staticmethod
    def _column_role_suggestions(column_name: str, type_category: Any, type_name: Any) -> list[str]:
        category = type_category if isinstance(type_category, str) else ""
        name = column_name.lower()
        identifier = type_name == "uuid" or name == "id" or name.endswith("_id")
        if identifier and category in {"N", "S", "U"}:
            return ["dimension", "identifier"]
        if category == "D":
            return ["dimension", "date"]
        if category == "N":
            return ["dimension", "measure"]
        if category in {"B", "E", "S"}:
            return ["dimension"]
        return []

    @staticmethod
    def _validate_database(database: Any) -> str:
        if (
            not isinstance(database, str) or not NAME_RE.fullmatch(database)
            or len(database.encode("utf-8")) > 63
        ):
            raise ValidationError("database must be a valid PostgreSQL name up to 63 bytes")
        return database

    @staticmethod
    def _validate_namespace(namespace: Any) -> str:
        if (
            not isinstance(namespace, str) or not NAME_RE.fullmatch(namespace)
            or len(namespace.encode("utf-8")) > 63
        ):
            raise ValidationError("namespace must be a valid PostgreSQL name up to 63 bytes")
        return namespace

    @staticmethod
    def _validate_relation_name(table_name: Any) -> str:
        if (
            not isinstance(table_name, str) or not NAME_RE.fullmatch(table_name)
            or len(table_name.encode("utf-8")) > 63
        ):
            raise ValidationError("relation must be a valid PostgreSQL name up to 63 bytes")
        return table_name

    @postgres_execution("catalog")
    def introspect(self, profile_id: str, namespace: str) -> dict[str, Any]:
        namespace = self._validate_namespace(namespace)
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            schema = self._introspect_connection(connection, profile_id, namespace)
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL schema introspection failed") from exc
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            self._close(connection)
        return schema

    def catalog_status(self, profile_id: str, namespace: str) -> dict[str, Any]:
        schema = self.introspect(profile_id, namespace)
        return {
            "profileId": profile_id,
            "database": schema["postgres"]["database"],
            "namespace": namespace,
            "fingerprint": schema["postgres"]["fingerprint"],
            "tables": len(schema["tables"]),
            "relationships": len(schema["relationships"]),
            "functions": len(schema["functions"]),
            "views": len(schema.get("views", [])),
        }

    def _introspect_connection(self, connection: Any, profile_id: str, namespace: str) -> dict[str, Any]:
        meta = self._execute_rows(connection, """
            SELECT current_database() AS database,
                   current_setting('server_version') AS server_version,
                   current_setting('server_version_num') AS server_version_num,
                   current_setting('TimeZone') AS timezone
        """)[0]
        self._require_namespace(connection, namespace)
        table_rows = self._execute_rows(connection, """
            SELECT c.oid AS table_oid, c.relname AS table_name, c.relkind AS relation_kind,
                   c.relispartition AS is_partition,
                   CASE WHEN c.relkind = 'p' THEN pg_catalog.pg_get_partkeydef(c.oid) END AS partition_key,
                   parent.relname AS parent_table
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_inherits inh ON inh.inhrelid = c.oid
            LEFT JOIN pg_catalog.pg_class parent ON parent.oid = inh.inhparent
            WHERE n.nspname = %s AND c.relkind IN ('r','p')
            ORDER BY c.relname
        """, (namespace,))
        columns = self._execute_rows(connection, """
            SELECT c.relname AS table_name, a.attname AS column_name, a.attnum AS ordinal,
                   pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                   NOT a.attnotnull AS nullable,
                   pg_catalog.pg_get_expr(d.adbin, d.adrelid, true) AS default_sql,
                   a.attidentity AS identity_kind, a.attgenerated AS generated_kind
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
            WHERE n.nspname = %s AND c.relkind IN ('r','p')
            ORDER BY c.relname, a.attnum
        """, (namespace,))
        constraints = self._execute_rows(connection, """
            SELECT con.conname AS constraint_name, src.relname AS table_name, con.contype AS constraint_type,
                   ARRAY(SELECT att.attname FROM unnest(con.conkey) WITH ORDINALITY key(attnum, ord)
                         JOIN pg_catalog.pg_attribute att ON att.attrelid=con.conrelid AND att.attnum=key.attnum
                         ORDER BY key.ord) AS columns,
                   tn.nspname AS target_namespace, target.relname AS target_table,
                   ARRAY(SELECT att.attname FROM unnest(con.confkey) WITH ORDINALITY key(attnum, ord)
                         JOIN pg_catalog.pg_attribute att ON att.attrelid=con.confrelid AND att.attnum=key.attnum
                         ORDER BY key.ord) AS target_columns,
                    con.confupdtype AS update_action, con.confdeltype AS delete_action,
                    con.confmatchtype AS match_type, con.convalidated AS validated,
                    con.condeferrable AS deferrable, con.condeferred AS initially_deferred,
                   pg_catalog.pg_get_constraintdef(con.oid, true) AS definition
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class src ON src.oid=con.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=src.relnamespace
            LEFT JOIN pg_catalog.pg_class target ON target.oid=con.confrelid
            LEFT JOIN pg_catalog.pg_namespace tn ON tn.oid=target.relnamespace
            WHERE n.nspname=%s AND con.contype IN ('p','u','f','c')
            ORDER BY src.relname, con.contype, con.conname
        """, (namespace,))
        indexes = self._execute_rows(connection, """
            SELECT tab.relname AS table_name, idx.relname AS index_name,
                   pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
                   i.indisunique AS is_unique, am.amname AS method
            FROM pg_catalog.pg_index i
            JOIN pg_catalog.pg_class idx ON idx.oid=i.indexrelid
            JOIN pg_catalog.pg_class tab ON tab.oid=i.indrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=tab.relnamespace
            JOIN pg_catalog.pg_am am ON am.oid=idx.relam
            LEFT JOIN pg_catalog.pg_constraint con ON con.conindid=i.indexrelid
            WHERE n.nspname=%s AND con.oid IS NULL
            ORDER BY tab.relname, idx.relname
        """, (namespace,))
        routines = self._execute_rows(connection, """
            SELECT p.proname AS name, p.prokind AS kind,
                   pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,
                   pg_catalog.pg_get_function_arguments(p.oid) AS arguments,
                   pg_catalog.pg_get_function_result(p.oid) AS return_type,
                   l.lanname AS language, pg_catalog.pg_get_functiondef(p.oid) AS definition
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            JOIN pg_catalog.pg_language l ON l.oid=p.prolang
            WHERE n.nspname=%s AND p.prokind IN ('f','p')
            ORDER BY p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid)
        """, (namespace,))
        views = self._execute_rows(connection, """
            SELECT c.relname AS name, c.relkind AS kind,
                   pg_catalog.pg_get_viewdef(c.oid, true) AS query_definition
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relkind IN ('v','m') ORDER BY c.relname
        """, (namespace,))
        triggers = self._execute_rows(connection, """
            SELECT c.relname AS table_name, t.tgname AS trigger_name,
                    pg_catalog.pg_get_triggerdef(t.oid, true) AS definition,
                    t.tgenabled AS enabled
            FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname, t.tgname
        """, (namespace,))
        collections = {
            "tables": table_rows, "columns": columns, "constraints": constraints, "indexes": indexes,
            "routines": routines, "views": views, "triggers": triggers,
        }
        for label, rows in collections.items():
            if len(rows) > self._result_limiter.limits.max_collection_items:
                raise PostgresServiceError(
                    422, "catalog_collection_too_large", "PostgreSQL catalog collection exceeds the item limit",
                    {"policy": "reject", "path": f"$.{label}", "limit": self._result_limiter.limits.max_collection_items, "actual": len(rows)},
                )
        for label, rows, fields in (
            ("columns", columns, ("default_sql",)),
            ("constraints", constraints, ("definition",)),
            ("indexes", indexes, ("definition",)),
            ("routines", routines, ("identity_arguments", "arguments", "return_type", "definition")),
            ("views", views, ("query_definition",)),
            ("triggers", triggers, ("definition",)),
        ):
            for index, row in enumerate(rows):
                for field in fields:
                    value = row.get(field)
                    if isinstance(value, str) and len(value.encode("utf-8")) > self._result_limiter.limits.max_cell_bytes:
                        raise PostgresServiceError(
                            422, "catalog_definition_too_large", "PostgreSQL catalog definition exceeds the byte limit",
                            {"policy": "reject", "path": f"$.{label}[{index}].{field}", "limit": self._result_limiter.limits.max_cell_bytes, "actual": len(value.encode("utf-8"))},
                        )
        return self._build_schema(
            profile_id, namespace, meta, columns, constraints, indexes, routines, views, triggers,
            [row["table_name"] for row in table_rows], table_rows,
        )

    def _build_schema(
        self, profile_id: str, namespace: str, meta: dict[str, Any], columns: list[dict[str, Any]],
        constraints: list[dict[str, Any]], indexes: list[dict[str, Any]], routines: list[dict[str, Any]],
        views: list[dict[str, Any]], triggers: list[dict[str, Any]], table_names: list[str] | None = None,
        table_metadata: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        table_names = sorted(set(table_names or []) | {row["table_name"] for row in columns})
        table_metadata_by_name = {row["table_name"]: row for row in (table_metadata or [])}
        tables: list[dict[str, Any]] = []
        table_map: dict[str, dict[str, Any]] = {}
        column_map: dict[tuple[str, str], dict[str, Any]] = {}
        columns_by_table: dict[str, list[dict[str, Any]]] = {name: [] for name in table_names}
        for row in columns:
            columns_by_table[row["table_name"]].append(row)
        row_width = max(1, int(len(table_names) ** 0.5 + 0.999))
        for index, table_name in enumerate(table_names):
            table_id = _semantic_id("table", namespace, table_name)
            table_columns = []
            for row in columns_by_table[table_name]:
                column = {
                    "id": _semantic_id("column", namespace, table_name, row["column_name"]),
                    "name": row["column_name"], "type": row["data_type"], "primary": False,
                    "nullable": bool(row["nullable"]), "unique": False,
                    "default": row.get("default_sql") or "", "ordinal": row["ordinal"],
                    "postgres": {"identity": row.get("identity_kind") or "", "generated": row.get("generated_kind") or ""},
                }
                table_columns.append(column)
                column_map[(table_name, row["column_name"])] = column
            table = {
                "id": table_id, "name": table_name, "namespace": namespace,
                "x": 100 + (index % row_width) * 370, "y": 100 + (index // row_width) * 360,
                "color": COLORS[index % len(COLORS)], "columns": table_columns,
                "primaryKey": None, "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [],
                "postgres": {
                    "liveOid": table_metadata_by_name.get(table_name, {}).get("table_oid"),
                    "partitioned": table_metadata_by_name.get(table_name, {}).get("relation_kind") == "p",
                    "isPartition": bool(table_metadata_by_name.get(table_name, {}).get("is_partition")),
                    "partitionKey": table_metadata_by_name.get(table_name, {}).get("partition_key"),
                    "parentTable": table_metadata_by_name.get(table_name, {}).get("parent_table"),
                },
            }
            tables.append(table)
            table_map[table_name] = table

        action_names = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
        relationships = []
        for row in constraints:
            table = table_map.get(row["table_name"])
            if table is None:
                continue
            names = list(row.get("columns") or [])
            column_ids = [column_map[(row["table_name"], name)]["id"] for name in names]
            common = {
                "id": _semantic_id("constraint", namespace, row["table_name"], row["constraint_name"]),
                "name": row["constraint_name"], "columnIds": column_ids,
                "definition": row["definition"],
                "validated": bool(row.get("validated", True)),
                "deferrable": bool(row.get("deferrable")),
                "initiallyDeferred": bool(row.get("initially_deferred")),
            }
            kind = row["constraint_type"]
            if kind == "p":
                table["primaryKey"] = common
                for name in names:
                    column_map[(row["table_name"], name)]["primary"] = True
                    column_map[(row["table_name"], name)]["nullable"] = False
                    column_map[(row["table_name"], name)]["unique"] = len(names) == 1
            elif kind == "u":
                table["uniqueConstraints"].append(common)
                if len(names) == 1:
                    column_map[(row["table_name"], names[0])]["unique"] = True
            elif kind == "c":
                table["checks"].append(common)
            elif kind == "f":
                target_table = table_map.get(row.get("target_table")) if row.get("target_namespace") == namespace else None
                target_names = list(row.get("target_columns") or [])
                target_ids = [
                    column_map[(row["target_table"], name)]["id"]
                    if target_table and (row["target_table"], name) in column_map
                    else _semantic_id("column", row.get("target_namespace"), row.get("target_table"), name)
                    for name in target_names
                ]
                relation = {
                    "id": common["id"], "name": row["constraint_name"], "constraintName": row["constraint_name"],
                    "fromTableId": table["id"],
                    "toTableId": target_table["id"] if target_table else _semantic_id("table", row.get("target_namespace"), row.get("target_table")),
                    "targetNamespace": row.get("target_namespace"), "targetTableName": row.get("target_table"),
                    "targetColumnNames": target_names,
                    "definition": row["definition"], "onUpdate": action_names.get(row.get("update_action"), row.get("update_action")),
                    "onDelete": action_names.get(row.get("delete_action"), row.get("delete_action")),
                    "deferrable": bool(row.get("deferrable")), "initiallyDeferred": bool(row.get("initially_deferred")),
                    "matchType": {"f": "FULL", "p": "PARTIAL", "s": "SIMPLE"}.get(row.get("match_type"), "SIMPLE"),
                    "validated": bool(row.get("validated", True)),
                }
                if len(column_ids) == 1:
                    relation.update(fromColumnId=column_ids[0], toColumnId=target_ids[0])
                else:
                    relation.update(fromColumnIds=column_ids, toColumnIds=target_ids)
                relationships.append(relation)
        for row in indexes:
            table = table_map.get(row["table_name"])
            if table:
                table["indexes"].append({
                    "id": _semantic_id("index", namespace, row["index_name"]), "name": row["index_name"],
                    "definition": row["definition"], "unique": bool(row["is_unique"]), "method": row["method"],
                })
        for row in triggers:
            table = table_map.get(row["table_name"])
            if table:
                table["triggers"].append({
                    "id": _semantic_id("trigger", namespace, row["table_name"], row["trigger_name"]),
                    "name": row["trigger_name"], "definition": row["definition"], "enabled": row.get("enabled", "O"),
                })
        function_items = [{
            "id": _semantic_id("routine", namespace, row["kind"], row["name"], row["identity_arguments"]),
            "name": row["name"], "namespace": namespace,
            "kind": "procedure" if row["kind"] == "p" else "function",
            "identityArguments": row["identity_arguments"], "arguments": row["arguments"],
            "returnType": row.get("return_type") or "", "language": row["language"], "definition": row["definition"],
        } for row in routines]
        view_items = []
        for row in views:
            materialized = row["kind"] == "m"
            prefix = "CREATE MATERIALIZED VIEW" if materialized else "CREATE OR REPLACE VIEW"
            view_items.append({
                "id": _semantic_id("view", namespace, row["name"]), "name": row["name"], "namespace": namespace,
                "materialized": materialized, "queryDefinition": row["query_definition"],
                "definition": f"{prefix} {quote_identifier(namespace)}.{quote_identifier(row['name'])} AS\n{row['query_definition']}",
            })
        schema = {
            "projectName": f"{meta['database']}.{namespace}", "tables": tables,
            "relationships": relationships, "functions": function_items, "views": view_items,
            "postgres": {
                "sourceProfileId": profile_id, "database": meta["database"], "namespace": namespace,
                "serverVersion": meta["server_version"], "serverVersionNum": str(meta["server_version_num"]),
                "timeZone": meta.get("timezone") or "UTC",
                "importedAt": _utc_now(),
            },
        }
        if any(table["postgres"]["partitioned"] or table["postgres"]["isPartition"] for table in tables):
            schema["postgres"]["unsupportedMigrations"] = ["partitioned tables"]
        schema["postgres"]["fingerprint"] = canonical_fingerprint(schema)
        return schema

    # ---- preview --------------------------------------------------------

    def _require_schema(self, schema: Any) -> dict[str, Any]:
        if not isinstance(schema, dict) or not isinstance(schema.get("tables"), list):
            raise ValidationError("desired_schema must contain a tables array")
        for field in ("relationships", "functions", "views"):
            if field in schema and not isinstance(schema[field], list):
                raise ValidationError(f"desired_schema.{field} must be an array")
        tables = self._named(schema["tables"], "table")
        relation_names = {name: f"table {name}" for name in tables}
        for table_name, table in tables.items():
            objects = []
            objects.extend((name, "primary key") for name in self._constraint_map(table, "primary key"))
            objects.extend((name, "unique constraint") for name in self._constraint_map(table, "unique constraint"))
            objects.extend((name, "index") for name in self._named(table.get("indexes", []), "index"))
            for name, kind in objects:
                if not isinstance(name, str) or not name or "\x00" in name or len(name.encode("utf-8")) > 63:
                    raise ValidationError(f"{kind.title()} on table {table_name} has an invalid PostgreSQL name")
                owner = f"{kind} on table {table_name}"
                if name in relation_names:
                    raise ValidationError(f"PostgreSQL relation name {name} is used by both {relation_names[name]} and {owner}")
                relation_names[name] = owner
        return schema

    @staticmethod
    def _named(items: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
        result = {}
        for item in items:
            if (
                not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]
                or "\x00" in item["name"] or len(item["name"].encode("utf-8")) > 63
            ):
                raise ValidationError(f"Every {kind} must have a name")
            if item["name"] in result:
                raise ValidationError(f"Duplicate {kind} name: {item['name']}")
            result[item["name"]] = item
        return result

    @staticmethod
    def _column_rename_pairs(live: dict[str, Any], desired: dict[str, Any]) -> dict[str, str]:
        live_columns = {column.get("name"): column for column in live.get("columns", [])}
        desired_columns = {column.get("name"): column for column in desired.get("columns", [])}
        live_only = set(live_columns) - set(desired_columns)
        desired_only = set(desired_columns) - set(live_columns)
        desired_by_id: dict[str, list[str]] = {}
        for name in desired_only:
            column_id = desired_columns[name].get("id")
            if isinstance(column_id, str) and column_id:
                desired_by_id.setdefault(column_id, []).append(name)
        pairs = {}
        matched_desired = set()
        for live_name in sorted(live_only):
            column_id = live_columns[live_name].get("id")
            candidates = [
                name for name in desired_by_id.get(column_id, [])
                if name not in matched_desired
                and _normalized_type(live_columns[live_name].get("type", ""))
                == _normalized_type(desired_columns[name].get("type", ""))
            ]
            if len(candidates) == 1:
                pairs[live_name] = candidates[0]
                matched_desired.add(candidates[0])
        return pairs

    def _normalize_live_column_names(self, live: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
        rename_pairs = self._column_rename_pairs(live, desired)
        if not rename_pairs:
            return live
        normalized = copy.deepcopy(live)
        for column in normalized.get("columns", []):
            if column.get("name") in rename_pairs:
                column["name"] = rename_pairs[column["name"]]
        return normalized

    @staticmethod
    def _column_names(table: dict[str, Any], ids: list[str]) -> list[str]:
        by_id = {column.get("id"): column["name"] for column in table.get("columns", [])}
        try:
            return [by_id[item] for item in ids]
        except KeyError as exc:
            raise ValidationError(f"Constraint on table {table['name']} references an unknown column ID") from exc

    @postgres_execution("write")
    def preview(
        self, profile_id: str, namespace: str, desired_schema: dict[str, Any], allow_destructive: bool = False,
        *, persist: bool = True,
    ) -> dict[str, Any]:
        namespace = self._validate_namespace(namespace)
        desired = self._require_schema(copy.deepcopy(desired_schema))
        if not isinstance(allow_destructive, bool):
            raise ValidationError("allow_destructive must be boolean")
        if not isinstance(persist, bool):
            raise ValidationError("persist must be boolean")
        profile_fingerprint = self._profile_fingerprint(self._profile(profile_id))
        live = self.introspect(profile_id, namespace)
        if self._profile_fingerprint(self._profile(profile_id)) != profile_fingerprint:
            raise ConflictError("profile_changed", "Connection profile changed during preview")
        if any((table.get("postgres") or {}).get("partitioned") or (table.get("postgres") or {}).get("isPartition") for table in live.get("tables", []) + desired.get("tables", [])):
            raise ValidationError("Partitioned tables can be imported but require manual migrations")
        reordered_tables = self._column_reorder_tables(live, desired)
        tables_with_rows = self._tables_with_rows(profile_id, namespace, reordered_tables) if reordered_tables else set()
        for table in live.get("tables", []):
            if table.get("name") in reordered_tables:
                table.setdefault("postgres", {})["hasRows"] = table["name"] in tables_with_rows
        steps, warnings = self._diff(namespace, live, desired, allow_destructive)
        plan_id = "plan_" + secrets.token_hex(16)
        now = self._clock()
        stored = {
            "id": plan_id, "profileId": profile_id, "database": live.get("postgres", {}).get("database"), "namespace": namespace,
            "liveFingerprint": live["postgres"]["fingerprint"], "allowDestructive": allow_destructive,
            "profileFingerprint": profile_fingerprint,
            "destructive": any(step["destructive"] for step in steps), "steps": copy.deepcopy(steps),
            "warnings": list(warnings), "createdAt": now, "expiresAt": now + self._plan_ttl,
            "desiredSchema": copy.deepcopy(desired),
        }
        if persist:
            raise PostgresServiceError(503, "durable_migrations_unavailable", "Apply-capable preview requires the durable migration coordinator")
        public = self._public_plan(stored)
        if not persist:
            public.update({"id": None, "previewOnly": True})
        return public

    @postgres_execution("write")
    def preview_ai_migration(self, operation_id: str, profile_id: str, database: str, namespace: str, desired_schema: dict[str, Any], allow_destructive: bool, schema_binding: dict[str, Any]) -> dict[str, Any]:
        return self._migrations.preview_ai_migration(operation_id, profile_id, database, namespace, desired_schema, allow_destructive, schema_binding)

    @postgres_execution("write")
    def preview_ai_insert_rows(self, operation_id: str, profile_id: str, database: str, namespace: str, relation: str, rows: list[dict[str, Any]], schema_binding: dict[str, Any]) -> dict[str, Any]:
        return self._migrations.preview_ai_insert_rows(profile_id, database, namespace, relation, rows, schema_binding)

    @postgres_execution("write")
    def preview_ai_create_view(self, operation_id: str, profile_id: str, database: str, namespace: str, relation: str, definition: str, schema_binding: dict[str, Any]) -> dict[str, Any]:
        return self._migrations.preview_ai_create_view(profile_id, database, namespace, relation, definition, schema_binding)

    def _inspect_ai_insert_target(self, connection: Any, database: str, namespace: str, relation: str, requested_columns: list[str]) -> dict[str, Any]:
        rows = self._execute_rows(connection, """
            SELECT current_database() AS database, c.oid AS live_oid, c.relkind AS catalog_kind,
                   pg_catalog.has_table_privilege(c.oid, 'INSERT') AS can_insert
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p')
        """, (namespace, relation))
        if len(rows) != 1:
            raise NotFoundError(f"Table {namespace}.{relation} was not found")
        relation_row = rows[0]
        if relation_row.get("database") != database:
            raise ConflictError("database_changed", "Connected PostgreSQL database does not match the requested database")
        if relation_row.get("can_insert") is not True:
            raise PostgresServiceError(403, "insert_not_permitted", "The selected role cannot insert into this table")
        if relation_row.get("catalog_kind") == "p":
            raise ValidationError("AI row insertion into partitioned tables is unsupported because routed partition effects cannot be bounded safely")
        column_rows = self._execute_rows(connection, """
            SELECT a.attname AS name, pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
                   NOT a.attnotnull AS nullable, a.attnum AS ordinal,
                        pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default,
                       a.attidentity AS identity, a.attgenerated AS generated
            FROM pg_catalog.pg_attribute a
            LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum
        """, (relation_row["live_oid"],))
        by_name = {row["name"]: row for row in column_rows}
        if any(name not in by_name for name in requested_columns):
            raise ConflictError("relation_changed", "One or more requested insert columns do not exist")
        if any(by_name[name].get("generated") or by_name[name].get("identity") == "a" for name in requested_columns):
            raise ValidationError("Generated and GENERATED ALWAYS identity columns cannot be inserted explicitly")
        type_rows = self._execute_rows(connection, """
            SELECT DISTINCT n.nspname AS namespace, t.typname AS name
            FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
            JOIN pg_catalog.pg_namespace n ON n.oid = t.typnamespace
            WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
        """, (relation_row["live_oid"],))
        if any(row.get("namespace") != "pg_catalog" for row in type_rows):
            raise ValidationError("AI row insertion is unsupported for tables with replaceable user-defined column types")
        mutation_rows = self._execute_rows(connection, """
            SELECT c.relrowsecurity AS row_security, c.relforcerowsecurity AS force_row_security,
                   COALESCE((SELECT jsonb_agg(pg_catalog.pg_get_constraintdef(con.oid, true) ORDER BY con.conname)
                             FROM pg_catalog.pg_constraint con WHERE con.conrelid = c.oid), '[]'::jsonb) AS constraints,
                   COALESCE((SELECT jsonb_agg(jsonb_build_array(t.tgname, t.tgenabled, pg_catalog.pg_get_triggerdef(t.oid, true),
                                                                p.oid::text, p.xmin::text) ORDER BY t.tgname)
                             FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
                             WHERE t.tgrelid = c.oid AND NOT t.tgisinternal), '[]'::jsonb) AS triggers,
                   COALESCE((SELECT jsonb_agg(jsonb_build_array(pol.polname, pol.polcmd, pol.polpermissive, pol.polroles,
                                                                pg_catalog.pg_get_expr(pol.polqual, pol.polrelid),
                                                                pg_catalog.pg_get_expr(pol.polwithcheck, pol.polrelid)) ORDER BY pol.polname)
                             FROM pg_catalog.pg_policy pol WHERE pol.polrelid = c.oid), '[]'::jsonb) AS policies,
                   COALESCE((SELECT jsonb_agg(pg_catalog.pg_get_ruledef(r.oid, true) ORDER BY r.rulename)
                             FROM pg_catalog.pg_rewrite r WHERE r.ev_class = c.oid AND r.rulename <> '_RETURN'), '[]'::jsonb) AS rules
            FROM pg_catalog.pg_class c WHERE c.oid = %s
        """, (relation_row["live_oid"],))
        mutation = mutation_rows[0] if mutation_rows else {}
        dependency_rows = self._execute_rows(connection, """
            WITH relation_objects AS (
                SELECT 'pg_attrdef'::pg_catalog.regclass AS classid, d.oid AS objid
                FROM pg_catalog.pg_attrdef d WHERE d.adrelid = %s
                UNION ALL SELECT 'pg_constraint'::pg_catalog.regclass, con.oid FROM pg_catalog.pg_constraint con WHERE con.conrelid = %s
                UNION ALL SELECT 'pg_policy'::pg_catalog.regclass, pol.oid FROM pg_catalog.pg_policy pol WHERE pol.polrelid = %s
                UNION ALL SELECT 'pg_rewrite'::pg_catalog.regclass, r.oid FROM pg_catalog.pg_rewrite r WHERE r.ev_class = %s
                UNION ALL SELECT 'pg_trigger'::pg_catalog.regclass, t.oid FROM pg_catalog.pg_trigger t WHERE t.tgrelid = %s
            )
            SELECT DISTINCT p.oid::text AS oid, p.xmin::text AS xmin, n.nspname AS namespace, p.proname AS name,
                   pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,
                   p.prolang::text AS language_oid, p.prosrc AS source, p.proconfig AS configuration
            FROM relation_objects o JOIN pg_catalog.pg_depend d ON d.classid = o.classid AND d.objid = o.objid
            JOIN pg_catalog.pg_proc p ON d.refclassid = 'pg_proc'::pg_catalog.regclass AND d.refobjid = p.oid
            JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
            ORDER BY namespace, name, identity_arguments
        """, (relation_row["live_oid"],) * 5)
        user_dependencies = [row for row in dependency_rows if row.get("namespace") != "pg_catalog"]
        if user_dependencies:
            raise ValidationError("AI row insertion is unsupported when defaults, checks, policies, rules, or triggers depend on replaceable user-defined functions")
        mutable_object_rows = self._execute_rows(connection, """
            WITH relation_objects AS (
                SELECT 'pg_attrdef'::pg_catalog.regclass AS classid, d.oid AS objid FROM pg_catalog.pg_attrdef d WHERE d.adrelid = %s
                UNION ALL SELECT 'pg_constraint'::pg_catalog.regclass, con.oid FROM pg_catalog.pg_constraint con WHERE con.conrelid = %s
                UNION ALL SELECT 'pg_policy'::pg_catalog.regclass, pol.oid FROM pg_catalog.pg_policy pol WHERE pol.polrelid = %s
                UNION ALL SELECT 'pg_rewrite'::pg_catalog.regclass, r.oid FROM pg_catalog.pg_rewrite r WHERE r.ev_class = %s
            )
            SELECT DISTINCT n.nspname AS namespace
            FROM relation_objects o JOIN pg_catalog.pg_depend d ON d.classid = o.classid AND d.objid = o.objid
            LEFT JOIN pg_catalog.pg_operator op ON d.refclassid = 'pg_operator'::pg_catalog.regclass AND d.refobjid = op.oid
            LEFT JOIN pg_catalog.pg_type t ON d.refclassid = 'pg_type'::pg_catalog.regclass AND d.refobjid = t.oid
            JOIN pg_catalog.pg_namespace n ON n.oid = COALESCE(op.oprnamespace, t.typnamespace)
        """, (relation_row["live_oid"],) * 4)
        if any(row.get("namespace") != "pg_catalog" for row in mutable_object_rows):
            raise ValidationError("AI row insertion is unsupported when defaults, checks, policies, or rules depend on replaceable user-defined operators or types")
        if mutation.get("triggers") or mutation.get("policies") or mutation.get("rules") or mutation.get("row_security") or mutation.get("force_row_security"):
            raise ValidationError("AI row insertion is unsupported for tables with user triggers, rules, or row-level security policies")
        requested_privileges = self._execute_rows(connection, """
            SELECT a.attname AS name, pg_catalog.has_column_privilege(%s, a.attnum, 'INSERT') AS can_insert
            FROM pg_catalog.pg_attribute a WHERE a.attrelid = %s AND a.attname = ANY(%s) ORDER BY a.attname
        """, (relation_row["live_oid"], relation_row["live_oid"], requested_columns))
        if len(requested_privileges) != len(requested_columns) or any(row.get("can_insert") is not True for row in requested_privileges):
            raise PostgresServiceError(403, "insert_not_permitted", "The selected role cannot insert into every requested column")
        canonical = {
            "database": database, "namespace": namespace, "relation": relation,
            "liveOid": relation_row["live_oid"], "catalogKind": relation_row["catalog_kind"],
            "columns": [{
                "name": row["name"], "type": row["type"], "nullable": bool(row["nullable"]),
                "ordinal": int(row["ordinal"]), "default": row.get("default"),
                "identity": row.get("identity") or "", "generated": row.get("generated") or "",
            } for row in column_rows],
            "rowSecurity": bool(mutation.get("row_security")), "forceRowSecurity": bool(mutation.get("force_row_security")),
            "constraints": mutation.get("constraints") or [], "triggers": mutation.get("triggers") or [],
            "policies": mutation.get("policies") or [], "rules": mutation.get("rules") or [],
            "executableDependencies": dependency_rows,
            "requestedColumnPrivileges": requested_privileges,
        }
        return {"kind": "table", "fingerprint": canonical_fingerprint(canonical), "catalog": canonical}

    @postgres_execution("write")
    def apply_ai_migration(self, operation_id: str, plan_id: str, profile_id: str, database: str, namespace: str, expected_destructive: bool, confirm_destructive: bool) -> dict[str, Any]:
        return self._migrations.apply_ai_migration(plan_id, profile_id, expected_destructive, confirm_destructive)

    @postgres_execution("write")
    def apply_ai_postgres_write(self, operation_id: str, plan_id: str, profile_id: str, database: str, namespace: str, relation: str, expected_kind: str, expected_review_digest: str) -> dict[str, Any]:
        return self._migrations.apply_ai_postgres_write(plan_id, profile_id, expected_kind, expected_review_digest)

    def reconcile_ai_postgres_write(self, plan_id: str, profile_id: str) -> dict[str, Any]:
        return self._migrations.reconcile(plan_id)

    def reconcile_ai_migration(self, plan_id: str, profile_id: str) -> dict[str, Any]:
        return self._migrations.reconcile(plan_id)

    @postgres_execution("write")
    def preview_view_mutation(
        self, profile_id: str, database: str, namespace: str, relation: str,
        operation: str, expectation: dict[str, Any], desired: dict[str, Any] | None, allow_destructive: bool,
        schema_binding: dict[str, Any], *, persist: bool = False,
    ) -> dict[str, Any]:
        profile_id = self._validate_profile_id(profile_id)
        database = self._validate_database(database)
        namespace = self._validate_namespace(namespace)
        relation = self._validate_relation_name(relation)
        if operation not in {"upsert", "delete"}:
            raise ValidationError("operation must be upsert or delete")
        if not isinstance(allow_destructive, bool):
            raise ValidationError("allowDestructive must be boolean")
        if not isinstance(expectation, dict):
            raise ValidationError("expectation must be an object")
        if set(expectation) == {"absent"}:
            if expectation["absent"] is not True:
                raise ValidationError("expectation.absent must be true")
        elif set(expectation) == {"kind", "fingerprint"}:
            if expectation["kind"] not in {"view", "materialized_view"}:
                raise ValidationError("expectation.kind is invalid")
            if not isinstance(expectation["fingerprint"], str) or not FINGERPRINT_RE.fullmatch(expectation["fingerprint"]):
                raise ValidationError("expectation.fingerprint is invalid")
        else:
            raise ValidationError("expectation must be exactly absent:true or kind plus fingerprint")
        if operation == "delete":
            if desired is not None or set(expectation) == {"absent"}:
                raise ValidationError("Delete requires an existing expectation and no desired definition")
            desired_kind = None
            definition = None
        else:
            if not isinstance(desired, dict) or set(desired) != {"kind", "definition"}:
                raise ValidationError("desired must contain exactly kind and definition")
            desired_kind = desired["kind"]
            if desired_kind not in {"view", "materialized_view"}:
                raise ValidationError("desired.kind is invalid")
            if not isinstance(desired["definition"], str) or not desired["definition"].strip():
                raise ValidationError("desired.definition must be a non-empty SQL statement")
            definition = _single_sql_statement(desired["definition"], "View definition")
            ordinary = re.match(r"^CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b", definition, re.I)
            materialized = re.match(r"^CREATE\s+MATERIALIZED\s+VIEW\b", definition, re.I)
            if (desired_kind == "view" and not ordinary) or (desired_kind == "materialized_view" and not materialized):
                raise ValidationError("View definition kind does not match desired.kind")
            _require_definition_identity(definition, "view", namespace, relation)

        profile = self._profile(profile_id)
        profile_fingerprint = self._profile_fingerprint(profile)
        connection = self._connect_profile(profile)
        live = None
        preservation = None
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            try:
                live = self._inspect_relation_connection(connection, profile_id, database, namespace, relation, None, None)
            except NotFoundError:
                live = None
            if set(expectation) == {"absent"}:
                if live is not None:
                    raise ConflictError("relation_changed", "The expected-absent PostgreSQL relation now exists")
            elif live is None or live["kind"] != expectation["kind"] or live["fingerprint"] != expectation["fingerprint"]:
                raise ConflictError("relation_changed", "The PostgreSQL relation changed after editing began")
            if live is not None and live["kind"] not in {"view", "materialized_view"}:
                raise ConflictError("relation_changed", "The target identity is no longer a view")
            if live is not None and desired_kind is not None and live["kind"] != desired_kind:
                raise PostgresServiceError(409, "view_kind_conversion_unsupported", "Converting between ordinary and materialized views is unsupported")
            destructive = operation == "delete" or (live is not None and live["kind"] == "materialized_view")
            if destructive:
                preservation = self._view_recreation_preservation(connection, namespace, relation) if live["kind"] == "materialized_view" else None
                dependents = live.get("dependents", {})
                blocked = []
                if dependents.get("status") != "available" or dependents.get("truncated") or dependents.get("items"):
                    blocked.append("direct dependents")
                if preservation:
                    blocked.extend(preservation["unsupported"])
                if blocked:
                    raise PostgresServiceError(
                        409, "view_recreation_unsupported",
                        "Destructive view recreation would lose or invalidate unsupported metadata",
                        {"concerns": blocked},
                    )
                if not allow_destructive:
                    raise ConflictError("destructive_preview_required", "View recreation requires allowDestructive")
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
            self._close(connection)
        if self._profile_fingerprint(self._profile(profile_id)) != profile_fingerprint:
            raise ConflictError("profile_changed", "Connection profile changed during preview")
        if desired_kind == "view":
            command = "CREATE OR REPLACE VIEW" if live is not None else "CREATE VIEW"
            definition = re.sub(r"^CREATE\s+(?:OR\s+REPLACE\s+)?VIEW", command, definition, count=1, flags=re.I)
        steps = []
        if live is not None and destructive:
            keyword = "MATERIALIZED VIEW" if live["kind"] == "materialized_view" else "VIEW"
            steps.append(self._step("drop", keyword.lower(), relation, f"DROP {keyword} {quote_identifier(namespace)}.{quote_identifier(relation)}", True))
        if operation == "upsert":
            if live is not None and live["kind"] == "materialized_view":
                definition = self._materialized_storage_definition(definition, preservation)
                definition = self._materialized_population_definition(definition, preservation["populated"])
            steps.append(self._step(
                "create_or_replace" if desired_kind == "view" and live is not None else "create",
                "view" if desired_kind == "view" else "materialized view", relation, definition,
            ))
            if preservation:
                steps.extend(self._materialized_restoration_steps(namespace, relation, preservation))
        plan_id = "plan_" + secrets.token_hex(16)
        now = self._clock()
        stored = {
            "id": plan_id, "planVersion": 2, "kind": "view_mutation", "operation": operation,
            "profileId": profile_id, "database": database,
            "namespace": namespace, "relation": relation, "profileFingerprint": profile_fingerprint,
            "expectation": copy.deepcopy(expectation), "desiredKind": desired_kind, "desiredDefinition": definition,
            "preservation": copy.deepcopy(preservation),
            "schemaBinding": copy.deepcopy(schema_binding), "allowDestructive": allow_destructive,
            "destructive": bool(live is not None and destructive), "steps": steps,
            "warnings": ([{
                "code": "view_output_compatibility_apply_validated",
                "message": "PostgreSQL will validate CREATE OR REPLACE VIEW output-column compatibility during apply",
            }] if live is not None and desired_kind == "view" and not destructive else []) + ([{
                "code": "materialized_rows_repopulated",
                "message": "Stored materialized-view rows will be discarded and the defining query will repopulate them before commit",
            }] if operation == "upsert" and live is not None and live["kind"] == "materialized_view" and preservation["populated"] else []) + ([{
                "code": "materialized_rows_deleted",
                "message": "The materialized view and all rows stored in it will be permanently deleted",
            }] if operation == "delete" and live is not None and live["kind"] == "materialized_view" else []),
            "state": "ready",
            "createdAt": now, "expiresAt": now + self._plan_ttl,
        }
        if persist:
            if self._migration_coordinator is None:
                raise PostgresServiceError(503, "durable_migrations_unavailable", "Durable migration metadata is unavailable")
            return self._migration_coordinator.preview_view(
                profile_id, database, namespace, relation, operation, expectation, desired,
                allow_destructive, schema_binding,
            )
        public = self._public_plan(stored)
        public.update({"desiredDefinition": definition, "preservation": copy.deepcopy(preservation)})
        return public

    @staticmethod
    def _materialized_population_definition(definition: str, populated: bool) -> str:
        definition = re.sub(r"\s+WITH\s+(?:NO\s+)?DATA\s*;?\s*$", "", definition, flags=re.I)
        return f"{definition.rstrip(';')} WITH {'DATA' if populated else 'NO DATA'};"

    def _materialized_storage_definition(self, definition: str, manifest: dict[str, Any]) -> str:
        clauses = []
        if manifest.get("accessMethod"):
            clauses.append(f"USING {quote_identifier(manifest['accessMethod'])}")
        if manifest.get("reloptions"):
            options = []
            for option in manifest["reloptions"]:
                name, separator, value = option.partition("=")
                if not separator or not re.fullmatch(r"[a-z_][a-z0-9_.]*", name):
                    raise PostgresServiceError(409, "view_recreation_unsupported", "Materialized view has unsupported relation options")
                options.append(f"{name} = {_quote_literal(value)}")
            clauses.append(f"WITH ({', '.join(options)})")
        if manifest.get("tablespace"):
            clauses.append(f"TABLESPACE {quote_identifier(manifest['tablespace'])}")
        if not clauses:
            return definition
        return re.sub(r"\s+AS\b", f" {' '.join(clauses)} AS", definition, count=1, flags=re.I)

    def _view_recreation_preservation(self, connection: Any, namespace: str, relation: str) -> dict[str, Any]:
        # PostgreSQL 16 added the explicit SET role privilege. USAGE is the
        # closest ownership feasibility signal on older supported servers.
        set_role_capability = "CASE WHEN current_setting('server_version_num')::integer >= 160000 THEN pg_catalog.pg_has_role(c.relowner, 'SET') ELSE pg_catalog.pg_has_role(c.relowner, 'MEMBER') END"
        rows = self._execute_rows(connection, """
            SELECT c.oid, pg_catalog.pg_get_userbyid(c.relowner) AS owner,
                   current_user = pg_catalog.pg_get_userbyid(c.relowner) AS is_owner,
                   {set_role_capability} AS can_set_owner_role,
                   c.relacl IS NOT NULL AS explicit_acl,
                   pg_catalog.obj_description(c.oid, 'pg_class') AS relation_comment,
                   c.reloptions, ts.spcname AS tablespace, am.amname AS access_method,
                   c.relispopulated AS populated, c.relrowsecurity, c.relforcerowsecurity,
                   c.relreplident,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_trigger t
                       WHERE t.tgrelid = c.oid AND NOT t.tgisinternal
                   ) AS triggers,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_rewrite r
                       WHERE r.ev_class = c.oid AND r.rulename <> '_RETURN'
                   ) AS rules,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_seclabel s
                       WHERE s.classoid = 'pg_catalog.pg_class'::pg_catalog.regclass AND s.objoid = c.oid
                   ) AS security_labels,
                   EXISTS (SELECT 1 FROM pg_catalog.pg_policy p WHERE p.polrelid = c.oid) AS policies,
                   EXISTS (SELECT 1 FROM pg_catalog.pg_statistic_ext s WHERE s.stxrelid = c.oid) AS extended_statistics,
                   EXISTS (SELECT 1 FROM pg_catalog.pg_publication_rel p WHERE p.prrelid = c.oid) AS publications,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_depend d
                       WHERE d.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
                         AND d.objid = c.oid AND d.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass
                         AND d.deptype = 'e'
                   ) AS extension_membership,
                   EXISTS (
                       SELECT 1
                       FROM pg_catalog.pg_depend dependency
                       JOIN pg_catalog.pg_depend extension_object
                         ON extension_object.classid = dependency.refclassid
                        AND extension_object.objid = dependency.refobjid
                        AND extension_object.refclassid = 'pg_catalog.pg_extension'::pg_catalog.regclass
                        AND extension_object.deptype = 'e'
                       WHERE (
                           dependency.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
                           AND dependency.objid = c.oid
                       ) OR (
                           dependency.classid = 'pg_catalog.pg_rewrite'::pg_catalog.regclass
                           AND dependency.objid IN (
                               SELECT rewrite.oid FROM pg_catalog.pg_rewrite rewrite WHERE rewrite.ev_class = c.oid
                           )
                       )
                   ) AS extension_dependencies,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_depend d
                       WHERE d.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass
                         AND d.refobjid = c.oid AND d.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
                         AND d.deptype IN ('a', 'i')
                         AND EXISTS (SELECT 1 FROM pg_catalog.pg_class seq WHERE seq.oid = d.objid AND seq.relkind = 'S')
                   ) AS owned_sequences,
                   EXISTS (
                       SELECT 1 FROM pg_catalog.pg_attribute a
                       WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                         AND (a.attidentity <> '' OR a.attgenerated <> '')
                   ) AS generated_or_identity,
                   c.relpersistence <> 'p' AS storage
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_catalog.pg_tablespace ts ON ts.oid = NULLIF(c.reltablespace, 0)
            LEFT JOIN pg_catalog.pg_am am ON am.oid = c.relam
            WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('v', 'm')
        """.format(set_role_capability=set_role_capability), (namespace, relation))
        if len(rows) != 1:
            raise ConflictError("relation_changed", "The PostgreSQL relation changed during preview")
        row = rows[0]
        oid = row["oid"]
        grants = self._execute_rows(connection, """
            SELECT pg_catalog.pg_get_userbyid(x.grantor) AS grantor,
                   CASE WHEN x.grantee = 0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(x.grantee) END AS grantee,
                   x.privilege_type, x.is_grantable
            FROM pg_catalog.pg_class c
            CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) x
            WHERE c.oid = %s ORDER BY 1, 2, 3
            LIMIT %s
        """, (oid, MAX_RECONSTRUCTION_METADATA_ITEMS + 1)) if row.get("explicit_acl") else []
        default_grantees = self._execute_rows(connection, """
            SELECT DISTINCT CASE WHEN x.grantee = 0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(x.grantee) END AS grantee
            FROM pg_catalog.pg_default_acl d
            CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) x
            WHERE d.defaclrole = (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = %s)
              AND d.defaclnamespace = (SELECT c.relnamespace FROM pg_catalog.pg_class c WHERE c.oid = %s)
              AND d.defaclobjtype = 'r'
            LIMIT %s
        """, (oid, oid, MAX_RECONSTRUCTION_METADATA_ITEMS + 1))
        comments = self._execute_rows(connection, """
            SELECT a.attname AS column_name, d.description
            FROM pg_catalog.pg_description d
            JOIN pg_catalog.pg_attribute a ON a.attrelid = d.objoid AND a.attnum = d.objsubid
            WHERE d.classoid = 'pg_catalog.pg_class'::pg_catalog.regclass AND d.objoid = %s AND d.objsubid > 0
            ORDER BY a.attnum
            LIMIT %s
        """, (oid, MAX_RECONSTRUCTION_METADATA_ITEMS + 1))
        indexes = self._execute_rows(connection, """
            SELECT ci.relname AS name, pg_catalog.pg_get_indexdef(i.indexrelid) AS definition,
                   pg_catalog.obj_description(i.indexrelid, 'pg_class') AS comment,
                   i.indisvalid, i.indisready
            FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class ci ON ci.oid = i.indexrelid
            WHERE i.indrelid = %s ORDER BY ci.relname
            LIMIT %s
        """, (oid, MAX_RECONSTRUCTION_METADATA_ITEMS + 1))
        unsupported = [name for name in (
            "triggers", "rules", "security_labels", "policies", "extended_statistics",
            "publications", "extension_membership", "extension_dependencies", "owned_sequences",
            "generated_or_identity", "storage",
        ) if row.get(name)]
        if row.get("relrowsecurity") or row.get("relforcerowsecurity"):
            unsupported.append("row level security")
        if row.get("relreplident") not in {None, "d"}:
            unsupported.append("replica identity")
        if row.get("owner") and row.get("is_owner") is False and row.get("can_set_owner_role") is False:
            unsupported.append("owner role cannot be assumed")
        if any(grant.get("grantor") != row.get("owner") for grant in grants):
            unsupported.append("non-owner grantors")
        if any(not item.get("indisvalid") or not item.get("indisready") for item in indexes):
            unsupported.append("invalid indexes")
        bounded = {
            "ACL entries": grants,
            "default ACL grantees": default_grantees,
            "column comments": comments,
            "indexes": indexes,
        }
        for label, items in bounded.items():
            if len(items) > MAX_RECONSTRUCTION_METADATA_ITEMS:
                unsupported.append(f"truncated {label}")
                del items[MAX_RECONSTRUCTION_METADATA_ITEMS:]
        manifest = {
            "owner": row.get("owner"), "explicitAcl": bool(row.get("explicit_acl")),
            "grants": grants, "defaultGrantees": [item["grantee"] for item in default_grantees],
            "relationComment": row.get("relation_comment"), "columnComments": comments,
            "indexes": indexes, "reloptions": list(row.get("reloptions") or []),
            "tablespace": row.get("tablespace"), "accessMethod": row.get("access_method"),
            "populated": bool(row.get("populated")), "unsupported": unsupported,
        }
        manifest["fingerprint"] = canonical_fingerprint({key: value for key, value in manifest.items() if key not in {"populated", "fingerprint"}})
        return manifest

    def _materialized_restoration_steps(self, namespace: str, relation: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        qualified = f"{quote_identifier(namespace)}.{quote_identifier(relation)}"
        steps = []
        if manifest.get("relationComment") is not None:
            steps.append(self._step("restore", "materialized view comment", relation, f"COMMENT ON MATERIALIZED VIEW {qualified} IS {_quote_literal(manifest['relationComment'])}"))
        for comment in manifest.get("columnComments", []):
            steps.append(self._step("restore", "column comment", comment["column_name"], f"COMMENT ON COLUMN {qualified}.{quote_identifier(comment['column_name'])} IS {_quote_literal(comment['description'])}"))
        for index in manifest.get("indexes", []):
            steps.append(self._step("restore", "index", index["name"], index["definition"]))
            if index.get("comment") is not None:
                steps.append(self._step("restore", "index comment", index["name"], f"COMMENT ON INDEX {quote_identifier(namespace)}.{quote_identifier(index['name'])} IS {_quote_literal(index['comment'])}"))
        if manifest.get("explicitAcl"):
            grantees = sorted({"PUBLIC", *manifest.get("defaultGrantees", []), *(grant["grantee"] for grant in manifest.get("grants", []))})
            for grantee in grantees:
                target = "PUBLIC" if grantee == "PUBLIC" else quote_identifier(grantee)
                steps.append(self._step("restore", "permissions", relation, f"REVOKE ALL PRIVILEGES ON TABLE {qualified} FROM {target}"))
            for grant in manifest.get("grants", []):
                target = "PUBLIC" if grant["grantee"] == "PUBLIC" else quote_identifier(grant["grantee"])
                suffix = " WITH GRANT OPTION" if grant.get("is_grantable") else ""
                steps.append(self._step("restore", "permission", grant["privilege_type"], f"GRANT {grant['privilege_type']} ON TABLE {qualified} TO {target}{suffix}"))
        return steps

    def _tables_with_rows(self, profile_id: str, namespace: str, table_names: set[str]) -> set[str]:
        if not table_names:
            return set()
        connection = self._connect(profile_id)
        populated = set()
        try:
            for table_name in sorted(table_names):
                rows = self._execute_rows(
                    connection,
                    f"SELECT EXISTS (SELECT 1 FROM {quote_identifier(namespace)}.{quote_identifier(table_name)} LIMIT 1) AS has_rows",
                )
                if rows and rows[0].get("has_rows"):
                    populated.add(table_name)
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "row_check_failed", "PostgreSQL table row check failed") from exc
        finally:
            self._close(connection)
        return populated

    def _column_reorder_tables(self, live: dict[str, Any], desired: dict[str, Any]) -> set[str]:
        live_tables = {table.get("name"): table for table in live.get("tables", [])}
        reordered = set()
        for desired_table in desired.get("tables", []):
            table_name = desired_table.get("name")
            live_table = live_tables.get(table_name)
            if live_table is None:
                continue
            normalized_live = self._normalize_live_column_names(live_table, desired_table)
            live_names = [column.get("name") for column in normalized_live.get("columns", [])]
            desired_names = [column.get("name") for column in desired_table.get("columns", [])]
            if len(live_names) == len(desired_names) and set(live_names) == set(desired_names) and live_names != desired_names:
                reordered.add(table_name)
        return reordered

    @staticmethod
    def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy({
            key: value for key, value in plan.items()
            if key not in {"createdAt", "desiredSchema", "desiredDefinition", "schemaBinding", "profileFingerprint", "preservation", "state"}
        })

    def _purge_retired_authority(self, now: float) -> None:
        for plan_id in [key for key, plan in self._removed_plan_authority.items() if plan["expiresAt"] <= now]:
            del self._removed_plan_authority[plan_id]

    @staticmethod
    def _step(action: str, object_type: str, name: str, sql: str, destructive: bool = False) -> dict[str, Any]:
        return {"action": action, "objectType": object_type, "name": name, "sql": sql.rstrip(";") + ";", "destructive": destructive}

    def _diff(self, namespace: str, live: dict[str, Any], desired: dict[str, Any], allow: bool):
        safe: list[dict[str, Any]] = []
        destructive: list[dict[str, Any]] = []
        rename: list[dict[str, Any]] = []
        late: list[dict[str, Any]] = []
        warnings: list[dict[str, str]] = []
        qn = quote_identifier(namespace)

        def add(step: dict[str, Any], *, last: bool = False) -> bool:
            if step.get("rename"):
                rename.append(step)
                return True
            if step["destructive"]:
                if allow:
                    destructive.append(step)
                    return True
                else:
                    warnings.append({"code": "destructive_omitted", "message": f"Omitted {step['action']} {step['objectType']} {step['name']}"})
                    return False
            elif last:
                late.append(step)
            else:
                safe.append(step)
            return True

        live_tables = self._named(live["tables"], "table")
        desired_tables = self._named(desired["tables"], "table")
        desired_relationship_names = {
            relation.get("constraintName") or relation.get("name")
            for relation in desired.get("relationships", [])
        }

        # Detect renamed tables by matching liveOid across name boundaries.
        live_by_oid: dict[int, dict[str, Any]] = {}
        for lt in live.get("tables", []):
            oid = (lt.get("postgres") or {}).get("liveOid")
            if oid:
                live_by_oid[oid] = lt
        rename_pairs: dict[str, str] = {}
        for dt_name, dt in sorted(desired_tables.items()):
            if dt_name in live_tables:
                continue
            oid = (dt.get("postgres") or {}).get("liveOid")
            if oid and oid in live_by_oid:
                live_name = live_by_oid[oid]["name"]
                if live_name in live_tables:
                    rename_pairs[dt_name] = live_name
        renamed_live_names = set(rename_pairs.values())
        reordered_tables = self._column_reorder_tables(live, desired)
        blocked_reorders = set()
        if reordered_tables and (live.get("views") or desired.get("views")):
            for table_name in sorted(reordered_tables):
                warnings.append({
                    "code": "unsupported",
                    "message": f"Column reorder for {table_name} requires removing dependent views first",
                })
            blocked_reorders = set(reordered_tables)
        for table_name in sorted(reordered_tables - blocked_reorders):
            table = desired_tables[table_name]
            if any(
                (column.get("postgres") or {}).get("identity")
                or (column.get("postgres") or {}).get("generated")
                or _is_sequence_default(column.get("default"))
                for column in table.get("columns", [])
            ):
                warnings.append({
                    "code": "unsupported",
                    "message": f"Column reorder for {table_name} with identity, generated, or sequence-backed columns requires a manual migration",
                })
                blocked_reorders.add(table_name)
        reordered_tables -= blocked_reorders
        reorder_steps = []
        if reordered_tables:
            if allow:
                reorder_steps = self._column_reorder_steps(namespace, live, desired, reordered_tables, warnings)
            else:
                for table_name in sorted(reordered_tables):
                    warnings.append({
                        "code": "destructive_omitted",
                        "message": f"Omitted physical column reorder for {table_name}; include destructive changes to preview the table rewrite",
                    })

        def key_signatures(table: dict[str, Any]) -> list[tuple[Any, ...]]:
            signatures = []
            for kind in ("primary key", "unique constraint"):
                signatures.extend(
                    (kind, *self._constraint_signature(table, item, kind))
                    for item in self._constraint_map(table, kind).values()
                )
            return sorted(signatures)

        key_change_tables = set()
        removed_key_columns_by_table_id = {}
        for desired_name, desired_table in desired_tables.items():
            live_name = desired_name if desired_name in live_tables else rename_pairs.get(desired_name)
            normalized_live = self._normalize_live_column_names(live_tables[live_name], desired_table) if live_name else None
            if normalized_live:
                live_signatures = set(key_signatures(normalized_live))
                desired_signatures = set(key_signatures(desired_table))
                if live_signatures != desired_signatures:
                    key_change_tables.add(desired_name)
                removed_signatures = live_signatures - desired_signatures
                table_id = desired_table.get("id")
                if removed_signatures and table_id:
                    removed_key_columns_by_table_id[table_id] = {
                        signature[1] for signature in removed_signatures
                    }
        rebuild_foreign_key_targets = removed_key_columns_by_table_id if allow else {}
        incoming_table_ids = {relation.get("toTableId") for relation in live.get("relationships", [])}

        # Existing table changes precede new constraints and object definitions.
        for table_name in sorted(set(live_tables) & set(desired_tables)):
            if table_name in reordered_tables:
                continue
            lt, dt = live_tables[table_name], desired_tables[table_name]
            block_key_changes = table_name in key_change_tables and lt.get("id") in incoming_table_ids and lt.get("id") not in rebuild_foreign_key_targets
            self._diff_table(namespace, live, lt, dt, add, warnings, block_key_changes)

        # Handle renamed tables: RENAME first, then diff as if names matched.
        for dt_name in sorted(rename_pairs):
            live_name = rename_pairs[dt_name]
            lt = live_tables[live_name]
            dt = desired_tables[dt_name]
            rename_sql = f"ALTER TABLE {qn}.{quote_identifier(live_name)} RENAME TO {quote_identifier(dt_name)}"
            rename_step = self._step("alter", "table", f"{live_name} -> {dt_name}", rename_sql)
            rename_step["rename"] = True
            add(rename_step)
            block_key_changes = dt_name in key_change_tables and lt.get("id") in incoming_table_ids and lt.get("id") not in rebuild_foreign_key_targets
            self._diff_table(namespace, live, lt, dt, add, warnings, block_key_changes)

        for table_name in sorted(set(desired_tables) - set(live_tables) - set(rename_pairs)):
            table = desired_tables[table_name]
            columns = []
            for column in table.get("columns", []):
                name = column.get("name")
                if not isinstance(name, str) or not name:
                    raise ValidationError(f"Table {table_name} has an unnamed column")
                columns.append(self._column_definition(column, table_name))
            if not columns:
                warnings.append({"code": "unsupported", "message": f"Cannot create table {table_name} without columns"})
                continue
            add(self._step("create", "table", table_name, f"CREATE TABLE {qn}.{quote_identifier(table_name)} (\n  " + ",\n  ".join(columns) + "\n)"))
            empty = {
                "name": table_name,
                "columns": [{**column, "primary": False, "unique": False} for column in table.get("columns", [])],
                "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [], "primaryKey": None,
            }
            self._diff_table_objects(namespace, empty, table, add, warnings)
        for table_name in sorted(set(live_tables) - set(desired_tables) - renamed_live_names):
            add(self._step("drop", "table", table_name, f"DROP TABLE {qn}.{quote_identifier(table_name)}", True))

        reordered_table_ids = {
            table.get("id") for table in desired.get("tables", []) if table.get("name") in reordered_tables
        }
        reordered_table_ids.discard(None)
        self._diff_relationships(
            namespace, live, desired, add, warnings, rebuild_foreign_key_targets,
            skip_table_ids=reordered_table_ids,
        )
        self._diff_root_definitions(namespace, "function", live.get("functions", []), desired.get("functions", []), add, warnings)
        self._diff_views(namespace, live.get("views", []), desired.get("views", []), add, warnings)
        # Dependency objects must be removed before columns/tables. Stable sort
        # retains deterministic name ordering within each object class.
        drop_priority = {
            "trigger": 0, "view": 1, "materialized view": 1, "function": 1, "procedure": 1,
            "foreign key": 2, "index": 3, "check": 4, "unique constraint": 4,
            "primary key": 4, "column_default": 5, "column_nullability": 5,
            "column_type": 5, "column": 6, "table": 7,
        }
        destructive.sort(key=lambda step: (drop_priority.get(step["objectType"], 5), step["name"]))
        constraint_renames = [step for step in safe if step.get("constraintRename")]
        safe = [step for step in safe if not step.get("constraintRename")]
        rename_sources = {step["constraintRename"][1] for step in constraint_renames}
        if any(step["constraintRename"][2] in rename_sources for step in constraint_renames):
            temporary_steps = []
            final_steps = []
            for step in constraint_renames:
                table_sql, old_name, new_name = step["constraintRename"]
                digest = hashlib.sha256(f"{table_sql}:{old_name}:{new_name}".encode()).hexdigest()[:20]
                temporary_name = f"sf_tmp_{digest}"
                temporary_steps.append(self._step(
                    "alter", step["objectType"], f"{step['name']} (temporary)",
                    f"ALTER TABLE {table_sql} RENAME CONSTRAINT {quote_identifier(old_name)} TO {quote_identifier(temporary_name)}",
                ))
                final_steps.append(self._step(
                    "alter", step["objectType"], step["name"],
                    f"ALTER TABLE {table_sql} RENAME CONSTRAINT {quote_identifier(temporary_name)} TO {quote_identifier(new_name)}",
                ))
            constraint_renames = temporary_steps + final_steps
        else:
            for step in constraint_renames:
                step.pop("constraintRename", None)
        # Trigger creation can reference a routine added by the same plan.
        late_priority = {"function": 0, "procedure": 0, "trigger": 2}
        late.sort(key=lambda step: late_priority.get(step["objectType"], 1))
        return destructive + reorder_steps + rename + constraint_renames + safe + late, warnings

    def _column_reorder_steps(
        self, namespace: str, live: dict[str, Any], desired: dict[str, Any],
        table_names: set[str], warnings: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        steps = []
        qn = quote_identifier(namespace)
        live_tables = {table["name"]: table for table in live.get("tables", [])}
        desired_tables = {table["name"]: table for table in desired.get("tables", [])}
        live_names_by_id = {table.get("id"): table.get("name") for table in live.get("tables", [])}
        desired_names_by_id = {table.get("id"): table.get("name") for table in desired.get("tables", [])}

        def relationship_tables(schema, names_by_id, relation):
            source_name = names_by_id.get(relation.get("fromTableId"))
            target_name = names_by_id.get(relation.get("toTableId"), relation.get("targetTableName"))
            return source_name, target_name

        for relation in live.get("relationships", []):
            source_name, target_name = relationship_tables(live, live_names_by_id, relation)
            if source_name not in table_names and target_name not in table_names:
                continue
            constraint_name = relation.get("constraintName") or relation.get("name")
            if not source_name or not constraint_name:
                raise ValidationError("A relationship involved in a column reorder is incomplete")
            steps.append(self._step(
                "drop", "foreign key", f"{source_name}.{constraint_name}",
                f"ALTER TABLE {qn}.{quote_identifier(source_name)} DROP CONSTRAINT {quote_identifier(constraint_name)}",
                True,
            ))

        for table_name in sorted(table_names):
            live_table = live_tables[table_name]
            desired_table = desired_tables[table_name]
            digest = hashlib.sha256(f"{namespace}:{table_name}:column-order".encode()).hexdigest()[:20]
            temporary_name = f"sf_reorder_{digest}"
            if temporary_name in live_tables or temporary_name in desired_tables:
                raise ValidationError(f"Temporary reorder table name conflicts with {temporary_name}")
            table_sql = f"{qn}.{quote_identifier(table_name)}"
            temporary_sql = f"{qn}.{quote_identifier(temporary_name)}"
            columns_sql = [self._column_definition(column, table_name) for column in desired_table.get("columns", [])]
            if not columns_sql:
                raise ValidationError(f"Cannot reorder table {table_name} without columns")

            steps.append(self._step(
                "prepare", "column order", table_name,
                f"ALTER TABLE {table_sql} RENAME TO {quote_identifier(temporary_name)}",
                True,
            ))
            steps.append(self._step(
                "create", "table", table_name,
                f"CREATE TABLE {table_sql} (\n  " + ",\n  ".join(columns_sql) + "\n)",
            ))

            rename_pairs = self._column_rename_pairs(live_table, desired_table)
            old_name_for_desired = {desired_name: live_name for live_name, desired_name in rename_pairs.items()}
            desired_column_names = [column["name"] for column in desired_table.get("columns", [])]
            source_column_names = [old_name_for_desired.get(name, name) for name in desired_column_names]
            steps.append(self._step(
                "move", "table data", table_name,
                f"INSERT INTO {table_sql} (" + ", ".join(map(quote_identifier, desired_column_names)) + ") "
                f"SELECT " + ", ".join(map(quote_identifier, source_column_names)) + f" FROM {temporary_sql}",
            ))
            if (live_table.get("postgres") or {}).get("hasRows"):
                warnings.append({
                    "code": "data_movement",
                    "message": f"Table {table_name} contains data; its column reorder copies every row into a replacement table inside the migration transaction",
                })

            steps.append(self._step("drop", "table", temporary_name, f"DROP TABLE {temporary_sql}", True))

            empty_table = {
                "name": table_name,
                "columns": [
                    {**column, "primary": False, "unique": False}
                    for column in desired_table.get("columns", [])
                ],
                "uniqueConstraints": [], "checks": [], "indexes": [], "triggers": [], "primaryKey": None,
            }

            def append_local(step, *, last=False):
                steps.append(step)
                return True

            self._diff_table_objects(namespace, empty_table, desired_table, append_local, warnings)

        touching_relationships = []
        for relation in desired.get("relationships", []):
            source_name, target_name = relationship_tables(desired, desired_names_by_id, relation)
            if source_name in table_names or target_name in table_names:
                touching_relationships.append(relation)
        empty_relationship_schema = {**desired, "relationships": []}
        touching_relationship_schema = {**desired, "relationships": touching_relationships}

        def append_relationship(step, *, last=False):
            steps.append(step)
            return True

        self._diff_relationships(
            namespace, empty_relationship_schema, touching_relationship_schema,
            append_relationship, warnings,
        )
        return steps

    @staticmethod
    def _raw(item: dict[str, Any], key: str, label: str) -> str:
        value = item.get(key)
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValidationError(f"{label} has invalid {key} SQL")
        return _sql_fragment(value, f"{label} {key}")

    def _column_definition(self, column: dict[str, Any], table_name: str) -> str:
        name = column.get("name")
        if not isinstance(name, str) or not name:
            raise ValidationError(f"Table {table_name} has an unnamed column")
        definition = f"{quote_identifier(name)} {self._raw(column, 'type', f'column {table_name}.{name}')}"
        postgres = column.get("postgres") if isinstance(column.get("postgres"), dict) else {}
        identity = postgres.get("identity", "")
        generated = postgres.get("generated", "")
        default = column.get("default")
        if generated:
            if generated != "s" or default in (None, ""):
                raise ValidationError(f"Generated column {table_name}.{name} has unsupported metadata")
            expression = self._raw(column, "default", f"generated column {table_name}.{name}")
            definition += f" GENERATED ALWAYS AS ({expression}) STORED"
        elif identity:
            if identity not in {"a", "d"}:
                raise ValidationError(f"Identity column {table_name}.{name} has unsupported metadata")
            definition += " GENERATED ALWAYS AS IDENTITY" if identity == "a" else " GENERATED BY DEFAULT AS IDENTITY"
        elif default not in (None, ""):
            if _is_sequence_default(default):
                raise ValidationError(f"Sequence-backed default for {table_name}.{name} requires a manual migration")
            definition += " DEFAULT " + self._raw(column, "default", f"column {table_name}.{name}")
        if not column.get("nullable", True):
            definition += " NOT NULL"
        return definition

    def _column_has_dependencies(self, schema: dict[str, Any], table: dict[str, Any], column: dict[str, Any]) -> bool:
        column_id = column.get("id")
        if column.get("primary") or column.get("unique"):
            return True
        if any(column_id in item.get("columnIds", []) for item in table.get("uniqueConstraints", [])):
            return True
        if any(column_id in item.get("columnIds", []) for item in table.get("checks", [])):
            return True
        if schema.get("views"):
            return True
        return any(
            column_id in self._relation_ids(relation, "from") or column_id in self._relation_ids(relation, "to")
            for relation in schema.get("relationships", [])
        )

    def _column_has_blocking_timezone_dependencies(
        self, schema: dict[str, Any], table: dict[str, Any], column: dict[str, Any]
    ) -> bool:
        column_id = column.get("id")
        if column.get("primary") or column.get("unique") or schema.get("views"):
            return True
        if any(column_id in item.get("columnIds", []) for item in table.get("uniqueConstraints", [])):
            return True
        if any(column_id in item.get("columnIds", []) for item in table.get("checks", [])):
            return True
        return any(
            column_id in self._relation_ids(relation, "from") or column_id in self._relation_ids(relation, "to")
            for relation in schema.get("relationships", [])
        )

    def _constraint_map(self, table: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
        if kind == "primary key":
            primary = [column["id"] for column in table.get("columns", []) if column.get("primary")]
            stored = table.get("primaryKey") or {}
            item = {**stored, "name": stored.get("name") or f"{table['name']}_pkey", "columnIds": primary} if primary else None
            return {item["name"]: item} if item else {}
        key = "uniqueConstraints" if kind == "unique constraint" else "checks"
        result = {}
        for original in table.get(key, []):
            item = dict(original)
            if not item.get("name") and kind == "unique constraint":
                names = self._column_names(table, list(item.get("columnIds") or []))
                item["name"] = f"{table['name']}_{'_'.join(names)}_key"
            if not item.get("name") and kind == "check":
                raise ValidationError(f"Every {kind} must have a name")
            if item["name"] in result:
                raise ValidationError(f"Duplicate {kind} name: {item['name']}")
            result[item["name"]] = item
        if kind == "unique constraint":
            represented = {
                item["columnIds"][0]
                for item in result.values()
                if len(item.get("columnIds", [])) == 1
            }
            for column in table.get("columns", []):
                if column.get("unique") and not column.get("primary") and column.get("id") not in represented:
                    name = f"{table['name']}_{column['name']}_key"
                    if name in result:
                        raise ValidationError(f"Duplicate unique constraint name: {name}")
                    result[name] = {"name": name, "columnIds": [column["id"]]}
        return result

    def _diff_table(self, namespace, live, lt, dt, add, warnings, block_key_changes=False):
        table_name = dt["name"]
        live_table_sql = f"{quote_identifier(namespace)}.{quote_identifier(lt['name'])}"
        lcols, dcols = self._named(lt.get("columns", []), "column"), self._named(dt.get("columns", []), "column")
        table_sql = f"{quote_identifier(namespace)}.{quote_identifier(table_name)}"
        column_renames = self._column_rename_pairs(lt, dt)
        renamed_live_names = set(column_renames)
        renamed_desired_names = set(column_renames.values())
        for live_name, desired_name in sorted(column_renames.items()):
            step = self._step(
                "alter", "column", f"{table_name}.{live_name} -> {desired_name}",
                f"ALTER TABLE {table_sql} RENAME COLUMN {quote_identifier(live_name)} TO {quote_identifier(desired_name)}",
            )
            step["rename"] = True
            add(step)
        for name in sorted(set(lcols) - set(dcols) - renamed_live_names):
            add(self._step("drop", "column", f"{table_name}.{name}", f"ALTER TABLE {live_table_sql} DROP COLUMN {quote_identifier(name)}", True))
        for name in sorted(set(dcols) - set(lcols) - renamed_desired_names):
            column = dcols[name]
            clause = f"ALTER TABLE {table_sql} ADD COLUMN {self._column_definition(column, table_name)}"
            add(self._step("add", "column", f"{table_name}.{name}", clause))
        matched_columns = [(name, name) for name in sorted(set(lcols) & set(dcols))]
        matched_columns.extend(sorted(column_renames.items()))
        for live_name, desired_name in matched_columns:
            lc, dc = lcols[live_name], dcols[desired_name]
            live_generation = ((lc.get("postgres") or {}).get("identity", ""), (lc.get("postgres") or {}).get("generated", ""))
            desired_generation = ((dc.get("postgres") or {}).get("identity", ""), (dc.get("postgres") or {}).get("generated", ""))
            if live_generation != desired_generation:
                warnings.append({"code": "unsupported", "message": f"Identity/generated change for {table_name}.{desired_name} requires a manual migration"})
            type_changed = _normalized_type(lc.get("type", "")) != _normalized_type(dc.get("type", ""))
            type_change_blocked = False
            type_change_added = False
            if type_changed:
                source_timestamp_kind = _timestamp_timezone_kind(lc.get("type", ""))
                target_timestamp_kind = _timestamp_timezone_kind(dc.get("type", ""))
                timezone_conversion = (
                    source_timestamp_kind is not None
                    and target_timestamp_kind is not None
                    and source_timestamp_kind != target_timestamp_kind
                )
                blocking_dependencies = (
                    self._column_has_blocking_timezone_dependencies(live, lt, lc)
                    if timezone_conversion
                    else self._column_has_dependencies(live, lt, lc)
                )
                if blocking_dependencies:
                    warnings.append({"code": "unsupported", "message": f"Type change for {table_name}.{desired_name} has dependent objects and requires a manual migration"})
                    type_change_blocked = True
                else:
                    raw_type = self._raw(dc, "type", f"column {table_name}.{desired_name}")
                    if lc.get("default") not in (None, ""):
                        add(self._step(
                            "drop", "column_default", f"{table_name}.{desired_name}",
                            f"ALTER TABLE {table_sql} ALTER COLUMN {quote_identifier(desired_name)} DROP DEFAULT",
                            True,
                        ))
                    using_sql = f" USING CAST({quote_identifier(desired_name)} AS {raw_type})"
                    if timezone_conversion:
                        source_timezone = (live.get("postgres") or {}).get("timeZone")
                        if not isinstance(source_timezone, str) or not source_timezone:
                            warnings.append({
                                "code": "unsupported",
                                "message": f"Timestamp conversion for {table_name}.{desired_name} requires the source database timezone",
                            })
                            continue
                        using_sql = f" USING {quote_identifier(desired_name)} AT TIME ZONE {_quote_literal(source_timezone)}"
                    type_change_added = add(self._step(
                        "alter", "column_type", f"{table_name}.{desired_name}",
                        f"ALTER TABLE {table_sql} ALTER COLUMN {quote_identifier(desired_name)} TYPE {raw_type}{using_sql}",
                        True,
                    ))
            if bool(lc.get("nullable", True)) != bool(dc.get("nullable", True)):
                operation = "DROP NOT NULL" if dc.get("nullable", True) else "SET NOT NULL"
                add(self._step("alter", "column_nullability", f"{table_name}.{desired_name}", f"ALTER TABLE {table_sql} ALTER COLUMN {quote_identifier(desired_name)} {operation}", not dc.get("nullable", True)))
            if type_changed and not type_change_blocked:
                if type_change_added and dc.get("default") not in (None, ""):
                    default = self._raw(dc, "default", f"column {table_name}.{desired_name}")
                    raw_type = self._raw(dc, "type", f"column {table_name}.{desired_name}")
                    add(self._step(
                        "alter", "column_default", f"{table_name}.{desired_name}",
                        f"ALTER TABLE {table_sql} ALTER COLUMN {quote_identifier(desired_name)} SET DEFAULT CAST(({default}) AS {raw_type})",
                    ))
            elif not type_changed and (lc.get("default") or "") != (dc.get("default") or ""):
                if dc.get("default") in (None, ""):
                    add(self._step("drop", "column_default", f"{table_name}.{desired_name}", f"ALTER TABLE {table_sql} ALTER COLUMN {quote_identifier(desired_name)} DROP DEFAULT", True))
                else:
                    if _is_sequence_default(dc.get("default")):
                        raise ValidationError(f"Sequence-backed default for {table_name}.{desired_name} requires a manual migration")
                    default = self._raw(dc, "default", f"column {table_name}.{desired_name}")
                    add(self._step("alter", "column_default", f"{table_name}.{desired_name}", f"ALTER TABLE {table_sql} ALTER COLUMN {quote_identifier(desired_name)} SET DEFAULT {default}"))
        normalized_live = self._normalize_live_column_names(lt, dt)
        self._diff_table_objects(namespace, normalized_live, dt, add, warnings, block_key_changes=block_key_changes)

    def _diff_table_objects(self, namespace, live, desired, add, warnings, block_key_changes=False):
        live_table_sql = f"{quote_identifier(namespace)}.{quote_identifier(live['name'])}"
        table_sql = f"{quote_identifier(namespace)}.{quote_identifier(desired['name'])}"
        for kind in ("primary key", "unique constraint", "check"):
            old, new = self._constraint_map(live, kind), self._constraint_map(desired, kind)
            unchanged = {
                name for name in set(old) & set(new)
                if self._constraint_signature(live, old[name], kind) == self._constraint_signature(desired, new[name], kind)
            }
            old_remaining = set(old) - unchanged
            new_remaining = set(new) - unchanged
            if kind in {"primary key", "unique constraint"}:
                matched_new = set()
                for old_name in sorted(old_remaining):
                    candidates = [
                        new_name for new_name in sorted(new_remaining - matched_new)
                        if self._constraint_signature(live, old[old_name], kind)
                        == self._constraint_signature(desired, new[new_name], kind)
                    ]
                    if len(candidates) != 1:
                        continue
                    new_name = candidates[0]
                    add(self._step(
                        "alter", kind, f"{desired['name']}.{old_name} -> {new_name}",
                        f"ALTER TABLE {table_sql} RENAME CONSTRAINT {quote_identifier(old_name)} TO {quote_identifier(new_name)}",
                    ) | {"constraintRename": (table_sql, old_name, new_name)})
                    matched_new.add(new_name)
                    old_remaining.remove(old_name)
                new_remaining -= matched_new
            for name in sorted(old_remaining | new_remaining):
                if block_key_changes and kind in {"primary key", "unique constraint"} and name in old:
                    warnings.append({"code": "unsupported", "message": f"Changing {kind} {name} while foreign keys reference the table requires a manual migration"})
                    continue
                can_replace = True
                if name in old:
                    can_replace = add(self._step("drop", kind, f"{desired['name']}.{name}", f"ALTER TABLE {live_table_sql} DROP CONSTRAINT {quote_identifier(name)}", True))
                if name in new:
                    if not can_replace:
                        warnings.append({"code": "replacement_omitted", "message": f"Omitted replacement of {kind} {name} because its drop was omitted"})
                        continue
                    item = new[name]
                    if kind == "check":
                        definition = item.get("definition")
                        if not isinstance(definition, str) or not re.match(r"^CHECK\s*\(", definition.strip(), re.I):
                            warnings.append({"code": "unsupported", "message": f"Check {name} requires a CHECK (...) definition"})
                            continue
                        definition = _sql_fragment(definition, f"Check {name}")
                        if not item.get("validated", True) and not re.search(r"\bNOT\s+VALID\s*$", definition, re.I):
                            definition += " NOT VALID"
                    else:
                        names = self._column_names(desired, list(item.get("columnIds") or []))
                        if not names:
                            warnings.append({"code": "unsupported", "message": f"Constraint {name} has no columns"})
                            continue
                        keyword = "PRIMARY KEY" if kind == "primary key" else "UNIQUE"
                        definition = keyword + " (" + ", ".join(quote_identifier(value) for value in names) + ")"
                        if item.get("deferrable"):
                            definition += " DEFERRABLE"
                            if item.get("initiallyDeferred"):
                                definition += " INITIALLY DEFERRED"
                    add(self._step("add", kind, f"{desired['name']}.{name}", f"ALTER TABLE {table_sql} ADD CONSTRAINT {quote_identifier(name)} {definition}"), last=True)
        old_indexes = self._named(live.get("indexes", []), "index")
        new_indexes = self._named(desired.get("indexes", []), "index")
        for name in sorted(set(old_indexes) | set(new_indexes)):
            if name in old_indexes and name in new_indexes and old_indexes[name].get("definition") == new_indexes[name].get("definition"):
                continue
            can_replace = True
            if name in old_indexes:
                can_replace = add(self._step("drop", "index", name, f"DROP INDEX {quote_identifier(namespace)}.{quote_identifier(name)}", True))
            if name in new_indexes:
                if not can_replace:
                    warnings.append({"code": "replacement_omitted", "message": f"Omitted replacement of index {name} because its drop was omitted"})
                    continue
                definition = new_indexes[name].get("definition")
                if not isinstance(definition, str) or not re.match(r"^CREATE\s+(?:UNIQUE\s+)?INDEX\b", definition.strip(), re.I):
                    warnings.append({"code": "unsupported", "message": f"Index {name} requires a full CREATE INDEX definition"})
                else:
                    definition = _single_sql_statement(definition, f"Index {name}")
                    _require_definition_identity(definition, "index", namespace, name, desired["name"])
                    add(self._step("create", "index", name, definition), last=True)
        old_triggers = self._named(live.get("triggers", []), "trigger")
        new_triggers = self._named(desired.get("triggers", []), "trigger")
        for name in sorted(set(old_triggers) | set(new_triggers)):
            if (
                name in old_triggers and name in new_triggers
                and _normalized_sql_whitespace(old_triggers[name].get("definition", ""))
                == _normalized_sql_whitespace(new_triggers[name].get("definition", ""))
            ):
                if old_triggers[name].get("enabled", "O") != new_triggers[name].get("enabled", "O"):
                    mode_sql = self._trigger_enabled_sql(table_sql, name, new_triggers[name].get("enabled", "O"))
                    add(self._step("alter", "trigger", f"{desired['name']}.{name}", mode_sql), last=True)
                continue
            can_replace = True
            if name in old_triggers:
                can_replace = add(self._step("drop", "trigger", f"{desired['name']}.{name}", f"DROP TRIGGER {quote_identifier(name)} ON {live_table_sql}", True))
            if name in new_triggers:
                if not can_replace:
                    warnings.append({"code": "replacement_omitted", "message": f"Omitted replacement of trigger {name} because its drop was omitted"})
                    continue
                definition = new_triggers[name].get("definition")
                if not isinstance(definition, str) or not re.match(r"^CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\b", definition.strip(), re.I):
                    warnings.append({"code": "unsupported", "message": f"Trigger {name} requires a full CREATE TRIGGER definition"})
                else:
                    definition = _single_sql_statement(definition, f"Trigger {name}")
                    _require_definition_identity(definition, "trigger", namespace, name, desired["name"])
                    add(self._step("create", "trigger", f"{desired['name']}.{name}", definition), last=True)
                    if new_triggers[name].get("enabled", "O") != "O":
                        add(self._step("alter", "trigger", f"{desired['name']}.{name}", self._trigger_enabled_sql(table_sql, name, new_triggers[name].get("enabled"))), last=True)

    @staticmethod
    def _trigger_enabled_sql(table_sql: str, name: str, mode: str) -> str:
        keyword = {"O": "ENABLE", "D": "DISABLE", "R": "ENABLE REPLICA", "A": "ENABLE ALWAYS"}.get(mode)
        if not keyword:
            raise ValidationError(f"Trigger {name} has an invalid enabled mode")
        return f"ALTER TABLE {table_sql} {keyword} TRIGGER {quote_identifier(name)}"

    def _constraint_signature(self, table, item, kind):
        if kind == "check":
            return (item.get("definition"), bool(item.get("validated", True)))
        return (
            tuple(self._column_names(table, list(item.get("columnIds") or []))),
            bool(item.get("deferrable")), bool(item.get("initiallyDeferred")),
        )

    @staticmethod
    def _relation_ids(relation: dict[str, Any], side: str) -> list[str]:
        plural = relation.get(side + "ColumnIds")
        return list(plural) if isinstance(plural, list) else [relation.get(side + "ColumnId")]

    def _relationship_signature(self, schema, relation):
        tables = {table["id"]: table for table in schema.get("tables", [])}
        source = tables.get(relation.get("fromTableId"))
        target = tables.get(relation.get("toTableId"))
        if source is None:
            raise ValidationError(f"Relationship {relation.get('name', relation.get('id', ''))} has an unknown source table")
        source_names = self._column_names(source, self._relation_ids(relation, "from"))
        if target:
            target_name = target["name"]
            target_namespace = target.get("namespace") or schema.get("postgres", {}).get("namespace")
            target_names = self._column_names(target, self._relation_ids(relation, "to"))
        else:
            target_name, target_namespace = relation.get("targetTableName"), relation.get("targetNamespace")
            target_names = list(relation.get("targetColumnNames") or [])
            if not target_names:
                raise ValidationError(f"Relationship {relation.get('name', relation.get('id', ''))} target is outside the desired schema")
        return (
            source["name"], tuple(source_names), target_namespace, target_name, tuple(target_names),
            relation.get("onUpdate", "NO ACTION"), relation.get("onDelete", "NO ACTION"),
            bool(relation.get("deferrable")), bool(relation.get("initiallyDeferred")),
            relation.get("matchType", "SIMPLE"), bool(relation.get("validated", True)),
        )

    def _diff_relationships(
        self, namespace, live, desired, add, warnings, rebuild_targets=None,
        skip_table_ids=None,
    ):
        rebuild_targets = rebuild_targets or {}
        skip_table_ids = skip_table_ids or set()
        def keyed(schema):
            result = {}
            table_names = {table.get("id"): table.get("name") for table in schema.get("tables", [])}
            for relation in schema.get("relationships", []):
                name = relation.get("constraintName") or relation.get("name")
                if not isinstance(name, str) or not name:
                    signature = self._relationship_signature(schema, relation)
                    name = f"{signature[0]}_{'_'.join(signature[1])}_fkey"
                    relation = {**relation, "name": name, "constraintName": name}
                source_name = table_names.get(relation.get("fromTableId"))
                key = (source_name, name)
                if key in result:
                    raise ValidationError(f"Duplicate foreign key name {name} on table {source_name}")
                result[key] = relation
            return result
        old, new = keyed(live), keyed(desired)
        desired_tables = {table["id"]: table for table in desired.get("tables", [])}
        for relationship_key in sorted(set(old) | set(new)):
            constraint_name = relationship_key[1]
            old_relation = old.get(relationship_key)
            new_relation = new.get(relationship_key)
            if any(
                relation and (
                    relation.get("fromTableId") in skip_table_ids
                    or relation.get("toTableId") in skip_table_ids
                )
                for relation in (old_relation, new_relation)
            ):
                continue
            old_sig = self._relationship_signature(live, old_relation) if old_relation else None
            new_sig = self._relationship_signature(desired, new_relation) if new_relation else None
            rebuild = bool(
                old_relation
                and old_relation.get("toTableId") in rebuild_targets
                and old_sig[4] in rebuild_targets[old_relation.get("toTableId")]
            ) or bool(
                new_relation
                and new_relation.get("toTableId") in rebuild_targets
                and new_sig[4] in rebuild_targets[new_relation.get("toTableId")]
            )
            if old_sig == new_sig and not rebuild:
                continue
            source_name = (new_sig or old_sig)[0]
            table_sql = f"{quote_identifier(namespace)}.{quote_identifier(source_name)}"
            can_replace = True
            if relationship_key in old:
                can_replace = add(self._step("drop", "foreign key", f"{source_name}.{constraint_name}", f"ALTER TABLE {table_sql} DROP CONSTRAINT {quote_identifier(constraint_name)}", True))
            if relationship_key in new:
                if not can_replace:
                    warnings.append({"code": "replacement_omitted", "message": f"Omitted replacement of foreign key {source_name}.{constraint_name} because its drop was omitted"})
                    continue
                signature = new_sig
                source_cols, target_ns, target_name, target_cols = signature[1], signature[2], signature[3], signature[4]
                if not target_ns or not target_name or not target_cols:
                    warnings.append({"code": "unsupported", "message": f"Foreign key {source_name}.{constraint_name} has an incomplete target"})
                    continue
                sql = (f"ALTER TABLE {table_sql} ADD CONSTRAINT {quote_identifier(constraint_name)} FOREIGN KEY ("
                       + ", ".join(map(quote_identifier, source_cols)) + f") REFERENCES {quote_identifier(target_ns)}.{quote_identifier(target_name)} ("
                       + ", ".join(map(quote_identifier, target_cols)) + ")")
                if signature[9] != "SIMPLE":
                    if signature[9] not in {"FULL", "PARTIAL"}:
                        raise ValidationError(f"Foreign key {constraint_name} has an invalid match type")
                    sql += f" MATCH {signature[9]}"
                for clause, action in (("ON UPDATE", signature[5]), ("ON DELETE", signature[6])):
                    if action and action != "NO ACTION":
                        if action not in {"RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"}:
                            raise ValidationError(f"Foreign key {constraint_name} has an invalid action")
                        sql += f" {clause} {action}"
                if signature[7]:
                    sql += " DEFERRABLE"
                    if signature[8]:
                        sql += " INITIALLY DEFERRED"
                if not signature[10]:
                    sql += " NOT VALID"
                add(self._step("add", "foreign key", f"{source_name}.{constraint_name}", sql), last=True)

    def _diff_root_definitions(self, namespace, object_type, live_items, desired_items, add, warnings):
        def key(item):
            return (item.get("kind", "function"), item.get("name"), item.get("identityArguments", ""))
        old, new = {key(item): item for item in live_items}, {key(item): item for item in desired_items}
        for identity in sorted(set(old) | set(new)):
            if identity in old and identity in new and old[identity].get("definition") == new[identity].get("definition"):
                continue
            kind, name, args = identity
            label = f"{name}({args})"
            if identity in old and identity not in new:
                keyword = "PROCEDURE" if kind == "procedure" else "FUNCTION"
                safe_args = _sql_fragment(args, f"Routine {label} identity arguments") if args else ""
                add(self._step("drop", kind, label, f"DROP {keyword} {quote_identifier(namespace)}.{quote_identifier(name)}({safe_args})", True))
            if identity in new and (identity not in old or old[identity].get("definition") != new[identity].get("definition")):
                definition = new[identity].get("definition")
                expected = r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\b"
                if not isinstance(definition, str) or not re.match(expected, definition.strip(), re.I):
                    warnings.append({"code": "unsupported", "message": f"Routine {label} requires a full CREATE definition"})
                elif identity in old and not re.match(r"^CREATE\s+OR\s+REPLACE\s+", definition.strip(), re.I):
                    warnings.append({"code": "unsupported", "message": f"Existing routine {label} must use CREATE OR REPLACE"})
                else:
                    definition = _single_sql_statement(definition, f"Routine {label}")
                    _require_definition_identity(definition, "routine", namespace, name)
                    add(self._step("create_or_replace", kind, label, definition), last=True)

    def _diff_views(self, namespace, live_items, desired_items, add, warnings):
        old, new = self._named(live_items, "view"), self._named(desired_items, "view")
        for name in sorted(set(old) | set(new)):
            if name in old and name in new and old[name].get("definition") == new[name].get("definition"):
                continue
            materialized = (new.get(name) or old.get(name) or {}).get("materialized", False)
            can_replace = True
            type_changed = name in old and name in new and bool(old[name].get("materialized")) != bool(new[name].get("materialized"))
            if name in old and name not in new:
                warnings.append({"code": "dedicated_view_lifecycle_required", "message": f"Deleting view {name} requires the live Views workspace"})
                continue
            if name in old and (materialized or type_changed):
                warnings.append({"code": "dedicated_view_lifecycle_required", "message": f"Changing materialized view {name} requires the live Views workspace"})
                continue
            if name in old and (name not in new or materialized or type_changed):
                keyword = "MATERIALIZED VIEW" if old[name].get("materialized") else "VIEW"
                can_replace = add(self._step("drop", keyword.lower(), name, f"DROP {keyword} {quote_identifier(namespace)}.{quote_identifier(name)}", True))
            if name in new:
                if not can_replace:
                    warnings.append({"code": "replacement_omitted", "message": f"Omitted replacement of view {name} because its drop was omitted"})
                    continue
                definition = new[name].get("definition")
                pattern = r"^CREATE\s+MATERIALIZED\s+VIEW\b" if materialized else r"^CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\b"
                if not isinstance(definition, str) or not re.match(pattern, definition.strip(), re.I):
                    warnings.append({"code": "unsupported", "message": f"View {name} requires a full CREATE definition"})
                elif name in old and not materialized and not re.match(r"^CREATE\s+OR\s+REPLACE\s+VIEW\b", definition.strip(), re.I):
                    warnings.append({"code": "unsupported", "message": f"Existing view {name} must use CREATE OR REPLACE VIEW"})
                else:
                    definition = _single_sql_statement(definition, f"View {name}")
                    _require_definition_identity(definition, "view", namespace, name)
                    add(self._step("create_or_replace", "materialized view" if materialized else "view", name, definition), last=True)

    # ---- apply ----------------------------------------------------------

    def _acquire_namespace_mutation_lock(self, cursor: Any, namespace: str, database: str | None = None) -> None:
        cursor.execute(f"SET LOCAL lock_timeout = '{self._lock_timeout_ms}ms'")
        cursor.execute(f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'")
        database = database or cursor.connection.info.dbname
        lock_keys = namespace_lock_keys(database, namespace)
        cursor.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(%s, %s)",
            lock_keys,
        )

    def view_mutation_binding(self, profile_id: str, plan_id: str) -> dict[str, Any]:
        if self._migration_coordinator is not None:
            plan = self._migration_coordinator.metadata.get_migration_plan(plan_id, include_private=True)
            private = plan["privatePayload"]
            return {"schemaBinding": private["schemaBinding"], "database": plan["target"]["databaseName"],
                    "namespace": plan["target"]["namespaceName"], "relation": private["relation"],
                    "operation": private["operation"], "expectation": private["expectation"]}
        raise PostgresServiceError(503, "durable_migrations_unavailable", "View mutation plans require durable metadata")
        profile_id = self._validate_profile_id(profile_id)
        if not isinstance(plan_id, str) or not PROFILE_ID_RE.fullmatch(plan_id):
            raise ValidationError("plan_id is invalid")
        with self._lock:
            self._purge_retired_authority(self._clock())
            plan = copy.deepcopy(self._removed_plan_authority.get(plan_id))
        if plan is None or plan.get("kind") != "view_mutation" or plan["profileId"] != profile_id:
            raise NotFoundError("Plan was not found or has expired")
        return {
            "schemaBinding": plan["schemaBinding"], "database": plan["database"],
            "namespace": plan["namespace"], "relation": plan["relation"],
            "operation": plan["operation"], "expectation": plan["expectation"],
        }

    @postgres_execution("write")
    def apply_view_mutation(self, profile_id: str, plan_id: str, confirm_destructive: bool = False) -> dict[str, Any]:
        if self._migration_coordinator is not None:
            status = self._migration_coordinator.status(plan_id)
            return self._migration_coordinator.apply(plan_id, status["reviewDigest"], confirm_destructive, expected_profile_id=profile_id)
        raise PostgresServiceError(503, "durable_migrations_unavailable", "View mutation execution requires durable metadata")
        profile_id = self._validate_profile_id(profile_id)
        if not isinstance(plan_id, str) or not PROFILE_ID_RE.fullmatch(plan_id):
            raise ValidationError("plan_id is invalid")
        if not isinstance(confirm_destructive, bool):
            raise ValidationError("confirmDestructive must be boolean")
        with self._lock:
            self._purge_retired_authority(self._clock())
            plan = copy.deepcopy(self._removed_plan_authority.get(plan_id))
            profile = copy.deepcopy(self._read_profiles().get(profile_id))
        if plan is None or plan.get("kind") != "view_mutation" or plan.get("planVersion") != 2 or plan["profileId"] != profile_id:
            raise NotFoundError("Plan was not found or has expired")
        if profile is None or self._profile_fingerprint(profile) != plan["profileFingerprint"]:
            raise ConflictError("profile_changed", "Connection profile changed after preview")
        if plan["destructive"] and not confirm_destructive:
            raise ConflictError("destructive_confirmation_required", "Plan contains destructive steps")
        with self._lock:
            stored_plan = self._removed_plan_authority.get(plan_id)
            if stored_plan is None or stored_plan.get("state") != "ready":
                raise ConflictError("plan_in_use", "View plan is already being applied")
            stored_plan["state"] = "applying"

        connection = None
        descriptor = None
        committed_at = None
        commit_outcome_uncertain = False
        failed_step = None
        try:
            connection = self._connect_profile(profile)
            cursor = connection.cursor()
            try:
                cursor.execute("BEGIN")
                self._acquire_namespace_mutation_lock(cursor, plan["namespace"], plan["database"])
                expectation = plan["expectation"]
                if set(expectation) != {"absent"}:
                    relation_sql = f"{quote_identifier(plan['namespace'])}.{quote_identifier(plan['relation'])}"
                    if expectation["kind"] == "materialized_view":
                        # PostgreSQL 17 rejects LOCK TABLE for materialized views. A no-data
                        # refresh acquires AccessExclusiveLock and is rolled back on failure.
                        cursor.execute(f"REFRESH MATERIALIZED VIEW {relation_sql} WITH NO DATA")
                    else:
                        # A view read takes AccessShare on the view and its base relations. It
                        # blocks target DDL without AccessExclusive-locking the base relations.
                        cursor.execute(f"SELECT * FROM {relation_sql} LIMIT 0")
                try:
                    current = self._inspect_relation_connection(
                        connection, profile_id, plan["database"], plan["namespace"], plan["relation"], None, None,
                    )
                except NotFoundError:
                    current = None
                if set(expectation) == {"absent"}:
                    if current is not None:
                        raise ConflictError("relation_changed", "The expected-absent PostgreSQL relation now exists")
                elif (
                    current is None or current["kind"] != expectation["kind"]
                    or current["fingerprint"] != expectation["fingerprint"]
                ):
                    raise ConflictError("relation_changed", "The PostgreSQL relation changed after preview")
                if plan["destructive"]:
                    preservation = self._view_recreation_preservation(connection, plan["namespace"], plan["relation"]) if current and current["kind"] == "materialized_view" else None
                    dependents = current.get("dependents", {}) if current else {}
                    blocked = []
                    if dependents.get("status") != "available" or dependents.get("truncated") or dependents.get("items"):
                        blocked.append("direct dependents")
                    if preservation:
                        blocked.extend(preservation["unsupported"])
                        if preservation["fingerprint"] != plan["preservation"]["fingerprint"]:
                            raise ConflictError("relation_changed", "Materialized view metadata changed after preview")
                    if blocked:
                        raise PostgresServiceError(
                            409, "view_recreation_unsupported",
                            "Destructive view recreation would lose or invalidate unsupported metadata",
                            {"concerns": blocked},
                        )
                owner = (plan.get("preservation") or {}).get("owner")
                if owner and plan["operation"] == "upsert":
                    cursor.execute(f"SET LOCAL ROLE {quote_identifier(owner)}")
                for index, step in enumerate(plan["steps"]):
                    failed_step = (index, step)
                    cursor.execute(step["sql"])
                failed_step = None
                if owner and plan["operation"] == "upsert":
                    cursor.execute("RESET ROLE")
                if plan["operation"] == "delete":
                    try:
                        self._inspect_relation_connection(connection, profile_id, plan["database"], plan["namespace"], plan["relation"], None, None)
                    except NotFoundError:
                        pass
                    else:
                        raise ConflictError("relation_changed", "PostgreSQL relation still exists after delete")
                else:
                    descriptor = self._inspect_relation_connection(
                        connection, profile_id, plan["database"], plan["namespace"], plan["relation"], plan["desiredKind"], None,
                    )
                try:
                    connection.commit()
                except Exception as exc:
                    commit_outcome_uncertain = True
                    postgres = postgres_error_diagnostic(exc)
                    raise PostgresServiceError(
                        500, "execution_outcome_unknown",
                        "View mutation commit outcome is uncertain; refresh PostgreSQL and the saved schema before continuing",
                        {"postgres": postgres} if postgres else None,
                    ) from exc
                committed_at = _utc_now()
            except PostgresServiceError:
                try:
                    connection.rollback()
                except Exception:
                    pass
                raise
            except Exception:
                connection.rollback()
                raise
            finally:
                close = getattr(cursor, "close", None)
                if close:
                    close()
        except PostgresServiceError:
            raise
        except Exception as exc:
            message = "View mutation failed and was rolled back"
            postgres = postgres_error_diagnostic(exc)
            # Planned view SQL may be rewritten after preview, so a server position
            # cannot be mapped reliably back to the user's definition text.
            postgres.pop("position", None)
            if failed_step is not None:
                index, step = failed_step
                message = f"View mutation step {index + 1} failed: {step['action']} {step['objectType']} {step['name']}. All changes were rolled back"
            if postgres.get("message"):
                message += f": {postgres['message']}"
            details = {"postgres": postgres} if postgres else None
            if failed_step is not None:
                details = {**(details or {}), "stepIndex": failed_step[0]}
            raise PostgresServiceError(422, "apply_failed", message, details) from exc
        finally:
            if connection is not None:
                self._close(connection)
            if committed_at is None:
                with self._lock:
                    stored_plan = self._removed_plan_authority.get(plan_id)
                    if stored_plan and stored_plan.get("state") == "applying":
                        stored_plan["state"] = "uncertain" if commit_outcome_uncertain else "ready"
        with self._lock:
            self._removed_plan_authority.pop(plan_id, None)
        try:
            self._append_history({
                "id": "migration_" + secrets.token_hex(12), "planId": plan_id,
                "profileId": profile_id, "database": plan["database"], "namespace": plan["namespace"],
                "appliedAt": committed_at, "sourceFingerprint": plan["expectation"].get("fingerprint"),
                "resultFingerprint": descriptor["fingerprint"] if descriptor else None, "destructive": plan["destructive"],
                "operation": plan["operation"],
                "steps": copy.deepcopy(plan["steps"]),
            })
        except PostgresServiceError:
            history_warning = {
                "code": "history_store_error",
                "message": "Migration committed, but its local history entry could not be written",
            }
        else:
            history_warning = None
        result = {
            "applied": True, "planId": plan_id, "operation": plan["operation"],
            "schemaBinding": plan["schemaBinding"], "expectedAbsent": set(plan["expectation"]) == {"absent"},
        }
        if descriptor:
            result.update(descriptor=descriptor, desiredDefinition=plan["desiredDefinition"], queryDefinition=descriptor.get("definition", {}).get("sql"))
        else:
            result["deleted"] = {
                "profileId": profile_id, "database": plan["database"], "namespace": plan["namespace"],
                "relation": plan["relation"], "kind": plan["expectation"]["kind"],
            }
        if history_warning:
            result["warnings"] = [history_warning]
        return result

    @postgres_execution("write")
    def apply(self, profile_id: str, plan_id: str, confirm_destructive: bool = False) -> dict[str, Any]:
        if self._migration_coordinator is not None:
            status = self._migration_coordinator.status(plan_id)
            return self._migration_coordinator.apply(plan_id, status["reviewDigest"], confirm_destructive, expected_profile_id=profile_id)
        raise PostgresServiceError(503, "durable_migrations_unavailable", "Migration execution requires durable metadata")
        profile_id = self._validate_profile_id(profile_id)
        if not isinstance(plan_id, str) or not PROFILE_ID_RE.fullmatch(plan_id):
            raise ValidationError("plan_id is invalid")
        if not isinstance(confirm_destructive, bool):
            raise ValidationError("confirm_destructive must be boolean")
        with self._lock:
            self._purge_retired_authority(self._clock())
            plan = copy.deepcopy(self._removed_plan_authority.get(plan_id))
            profile = copy.deepcopy(self._read_profiles().get(profile_id))
        if plan is None or plan["profileId"] != profile_id:
            raise NotFoundError("Plan was not found or has expired")
        if plan.get("kind") == "view_mutation":
            raise NotFoundError("Plan was not found or has expired")
        if profile is None or self._profile_fingerprint(profile) != plan["profileFingerprint"]:
            raise ConflictError("profile_changed", "Connection profile changed after preview")
        if plan["destructive"] and not confirm_destructive:
            raise ConflictError("destructive_confirmation_required", "Plan contains destructive steps")
        connection = self._connect_profile(profile)
        refreshed = None
        committed_at = None
        failed_step = None
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("BEGIN")
                self._acquire_namespace_mutation_lock(cursor, plan["namespace"], plan["database"])
                current = self._introspect_connection(connection, profile_id, plan["namespace"])
                if current["postgres"]["fingerprint"] != plan["liveFingerprint"]:
                    raise ConflictError("stale_plan", "Database schema changed after preview")
                for index, step in enumerate(plan["steps"]):
                    failed_step = (index, step)
                    cursor.execute(step["sql"])
                failed_step = None
                refreshed = self._introspect_connection(connection, profile_id, plan["namespace"])
                connection.commit()
                committed_at = _utc_now()
            except PostgresServiceError:
                rollback = getattr(connection, "rollback", None)
                if rollback:
                    rollback()
                raise
            except Exception:
                rollback = getattr(connection, "rollback", None)
                if rollback:
                    rollback()
                raise
            finally:
                close = getattr(cursor, "close", None)
                if close:
                    close()
        except PostgresServiceError:
            raise
        except Exception as exc:
            message = "PostgreSQL plan failed and was rolled back"
            if failed_step is not None:
                index, step = failed_step
                message = (
                    f"Migration step {index + 1} failed: {step['action']} "
                    f"{step['objectType']} {step['name']}. All changes were rolled back"
                )
            raise PostgresServiceError(422, "apply_failed", message) from exc
        finally:
            self._close(connection)
        with self._lock:
            self._removed_plan_authority.pop(plan_id, None)
        try:
            self._append_history({
                "id": "migration_" + secrets.token_hex(12),
                "planId": plan_id,
                "profileId": profile_id,
                "database": profile["dbname"],
                "namespace": plan["namespace"],
                "appliedAt": committed_at,
                "sourceFingerprint": plan["liveFingerprint"],
                "resultFingerprint": refreshed.get("postgres", {}).get("fingerprint"),
                "destructive": plan["destructive"],
                "steps": copy.deepcopy(plan["steps"]),
            })
        except PostgresServiceError:
            refreshed.setdefault("postgres", {}).setdefault("warnings", []).append({
                "code": "history_store_error",
                "message": "Migration committed, but its local history entry could not be written",
            })
        return refreshed


__all__ = [
    "PostgresService", "PostgresServiceError", "ValidationError", "NotFoundError",
    "ConflictError", "canonical_fingerprint", "quote_identifier",
]
