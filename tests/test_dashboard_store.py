import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.dashboard_store import DashboardStore, DashboardStoreError, mercury_dashboard_record


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

    def test_create_duplicate_and_permissions(self):
        self.store.initialize_once()
        created = self.store.create("Operations")
        duplicate = self.store.create("Mercury copy", "dashboard_mercury")
        self.assertEqual(created["dashboard"]["widgets"], [])
        self.assertEqual(len(duplicate["dashboard"]["widgets"]), 6)
        self.assertNotEqual(duplicate["id"], "dashboard_mercury")
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / f"{created['id']}.json").stat().st_mode), 0o600)

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
