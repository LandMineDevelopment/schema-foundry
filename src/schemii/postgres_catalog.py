from __future__ import annotations

import re
from typing import Any

from .postgres_common import NotFoundError, PostgresServiceError, ValidationError, canonical_fingerprint


FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class PostgresCatalogMixin:
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
            return self._inspect_relation_connection(
                connection, profile_id, database, namespace, relation, expected_kind, expected_fingerprint
            )
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relation metadata could not be read") from exc
        finally:
            self._close(connection)

    def _inspect_relation_connection(
        self,
        connection: Any,
        profile_id: str,
        database: str,
        namespace: str,
        relation: str,
        expected_kind: str | None,
        expected_fingerprint: str | None,
    ) -> dict[str, Any]:
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
