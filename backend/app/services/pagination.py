"""Opaque keyset cursors: base64url(JSON) of the last row's (sort value, id)."""

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from app.domain.errors import DomainError


class InvalidCursor(DomainError):
    code = "VALIDATION_ERROR"


def encode_cursor(value: Any, row_id: uuid.UUID) -> str:
    if isinstance(value, datetime):
        value = value.isoformat()
    raw = json.dumps({"v": value, "id": str(row_id)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, *, as_datetime: bool) -> tuple[Any, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        value = datetime.fromisoformat(payload["v"]) if as_datetime else int(payload["v"])
        return value, uuid.UUID(payload["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidCursor("Invalid pagination cursor") from exc
