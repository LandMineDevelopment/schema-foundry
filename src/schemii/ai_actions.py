from __future__ import annotations

import copy
import re
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,63}$")


def normalize_schemii_action(action: Any, access: str) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    action_type = action.get("type") or action.get("action")
    if action_type == "schema_read_query":
        field = "action" if "action" in action else "type"
        approval = "requiresApproval" if "requiresApproval" in action else "requiresConfirmation"
        _exact(action, {field, "profileId", "namespace", "sql", "purpose", "readOnly", approval})
        if access != "data" or action.get("readOnly") is not True or action.get(approval) is not True:
            raise ValueError("query action is not authorized")
        return {
            "type": action_type, "profileId": _id(action.get("profileId")),
            "namespace": _name(action.get("namespace")), "sql": _sql(action.get("sql")),
            "purpose": _text(action.get("purpose"), 500), "readOnly": True, "requiresConfirmation": True,
        }
    if action_type == "open_project":
        _exact(action, {"type", "schemaId", "projectName", "requiresConfirmation"})
        if action.get("requiresConfirmation") is not True:
            raise ValueError("confirmation is required")
        return {"type": action_type, "schemaId": _id(action.get("schemaId")), "projectName": _text(action.get("projectName"), 256), "requiresConfirmation": True}
    if action_type == "connection_setup":
        _exact(action, {"type", "name", "host", "port", "database", "user", "sslmode", "requiresPasswordEntry", "requiresConfirmation"})
        if action.get("requiresPasswordEntry") is not True or action.get("requiresConfirmation") is not True:
            raise ValueError("connection confirmation is required")
        port = action.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port is invalid")
        sslmode = action.get("sslmode")
        if sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError("sslmode is invalid")
        return {
            "type": action_type, "name": _text(action.get("name"), 100), "host": _text(action.get("host"), 253),
            "port": port, "database": _name(action.get("database")), "user": _text(action.get("user"), 63), "sslmode": sslmode,
            "requiresPasswordEntry": True, "requiresConfirmation": True,
        }
    raise ValueError("unsupported action")


def normalize_schemer_action(action: Any, access: str) -> dict[str, Any]:
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    action_type = action.get("type")
    if action_type == "read_query":
        fields = {"type", "dashboardId", "expectedRevision", "profileId", "database", "namespace", "sql", "purpose", "readOnly", "requiresConfirmation"}
        _exact(action, fields)
        if access != "data" or action.get("readOnly") is not True or action.get("requiresConfirmation") is not True:
            raise ValueError("query action is not authorized")
        return {
            "type": action_type, "dashboardId": _id(action.get("dashboardId")),
            "expectedRevision": _revision(action.get("expectedRevision")), "profileId": _id(action.get("profileId")),
            "database": _name(action.get("database")), "namespace": _name(action.get("namespace")),
            "sql": _sql(action.get("sql")), "purpose": _text(action.get("purpose"), 500),
            "readOnly": True, "requiresConfirmation": True,
        }
    if action_type == "dashboard_open":
        _exact(action, {"type", "dashboardId", "expectedRevision", "title", "requiresConfirmation"})
        if action.get("requiresConfirmation") is not True:
            raise ValueError("confirmation is required")
        return {
            "type": action_type, "dashboardId": _id(action.get("dashboardId")),
            "expectedRevision": _revision(action.get("expectedRevision")), "title": _text(action.get("title"), 128),
            "requiresConfirmation": True,
        }
    raise ValueError("unsupported action")


def _exact(value: dict[str, Any], fields: set[str]) -> None:
    if set(value) != fields:
        raise ValueError("action fields are invalid")


def _id(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError("identifier is invalid")
    return value


def _name(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or not NAME.fullmatch(value) or len(value.encode("utf-8")) > 63:
        raise ValueError("PostgreSQL name is invalid")
    return value


def _text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value.encode("utf-8")) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("text is invalid")
    return copy.deepcopy(value)


def _sql(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value.encode("utf-8")) > 10_000 or "\x00" in value:
        raise ValueError("SQL is invalid")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ValueError("SQL is invalid")
    return value


def _revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("revision is invalid")
    return value
