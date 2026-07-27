"""Bridge approved on-page strategies to the existing guarded SEO writers."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.custom_connector_service import (
    execute_oemapps_seo_update,
    preview_oemapps_seo_update,
)
from app.services.oemapps_collection_service import (
    execute_collection_seo_update,
    preview_collection_seo_update,
)
from app.services.oemapps_home_seo_service import (
    execute_home_seo_update,
    preview_home_seo_update,
)
from app.services.strategy_effect_service import normalize_canonical_url


_FIELDS = {
    "home": {"meta_title", "meta_description", "meta_keywords"},
    "product": {"meta_title", "meta_description", "meta_keywords", "image_alts"},
    "category": {"meta_title", "meta_description", "meta_keywords"},
}


async def preview_strategy_on_page(
    session: AsyncSession,
    *,
    strategy_task_id: str,
    patch: dict[str, Any],
    generation_mode: str | None = None,
    generation_provider: str | None = None,
    generation_model: str | None = None,
    generation_run_id: str | None = None,
) -> dict[str, Any]:
    target = await _load_target(session, strategy_task_id)
    blocker = _target_blocker(target)
    if blocker:
        return {"status": "blocked", "blocker": blocker, "strategy_task_id": strategy_task_id}
    _validate_patch(target["page_type"], patch)
    decision = dict(target.get("decision") or {})
    provenance = _generation_provenance(
        decision,
        generation_mode=generation_mode,
        generation_provider=generation_provider,
        generation_model=generation_model,
        generation_run_id=generation_run_id,
    )

    page_type = target["page_type"]
    if page_type == "home":
        result = await preview_home_seo_update(session, target["connector_id"], patch)
    elif page_type == "product":
        result = await preview_oemapps_seo_update(
            session, target["connector_id"], target["external_id"], patch
        )
    else:
        result = await preview_collection_seo_update(
            session, target["connector_id"], target["external_id"], patch
        )
    decision.update(
        {
            "execution_status": "previewed",
            "on_page_preview": result,
            "previewed_patch": patch,
            **provenance,
        }
    )
    await _persist_decision_state(session, strategy_task_id, decision)
    return {
        **result,
        "status": "previewed",
        "strategy_task_id": strategy_task_id,
        "page_type": page_type,
    }


async def execute_strategy_on_page(
    session: AsyncSession,
    *,
    strategy_task_id: str,
    patch: dict[str, Any],
    expected_snapshot_hash: str,
    confirm: bool,
    confirm_variant_recreation: bool = False,
    confirm_membership_top_reset: bool = False,
    generation_mode: str | None = None,
    generation_provider: str | None = None,
    generation_model: str | None = None,
    generation_run_id: str | None = None,
) -> dict[str, Any]:
    target = await _load_target(session, strategy_task_id)
    blocker = _target_blocker(target)
    if blocker:
        return {"status": "blocked", "blocker": blocker, "strategy_task_id": strategy_task_id}
    _validate_patch(target["page_type"], patch)
    if not confirm:
        raise ValueError("explicit confirmation is required for on-page execution")

    page_type = target["page_type"]
    decision = dict(target.get("decision") or {})
    provenance = _generation_provenance(
        decision,
        generation_mode=generation_mode,
        generation_provider=generation_provider,
        generation_model=generation_model,
        generation_run_id=generation_run_id,
    )
    target_url = normalize_canonical_url(
        target.get("target_url") or decision.get("target_url"),
        site_url=target.get("base_url"),
        site_domain=target.get("domain"),
    )
    result: dict[str, Any] | None = None
    try:
        if page_type == "home":
            result = await execute_home_seo_update(
                session,
                target["connector_id"],
                patch,
                expected_snapshot_hash=expected_snapshot_hash,
                confirm=True,
            )
        elif page_type == "product":
            if not confirm_variant_recreation:
                raise ValueError("explicit variant recreation confirmation is required")
            result = await execute_oemapps_seo_update(
                session,
                target["connector_id"],
                target["external_id"],
                patch,
                expected_snapshot_hash=expected_snapshot_hash,
                confirm_variant_recreation=confirm_variant_recreation,
            )
        else:
            if not confirm_membership_top_reset:
                raise ValueError("explicit membership top reset confirmation is required")
            result = await execute_collection_seo_update(
                session,
                target["connector_id"],
                target["external_id"],
                patch,
                expected_snapshot_hash=expected_snapshot_hash,
                confirm_membership_top_reset=confirm_membership_top_reset,
            )
        if result.get("ok") is not True:
            raise ValueError("on-page writer did not confirm a successful readback")
    except Exception as error:
        decision.update(
            {
                "execution_status": "failed",
                "execution_error": str(error)[:2000],
                "retryable": True,
                "submitted_patch": patch,
                "operation_target_url": target_url,
                "api_result": result,
            }
        )
        await _persist_decision_state(session, strategy_task_id, decision)
        raise

    preview = decision.get("on_page_preview")
    decision.update(
        {
            "execution_status": "completed",
            "executed_at": _utc_now(),
            "before_snapshot": preview if isinstance(preview, dict) else {},
            "submitted_patch": patch,
            "readback": _readback_from_result(page_type, result),
            "operation_target_url": target_url,
            "api_result": result,
            **provenance,
            "execution_provider": provenance["generation_provider"],
            "execution_model": provenance["generation_model"],
            "retryable": False,
        }
    )
    await _persist_decision_state(session, strategy_task_id, decision)
    return {
        **result,
        "status": "executed",
        "strategy_task_id": strategy_task_id,
        "page_type": page_type,
    }


async def _load_target(session: AsyncSession, strategy_task_id: str) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT t.decision->>'strategy_type' AS strategy_type,
                       t.decision->>'page_type' AS page_type,
                       t.decision->>'subtype' AS subtype,
                       t.decision AS decision, t.target_url,
                       s.status AS site_status, s.strategy_enabled,
                       s.base_url, s.domain,
                       target.connector_id, target.external_id
                  FROM seo_agent.tasks t
                  JOIN seo_agent.sites s ON s.id = t.site_id
                  LEFT JOIN LATERAL (
                    SELECT p.source_connector_id::text AS connector_id,
                           p.external_id::text AS external_id
                      FROM seo_agent.products p
                     WHERE p.site_id = t.site_id
                       AND p.id::text = t.decision->>'target_asset_id'
                    UNION ALL
                    SELECT c.source_connector_id::text, c.external_id::text
                      FROM seo_agent.product_collections c
                     WHERE c.site_id = t.site_id
                       AND c.id::text = t.decision->>'target_asset_id'
                    UNION ALL
                    SELECT h.source_connector_id::text, NULL::text
                      FROM seo_agent.site_home_seo h
                     WHERE h.site_id = t.site_id
                       AND h.id::text = t.decision->>'target_asset_id'
                    LIMIT 1
                  ) target ON true
                 WHERE t.id = CAST(:id AS uuid)
                   AND t.task_type = 'review'
                   AND t.status = 'done'
                   AND t.payload->>'kind' = 'seo_strategy'
                """
            ),
            {"id": strategy_task_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("approved on-page strategy task not found")
    return dict(row)


async def _persist_decision_state(
    session: AsyncSession, strategy_task_id: str, decision: dict[str, Any]
) -> None:
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks /* execution_status */
               SET decision = CAST(:decision AS jsonb), updated_at = now()
             WHERE id = CAST(:id AS uuid)
               AND task_type = 'review'
               AND payload->>'kind' = 'seo_strategy'
            """
        ),
        {
            "id": strategy_task_id,
            "decision": json.dumps(decision, ensure_ascii=False, default=str),
        },
    )
    await session.commit()


def _readback_from_result(page_type: str, result: dict[str, Any]) -> dict[str, Any]:
    if page_type == "home":
        return {"home_seo": result.get("home_seo")}
    return {
        "run_id": result.get("run_id"),
        "verification_errors": result.get("verification_errors") or [],
        f"{page_type}_id": result.get(f"{page_type}_id"),
    }


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _target_blocker(target: dict[str, Any]) -> str | None:
    if target.get("strategy_type") != "on_page_fix":
        raise ValueError("strategy is not an on-page fix")
    if target.get("site_status") != "active" or not target.get("strategy_enabled"):
        return "site_out_of_strategy_scope"
    page_type = target.get("page_type")
    if page_type not in _FIELDS:
        return "unsupported_page_type"
    if target.get("subtype") == "internal_link":
        return "safe_writer_unavailable"
    if not target.get("connector_id"):
        return "safe_connector_unavailable"
    if page_type != "home" and not target.get("external_id"):
        return "remote_object_identity_missing"
    return None


def _validate_patch(page_type: str, patch: dict[str, Any]) -> None:
    if not patch:
        raise ValueError("on-page patch must contain at least one SEO field")
    unsupported = set(patch) - _FIELDS[page_type]
    if unsupported:
        raise ValueError(f"unsupported on-page fields: {sorted(unsupported)}")


def _generation_provenance(
    decision: dict[str, Any],
    *,
    generation_mode: str | None,
    generation_provider: str | None,
    generation_model: str | None,
    generation_run_id: str | None,
) -> dict[str, Any]:
    existing_mode = str(decision.get("generation_mode") or "").strip().casefold()
    if not generation_mode and not existing_mode:
        raise ValueError("generation_mode must explicitly identify manual or model content")
    mode = str(generation_mode or existing_mode).strip().casefold()
    if mode not in {"manual", "model"}:
        raise ValueError("generation_mode must be manual or model")
    if existing_mode and generation_mode and mode != existing_mode:
        raise ValueError("generation provenance does not match the approved preview")
    if mode == "manual":
        return {
            "generation_mode": "manual",
            "generation_provider": None,
            "generation_model": None,
            "generation_run_id": None,
        }

    provider = str(
        generation_provider or decision.get("generation_provider") or ""
    ).strip()
    model = str(generation_model or decision.get("generation_model") or "").strip()
    run_id = generation_run_id or decision.get("generation_run_id")
    invalid = {
        "",
        "unknown",
        "not_exposed_by_runtime",
        "gpt",
        "gpt-4",
        "gpt-5",
        "claude",
        "gemini",
        "latest",
        "default",
        "model",
        "auto",
    }
    if provider.casefold() in invalid:
        raise ValueError("model-generated on-page patch requires an exact generation provider")
    if model.casefold() in invalid or re.fullmatch(
        r"(?:gpt-\d+(?:\.0)?|claude-\d+|gemini-\d+(?:\.\d+)?)",
        model.casefold(),
    ):
        raise ValueError("model-generated on-page patch requires an exact generation model")
    if decision.get("generation_provider") and provider != decision["generation_provider"]:
        raise ValueError("generation provider does not match the approved preview")
    if decision.get("generation_model") and model != decision["generation_model"]:
        raise ValueError("generation model does not match the approved preview")
    if (
        decision.get("generation_run_id")
        and run_id
        and str(run_id) != str(decision["generation_run_id"])
    ):
        raise ValueError("generation run does not match the approved preview")
    return {
        "generation_mode": "model",
        "generation_provider": provider,
        "generation_model": model,
        "generation_run_id": str(run_id) if run_id else None,
    }


__all__ = ["execute_strategy_on_page", "preview_strategy_on_page"]
