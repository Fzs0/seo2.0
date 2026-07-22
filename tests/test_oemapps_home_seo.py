from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.oemapps_home_seo import (
    OemAppsHomeSeo,
    home_seo_snapshot_hash,
    prepare_home_seo_update,
)


HOME_SEO = {
    "meta_title": "AVINOTI",
    "meta_descript": "AVINOTI cookware",
    "meta_keywords": ["AVINOTI", "cookware"],
}


def test_prepare_home_seo_update_changes_only_approved_fields() -> None:
    prepared = prepare_home_seo_update(
        HOME_SEO,
        {
            "meta_title": "AVINOTI Titanium Cookware",
            "meta_description": "Shop AVINOTI titanium cookware.",
        },
    )

    assert prepared.body == {
        "meta_title": "AVINOTI Titanium Cookware",
        "meta_descript": "Shop AVINOTI titanium cookware.",
        "meta_keywords": ["AVINOTI", "cookware"],
    }
    assert {item["field"] for item in prepared.changes} == {
        "meta_title",
        "meta_description",
    }


def test_home_seo_snapshot_detects_concurrent_change() -> None:
    assert home_seo_snapshot_hash(HOME_SEO) != home_seo_snapshot_hash(
        {**HOME_SEO, "meta_title": "Changed"}
    )


@pytest.mark.asyncio
async def test_client_uses_fixed_seoplans_path_and_data_only_put() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "msg": "success", "data": HOME_SEO})
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OemAppsHomeSeo("site-token", client=client)
    current = await adapter.get_home_seo()
    await adapter.update_home_seo(current)
    await client.aclose()

    assert [request.method for request in requests] == ["GET", "PUT"]
    assert all(request.url.path == "/seoplans" for request in requests)
    assert requests[1].headers["token"] == "site-token"
    assert json.loads(requests[1].content) == HOME_SEO
