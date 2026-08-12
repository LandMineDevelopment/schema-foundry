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
    if action_type == "create_project":
        _exact(action, {"type", "projectName", "requiresConfirmation"})
        _confirmation(action)
        return {"type": action_type, "projectName": _text(action.get("projectName"), 256), "requiresConfirmation": True}
    if action_type == "populate_schema":
        _exact(action, {"type", "purpose", "tables", "relationships", "requiresConfirmation"})
        _confirmation(action)
        tables = _list(action.get("tables"), 1, 20, lambda item: _table_definition(item, require_purpose=True))
        relationships = _list(action.get("relationships"), 0, 50, _relationship_definition)
        _unique_names(tables, "table")
        return {"type": action_type, "purpose": _text(action.get("purpose"), 500), "tables": tables, "relationships": relationships, "requiresConfirmation": True}
    if action_type == "add_table":
        _exact_optional(action, {"type", "name", "purpose", "columns", "requiresConfirmation"}, {"profileId", "namespace"})
        _confirmation(action)
        result = {"type": action_type, **_table_definition({key: action[key] for key in ("name", "purpose", "columns")}, require_purpose=True), "requiresConfirmation": True}
        return _optional_target(action, result)
    if action_type == "rename_table":
        _exact_optional(action, {"type", "tableId", "newName", "requiresConfirmation"}, {"profileId", "namespace"})
        _confirmation(action)
        return _optional_target(action, {"type": action_type, "tableId": _id(action.get("tableId")), "newName": _name(action.get("newName")), "requiresConfirmation": True})
    if action_type == "add_column":
        _exact_optional(action, {"type", "tableId", "name", "type", "nullable", "requiresConfirmation"}, {"profileId", "namespace", "default"})
        _confirmation(action)
        if not isinstance(action.get("nullable"), bool):
            raise ValueError("nullable is invalid")
        result = {"type": action_type, "tableId": _id(action.get("tableId")), "name": _name(action.get("name")), "type": _column_type(action.get("type")), "nullable": action["nullable"], "requiresConfirmation": True}
        if "default" in action:
            result["default"] = _default(action["default"], nullable=False)
        return _optional_target(action, result)
    if action_type == "update_column":
        _exact_optional(action, {"type", "tableId", "columnId", "changes", "requiresConfirmation"}, {"profileId", "namespace"})
        _confirmation(action)
        changes = action.get("changes")
        if not isinstance(changes, dict) or not changes or set(changes) - {"name", "type", "nullable", "default"}:
            raise ValueError("column changes are invalid")
        normalized = {}
        if "name" in changes: normalized["name"] = _name(changes["name"])
        if "type" in changes: normalized["type"] = _column_type(changes["type"])
        if "nullable" in changes:
            if not isinstance(changes["nullable"], bool): raise ValueError("nullable is invalid")
            normalized["nullable"] = changes["nullable"]
        if "default" in changes: normalized["default"] = _default(changes["default"], nullable=True)
        return _optional_target(action, {"type": action_type, "tableId": _id(action.get("tableId")), "columnId": _id(action.get("columnId")), "changes": normalized, "requiresConfirmation": True})
    if action_type == "delete_element":
        _exact_optional(action, {"type", "elementType", "tableId", "reason", "destructive", "requiresConfirmation"}, {"profileId", "namespace", "columnId", "impact"})
        _confirmation(action)
        if action.get("destructive") is not True or action.get("elementType") not in {"table", "column"}:
            raise ValueError("destructive element type is invalid")
        if (action["elementType"] == "column") != ("columnId" in action):
            raise ValueError("column identity is invalid")
        result = {"type": action_type, "elementType": action["elementType"], "tableId": _id(action.get("tableId")), "reason": _text(action.get("reason"), 500), "destructive": True, "requiresConfirmation": True}
        if "columnId" in action: result["columnId"] = _id(action["columnId"])
        if "impact" in action:
            if not isinstance(action["impact"], list): raise ValueError("impact is invalid")
            result["impact"] = copy.deepcopy(action["impact"])
        return _optional_target(action, result)
    if action_type == "add_relationship":
        required = {"type", "fromTableName", "fromColumnName", "toTableName", "toColumnName", "onDelete", "onUpdate", "requiresConfirmation"}
        optional = {"profileId", "namespace", "fromTableId", "fromColumnId", "toTableId", "toColumnId", "constraintName"}
        _exact_optional(action, required, optional)
        _confirmation(action)
        return _optional_target(action, {"type": action_type, **_relationship_definition({key: value for key, value in action.items() if key not in {"type", "profileId", "namespace", "requiresConfirmation"}}), "requiresConfirmation": True})
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


def _exact_optional(value: dict[str, Any], required: set[str], optional: set[str]) -> None:
    if not required <= set(value) or set(value) - required - optional:
        raise ValueError("action fields are invalid")


def _confirmation(action: dict[str, Any]) -> None:
    if action.get("requiresConfirmation") is not True:
        raise ValueError("confirmation is required")


def _optional_target(action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if ("profileId" in action) != ("namespace" in action):
        raise ValueError("optional target is incomplete")
    if "profileId" in action:
        result.update({"profileId": _id(action["profileId"]), "namespace": _name(action["namespace"])})
    return result


def _list(value: Any, minimum: int, maximum: int, normalize) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("list size is invalid")
    return [normalize(item) for item in value]


def _table_definition(value: Any, *, require_purpose: bool) -> dict[str, Any]:
    fields = {"name", "columns"} | ({"purpose"} if require_purpose else set())
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("table definition is invalid")
    columns = _list(value.get("columns"), 1, 50, _column_definition)
    _unique_names(columns, "column")
    result = {"name": _name(value.get("name")), "columns": columns}
    if require_purpose: result["purpose"] = _text(value.get("purpose"), 500)
    return result


def _column_definition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not {"name", "type"} <= set(value) or set(value) - {"name", "type", "primary", "nullable", "unique", "default"}:
        raise ValueError("column definition is invalid")
    result = {"name": _name(value.get("name")), "type": _column_type(value.get("type"))}
    for field in ("primary", "nullable", "unique"):
        if field in value:
            if not isinstance(value[field], bool): raise ValueError(f"{field} is invalid")
            result[field] = value[field]
    if "default" in value: result["default"] = _default(value["default"], nullable=False)
    return result


def _relationship_definition(value: Any) -> dict[str, Any]:
    required = {"fromTableName", "fromColumnName", "toTableName", "toColumnName", "onDelete", "onUpdate"}
    optional = {"fromTableId", "fromColumnId", "toTableId", "toColumnId", "constraintName"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError("relationship definition is invalid")
    result = {key: _name(value[key]) for key in ("fromTableName", "fromColumnName", "toTableName", "toColumnName")}
    for key in ("fromTableId", "fromColumnId", "toTableId", "toColumnId"):
        if key in value: result[key] = _id(value[key])
    if "constraintName" in value: result["constraintName"] = _name(value["constraintName"])
    for key in ("onDelete", "onUpdate"):
        if value[key] not in {"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"}: raise ValueError("relationship action is invalid")
        result[key] = value[key]
    return result


def _unique_names(items: list[dict[str, Any]], kind: str) -> None:
    names = [item["name"].casefold() for item in items]
    if len(names) != len(set(names)): raise ValueError(f"{kind} names are duplicated")


def _column_type(value: Any) -> str:
    return _text(value, 128)


def _default(value: Any, *, nullable: bool) -> str | None:
    if nullable and value is None: return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > 1000 or "\x00" in value: raise ValueError("column default is invalid")
    return value


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
