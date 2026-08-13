from __future__ import annotations

import hashlib
import json
import re
from typing import Any


TRANSIENT_KEYS = {
    "x", "y", "color", "fingerprint", "importedAt", "importTime", "updatedAt",
    "profileId", "sourceProfileId", "liveOid", "layout", "timeZone",
}


class PostgresServiceError(Exception):
    """Safe error suitable for direct serialization by an HTTP adapter."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        error = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


class ValidationError(PostgresServiceError):
    def __init__(self, message: str):
        super().__init__(400, "validation_error", message)


class NotFoundError(PostgresServiceError):
    def __init__(self, message: str):
        super().__init__(404, "not_found", message)


class ConflictError(PostgresServiceError):
    def __init__(self, code: str, message: str):
        super().__init__(409, code, message)


def postgres_error_diagnostic(exc: Exception) -> dict[str, Any]:
    """Return a bounded PostgreSQL diagnostic safe for an HTTP response."""
    diagnostic = getattr(exc, "diag", None)
    result: dict[str, Any] = {}
    sqlstate = getattr(exc, "sqlstate", None)
    if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        result["sqlstate"] = sqlstate
    for source, target in (("message_primary", "message"), ("message_detail", "detail"), ("message_hint", "hint")):
        value = getattr(diagnostic, source, None)
        if isinstance(value, str):
            value = " ".join(value[:4000].split())[:1000]
            if value:
                result[target] = value
    position = getattr(diagnostic, "statement_position", None)
    if isinstance(position, str) and position.isdigit():
        position = int(position)
    if isinstance(position, int) and not isinstance(position, bool) and 1 <= position <= 100_000:
        result["position"] = position
    return result


def quote_identifier(value: str) -> str:
    """Always quote a PostgreSQL identifier, including ordinary names."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValidationError("SQL identifier must be a non-empty string")
    return '"' + value.replace('"', '""') + '"'


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical_value(item)
            for key, item in sorted(value.items())
            if key not in TRANSIENT_KEYS
        }
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def canonical_fingerprint(schema: dict[str, Any]) -> str:
    """Hash semantic schema content while ignoring canvas/transient fields."""
    canonical = json.dumps(
        _canonical_value(schema), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
