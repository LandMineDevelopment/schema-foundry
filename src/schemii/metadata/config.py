from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_APPLICATION_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


@dataclass(frozen=True)
class MetadataConfig:
    dsn: str
    application_name: str = "schemii-metadata"
    connect_timeout: int = 5
    max_json_bytes: int = 1024 * 1024
    password_file: str = ""

    def __post_init__(self) -> None:
        dsn = self.dsn.strip() if isinstance(self.dsn, str) else ""
        if not dsn:
            raise ValueError("metadata dsn is required")
        if not (dsn.startswith("postgresql://") or dsn.startswith("postgres://") or "=" in dsn):
            raise ValueError("metadata dsn must be a PostgreSQL connection string")
        if not _APPLICATION_NAME.fullmatch(self.application_name):
            raise ValueError("metadata application_name is invalid")
        if isinstance(self.connect_timeout, bool) or not 1 <= self.connect_timeout <= 60:
            raise ValueError("metadata connect_timeout must be between 1 and 60")
        if isinstance(self.max_json_bytes, bool) or not 1024 <= self.max_json_bytes <= 1024 * 1024:
            raise ValueError("metadata max_json_bytes must be between 1024 and 1048576")
        object.__setattr__(self, "dsn", dsn)
        if self.password_file and not Path(self.password_file).is_absolute():
            raise ValueError("metadata password_file must be absolute")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MetadataConfig":
        values = os.environ if env is None else env
        dsn = values.get("SCHEMII_METADATA_DSN", "")
        try:
            timeout = int(values.get("SCHEMII_METADATA_CONNECT_TIMEOUT", "5"))
            max_json = int(values.get("SCHEMII_METADATA_MAX_JSON_BYTES", str(1024 * 1024)))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata numeric environment settings must be integers") from exc
        return cls(
            dsn=dsn,
            password_file=values.get("SCHEMII_METADATA_PASSWORD_FILE", ""),
            application_name=values.get("SCHEMII_METADATA_APPLICATION_NAME", "schemii-metadata"),
            connect_timeout=timeout,
            max_json_bytes=max_json,
        )
