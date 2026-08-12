from __future__ import annotations

import re
from typing import Any

from .postgres_common import NotFoundError, PostgresServiceError, ValidationError, canonical_fingerprint


FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_RELATION_DEFINITION_BYTES = 64 * 1024
MAX_RELATION_LINEAGE_ITEMS = 500


class PostgresCatalogMixin:
    def list_relations(self, profile_id: str, database: str, namespace: str) -> dict[str, Any]:
        database = self._validate_database(database)
        namespace = self._validate_namespace(namespace)
        connection = self._connect(profile_id)
        try:
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
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
            try:
                connection.rollback()
            except Exception:
                pass
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
            self._execute_statement(connection, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return self._inspect_relation_connection(
                connection, profile_id, database, namespace, relation, expected_kind, expected_fingerprint
            )
        except PostgresServiceError:
            raise
        except Exception as exc:
            raise PostgresServiceError(502, "introspection_failed", "PostgreSQL relation metadata could not be read") from exc
        finally:
            try:
                connection.rollback()
            except Exception:
                pass
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
        connection_row = self._execute_rows(connection, """
            SELECT current_database() AS database,
                   current_setting('server_version_num')::integer AS server_version_num
        """)[0]
        current = connection_row["database"]
        if current != database:
            raise PostgresServiceError(409, "database_changed", "The connected PostgreSQL database does not match the requested database")
        supports_maintain = int(connection_row.get("server_version_num") or 0) >= 170000
        maintain_permission = (
            "pg_catalog.has_table_privilege(c.oid, 'MAINTAIN')"
            if supports_maintain
            else "pg_catalog.pg_has_role(c.relowner, 'USAGE')"
        )
        relation_rows = self._execute_rows(connection, f"""
                SELECT c.oid AS live_oid,
                       c.relkind AS catalog_kind,
                       CASE WHEN c.relkind IN ('r', 'p') THEN 'table'
                            WHEN c.relkind = 'v' THEN 'view'
                            ELSE 'materialized_view' END AS relation_kind,
                       CASE WHEN c.relkind IN ('v', 'm') THEN pg_catalog.pg_get_viewdef(c.oid, true) END AS view_definition,
                       pg_catalog.pg_get_userbyid(c.relowner) AS owner_name,
                       pg_catalog.pg_has_role(c.relowner, 'USAGE') AS can_alter,
                       pg_catalog.has_table_privilege(c.oid, 'SELECT') AS can_select,
                       {maintain_permission} AS can_refresh,
                       c.relispopulated AS materialized_populated
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
                 FROM pg_catalog.pg_attribute a
                 JOIN pg_catalog.pg_type attribute_type ON attribute_type.oid = a.atttypid
                 LEFT JOIN pg_catalog.pg_type base_type ON base_type.oid = attribute_type.typbasetype
                 WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                 ORDER BY a.attnum
        """, (relation_row["live_oid"],))
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
        view_definition = relation_row.get("view_definition")
        if descriptor["kind"] not in {"view", "materialized_view"}:
            descriptor["definition"] = {"status": "unavailable", "reason": "not_supported"}
        elif not isinstance(view_definition, str) or not view_definition:
            descriptor["definition"] = {"status": "unavailable", "reason": "not_permitted"}
        elif len(view_definition.encode("utf-8")) > MAX_RELATION_DEFINITION_BYTES:
            descriptor["definition"] = {"status": "unavailable", "reason": "too_large"}
        else:
            descriptor["definition"] = {"status": "available", "format": "query", "sql": view_definition}
        owner_name = relation_row.get("owner_name")
        descriptor["owner"] = (
            {"status": "available", "name": owner_name}
            if isinstance(owner_name, str) and owner_name
            else {"status": "unavailable", "reason": "not_permitted"}
        )
        can_alter = bool(relation_row.get("can_alter"))
        descriptor["permissions"] = {
            "canSelect": bool(relation_row.get("can_select")),
            "canAlter": can_alter,
            "canRefresh": descriptor["kind"] == "materialized_view" and bool(
                relation_row.get("can_refresh", can_alter if not supports_maintain else False)
            ),
        }
        descriptor["columnProvenance"] = {"status": "unavailable", "reason": "not_supported"}
        if descriptor["kind"] == "materialized_view":
            index_rows = self._execute_rows(connection, """
                /* concurrent_refresh_index */
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_catalog.pg_index i
                    WHERE i.indrelid = %s
                      AND i.indisunique
                      AND i.indisvalid
                      AND i.indisready
                      AND i.indimmediate
                      AND i.indpred IS NULL
                      AND i.indexprs IS NULL
                ) AS has_refresh_index
            """, (relation_row.get("live_oid"),))
            populated = bool(relation_row.get("materialized_populated"))
            descriptor["materialized"] = {
                "status": "available",
                "populated": populated,
                "concurrentRefreshEligible": populated and bool(index_rows and index_rows[0]["has_refresh_index"]),
            }
        else:
            descriptor["materialized"] = {"status": "unavailable", "reason": "not_applicable"}
        if descriptor["kind"] in {"view", "materialized_view"}:
            descriptor["dependencies"] = self._relation_lineage(
                connection, current, relation_row.get("live_oid"), dependents=False
            )
            descriptor["dependents"] = self._relation_lineage(
                connection, current, relation_row.get("live_oid"), dependents=True
            )
        else:
            descriptor["dependencies"] = {"status": "unavailable", "reason": "not_applicable"}
            descriptor["dependents"] = {"status": "unavailable", "reason": "not_applicable"}
        return descriptor

    def _relation_lineage(
        self, connection: Any, database: str, live_oid: int | None, *, dependents: bool
    ) -> dict[str, Any]:
        if dependents:
            identity_join = "c.oid = rw.ev_class"
            object_filter = "d.refobjid = %s"
        else:
            identity_join = "c.oid = d.refobjid"
            object_filter = "rw.ev_class = %s"
        direction = "dependents" if dependents else "dependencies"
        rows = self._execute_rows(connection, f"""
            /* relation_{direction} */
            SELECT DISTINCT c.oid AS live_oid,
                   n.nspname AS namespace,
                   c.relname AS relation_name,
                   CASE WHEN c.relkind IN ('r', 'p') THEN 'table'
                        WHEN c.relkind = 'v' THEN 'view'
                        WHEN c.relkind = 'm' THEN 'materialized_view'
                        ELSE 'foreign_table' END AS relation_kind
            FROM pg_catalog.pg_rewrite rw
            JOIN pg_catalog.pg_depend d
              ON d.classid = 'pg_catalog.pg_rewrite'::pg_catalog.regclass
             AND d.objid = rw.oid
             AND d.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass
             AND d.deptype = 'n'
            JOIN pg_catalog.pg_class c ON {identity_join}
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE {object_filter}
              AND c.oid <> %s
              AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
            ORDER BY n.nspname, c.relname, relation_kind, c.oid
            LIMIT %s
        """, (live_oid, live_oid, MAX_RELATION_LINEAGE_ITEMS + 1))
        unique = {}
        for row in rows:
            key = (row["namespace"], row["relation_name"], row["relation_kind"], int(row["live_oid"]))
            unique[key] = {
                "database": database,
                "namespace": row["namespace"],
                "relation": row["relation_name"],
                "kind": row["relation_kind"],
                "liveOid": int(row["live_oid"]),
            }
        ordered = [unique[key] for key in sorted(unique)]
        return {
            "status": "available",
            "items": ordered[:MAX_RELATION_LINEAGE_ITEMS],
            "truncated": len(ordered) > MAX_RELATION_LINEAGE_ITEMS,
        }
