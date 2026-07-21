from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.oemapps_collections import (
    OemAppsCollectionError,
    OemAppsCollections,
    build_editable_collection,
    collection_snapshot_hash,
    derive_collection_members,
    prepare_collection_seo_update,
)


COLLECTION = {
    "id": 310721,
    "title": "BRIGHT MIRROR",
    "top_descript": "Top",
    "bottom_descript": "Bottom",
    "handle": "brightmirror",
    "meta_title": "",
    "meta_descript": "",
    "meta_keywords": [],
    "sort_order": {"direction": "desc", "by": "created_at"},
    "manual_mod_index": 0,
    "src": "https://cdn.example.com/collection.png",
    "product_count": 2,
    "updated_at": 1781496685,
}

PRODUCTS = [
    {"id": 11, "collections": [{"id": 310721, "title": "BRIGHT MIRROR"}]},
    {"id": 12, "collections": [{"id": 310721, "title": "BRIGHT MIRROR"}]},
    {"id": 13, "collections": [{"id": 999, "title": "Other"}]},
]


def test_members_are_derived_from_product_inventory() -> None:
    members = derive_collection_members(COLLECTION, PRODUCTS)

    assert members == [{"id": 11, "is_top": 0}, {"id": 12, "is_top": 0}]


def test_member_count_mismatch_blocks_full_put() -> None:
    with pytest.raises(OemAppsCollectionError, match="member count"):
        derive_collection_members({**COLLECTION, "product_count": 3}, PRODUCTS)


def test_prepare_update_preserves_members_and_changes_only_approved_seo() -> None:
    members = derive_collection_members(COLLECTION, PRODUCTS)
    before = build_editable_collection(COLLECTION, members)
    prepared = prepare_collection_seo_update(
        COLLECTION,
        members,
        {
            "meta_title": "Bright Mirror Collection | ExDivo",
            "meta_description": "Shop Bright Mirror products.",
            "meta_keywords": ["bright mirror", "ExDivo"],
        },
    )

    assert prepared.body["products"] == before["products"]
    assert prepared.body["sort_order"] == before["sort_order"]
    assert prepared.body["meta_title"] == "Bright Mirror Collection | ExDivo"
    assert prepared.body["meta_descript"] == "Shop Bright Mirror products."
    assert {item["field"] for item in prepared.changes} == {
        "meta_title",
        "meta_description",
        "meta_keywords",
    }


def test_snapshot_hash_includes_collection_membership() -> None:
    members = derive_collection_members(COLLECTION, PRODUCTS)
    first = collection_snapshot_hash(COLLECTION, members)

    assert first != collection_snapshot_hash(COLLECTION, members[:1])


@pytest.mark.asyncio
async def test_client_uses_fixed_collection_paths_and_token() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/collections/list":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "success",
                    "data": {
                        "list": [COLLECTION],
                        "paginate": {"current": 1, "pageTotal": 1},
                    },
                },
            )
        if request.method == "GET":
            return httpx.Response(200, json={"code": 0, "msg": "success", "data": COLLECTION})
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {"id": 310721}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OemAppsCollections("site-token", client=client)
    listed = await adapter.list_collections(page=1, limit=200)
    detail = await adapter.get_collection(310721)
    body = build_editable_collection(detail, [{"id": 11, "is_top": 0}])
    await adapter.update_collection(310721, body)
    await client.aclose()

    assert len(listed["list"]) == 1
    assert [request.method for request in requests] == ["GET", "GET", "PUT"]
    assert requests[0].url.params["limit"] == "200"
    assert requests[2].headers["token"] == "site-token"
    assert json.loads(requests[2].content)["products"] == [{"id": 11, "is_top": 0}]
