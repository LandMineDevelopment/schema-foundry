from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_json import write_json


SCHEMA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
LAYOUT_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SchemaStoreError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"code": code, "message": message, **details}}


def schema_layout_token(record: dict[str, Any]) -> str:
    layout = record.get("schema", {}).get("layout", {}) if isinstance(record, dict) else {}
    encoded = json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def postgres_view_id(namespace: str, relation: str) -> str:
    encoded = json.dumps((namespace, relation), ensure_ascii=True, separators=(",", ":"))
    return f"pg_view_{hashlib.sha256(encoded.encode()).hexdigest()[:20]}"


def is_wholesale_layout_change(existing_record: dict[str, Any], incoming_record: dict[str, Any]) -> bool:
    def layers(record: dict[str, Any]) -> dict[str, tuple[dict[str, Any], Any]]:
        layout = record.get("schema", {}).get("layout", {})
        if not isinstance(layout, dict):
            return {}
        configured = layout.get("layers")
        if isinstance(configured, dict):
            result = {}
            for name in ("tables", "views"):
                layer = configured.get(name, {})
                if isinstance(layer, dict):
                    objects = layer.get("objects", {})
                    result[name] = (objects if isinstance(objects, dict) else {}, layer.get("viewport"))
            return result
        tables = layout.get("tables", {})
        return {"tables": (tables if isinstance(tables, dict) else {}, layout.get("view"))}

    existing_layers = layers(existing_record)
    incoming_layers = layers(incoming_record)
    if any(
        existing_layers[name][1] != incoming_layers.get(name, ({}, None))[1]
        for name in existing_layers
        if existing_layers[name][1] is not None
    ):
        return True
    visual_fields = ("x", "y", "color")
    established = 0
    changed = 0
    for name, (existing, _) in existing_layers.items():
        incoming = incoming_layers.get(name, ({}, None))[0]
        established += len(existing)
        layer_changed = 0
        for object_id, current in existing.items():
            candidate = incoming.get(object_id)
            if not isinstance(current, dict) or not isinstance(candidate, dict):
                layer_changed += 1
            elif any(current.get(field) != candidate.get(field) for field in visual_fields):
                layer_changed += 1
        changed += layer_changed
    return established >= 8 and changed >= max(8, (established + 1) // 2)


class SchemaStore:
    def __init__(self, schema_dir: str | os.PathLike[str]):
        self.schema_dir = Path(schema_dir).expanduser()
        self._lock = threading.RLock()
        self._schema_locks: dict[str, threading.RLock] = {}
        self.schema_dir.mkdir(parents=True, exist_ok=True)

    def _schema_lock(self, schema_id: str) -> threading.RLock:
        with self._lock:
            return self._schema_locks.setdefault(schema_id, threading.RLock())

    @staticmethod
    def validate_id(schema_id: Any) -> str:
        if not isinstance(schema_id, str) or not SCHEMA_ID_PATTERN.fullmatch(schema_id):
            raise SchemaStoreError(404, "not_found", "Unknown schema path")
        return schema_id

    @staticmethod
    def _validate_record(record: Any, schema_id: str | None = None) -> dict[str, Any]:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            raise SchemaStoreError(400, "invalid_schema", "Invalid schema record")
        if schema_id is not None and record["id"] != schema_id:
            raise SchemaStoreError(400, "invalid_schema", "Invalid schema record")
        schema = record.get("schema")
        if not (
            isinstance(schema, dict)
            and isinstance(schema.get("projectName"), str)
            and isinstance(schema.get("tables"), list)
            and isinstance(schema.get("relationships"), list)
            and ("functions" not in schema or isinstance(schema["functions"], list))
        ):
            raise SchemaStoreError(400, "invalid_schema", "Invalid schema record")
        return record

    def _records(self) -> list[tuple[Path, dict[str, Any]]]:
        records = []
        self.schema_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.schema_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                records.append((path, self._validate_record(record)))
            except (OSError, json.JSONDecodeError, SchemaStoreError):
                continue
        return records

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{**record, "layoutToken": schema_layout_token(record)} for _, record in self._records()]

    def get(self, schema_id: str) -> dict[str, Any]:
        schema_id = self.validate_id(schema_id)
        with self._schema_lock(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            return json.loads(json.dumps(found[1]))

    def _find(self, schema_id: str) -> tuple[Path, dict[str, Any]] | None:
        for path, record in self._records():
            if record["id"] == schema_id:
                return path, record
        return None

    @staticmethod
    def _require_view_binding(
        record: dict[str, Any], expected_revision: Any, layout_token: Any,
        profile_id: str, database: str, namespace: str,
    ) -> None:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedSchemaRevision is invalid")
        if not isinstance(layout_token, str) or not LAYOUT_TOKEN_PATTERN.fullmatch(layout_token):
            raise SchemaStoreError(400, "invalid_schema_binding", "layoutToken is invalid")
        revision = record.get("revision", 0)
        if revision != expected_revision:
            raise SchemaStoreError(409, "schema_conflict", "Schema changed in another session; reload before continuing", currentRevision=revision)
        if schema_layout_token(record) != layout_token:
            raise SchemaStoreError(409, "layout_conflict", "Saved layout changed; hard-refresh before continuing")
        postgres = record.get("schema", {}).get("postgres")
        expected = (profile_id, database, namespace)
        actual = (
            postgres.get("sourceProfileId"), postgres.get("database"), postgres.get("namespace")
        ) if isinstance(postgres, dict) else (None, None, None)
        if actual != expected:
            raise SchemaStoreError(409, "schema_target_changed", "Saved schema is not bound to the requested PostgreSQL target")

    def require_view_mutation_binding(
        self, schema_id: str, expected_revision: Any, layout_token: Any,
        profile_id: str, database: str, namespace: str, relation: str,
        operation: str, expectation: dict[str, Any], saved_view_id: str | None = None,
    ) -> dict[str, Any]:
        if operation not in {"upsert", "delete"}:
            raise SchemaStoreError(400, "invalid_schema_binding", "View operation is invalid")
        schema_id = self.validate_id(schema_id)
        with self._schema_lock(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            record = found[1]
            self._require_view_binding(record, expected_revision, layout_token, profile_id, database, namespace)
            matches = [
                item for item in record["schema"].get("views", [])
                if isinstance(item, dict) and item.get("namespace") == namespace and item.get("name") == relation
            ]
            expected_absent = isinstance(expectation, dict) and expectation == {"absent": True}
            if operation == "delete" and expected_absent:
                raise SchemaStoreError(400, "invalid_schema_binding", "Delete requires an existing saved view")
            if expected_absent:
                if matches:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view collides with the expected new view")
                matched_id = None
            else:
                if len(matches) != 1:
                    message = "Saved schema contains ambiguous matching view items" if len(matches) > 1 else "Saved schema view changed after editing began"
                    raise SchemaStoreError(409, "schema_view_changed", message)
                matched_id = matches[0].get("id")
                if not isinstance(matched_id, str) or not matched_id:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view has no stable identity")
                if saved_view_id is not None and matched_id != saved_view_id:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view identity changed after preview")
                expected_kind = expectation.get("kind") if isinstance(expectation, dict) else None
                if expected_kind in {"view", "materialized_view"} and bool(matches[0].get("materialized")) != (expected_kind == "materialized_view"):
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view kind changed after editing began")
            return {"record": json.loads(json.dumps(record)), "savedViewId": matched_id}

    @contextmanager
    def reserve_view_mutation_binding(
        self, schema_id: str, expected_revision: Any, layout_token: Any,
        profile_id: str, database: str, namespace: str, relation: str,
        operation: str, expectation: dict[str, Any], saved_view_id: str | None,
    ):
        """Reserve one schema from binding validation through narrow sync."""
        schema_id = self.validate_id(schema_id)
        with self._schema_lock(schema_id):
            self.require_view_mutation_binding(
                schema_id, expected_revision, layout_token,
                profile_id, database, namespace, relation, operation, expectation, saved_view_id,
            )
            yield

    def sync_view_after_mutation(
        self, schema_id: str, expected_revision: int, layout_token: str,
        profile_id: str, database: str, namespace: str, relation: str,
        kind: str | None, definition: str | None, query_definition: str | None, fingerprint: str | None,
        *, operation: str, expected_absent: bool, saved_view_id: str | None,
    ) -> dict[str, Any]:
        if operation not in {"upsert", "delete"} or not isinstance(expected_absent, bool):
            raise SchemaStoreError(400, "invalid_schema_binding", "expectedAbsent is invalid")
        schema_id = self.validate_id(schema_id)
        with self._schema_lock(schema_id):
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            path, record = found
            self._require_view_binding(record, expected_revision, layout_token, profile_id, database, namespace)
            views = record["schema"].get("views", [])
            indexes = [
                index for index, item in enumerate(views)
                if isinstance(item, dict) and item.get("namespace") == namespace and item.get("name") == relation
            ]
            if expected_absent and indexes:
                message = "Saved schema contains ambiguous matching view items" if len(indexes) > 1 else "Saved schema view collides with the newly created view"
                raise SchemaStoreError(409, "schema_view_changed", message)
            if not expected_absent and len(indexes) != 1:
                raise SchemaStoreError(409, "schema_view_changed", "Saved schema view changed after preview")
            stored_id = views[indexes[0]].get("id") if indexes else None
            if not expected_absent and stored_id:
                if stored_id != saved_view_id:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view identity changed after preview")
            stored = json.loads(json.dumps(record))
            if operation == "delete":
                if expected_absent or len(indexes) != 1 or not saved_view_id:
                    raise SchemaStoreError(409, "schema_view_changed", "Saved schema view changed after preview")
                del stored["schema"]["views"][indexes[0]]
            elif expected_absent:
                item = {"id": postgres_view_id(namespace, relation)}
                stored["schema"].setdefault("views", []).append(item)
            else:
                item = stored["schema"]["views"][indexes[0]]
            if operation == "upsert":
                item.update({
                    "name": relation,
                    "namespace": namespace,
                    "materialized": kind == "materialized_view",
                    "definition": definition,
                    "queryDefinition": query_definition,
                    "fingerprint": fingerprint,
                })
            stored["revision"] = expected_revision + 1
            stored["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            try:
                write_json(path, stored)
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema file could not be saved") from exc
            return {
                "status": "saved", "revision": stored["revision"],
                "updatedAt": stored["updatedAt"], "layoutToken": schema_layout_token(stored),
            }

    def save(
        self,
        schema_id: str,
        record: Any,
        *,
        expected_layout_token: str | None,
        layout_protocol: str | None,
    ) -> dict[str, Any]:
        schema_id = self.validate_id(schema_id)
        record = self._validate_record(record, schema_id)
        with self._schema_lock(schema_id):
            found = self._find(schema_id)
            current_revision = 0
            existing_path = None
            if found:
                existing_path, existing_record = found
                current_revision = existing_record.get("revision", 0)
                if record.get("revision", 0) != current_revision:
                    raise SchemaStoreError(
                        409,
                        "schema_conflict",
                        "Schema changed in another session; reload before saving",
                        currentRevision=current_revision,
                    )
                layout_changed = schema_layout_token(record) != schema_layout_token(existing_record)
                if layout_changed and (
                    layout_protocol != "2"
                    or (
                        expected_layout_token != schema_layout_token(existing_record)
                        and is_wholesale_layout_change(existing_record, record)
                    )
                ):
                    raise SchemaStoreError(
                        409,
                        "layout_conflict",
                        "A stale client attempted to change the saved layout; hard-refresh before saving",
                    )

            stored = dict(record)
            stored.pop("layoutToken", None)
            stored["revision"] = current_revision + 1
            stored["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            destination = self.schema_dir / f"{schema_id}.json"
            try:
                write_json(destination, stored)
                if existing_path and existing_path != destination:
                    existing_path.unlink()
            except OSError as exc:
                raise SchemaStoreError(500, "schema_store_error", "Schema file could not be saved") from exc
            return {
                "saved": schema_id,
                "revision": stored["revision"],
                "updatedAt": stored["updatedAt"],
                "layoutToken": schema_layout_token(stored),
            }

    def delete(self, schema_id: str) -> dict[str, str]:
        schema_id = self.validate_id(schema_id)
        with self._schema_lock(schema_id):
            found = self._find(schema_id)
            if found:
                try:
                    found[0].unlink()
                except OSError as exc:
                    raise SchemaStoreError(500, "schema_store_error", "Schema file could not be deleted") from exc
        return {"deleted": schema_id}
