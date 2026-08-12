from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .postgres_common import NotFoundError, PostgresServiceError, ValidationError, quote_identifier


MAX_STATEMENTS = 20
MAX_SCRIPT_CHARS = 100_000
MAX_ROWS_PER_RESULT = 500
MAX_COLUMNS_PER_RESULT = 100
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_NOTICES = 50
MAX_NOTICE_BYTES = 8 * 1024
MAX_ACTIVE_EXECUTIONS = 4


@dataclass(frozen=True)
class ConsolePolicy:
    allow_write: bool = False
    statement_timeout_ms: int = 30_000


def _scan_sql(value: str) -> tuple[list[int], list[str]]:
    semicolons: list[int] = []
    words: list[str] = []
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
            continue
        if character.isalpha() or character == "_":
            end = index + 1
            while end < len(value) and (value[end].isalnum() or value[end] in {"_", "$"}):
                end += 1
            words.append(value[index:end].upper())
            index = end
            continue
        index += 1
    if quote or dollar_quote or block_depth:
        raise ValidationError("SQL contains an unterminated quote or comment")
    return semicolons, words


def top_level_semicolons(value: str) -> list[int]:
    return _scan_sql(value)[0]


def single_sql_statement(value: str, label: str) -> str:
    statement = value.strip()
    semicolons = top_level_semicolons(statement)
    if len(semicolons) > 1 or (semicolons and statement[semicolons[0] + 1:].strip()):
        raise ValidationError(f"{label} must contain exactly one SQL statement")
    return statement


def split_console_statements(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("sql must be a non-empty string")
    if "\x00" in value:
        raise ValidationError("sql must not contain null bytes")
    if len(value) > MAX_SCRIPT_CHARS:
        raise PostgresServiceError(400, "script_too_large", "SQL script exceeds the 100000-character limit")
    semicolons = top_level_semicolons(value)
    statements = []
    start = 0
    for end in [*semicolons, len(value)]:
        candidate = value[start:end].strip()
        if candidate and _scan_sql(candidate)[1]:
            statements.append(candidate)
        start = end + 1
    if not statements:
        raise ValidationError("sql must contain at least one statement")
    if len(statements) > MAX_STATEMENTS:
        raise PostgresServiceError(400, "too_many_statements", "SQL script exceeds the 20-statement limit")
    for statement in statements:
        words = _scan_sql(statement)[1]
        first = words[0] if words else ""
        transaction_control = first in {"BEGIN", "COMMIT", "END", "ROLLBACK", "ABORT", "SAVEPOINT", "RELEASE"}
        transaction_control = transaction_control or words[:2] in (["START", "TRANSACTION"], ["PREPARE", "TRANSACTION"], ["SET", "TRANSACTION"])
        transaction_control = transaction_control or words[:3] in (["SET", "LOCAL", "TRANSACTION"], ["SET", "SESSION", "TRANSACTION"])
        transaction_control = transaction_control or words[:4] == ["SET", "SESSION", "CHARACTERISTICS", "AS"] and len(words) > 4 and words[4] == "TRANSACTION"
        if transaction_control:
            raise PostgresServiceError(400, "unsupported_transaction_control", "Explicit transaction control is not supported")
    return statements


def _canonical_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a canonical UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"{label} must be a canonical UUID string") from exc
    if str(parsed) != value:
        raise ValidationError(f"{label} must be a canonical UUID string")
    return value


class ConsoleExecutionRegistry:
    def __init__(self, maximum_active: int = MAX_ACTIVE_EXECUTIONS):
        self._maximum_active = maximum_active
        self._lock = threading.RLock()
        self._entries: dict[str, dict[str, Any]] = {}

    def reserve(self, execution_id: str, console_id: str, profile_id: str, binding: str, server_id: str) -> None:
        with self._lock:
            if execution_id in self._entries:
                raise PostgresServiceError(409, "execution_conflict", "The execution ID is already active")
            if len(self._entries) >= self._maximum_active or any(
                entry["consoleId"] == console_id for entry in self._entries.values()
            ):
                raise PostgresServiceError(429, "execution_busy", "Console execution capacity is busy")
            self._entries[execution_id] = {
                "consoleId": console_id, "profileId": profile_id, "binding": binding,
                "serverId": server_id, "connection": None, "cancelRequested": False,
            }

    def attach(self, execution_id: str, connection: Any) -> bool:
        with self._lock:
            entry = self._entries[execution_id]
            entry["connection"] = connection
            requested = entry["cancelRequested"]
        if requested:
            connection.cancel()
        return requested

    def cancel(self, execution_id: str, profile_id: str, binding: str, server_id: str) -> dict[str, bool]:
        with self._lock:
            entry = self._entries.get(execution_id)
            if entry is None or (entry["profileId"], entry["binding"], entry["serverId"]) != (profile_id, binding, server_id):
                raise PostgresServiceError(404, "execution_not_found", "Console execution was not found")
            entry["cancelRequested"] = True
            connection = entry["connection"]
        if connection is not None:
            try:
                connection.cancel()
            except Exception:
                pass
        return {"requested": True}

    def cancel_requested(self, execution_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(execution_id)
            return bool(entry and entry["cancelRequested"])

    def release(self, execution_id: str) -> None:
        with self._lock:
            self._entries.pop(execution_id, None)

    def close(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            for entry in entries:
                entry["cancelRequested"] = True
        for entry in entries:
            if entry["connection"] is not None:
                try:
                    entry["connection"].cancel()
                except Exception:
                    pass


class PostgresConsole:
    def __init__(self, service: Any):
        self.service = service
        self.registry = ConsoleExecutionRegistry()

    @staticmethod
    def _encoded_size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def _command(cursor: Any) -> str:
        status = getattr(cursor, "statusmessage", "")
        match = re.match(r"^[A-Z]+(?: [A-Z]+)?", status if isinstance(status, str) else "")
        return match.group(0)[:64] if match else "UNKNOWN"

    @staticmethod
    def _notice_collector(connection: Any) -> tuple[list[str], Any]:
        notices: list[str] = []

        def collect(diagnostic: Any) -> None:
            primary = getattr(diagnostic, "message_primary", None)
            text = primary if isinstance(primary, str) else str(diagnostic)
            text = " ".join(text.split())
            if text:
                notices.append(text)

        add_handler = getattr(connection, "add_notice_handler", None)
        if add_handler:
            add_handler(collect)
        return notices, collect

    @staticmethod
    def _take_notices(pending: list[str], remaining_count: int, remaining_bytes: int) -> tuple[list[str], int, int]:
        collected = []
        for notice in pending:
            if remaining_count <= 0 or remaining_bytes <= 0:
                break
            encoded = notice.encode("utf-8")
            if len(encoded) > remaining_bytes:
                notice = encoded[:remaining_bytes].decode("utf-8", errors="ignore")
                encoded = notice.encode("utf-8")
            if not notice:
                break
            collected.append(notice)
            remaining_count -= 1
            remaining_bytes -= len(encoded)
        pending.clear()
        return collected, remaining_count, remaining_bytes

    @staticmethod
    def _error(exc: Exception, statement_index: int, cancelled: bool) -> PostgresServiceError:
        if cancelled:
            return PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled", {"statementIndex": statement_index})
        sqlstate = getattr(exc, "sqlstate", None)
        primary = getattr(getattr(exc, "diag", None), "message_primary", None)
        details = {"statementIndex": statement_index}
        if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
            details["sqlstate"] = sqlstate
        if sqlstate == "57014":
            return PostgresServiceError(422, "sql_timeout", "Console statement timed out", details)
        if sqlstate == "25001":
            return PostgresServiceError(
                422,
                "unsupported_in_transaction",
                "This command cannot run in the server-owned transaction",
                details,
            )
        message = "Console SQL statement failed"
        if isinstance(primary, str):
            safe_primary = " ".join(primary.split())[:500]
            if safe_primary:
                message += f": {safe_primary}"
        return PostgresServiceError(422, "sql_query_failed", message, details)

    def execute(self, profile_id: str, payload: Any, binding: str, server_id: str, policy: ConsolePolicy) -> dict[str, Any]:
        required = {"executionId", "consoleId", "database", "namespace", "sql", "mode", "writeGrantId"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValidationError("Console execution request fields are invalid")
        execution_id = _canonical_uuid(payload["executionId"], "executionId")
        console_id = _canonical_uuid(payload["consoleId"], "consoleId")
        if payload["mode"] != "read" or payload["writeGrantId"] is not None:
            raise ValidationError("Only read mode with a null writeGrantId is supported")
        statements = split_console_statements(payload["sql"])
        database = self.service._validate_database(payload["database"])
        namespace = self.service._validate_namespace(payload["namespace"])
        profile_id = self.service._validate_profile_id(profile_id)
        profile = self.service._profile(profile_id)
        if profile["dbname"] != database:
            raise PostgresServiceError(409, "database_changed", "The saved profile database does not match the requested database")

        self.registry.reserve(execution_id, console_id, profile_id, binding, server_id)
        connection = None
        cursor = None
        notice_handler = None
        statement_index = 0
        try:
            connection = self.service._connect_profile(profile)
            if self.registry.attach(execution_id, connection):
                raise PostgresServiceError(409, "execution_cancelled", "Console execution was cancelled")
            pending_notices, notice_handler = self._notice_collector(connection)
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(f"SET LOCAL statement_timeout = '{policy.statement_timeout_ms}ms'")
            cursor.execute("SELECT current_database() AS database")
            rows = cursor.fetchall()
            current_database = rows[0]["database"] if rows and isinstance(rows[0], dict) else rows[0][0]
            if current_database != database:
                raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s) AS exists", (namespace,))
            rows = cursor.fetchall()
            exists = rows[0]["exists"] if rows and isinstance(rows[0], dict) else rows[0][0]
            if not exists:
                raise NotFoundError("Namespace was not found")
            cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (f"pg_catalog, {quote_identifier(namespace)}",))

            limits = {
                "maxStatements": MAX_STATEMENTS, "maxRowsPerResult": MAX_ROWS_PER_RESULT,
                "maxColumnsPerResult": MAX_COLUMNS_PER_RESULT, "maxResponseBytes": MAX_RESPONSE_BYTES,
                "statementTimeoutMs": policy.statement_timeout_ms,
            }
            result = {
                "executionId": execution_id,
                "target": {"profileId": profile_id, "database": database, "namespace": namespace},
                "mode": "read", "committed": False, "statements": [], "limits": limits,
            }
            remaining_notice_count = MAX_NOTICES
            remaining_notice_bytes = MAX_NOTICE_BYTES
            for statement_index, statement in enumerate(statements):
                cursor.execute(f"SET LOCAL statement_timeout = '{policy.statement_timeout_ms}ms'")
                cursor.execute("SELECT pg_catalog.set_config('search_path', %s, true)", (f"pg_catalog, {quote_identifier(namespace)}",))
                pending_notices.clear()
                cursor.execute(statement)
                notices, remaining_notice_count, remaining_notice_bytes = self._take_notices(
                    pending_notices, remaining_notice_count, remaining_notice_bytes,
                )
                description = cursor.description
                if description is None:
                    row_count = getattr(cursor, "rowcount", -1)
                    entry = {
                        "index": statement_index, "command": self._command(cursor), "columns": [], "rows": [],
                        "rowCount": row_count if isinstance(row_count, int) and row_count >= 0 else 0,
                        "truncated": False, "notices": notices,
                    }
                else:
                    names = [item.name if hasattr(item, "name") else item[0] for item in description]
                    if len(names) > MAX_COLUMNS_PER_RESULT:
                        raise PostgresServiceError(422, "sql_result_too_wide", f"SQL result exceeds the {MAX_COLUMNS_PER_RESULT}-column limit", {"statementIndex": statement_index})
                    fetchmany = getattr(cursor, "fetchmany", None)
                    raw_rows = fetchmany(MAX_ROWS_PER_RESULT + 1) if fetchmany else cursor.fetchall()[:MAX_ROWS_PER_RESULT + 1]
                    entry = {
                        "index": statement_index, "command": self._command(cursor),
                        "columns": [{"name": name} for name in names], "rows": [], "rowCount": 0,
                        "truncated": len(raw_rows) > MAX_ROWS_PER_RESULT, "notices": notices,
                    }
                    for row in raw_rows[:MAX_ROWS_PER_RESULT]:
                        values = [row.get(name) for name in names] if isinstance(row, dict) else list(row)
                        candidate = [self.service._json_cell(value) for value in values]
                        entry["rows"].append(candidate)
                        entry["rowCount"] = len(entry["rows"])
                        if self._encoded_size({**result, "statements": [*result["statements"], entry]}) > MAX_RESPONSE_BYTES:
                            entry["rows"].pop()
                            entry["rowCount"] -= 1
                            entry["truncated"] = True
                            break
                result["statements"].append(entry)
                if self._encoded_size(result) > MAX_RESPONSE_BYTES:
                    raise PostgresServiceError(422, "sql_result_too_large", "Console result metadata exceeds the byte limit", {"statementIndex": statement_index})
            return result
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise self._error(exc, statement_index, self.registry.cancel_requested(execution_id)) from exc
        finally:
            if connection is not None and notice_handler is not None:
                remove_handler = getattr(connection, "remove_notice_handler", None)
                if remove_handler:
                    try:
                        remove_handler(notice_handler)
                    except Exception:
                        pass
            if cursor is not None:
                close = getattr(cursor, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        pass
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
                try:
                    self.service._close(connection)
                finally:
                    self.registry.release(execution_id)
            else:
                self.registry.release(execution_id)

    def cancel(self, profile_id: str, execution_id: Any, binding: str, server_id: str) -> dict[str, bool]:
        profile_id = self.service._validate_profile_id(profile_id)
        execution_id = _canonical_uuid(execution_id, "executionId")
        return self.registry.cancel(execution_id, profile_id, binding, server_id)

    def close(self) -> None:
        self.registry.close()
