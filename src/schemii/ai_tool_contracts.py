from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .schemii_ai_actions import normalize_schemii_action, schemii_action_approval_floor, schemii_action_capability
from .schemer_ai_actions import normalize_schemer_action


ActionNormalizer = Callable[[Any, str], dict[str, Any]]
ActionClassifier = Callable[[Any], str | None]


@dataclass(frozen=True)
class AiToolContract:
    name: str
    schema: str
    action_type: str
    normalizer: ActionNormalizer
    capability: str | None
    approval_floor: ActionClassifier
    executor_adapter: str
    supported_app: str


def _schemii(name: str, action_type: str, capability: str | None, adapter: str | None = None, floor: str | None = None) -> AiToolContract:
    return AiToolContract(name, f"ai/workspace/.opencode/tools/{name}.ts", action_type, normalize_schemii_action, capability, lambda action: schemii_action_approval_floor(action) or floor, adapter or action_type, "schemii")


def _schemer(name: str, action_type: str, capability: str | None, adapter: str | None = None, floor: str | None = None) -> AiToolContract:
    return AiToolContract(name, f"ai/schemer-workspace/.opencode/tools/{name}.ts", action_type, normalize_schemer_action, capability, lambda _action: floor, adapter or action_type, "schemer")


SCHEMII_TOOL_CONTRACTS: Mapping[str, AiToolContract] = MappingProxyType({item.name: item for item in (
    _schemii("schema_read_query", "schema_read_query", "rawread", floor="every_action"),
    _schemii("schema_data_read", "data_read", "structured"),
    _schemii("schema_raw_write", "raw_write", "rawwrite", floor="every_action"),
    _schemii("schema_connection_setup", "connection_setup", None, floor="every_action"),
    _schemii("schema_project_open", "open_project", None, floor="every_action"),
    _schemii("schema_project_create", "create_project", "schema"),
    _schemii("schema_populate", "populate_schema", "schema"),
    _schemii("schema_add_table", "add_table", "schema"),
    _schemii("schema_rename_table", "rename_table", "schema"),
    _schemii("schema_add_column", "add_column", "schema"),
    _schemii("schema_update_column", "update_column", "schema"),
    _schemii("schema_delete_element", "delete_element", "schema", floor="every_action"),
    _schemii("schema_add_relationship", "add_relationship", "schema"),
    _schemii("schema_connection_open", "open_connection", None, floor="every_action"),
    _schemii("schema_migration_preview", "migration_preview", "schema"),
    _schemii("schema_insert_rows_preview", "insert_rows_preview", "write"),
    _schemii("schema_create_view_preview", "create_view_preview", "write"),
)})

SCHEMER_TOOL_CONTRACTS: Mapping[str, AiToolContract] = MappingProxyType({item.name: item for item in (
    _schemer("schemer_read_query", "read_query", "data"),
    _schemer("schemer_dashboard_open", "dashboard_open", None),
    _schemer("schemer_dashboard_create", "dashboard_create", "metadata"),
    _schemer("schemer_widget_create", "widget_create", "dashboard"),
    _schemer("schemer_widget_rename", "widget_rename", "dashboard"),
    _schemer("schemer_widget_duplicate", "widget_duplicate", "dashboard"),
    _schemer("schemer_widget_delete", "widget_delete", "dashboard", floor="every_action"),
)})

AI_TOOL_CONTRACTS: Mapping[str, Mapping[str, AiToolContract]] = MappingProxyType({
    "schemii": SCHEMII_TOOL_CONTRACTS,
    "schemer": SCHEMER_TOOL_CONTRACTS,
})


def contract_for_action(application: str, action: Any) -> AiToolContract | None:
    action_type = action.get("type") if isinstance(action, dict) else None
    return next((item for item in AI_TOOL_CONTRACTS[application].values() if item.action_type == action_type), None)


def effective_schemii_contract(action: Any) -> tuple[str | None, str | None]:
    """Classify model and server-issued Schemii actions through one policy contract."""
    contract = contract_for_action("schemii", action)
    return (
        contract.capability if contract is not None else schemii_action_capability(action),
        contract.approval_floor(action) if contract is not None else schemii_action_approval_floor(action),
    )
