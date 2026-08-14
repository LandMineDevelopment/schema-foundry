from __future__ import annotations

from typing import Any, Callable

from .config import MetadataConfig
from .errors import MetadataStoreError


class MetadataConnectionFactory:
    def __init__(self, config: MetadataConfig, connect: Callable[..., Any] | None = None):
        self.config = config
        self._connect = connect

    def __call__(self):
        try:
            if self._connect is not None:
                return self._connect(
                    self.config.dsn,
                    connect_timeout=self.config.connect_timeout,
                    application_name=self.config.application_name,
                )
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(
                self.config.dsn,
                connect_timeout=self.config.connect_timeout,
                application_name=self.config.application_name,
                row_factory=dict_row,
            )
        except Exception as exc:
            raise MetadataStoreError(
                "metadata_unavailable",
                "Server metadata PostgreSQL is unavailable",
                status=503,
                retryable=True,
            ) from exc
