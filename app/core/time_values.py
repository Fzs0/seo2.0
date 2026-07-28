"""Strict timestamp coercion for values crossing JSON and database boundaries."""
from __future__ import annotations

from datetime import datetime
from typing import Any


class TimestampValueError(ValueError):
    """Stable business-facing error for invalid timestamp values."""

    code = "invalid_aware_timestamp"

    def __init__(self, *, field: str) -> None:
        self.field = field
        super().__init__(f"{self.code}: {field} must be a timezone-aware ISO 8601 timestamp")


def require_aware_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise TimestampValueError(field=field) from error
    else:
        raise TimestampValueError(field=field)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TimestampValueError(field=field)
    return parsed


__all__ = ["TimestampValueError", "require_aware_datetime"]
