from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .atomic_json import write_json


SCHEMA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class SchemaStoreError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"code": code, "message": message, **details}}


def schema_layout_token(record: dict[str, Any]) -> str:
    layout = record.get("schema", {}).get("layout", {}) if isinstance(record, dict) else {}
    encoded = json.dumps(layout, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_wholesale_layout_change(existing_record: dict[str, Any], incoming_record: dict[str, Any]) -> bool:
    existing = existing_record.get("schema", {}).get("layout", {}).get("tables", {})
    incoming = incoming_record.get("schema", {}).get("layout", {}).get("tables", {})
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return False
    shared = set(existing) & set(incoming)
    if len(shared) < 8:
        return False
    visual_fields = ("x", "y", "color")
    changed = sum(
        any(existing[table_id].get(field) != incoming[table_id].get(field) for field in visual_fields)
        for table_id in shared
        if isinstance(existing[table_id], dict) and isinstance(incoming[table_id], dict)
    )
    return changed >= max(8, (len(shared) + 1) // 2)


class SchemaStore:
    def __init__(self, schema_dir: str | os.PathLike[str]):
        self.schema_dir = Path(schema_dir).expanduser()
        self._lock = threading.RLock()
        self.schema_dir.mkdir(parents=True, exist_ok=True)

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
        with self._lock:
            found = self._find(schema_id)
            if found is None:
                raise SchemaStoreError(404, "not_found", "Schema was not found")
            return json.loads(json.dumps(found[1]))

    def _find(self, schema_id: str) -> tuple[Path, dict[str, Any]] | None:
        for path, record in self._records():
            if record["id"] == schema_id:
                return path, record
        return None

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
        with self._lock:
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
        with self._lock:
            found = self._find(schema_id)
            if found:
                try:
                    found[0].unlink()
                except OSError as exc:
                    raise SchemaStoreError(500, "schema_store_error", "Schema file could not be deleted") from exc
        return {"deleted": schema_id}
