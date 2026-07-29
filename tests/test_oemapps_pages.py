from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.oemapps_pages import (
    OemAppsPageError,
    OemAppsPages,
    build_editable_page,
    page_snapshot_hash,
    prepare_page_update,
)


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


def test_prepare_page_update_preserves_complete_body_and_maps_description() -> None:
    before = build_editable_page(PAGE)
    prepared = prepare_page_update(
        PAGE,
        {
            "title": "About Our Company",
            "meta_description": "Updated company introduction.",
            "content": "<h1>About Our Company</h1>",
        },
    )

    assert set(prepared.body) == {
        "handle",
        "title",
        "meta_title",
        "meta_descript",
        "meta_keywords",
        "is_default",
        "from_id",
        "from_name",
        "content",
    }
    assert prepared.body["handle"] == before["handle"]
    assert prepared.body["meta_descript"] == "Updated company introduction."
    assert {change["field"] for change in prepared.changes} == {
        "title",
        "meta_description",
        "content",
    }


def test_page_snapshot_hash_detects_remote_content_changes() -> None:
    first = page_snapshot_hash(PAGE)

    assert first == page_snapshot_hash(dict(PAGE))
    assert first != page_snapshot_hash({**PAGE, "content": "<p>Changed</p>"})


def test_prepare_page_update_rejects_unknown_fields() -> None:
    with pytest.raises(OemAppsPageError, match="unsupported"):
        prepare_page_update(PAGE, {"script": "alert(1)"})


def test_complete_put_is_blocked_when_page_list_omits_content_fields() -> None:
    with pytest.raises(OemAppsPageError, match="complete PUT fields"):
        build_editable_page({"id": 1234, "title": "Summary only", "handle": "summary"})


@pytest.mark.asyncio
async def test_client_uses_documented_page_paths_token_and_complete_put() -> None:
    requests: list[httpx.Request] = []
    current = dict(PAGE)

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PUT":
            current.update(json.loads(request.content))
            return httpx.Response(
                200, json={"code": 0, "msg": "success", "data": True}
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "success",
                "data": {
                    "list": [current],
                    "paginate": {"current": 1, "pageTotal": 1},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OemAppsPages("site-token", client=client)
    pages = await adapter.list_pages()
    page = await adapter.get_page(1234)
    prepared = prepare_page_update(page, {"title": "Updated"})
    await adapter.update_page(1234, prepared.body)
    await client.aclose()

    assert pages[0]["id"] == 1234
    assert [request.method for request in requests] == ["GET", "GET", "PUT"]
    assert str(requests[2].url) == "https://openapi.oemapps.com/pages/1234"
    assert requests[2].headers["token"] == "site-token"
    assert set(json.loads(requests[2].content)) == set(build_editable_page(PAGE))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "data": [PAGE]},
        {"code": 0, "list": [PAGE]},
    ],
)
async def test_page_list_accepts_supported_envelope_variants(
    payload: dict[str, object],
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OemAppsPages("site-token", client=client)
    pages = await adapter.list_pages()
    await client.aclose()

    assert pages == [PAGE]


@pytest.mark.asyncio
async def test_page_list_uses_confirmed_pagesize_parameter_and_reads_all_pages() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        current = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "list": [{**PAGE, "id": current}],
                    "paginate": {"current": current, "pageTotal": 2},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OemAppsPages("site-token", client=client)
    pages = await adapter.list_pages()
    await client.aclose()

    assert [page["id"] for page in pages] == [1, 2]
    assert [request.url.params["pagesize"] for request in requests] == ["200", "200"]
