from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import UUID


@dataclass(frozen=True)
class ResultLimits:
    max_cell_bytes: int = 64 * 1024
    max_row_bytes: int = 256 * 1024
    max_result_bytes: int = 1024 * 1024
    max_nesting: int = 8
    max_collection_items: int = 1000

    def __post_init__(self) -> None:
        values = (
            self.max_cell_bytes, self.max_row_bytes, self.max_result_bytes,
            self.max_nesting, self.max_collection_items,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
            raise ValueError("result limits must be positive integers")


class ResultLimitError(ValueError):
    def __init__(self, code: str, message: str, *, path: str, limit: int, actual: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {
            "policy": "reject", "path": path, "limit": limit, "actual": actual,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": dict(self.details)}


def json_utf8_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def truncate_utf8(value: str, maximum_bytes: int) -> str:
    if maximum_bytes < 0:
        raise ValueError("maximum_bytes must not be negative")
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum_bytes:
        return value
    return encoded[:maximum_bytes].decode("utf-8", errors="ignore")


class ResultLimiter:
    """Convert database values to bounded JSON values using an explicit limit policy."""

    def __init__(self, limits: ResultLimits = ResultLimits()):
        self.limits = limits

    @staticmethod
    def _event(code: str, path: str, limit: int, actual: int) -> dict[str, Any]:
        return {
            "code": code, "policy": "truncate", "path": path,
            "limit": limit, "actual": actual,
        }

    @staticmethod
    def _scalar(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, (Decimal, UUID)):
            return str(value)
        if isinstance(value, bytes):
            return "\\x" + value.hex()
        return str(value)

    def cell(self, value: Any, *, path: str = "$", events: list[dict[str, Any]] | None = None) -> Any:
        collected = events if events is not None else []
        normalized = self._normalize(value, path, 0, collected, set())
        size = json_utf8_size(normalized)
        if size > self.limits.max_cell_bytes:
            raise ResultLimitError(
                "result_cell_too_large", "Result cell exceeds the byte limit",
                path=path, limit=self.limits.max_cell_bytes, actual=size,
            )
        return normalized

    def row(self, values: Iterable[Any], *, row_index: int = 0) -> tuple[list[Any], list[dict[str, Any]]]:
        events: list[dict[str, Any]] = []
        row = [
            self.cell(value, path=f"$[{row_index}][{column_index}]", events=events)
            for column_index, value in enumerate(values)
        ]
        size = json_utf8_size(row)
        if size > self.limits.max_row_bytes:
            raise ResultLimitError(
                "result_row_too_large", "Result row exceeds the byte limit",
                path=f"$[{row_index}]", limit=self.limits.max_row_bytes, actual=size,
            )
        return row, events

    def rows(
        self,
        rows: Iterable[Any],
        aliases: Sequence[str],
        *,
        max_rows: int,
        envelope: Callable[[list[list[Any]]], Any] | None = None,
    ) -> dict[str, Any]:
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
            raise ValueError("max_rows must be a positive integer")
        bounded: list[list[Any]] = []
        events: list[dict[str, Any]] = []
        truncated = False
        for index, raw_row in enumerate(rows):
            if index >= max_rows:
                truncated = True
                events.append(self._event("result_row_count_truncated", "$", max_rows, index + 1))
                break
            values = [raw_row.get(alias) for alias in aliases] if isinstance(raw_row, Mapping) else list(raw_row)
            candidate, row_events = self.row(values, row_index=index)
            trial = [*bounded, candidate]
            sized_value = envelope(trial) if envelope is not None else trial
            size = json_utf8_size(sized_value)
            if size > self.limits.max_result_bytes:
                truncated = True
                events.extend(row_events)
                events.append(self._event(
                    "result_total_bytes_truncated", "$", self.limits.max_result_bytes, size,
                ))
                break
            bounded.append(candidate)
            events.extend(row_events)
        return {"rows": bounded, "truncated": truncated, "limitEvents": events}

    def records(
        self,
        rows: Iterable[Any],
        aliases: Sequence[str],
        *,
        max_rows: int,
        envelope: Callable[[list[dict[str, Any]]], Any] | None = None,
    ) -> dict[str, Any]:
        limited = self.rows(
            rows, aliases, max_rows=max_rows,
            envelope=(lambda values: envelope([dict(zip(aliases, row)) for row in values])) if envelope else None,
        )
        return {
            **limited,
            "rows": [dict(zip(aliases, row)) for row in limited["rows"]],
        }

    def _normalize(
        self, value: Any, path: str, depth: int, events: list[dict[str, Any]], active: set[int],
    ) -> Any:
        is_mapping = isinstance(value, Mapping)
        is_sequence = isinstance(value, (list, tuple))
        if not is_mapping and not is_sequence:
            scalar = self._scalar(value)
            if isinstance(scalar, str):
                actual = json_utf8_size(scalar)
                if actual > self.limits.max_cell_bytes:
                    low, high = 0, len(scalar)
                    while low < high:
                        middle = (low + high + 1) // 2
                        if json_utf8_size(scalar[:middle]) <= self.limits.max_cell_bytes:
                            low = middle
                        else:
                            high = middle - 1
                    scalar = scalar[:low]
                    events.append(self._event(
                        "result_string_truncated", path, self.limits.max_cell_bytes, actual,
                    ))
            return scalar
        if depth >= self.limits.max_nesting:
            raise ResultLimitError(
                "result_nesting_too_deep", "Result cell exceeds the nesting limit",
                path=path, limit=self.limits.max_nesting, actual=depth + 1,
            )
        identity = id(value)
        if identity in active:
            raise ResultLimitError(
                "result_nesting_cycle", "Result cell contains a cyclic collection",
                path=path, limit=self.limits.max_nesting, actual=depth + 1,
            )
        active.add(identity)
        try:
            if is_mapping:
                items = list(value.items())
                limited = items[:self.limits.max_collection_items]
                if len(items) > len(limited):
                    events.append(self._event(
                        "result_collection_truncated", path,
                        self.limits.max_collection_items, len(items),
                    ))
                return {
                    str(key): self._normalize(item, f"{path}.{key}", depth + 1, events, active)
                    for key, item in limited
                }
            items = list(value)
            limited = items[:self.limits.max_collection_items]
            if len(items) > len(limited):
                events.append(self._event(
                    "result_collection_truncated", path,
                    self.limits.max_collection_items, len(items),
                ))
            return [
                self._normalize(item, f"{path}[{index}]", depth + 1, events, active)
                for index, item in enumerate(limited)
            ]
        finally:
            active.remove(identity)
