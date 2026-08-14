from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time as datetime_time
from decimal import Decimal
from typing import Any
from uuid import UUID

from .postgres_common import PostgresServiceError


def _json_cell(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, datetime_time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return "\\x" + value.hex()
    if isinstance(value, dict):
        return {str(key): _json_cell(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_cell(item) for item in value]
    return str(value)


class PostgresConnectionMixin:
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

    def _require_namespace(self, connection: Any, namespace: str) -> None:
        rows = self._execute_rows(connection, """
            SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s
            ) AS namespace_exists
        """, (namespace,))
        if not rows or not rows[0].get("namespace_exists"):
            raise PostgresServiceError(404, "namespace_not_found", f"PostgreSQL namespace {namespace} was not found")

    _json_cell = staticmethod(_json_cell)

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
