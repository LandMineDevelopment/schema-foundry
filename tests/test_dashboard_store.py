import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import DashboardStore, DashboardStoreError, mercury_dashboard_record
from schemii.schemer_examples import build_mercury_dashboard


SOURCE = {
    "profileId": "schemii_example_postgres",
    "database": "schemii",
    "namespace": "bookstore",
    "relation": "orders",
    "kind": "table",
    "fingerprint": "a" * 64,
}
SOURCE_COLUMNS = [
    {"name": "id", "type": "bigint", "nullable": False, "ordinal": 1},
    {"name": "ordered_at", "type": "timestamp with time zone", "nullable": False, "ordinal": 2},
]
QUERY = {
    "version": 2,
    "dimensions": [{"id": "dimension_date", "label": "Order date", "column": "ordered_at"}],
    "measures": [{"id": "measure_orders", "label": "Orders", "column": None, "aggregation": "count_rows", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"}}],
    "filters": [],
    "sort": [
        {"targetKind": "measure", "targetId": "measure_orders", "direction": "desc", "nulls": "last"},
        {"targetKind": "dimension", "targetId": "dimension_date", "direction": "asc", "nulls": "last"},
    ],
    "limit": 100,
}
TABLE = {
    "version": 1,
    "columns": [
        {"targetId": "dimension_date", "width": 180, "hidden": False, "pinned": True, "label": "Order date"},
        {"targetId": "measure_orders", "width": 120, "hidden": False, "pinned": False, "label": "Orders"},
    ],
    "pageSize": 25,
}
VISUALIZATION = {
    "version": 1,
    "mode": "bar",
    "selections": {
        "kpi": {"measureIds": ["measure_orders"]},
        "bar": {"dimensionId": "dimension_date", "measureIds": ["measure_orders"]},
        "line": {"dimensionId": "dimension_date", "measureIds": ["measure_orders"]},
        "donut": {"dimensionId": "dimension_date", "measureId": "measure_orders"},
    },
}
DETAIL = {
    "version": 1,
    "columns": [
        {"sourceColumn": "id", "label": "Order ID", "width": 120, "hidden": False, "searchable": False, "numberFormat": {"style": "integer"}},
        {"sourceColumn": "ordered_at", "label": "Ordered at", "width": 240, "hidden": False, "searchable": False, "numberFormat": {"style": "auto"}},
    ],
    "defaultSort": {"sourceColumn": "ordered_at", "direction": "desc", "nulls": "last"},
    "rowIdentifier": "id",
    "pageSize": 25,
}
MERCURY_COLUMNS = [
    {"name": name, "type": column_type, "nullable": nullable, "ordinal": index + 1}
    for index, (name, column_type, nullable) in enumerate([
        ("order_id", "bigint", False), ("customer_id", "bigint", False),
        ("customer_name", "character varying(160)", False), ("status", "character varying(20)", False),
        ("ordered_at", "timestamp with time zone", False), ("shipped_at", "timestamp with time zone", True),
        ("order_date", "date", False), ("item_count", "bigint", True), ("order_total", "numeric(14,2)", True),
    ])
]
MERCURY_DESCRIPTOR = {
    "profileId": "schemii_example_postgres", "database": "schemii", "namespace": "bookstore",
    "relation": "order_summary", "kind": "view", "fingerprint": "b" * 64, "columns": MERCURY_COLUMNS,
}


class DashboardStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "dashboards"
        self.store = DashboardStore(self.root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_example_initializes_once_and_deletion_is_respected(self):
        self.store.initialize_once()
        records = self.store.list()
        self.assertEqual([record["id"] for record in records], ["dashboard_mercury"])
        self.assertEqual(len(records[0]["dashboard"]["widgets"]), 6)
        self.store.delete("dashboard_mercury")
        self.store.initialize_once()
        self.assertEqual(self.store.list(), [])

    def test_live_mercury_template_has_six_executable_widgets(self):
        record = build_mercury_dashboard(MERCURY_DESCRIPTOR)
        widgets = record["dashboard"]["widgets"]
        self.assertEqual([widget["kind"] for widget in widgets], ["aggregate_report"] * 6)
        self.assertEqual({widget["configuration"]["source"]["relation"] for widget in widgets}, {"order_summary"})
        self.assertEqual([widget["configuration"]["visualization"]["mode"] for widget in widgets], ["kpi", "kpi", "kpi", "line", "donut", "table"])
        self.assertEqual(widgets[-1]["configuration"]["query"]["limit"], 10)

    def test_mercury_reset_preserves_layout_viewport_and_custom_widgets(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        current["dashboard"]["widgets"][0]["layout"]["desktop"]["x"] = 1
        current["dashboard"]["viewport"]["desktop"] = {"x": 42, "y": 73}
        current["dashboard"]["widgets"].append({
            "id": "widget_custom", "kind": "placeholder", "title": "Custom",
            "layout": {"desktop": {"x": 0, "y": 20, "w": 4, "h": 3}, "mobile": {"order": 9, "h": 3}},
            "configuration": {},
        })
        current = self.store.save(current["id"], current)
        restored = self.store.restore_mercury(build_mercury_dashboard(MERCURY_DESCRIPTOR), current["revision"])
        self.assertEqual(restored["dashboard"]["widgets"][0]["layout"]["desktop"]["x"], 1)
        self.assertEqual(restored["dashboard"]["viewport"]["desktop"], {"x": 42, "y": 73})
        self.assertEqual(restored["dashboard"]["widgets"][-1]["id"], "widget_custom")
        self.assertTrue(all(widget["kind"] == "aggregate_report" for widget in restored["dashboard"]["widgets"][:6]))
        with self.assertRaises(DashboardStoreError) as error:
            self.store.restore_mercury(build_mercury_dashboard(MERCURY_DESCRIPTOR), current["revision"])
        self.assertEqual(error.exception.payload["error"]["code"], "dashboard_changed")

    def test_mercury_reset_places_missing_widgets_without_layout_collisions(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        current["dashboard"]["widgets"] = [widget for widget in current["dashboard"]["widgets"] if widget["id"] != "widget_revenue"]
        current["dashboard"]["widgets"].append({
            "id": "widget_custom", "kind": "placeholder", "title": "Custom",
            "layout": {"desktop": {"x": 0, "y": 0, "w": 4, "h": 3}, "mobile": {"order": 10, "h": 3}},
            "configuration": {},
        })
        current = self.store.save(current["id"], current)
        restored = self.store.restore_mercury(build_mercury_dashboard(MERCURY_DESCRIPTOR), current["revision"])
        widgets = restored["dashboard"]["widgets"]
        revenue = next(widget for widget in widgets if widget["id"] == "widget_revenue")
        self.assertNotEqual(revenue["layout"]["desktop"], {"x": 0, "y": 0, "w": 4, "h": 3})
        self.assertEqual(revenue["layout"]["mobile"]["order"], 11)
        for index, widget in enumerate(widgets):
            left = widget["layout"]["desktop"]
            for other in widgets[index + 1:]:
                right = other["layout"]["desktop"]
                self.assertTrue(left["x"] + left["w"] <= right["x"] or right["x"] + right["w"] <= left["x"] or left["y"] + left["h"] <= right["y"] or right["y"] + right["h"] <= left["y"])

    def test_legacy_mercury_upgrade_preserves_layout_but_not_configured_widgets(self):
        self.store.initialize_once()
        current = self.store.get("dashboard_mercury")
        current["dashboard"]["widgets"][0]["layout"]["desktop"]["x"] = 1
        configured = current["dashboard"]["widgets"][1]
        configured["kind"] = "aggregate_report"
        configured["configuration"] = build_mercury_dashboard(MERCURY_DESCRIPTOR)["dashboard"]["widgets"][1]["configuration"]
        configured["title"] = "My configured orders"
        current["dashboard"]["widgets"][2]["title"] = "My renamed preview"
        current = self.store.save(current["id"], current)
        upgraded = self.store.upgrade_mercury_example(build_mercury_dashboard(MERCURY_DESCRIPTOR))
        self.assertEqual(upgraded["dashboard"]["widgets"][0]["layout"]["desktop"]["x"], 1)
        self.assertEqual(upgraded["dashboard"]["widgets"][0]["kind"], "aggregate_report")
        self.assertEqual(upgraded["dashboard"]["widgets"][1]["title"], "My configured orders")
        self.assertEqual(upgraded["dashboard"]["widgets"][2]["kind"], "placeholder")
        self.assertEqual(upgraded["dashboard"]["widgets"][2]["title"], "My renamed preview")
        self.assertEqual(self.store.upgrade_mercury_example(build_mercury_dashboard(MERCURY_DESCRIPTOR))["revision"], upgraded["revision"])

    def test_create_duplicate_and_permissions(self):
        self.store.initialize_once()
        created = self.store.create("Operations")
        duplicate = self.store.create("Mercury copy", "dashboard_mercury")
        self.assertEqual(created["dashboard"]["widgets"], [])
        self.assertEqual(len(duplicate["dashboard"]["widgets"]), 6)
        self.assertNotEqual(duplicate["id"], "dashboard_mercury")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((self.root / f"{created['id']}.json").stat().st_mode), 0o600)

    def test_ai_mutations_are_idempotent_and_preserve_unrelated_state(self):
        self.store.initialize_once()
        before = self.store.get("dashboard_mercury")
        viewport = json.loads(json.dumps(before["dashboard"]["viewport"]))
        unrelated = json.loads(json.dumps(before["dashboard"]["widgets"][1:]))
        action = {"type": "widget_rename", "dashboardId": "dashboard_mercury", "expectedRevision": 1, "widgetId": before["dashboard"]["widgets"][0]["id"], "currentTitle": before["dashboard"]["widgets"][0]["title"], "title": "Renamed", "requiresConfirmation": True}
        first = self.store.apply_ai_mutation("dashboard_mercury", "operation_one", 1, action)
        duplicate = DashboardStore(self.root).apply_ai_mutation("dashboard_mercury", "operation_one", 1, action)
        current = self.store.get("dashboard_mercury")
        self.assertEqual(first, duplicate)
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["dashboard"]["viewport"], viewport)
        self.assertEqual(current["dashboard"]["widgets"][1:], unrelated)

    def test_ai_duplicate_uses_deterministic_id_and_nonoverlapping_layout(self):
        self.store.initialize_once(); current = self.store.get("dashboard_mercury"); source = current["dashboard"]["widgets"][0]
        action = {"type": "widget_duplicate", "dashboardId": current["id"], "expectedRevision": current["revision"], "widgetId": source["id"], "currentTitle": source["title"], "title": "Copy", "requiresConfirmation": True}
        result = self.store.apply_ai_mutation(current["id"], "operation_duplicate", current["revision"], action)
        saved = self.store.get(current["id"]); duplicate = next(item for item in saved["dashboard"]["widgets"] if item["id"] == result["widgetId"])
        self.assertEqual(duplicate["configuration"], source["configuration"])
        for widget in saved["dashboard"]["widgets"]:
            if widget["id"] == duplicate["id"]: continue
            left, right = duplicate["layout"]["desktop"], widget["layout"]["desktop"]
            self.assertTrue(left["x"] + left["w"] <= right["x"] or right["x"] + right["w"] <= left["x"] or left["y"] + left["h"] <= right["y"] or right["y"] + right["h"] <= left["y"])

    def test_ai_mutations_serialize_across_store_instances(self):
        self.store.initialize_once(); current = self.store.get("dashboard_mercury"); widget = current["dashboard"]["widgets"][0]
        action = {"type": "widget_rename", "dashboardId": current["id"], "expectedRevision": 1, "widgetId": widget["id"], "currentTitle": widget["title"], "title": "First", "requiresConfirmation": True}
        outcomes = []
        errors = []
        def mutate(store, operation):
            try: outcomes.append(store.apply_ai_mutation(current["id"], operation, 1, action))
            except DashboardStoreError as error: errors.append(error.payload["error"]["code"])
        threads = [threading.Thread(target=mutate, args=(store, operation)) for store, operation in ((self.store, "operation_one"), (DashboardStore(self.root), "operation_two"))]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(errors, ["dashboard_changed"])
        self.assertEqual(self.store.get(current["id"])["revision"], 2)

    def test_client_save_cannot_remove_or_forge_ai_receipts(self):
        self.store.initialize_once(); current = self.store.get("dashboard_mercury"); widget = current["dashboard"]["widgets"][0]
        action = {"type": "widget_rename", "dashboardId": current["id"], "expectedRevision": 1, "widgetId": widget["id"], "currentTitle": widget["title"], "title": "Renamed", "requiresConfirmation": True}
        receipt = self.store.apply_ai_mutation(current["id"], "operation_receipt", 1, action)
        saved = self.store.get(current["id"]); saved["aiOperationReceipts"] = {"forged": {"kind": "fake"}}
        self.store.save(saved["id"], saved)
        self.assertEqual(self.store.operation_receipt(current["id"], "operation_receipt"), receipt)
        self.assertIsNone(self.store.operation_receipt(current["id"], "forged"))

    def test_ai_placeholder_uses_next_sparse_mobile_order(self):
        self.store.initialize_once(); current = self.store.get("dashboard_mercury")
        current["dashboard"]["widgets"][0]["layout"]["mobile"]["order"] = 50
        current = self.store.save(current["id"], current)
        action = {"type": "widget_create", "dashboardId": current["id"], "expectedRevision": current["revision"], "title": "New", "requiresConfirmation": True}
        result = self.store.apply_ai_mutation(current["id"], "operation_create", current["revision"], action)
        widget = next(item for item in self.store.get(current["id"])["dashboard"]["widgets"] if item["id"] == result["widgetId"])
        self.assertEqual(widget["layout"]["mobile"]["order"], 51)

    def test_stale_revision_is_rejected_without_changing_layout(self):
        self.store.initialize_once()
        first = self.store.get("dashboard_mercury")
        stale = json.loads(json.dumps(first))
        before_mobile = json.loads(json.dumps(first["dashboard"]["widgets"][0]["layout"]["mobile"]))
        first["dashboard"]["widgets"][0]["layout"]["desktop"]["x"] = 1
        saved = self.store.save(first["id"], first)
        self.assertEqual(saved["dashboard"]["widgets"][0]["layout"]["mobile"], before_mobile)
        with self.assertRaises(DashboardStoreError) as error:
            self.store.save(stale["id"], stale)
        self.assertEqual(error.exception.payload["error"]["code"], "dashboard_conflict")
        self.assertEqual(self.store.get(first["id"])["dashboard"]["widgets"][0]["layout"]["desktop"]["x"], 1)

    def test_revision_guard_rejects_stale_operations(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        with self.store.guard_revision(record["id"], record["revision"]):
            self.assertEqual(self.store.get(record["id"])["revision"], record["revision"])
        with self.assertRaises(DashboardStoreError) as error:
            with self.store.guard_revision(record["id"], record["revision"] + 1):
                pass
        self.assertEqual(error.exception.payload["error"]["code"], "dashboard_changed")

    def test_invalid_records_and_duplicate_widget_ids_are_rejected(self):
        record = mercury_dashboard_record()
        record["dashboard"]["widgets"][1]["id"] = record["dashboard"]["widgets"][0]["id"]
        with self.assertRaises(DashboardStoreError):
            self.store.save(record["id"], record)
        with self.assertRaises(DashboardStoreError):
            self.store.create("  invalid  ")

    def test_single_widget_source_persists_and_duplicates_independently(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        source = {**SOURCE, "columns": SOURCE_COLUMNS}
        record["dashboard"]["widgets"][0]["configuration"] = {"source": source}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["source"], source)
        duplicate = self.store.create("Sourced copy", record["id"])
        duplicate_source = duplicate["dashboard"]["widgets"][0]["configuration"]["source"]
        duplicate_source["relation"] = "customers"
        self.assertEqual(self.store.get(record["id"])["dashboard"]["widgets"][0]["configuration"]["source"]["relation"], "orders")

    def test_widget_source_rejects_multiple_sources_joins_sql_and_malformed_identity(self):
        invalid_configurations = [
            {"sources": [SOURCE]},
            {"source": SOURCE, "columns": [{"relation": "customers", "column": "id"}]},
            {"source": {**SOURCE, "join": {"relation": "customers"}}},
            {"source": {**SOURCE, "columnReference": "customers.id"}},
            {"source": {**SOURCE, "sql": "SELECT * FROM orders"}},
            {"source": [SOURCE]},
            {"source": {**SOURCE, "kind": "sequence"}},
            {"source": {**SOURCE, "fingerprint": "short"}},
            {"source": {key: value for key, value in SOURCE.items() if key != "namespace"}},
            {"source": {**SOURCE, "columns": [{**SOURCE_COLUMNS[0], "suggestions": ["identifier"]}]}},
            {"source": {**SOURCE, "columns": [SOURCE_COLUMNS[0], SOURCE_COLUMNS[0]]}},
        ]
        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                record = mercury_dashboard_record()
                record["dashboard"]["widgets"][0]["configuration"] = configuration
                with self.assertRaises(DashboardStoreError) as error:
                    self.store.save(record["id"], record)
                self.assertEqual(error.exception.payload["error"]["code"], "invalid_dashboard")

    def test_versioned_widget_query_round_trips_and_requires_snapshot(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        record["dashboard"]["widgets"][0]["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["query"], QUERY)
        for configuration in (
            {"source": SOURCE, "query": QUERY},
            {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": {**QUERY, "version": 3}},
            {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": {**QUERY, "measures": []}},
            {"query": QUERY},
        ):
            invalid = mercury_dashboard_record()
            invalid["dashboard"]["widgets"][0]["configuration"] = configuration
            with self.assertRaises(DashboardStoreError):
                self.store.save(invalid["id"], invalid)

    def test_aggregate_report_table_configuration_round_trips(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "table": TABLE}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["table"], TABLE)

    def test_aggregate_report_visualization_round_trips_without_changing_query_or_table(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "table": TABLE, "visualization": VISUALIZATION}
        saved = self.store.save(record["id"], record)
        configuration = saved["dashboard"]["widgets"][0]["configuration"]
        self.assertEqual(configuration["visualization"], VISUALIZATION)
        self.assertEqual(configuration["query"], QUERY)
        self.assertEqual(configuration["table"], TABLE)
        configuration["visualization"]["selections"]["bar"]["dimensionId"] = None
        configuration["visualization"]["selections"]["line"]["dimensionId"] = None
        configuration["visualization"]["selections"]["donut"]["dimensionId"] = None
        saved_again = self.store.save(saved["id"], saved)
        self.assertIsNone(saved_again["dashboard"]["widgets"][0]["configuration"]["visualization"]["selections"]["bar"]["dimensionId"])

    def test_aggregate_report_detail_round_trips_without_changing_other_configuration(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        source = {**SOURCE, "columns": SOURCE_COLUMNS}
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {
            "source": source,
            "query": QUERY,
            "table": TABLE,
            "visualization": VISUALIZATION,
            "detail": DETAIL,
        }
        saved = self.store.save(record["id"], record)
        configuration = saved["dashboard"]["widgets"][0]["configuration"]
        self.assertEqual(configuration["detail"], DETAIL)
        self.assertEqual(configuration["source"], source)
        self.assertEqual(configuration["query"], QUERY)
        self.assertEqual(configuration["table"], TABLE)
        self.assertEqual(configuration["visualization"], VISUALIZATION)

    def test_aggregate_report_detail_accepts_nullable_options_and_boundaries(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        detail = {
            **DETAIL,
            "columns": [
                {"sourceColumn": "id", "label": "I", "width": 64, "hidden": True, "searchable": False, "numberFormat": {"style": "integer"}},
                {"sourceColumn": "ordered_at", "label": "O" * 128, "width": 1024, "hidden": False, "searchable": False, "numberFormat": {"style": "auto"}},
            ],
            "defaultSort": None,
            "rowIdentifier": None,
            "pageSize": 100,
        }
        widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "detail": detail}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["detail"], detail)

    def test_aggregate_report_rejects_invalid_detail_shapes_and_references(self):
        invalid_details = [
            {**DETAIL, "version": 2},
            {**DETAIL, "version": True},
            {**DETAIL, "pageSize": 20},
            {**DETAIL, "pageSize": True},
            {**DETAIL, "pageSize": []},
            {**DETAIL, "columns": []},
            {**DETAIL, "columns": [DETAIL["columns"][0], DETAIL["columns"][0]]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "sourceColumn": "missing"}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "width": 63}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "width": 1025}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "hidden": 0}]},
            {**DETAIL, "columns": [{**column, "hidden": True} for column in DETAIL["columns"]]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "searchable": "yes"}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "label": ""}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "label": "x" * 129}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "extra": False}]},
            {**DETAIL, "columns": [{**DETAIL["columns"][0], "numberFormat": {"style": "currency", "currency": "usd", "fractionDigits": 2}}]},
            {**DETAIL, "columns": [{key: value for key, value in DETAIL["columns"][0].items() if key != "numberFormat"}]},
            {**DETAIL, "defaultSort": {"sourceColumn": "missing", "direction": "asc", "nulls": "first"}},
            {**DETAIL, "defaultSort": {"sourceColumn": [], "direction": "asc", "nulls": "first"}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": "up", "nulls": "first"}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": [], "nulls": "first"}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": "asc", "nulls": "auto"}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": "asc", "nulls": []}},
            {**DETAIL, "defaultSort": {"sourceColumn": "id", "direction": "asc", "nulls": "first", "extra": True}},
            {**DETAIL, "rowIdentifier": "missing"},
            {**DETAIL, "rowIdentifier": False},
            {**DETAIL, "extra": None},
        ]
        for detail in invalid_details:
            with self.subTest(detail=detail):
                record = mercury_dashboard_record()
                widget = record["dashboard"]["widgets"][0]
                widget["kind"] = "aggregate_report"
                widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "detail": detail}
                with self.assertRaises(DashboardStoreError) as error:
                    self.store.save(record["id"], record)
                self.assertEqual(error.exception.payload["error"]["code"], "invalid_dashboard")
        extra_source_columns = [
            {"name": f"column_{index}", "type": "text", "nullable": True, "ordinal": index + 3}
            for index in range(63)
        ]
        too_many_columns = [
            {"sourceColumn": column["name"], "label": column["name"], "width": 160, "hidden": False, "searchable": True, "numberFormat": {"style": "auto"}}
            for column in extra_source_columns
        ]
        record = mercury_dashboard_record()
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        widget["configuration"] = {
            "source": {**SOURCE, "columns": SOURCE_COLUMNS + extra_source_columns},
            "query": QUERY,
            "detail": {**DETAIL, "columns": DETAIL["columns"] + too_many_columns},
        }
        with self.assertRaises(DashboardStoreError):
            self.store.save(record["id"], record)

    def test_detail_references_can_use_snapshot_columns_and_non_aggregate_widgets_reject_detail(self):
        self.store.initialize_once()
        record = self.store.get("dashboard_mercury")
        widget = record["dashboard"]["widgets"][0]
        widget["kind"] = "aggregate_report"
        detail = {**DETAIL, "columns": [{**DETAIL["columns"][0], "searchable": True}], "defaultSort": {"sourceColumn": "id", "direction": "asc", "nulls": "last"}}
        widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "detail": detail}
        saved = self.store.save(record["id"], record)
        self.assertEqual(saved["dashboard"]["widgets"][0]["configuration"]["detail"], detail)
        for kind in ("preview", "placeholder"):
            with self.subTest(kind=kind):
                record = mercury_dashboard_record()
                record["dashboard"]["widgets"][0]["kind"] = kind
                record["dashboard"]["widgets"][0]["configuration"] = {
                    "source": {**SOURCE, "columns": SOURCE_COLUMNS},
                    "query": QUERY,
                    "detail": DETAIL,
                }
                with self.assertRaises(DashboardStoreError):
                    self.store.save(record["id"], record)

    def test_aggregate_report_rejects_invalid_visualization_references_and_shapes(self):
        invalid_visualizations = [
            {**VISUALIZATION, "version": 2},
            {**VISUALIZATION, "mode": "scatter"},
            {**VISUALIZATION, "mode": []},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "bar": {"dimensionId": "missing", "measureIds": ["measure_orders"]}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "bar": {"dimensionId": [], "measureIds": ["measure_orders"]}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "line": {"dimensionId": "dimension_date", "measureIds": []}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "line": {"dimensionId": "dimension_date", "measureIds": [[]]}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "kpi": {"measureIds": ["missing"]}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "donut": {"dimensionId": "dimension_date", "measureId": None}}},
            {**VISUALIZATION, "selections": {**VISUALIZATION["selections"], "extra": {}}},
        ]
        for visualization in invalid_visualizations:
            with self.subTest(visualization=visualization):
                record = mercury_dashboard_record()
                widget = record["dashboard"]["widgets"][0]
                widget["kind"] = "aggregate_report"
                widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "visualization": visualization}
                with self.assertRaises(DashboardStoreError):
                    self.store.save(record["id"], record)

    def test_aggregate_report_rejects_invalid_presentation_without_breaking_existing_widgets(self):
        invalid_tables = [
            {**TABLE, "pageSize": 500},
            {**TABLE, "columns": TABLE["columns"][:-1]},
            {**TABLE, "columns": [TABLE["columns"][0], TABLE["columns"][0]]},
            {**TABLE, "columns": [TABLE["columns"][1], TABLE["columns"][0]]},
            {**TABLE, "columns": [{**TABLE["columns"][0], "width": 63}, TABLE["columns"][1]]},
            {**TABLE, "columns": [{**TABLE["columns"][0], "hidden": "no"}, TABLE["columns"][1]]},
            {**TABLE, "columns": [{**TABLE["columns"][0], "targetId": []}, TABLE["columns"][1]]},
        ]
        for table in invalid_tables:
            with self.subTest(table=table):
                record = mercury_dashboard_record()
                widget = record["dashboard"]["widgets"][0]
                widget["kind"] = "aggregate_report"
                widget["configuration"] = {"source": {**SOURCE, "columns": SOURCE_COLUMNS}, "query": QUERY, "table": table}
                with self.assertRaises(DashboardStoreError):
                    self.store.save(record["id"], record)
        record = mercury_dashboard_record()
        record["dashboard"]["widgets"][0]["kind"] = "aggregate_report"
        with self.assertRaises(DashboardStoreError):
            self.store.save(record["id"], record)

    def test_malformed_file_is_not_listed_or_overwritten(self):
        malformed = self.root / "broken.json"
        malformed.write_text("not json", encoding="utf-8")
        self.assertEqual(self.store.list(), [])
        with self.assertRaises(DashboardStoreError):
            self.store.get("broken")
        self.assertEqual(malformed.read_text(encoding="utf-8"), "not json")


if __name__ == "__main__":
    unittest.main()
