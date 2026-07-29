"""Versioned, read-only custom data connector endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.custom_data import ConnectorConfig, ConnectorError
from app.connectors.oemapps_collections import OemAppsCollectionError
from app.connectors.oemapps_home_seo import OemAppsHomeSeoError
from app.connectors.oemapps_pages import OemAppsPageError
from app.connectors.oemapps_products import OemAppsProductError
from app.connectors.safe_http import ConnectorHttpError
from app.connectors.secrets import ConnectorSecretError
from app.core.database import get_db
from app.services.custom_connector_service import (
    activate_connector,
    configure_oemapps_connector,
    create_connector,
    execute_oemapps_seo_update,
    get_connector,
    list_connector_runs,
    list_connector_versions,
    list_connectors,
    list_oemapps_seo_update_runs,
    preview_oemapps_seo_update,
    preview_config,
    sync_connector_products,
    test_config_request,
    test_connector,
    update_connector,
)
from app.services.oemapps_collection_service import (
    execute_collection_seo_update,
    list_collection_update_runs,
    list_synced_collections,
    preview_collection_seo_update,
    sync_oemapps_collections,
)
from app.services.oemapps_home_seo_service import (
    execute_home_seo_update,
    get_synced_home_seo,
    list_home_seo_update_runs,
    preview_home_seo_update,
    sync_oemapps_home_seo,
)
from app.services.oemapps_page_service import (
    execute_page_update,
    list_oemapps_pages,
    preview_page_update,
)


router = APIRouter(prefix="/connectors", tags=["custom-connectors"])


class ConnectorCreateBody(BaseModel):
    site_id: str
    config: ConnectorConfig
    secrets: dict[str, str] = Field(default_factory=dict, repr=False)


class ConnectorUpdateBody(BaseModel):
    config: ConnectorConfig
    secrets: dict[str, str] = Field(default_factory=dict, repr=False)


class ConnectorPreviewBody(BaseModel):
    config: ConnectorConfig
    response: dict[str, Any]
    limit: int = Field(default=20, ge=1, le=100)


class ConnectorTestRequestBody(BaseModel):
    config: ConnectorConfig
    secrets: dict[str, str] = Field(default_factory=dict, repr=False)
    limit: int = Field(default=20, ge=1, le=100)


class OemAppsConfigureBody(BaseModel):
    site_id: str
    token: str = Field(min_length=1, max_length=500, repr=False)


class OemAppsSeoPatch(BaseModel):
    meta_title: str | None = Field(default=None, max_length=500)
    meta_description: str | None = Field(default=None, max_length=2000)
    meta_keywords: list[str] | None = Field(default=None, max_length=100)
    image_alts: dict[str, str] | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_patch(self) -> "OemAppsSeoPatch":
        seo_fields = {"meta_title", "meta_description", "meta_keywords", "image_alts"}
        if not (self.model_fields_set & seo_fields):
            raise ValueError("at least one SEO field must be provided")
        if self.meta_keywords is not None and any(len(value) > 200 for value in self.meta_keywords):
            raise ValueError("each meta keyword must be at most 200 characters")
        if self.image_alts is not None and any(len(value) > 500 for value in self.image_alts.values()):
            raise ValueError("each image alt must be at most 500 characters")
        return self

    def as_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class OemAppsSeoExecuteBody(OemAppsSeoPatch):
    expected_snapshot_hash: str = Field(min_length=64, max_length=64)
    confirm_variant_recreation: bool = False

    def as_patch(self) -> dict[str, Any]:
        return self.model_dump(
            exclude_unset=True,
            exclude={"expected_snapshot_hash", "confirm_variant_recreation"},
        )


class OemAppsCollectionSeoPatch(BaseModel):
    meta_title: str | None = Field(default=None, max_length=500)
    meta_description: str | None = Field(default=None, max_length=2000)
    meta_keywords: list[str] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_patch(self) -> "OemAppsCollectionSeoPatch":
        fields = {"meta_title", "meta_description", "meta_keywords"}
        if not (self.model_fields_set & fields):
            raise ValueError("at least one collection SEO field must be provided")
        if self.meta_keywords is not None and any(len(value) > 200 for value in self.meta_keywords):
            raise ValueError("each meta keyword must be at most 200 characters")
        return self

    def as_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class OemAppsCollectionSeoExecuteBody(OemAppsCollectionSeoPatch):
    expected_snapshot_hash: str = Field(min_length=64, max_length=64)
    confirm_membership_top_reset: bool = False

    def as_patch(self) -> dict[str, Any]:
        return self.model_dump(
            exclude_unset=True,
            exclude={"expected_snapshot_hash", "confirm_membership_top_reset"},
        )


class OemAppsHomeSeoExecuteBody(OemAppsCollectionSeoPatch):
    expected_snapshot_hash: str = Field(min_length=64, max_length=64)
    confirm: bool = False

    def as_patch(self) -> dict[str, Any]:
        return self.model_dump(
            exclude_unset=True,
            exclude={"expected_snapshot_hash", "confirm"},
        )


class OemAppsPagePatch(BaseModel):
    handle: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=1000)
    meta_title: str | None = Field(default=None, max_length=500)
    meta_description: str | None = Field(default=None, max_length=2000)
    meta_keywords: list[str] | None = Field(default=None, max_length=100)
    is_default: int | None = Field(default=None, ge=0)
    from_id: int | None = Field(default=None, ge=0)
    from_name: str | None = Field(default=None, max_length=1000)
    content: str | None = Field(default=None, max_length=2_000_000)

    @model_validator(mode="after")
    def validate_patch(self) -> "OemAppsPagePatch":
        fields = {
            "handle",
            "title",
            "meta_title",
            "meta_description",
            "meta_keywords",
            "is_default",
            "from_id",
            "from_name",
            "content",
        }
        if not (self.model_fields_set & fields):
            raise ValueError("at least one custom page field must be provided")
        if self.meta_keywords is not None and any(
            len(value) > 200 for value in self.meta_keywords
        ):
            raise ValueError("each meta keyword must be at most 200 characters")
        return self

    def as_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class OemAppsPageExecuteBody(OemAppsPagePatch):
    expected_snapshot_hash: str = Field(min_length=64, max_length=64)
    confirm: bool = False

    def as_patch(self) -> dict[str, Any]:
        return self.model_dump(
            exclude_unset=True,
            exclude={"expected_snapshot_hash", "confirm"},
        )


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@router.post("/preview")
async def preview_connector_route(body: ConnectorPreviewBody) -> dict[str, Any]:
    """Map a provided response sample without saving or making a network request."""
    try:
        return await preview_config(body.config, body.response, limit=body.limit)
    except ConnectorError as error:
        raise _bad_request(error) from error


@router.post("/test-request")
async def test_connector_request_route(body: ConnectorTestRequestBody) -> dict[str, Any]:
    """Run one bounded external request before a connector is saved."""
    try:
        return await test_config_request(body.config, secrets=body.secrets, limit=body.limit)
    except (ConnectorError, ConnectorHttpError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("")
async def create_connector_route(
    body: ConnectorCreateBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await create_connector(session, site_id=body.site_id, config=body.config, secrets=body.secrets)
    except (ValueError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("/oemapps")
async def configure_oemapps_connector_route(
    body: OemAppsConfigureBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Create or rotate a self-hosted OEMApps connector using only its site token."""
    try:
        return await configure_oemapps_connector(
            session, site_id=body.site_id, token=body.token
        )
    except (ValueError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.get("")
async def list_connectors_route(
    site_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_connectors(session, site_id=site_id, limit=limit)


@router.get("/{connector_id}")
async def get_connector_route(
    connector_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await get_connector(session, connector_id)
    if item is None:
        raise HTTPException(status_code=404, detail="connector not found")
    return item


@router.put("/{connector_id}")
async def update_connector_route(
    connector_id: str,
    body: ConnectorUpdateBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await update_connector(session, connector_id, config=body.config, secrets=body.secrets)
    except (ValueError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/test")
async def test_saved_connector_route(
    connector_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await test_connector(session, connector_id)
    except (ValueError, ConnectorError, ConnectorHttpError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/activate")
async def activate_connector_route(
    connector_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await activate_connector(session, connector_id)
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/sync-products")
async def sync_connector_products_route(
    connector_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await sync_connector_products(session, connector_id)
    except (ValueError, ConnectorError, ConnectorHttpError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/sync-collections")
async def sync_oemapps_collections_route(
    connector_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await sync_oemapps_collections(session, connector_id)
    except (ValueError, ConnectorSecretError, OemAppsCollectionError) as error:
        raise _bad_request(error) from error


@router.get("/{connector_id}/collections")
async def list_oemapps_collections_route(
    connector_id: str,
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_synced_collections(session, connector_id, limit=limit)


@router.post("/{connector_id}/collections/{collection_id}/seo-update/preview")
async def preview_oemapps_collection_seo_update_route(
    connector_id: str,
    collection_id: str,
    body: OemAppsCollectionSeoPatch,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await preview_collection_seo_update(
            session, connector_id, collection_id, body.as_patch()
        )
    except (ValueError, ConnectorSecretError, OemAppsCollectionError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/collections/{collection_id}/seo-update/execute")
async def execute_oemapps_collection_seo_update_route(
    connector_id: str,
    collection_id: str,
    body: OemAppsCollectionSeoExecuteBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await execute_collection_seo_update(
            session,
            connector_id,
            collection_id,
            body.as_patch(),
            expected_snapshot_hash=body.expected_snapshot_hash,
            confirm_membership_top_reset=body.confirm_membership_top_reset,
        )
    except (ValueError, ConnectorSecretError, OemAppsCollectionError) as error:
        raise _bad_request(error) from error


@router.get("/{connector_id}/collection-seo-update-runs")
async def collection_seo_update_runs_route(
    connector_id: str,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_collection_update_runs(session, connector_id, limit=limit)


@router.post("/{connector_id}/sync-home-seo")
async def sync_oemapps_home_seo_route(
    connector_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await sync_oemapps_home_seo(session, connector_id)
    except (ValueError, ConnectorSecretError, OemAppsHomeSeoError) as error:
        raise _bad_request(error) from error


@router.get("/{connector_id}/home-seo")
async def get_oemapps_home_seo_route(
    connector_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    result = await get_synced_home_seo(session, connector_id)
    if result is None:
        raise HTTPException(status_code=404, detail="homepage SEO has not been synchronized")
    return result


@router.post("/{connector_id}/home-seo/update/preview")
async def preview_oemapps_home_seo_update_route(
    connector_id: str,
    body: OemAppsCollectionSeoPatch,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await preview_home_seo_update(session, connector_id, body.as_patch())
    except (ValueError, ConnectorSecretError, OemAppsHomeSeoError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/home-seo/update/execute")
async def execute_oemapps_home_seo_update_route(
    connector_id: str,
    body: OemAppsHomeSeoExecuteBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await execute_home_seo_update(
            session,
            connector_id,
            body.as_patch(),
            expected_snapshot_hash=body.expected_snapshot_hash,
            confirm=body.confirm,
        )
    except (ValueError, ConnectorSecretError, OemAppsHomeSeoError) as error:
        raise _bad_request(error) from error


@router.get("/{connector_id}/home-seo-update-runs")
async def home_seo_update_runs_route(
    connector_id: str,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_home_seo_update_runs(session, connector_id, limit=limit)


@router.get("/{connector_id}/pages")
async def list_oemapps_pages_route(
    connector_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await list_oemapps_pages(session, connector_id)
    except (ValueError, ConnectorSecretError, OemAppsPageError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/pages/{page_id}/update/preview")
async def preview_oemapps_page_update_route(
    connector_id: str,
    page_id: str,
    body: OemAppsPagePatch,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await preview_page_update(
            session, connector_id, page_id, body.as_patch()
        )
    except (ValueError, ConnectorSecretError, OemAppsPageError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/pages/{page_id}/update/execute")
async def execute_oemapps_page_update_route(
    connector_id: str,
    page_id: str,
    body: OemAppsPageExecuteBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await execute_page_update(
            session,
            connector_id,
            page_id,
            body.as_patch(),
            expected_snapshot_hash=body.expected_snapshot_hash,
            confirm=body.confirm,
        )
    except (ValueError, ConnectorSecretError, OemAppsPageError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/products/{product_id}/seo-update/preview")
async def preview_oemapps_seo_update_route(
    connector_id: str,
    product_id: str,
    body: OemAppsSeoPatch,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await preview_oemapps_seo_update(
            session, connector_id, product_id, body.as_patch()
        )
    except (ValueError, ConnectorSecretError, OemAppsProductError) as error:
        raise _bad_request(error) from error


@router.post("/{connector_id}/products/{product_id}/seo-update/execute")
async def execute_oemapps_seo_update_route(
    connector_id: str,
    product_id: str,
    body: OemAppsSeoExecuteBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await execute_oemapps_seo_update(
            session,
            connector_id,
            product_id,
            body.as_patch(),
            expected_snapshot_hash=body.expected_snapshot_hash,
            confirm_variant_recreation=body.confirm_variant_recreation,
        )
    except (ValueError, ConnectorSecretError, OemAppsProductError) as error:
        raise _bad_request(error) from error


@router.get("/{connector_id}/seo-update-runs")
async def oemapps_seo_update_runs_route(
    connector_id: str,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_oemapps_seo_update_runs(session, connector_id, limit=limit)


@router.get("/{connector_id}/versions")
async def connector_versions_route(
    connector_id: str, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await list_connector_versions(session, connector_id)


@router.get("/{connector_id}/runs")
async def connector_runs_route(
    connector_id: str,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_connector_runs(session, connector_id, limit=limit)
