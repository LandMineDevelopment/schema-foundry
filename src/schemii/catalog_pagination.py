from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from .postgres_common import PostgresServiceError, ValidationError


DEFAULT_CATALOG_PAGE_SIZE = 100
MAX_CATALOG_PAGE_SIZE = 200


def catalog_page_size(value: Any) -> int:
    if value is None:
        return DEFAULT_CATALOG_PAGE_SIZE
    if isinstance(value, bool):
        raise ValidationError("pageSize must be an integer between 1 and 200")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("pageSize must be an integer between 1 and 200") from exc
    if str(parsed) != str(value) or not 1 <= parsed <= MAX_CATALOG_PAGE_SIZE:
        raise ValidationError("pageSize must be an integer between 1 and 200")
    return parsed


def encode_catalog_cursor(secret: bytes, context: dict[str, Any], after: list[str]) -> str:
    payload = {"v": 1, "context": context, "after": after}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    signature = hmac.new(secret, encoded, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(encoded + signature).decode().rstrip("=")


def decode_catalog_cursor(secret: bytes, cursor: Any, expected_context: dict[str, Any]) -> list[str] | None:
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
        raise PostgresServiceError(400, "invalid_catalog_cursor", "The catalog cursor is malformed")
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        encoded, signature = raw[:-32], raw[-32:]
        if len(raw) <= 32 or not hmac.compare_digest(signature, hmac.new(secret, encoded, hashlib.sha256).digest()):
            raise ValueError
        payload = json.loads(encoded)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise PostgresServiceError(400, "invalid_catalog_cursor", "The catalog cursor is malformed") from None
    if not isinstance(payload, dict) or set(payload) != {"v", "context", "after"} or payload["v"] != 1:
        raise PostgresServiceError(400, "invalid_catalog_cursor", "The catalog cursor is malformed")
    context = payload["context"]
    after = payload["after"]
    if not isinstance(context, dict) or set(context) != set(expected_context) or not isinstance(after, list) or not after or any(not isinstance(item, str) for item in after):
        raise PostgresServiceError(400, "invalid_catalog_cursor", "The catalog cursor is malformed")
    comparison_context = {key: value for key, value in context.items() if key != "catalogFingerprint"}
    expected_comparison = {key: value for key, value in expected_context.items() if key != "catalogFingerprint"}
    if comparison_context != expected_comparison:
        raise PostgresServiceError(409, "catalog_cursor_mismatch", "The catalog cursor belongs to a different target, filter, sort, or page size")
    if context.get("catalogFingerprint") != expected_context.get("catalogFingerprint"):
        raise PostgresServiceError(409, "catalog_cursor_stale", "The PostgreSQL catalog changed; restart catalog paging")
    return after
