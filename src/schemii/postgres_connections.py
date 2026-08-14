from __future__ import annotations

import hashlib
import json
from typing import Any

from .postgres_common import PostgresServiceError
from .postgres_concurrency import postgres_execution
from .result_limits import ResultLimitError


MAX_CATALOG_ROWS = 5000


def _json_cell(value: Any) -> Any:
    # Kept as a compatibility hook for callers; the service-owned limiter is authoritative.
    from .result_limits import ResultLimiter
    return ResultLimiter().cell(value)


class PostgresConnectionMixin:
    def _connect(self, profile_id: str):
        profile = self._profile(profile_id)
        observed = {**profile, "id": profile_id}
        try:
            connection = self._connect_profile(profile)
        except PostgresServiceError:
            self._record_target_connection(observed, False)
            raise
        self._record_target_connection(observed, True)
        return connection

    def _connect_profile(self, profile: dict[str, Any]):
        kwargs = {
            "host": profile["host"], "port": profile["port"], "dbname": profile["dbname"],
            "user": profile["user"], "password": profile["password"], "sslmode": profile["sslmode"],
            "connect_timeout": profile["timeout"], "application_name": self._application_name,
        }
        try:
            if self._connect_factory is not None:
                connection = self._connect_factory(**kwargs)
            else:
                import psycopg
                from psycopg.rows import dict_row
                connection = psycopg.connect(**kwargs, row_factory=dict_row)
        except Exception as exc:
            self._record_target_connection(profile, False)
            raise PostgresServiceError(502, "connection_failed", "PostgreSQL connection failed") from exc
        self._record_target_connection(profile, True)
        return connection

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
            fetchmany = getattr(cursor, "fetchmany", None)
            rows = fetchmany(MAX_CATALOG_ROWS + 1) if fetchmany else cursor.fetchall()
            if len(rows) > MAX_CATALOG_ROWS:
                raise PostgresServiceError(
                    422, "catalog_result_too_large", "PostgreSQL catalog result exceeds the item limit",
                    {"policy": "reject", "path": "$", "limit": MAX_CATALOG_ROWS, "actual": len(rows)},
                )
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

    def _bounded_cell(self, value: Any, *, path: str, events: list[dict[str, Any]]) -> Any:
        try:
            return self._result_limiter.cell(value, path=path, events=events)
        except ResultLimitError as exc:
            raise PostgresServiceError(422, exc.code, exc.message, exc.details) from exc

    @postgres_execution("catalog")
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
