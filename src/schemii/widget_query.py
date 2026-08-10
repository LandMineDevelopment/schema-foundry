from __future__ import annotations

import math
import re
from typing import Any, Callable


ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
NUMERIC_TYPE_RE = re.compile(r"^(?:smallint|integer|bigint|numeric(?:\([^)]*\))?|decimal(?:\([^)]*\))?|real|double precision)$", re.I)
SUM_TYPE_RE = re.compile(r"^(?:smallint|integer|bigint|numeric(?:\([^)]*\))?|decimal(?:\([^)]*\))?|real|double precision|interval|money)$", re.I)
AVERAGE_TYPE_RE = re.compile(r"^(?:smallint|integer|bigint|numeric(?:\([^)]*\))?|decimal(?:\([^)]*\))?|real|double precision|interval)$", re.I)
NON_ORDERABLE_TYPE_RE = re.compile(r"^(?:boolean|bit(?: varying)?(?:\([^)]*\))?|jsonb?|xml|box|circle|line|lseg|path|point|polygon)$", re.I)
NON_GROUPABLE_TYPE_RE = re.compile(r"^(?:json|xml|box|circle|line|lseg|path|point|polygon)$", re.I)
AGGREGATIONS = {"count_rows", "count", "sum", "average", "minimum", "maximum"}
FILTER_OPERATORS = {"eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "not_in", "like", "contains", "starts_with", "ends_with", "is_null", "is_not_null"}
NULL_FILTER_OPERATORS = {"is_null", "is_not_null"}
ORDER_FILTER_OPERATORS = {"eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "not_in"} | NULL_FILTER_OPERATORS
TEXT_FILTER_OPERATORS = {"eq", "neq", "in", "not_in", "like", "contains", "starts_with", "ends_with"} | NULL_FILTER_OPERATORS
BOOLEAN_FILTER_OPERATORS = {"eq", "neq"} | NULL_FILTER_OPERATORS
TEXT_TYPE_RE = re.compile(r"^(?:text|character varying(?:\([^)]*\))?|character(?:\([^)]*\))?|varchar(?:\([^)]*\))?|char(?:\([^)]*\))?|citext|name)$", re.I)
BOOLEAN_TYPE_RE = re.compile(r"^boolean$", re.I)
TEMPORAL_TYPE_RE = re.compile(r"^(?:date|time(?:stamp)?(?: with(?:out)? time zone)?|interval)$", re.I)


class QueryValidationError(ValueError):
    pass


def _text(value: Any, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise QueryValidationError(f"{field} must be a trimmed string up to {maximum} characters")
    return value


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise QueryValidationError(f"{field} is invalid")
    return value


def _bounded_list(value: Any, field: str, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise QueryValidationError(f"{field} must contain from {minimum} to {maximum} items")
    return value


def _format(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or "style" not in value:
        raise QueryValidationError("measure numberFormat is invalid")
    style = value.get("style")
    if style in {"auto", "integer"} and set(value) == {"style"}:
        return {"style": style}
    if style in {"decimal", "percent"} and set(value) == {"style", "fractionDigits"}:
        digits = value.get("fractionDigits")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 20:
            raise QueryValidationError("fractionDigits must be from 0 to 20")
        return {"style": style, "fractionDigits": digits}
    if style == "currency" and set(value) == {"style", "currency", "fractionDigits"}:
        currency = value.get("currency")
        digits = value.get("fractionDigits")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency) or isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 20:
            raise QueryValidationError("currency number format is invalid")
        return {"style": style, "currency": currency, "fractionDigits": digits}
    raise QueryValidationError("measure numberFormat fields are invalid")


def _filter_operators(column_type: str) -> set[str]:
    if NON_GROUPABLE_TYPE_RE.fullmatch(column_type):
        return NULL_FILTER_OPERATORS
    if TEXT_TYPE_RE.fullmatch(column_type):
        return TEXT_FILTER_OPERATORS
    if BOOLEAN_TYPE_RE.fullmatch(column_type):
        return BOOLEAN_FILTER_OPERATORS
    if NUMERIC_TYPE_RE.fullmatch(column_type) or TEMPORAL_TYPE_RE.fullmatch(column_type):
        return ORDER_FILTER_OPERATORS
    return {"eq", "neq", "in", "not_in"} | NULL_FILTER_OPERATORS


def normalize_query(query: Any, source_columns: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(query, dict) or set(query) not in (
        {"version", "dimensions", "measures", "filters", "sort"},
        {"version", "dimensions", "measures", "filters", "sort", "limit"},
    ) or query.get("version") not in {1, 2}:
        raise QueryValidationError("query must use supported version-1 or version-2 fields")
    input_version = query["version"]
    columns = {column.get("name"): column for column in source_columns or []}
    dimensions = []
    measures = []
    filter_groups = []
    sorts = []
    ids: set[str] = set()
    dimension_columns: set[str] = set()

    for item in _bounded_list(query.get("dimensions"), "dimensions", 0, 32):
        if not isinstance(item, dict) or set(item) != {"id", "label", "column"}:
            raise QueryValidationError("dimension fields are invalid")
        item_id = _id(item.get("id"), "dimension ID")
        column = _text(item.get("column"), "dimension column", 63)
        if item_id in ids or column in dimension_columns or source_columns is not None and column not in columns:
            raise QueryValidationError("dimension ID or source column is invalid or duplicated")
        if source_columns is not None and NON_GROUPABLE_TYPE_RE.fullmatch(str(columns[column].get("type", ""))):
            raise QueryValidationError("dimension requires a groupable PostgreSQL column")
        ids.add(item_id)
        dimension_columns.add(column)
        dimensions.append({"id": item_id, "label": _text(item.get("label"), "dimension label"), "column": column})

    for item in _bounded_list(query.get("measures"), "measures", 1, 32):
        fields = {"id", "label", "column", "aggregation", "distinct", "nullBehavior", "numberFormat"}
        if not isinstance(item, dict) or set(item) != fields:
            raise QueryValidationError("measure fields are invalid")
        item_id = _id(item.get("id"), "measure ID")
        aggregation = item.get("aggregation")
        column = item.get("column")
        distinct = item.get("distinct")
        null_behavior = item.get("nullBehavior")
        if item_id in ids or aggregation not in AGGREGATIONS or not isinstance(distinct, bool) or null_behavior not in {"preserve", "zero"}:
            raise QueryValidationError("measure identity or behavior is invalid")
        if aggregation == "count_rows":
            if column is not None or distinct or null_behavior != "preserve":
                raise QueryValidationError("count_rows cannot use a column, distinct, or zero null behavior")
        else:
            column = _text(column, "measure column", 63)
            if source_columns is not None and column not in columns:
                raise QueryValidationError("measure source column does not exist")
            if aggregation != "count" and distinct:
                raise QueryValidationError("distinct is supported only for count")
            if aggregation == "count" and null_behavior != "preserve":
                raise QueryValidationError("count must preserve native null behavior")
            column_type = str(columns[column].get("type", "")) if source_columns is not None else ""
            if aggregation == "sum" and source_columns is not None and not SUM_TYPE_RE.fullmatch(column_type):
                raise QueryValidationError("sum is not supported for this PostgreSQL column")
            if aggregation == "average" and source_columns is not None and not AVERAGE_TYPE_RE.fullmatch(column_type):
                raise QueryValidationError("average is not supported for this PostgreSQL column")
            if aggregation == "count" and distinct and source_columns is not None and NON_GROUPABLE_TYPE_RE.fullmatch(column_type):
                raise QueryValidationError("count distinct requires a comparable PostgreSQL column")
            if aggregation in {"minimum", "maximum"} and source_columns is not None and NON_ORDERABLE_TYPE_RE.fullmatch(str(columns[column].get("type", ""))):
                raise QueryValidationError(f"{aggregation} is not supported for this PostgreSQL column")
            if null_behavior == "zero" and aggregation not in {"sum", "average", "minimum", "maximum"}:
                raise QueryValidationError("zero null behavior is invalid for this aggregation")
            if null_behavior == "zero" and source_columns is not None and not NUMERIC_TYPE_RE.fullmatch(str(columns[column].get("type", ""))):
                raise QueryValidationError("zero null behavior requires a numeric PostgreSQL column")
        ids.add(item_id)
        measures.append({
            "id": item_id, "label": _text(item.get("label"), "measure label"), "column": column,
            "aggregation": aggregation, "distinct": distinct, "nullBehavior": null_behavior,
            "numberFormat": _format(item.get("numberFormat")),
        })

    raw_filters = _bounded_list(query.get("filters"), "filters", 0, 32 if input_version == 2 else 64)
    legacy_group_id = "filter_group_legacy"
    reserved_filter_ids = {item.get("id") for item in raw_filters if isinstance(item, dict)}
    while legacy_group_id in ids or legacy_group_id in reserved_filter_ids:
        legacy_group_id += "_"
    raw_groups = [{"id": legacy_group_id, "conditions": raw_filters}] if input_version == 1 and raw_filters else raw_filters
    condition_count = 0
    for group in raw_groups:
        if not isinstance(group, dict) or set(group) != {"id", "conditions"}:
            raise QueryValidationError("filter group fields are invalid")
        group_id = _id(group.get("id"), "filter group ID")
        if group_id in ids:
            raise QueryValidationError("filter group ID is duplicated")
        ids.add(group_id)
        conditions = []
        for item in _bounded_list(group.get("conditions"), "filter group conditions", 1, 64):
            condition_count += 1
            if condition_count > 64 or not isinstance(item, dict) or set(item) != {"id", "column", "operator", "values"}:
                raise QueryValidationError("filter fields are invalid")
            item_id = _id(item.get("id"), "filter ID")
            column = _text(item.get("column"), "filter column", 63)
            operator = item.get("operator")
            values = item.get("values")
            if item_id in ids or operator not in FILTER_OPERATORS or source_columns is not None and column not in columns:
                raise QueryValidationError("filter identity, operator, or column is invalid")
            if source_columns is not None and operator not in _filter_operators(str(columns[column].get("type", ""))):
                raise QueryValidationError("filter operator is not supported for this PostgreSQL column type")
            if not isinstance(values, list) or len(values) > 100:
                raise QueryValidationError("filter values are invalid")
            expected = 0 if operator in NULL_FILTER_OPERATORS else 2 if operator == "between" else None if operator in {"in", "not_in"} else 1
            if expected is not None and len(values) != expected or expected is None and not values:
                raise QueryValidationError("filter value count is invalid")
            for value in values:
                if value is None or isinstance(value, (dict, list)) or isinstance(value, float) and not math.isfinite(value):
                    raise QueryValidationError("filter values must be finite non-null scalars")
                if operator in {"like", "contains", "starts_with", "ends_with"} and not isinstance(value, str):
                    raise QueryValidationError("text filter values must be strings")
            ids.add(item_id)
            conditions.append({"id": item_id, "column": column, "operator": operator, "values": list(values)})
        filter_groups.append({"id": group_id, "conditions": conditions})

    targets = {item["id"]: "dimension" for item in dimensions} | {item["id"]: "measure" for item in measures}
    sorted_targets: set[str] = set()
    for item in _bounded_list(query.get("sort"), "sort", 0, 64):
        if not isinstance(item, dict) or set(item) != {"targetKind", "targetId", "direction", "nulls"}:
            raise QueryValidationError("sort fields are invalid")
        target_id = _id(item.get("targetId"), "sort target")
        if item.get("targetKind") != targets.get(target_id) or target_id in sorted_targets or item.get("direction") not in {"asc", "desc"} or item.get("nulls") not in {"first", "last"}:
            raise QueryValidationError("sort target or behavior is invalid")
        sorted_targets.add(target_id)
        sorts.append({"targetKind": item["targetKind"], "targetId": target_id, "direction": item["direction"], "nulls": item["nulls"]})

    limit = query.get("limit", 500)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise QueryValidationError("query limit must be from 1 to 500")
    return {"version": 2, "dimensions": dimensions, "measures": measures, "filters": filter_groups, "sort": sorts, "limit": limit}


def compile_query(source: dict[str, Any], query: dict[str, Any], quote: Callable[[str], str]) -> dict[str, Any]:
    dimensions = query["dimensions"]
    measures = query["measures"]
    aliases: dict[str, str] = {}
    select = []
    output = []
    for index, item in enumerate(dimensions):
        alias = f"__schemer_d{index}"
        aliases[item["id"]] = alias
        select.append(f'{quote(item["column"])} AS {quote(alias)}')
        output.append({"id": item["id"], "kind": "dimension", "label": item["label"], "sourceColumn": item["column"]})
    aggregation_sql = {"count": "count", "sum": "sum", "average": "avg", "minimum": "min", "maximum": "max"}
    for index, item in enumerate(measures):
        alias = f"__schemer_m{index}"
        aliases[item["id"]] = alias
        if item["aggregation"] == "count_rows":
            expression = "pg_catalog.count(*)"
        else:
            distinct = "DISTINCT " if item["distinct"] else ""
            expression = f'pg_catalog.{aggregation_sql[item["aggregation"]]}({distinct}{quote(item["column"])})'
        if item["nullBehavior"] == "zero":
            expression = f"COALESCE({expression}, 0)"
        select.append(f"{expression} AS {quote(alias)}")
        output.append({
            "id": item["id"], "kind": "measure", "label": item["label"], "sourceColumn": item["column"],
            "aggregation": item["aggregation"], "distinct": item["distinct"],
            "nullBehavior": item["nullBehavior"], "numberFormat": item["numberFormat"],
        })
    parameters = []
    predicate_groups = []
    operators = {"eq": "=", "neq": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
    for group in query["filters"]:
        predicates = []
        for item in group["conditions"]:
            column = quote(item["column"])
            operator = item["operator"]
            if operator in operators:
                predicates.append(f"{column} {operators[operator]} %s")
                parameters.append(item["values"][0])
            elif operator == "between":
                predicates.append(f"{column} BETWEEN %s AND %s")
                parameters.extend(item["values"])
            elif operator in {"in", "not_in"}:
                placeholders = ", ".join("%s" for _ in item["values"])
                predicates.append(f"{column} {'NOT IN' if operator == 'not_in' else 'IN'} ({placeholders})")
                parameters.extend(item["values"])
            elif operator in {"like", "contains", "starts_with", "ends_with"}:
                value = item["values"][0]
                if operator != "like":
                    value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    value = f"%{value}%" if operator == "contains" else f"{value}%" if operator == "starts_with" else f"%{value}"
                predicates.append(f"{column} LIKE %s ESCAPE E'\\\\'")
                parameters.append(value)
            else:
                predicates.append(f"{column} IS {'NOT ' if operator == 'is_not_null' else ''}NULL")
        predicate_groups.append(predicates)
    relation = f'{quote(source["namespace"])}.{quote(source["relation"])}'
    sql = "SELECT\n    " + ",\n    ".join(select) + f"\nFROM {relation}"
    if predicate_groups:
        formatted_groups = ["(\n        " + "\n        AND ".join(group) + "\n    )" for group in predicate_groups]
        sql += "\nWHERE\n    " + "\n    OR ".join(formatted_groups)
    if dimensions:
        sql += "\nGROUP BY\n    " + ",\n    ".join(quote(item["column"]) for item in dimensions)
    sort_parts = [f'{quote(aliases[item["targetId"]])} {item["direction"].upper()} NULLS {item["nulls"].upper()}' for item in query["sort"]]
    sorted_ids = {item["targetId"] for item in query["sort"]}
    sort_parts.extend(f'{quote(aliases[item["id"]])} ASC NULLS LAST' for item in dimensions if item["id"] not in sorted_ids)
    if sort_parts:
        sql += "\nORDER BY\n    " + ",\n    ".join(sort_parts)
    sql += "\nLIMIT %s"
    parameters.append(query["limit"] + 1)
    return {"sql": sql, "parameters": parameters, "columns": output, "aliases": [aliases[item["id"]] for item in dimensions + measures]}
