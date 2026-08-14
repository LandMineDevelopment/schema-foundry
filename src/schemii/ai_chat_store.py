from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .atomic_json import remove_file, write_json
from .file_lock import exclusive_file_lock


CHAT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CAPABILITIES = frozenset({"schema", "structured", "write", "rawread", "rawwrite"})
APPROVAL_MODES = frozenset({"every_action", "once_per_chat", "automatic"})


class AiChatStoreError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = {"error": {"code": code, "message": message}}

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)


class AiChatStore:
    """Application-owned chat identity, fixed connection, and mutable user policy."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.lock_path = self.root / ".lock"
        self._lock = threading.RLock()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def create(self, chat_id: str, schema_id: str, target: dict[str, Any], capabilities: Any, approvals: Any) -> dict[str, Any]:
        chat_id = self._id(chat_id, "chatId")
        record = {
            "version": 2, "id": chat_id, "schemaId": self._id(schema_id, "schemaId"),
            "target": self._target(target), "capabilities": self._capabilities(capabilities),
            "approvals": self._approvals(approvals), "policyRevision": 1, "grants": {},
        }
        with self._guard():
            if self._path(chat_id).exists():
                raise AiChatStoreError(409, "chat_exists", "AI chat identity already exists")
            write_json(self._path(chat_id), record, mode=0o600, sort_keys=True)
        return copy.deepcopy(record)

    def get(self, chat_id: str) -> dict[str, Any]:
        with self._guard():
            return self._read(chat_id)

    def update_policy(self, chat_id: str, capabilities: Any, approvals: Any, expected_revision: Any) -> dict[str, Any]:
        with self._guard():
            record = self._read(chat_id)
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
                raise AiChatStoreError(400, "validation_error", "AI policy revision is invalid")
            if record["policyRevision"] != expected_revision:
                raise AiChatStoreError(409, "chat_policy_changed", "AI chat policy changed; reload before updating it")
            next_capabilities = self._capabilities(capabilities)
            next_approvals = self._approvals(approvals)
            if (record["capabilities"], record["approvals"]) != (next_capabilities, next_approvals):
                if not record["target"] and any(item != "schema" for item in next_capabilities):
                    raise AiChatStoreError(409, "chat_target_required", "Start a new target-bound chat to enable data capabilities")
                record["capabilities"] = next_capabilities
                record["approvals"] = next_approvals
                record["policyRevision"] += 1
                record["grants"] = {}
                write_json(self._path(chat_id), record, mode=0o600, sort_keys=True)
            return copy.deepcopy(record)

    def delete(self, chat_id: str) -> None:
        with self._guard():
            remove_file(self._path(self._id(chat_id, "chatId")))

    def list(self, schema_id: str, target: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        schema_id = self._id(schema_id, "schemaId")
        target = self._target(target) if target is not None else None
        with self._guard():
            records = []
            for path in self.root.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if record.get("schemaId") == schema_id and (target is None or record.get("target") == target):
                    records.append(copy.deepcopy(record))
            return records

    def require_capability(self, chat_id: str, capability: str) -> dict[str, Any]:
        record = self.get(chat_id)
        if capability not in record["capabilities"]:
            raise AiChatStoreError(403, "capability_disabled", f"AI capability {capability} is disabled for this chat")
        return record

    def authorize(self, chat_id: str, capability: str | None, policy_revision: Any, effective_mode: str, confirmation: Any, begin):
        if effective_mode not in APPROVAL_MODES or not callable(begin):
            raise AiChatStoreError(400, "validation_error", "AI approval request is invalid")
        with self._guard():
            record = self._read(chat_id)
            if policy_revision != record["policyRevision"]:
                raise AiChatStoreError(409, "chat_policy_changed", "AI chat policy changed; request a fresh proposal")
            configured = "every_action" if capability is None else record["approvals"].get(capability)
            if capability is not None and capability not in record["capabilities"]:
                raise AiChatStoreError(403, "capability_disabled", f"AI capability {capability} is disabled for this chat")
            granted = capability is not None and capability in record["grants"]
            source = "explicit"
            grant_created = False
            if effective_mode == "every_action":
                self._require_confirmation(confirmation, {"every_action", "explicit"})
            elif configured == "once_per_chat" and effective_mode == "once_per_chat":
                if granted:
                    source = "chat_grant"
                else:
                    self._require_confirmation(confirmation, {"once_per_chat", "explicit"})
                    grant_created = True
            elif configured == "automatic" and effective_mode == "automatic":
                if confirmation is not None:
                    raise AiChatStoreError(400, "validation_error", "Automatic approval is server-owned")
                source = "automatic"
            else:
                raise AiChatStoreError(409, "chat_policy_changed", "Proposal approval policy no longer matches this chat")
            operation = begin()
            if grant_created:
                record["grants"][capability] = {"policyRevision": record["policyRevision"], "grantedAtMs": round(time.time() * 1000)}
                write_json(self._path(chat_id), record, mode=0o600, sort_keys=True)
            return operation, {
                "capability": capability, "configuredMode": configured, "effectiveMode": effective_mode,
                "source": source, "grantCreated": grant_created, "policyRevision": record["policyRevision"],
            }

    def _read(self, chat_id: str) -> dict[str, Any]:
        path = self._path(self._id(chat_id, "chatId"))
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AiChatStoreError(404, "chat_not_found", "AI chat was not found") from exc
        except (OSError, ValueError) as exc:
            raise AiChatStoreError(500, "chat_store_error", "AI chat record could not be read") from exc
        if record.get("version") == 1 and isinstance(record, dict):
            record.update({"version": 2, "grants": {}})
            write_json(path, record, mode=0o600, sort_keys=True)
        required = {"version", "id", "schemaId", "target", "capabilities", "approvals", "policyRevision", "grants"}
        try:
            valid = (
                isinstance(record, dict) and set(record) == required and record.get("version") == 2
                and record.get("id") == chat_id and self._id(record.get("schemaId"), "schemaId")
                and self._target(record.get("target")) == record["target"]
                and self._capabilities(record.get("capabilities")) == record["capabilities"]
                and self._approvals(record.get("approvals")) == record["approvals"]
                and isinstance(record.get("policyRevision"), int) and not isinstance(record["policyRevision"], bool) and record["policyRevision"] >= 1
                and isinstance(record.get("grants"), dict)
                and all(capability in record["capabilities"] and record["approvals"][capability] == "once_per_chat" and set(grant) == {"policyRevision", "grantedAtMs"} and grant["policyRevision"] == record["policyRevision"] and isinstance(grant["grantedAtMs"], int) for capability, grant in record["grants"].items())
            )
        except (AiChatStoreError, TypeError):
            valid = False
        if not valid:
            raise AiChatStoreError(500, "chat_store_error", "AI chat record is invalid")
        return record

    def _path(self, chat_id: str) -> Path:
        return self.root / f"{chat_id}.json"

    def _guard(self):
        class Guard:
            def __init__(inner, outer): inner.outer = outer; inner.file = None
            def __enter__(inner):
                inner.outer._lock.acquire(); inner.file = exclusive_file_lock(inner.outer.lock_path); inner.file.__enter__()
            def __exit__(inner, *args):
                try: inner.file.__exit__(*args)
                finally: inner.outer._lock.release()
        return Guard(self)

    @staticmethod
    def _id(value: Any, label: str) -> str:
        if not isinstance(value, str) or not CHAT_ID.fullmatch(value):
            raise AiChatStoreError(400, "validation_error", f"{label} is invalid")
        return value

    @staticmethod
    def _target(value: Any) -> dict[str, Any]:
        if value == {}:
            return {}
        fields = {"profileId", "database", "namespace", "profileFingerprint"}
        if not isinstance(value, dict) or set(value) != fields or any(not isinstance(value[key], str) or not value[key] for key in fields):
            raise AiChatStoreError(400, "validation_error", "AI chat target is invalid")
        return copy.deepcopy(value)

    @staticmethod
    def _capabilities(value: Any) -> list[str]:
        if not isinstance(value, list) or len(value) != len(set(value)) or any(item not in CAPABILITIES for item in value):
            raise AiChatStoreError(400, "validation_error", "AI capabilities are invalid")
        return sorted(value)

    @staticmethod
    def _approvals(value: Any) -> dict[str, str]:
        if not isinstance(value, dict) or set(value) != CAPABILITIES or any(mode not in APPROVAL_MODES for mode in value.values()):
            raise AiChatStoreError(400, "validation_error", "AI approval settings are invalid")
        return dict(value)

    @staticmethod
    def _require_confirmation(value: Any, modes: set[str]) -> None:
        if not isinstance(value, dict) or set(value) != {"accepted", "mode"} or value.get("accepted") is not True or value.get("mode") not in modes:
            raise AiChatStoreError(400, "approval_required", "This AI action requires approval")
