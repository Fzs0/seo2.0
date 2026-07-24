"""Social content, Hubstudio delivery, and local extension endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.secrets import ConnectorSecretError
from app.core.database import get_db
from app.services.social_connection_service import (
    create_social_connection,
    list_social_connections,
    test_social_connection,
)
from app.services.social_delivery_service import confirm_social_job, confirm_social_jobs, prepare_social_job
from app.services.social_extension_service import (
    authenticate_extension,
    create_pairing_code,
    heartbeat_extension,
    list_extension_devices,
    next_extension_task,
    offer_extension_task,
    pair_extension,
    record_extension_stage,
)
from app.services.social_publishing_service import (
    activate_binding,
    cancel_publish_job,
    create_binding,
    create_decision,
    create_package,
    create_package_version,
    create_publish_job,
    list_bindings,
    list_packages,
    list_publish_jobs,
    review_package,
    submit_package,
)
from app.services.social_platform_registry import list_platform_specs

router = APIRouter(prefix="/social", tags=["social-publishing"])


class SocialDecisionPayload(BaseModel):
    allow_publish: bool
    target_platforms: list[str] = Field(min_length=1)
    recommended_content_type: str = Field(min_length=1, max_length=100)
    recommended_topic: str = Field(min_length=1, max_length=500)
    angle: str = Field(default="", max_length=1000)
    potential_score: float = Field(ge=0, le=100)
    platform_fit_reason: str = Field(default="", max_length=2000)
    account_group_fit: str = Field(default="", max_length=500)
    risk_score: float = Field(ge=0, le=100)
    forbidden_expressions: list[str] = Field(default_factory=list, max_length=100)
    recommended_cta: str = Field(default="", max_length=1000)
    next_action: str = Field(default="", max_length=1000)
    requires_human_confirmation: bool = True
    next_iteration: str = Field(default="", max_length=2000)


class SocialDecisionCreateBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=1000)
    language_code: str = Field(min_length=2, max_length=20)
    requested_platforms: list[str] = Field(min_length=1, max_length=20)
    decision: SocialDecisionPayload
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(default="api", min_length=1, max_length=200)

    @model_validator(mode="after")
    def ensure_targets_were_requested(self) -> "SocialDecisionCreateBody":
        requested = {value.strip().casefold() for value in self.requested_platforms}
        targets = {value.strip().casefold() for value in self.decision.target_platforms}
        if not targets.issubset(requested):
            raise ValueError("decision target_platforms must be a subset of requested_platforms")
        return self


class SocialPackageCreateBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    decision_id: str
    platform: str = Field(min_length=1, max_length=50)
    content_type: str = Field(min_length=1, max_length=100)
    language_code: str = Field(min_length=2, max_length=20)
    title: str = Field(default="", max_length=1000)
    body: str = Field(min_length=1)
    media: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    risk_score: float = Field(default=0, ge=0, le=100)
    created_by: str = Field(default="api", min_length=1, max_length=200)


class SocialPackageVersionBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    language_code: str = Field(min_length=2, max_length=20)
    title: str = Field(default="", max_length=1000)
    body: str = Field(min_length=1)
    media: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    risk_score: float = Field(default=0, ge=0, le=100)
    created_by: str = Field(default="api", min_length=1, max_length=200)


class SocialVersionActionBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class SocialReviewBody(SocialVersionActionBody):
    approve: bool
    actor: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class SocialBindingCreateBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=50)
    connection_id: str
    display_name: str = Field(min_length=1, max_length=300)
    container_code: str = Field(default="", max_length=300)
    container_name: str = Field(default="", max_length=300)
    binding_name: str = Field(min_length=1, max_length=300)
    default_publish_url: HttpUrl
    delivery_channel: str = Field(default="hubstudio_browser", max_length=100)
    publish_adapter: str = Field(min_length=1, max_length=200)
    enabled: bool = False

    @model_validator(mode="after")
    def require_https(self) -> "SocialBindingCreateBody":
        if self.default_publish_url.scheme != "https":
            raise ValueError("default_publish_url must use https")
        return self


class SocialPublishJobCreateBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    package_id: str
    binding_id: str
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=300)


class SocialBusinessActionBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)


class SocialBatchConfirmBody(SocialBusinessActionBody):
    job_ids: list[str] = Field(min_length=1, max_length=100)


class SocialExtensionPairingCodeBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    container_code: str = Field(min_length=1, max_length=300)
    display_name: str = Field(min_length=1, max_length=300)


class SocialExtensionPairBody(BaseModel):
    pairing_code: str = Field(min_length=8, max_length=20)
    extension_instance_id: str = Field(min_length=16, max_length=200)


class SocialExtensionOfferBody(SocialBusinessActionBody):
    device_id: str


class SocialExtensionStageBody(BaseModel):
    sequence_no: int = Field(ge=1)
    stage: str = Field(min_length=1, max_length=100)
    details: dict[str, Any] = Field(default_factory=dict)


class SocialConnectionCreateBody(BaseModel):
    business_id: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=300)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict, repr=False)


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


async def _extension_device(
    session: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return await authenticate_extension(session, authorization)
    except ValueError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error


@router.post("/extension/pairing-codes", status_code=201)
async def create_extension_pairing_code_route(
    body: SocialExtensionPairingCodeBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await create_pairing_code(session, **body.model_dump())
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/extension/pair")
async def pair_extension_route(
    body: SocialExtensionPairBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await pair_extension(session, **body.model_dump())
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/extension/heartbeat")
async def extension_heartbeat_route(
    device: dict[str, Any] = Depends(_extension_device),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await heartbeat_extension(session, device)


@router.get("/extension/devices")
async def list_extension_devices_route(
    business_id: str,
    container_code: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await list_extension_devices(
        session, business_id=business_id, container_code=container_code
    )


@router.post("/extension/tasks/{job_id}/offer")
async def offer_extension_task_route(
    job_id: str,
    body: SocialExtensionOfferBody,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await offer_extension_task(
            session,
            job_id=job_id,
            business_id=body.business_id,
            device_id=body.device_id,
        )
    except ValueError as error:
        raise _bad_request(error) from error


@router.get("/extension/tasks/next")
async def next_extension_task_route(
    device: dict[str, Any] = Depends(_extension_device),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await next_extension_task(session, device=device)


@router.post("/extension/tasks/{job_id}/stage")
async def record_extension_task_stage_route(
    job_id: str,
    body: SocialExtensionStageBody,
    device: dict[str, Any] = Depends(_extension_device),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await record_extension_stage(
            session, device=device, job_id=job_id, **body.model_dump()
        )
    except ValueError as error:
        raise _bad_request(error) from error


@router.get("/platforms")
async def list_social_platforms_route() -> dict[str, Any]:
    items = list_platform_specs()
    return {"items": items, "total": len(items)}


@router.post("/connections", status_code=201)
async def create_social_connection_route(
    body: SocialConnectionCreateBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await create_social_connection(
            session, business_id=body.business_id, platform=body.platform,
            name=body.name, config=body.config, secrets=body.secrets,
        )
    except (ValueError, ConnectorSecretError, IntegrityError) as error:
        raise _bad_request(error) from error


@router.get("/connections")
async def list_social_connections_route(
    business_id: str, platform: str | None = None,
    limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await list_social_connections(session, business_id=business_id, platform=platform, limit=limit)
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/connections/{connection_id}/test")
async def test_social_connection_route(
    connection_id: str, body: SocialBusinessActionBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await test_social_connection(session, connection_id=connection_id, business_id=body.business_id)
    except (ValueError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("/decisions", status_code=201)
async def create_social_decision_route(body: SocialDecisionCreateBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await create_decision(session, body.model_dump(mode="json"))
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/packages", status_code=201)
async def create_social_package_route(body: SocialPackageCreateBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await create_package(session, body.model_dump(mode="json"))
    except (ValueError, IntegrityError) as error:
        raise _bad_request(error) from error


@router.post("/packages/{package_id}/versions", status_code=201)
async def create_social_package_version_route(package_id: str, body: SocialPackageVersionBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await create_package_version(
            session, package_id, business_id=body.business_id,
            payload=body.model_dump(mode="json", exclude={"business_id"}),
        )
    except (ValueError, IntegrityError) as error:
        raise _bad_request(error) from error


@router.get("/packages")
async def list_social_packages_route(business_id: str, status: str | None = None, limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await list_packages(session, business_id=business_id, status=status, limit=limit)


@router.post("/packages/{package_id}/submit-review")
async def submit_social_package_route(package_id: str, body: SocialVersionActionBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await submit_package(session, package_id, business_id=body.business_id, expected_version=body.expected_version)
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/packages/{package_id}/review")
async def review_social_package_route(package_id: str, body: SocialReviewBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await review_package(
            session, package_id, business_id=body.business_id, expected_version=body.expected_version,
            approve=body.approve, actor=body.actor, note=body.note,
        )
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/bindings", status_code=201)
async def create_social_binding_route(body: SocialBindingCreateBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        payload = body.model_dump(mode="json")
        payload["default_publish_url"] = str(body.default_publish_url)
        return await create_binding(session, payload)
    except (ValueError, IntegrityError) as error:
        raise _bad_request(error) from error


@router.get("/bindings")
async def list_social_bindings_route(business_id: str, platform: str | None = None, limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await list_bindings(session, business_id=business_id, platform=platform, limit=limit)
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/bindings/{binding_id}/activate")
async def activate_social_binding_route(
    binding_id: str, body: SocialBusinessActionBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await activate_binding(session, binding_id, business_id=body.business_id)
    except (ValueError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("/publish-jobs", status_code=201)
async def create_social_publish_job_route(body: SocialPublishJobCreateBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await create_publish_job(
            session, business_id=body.business_id, package_id=body.package_id,
            binding_id=body.binding_id, expected_version=body.expected_version,
            idempotency_key=body.idempotency_key,
        )
    except (ValueError, IntegrityError) as error:
        raise _bad_request(error) from error


@router.get("/publish-jobs")
async def list_social_publish_jobs_route(business_id: str, status: str | None = None, limit: int = Query(100, ge=1, le=500), session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await list_publish_jobs(session, business_id=business_id, status=status, limit=limit)


@router.post("/publish-jobs/{job_id}/cancel")
async def cancel_social_publish_job_route(job_id: str, body: SocialBusinessActionBody, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await cancel_publish_job(session, job_id, business_id=body.business_id)
    except ValueError as error:
        raise _bad_request(error) from error


@router.post("/publish-jobs/{job_id}/prepare")
async def prepare_social_publish_job_route(
    job_id: str, body: SocialBusinessActionBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await prepare_social_job(session, job_id, business_id=body.business_id)
    except (ValueError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("/publish-jobs/{job_id}/confirm")
async def confirm_social_publish_job_route(
    job_id: str, body: SocialBusinessActionBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await confirm_social_job(session, job_id, business_id=body.business_id)
    except (ValueError, ConnectorSecretError) as error:
        raise _bad_request(error) from error


@router.post("/publish-jobs/confirm-batch")
async def confirm_social_publish_jobs_route(
    body: SocialBatchConfirmBody, session: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await confirm_social_jobs(session, body.job_ids, business_id=body.business_id)
    except ValueError as error:
        raise _bad_request(error) from error
