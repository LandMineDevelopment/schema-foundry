"""PostgreSQL profile, introspection, preview, and apply service.

The module deliberately has no HTTP dependency.  ``PostgresService`` methods
return JSON-serializable values and raise ``PostgresServiceError`` for a thin
HTTP adapter (such as server.py) to translate into responses.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, time as datetime_time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

try:
    import fcntl
except ImportError:  # pragma: no cover - direct Windows use has one process per profile store.
    fcntl = None

PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
SQL_IDENTIFIER_RE = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
SQL_QUALIFIED_RE = rf'{SQL_IDENTIFIER_RE}(?:\s*\.\s*{SQL_IDENTIFIER_RE})?'
SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
COLORS = ("#f4b942", "#65a9ff", "#9b82f4", "#59c894", "#ef7c8e", "#e58d4c")
TRANSIENT_KEYS = {
    "x", "y", "color", "fingerprint", "importedAt", "importTime", "updatedAt",
    "profileId", "sourceProfileId", "liveOid", "layout", "timeZone",
}


class PostgresServiceError(Exception):
    """Safe error suitable for direct serialization by an HTTP adapter."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


def _safe_sql_query_failure(exc: Exception) -> str:
    sqlstate = getattr(exc, "sqlstate", None)
    primary = getattr(getattr(exc, "diag", None), "message_primary", None)
    if not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate) or not isinstance(primary, str):
        return "Read-only SQL query failed"
    primary = " ".join(primary.split())[:500]
    return f"Read-only SQL query failed: {primary}" if primary else "Read-only SQL query failed"


class ValidationError(PostgresServiceError):
    def __init__(self, message: str):
        super().__init__(400, "validation_error", message)


class NotFoundError(PostgresServiceError):
    def __init__(self, message: str):
        super().__init__(404, "not_found", message)


class ConflictError(PostgresServiceError):
    def __init__(self, code: str, message: str):
        super().__init__(409, code, message)


def quote_identifier(value: str) -> str:
    """Always quote a PostgreSQL identifier, including ordinary names."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValidationError("SQL identifier must be a non-empty string")
    return '"' + value.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValidationError("SQL literal must be a non-empty string")
    return "'" + value.replace("'", "''") + "'"


def _semantic_id(kind: str, *parts: Any) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return f"pg_{kind}_{hashlib.sha256(encoded.encode()).hexdigest()[:20]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical_value(item)
            for key, item in sorted(value.items())
            if key not in TRANSIENT_KEYS
        }
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def canonical_fingerprint(schema: dict[str, Any]) -> str:
    """Hash semantic schema content while ignoring canvas/transient fields."""
    canonical = json.dumps(
        _canonical_value(schema), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _top_level_semicolons(value: str) -> list[int]:
    semicolons = []
    quote = None
    escape_string = False
    dollar_quote = None
    block_depth = 0
    index = 0
    while index < len(value):
        character = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if dollar_quote:
            if value.startswith(dollar_quote, index):
                index += len(dollar_quote)
                dollar_quote = None
            else:
                index += 1
            continue
        if quote:
            if character == quote:
                if following == quote:
                    index += 2
                    continue
                quote = None
                escape_string = False
            elif character == "\\" and quote == "'" and escape_string and following:
                index += 2
                continue
            index += 1
            continue
        if block_depth:
            if character == "/" and following == "*":
                block_depth += 1
                index += 2
            elif character == "*" and following == "/":
                block_depth -= 1
                index += 2
            else:
                index += 1
            continue
        if character == "-" and following == "-":
            newline = value.find("\n", index + 2)
            index = len(value) if newline == -1 else newline + 1
            continue
        if character == "/" and following == "*":
            block_depth = 1
            index += 2
            continue
        if character in {"'", '"'}:
            quote = character
            escape_string = (
                character == "'" and index > 0 and value[index - 1] in {"e", "E"}
                and (index < 2 or not (value[index - 2].isalnum() or value[index - 2] in {"_", "$"}))
            )
            index += 1
            continue
        if character == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", value[index:])
            if match:
                dollar_quote = match.group(0)
                index += len(dollar_quote)
                continue
        if character == ";":
            semicolons.append(index)
        index += 1
    if quote or dollar_quote or block_depth:
        raise ValidationError("SQL contains an unterminated quote or comment")
    return semicolons


def _single_sql_statement(value: str, label: str) -> str:
    statement = value.strip()
    semicolons = _top_level_semicolons(statement)
    if len(semicolons) > 1 or (semicolons and statement[semicolons[0] + 1:].strip()):
        raise ValidationError(f"{label} must contain exactly one SQL statement")
    return statement


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


class PostgresService:
    """Backend engine for PostgreSQL-backed schema profiles and changes."""

    def __init__(
        self,
        config_dir: str | os.PathLike[str],
        *,
        connect_factory: Callable[..., Any] | None = None,
        plan_ttl_seconds: int = 900,
        lock_timeout_ms: int = 5000,
        statement_timeout_ms: int = 30000,
        clock: Callable[[], float] = time.time,
    ):
        if not isinstance(plan_ttl_seconds, int) or plan_ttl_seconds < 1:
            raise ValueError("plan_ttl_seconds must be a positive integer")
        if not isinstance(lock_timeout_ms, int) or lock_timeout_ms < 1:
            raise ValueError("lock_timeout_ms must be a positive integer")
        if not isinstance(statement_timeout_ms, int) or statement_timeout_ms < 1:
            raise ValueError("statement_timeout_ms must be a positive integer")
        self.config_dir = Path(config_dir)
        self.profile_path = self.config_dir / "postgres_profiles.json"
        self.profile_lock_path = self.config_dir / ".postgres_profiles.lock"
        self.history_path = self.config_dir / "migration_history.json"
        self._connect_factory = connect_factory
        self._plan_ttl = plan_ttl_seconds
        self._lock_timeout_ms = lock_timeout_ms
        self._statement_timeout_ms = statement_timeout_ms
        self._clock = clock
        self._lock = threading.RLock()
        self._plans: dict[str, dict[str, Any]] = {}
        self._ensure_config_dir()

    # ---- profiles -------------------------------------------------------

    def _ensure_config_dir(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.config_dir, 0o700)
        if self.profile_path.exists():
            os.chmod(self.profile_path, 0o600)
        if self.history_path.exists():
            os.chmod(self.history_path, 0o600)

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
        descriptor = os.open(self.profile_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _write_profiles(self, profiles: dict[str, dict[str, Any]]) -> None:
        self._ensure_config_dir()
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=".postgres_profiles.", suffix=".tmp", dir=self.config_dir)
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"profiles": profiles}, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.profile_path)
            os.chmod(self.profile_path, 0o600)
        except OSError as exc:
            raise PostgresServiceError(500, "profile_store_error", "Profile store could not be written") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

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
        return {"id": profile_id, **{key: value for key, value in profile.items() if key != "password"}}

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
            if existing is not None:
                for plan_key in [key for key, plan in self._plans.items() if plan["profileId"] == profile_id]:
                    del self._plans[plan_key]
            return self._redact(profile_id, profile)

    def delete_profile(self, profile_id: str) -> dict[str, str]:
        profile_id = self._validate_profile_id(profile_id)
        with self._lock:
            with self._profile_store_lock():
                profiles = self._read_profiles()
                if profile_id not in profiles:
                    raise NotFoundError("Profile was not found")
                del profiles[profile_id]
                self._write_profiles(profiles)
            for plan_id in [key for key, plan in self._plans.items() if plan["profileId"] == profile_id]:
                del self._plans[plan_id]
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
            temporary = None
            try:
                descriptor, name = tempfile.mkstemp(prefix=".migration_history.", suffix=".tmp", dir=self.config_dir)
                temporary = Path(name)
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(history, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.history_path)
                os.chmod(self.history_path, 0o600)
            except OSError as exc:
                raise PostgresServiceError(500, "history_store_error", "Migration history could not be written") from exc
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)

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

    def _connect(self, profile_id: str):
        return self._connect_profile(self._profile(profile_id))

    def _connect_profile(self, profile: dict[str, Any]):
        kwargs = {
            "host": profile["host"], "port": profile["port"], "dbname": profile["dbname"],
            "user": profile["user"], "password": profile["password"], "sslmode": profile["sslmode"],
            "connect_timeout": profile["timeout"], "application_name": "schemii",
        }
        try:
            if self._connect_factory is not None:
                return self._connect_factory(**kwargs)
            import psycopg
            from psycopg.rows import dict_row
            return psycopg.connect(**kwargs, row_factory=dict_row)
        except Exception as exc:
            raise PostgresServiceError(502, "connection_failed", "PostgreSQL connection failed") from exc

    @staticmethod
    def _profile_fingerprint(profile: dict[str, Any]) -> str:
        encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _close(connection: Any) -> None:
        close = getattr(connection, "close", None)
        if close:
            close()

    @staticmethod
    def _execute_rows(connection: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            if not rows:
                return []
            if isinstance(rows[0], dict):
                return [dict(row) for row in rows]
            names = [item.name if hasattr(item, "name") else item[0] for item in cursor.description]
            return [dict(zip(names, row)) for row in rows]
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    @staticmethod
    def _execute_statement(connection: Any, query: str, params: tuple[Any, ...] = ()) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(query, params)
        finally:
            close = getattr(cursor, "close", None)
            if close:
                close()

    @staticmethod
    def _json_cell(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (datetime, date, datetime_time)):
            return value.isoformat()
        if isinstance(value, (Decimal, UUID)):
            return str(value)
        if isinstance(value, bytes):
            return "\\x" + value.hex()
        if isinstance(value, dict):
            return {str(key): PostgresService._json_cell(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [PostgresService._json_cell(item) for item in value]
        return str(value)

    def test_profile(self, profile_id: str) -> dict[str, Any]:
        connection = self._connect(profile_id)
        try:
            rows = self._execute_rows(connection, "SELECT current_database() AS database, version() AS version")
            row = rows[0]
            return {"ok": True, "database": row["database"], "serverVersion": row["version"]}
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "query_failed", "PostgreSQL connection test failed") from exc
        finally:
            self._close(connection)

    def list_namespaces(self, profile_id: str) -> list[str]:
        connection = self._connect(profile_id)
        try:
            rows = self._execute_rows(connection, """
                SELECT nspname AS namespace FROM pg_catalog.pg_namespace
                WHERE nspname <> 'information_schema' AND nspname !~ '^pg_'
                ORDER BY nspname
            """)
            return [row["namespace"] for row in rows]
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL namespaces could not be read") from exc
        finally:
            self._close(connection)

    def list_relations(self, profile_id: str, database: str, namespace: str) -> dict[str, Any]:
        database = self._validate_database(database)
        namespace = self._validate_namespace(namespace)
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION READ ONLY")
            current = self._execute_rows(connection, "SELECT current_database() AS database")[0]["database"]
            if current != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            rows = self._execute_rows(connection, """
                SELECT c.relname AS relation_name,
                       CASE WHEN c.relkind IN ('r', 'p') THEN 'table'
                            WHEN c.relkind = 'v' THEN 'view'
                            ELSE 'materialized_view' END AS relation_kind
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
                ORDER BY relation_kind, c.relname
            """, (namespace,))
            return {
                "profileId": profile_id,
                "database": current,
                "namespace": namespace,
                "relations": [{"name": row["relation_name"], "kind": row["relation_kind"]} for row in rows],
            }
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relations could not be read") from exc
        finally:
            self._close(connection)

    def inspect_relation(
        self,
        profile_id: str,
        database: str,
        namespace: str,
        relation: str,
        expected_kind: str | None = None,
        expected_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        database = self._validate_database(database)
        namespace = self._validate_namespace(namespace)
        relation = self._validate_relation_name(relation)
        if expected_kind is not None and expected_kind not in {"table", "view", "materialized_view"}:
            raise ValidationError("expectedKind must be table, view, or materialized_view")
        if expected_fingerprint is not None and (not isinstance(expected_fingerprint, str) or not FINGERPRINT_RE.fullmatch(expected_fingerprint)):
            raise ValidationError("expectedFingerprint must be a 64-character lowercase hexadecimal fingerprint")
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION READ ONLY")
            current = self._execute_rows(connection, "SELECT current_database() AS database")[0]["database"]
            if current != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            relation_rows = self._execute_rows(connection, """
                SELECT c.relkind AS catalog_kind,
                       CASE WHEN c.relkind IN ('r', 'p') THEN 'table'
                            WHEN c.relkind = 'v' THEN 'view'
                            ELSE 'materialized_view' END AS relation_kind,
                       CASE WHEN c.relkind IN ('v', 'm') THEN pg_catalog.pg_get_viewdef(c.oid, true) END AS view_definition
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
            """, (namespace, relation))
            if not relation_rows:
                raise NotFoundError(f"Relation {namespace}.{relation} was not found")
            relation_row = relation_rows[0]
            column_rows = self._execute_rows(connection, """
                SELECT a.attname AS column_name,
                       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                       NOT a.attnotnull AS nullable,
                       a.attnum AS ordinal,
                       COALESCE(base_type.typcategory, attribute_type.typcategory) AS type_category,
                       COALESCE(base_type.typname, attribute_type.typname) AS type_name
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_attribute a
                  ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                JOIN pg_catalog.pg_type attribute_type ON attribute_type.oid = a.atttypid
                LEFT JOIN pg_catalog.pg_type base_type ON base_type.oid = attribute_type.typbasetype
                WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
                ORDER BY a.attnum
            """, (namespace, relation))
            fingerprint_columns = [
                {
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "nullable": bool(row["nullable"]),
                    "ordinal": int(row["ordinal"]),
                }
                for row in column_rows
            ]
            columns = [
                {
                    **column,
                    "suggestions": self._column_role_suggestions(
                        column["name"], row.get("type_category"), row.get("type_name")
                    ),
                }
                for column, row in zip(fingerprint_columns, column_rows)
            ]
            descriptor = {
                "profileId": profile_id,
                "database": current,
                "namespace": namespace,
                "relation": relation,
                "kind": relation_row["relation_kind"],
                "columns": columns,
            }
            descriptor["fingerprint"] = canonical_fingerprint({
                **descriptor, "columns": fingerprint_columns,
                "catalogKind": relation_row["catalog_kind"],
                "viewDefinition": relation_row.get("view_definition"),
            })
            if expected_kind is not None and descriptor["kind"] != expected_kind:
                raise PostgresServiceError(409, "relation_changed", "The PostgreSQL relation kind changed; reselect the widget source")
            if expected_fingerprint is not None and descriptor["fingerprint"] != expected_fingerprint:
                raise PostgresServiceError(409, "relation_changed", "The PostgreSQL relation definition changed; reselect the widget source")
            return descriptor
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relation metadata could not be read") from exc
        finally:
            self._close(connection)

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
            page = rows[:limit]
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
                "rows": [
                    {key: self._json_cell(value) for key, value in row.items()}
                    for row in page
                ],
                "offset": offset,
                "nextOffset": offset + len(page),
                "hasMore": has_more,
                "stableOrder": bool(primary_key),
            }
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "data_preview_failed", "PostgreSQL table data could not be read") from exc
        finally:
            self._close(connection)

    def execute_read_only_sql(self, profile_id: str, namespace: str, statement: Any) -> dict[str, Any]:
        namespace = self._validate_namespace(namespace)
        if not isinstance(statement, str) or not statement.strip():
            raise ValidationError("sql must be a non-empty string")
        if "\x00" in statement or len(statement) > 100_000:
            raise ValidationError("sql must be at most 100000 characters and contain no null bytes")
        statement = _single_sql_statement(statement, "SQL query")
        if not re.match(r"^\s*(?:SELECT|WITH|VALUES|TABLE|EXPLAIN)\b", statement, re.I):
            raise ValidationError("Only read-only SELECT, WITH, VALUES, TABLE, or EXPLAIN queries are allowed")

        connection = self._connect(profile_id)
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'")
            cursor.execute(
                "SELECT pg_catalog.set_config('search_path', %s, true)",
                (f"{quote_identifier(namespace)}, pg_catalog",),
            )
            cursor.execute(statement)
            if cursor.description is None:
                raise ValidationError("The SQL query did not return a result set")
            names = [item.name if hasattr(item, "name") else item[0] for item in cursor.description]
            fetchmany = getattr(cursor, "fetchmany", None)
            raw_rows = fetchmany(501) if fetchmany else cursor.fetchall()[:501]
            truncated = len(raw_rows) > 500
            raw_rows = raw_rows[:500]
            rows = []
            for row in raw_rows:
                values = [row.get(name) for name in names] if isinstance(row, dict) else list(row)
                rows.append([self._json_cell(value) for value in values])
            return {
                "namespace": namespace,
                "columns": [{"name": name} for name in names],
                "rows": rows,
                "rowCount": len(rows),
                "truncated": truncated,
                "maxRows": 500,
            }
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

    def introspect(self, profile_id: str, namespace: str) -> dict[str, Any]:
        namespace = self._validate_namespace(namespace)
        connection = self._connect(profile_id)
        try:
            schema = self._introspect_connection(connection, profile_id, namespace)
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL schema introspection failed") from exc
        finally:
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

    def preview(
        self, profile_id: str, namespace: str, desired_schema: dict[str, Any], allow_destructive: bool = False
    ) -> dict[str, Any]:
        namespace = self._validate_namespace(namespace)
        desired = self._require_schema(copy.deepcopy(desired_schema))
        if not isinstance(allow_destructive, bool):
            raise ValidationError("allow_destructive must be boolean")
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
            "id": plan_id, "profileId": profile_id, "namespace": namespace,
            "liveFingerprint": live["postgres"]["fingerprint"], "allowDestructive": allow_destructive,
            "profileFingerprint": profile_fingerprint,
            "destructive": any(step["destructive"] for step in steps), "steps": copy.deepcopy(steps),
            "warnings": list(warnings), "createdAt": now, "expiresAt": now + self._plan_ttl,
            "desiredSchema": copy.deepcopy(desired),
        }
        with self._lock:
            self._purge_plans(now)
            self._plans[plan_id] = stored
        return self._public_plan(stored)

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
        return copy.deepcopy({key: value for key, value in plan.items() if key not in {"createdAt", "desiredSchema"}})

    def _purge_plans(self, now: float) -> None:
        for plan_id in [key for key, plan in self._plans.items() if plan["expiresAt"] <= now]:
            del self._plans[plan_id]

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

    def apply(self, profile_id: str, plan_id: str, confirm_destructive: bool = False) -> dict[str, Any]:
        profile_id = self._validate_profile_id(profile_id)
        if not isinstance(plan_id, str) or not PROFILE_ID_RE.fullmatch(plan_id):
            raise ValidationError("plan_id is invalid")
        if not isinstance(confirm_destructive, bool):
            raise ValidationError("confirm_destructive must be boolean")
        with self._lock:
            self._purge_plans(self._clock())
            plan = copy.deepcopy(self._plans.get(plan_id))
            profile = copy.deepcopy(self._read_profiles().get(profile_id))
        if plan is None or plan["profileId"] != profile_id:
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
                cursor.execute(f"SET LOCAL lock_timeout = '{self._lock_timeout_ms}ms'")
                cursor.execute(f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'")
                cursor.execute("SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))", (f"schemii:{plan['namespace']}",))
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
            self._plans.pop(plan_id, None)
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
            refreshed.setdefault("postgres", {})["historyWarning"] = "Migration committed, but its local history entry could not be written"
        return refreshed


__all__ = [
    "PostgresService", "PostgresServiceError", "ValidationError", "NotFoundError",
    "ConflictError", "canonical_fingerprint", "quote_identifier",
]
