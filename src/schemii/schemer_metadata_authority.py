from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .ai_metadata_authority import SchemiiMetadataAuthority
from .metadata import MetadataStore, MetadataStoreError


CAPABILITIES = ("metadata", "dashboard", "data")


class SchemerMetadataAuthority(SchemiiMetadataAuthority):
    """Schemer chat and execution authority backed exclusively by metadata PostgreSQL."""

    def __init__(self, store: MetadataStore, *, worker_id: str):
        super().__init__(store, worker_id=worker_id)

    def provision_chat(self, dashboard_id: str) -> dict[str, Any]:
        return self.store.provision_chat("schemer", "dashboard", dashboard_id)

    def bind_external_session(self, chat_id: str, external_session_id: str, title: str) -> dict[str, Any]:
        try:
            return self.store.bind_chat_external_session(chat_id, external_session_id, title)
        except MetadataStoreError as error:
            if error.code != "metadata_commit_uncertain":
                raise
            return self.store.bind_chat_external_session(chat_id, external_session_id, title)

    def activate_chat(self, chat_id: str, target: dict[str, Any], access_level: str) -> dict[str, Any]:
        enabled = self._access_capabilities(access_level)
        policy = {
            "version": 1,
            "accessLevel": access_level,
            "capabilities": sorted(enabled),
            "approvals": {name: "every_action" for name in CAPABILITIES},
        }
        modes = {name: "approval" if name in enabled else "deny" for name in CAPABILITIES}
        metadata_target = self._metadata_target(target) if target else None
        try:
            self.store.activate_chat(chat_id, metadata_target, policy=policy, capabilities=modes)
        except MetadataStoreError as error:
            if error.code != "metadata_commit_uncertain":
                raise
            self.store.activate_chat(chat_id, metadata_target, policy=policy, capabilities=modes)
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        chat = self.store.get_chat(chat_id)
        if chat["state"] != "active":
            raise MetadataStoreError("chat_inactive", "AI chat is not active", status=409)
        current = self.store.get_current_policy(chat_id)
        policy = current["policy"]
        target = chat["target"]
        return {
            "id": chat["chatId"],
            "dashboardId": chat["resourceId"],
            "externalSessionId": chat["externalSessionId"],
            "title": chat["displayTitle"],
            "accessLevel": policy["accessLevel"],
            "target": {} if target is None else {
                "profileId": target["profileId"],
                "database": target["databaseName"],
                "namespace": target["namespaceName"],
                "profileFingerprint": target["profileFingerprint"],
            },
            "capabilities": list(policy["capabilities"]),
            "policyRevision": current["revision"],
        }

    def list_chats(self, dashboard_id: str | None = None) -> list[dict[str, Any]]:
        records = self.store.list_chats(
            resource_kind="dashboard", resource_id=dashboard_id, states=["active"],
        )
        return [self.get_chat(item["chatId"]) for item in records]

    @staticmethod
    def policy_binding(chat: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        action_type = action.get("type")
        capability = "data" if action_type == "read_query" else "metadata" if action_type in {"dashboard_create", "dashboard_open"} else "dashboard"
        if capability not in chat["capabilities"]:
            raise MetadataStoreError("capability_disabled", "AI action is not enabled for this chat", status=403)
        return {
            "capability": capability,
            "configuredMode": "every_action",
            "effectiveMode": "every_action",
            "policyRevision": chat["policyRevision"],
            "origin": "model",
        }

    @staticmethod
    def _access_capabilities(access_level: Any) -> set[str]:
        if access_level == "metadata":
            return {"metadata"}
        if access_level == "dashboard":
            return {"metadata", "dashboard"}
        if access_level == "data":
            return set(CAPABILITIES)
        raise MetadataStoreError("invalid_metadata", "AI access level is invalid", status=400)


def retire_legacy_schemer_authority(config_dir: Path) -> list[str]:
    """Archive Schemer JSON authority without importing authority or title bindings."""
    retired = config_dir / "retired-json-authority"
    retired.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(retired, 0o700)
    source = config_dir / "ai_authority" / "v1" / "schemer"
    destination = retired / "ai-authority-v1-schemer"
    moved = []
    if source.exists() and not destination.exists():
        try:
            os.replace(source, destination)
            moved.append("ai-authority-v1-schemer")
        except FileNotFoundError:
            pass
    marker = retired / "SCHEMER.txt"
    if not marker.exists():
        marker.write_text(
            "Legacy Schemer JSON authority and SCHEMER_CONTEXT title bindings were retired without import. "
            "They are inert and must never authorize a request.\n",
            encoding="ascii",
        )
        os.chmod(marker, 0o600)
    return moved
