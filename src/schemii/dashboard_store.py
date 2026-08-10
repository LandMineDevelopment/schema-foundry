from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .widget_query import QueryValidationError, normalize_query


DASHBOARD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DASHBOARD_VERSION = 1
MAX_WIDGETS = 100
TABLE_PAGE_SIZES = {10, 25, 50, 100}
VISUALIZATION_MODES = {"table", "kpi", "bar", "line", "donut"}


class DashboardStoreError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"code": code, "message": message, **details}}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        raise DashboardStoreError(400, "invalid_dashboard", f"{field} must be a trimmed string up to {maximum} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DashboardStoreError(400, "invalid_dashboard", f"{field} contains invalid characters")
    return value


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise DashboardStoreError(400, "invalid_dashboard", f"{field} must be from {minimum} to {maximum}")
    return value


def _layout(value: Any, widget_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"desktop", "mobile"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} layout is invalid")
    desktop = value["desktop"]
    mobile = value["mobile"]
    if not isinstance(desktop, dict) or set(desktop) != {"x", "y", "w", "h"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} desktop layout is invalid")
    if not isinstance(mobile, dict) or set(mobile) != {"order", "h"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} mobile layout is invalid")
    normalized_desktop = {
        "x": _integer(desktop["x"], "desktop x", 0, 11),
        "y": _integer(desktop["y"], "desktop y", 0, 999),
        "w": _integer(desktop["w"], "desktop width", 1, 12),
        "h": _integer(desktop["h"], "desktop height", 1, 50),
    }
    if normalized_desktop["x"] + normalized_desktop["w"] > 12:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} extends past the dashboard grid")
    return {
        "desktop": normalized_desktop,
        "mobile": {
            "order": _integer(mobile["order"], "mobile order", 0, 999),
            "h": _integer(mobile["h"], "mobile height", 1, 50),
        },
    }


def _postgres_identifier(value: Any, field: str) -> str:
    if (
        not isinstance(value, str) or not value or len(value.encode("utf-8")) > 63
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise DashboardStoreError(400, "invalid_dashboard", f"{field} must be a valid PostgreSQL identifier up to 63 bytes")
    return value


def _table_configuration(value: Any, query: dict[str, Any], widget_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"version", "columns", "pageSize"} or isinstance(value.get("version"), bool) or value.get("version") != 1:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table configuration is invalid")
    page_size = value.get("pageSize")
    if isinstance(page_size, bool) or page_size not in TABLE_PAGE_SIZES:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table page size is invalid")
    targets = {item["id"]: "dimension" for item in query["dimensions"]} | {item["id"]: "measure" for item in query["measures"]}
    columns = value.get("columns")
    if not isinstance(columns, list) or len(columns) != len(targets):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table columns must cover every query result field")
    normalized_columns = []
    seen = set()
    measure_seen = False
    for column in columns:
        if not isinstance(column, dict) or set(column) != {"targetId", "width", "hidden", "pinned", "label"}:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table column is invalid")
        target_id = column.get("targetId")
        if not isinstance(target_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(target_id):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table target is invalid or duplicated")
        target_kind = targets.get(target_id)
        if target_kind is None or target_id in seen:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table target is invalid or duplicated")
        if target_kind == "dimension" and measure_seen:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table dimensions must precede measures")
        measure_seen = measure_seen or target_kind == "measure"
        if not isinstance(column.get("hidden"), bool) or not isinstance(column.get("pinned"), bool):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table column behavior is invalid")
        seen.add(target_id)
        normalized_columns.append({
            "targetId": target_id,
            "width": _integer(column.get("width"), "aggregate table column width", 64, 1024),
            "hidden": column["hidden"],
            "pinned": column["pinned"],
            "label": _bounded_text(column.get("label"), "aggregate table column label", 128),
        })
    if seen != set(targets):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate table columns must cover every query result field")
    return {"version": 1, "columns": normalized_columns, "pageSize": page_size}


def _visualization_configuration(value: Any, query: dict[str, Any], widget_id: str) -> dict[str, Any]:
    required = {"version", "mode", "selections"}
    if not isinstance(value, dict) or set(value) != required or isinstance(value.get("version"), bool) or value.get("version") != 1:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} visualization configuration is invalid")
    mode = value.get("mode")
    selections = value.get("selections")
    if not isinstance(mode, str) or mode not in VISUALIZATION_MODES or not isinstance(selections, dict) or set(selections) != {"kpi", "bar", "line", "donut"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} visualization mode or selections are invalid")
    dimension_ids = {item["id"] for item in query["dimensions"]}
    measure_ids = {item["id"] for item in query["measures"]}

    def dimension_id(selection: dict[str, Any], kind: str) -> str | None:
        selected = selection.get("dimensionId")
        if selected is not None and (not isinstance(selected, str) or selected not in dimension_ids):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} {kind} dimension is not in its query")
        return selected

    def selected_measures(selection: dict[str, Any], kind: str) -> list[str]:
        selected = selection.get("measureIds")
        if not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected) or len(selected) != len(set(selected)) or any(item not in measure_ids for item in selected):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} {kind} measures are invalid")
        return selected

    kpi = selections["kpi"]
    if not isinstance(kpi, dict) or set(kpi) != {"measureIds"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} KPI selection is invalid")
    bar = selections["bar"]
    line = selections["line"]
    if not isinstance(bar, dict) or set(bar) != {"dimensionId", "measureIds"} or not isinstance(line, dict) or set(line) != {"dimensionId", "measureIds"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} chart selection is invalid")
    donut = selections["donut"]
    if not isinstance(donut, dict) or set(donut) != {"dimensionId", "measureId"} or not isinstance(donut.get("measureId"), str) or donut.get("measureId") not in measure_ids:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} donut selection is invalid")
    return {
        "version": 1,
        "mode": mode,
        "selections": {
            "kpi": {"measureIds": selected_measures(kpi, "KPI")},
            "bar": {"dimensionId": dimension_id(bar, "bar"), "measureIds": selected_measures(bar, "bar")},
            "line": {"dimensionId": dimension_id(line, "line"), "measureIds": selected_measures(line, "line")},
            "donut": {"dimensionId": dimension_id(donut, "donut"), "measureId": donut["measureId"]},
        },
    }


def _widget_configuration(value: Any, widget_id: str, widget_kind: str) -> dict[str, Any]:
    allowed = (set(), {"source"}, {"source", "query"}, {"source", "query", "table"}, {"source", "query", "visualization"}, {"source", "query", "table", "visualization"})
    if not isinstance(value, dict) or set(value) not in allowed:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} configuration must contain at most one source")
    if widget_kind == "aggregate_report" and set(value) not in ({"source", "query"}, {"source", "query", "table"}, {"source", "query", "visualization"}, {"source", "query", "table", "visualization"}):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} aggregate report requires a source and query")
    if widget_kind != "aggregate_report" and ({"table", "visualization"} & set(value)):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} presentation requires an aggregate report")
    if not value:
        return {}
    source = value["source"]
    identity_fields = {"profileId", "database", "namespace", "relation", "kind", "fingerprint"}
    if not isinstance(source, dict) or set(source) not in (identity_fields, identity_fields | {"columns"}):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source identity is invalid")
    profile_id = source.get("profileId")
    if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source profile is invalid")
    kind = source.get("kind")
    if kind not in {"table", "view", "materialized_view"}:
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source kind is invalid")
    fingerprint = source.get("fingerprint")
    if not isinstance(fingerprint, str) or not FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source fingerprint is invalid")
    normalized_source = {
        "profileId": profile_id,
        "database": _postgres_identifier(source.get("database"), "source database"),
        "namespace": _postgres_identifier(source.get("namespace"), "source namespace"),
        "relation": _postgres_identifier(source.get("relation"), "source relation"),
        "kind": kind,
        "fingerprint": fingerprint,
    }
    if "columns" in source:
        columns = source["columns"]
        if not isinstance(columns, list) or len(columns) > 1600:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source columns are invalid")
        normalized_columns = []
        names = set()
        ordinals = set()
        for column in columns:
            if not isinstance(column, dict) or set(column) != {"name", "type", "nullable", "ordinal"}:
                raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source column is invalid")
            name = _postgres_identifier(column.get("name"), "source column")
            ordinal = _integer(column.get("ordinal"), "source column ordinal", 1, 1600)
            if name in names or ordinal in ordinals or not isinstance(column.get("nullable"), bool):
                raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source columns are duplicated or invalid")
            names.add(name)
            ordinals.add(ordinal)
            normalized_columns.append({
                "name": name,
                "type": _bounded_text(column.get("type"), "source column type", 512),
                "nullable": column["nullable"],
                "ordinal": ordinal,
            })
        if [column["ordinal"] for column in normalized_columns] != sorted(ordinals):
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} source columns must use ordinal order")
        normalized_source["columns"] = normalized_columns
    normalized = {"source": normalized_source}
    if "query" in value:
        if "columns" not in normalized_source:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} query requires a source column snapshot")
        try:
            normalized["query"] = normalize_query(value["query"], normalized_source["columns"])
        except QueryValidationError as exc:
            raise DashboardStoreError(400, "invalid_dashboard", f"Widget {widget_id} query is invalid: {exc}") from exc
    if "table" in value:
        normalized["table"] = _table_configuration(value["table"], normalized["query"], widget_id)
    if "visualization" in value:
        normalized["visualization"] = _visualization_configuration(value["visualization"], normalized["query"], widget_id)
    return normalized


def validate_dashboard_record(record: Any, dashboard_id: str | None = None) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard record must be an object")
    allowed_record = {"id", "version", "revision", "updatedAt", "dashboard"}
    if set(record) - allowed_record:
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard record contains unknown fields")
    record_id = record.get("id")
    if not isinstance(record_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(record_id) or (dashboard_id and record_id != dashboard_id):
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard ID is invalid")
    if record.get("version") != DASHBOARD_VERSION:
        raise DashboardStoreError(400, "invalid_dashboard", f"Dashboard version must be {DASHBOARD_VERSION}")
    revision = record.get("revision", 0)
    _integer(revision, "revision", 0, 2_147_483_647)
    dashboard = record.get("dashboard")
    if not isinstance(dashboard, dict):
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard content must be an object")
    allowed_dashboard = {"title", "archived", "widgets", "slicers", "viewport"}
    if set(dashboard) != allowed_dashboard:
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard content fields are invalid")
    title = _bounded_text(dashboard.get("title"), "title", 128)
    if not isinstance(dashboard.get("archived"), bool):
        raise DashboardStoreError(400, "invalid_dashboard", "archived must be true or false")
    widgets = dashboard.get("widgets")
    slicers = dashboard.get("slicers")
    if not isinstance(widgets, list) or len(widgets) > MAX_WIDGETS or not isinstance(slicers, list):
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard widgets or slicers are invalid")
    if slicers:
        raise DashboardStoreError(400, "invalid_dashboard", "Slicers are reserved for a later dashboard version")
    normalized_widgets = []
    widget_ids = set()
    for widget in widgets:
        if not isinstance(widget, dict) or set(widget) != {"id", "kind", "title", "layout", "configuration"}:
            raise DashboardStoreError(400, "invalid_dashboard", "Widget fields are invalid")
        widget_id = widget.get("id")
        if not isinstance(widget_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(widget_id) or widget_id in widget_ids:
            raise DashboardStoreError(400, "invalid_dashboard", "Widget ID is invalid or duplicated")
        widget_ids.add(widget_id)
        kind = _bounded_text(widget.get("kind"), "widget kind", 64)
        if kind not in {"preview", "placeholder", "aggregate_report"}:
            raise DashboardStoreError(400, "invalid_dashboard", "Widget kind is not supported by this dashboard version")
        normalized_widgets.append({
            "id": widget_id,
            "kind": kind,
            "title": _bounded_text(widget.get("title"), "widget title", 128),
            "layout": _layout(widget.get("layout"), widget_id),
            "configuration": _widget_configuration(widget.get("configuration"), widget_id, kind),
        })
    viewport = dashboard.get("viewport")
    if not isinstance(viewport, dict) or set(viewport) != {"desktop", "mobile"}:
        raise DashboardStoreError(400, "invalid_dashboard", "Dashboard viewport is invalid")
    normalized_viewport = {}
    for mode in ("desktop", "mobile"):
        value = viewport[mode]
        if not isinstance(value, dict) or set(value) != {"x", "y"}:
            raise DashboardStoreError(400, "invalid_dashboard", "Dashboard viewport is invalid")
        normalized_viewport[mode] = {
            "x": _integer(value["x"], f"{mode} viewport x", 0, 1_000_000),
            "y": _integer(value["y"], f"{mode} viewport y", 0, 1_000_000),
        }
    return {
        "id": record_id,
        "version": DASHBOARD_VERSION,
        "revision": revision,
        **({"updatedAt": record["updatedAt"]} if isinstance(record.get("updatedAt"), str) else {}),
        "dashboard": {
            "title": title,
            "archived": dashboard["archived"],
            "widgets": normalized_widgets,
            "slicers": [],
            "viewport": normalized_viewport,
        },
    }


def mercury_dashboard_record() -> dict[str, Any]:
    layouts = {
        "widget_revenue": (0, 0, 4, 3, 0),
        "widget_orders": (4, 0, 4, 3, 1),
        "widget_average": (8, 0, 4, 3, 2),
        "widget_trend": (0, 3, 8, 6, 3),
        "widget_status": (8, 3, 4, 6, 4),
        "widget_recent": (0, 9, 12, 5, 5),
    }
    titles = {
        "widget_revenue": "Gross revenue",
        "widget_orders": "Orders",
        "widget_average": "Average order",
        "widget_trend": "Revenue trend",
        "widget_status": "Order status",
        "widget_recent": "Recent orders",
    }
    widgets = []
    for widget_id, (x, y, width, height, order) in layouts.items():
        widgets.append({
            "id": widget_id,
            "kind": "preview",
            "title": titles[widget_id],
            "layout": {
                "desktop": {"x": x, "y": y, "w": width, "h": height},
                "mobile": {"order": order, "h": height},
            },
            "configuration": {},
        })
    return {
        "id": "dashboard_mercury",
        "version": DASHBOARD_VERSION,
        "revision": 0,
        "dashboard": {
            "title": "Mercury overview",
            "archived": False,
            "widgets": widgets,
            "slicers": [],
            "viewport": {"desktop": {"x": 0, "y": 0}, "mobile": {"x": 0, "y": 0}},
        },
    }


class DashboardStore:
    def __init__(self, dashboard_dir: str | os.PathLike[str]):
        self.dashboard_dir = Path(dashboard_dir).expanduser()
        self.marker_path = self.dashboard_dir / ".examples_initialized"
        self._lock = threading.RLock()
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        self.dashboard_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.dashboard_dir, 0o700)
        for path in self.dashboard_dir.glob("*.json"):
            os.chmod(path, 0o600)

    @staticmethod
    def validate_id(dashboard_id: Any) -> str:
        if not isinstance(dashboard_id, str) or not DASHBOARD_ID_PATTERN.fullmatch(dashboard_id):
            raise DashboardStoreError(404, "not_found", "Unknown dashboard path")
        return dashboard_id

    def _path(self, dashboard_id: str) -> Path:
        return self.dashboard_dir / f"{dashboard_id}.json"

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            return validate_dashboard_record(json.loads(path.read_text(encoding="utf-8")), path.stem)
        except DashboardStoreError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise DashboardStoreError(500, "dashboard_store_error", "Dashboard file could not be read") from exc

    def list(self) -> list[dict[str, Any]]:
        records = []
        with self._lock:
            for path in sorted(self.dashboard_dir.glob("*.json")):
                try:
                    records.append(self._read(path))
                except DashboardStoreError:
                    continue
        return sorted(records, key=lambda item: (item["dashboard"]["archived"], item["dashboard"]["title"].lower(), item["id"]))

    def get(self, dashboard_id: str) -> dict[str, Any]:
        dashboard_id = self.validate_id(dashboard_id)
        with self._lock:
            path = self._path(dashboard_id)
            if not path.is_file():
                raise DashboardStoreError(404, "not_found", "Dashboard was not found")
            return self._read(path)

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        destination = self._path(record["id"])
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(prefix=f".{record['id']}.", suffix=".tmp", dir=self.dashboard_dir)
            temporary = Path(name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
        except OSError as exc:
            raise DashboardStoreError(500, "dashboard_store_error", "Dashboard file could not be saved") from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        return json.loads(json.dumps(record))

    def create(self, title: Any, source_id: Any = None) -> dict[str, Any]:
        title = _bounded_text(title, "title", 128)
        with self._lock:
            if source_id is None:
                dashboard = {
                    "title": title,
                    "archived": False,
                    "widgets": [],
                    "slicers": [],
                    "viewport": {"desktop": {"x": 0, "y": 0}, "mobile": {"x": 0, "y": 0}},
                }
            else:
                source = self.get(self.validate_id(source_id))
                dashboard = json.loads(json.dumps(source["dashboard"]))
                dashboard["title"] = title
                dashboard["archived"] = False
            record = validate_dashboard_record({
                "id": "dashboard_" + secrets.token_hex(8),
                "version": DASHBOARD_VERSION,
                "revision": 0,
                "dashboard": dashboard,
            })
            record["revision"] = 1
            record["updatedAt"] = _utc_now()
            return self._write(record)

    def save(self, dashboard_id: str, incoming: Any) -> dict[str, Any]:
        dashboard_id = self.validate_id(dashboard_id)
        record = validate_dashboard_record(incoming, dashboard_id)
        with self._lock:
            current = self.get(dashboard_id)
            if record["revision"] != current["revision"]:
                raise DashboardStoreError(
                    409,
                    "dashboard_conflict",
                    "Dashboard changed in another session; reload before saving",
                    currentRevision=current["revision"],
                )
            record["revision"] = current["revision"] + 1
            record["updatedAt"] = _utc_now()
            return self._write(record)

    def delete(self, dashboard_id: str) -> dict[str, str]:
        dashboard_id = self.validate_id(dashboard_id)
        with self._lock:
            try:
                self._path(dashboard_id).unlink(missing_ok=True)
            except OSError as exc:
                raise DashboardStoreError(500, "dashboard_store_error", "Dashboard file could not be deleted") from exc
        return {"deleted": dashboard_id}

    def initialize_once(self) -> None:
        with self._lock:
            if self.marker_path.exists():
                return
            if not self.list():
                record = mercury_dashboard_record()
                record["revision"] = 1
                record["updatedAt"] = _utc_now()
                self._write(validate_dashboard_record(record))
            try:
                descriptor = os.open(self.marker_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write("1\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(self.marker_path, 0o600)
            except OSError as exc:
                raise DashboardStoreError(500, "dashboard_store_error", "Dashboard example marker could not be saved") from exc
