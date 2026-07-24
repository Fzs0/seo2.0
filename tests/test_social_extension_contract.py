from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.social_extension_service import (
    hash_extension_secret,
    list_extension_devices,
    normalize_pairing_code,
)


ROOT = Path(__file__).resolve().parents[1]


class _DeviceMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _DeviceResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _DeviceMappings:
        return _DeviceMappings(self._rows)


class _DeviceSession:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def execute(self, *_args: object, **_kwargs: object) -> _DeviceResult:
        return _DeviceResult(self._rows)


def test_pairing_code_normalization_and_hashing_are_stable() -> None:
    assert normalize_pairing_code(" abcd-2345 ") == "ABCD2345"
    assert hash_extension_secret("secret", pepper="p" * 32) == hash_extension_secret(
        "secret", pepper="p" * 32
    )
    assert hash_extension_secret("secret", pepper="p" * 32) != hash_extension_secret(
        "different", pepper="p" * 32
    )


def test_extension_devices_are_collapsed_to_one_row_per_hubstudio_environment() -> None:
    now = datetime.now(timezone.utc)
    session = _DeviceSession(
        [
            {
                "id": "new-device",
                "business_id": "exdivo",
                "container_code": "1724350465",
                "display_name": "Hubstudio 1724350465",
                "environment_name": "Hubstudio 2847",
                "last_seen_at": now,
                "created_at": now,
            },
            {
                "id": "old-device",
                "business_id": "exdivo",
                "container_code": "1724350465",
                "display_name": "Hubstudio 1724350465",
                "environment_name": "Hubstudio 2847",
                "last_seen_at": now - timedelta(minutes=5),
                "created_at": now - timedelta(minutes=10),
            },
            {
                "id": "other-device",
                "business_id": "exdivo",
                "container_code": "1729002234",
                "display_name": "Hubstudio 1729002234",
                "environment_name": "Hubstudio 2874",
                "last_seen_at": now - timedelta(seconds=10),
                "created_at": now,
            },
        ]
    )

    payload = asyncio.run(
        list_extension_devices(session, business_id="exdivo")  # type: ignore[arg-type]
    )

    assert payload["total"] == 2
    assert [item["environment_name"] for item in payload["items"]] == [
        "Hubstudio 2847",
        "Hubstudio 2874",
    ]
    assert payload["items"][0]["id"] == "new-device"
    assert payload["items"][0]["paired_instances"] == 2


def test_extension_manifest_is_loadable_and_local_only_for_backend() -> None:
    manifest = json.loads(
        (ROOT / "hubstudio-social-extension/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["manifest_version"] == 3
    assert "http://127.0.0.1:8000/*" in manifest["host_permissions"]
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["action"]["default_popup"] == "popup/popup.html"


def test_extension_migration_keeps_pairing_secrets_hashed() -> None:
    migration = (ROOT / "db/migrations/027_social_extension_pairing.sql").read_text(
        encoding="utf-8"
    )
    assert "social.extension_pairing_codes" in migration
    assert "social.extension_devices" in migration
    assert "code_hash" in migration
    assert "token_hash" in migration
    assert "pairing_code text" not in migration.lower()
    assert "access_token text" not in migration.lower()


def test_extension_routes_are_registered_under_social_router() -> None:
    source = (ROOT / "app/api/v1/social.py").read_text(encoding="utf-8")
    for route in (
        '"/extension/pairing-codes"',
        '"/extension/pair"',
        '"/extension/heartbeat"',
        '"/extension/tasks/next"',
        '"/extension/tasks/{job_id}/stage"',
    ):
        assert route in source
