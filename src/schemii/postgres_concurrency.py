from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Iterator

from .postgres_common import PostgresServiceError


EXECUTION_CAPACITIES = {
    "catalog": 8,
    "read": 8,
    "console": 4,
    "write": 1,
}


@dataclass
class _ClassState:
    capacity: int
    active: int = 0
    admitted: int = 0
    rejected: int = 0
    completed: int = 0
    failed: int = 0
    wait_ms: float = 0.0
    run_ms: float = 0.0


class PostgresExecutionController:
    """Process-local admission control for independent PostgreSQL connections."""

    def __init__(
        self,
        capacities: dict[str, int] | None = None,
        *,
        global_capacity: int = 12,
        clock: Callable[[], float] = time.perf_counter,
    ):
        configured = dict(EXECUTION_CAPACITIES if capacities is None else capacities)
        if set(configured) != set(EXECUTION_CAPACITIES) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in configured.values()
        ):
            raise ValueError("PostgreSQL execution capacities are invalid")
        if isinstance(global_capacity, bool) or not isinstance(global_capacity, int) or global_capacity < 1:
            raise ValueError("global PostgreSQL execution capacity must be positive")
        self._states = {name: _ClassState(value) for name, value in configured.items()}
        self._global_capacity = global_capacity
        self._global_active = 0
        self._closed = False
        self._lock = threading.Lock()
        self._local = threading.local()
        self._clock = clock

    @contextmanager
    def execution(self, execution_class: str) -> Iterator[None]:
        if getattr(self._local, "active", False):
            yield
            return
        requested_at = self._clock()
        with self._lock:
            state = self._states.get(execution_class)
            if state is None:
                raise ValueError("unknown PostgreSQL execution class")
            if self._closed:
                raise PostgresServiceError(
                    503, "postgres_execution_unavailable",
                    "PostgreSQL execution is unavailable while the service is stopping",
                    {"executionClass": execution_class, "retryable": True},
                )
            if state.active >= state.capacity or self._global_active >= self._global_capacity:
                state.rejected += 1
                raise PostgresServiceError(
                    429, "postgres_execution_busy",
                    "PostgreSQL execution capacity is busy; retry the request",
                    {"executionClass": execution_class, "retryable": True},
                )
            state.active += 1
            state.admitted += 1
            state.wait_ms += max(0.0, (self._clock() - requested_at) * 1000)
            self._global_active += 1
        started_at = self._clock()
        failed = False
        self._local.active = True
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._local.active = False
            elapsed = max(0.0, (self._clock() - started_at) * 1000)
            with self._lock:
                state.active -= 1
                state.completed += 1
                state.failed += int(failed)
                state.run_ms += elapsed
                self._global_active -= 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": "stopping" if self._closed else "available",
                "global": {"active": self._global_active, "capacity": self._global_capacity},
                "classes": {
                    name: {
                        "active": state.active, "capacity": state.capacity,
                        "admitted": state.admitted, "rejected": state.rejected,
                        "completed": state.completed, "failed": state.failed,
                        "waitMs": round(state.wait_ms, 3), "runMs": round(state.run_ms, 3),
                    }
                    for name, state in self._states.items()
                },
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True


def postgres_execution(execution_class: str):
    if execution_class not in EXECUTION_CAPACITIES:
        raise ValueError("unknown PostgreSQL execution class")

    def decorate(function):
        @wraps(function)
        def admitted(self, *args, **kwargs):
            with self.execution(execution_class):
                return function(self, *args, **kwargs)
        return admitted
    return decorate
