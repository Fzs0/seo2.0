from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from app.services import oemapps_page_service


PAGE = {
    "id": 1234,
    "handle": "about-us",
    "title": "About Us",
    "meta_title": "About ExDivo",
    "meta_descript": "Learn about ExDivo.",
    "meta_keywords": ["about", "ExDivo"],
    "is_default": 0,
    "from_id": 0,
    "from_name": "",
    "content": "<h1>About ExDivo</h1>",
}


class DummySession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakePages:
    current = deepcopy(PAGE)

    def __init__(self, token: str) -> None:
        assert token == "site-token"

    async def aclose(self) -> None:
        return None

    async def list_pages(self) -> list[dict[str, Any]]:
        return [deepcopy(self.current)]

    async def get_page(self, page_id: str) -> dict[str, Any]:
        assert str(page_id) == "1234"
        return deepcopy(self.current)

    async def update_page(
        self, page_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        assert str(page_id) == "1234"
        self.current = {"id": 1234, **deepcopy(body)}
        type(self).current = self.current
        return {"code": 0, "data": True}


@pytest.fixture(autouse=True)
def reset_fake_page(monkeypatch: pytest.MonkeyPatch) -> None:
    FakePages.current = deepcopy(PAGE)

    async def load_runtime(
        _session: DummySession, _connector_id: str
    ) -> tuple[dict[str, Any], str]:
        return {"id": "connector-id"}, "site-token"

    monkeypatch.setattr(oemapps_page_service, "load_oemapps_runtime", load_runtime)
    monkeypatch.setattr(oemapps_page_service, "OemAppsPages", FakePages)


@pytest.mark.asyncio
async def test_preview_then_execute_updates_and_verifies_complete_page() -> None:
    session = DummySession()
    patch = {
        "title": "About Our Company",
        "meta_description": "Updated company introduction.",
        "content": "<h1>About Our Company</h1>",
    }
    preview = await oemapps_page_service.preview_page_update(
        session, "connector-id", "1234", patch
    )
    result = await oemapps_page_service.execute_page_update(
        session,
        "connector-id",
        "1234",
        patch,
        expected_snapshot_hash=preview["expected_snapshot_hash"],
        confirm=True,
    )

    assert result["ok"] is True
    assert result["verification_errors"] == []
    assert result["page"]["title"] == "About Our Company"
    assert result["page"]["meta_descript"] == "Updated company introduction."
    assert FakePages.current["handle"] == PAGE["handle"]
    assert session.commits == 2


@pytest.mark.asyncio
async def test_execute_rejects_missing_confirmation_and_stale_snapshot() -> None:
    session = DummySession()
    with pytest.raises(ValueError, match="confirm must be true"):
        await oemapps_page_service.execute_page_update(
            session,
            "connector-id",
            "1234",
            {"title": "Updated"},
            expected_snapshot_hash="a" * 64,
            confirm=False,
        )

    with pytest.raises(ValueError, match="changed after preview"):
        await oemapps_page_service.execute_page_update(
            session,
            "connector-id",
            "1234",
            {"title": "Updated"},
            expected_snapshot_hash="a" * 64,
            confirm=True,
        )
