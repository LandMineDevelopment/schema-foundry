import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.widget_query import (
    QueryValidationError,
    compile_detail_query,
    compile_query,
    compile_temporal_series_manifest,
    compile_temporal_series_window,
    normalize_detail_request,
    normalize_query,
    normalize_temporal_series,
)
from schemii.postgres_service import quote_identifier


COLUMNS = [
    {"name": "publisher", "type": "text", "nullable": False, "ordinal": 1},
    {"name": "format", "type": "text", "nullable": True, "ordinal": 2},
    {"name": "revenue", "type": "numeric(10,2)", "nullable": True, "ordinal": 3},
    {"name": "customer_id", "type": "bigint", "nullable": False, "ordinal": 4},
    {"name": "metadata", "type": "jsonb", "nullable": True, "ordinal": 5},
    {"name": "raw", "type": "json", "nullable": True, "ordinal": 6},
    {"name": "elapsed", "type": "interval", "nullable": True, "ordinal": 7},
    {"name": "price", "type": "money", "nullable": True, "ordinal": 8},
    {"name": "location", "type": "point", "nullable": True, "ordinal": 9},
]


def query():
    return {
        "version": 2,
        "dimensions": [
            {"id": "dimension_publisher", "label": "Publisher", "column": "publisher"},
            {"id": "dimension_format", "label": "Format", "column": "format"},
        ],
        "measures": [
            {"id": "measure_orders", "label": "Orders", "column": None, "aggregation": "count_rows", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "integer"}},
            {"id": "measure_revenue", "label": "Revenue", "column": "revenue", "aggregation": "sum", "distinct": False, "nullBehavior": "zero", "numberFormat": {"style": "currency", "currency": "USD", "fractionDigits": 2}},
            {"id": "measure_customers", "label": "Customers", "column": "customer_id", "aggregation": "count", "distinct": True, "nullBehavior": "preserve", "numberFormat": {"style": "integer"}},
        ],
        "filters": [{"id": "filter_group_formats", "conditions": [{"id": "filter_format", "column": "format", "operator": "in", "values": ["paperback", "hardcover"]}]}],
        "sort": [{"targetKind": "measure", "targetId": "measure_revenue", "direction": "desc", "nulls": "last"}],
        "limit": 100,
    }


class WidgetQueryTests(unittest.TestCase):
    def test_temporal_series_compiles_utc_manifest_and_half_open_aligned_window(self):
        columns = [*COLUMNS, {"name": "ordered_at", "type": "timestamp without time zone", "nullable": False, "ordinal": 10}]
        value = query()
        value["dimensions"] = [{"id": "dimension_ordered", "label": "Ordered", "column": "ordered_at"}]
        value["measures"] = [value["measures"][1]]
        value["sort"] = [{"targetKind": "measure", "targetId": "measure_revenue", "direction": "desc", "nulls": "last"}]
        series = normalize_temporal_series(value, columns)
        manifest = compile_temporal_series_manifest({"namespace": "bookstore", "relation": "orders"}, series, quote_identifier)
        self.assertIn('pg_catalog.min(("ordered_at" AT TIME ZONE \'UTC\'))', manifest["sql"])
        self.assertIn('(\n    (\n        "format" IN (%s, %s)\n    )\n)\n    AND ("ordered_at" AT TIME ZONE \'UTC\') IS NOT NULL', manifest["sql"])
        window = compile_temporal_series_window(
            {"namespace": "bookstore", "relation": "orders"}, series, quote_identifier,
            86400, "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", 32,
        )
        self.assertIn("extract(epoch FROM", window["sql"])
        self.assertIn("GROUP BY\n    1", window["sql"])
        self.assertIn('ORDER BY\n    "__schemer_t0" ASC NULLS LAST', window["sql"])
        self.assertIn(">= %s", window["sql"])
        self.assertIn("< %s", window["sql"])
        self.assertNotIn("__schemer_m0\" DESC", window["sql"])
        self.assertEqual(window["parameters"], [86400, 86400, "paperback", "hardcover", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", 33])

        value["dimensions"] = [{"id": "dimension_publisher", "label": "Publisher", "column": "publisher"}]
        with self.assertRaisesRegex(QueryValidationError, "date or timestamp"):
            normalize_temporal_series(value, columns)

    def test_normalizes_and_compiles_deterministically(self):
        normalized = normalize_query(query(), COLUMNS)
        compiled = compile_query(
            {"namespace": "bookstore", "relation": "book sales"}, normalized, quote_identifier
        )
        self.assertIn('"publisher" AS "__schemer_d0"', compiled["sql"])
        self.assertIn('pg_catalog.count(*) AS "__schemer_m0"', compiled["sql"])
        self.assertIn('COALESCE(pg_catalog.sum("revenue"), 0)', compiled["sql"])
        self.assertIn('pg_catalog.count(DISTINCT "customer_id")', compiled["sql"])
        self.assertIn('FROM "bookstore"."book sales"', compiled["sql"])
        self.assertIn('WHERE\n    (\n        "format" IN (%s, %s)\n    )', compiled["sql"])
        self.assertIn('GROUP BY\n    "publisher",\n    "format"', compiled["sql"])
        self.assertIn('ORDER BY\n    "__schemer_m1" DESC NULLS LAST,\n    "__schemer_d0" ASC NULLS LAST,\n    "__schemer_d1" ASC NULLS LAST', compiled["sql"])
        self.assertEqual(compiled["parameters"], ["paperback", "hardcover", 101])
        self.assertNotIn("paperback", compiled["sql"])

    def test_supports_every_aggregation_and_zero_dimensions(self):
        value = query()
        value["dimensions"] = []
        value["filters"] = []
        value["sort"] = []
        value["measures"] = [
            {"id": f"m_{aggregation}", "label": aggregation, "column": None if aggregation == "count_rows" else "revenue", "aggregation": aggregation, "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "auto"}}
            for aggregation in ("count_rows", "count", "sum", "average", "minimum", "maximum")
        ]
        sql = compile_query({"namespace": "public", "relation": "orders"}, normalize_query(value, COLUMNS), quote_identifier)["sql"]
        for function in ("count", "sum", "avg", "min", "max"):
            self.assertIn(f"pg_catalog.{function}(", sql)
        self.assertNotIn("GROUP BY", sql)

    def test_preserves_explicit_multi_column_sort_order(self):
        value = query()
        value["sort"] = [
            {"targetKind": "dimension", "targetId": "dimension_format", "direction": "asc", "nulls": "first"},
            {"targetKind": "measure", "targetId": "measure_revenue", "direction": "desc", "nulls": "last"},
            {"targetKind": "dimension", "targetId": "dimension_publisher", "direction": "desc", "nulls": "first"},
        ]
        normalized = normalize_query(value, COLUMNS)
        self.assertEqual([item["targetId"] for item in normalized["sort"]], ["dimension_format", "measure_revenue", "dimension_publisher"])
        sql = compile_query({"namespace": "public", "relation": "orders"}, normalized, quote_identifier)["sql"]
        self.assertIn(
            'ORDER BY\n    "__schemer_d1" ASC NULLS FIRST,\n    "__schemer_m1" DESC NULLS LAST,\n    "__schemer_d0" DESC NULLS FIRST',
            sql,
        )

    def test_supports_postgresql_interval_and_money_aggregates(self):
        value = query()
        value["dimensions"] = []
        value["filters"] = []
        value["sort"] = []
        value["measures"] = [
            {"id": "m_interval", "label": "Average elapsed", "column": "elapsed", "aggregation": "average", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "auto"}},
            {"id": "m_money", "label": "Total price", "column": "price", "aggregation": "sum", "distinct": False, "nullBehavior": "preserve", "numberFormat": {"style": "auto"}},
        ]
        sql = compile_query({"namespace": "public", "relation": "orders"}, normalize_query(value, COLUMNS), quote_identifier)["sql"]
        self.assertIn('pg_catalog.avg("elapsed")', sql)
        self.assertIn('pg_catalog.sum("price")', sql)

    def test_compiles_or_groups_and_type_aware_text_filters(self):
        value = query()
        value["filters"] = [
            {"id": "group_one", "conditions": [
                {"id": "f_one_a", "column": "publisher", "operator": "eq", "values": ["A"]},
                {"id": "f_one_b", "column": "format", "operator": "starts_with", "values": ["hard_"]},
            ]},
            {"id": "group_two", "conditions": [
                {"id": "f_two_a", "column": "publisher", "operator": "like", "values": ["B%"]},
                {"id": "f_two_b", "column": "revenue", "operator": "between", "values": [10, 20]},
            ]},
        ]
        compiled = compile_query({"namespace": "public", "relation": "books"}, normalize_query(value, COLUMNS), quote_identifier)
        self.assertIn("\n        AND ", compiled["sql"])
        self.assertIn("\n    OR (", compiled["sql"])
        self.assertIn('"revenue" BETWEEN %s AND %s', compiled["sql"])
        self.assertIn('"format" LIKE %s ESCAPE E\'\\\\\'', compiled["sql"])
        self.assertEqual(compiled["parameters"][:5], ["A", "hard\\_%", "B%", 10, 20])

    def test_upgrades_version_one_flat_filters(self):
        value = query()
        value["version"] = 1
        value["filters"] = value["filters"][0]["conditions"]
        normalized = normalize_query(value, COLUMNS)
        self.assertEqual(normalized["version"], 2)
        self.assertEqual(normalized["filters"][0]["id"], "filter_group_legacy")

        value["dimensions"][0]["id"] = "filter_group_legacy"
        normalized = normalize_query(value, COLUMNS)
        self.assertEqual(normalized["filters"][0]["id"], "filter_group_legacy_")

    def test_compiles_detail_count_and_page_from_server_owned_bindings(self):
        normalized = normalize_query(query(), COLUMNS)
        detail = {
            "version": 1,
            "columns": [
                {"id": "detail_customer", "label": "Customer", "column": "customer_id", "numberFormat": {"style": "integer"}, "searchable": True},
                {"id": "detail_publisher", "label": "Publisher", "column": "publisher", "numberFormat": {"style": "auto"}, "searchable": True},
                {"id": "detail_format", "label": "Format", "column": "format", "numberFormat": {"style": "auto"}, "searchable": True},
            ],
            "rowIdentifier": "customer_id",
        }
        request = normalize_detail_request(
            {"dimensions": [
                {"targetId": "dimension_format", "value": None},
                {"targetId": "dimension_publisher", "value": "O'Reilly"},
            ], "measureId": "measure_revenue"},
            detail, 20, 25,
            {"targetId": "detail_publisher", "direction": "desc", "nulls": "last"},
            [
                {"targetId": "detail_format", "value": "paper"},
                {"targetId": "detail_publisher", "value": "50%_off"},
                {"targetId": "detail_customer", "value": "42"},
            ], normalized, COLUMNS,
        )
        compiled = compile_detail_query(
            {"namespace": "bookstore", "relation": "book sales"}, normalized, request, COLUMNS, quote_identifier
        )
        for sql in (compiled["countSql"], compiled["sql"]):
            self.assertIn('FROM "bookstore"."book sales"', sql)
            self.assertIn('"format" IN (%s, %s)', sql)
            self.assertIn('"publisher" = %s', sql)
            self.assertIn('"format" IS NULL', sql)
            self.assertIn('"revenue" IS NOT NULL', sql)
            self.assertIn('CAST("customer_id" AS text) ILIKE %s', sql)
            self.assertIn('CAST("publisher" AS text) ILIKE %s', sql)
            self.assertIn('CAST("format" AS text) ILIKE %s', sql)
            self.assertNotIn('ILIKE %s ESCAPE E\'\\\\\' OR', sql)
            self.assertNotIn("O'Reilly", sql)
        self.assertIn('"__schemer_c1" DESC NULLS LAST', compiled["sql"])
        self.assertIn('"customer_id" ASC NULLS LAST', compiled["sql"])
        self.assertEqual(
            compiled["countParameters"],
            ["paperback", "hardcover", "O'Reilly", "%42%", "%50\\%\\_off%", "%paper%"],
        )
        self.assertEqual(compiled["parameters"][-2:], [25, 20])
        self.assertIn("contains", compiled["columns"][1]["operators"])
        self.assertNotIn("contains", compiled["columns"][0]["operators"])

    def test_rejects_invalid_detail_selection_configuration_and_bounds(self):
        normalized = normalize_query(query(), COLUMNS)
        detail = {
            "version": 1,
            "columns": [{"id": "detail_publisher", "label": "Publisher", "column": "publisher", "numberFormat": {"style": "auto"}, "searchable": True}],
            "rowIdentifier": None,
        }
        valid_selection = {"dimensions": [
            {"targetId": "dimension_publisher", "value": "A"},
            {"targetId": "dimension_format", "value": "paperback"},
        ]}
        invalid = [
            (valid_selection["dimensions"][:1], detail, 0, 20, None, []),
            ([valid_selection["dimensions"][0], valid_selection["dimensions"][0]], detail, 0, 20, None, []),
            (valid_selection["dimensions"], {**detail, "columns": [{**detail["columns"][0], "column": "missing"}]}, 0, 20, None, []),
            (valid_selection["dimensions"], detail, -1, 20, None, []),
            (valid_selection["dimensions"], detail, 0, 101, None, []),
            (valid_selection["dimensions"], detail, 0, 20, {"targetId": "missing", "direction": "asc", "nulls": "last"}, []),
            (valid_selection["dimensions"], detail, 0, 20, None, [{"targetId": "missing", "value": "search"}]),
            (valid_selection["dimensions"], detail, 0, 20, None, [{"targetId": "detail_publisher", "value": "search"}, {"targetId": "detail_publisher", "value": "again"}]),
        ]
        for dimensions, candidate_detail, offset, limit, sort, searches in invalid:
            with self.subTest(detail=candidate_detail, offset=offset, limit=limit), self.assertRaises(QueryValidationError):
                normalize_detail_request({"dimensions": dimensions}, candidate_detail, offset, limit, sort, searches, normalized, COLUMNS)

    def test_temporal_bucket_detail_selection_uses_a_half_open_utc_range(self):
        columns = [*COLUMNS, {"name": "ordered_at", "type": "timestamp without time zone", "nullable": False, "ordinal": 10}]
        value = query()
        value["dimensions"] = [{"id": "dimension_ordered", "label": "Ordered", "column": "ordered_at"}]
        value["measures"] = [value["measures"][1]]
        value["sort"] = []
        normalized = normalize_query(value, columns)
        detail = {
            "version": 1,
            "columns": [{"id": "detail_ordered", "label": "Ordered", "column": "ordered_at", "numberFormat": {"style": "auto"}, "searchable": True}],
            "rowIdentifier": None,
        }
        request = normalize_detail_request(
            {"dimensions": [{
                "targetId": "dimension_ordered", "operator": "gte_lt",
                "values": ["2026-01-01T00:00:00.000Z", "2026-01-02T00:00:00.000Z"],
            }]},
            detail, 0, 25, None, [], normalized, columns,
        )
        compiled = compile_detail_query({"namespace": "public", "relation": "orders"}, normalized, request, columns, quote_identifier)
        self.assertIn("(\"ordered_at\" AT TIME ZONE 'UTC') >= %s", compiled["sql"])
        self.assertIn("(\"ordered_at\" AT TIME ZONE 'UTC') < %s", compiled["sql"])
        self.assertEqual(compiled["countParameters"][-2:], ["2026-01-01T00:00:00.000Z", "2026-01-02T00:00:00.000Z"])

        with self.assertRaisesRegex(QueryValidationError, "selected dimension"):
            normalize_detail_request(
                {"dimensions": [{"targetId": "dimension_ordered", "operator": "gte_lt", "values": ["2026-01-01T00:00:00.000Z"]}]},
                detail, 0, 25, None, [], normalized, columns,
            )

    def test_rejects_invalid_shapes_references_and_values(self):
        invalid = []
        no_measures = query(); no_measures["measures"] = []; invalid.append(no_measures)
        missing_column = query(); missing_column["dimensions"][0]["column"] = "missing"; invalid.append(missing_column)
        bad_distinct = query(); bad_distinct["measures"][1]["distinct"] = True; invalid.append(bad_distinct)
        dangling_sort = query(); dangling_sort["sort"][0]["targetId"] = "missing"; invalid.append(dangling_sort)
        empty_in = query(); empty_in["filters"][0]["conditions"][0]["values"] = []; invalid.append(empty_in)
        bad_limit = query(); bad_limit["limit"] = True; invalid.append(bad_limit)
        bad_zero = query(); bad_zero["measures"][1].update({"column": "publisher", "aggregation": "maximum", "nullBehavior": "zero"}); invalid.append(bad_zero)
        bad_max = query(); bad_max["measures"][1].update({"column": "metadata", "aggregation": "maximum", "nullBehavior": "preserve"}); invalid.append(bad_max)
        bad_dimension = query(); bad_dimension["dimensions"][0]["column"] = "raw"; invalid.append(bad_dimension)
        bad_distinct_type = query(); bad_distinct_type["measures"][2]["column"] = "raw"; invalid.append(bad_distinct_type)
        bad_filter_type = query(); bad_filter_type["filters"][0]["conditions"][0]["column"] = "raw"; invalid.append(bad_filter_type)
        bad_geometric_filter = query(); bad_geometric_filter["filters"][0]["conditions"][0]["column"] = "location"; invalid.append(bad_geometric_filter)
        bad_numeric_filter = query(); bad_numeric_filter["filters"][0]["conditions"][0].update({"column": "revenue", "operator": "contains", "values": ["2"]}); invalid.append(bad_numeric_filter)
        bad_text_between = query(); bad_text_between["filters"][0]["conditions"][0].update({"operator": "between", "values": ["a", "z"]}); invalid.append(bad_text_between)
        bad_between_count = query(); bad_between_count["filters"][0]["conditions"][0].update({"column": "revenue", "operator": "between", "values": [10]}); invalid.append(bad_between_count)
        unknown = query(); unknown["sql"] = "SELECT 1"; invalid.append(unknown)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(QueryValidationError):
                normalize_query(value, COLUMNS)


if __name__ == "__main__":
    unittest.main()
