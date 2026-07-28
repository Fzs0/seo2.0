import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request

from app.api.v1.response_contract import success


def test_success_envelope_serializes_uuid_and_datetime_values() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )

    response = success(
        request,
        {
            "task_id": UUID("9f12488b-7fc8-4593-9f75-a6a93186995d"),
            "published_at": datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        },
    )

    payload = json.loads(response.body)
    assert payload["data"] == {
        "task_id": "9f12488b-7fc8-4593-9f75-a6a93186995d",
        "published_at": "2026-07-28T10:00:00+00:00",
    }
