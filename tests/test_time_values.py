from datetime import datetime, timezone

import pytest

from app.core.time_values import TimestampValueError, require_aware_datetime


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            datetime(2026, 7, 27, 13, 54, 12, 774115, tzinfo=timezone.utc),
            datetime(2026, 7, 27, 13, 54, 12, 774115, tzinfo=timezone.utc),
        ),
        (
            "2026-07-27T13:54:12.774115+00:00",
            datetime(2026, 7, 27, 13, 54, 12, 774115, tzinfo=timezone.utc),
        ),
        (
            "2026-07-27T13:54:12.774115Z",
            datetime(2026, 7, 27, 13, 54, 12, 774115, tzinfo=timezone.utc),
        ),
    ],
)
def test_require_aware_datetime_accepts_runtime_timestamp_shapes(value, expected):
    assert require_aware_datetime(value, field="source_audit_scanned_at") == expected


@pytest.mark.parametrize("value", [None, "", "not-a-time", datetime(2026, 7, 27, 13, 54, 12)])
def test_require_aware_datetime_rejects_missing_invalid_or_naive_values(value):
    with pytest.raises(TimestampValueError) as caught:
        require_aware_datetime(value, field="source_audit_scanned_at")

    assert caught.value.code == "invalid_aware_timestamp"
    assert caught.value.field == "source_audit_scanned_at"
