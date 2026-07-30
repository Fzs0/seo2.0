"""Persistent orchestration contract for autonomous SEO strategy runs.

This module deliberately stores runs and events in ``seo_agent.tasks`` JSON so
the public run API can be introduced without a schema migration. It only owns
coordination metadata; creating a run never starts an external operation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

StrategyPlanner = Callable[..., Awaitable[dict[str, Any]]]
SiteDiscoverer = Callable[..., Awaitable[list[dict[str, Any]]]]
CapabilityLoader = Callable[..., Awaitable[dict[str, Any]]]
ActionLoader = Callable[..., Awaitable[list[dict[str, Any]]]]
ActionFactory = Callable[..., Awaitable[dict[str, Any]]]
EvidenceGatherer = Callable[..., Awaitable[dict[str, Any]]]
EvidenceRefresher = Callable[..., Awaitable[list[dict[str, Any]]]]


RUN_KIND = "strategy_run"
EVENT_KIND = "strategy_run_event"
UNIFIED_ACTION_TYPES = frozenset(
    {
        "new_article",
        "update_article",
        "homepage_seo",
        "product_seo",
        "category_seo",
        "product_image_alt",
    }
)
ON_PAGE_UNIFIED_ACTION_TYPES = frozenset(
    {"homepage_seo", "product_seo", "category_seo", "product_image_alt"}
)
RUN_LOCAL_ACTION_TYPES = frozenset(
    {
        "new_article",
        "update_article",
        "on_page_fix",
        "hold",
        "configuration_repair",
    }
)
RUN_LOCAL_SCHEDULE_CLASSES = frozenset(
    {"execute_now", "deferred", "hold", "configuration_repair"}
)
RUN_STATUSES = frozenset(
    {
        "queued",
        "discovering_sites",
        "checking_capabilities",
        "gathering_evidence",
        "planning",
        "refreshing_evidence",
        "replanning",
        "awaiting_approval",
        "executing",
        "verifying",
        "observing",
        "completed",
        "partial",
        "blocked",
        "failed",
        "canceled",
    }
)
TERMINAL_RUN_STATUSES = frozenset({"completed", "partial", "blocked", "failed", "canceled"})
_DB_STATUS = {
    "queued": "queued",
    "discovering_sites": "running",
    "checking_capabilities": "running",
    "gathering_evidence": "running",
    "planning": "running",
    "refreshing_evidence": "running",
    "replanning": "running",
    "awaiting_approval": "blocked",
    "executing": "running",
    "verifying": "running",
    "observing": "running",
    "completed": "done",
    "partial": "done",
    "blocked": "blocked",
    "failed": "failed",
    "canceled": "canceled",
}
_TRANSITIONS = {
    "queued": {"discovering_sites", "canceled", "failed"},
    "discovering_sites": {"checking_capabilities", "blocked", "failed", "canceled"},
    "checking_capabilities": {"gathering_evidence", "blocked", "failed", "canceled"},
    "gathering_evidence": {"planning", "blocked", "failed", "canceled"},
    "planning": {
        "refreshing_evidence",
        "awaiting_approval",
        "executing",
        "observing",
        "partial",
        "blocked",
        "failed",
        "canceled",
    },
    "refreshing_evidence": {"replanning", "blocked", "failed", "canceled"},
    "replanning": {
        "awaiting_approval",
        "executing",
        "observing",
        "partial",
        "blocked",
        "failed",
        "canceled",
    },
    "awaiting_approval": {"executing", "blocked", "failed", "canceled"},
    "executing": {"verifying", "partial", "failed", "canceled"},
    "verifying": {"observing", "completed", "partial", "failed", "canceled"},
    "observing": {"completed", "partial", "failed", "canceled"},
}


def validate_run_transition(current_status: str, next_status: str) -> None:
    if current_status not in RUN_STATUSES or next_status not in RUN_STATUSES:
        raise ValueError("unknown strategy run status")
    if current_status in TERMINAL_RUN_STATUSES:
        raise ValueError(f"strategy run status {current_status} is terminal")
    if next_status not in _TRANSITIONS.get(current_status, set()):
        raise ValueError(
            f"illegal strategy run transition: {current_status} -> {next_status}"
        )


def derive_child_idempotency_key(
    run_id: str, site_id: str, action_type: str, target_identity: str
) -> str:
    material = "\x1f".join((run_id, site_id, action_type, target_identity))
    return "strategy-action:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


async def create_strategy_run(
    session: AsyncSession,
    *,
    business_id: str,
    site_ids: list[str] | None,
    scope: str,
    mode: str,
    requested_by: str,
    idempotency_key: str,
    action_budget: int,
    site_quotas: dict[str, int],
    approval_policy: str,
    root_run_id: str | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    if mode not in {"dry_run", "approval_execution"}:
        raise ValueError("mode must be dry_run or approval_execution")
    lock_key = f"strategy-run:{business_id}:{idempotency_key}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": lock_key},
    )
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, decision, created_at, updated_at,
                       started_at, finished_at
                  FROM seo_agent.tasks
                 WHERE task_type = 'review'
                   AND payload->>'kind' = :kind
                   AND payload->>'business_id' = :business_id
                   AND payload->>'idempotency_key' = :idempotency_key
                 ORDER BY created_at ASC
                 LIMIT 1
                """
            ),
            {
                "kind": RUN_KIND,
                "business_id": business_id,
                "idempotency_key": idempotency_key,
            },
        )
    ).mappings().first()
    if existing:
        await session.commit()
        result = _serialize_run(dict(existing))
        result["idempotency_replayed"] = True
        return result

    run_id = str(uuid4())
    payload = {
        "kind": RUN_KIND,
        "business_id": business_id,
        "site_ids": site_ids,
        "scope": scope,
        "mode": mode,
        "requested_by": requested_by,
        "idempotency_key": idempotency_key,
        "action_budget": action_budget,
        "site_quotas": site_quotas,
        "approval_policy": approval_policy,
        "root_run_id": root_run_id or run_id,
        "attempt": attempt,
    }
    decision = {
        "status": "queued",
        "current_stage": "queued",
        "counts": {
            "executed": 0,
            "awaiting_approval": 0,
            "execute_now": 0,
            "deferred": 0,
            "hold": 0,
            "configuration_repair": 0,
            "failed": 0,
        },
        "site_results": [],
        "exceptions": [],
        "observation_ids": [],
        "next_action": "poll",
        "cancel_requested": False,
    }
    inserted = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, priority, title, payload, decision)
                VALUES
                  (CAST(:id AS uuid), 'review', 'queued', 'P1', :title,
                   CAST(:payload AS jsonb), CAST(:decision AS jsonb))
                RETURNING id::text AS id, payload, decision, created_at, updated_at,
                          started_at, finished_at
                """
            ),
            {
                "id": run_id,
                "title": f"SEO strategy run: {business_id}",
                "payload": json.dumps(payload, ensure_ascii=False),
                "decision": json.dumps(decision, ensure_ascii=False),
            },
        )
    ).mappings().first()
    await session.commit()
    result = _serialize_run(dict(inserted))
    result["idempotency_replayed"] = False
    return result


async def submit_strategy_run_local_options(
    session: AsyncSession,
    *,
    run_id: str,
    requested_by: str,
    idempotency_key: str,
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    """Attach Codex-researched options to a queued Run without a candidate gate."""
    requested_by = requested_by.strip()
    idempotency_key = idempotency_key.strip()
    if not requested_by:
        raise ValueError("requested_by is required")
    if len(idempotency_key) < 8:
        raise ValueError("idempotency key is too short")
    if not options:
        raise ValueError("at least one run-local option is required")
    if len(options) > 200:
        raise ValueError("run-local option count exceeds the safety ceiling")
    request_hash = _stable_json_hash(
        {"requested_by": requested_by, "options": options}
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"strategy-run-options:{run_id}"},
    )
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, decision, created_at, updated_at,
                       started_at, finished_at, error_message
                  FROM seo_agent.tasks
                 WHERE id=CAST(:run_id AS uuid)
                   AND task_type='review'
                   AND payload->>'kind'=:kind
                 FOR UPDATE
                """
            ),
            {"run_id": run_id, "kind": RUN_KIND},
        )
    ).mappings().first()
    if not row:
        raise ValueError("strategy run not found")
    payload = dict(row.get("payload") or {})
    decision = dict(row.get("decision") or {})
    if decision.get("status", "queued") != "queued":
        raise ValueError(
            "run-local options can only be submitted before the Run starts"
        )
    receipt = dict(decision.get("run_local_option_submission") or {})
    if receipt:
        if (
            receipt.get("idempotency_key") == idempotency_key
            and receipt.get("request_hash") == request_hash
        ):
            result = _serialize_run(dict(row))
            result["idempotency_replayed"] = True
            return result
        if receipt.get("idempotency_key") == idempotency_key:
            raise ValueError(
                "idempotency key is already bound to different run-local options"
            )
        raise ValueError(
            "run-local options were already submitted; create a new Run to replace them"
        )

    site_ids = sorted({str(option.get("site_id") or "") for option in options})
    if "" in site_ids:
        raise ValueError("every run-local option requires site_id")
    site_rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, name, site_type, strategy_enabled, market,
                       language_code, domain, base_url, api_base_url
                  FROM seo_agent.sites
                 WHERE business_id=:business_id
                   AND status='active'
                   AND strategy_enabled=TRUE
                   AND id=ANY(CAST(:site_ids AS uuid[]))
                """
            ),
            {
                "business_id": payload.get("business_id"),
                "site_ids": site_ids,
            },
        )
    ).mappings().all()
    sites = [dict(site) for site in site_rows]
    found_site_ids = {str(site["id"]) for site in sites}
    missing_site_ids = sorted(set(site_ids) - found_site_ids)
    if missing_site_ids:
        raise ValueError(
            "run-local option sites are missing, disabled, or outside the business: "
            + ", ".join(missing_site_ids)
        )
    if payload.get("scope") == "selected_sites":
        selected_site_ids = {
            str(site_id) for site_id in (payload.get("site_ids") or [])
        }
        outside_scope = sorted(set(site_ids) - selected_site_ids)
        if outside_scope:
            raise ValueError(
                "run-local option sites are outside the selected Run scope: "
                + ", ".join(outside_scope)
            )
    await _validate_submitted_option_references(
        session,
        business_id=str(payload.get("business_id") or ""),
        options=options,
    )
    normalized = _normalize_submitted_run_local_options(
        business_id=str(payload.get("business_id") or ""),
        sites=sites,
        options=options,
    )
    submitted_at = datetime.now().astimezone().isoformat()
    patch = {
        "run_local_options": normalized,
        "run_local_option_count": len(normalized),
        "run_local_option_submission": {
            "requested_by": requested_by,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "submitted_at": submitted_at,
            "source": "codex_current_research",
        },
        "next_action": "start",
    }
    updated = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET decision=decision || CAST(:patch AS jsonb),
                       updated_at=now()
                 WHERE id=CAST(:run_id AS uuid)
                   AND task_type='review'
                   AND payload->>'kind'=:kind
                   AND COALESCE(decision->>'status','queued')='queued'
                 RETURNING id::text AS id, payload, decision, created_at, updated_at,
                           started_at, finished_at, error_message
                """
            ),
            {
                "run_id": run_id,
                "kind": RUN_KIND,
                "patch": json.dumps(patch, ensure_ascii=False, default=str),
            },
        )
    ).mappings().first()
    if not updated:
        raise ValueError("strategy run changed concurrently before option submission")
    await session.commit()
    result = _serialize_run(dict(updated))
    result["idempotency_replayed"] = False
    return result


def _normalize_submitted_run_local_options(
    *,
    business_id: str,
    sites: list[dict[str, Any]],
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate editorial decisions and convert them to formal-plan inputs."""
    from app.services.strategy_effect_service import strategy_identity

    sites_by_id = {str(site["id"]): site for site in sites}
    normalized: list[dict[str, Any]] = []
    seen_option_ids: set[str] = set()
    for raw in options:
        option = {
            key: (str(value) if key.endswith("_id") and value is not None else value)
            for key, value in dict(raw).items()
        }
        site_id = str(option.get("site_id") or "")
        site = sites_by_id.get(site_id)
        if not site:
            raise ValueError(f"run-local option site is outside the Run: {site_id}")
        action = str(option.get("action") or option.get("strategy_type") or "")
        concrete_action = (
            str(option.get("action_type") or "") if action == "on_page_fix" else action
        )
        schedule_class = str(option.get("schedule_class") or "")
        if action not in RUN_LOCAL_ACTION_TYPES:
            raise ValueError(f"unsupported run-local action: {action}")
        if schedule_class not in RUN_LOCAL_SCHEDULE_CLASSES:
            raise ValueError(
                f"unsupported run-local schedule class: {schedule_class}"
            )
        expected_schedule = {
            "hold": "hold",
            "configuration_repair": "configuration_repair",
        }.get(action)
        if expected_schedule and schedule_class != expected_schedule:
            raise ValueError(
                f"{action} requires schedule_class={expected_schedule}"
            )
        if schedule_class in {"hold", "configuration_repair"} and (
            action != schedule_class
        ):
            raise ValueError(
                "hold/configuration_repair schedule must match the action"
            )
        topic = str(option.get("topic") or option.get("query") or "").strip()
        if action in {"new_article", "update_article"} and not topic:
            raise ValueError("article run-local options require topic")
        target_url = str(option.get("target_url") or "").strip() or None
        if (
            action == "update_article"
            and not option.get("post_id")
            and not option.get("article_id")
            and not target_url
        ):
            raise ValueError(
                "update_article run-local options require a target identity"
            )
        page_type_by_action = {
            "homepage_seo": "homepage",
            "product_seo": "product",
            "category_seo": "category",
            "product_image_alt": "product",
        }
        if action == "on_page_fix":
            expected_page_type = page_type_by_action.get(concrete_action)
            if not expected_page_type:
                raise ValueError(
                    "on_page_fix run-local options require a supported concrete action_type"
                )
            if option.get("page_type") != expected_page_type:
                raise ValueError(
                    f"{concrete_action} requires page_type={expected_page_type}"
                )
            if (
                option.get("page_type") != "homepage"
                and not option.get("target_asset_id")
                and not option.get("remote_object_id")
            ):
                raise ValueError(
                    "product/category on_page_fix options require target_asset_id "
                    "or remote_object_id"
                )
            if not option.get("target_url"):
                raise ValueError(
                    "on_page_fix run-local options require target_url"
                )
            if not option.get("expected_fields"):
                raise ValueError(
                    "on_page_fix run-local options require expected_fields"
                )
        if target_url and not _url_belongs_to_site(target_url, site):
            raise ValueError(
                f"run-local option target URL does not belong to site {site_id}"
            )
        reason = str(option.get("reason") or "").strip()
        user_intent = str(option.get("user_intent") or "").strip()
        evidence = option.get("evidence")
        if not reason or not user_intent or not isinstance(evidence, dict) or not evidence:
            raise ValueError(
                "run-local options require reason, user_intent, and current evidence"
            )
        title = str(option.get("title") or topic or action).strip()
        identity = strategy_identity(
            business_id,
            site_id=site_id,
            market=site.get("market"),
            language_code=site.get("language_code"),
            topic_cluster_id=None,
            post_id=option.get("post_id"),
            article_id=option.get("article_id"),
            query=topic,
            action=concrete_action,
            objective=reason,
            evidence=evidence,
            target_url=target_url,
            site_url=site.get("base_url"),
            site_domain=site.get("domain"),
        )
        option_id = "research-" + _stable_json_hash(
            {
                "site_id": site_id,
                "action": action,
                "action_type": concrete_action,
                "topic": topic,
                "target_url": target_url,
                "target_asset_id": option.get("target_asset_id"),
                "remote_object_id": option.get("remote_object_id"),
                "post_id": option.get("post_id"),
                "article_id": option.get("article_id"),
            }
        )[:32]
        if option_id in seen_option_ids:
            raise ValueError("duplicate run-local option")
        seen_option_ids.add(option_id)
        risk_level = str(option.get("risk_level") or "medium")
        normalized.append(
            {
                "option_id": option_id,
                "option_origin": "codex_research",
                "candidate_id": option.get("candidate_id"),
                "research_candidate_id": None,
                "candidate_influence": option.get("candidate_influence")
                or "not_consulted_or_optional_reference",
                "keyword_id": option.get("keyword_id"),
                "site_id": site_id,
                "site_name": site.get("name"),
                "post_id": option.get("post_id"),
                "article_id": option.get("article_id"),
                "target_url": target_url,
                "target_asset_id": option.get("target_asset_id"),
                "remote_object_id": option.get("remote_object_id"),
                "connector_id": option.get("connector_id"),
                "connector_type": option.get("connector_type"),
                "page_type": option.get("page_type"),
                "expected_fields": list(option.get("expected_fields") or []),
                "editorial_action": (
                    "on_page_fix" if action == "on_page_fix" else action
                ),
                "strategy_type": concrete_action,
                "action_type": concrete_action,
                "query": topic,
                "title": title,
                "reason": reason,
                "recommended_action": reason,
                "user_intent": user_intent,
                "evidence": evidence,
                "execution_evidence": evidence,
                "hypothesis": option.get("hypothesis"),
                "success_metrics": list(option.get("success_metrics") or []),
                "rejected_alternatives": list(
                    option.get("rejected_alternatives") or []
                ),
                "priority": option.get("priority") or "P2",
                "score": float(option.get("opportunity_score") or 0),
                "opportunity_score": float(
                    option.get("opportunity_score") or 0
                ),
                "readiness_score": float(option.get("readiness_score") or 0.5),
                "confidence": float(option.get("readiness_score") or 0.5),
                "risk_score": float(option.get("risk_score") or 0.5),
                "risk_level": risk_level,
                "risk_gate_passed": risk_level != "high",
                "site_configuration_ready": bool(
                    site.get("strategy_enabled")
                    and (site.get("base_url") or site.get("domain"))
                    and site.get("market")
                    and site.get("language_code")
                ),
                "requested_schedule_class": schedule_class,
                "schedule_class": schedule_class,
                "schedule_reason": reason,
                "reevaluate_at": option.get("reevaluate_at"),
                "reevaluation_condition": option.get(
                    "reevaluation_condition"
                ),
                "evidence_level": "current_research",
                **identity,
            }
        )
    return normalized


async def _validate_submitted_option_references(
    session: AsyncSession,
    *,
    business_id: str,
    options: list[dict[str, Any]],
) -> None:
    expected_sites = {
        str(option.get("keyword_id")): str(option.get("site_id"))
        for option in options
        if option.get("keyword_id")
    }
    if expected_sites:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, assigned_site_id::text AS site_id
                      FROM seo_agent.keywords
                     WHERE business_id=:business_id
                       AND id=ANY(CAST(:ids AS uuid[]))
                    """
                ),
                {"business_id": business_id, "ids": list(expected_sites)},
            )
        ).mappings().all()
        found = {str(row["id"]): row.get("site_id") for row in rows}
        for reference_id, site_id in expected_sites.items():
            if reference_id not in found or (
                found[reference_id] and str(found[reference_id]) != site_id
            ):
                raise ValueError(
                    "keyword reference is missing or belongs to another scope"
                )

    expected_candidates = {
        str(option.get("candidate_id")): str(option.get("site_id"))
        for option in options
        if option.get("candidate_id")
    }
    if expected_candidates:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id::text AS id, site_id::text AS site_id
                      FROM seo_agent.tasks
                     WHERE id=ANY(CAST(:ids AS uuid[]))
                       AND task_type='review'
                       AND payload->>'kind'='strategy_candidate'
                       AND payload->>'business_id'=:business_id
                    """
                ),
                {"business_id": business_id, "ids": list(expected_candidates)},
            )
        ).mappings().all()
        found = {str(row["id"]): str(row.get("site_id") or "") for row in rows}
        if any(
            found.get(reference_id) != site_id
            for reference_id, site_id in expected_candidates.items()
        ):
            raise ValueError(
                "candidate reference is missing or belongs to another scope"
            )

    for table, field in (("posts", "post_id"), ("articles", "article_id")):
        expected = {
            str(option.get(field)): str(option.get("site_id"))
            for option in options
            if option.get(field)
        }
        if not expected:
            continue
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT id::text AS id, site_id::text AS site_id
                      FROM seo_agent.{table}
                     WHERE id=ANY(CAST(:ids AS uuid[]))
                    """
                ),
                {"ids": list(expected)},
            )
        ).mappings().all()
        found = {str(row["id"]): str(row.get("site_id") or "") for row in rows}
        if any(
            found.get(reference_id) != site_id
            for reference_id, site_id in expected.items()
        ):
            raise ValueError(
                f"{field} is missing or belongs to another site"
            )

    for option in options:
        if option.get("action") != "on_page_fix":
            continue
        action_type = str(option.get("action_type") or "")
        page_type = str(option.get("page_type") or "")
        target_asset_id = str(option.get("target_asset_id") or "")
        if target_asset_id and not target_asset_id.isdigit():
            raise ValueError("on-page target_asset_id must be a persisted numeric ID")
        remote_object_id = str(option.get("remote_object_id") or "")
        params: dict[str, Any] = {
            "site_id": str(option.get("site_id") or ""),
        }
        if target_asset_id:
            identity_clause = "id=:target_asset_id"
            params["target_asset_id"] = int(target_asset_id)
        elif action_type == "homepage_seo":
            identity_clause = "site_id=CAST(:site_id AS uuid)"
        elif remote_object_id:
            identity_clause = (
                "site_id=CAST(:site_id AS uuid) "
                "AND external_id=:remote_object_id"
            )
            params["remote_object_id"] = remote_object_id
        else:
            raise ValueError("on-page target identity is incomplete")
        if action_type in {"product_seo", "product_image_alt"}:
            query = """
                SELECT p.id::text AS target_asset_id, p.site_id::text AS site_id,
                       p.external_id AS remote_object_id,
                       p.source_connector_id::text AS connector_id, p.url AS target_url,
                       CASE
                         WHEN p.source='shopify_admin_graphql' THEN 'shopify'
                         ELSE lower(COALESCE(v.config->>'adapter', ''))
                       END AS connector_type
                  FROM seo_agent.products p
             LEFT JOIN seo_agent.custom_connectors c
                    ON c.id=p.source_connector_id
             LEFT JOIN seo_agent.custom_connector_versions v
                    ON v.connector_id=c.id AND v.version=c.current_version
                 WHERE p.{identity_clause}
            """
        elif action_type == "category_seo" and page_type == "category":
            query = """
                SELECT p.id::text AS target_asset_id, p.site_id::text AS site_id,
                       p.external_id AS remote_object_id,
                       p.source_connector_id::text AS connector_id, p.url AS target_url,
                       lower(COALESCE(v.config->>'adapter', '')) AS connector_type
                  FROM seo_agent.product_collections p
                  JOIN seo_agent.custom_connectors c
                    ON c.id=p.source_connector_id
                  JOIN seo_agent.custom_connector_versions v
                    ON v.connector_id=c.id AND v.version=c.current_version
                 WHERE p.{identity_clause}
            """
        elif action_type == "homepage_seo" and page_type == "homepage":
            query = """
                SELECT h.id::text AS target_asset_id, h.site_id::text AS site_id,
                       h.site_id::text AS remote_object_id,
                       h.source_connector_id::text AS connector_id,
                       COALESCE(s.base_url, s.domain) AS target_url,
                       lower(COALESCE(v.config->>'adapter', '')) AS connector_type
                  FROM seo_agent.site_home_seo h
                  JOIN seo_agent.sites s ON s.id=h.site_id
                  JOIN seo_agent.custom_connectors c
                    ON c.id=h.source_connector_id
                  JOIN seo_agent.custom_connector_versions v
                    ON v.connector_id=c.id AND v.version=c.current_version
                 WHERE h.{identity_clause}
            """
        else:
            raise ValueError("on-page action_type and page_type do not match")
        row = (
            await session.execute(
                text(query.format(identity_clause=identity_clause)), params
            )
        ).mappings().first()
        if not row or str(row.get("site_id") or "") != str(option.get("site_id")):
            raise ValueError(
                "on-page target is missing or belongs to another site"
            )
        actual = dict(row)
        option["target_asset_id"] = actual.get("target_asset_id")
        for field in ("remote_object_id", "connector_id", "connector_type"):
            supplied = option.get(field)
            if supplied and str(supplied).casefold() != str(
                actual.get(field) or ""
            ).casefold():
                raise ValueError(
                    f"on-page target {field} does not match current data"
                )
            option[field] = actual.get(field)
        supplied_url = str(option.get("target_url") or "").rstrip("/")
        actual_url = str(actual.get("target_url") or "").rstrip("/")
        if supplied_url and supplied_url != actual_url:
            raise ValueError(
                "on-page target target_url does not match current data"
            )
        option["target_url"] = actual.get("target_url")


def _url_belongs_to_site(url: str, site: dict[str, Any]) -> bool:
    target_host = (urlparse(url).hostname or "").lower()
    if not target_host:
        return False
    allowed_hosts = {
        host
        for value in (site.get("domain"), site.get("base_url"))
        if (host := (urlparse(str(value)).hostname or str(value).split("/")[0]).lower())
    }
    return target_host in allowed_hosts


def _stable_json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


async def get_strategy_run(
    session: AsyncSession, *, run_id: str
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, decision, created_at, updated_at,
                       started_at, finished_at, error_message
                  FROM seo_agent.tasks
                 WHERE id = CAST(:run_id AS uuid) AND task_type = 'review'
                   AND payload->>'kind' = :kind
                """
            ),
            {"run_id": run_id, "kind": RUN_KIND},
        )
    ).mappings().first()
    return _serialize_run(dict(row)) if row else None


async def list_strategy_runs(
    session: AsyncSession,
    *,
    business_id: str | None = None,
    status: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if status is not None and status not in RUN_STATUSES:
        raise ValueError("unknown strategy run status")
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, decision, created_at, updated_at,
                       started_at, finished_at, error_message
                 FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = :kind
                   AND (
                     CAST(:business_id AS text) IS NULL
                     OR payload->>'business_id' = CAST(:business_id AS text)
                   )
                   AND (
                     CAST(:status AS text) IS NULL
                     OR decision->>'status' = CAST(:status AS text)
                   )
                   AND (CAST(:from_at AS timestamptz) IS NULL OR created_at >= CAST(:from_at AS timestamptz))
                   AND (CAST(:to_at AS timestamptz) IS NULL OR created_at <= CAST(:to_at AS timestamptz))
                 ORDER BY created_at DESC
                 LIMIT :limit
                """
            ),
            {
                "kind": RUN_KIND,
                "business_id": business_id,
                "status": status,
                "from_at": from_at,
                "to_at": to_at,
                "limit": max(1, min(limit, 500)),
            },
        )
    ).mappings().all()
    return [_serialize_run(dict(row)) for row in rows]


async def transition_strategy_run(
    session: AsyncSession,
    *,
    run_id: str,
    current_status: str,
    next_status: str,
    updates: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    validate_run_transition(current_status, next_status)
    patch = {"status": next_status, "current_stage": next_status, **(updates or {})}
    row = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status = :db_status,
                       decision = decision || CAST(:patch AS jsonb),
                       started_at = CASE WHEN :mark_started THEN COALESCE(started_at, now()) ELSE started_at END,
                       finished_at = CASE WHEN :mark_finished THEN now() ELSE finished_at END,
                       updated_at = now()
                 WHERE id = CAST(:run_id AS uuid) AND task_type = 'review'
                   AND payload->>'kind' = :kind
                   AND decision->>'status' = :current_status
                RETURNING id::text AS id, payload, decision, created_at, updated_at,
                          started_at, finished_at, error_message
                """
            ),
            {
                "run_id": run_id,
                "kind": RUN_KIND,
                "current_status": current_status,
                "next_status": next_status,
                "db_status": _DB_STATUS[next_status],
                "patch": json.dumps(patch, ensure_ascii=False),
                "mark_started": next_status not in {"queued"},
                "mark_finished": next_status in TERMINAL_RUN_STATUSES,
            },
        )
    ).mappings().first()
    if not row:
        if commit:
            await session.commit()
        raise ValueError("strategy run changed concurrently or was not found")
    if commit:
        await session.commit()
    return _serialize_run(dict(row))


async def append_strategy_run_event(
    session: AsyncSession,
    *,
    run_id: str,
    stage: str,
    event_type: str,
    message: str,
    site_id: str | None = None,
    data: dict[str, Any] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    if event_type not in {"stage_started", "stage_completed", "warning", "error", "action"}:
        raise ValueError("unknown strategy run event type")
    event_id = str(uuid4())
    event_key = (
        f"{run_id}:{stage}:{event_type}"
        if event_type in {"stage_started", "stage_completed"}
        else f"{run_id}:{stage}:{event_type}:{event_id}"
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:event_key))"),
        {"event_key": f"strategy-run-event:{event_key}"},
    )
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, created_at
                  FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = :kind
                   AND payload->>'event_key' = :event_key
                 ORDER BY created_at ASC LIMIT 1
                """
            ),
            {"kind": EVENT_KIND, "event_key": event_key},
        )
    ).mappings().first()
    if existing:
        if commit:
            await session.commit()
        return _serialize_event(dict(existing))
    payload = {
        "kind": EVENT_KIND,
        "event_key": event_key,
        "run_id": run_id,
        "stage": stage,
        "event_type": event_type,
        "site_id": site_id,
        "message": message,
        "data": data or {},
    }
    row = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (id, task_type, status, priority, title, payload, decision, finished_at)
                SELECT CAST(:id AS uuid), 'review', 'done', 'P3', :title,
                       CAST(:payload AS jsonb), '{}'::jsonb, now()
                 WHERE EXISTS (
                   SELECT 1 FROM seo_agent.tasks
                    WHERE id = CAST(:run_id AS uuid) AND payload->>'kind' = :run_kind
                 )
                RETURNING id::text AS id, payload, created_at
                """
            ),
            {
                "id": event_id,
                "run_id": run_id,
                "run_kind": RUN_KIND,
                "title": f"Strategy run event: {event_type}",
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )
    ).mappings().first()
    if not row:
        raise ValueError("strategy run not found")
    if commit:
        await session.commit()
    return _serialize_event(dict(row))


async def list_strategy_run_events(
    session: AsyncSession, *, run_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, created_at
                  FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = :kind
                   AND payload->>'run_id' = :run_id
                 ORDER BY created_at ASC
                 LIMIT :limit
                """
            ),
            {"kind": EVENT_KIND, "run_id": run_id, "limit": max(1, min(limit, 1000))},
        )
    ).mappings().all()
    return [_serialize_event(dict(row)) for row in rows]


async def cancel_strategy_run(
    session: AsyncSession, *, run_id: str, requested_by: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if idempotency_key and not await _claim_control_request(
        session, run_id=run_id, operation="cancel", key=idempotency_key
    ):
        replay = await get_strategy_run(session, run_id=run_id)
        return {**replay, "idempotency_replayed": True} if replay else None
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        raise ValueError("strategy run not found")
    if run["status"] in TERMINAL_RUN_STATUSES:
        return run
    # Cancellation is cooperative: running actions are not rolled back or
    # misrepresented. The orchestrator must check this flag before each stage.
    return await transition_strategy_run(
        session,
        run_id=run_id,
        current_status=run["status"],
        next_status="canceled",
        updates={"cancel_requested": True, "cancel_requested_by": requested_by},
    )


async def retry_strategy_run(
    session: AsyncSession,
    *,
    run_id: str,
    requested_by: str,
    idempotency_key: str,
) -> dict[str, Any]:
    original = await get_strategy_run(session, run_id=run_id)
    if not original:
        raise ValueError("strategy run not found")
    if original["status"] not in {"partial", "blocked", "failed", "canceled"}:
        raise ValueError("only partial, blocked, failed, or canceled runs can be retried")
    root_run_id = original.get("root_run_id") or original["run_id"]
    unresolved = (
        await session.execute(
            text(
                """
                WITH lineage AS (
                    SELECT id::text AS run_id
                      FROM seo_agent.tasks
                     WHERE payload->>'kind' = :run_kind
                       AND (
                         id::text = :root_run_id
                         OR payload->>'root_run_id' = :root_run_id
                       )
                )
                SELECT COALESCE(
                    payload->>'remote_outcome',
                    payload->>'recovery_status',
                    decision->>'remote_outcome'
                )
                  FROM seo_agent.tasks
                 WHERE COALESCE(payload->>'run_id', payload->>'strategy_run_id')
                       IN (SELECT run_id FROM lineage)
                   AND COALESCE(
                         payload->>'remote_outcome',
                         payload->>'recovery_status',
                         decision->>'remote_outcome'
                       ) IN (
                         'partially_applied',
                         'unknown_remote_state',
                         'identity_conflict'
                       )
                   AND status IN ('queued', 'running', 'blocked', 'failed')
                 LIMIT 1
                """
            ),
            {"root_run_id": root_run_id, "run_kind": RUN_KIND},
        )
    ).scalar_one_or_none()
    if unresolved:
        raise ValueError(
            "REMOTE_STATE_REQUIRES_MANUAL_READBACK: resolve the uncertain remote outcome before retrying"
        )
    return await create_strategy_run(
        session,
        business_id=original["business_id"],
        site_ids=original.get("site_ids"),
        scope=original["scope"],
        mode=original["mode"],
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        action_budget=original["action_budget"],
        site_quotas=original["site_quotas"],
        approval_policy=original["approval_policy"],
        root_run_id=root_run_id,
        attempt=int(original.get("attempt") or 1) + 1,
    )


async def run_strategy_run(
    session: AsyncSession,
    *,
    run_id: str,
    planner: StrategyPlanner | None = None,
    site_discoverer: SiteDiscoverer | None = None,
    capability_loader: CapabilityLoader | None = None,
    action_loader: ActionLoader | None = None,
    action_factory: ActionFactory | None = None,
    evidence_gatherer: EvidenceGatherer | None = None,
    evidence_refresher: EvidenceRefresher | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Run or resume the safe pre-approval stages.

    Stage outputs are persisted on the run before advancing, so a process can
    resume at the current stage without repeating completed work.  This worker
    intentionally stops at ``awaiting_approval`` or ``blocked``; it never
    performs an unapproved remote write.
    """
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        raise ValueError("strategy run not found")
    if idempotency_key and not await _claim_control_request(
        session, run_id=run_id, operation="start", key=idempotency_key
    ):
        return {**run, "idempotency_replayed": True}
    if run["status"] in TERMINAL_RUN_STATUSES:
        return {**run, "start_replayed": True}
    try:
        if run["status"] in {"awaiting_approval", "executing", "verifying", "observing"}:
            return await reconcile_strategy_run(
                session,
                run_id=run_id,
                action_loader=action_loader or _load_run_actions,
            )
        if run["status"] == "queued":
            try:
                run = await transition_strategy_run(
                    session, run_id=run_id, current_status="queued",
                    next_status="discovering_sites",
                )
            except ValueError:
                current = await get_strategy_run(session, run_id=run_id)
                if current:
                    return {**current, "start_replayed": True}
                raise

        if site_discoverer is None:
            site_discoverer = _discover_run_sites
        if capability_loader is None:
            from app.services.site_capability_service import (
                get_business_site_capabilities,
            )
            capability_loader = get_business_site_capabilities
        evidence_gatherer = evidence_gatherer or _gather_run_evidence
        evidence_refresher = evidence_refresher or _refresh_run_evidence
        action_factory = action_factory or _create_unified_action
        if planner is None:
            from app.services.strategy_service import generate_strategies

            planner = generate_strategies

        if run["status"] == "discovering_sites":
            await _stage_started(session, run_id, "discovering_sites")
            sites = await site_discoverer(
                session, business_id=run["business_id"], scope=run["scope"],
                site_ids=run.get("site_ids"),
            )
            if not sites:
                raise RuntimeError("strategy run discovered no enabled sites")
            run = await _complete_stage(
                session, run_id=run_id, current="discovering_sites",
                next_status="checking_capabilities",
                updates={
                    "discovered_sites": sites,
                    "discovered_site_count": len(sites),
                    "stage_outputs": _merge_stage_output(
                        run, "discovering_sites",
                        {"site_count": len(sites), "site_ids": [str(s["id"]) for s in sites]},
                    ),
                },
            )

        if run["status"] == "checking_capabilities":
            await _stage_started(session, run_id, "checking_capabilities")
            capability_snapshot = await capability_loader(session, run["business_id"])
            wanted = {str(site["id"]) for site in run.get("discovered_sites") or []}
            snapshot_sites = [
                item for item in capability_snapshot.get("sites", [])
                if str(item.get("site_id")) in wanted
            ]
            capability_snapshot = {**capability_snapshot, "sites": snapshot_sites}
            if len(snapshot_sites) != len(wanted):
                raise RuntimeError("capability snapshot does not cover every discovered site")
            run = await _complete_stage(
                session, run_id=run_id, current="checking_capabilities",
                next_status="gathering_evidence",
                updates={
                    "capability_snapshot": capability_snapshot,
                    "stage_outputs": _merge_stage_output(
                        run, "checking_capabilities",
                        {"generated_at": capability_snapshot.get("generated_at"),
                         "site_count": len(snapshot_sites)},
                    ),
                },
            )

        if run["status"] == "gathering_evidence":
            await _stage_started(session, run_id, "gathering_evidence")
            evidence_snapshot = await evidence_gatherer(
                session, business_id=run["business_id"],
                sites=run.get("discovered_sites") or [],
                run=run,
            )
            run = await _complete_stage(
                session, run_id=run_id, current="gathering_evidence",
                next_status="planning",
                updates={
                    "evidence_snapshot": evidence_snapshot,
                    "evidence_snapshot_id": evidence_snapshot.get("snapshot_id"),
                    "stage_outputs": _merge_stage_output(
                        run, "gathering_evidence",
                        {"capability_snapshot_generated_at":
                         (run.get("capability_snapshot") or {}).get("generated_at"),
                         "site_ids": [str(s["id"]) for s in run.get("discovered_sites") or []],
                         "snapshot_id": evidence_snapshot.get("snapshot_id")},
                    ),
                },
            )

        if run["status"] not in {"planning", "replanning"}:
            return {**run, "start_replayed": True}
        stage = run["status"]
        await _stage_started(session, run_id, stage)
        planned = await _plan_all_sites(session, run=run, planner=planner)
        planned = _bind_planned_actions(planned)
        planned = _apply_capability_gate(
            planned, run.get("capability_snapshot") or {}
        )
        planned = _restrict_plan_to_discovered_sites(
            planned,
            run.get("discovered_sites") or [],
        )
        if stage == "planning" and planned.get("replanned_after_hold_refresh"):
            run = await _complete_stage(
                session, run_id=run_id, current="planning",
                next_status="refreshing_evidence",
                updates={"stage_outputs": _merge_stage_output(
                    run, "planning", {"refresh_required": True}
                )},
            )
            await _stage_started(session, run_id, "refreshing_evidence")
            try:
                refresh_results = await evidence_refresher(
                    session, business_id=run["business_id"],
                    sites=run.get("discovered_sites") or [],
                )
            except Exception as refresh_error:
                refresh_results = [{
                    "status": "failed",
                    "error": str(refresh_error)[:1000],
                    "impact": "replanning uses previously gathered evidence",
                }]
            run = await _complete_stage(
                session, run_id=run_id, current="refreshing_evidence",
                next_status="replanning",
                updates={
                    "evidence_refreshes": refresh_results,
                    "stage_outputs": _merge_stage_output(
                        run, "refreshing_evidence",
                        {"sources": refresh_results},
                    ),
                },
            )
            await _stage_started(session, run_id, "replanning")
            stage = "replanning"
            planned = await _plan_all_sites(session, run=run, planner=planner)
            planned = _bind_planned_actions(planned)
            planned = _apply_capability_gate(
                planned, run.get("capability_snapshot") or {}
            )
            planned = _restrict_plan_to_discovered_sites(
                planned,
                run.get("discovered_sites") or [],
            )
        summary = _summarize_plan(planned)
        if summary["discovered_site_count"] != summary["decided_site_count"]:
            raise RuntimeError(
                "strategy coverage incomplete: discovered_site_count must equal decided_site_count"
            )
        discovered_ids = {str(site["id"]) for site in run.get("discovered_sites") or []}
        decided_ids = {str(item.get("site_id")) for item in summary["site_results"]}
        if discovered_ids != decided_ids:
            raise RuntimeError(
                "strategy coverage incomplete: every discovered site must have exactly one decision"
            )
        next_status = (
            "awaiting_approval"
            if summary["counts"]["awaiting_approval"] > 0
            else "blocked"
        )
        created_actions = []
        if next_status == "awaiting_approval":
            for decision in summary["executable_decisions"]:
                action = str(
                    decision.get("action") or decision.get("strategy_type") or ""
                )
                created_actions.append(
                    await action_factory(
                        session, run=run, decision=decision,
                        capability_snapshot=next(
                            (
                                item for item in
                                (run.get("capability_snapshot") or {}).get("sites", [])
                                if str(item.get("site_id")) == str(decision["site_id"])
                            ),
                            None,
                        ),
                        idempotency_key=derive_child_idempotency_key(
                            run_id, str(decision["site_id"]), action,
                            str(
                                decision.get("source_strategy_task_id")
                                or decision.get("target_url")
                                or decision.get("canonical_url")
                                or action
                            ),
                        ),
                    )
                )
            if not created_actions:
                raise RuntimeError("approval run has no unified strategy actions")
        return await _complete_stage(
            session, run_id=run_id, current=stage, next_status=next_status,
            updates={
                **summary,
                "action_ids": [item.get("action_id") for item in created_actions],
                "stage_outputs": _merge_stage_output(
                    run, stage,
                    {"discovered_site_count": summary["discovered_site_count"],
                     "decided_site_count": summary["decided_site_count"],
                     "source_audit_batch_id": summary.get("source_audit_batch_id")},
                ),
                "next_action": "approve_actions" if next_status == "awaiting_approval"
                else "resolve_blocks",
            },
        )
    except Exception as error:
        if hasattr(session, "rollback"):
            await session.rollback()
        if idempotency_key:
            await _release_control_request(
                session,
                run_id=run_id,
                operation="start",
                key=idempotency_key,
            )
        current = await get_strategy_run(session, run_id=run_id)
        if current and current["status"] not in TERMINAL_RUN_STATUSES:
            await append_strategy_run_event(
                session,
                run_id=run_id,
                stage=current["current_stage"],
                event_type="error",
                message="Strategy run failed",
                data={"error": str(error)[:1000]},
            )
            return await transition_strategy_run(
                session,
                run_id=run_id,
                current_status=current["status"],
                next_status="failed",
                updates={
                    "exceptions": [{"type": type(error).__name__, "message": str(error)[:1000]}],
                    "next_action": "retry",
                },
            )
        raise


async def _discover_run_sites(
    session: AsyncSession, *, business_id: str, scope: str,
    site_ids: list[str] | None,
) -> list[dict[str, Any]]:
    if scope == "selected_sites" and not site_ids:
        raise ValueError("selected_sites scope requires site_ids")
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, name, site_type, is_main
                  FROM seo_agent.sites
                 WHERE business_id = :business_id
                   AND status = 'active' AND strategy_enabled = TRUE
                   AND (:all_sites OR id = ANY(CAST(:site_ids AS uuid[])))
                 ORDER BY is_main DESC, name ASC
                """
            ),
            {
                "business_id": business_id,
                "all_sites": scope == "all_sites",
                "site_ids": site_ids or [],
            },
        )
    ).mappings().all()
    sites = [dict(row) for row in rows]
    if scope == "selected_sites":
        found = {str(site["id"]) for site in sites}
        missing = [item for item in site_ids or [] if str(item) not in found]
        if missing:
            raise ValueError("selected sites are missing, disabled, or outside the business")
    return sites


async def _plan_all_sites(
    session: AsyncSession, *, run: dict[str, Any], planner: StrategyPlanner
) -> dict[str, Any]:
    sites = list(run.get("discovered_sites") or [])
    values = {
        "business_id": run["business_id"],
        "site_ids": (
            [str(site["id"]) for site in sites]
            if run["scope"] == "selected_sites"
            else None
        ),
        "action_budget": run["action_budget"],
        "site_quotas": run["site_quotas"],
        "_strategy_run_id": run.get("run_id"),
    }
    if run.get("run_local_options"):
        values["run_local_options"] = list(run["run_local_options"])
    return await planner(session, **values)


async def _stage_started(session: AsyncSession, run_id: str, stage: str) -> None:
    await append_strategy_run_event(
        session, run_id=run_id, stage=stage, event_type="stage_started",
        message=f"Stage {stage} started",
    )


async def _complete_stage(
    session: AsyncSession, *, run_id: str, current: str, next_status: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    advanced = await transition_strategy_run(
        session, run_id=run_id, current_status=current,
        next_status=next_status, updates=updates, commit=False,
    )
    await append_strategy_run_event(
        session, run_id=run_id, stage=current, event_type="stage_completed",
        message=f"Stage {current} completed",
        data=updates.get("stage_outputs", {}).get(current, {}), commit=False,
    )
    if hasattr(session, "commit"):
        await session.commit()
    return advanced


def _merge_stage_output(
    run: dict[str, Any], stage: str, output: dict[str, Any]
) -> dict[str, Any]:
    return {**(run.get("stage_outputs") or {}), stage: output}


def _apply_capability_gate(
    planned: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Convert undeclared/forbidden actions into explicit safe site outcomes."""
    coverage = dict(planned.get("coverage_matrix") or {})
    decisions = list(coverage.get("decisions") or planned.get("decisions") or [])
    capabilities = {
        str(site.get("site_id")): site for site in snapshot.get("sites") or []
    }
    gated = []
    for decision in decisions:
        item = dict(decision)
        action = str(item.get("action") or item.get("strategy_type") or "")
        if action in {"hold", "configuration_repair", "failed"}:
            gated.append(item)
            continue
        site_capability = capabilities.get(str(item.get("site_id"))) or {}
        permission = (site_capability.get("supported_actions") or {}).get(action)
        adapter_identity = (
            site_capability.get("action_adapters") or {}
        ).get(action)
        adapter_unavailable = action not in UNIFIED_ACTION_TYPES or (
            action in ON_PAGE_UNIFIED_ACTION_TYPES
            and (
                not isinstance(adapter_identity, dict)
                or str(adapter_identity.get("connector_type") or "").casefold()
                != str(item.get("connector_type") or "").casefold()
                or adapter_identity.get("readback") is not True
            )
        )
        if (
            adapter_unavailable
            or permission not in {"approval_required", "allowed", "execute"}
        ):
            issues = site_capability.get("configuration_issues") or []
            item.update(
                {
                    "action": "configuration_repair" if issues else "hold",
                    "schedule_class": (
                        "configuration_repair" if issues else "hold"
                    ),
                    "original_action": action,
                    "block_reason": (
                        "unified_action_adapter_unavailable"
                        if adapter_unavailable
                        else "action_capability_not_declared"
                        if permission is None
                        else "action_capability_forbidden"
                    ),
                    "unlock_condition": "Refresh site capabilities after connector configuration is repaired.",
                    "capability_issues": issues,
                }
            )
        gated.append(item)
    executable = []
    for decision in planned.get("executable_decisions") or []:
        action = str(decision.get("action") or decision.get("strategy_type") or "")
        site_capability = capabilities.get(str(decision.get("site_id"))) or {}
        permission = (site_capability.get("supported_actions") or {}).get(action)
        adapter_identity = (
            site_capability.get("action_adapters") or {}
        ).get(action)
        adapter_ready = action not in ON_PAGE_UNIFIED_ACTION_TYPES or (
            isinstance(adapter_identity, dict)
            and str(adapter_identity.get("connector_type") or "").casefold()
            == str(decision.get("connector_type") or "").casefold()
            and adapter_identity.get("readback") is True
        )
        if (
            action in UNIFIED_ACTION_TYPES
            and permission in {"approval_required", "allowed", "execute"}
            and adapter_ready
        ):
            executable.append(decision)
    return {
        **planned,
        "decisions": gated,
        "executable_decisions": executable,
        "coverage_matrix": {
            **coverage,
            "decisions": gated,
            "decided_site_count": len(gated),
        },
    }


async def _gather_run_evidence(
    session: AsyncSession,
    *,
    business_id: str,
    sites: list[dict[str, Any]],
    run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                SELECT id::text AS snapshot_id, payload->>'scanned_at' AS scanned_at
                  FROM seo_agent.tasks
                 WHERE task_type='review'
                   AND payload->>'kind'='content_audit_batch'
                   AND payload->>'business_id'=:business_id
                 ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    if not row:
        submitted_options = list((run or {}).get("run_local_options") or [])
        if submitted_options:
            return {
                "snapshot_id": None,
                "business_id": business_id,
                "site_ids": [str(site["id"]) for site in sites],
                "source": "codex_current_research",
                "run_local_option_count": len(submitted_options),
                "evidence_hash": _stable_json_hash(
                    [
                        {
                            "option_id": option.get("option_id"),
                            "site_id": option.get("site_id"),
                            "evidence": option.get("evidence") or {},
                        }
                        for option in submitted_options
                    ]
                ),
            }
        raise RuntimeError("current business has no content audit evidence snapshot")
    return {
        **dict(row),
        "business_id": business_id,
        "site_ids": [str(site["id"]) for site in sites],
        "source": "content_audit_batch",
    }


async def _refresh_run_evidence(
    session: AsyncSession, *, business_id: str, sites: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    from app.services.strategy_evidence_refresh_service import (
        refresh_default_strategy_evidence,
    )
    return await refresh_default_strategy_evidence(
        session, business_id=business_id, sites=sites
    )


async def _create_unified_action(
    session: AsyncSession, *, run: dict[str, Any], decision: dict[str, Any],
    idempotency_key: str, capability_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    from app.services.strategy_action_service import SQLActionStore, create_action
    action_type = str(
        decision.get("action_type")
        or decision.get("action")
        or decision.get("strategy_type")
    )
    return await create_action(
        SQLActionStore(session),
        run_id=run["run_id"],
        run_mode=run.get("mode"),
        business_id=run["business_id"],
        site_id=str(decision["site_id"]),
        plan_id=decision.get("plan_id"),
        action_type=action_type,
        editorial_action=decision.get("editorial_action"),
        page_type=decision.get("page_type"),
        target_url=decision.get("target_url") or decision.get("canonical_url"),
        target_asset_id=decision.get("target_asset_id"),
        remote_object_id=decision.get("remote_object_id"),
        connector_id=decision.get("connector_id"),
        connector_type=decision.get("connector_type"),
        expected_fields=list(decision.get("expected_fields") or []),
        adapter_identity=(
            (capability_snapshot or {}).get("action_adapters", {}).get(action_type)
        ),
        source_strategy_task_id=decision.get("source_strategy_task_id"),
        evidence_snapshot_id=run.get("evidence_snapshot_id"),
        strategy_fingerprint=decision.get("strategy_fingerprint"),
        evidence_fingerprint=decision.get("evidence_fingerprint"),
        topic=decision.get("query"),
        schedule_class=decision.get("schedule_class"),
        wave_number=decision.get("wave_number") or 1,
        idempotency_key=idempotency_key,
        risk_level=decision.get("risk_level") or "medium",
        approval_requirement="approval_required",
        capability_snapshot=capability_snapshot,
        strategy_decision=decision.get("strategy_decision") or decision,
    )


async def _claim_control_request(
    session: AsyncSession, *, run_id: str, operation: str, key: str
) -> bool:
    lock_key = f"strategy-run-control:{run_id}:{key}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key}
    )
    row = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET decision=jsonb_set(
                       decision, '{control_idempotency}',
                       COALESCE(decision->'control_idempotency','{}'::jsonb)
                         || jsonb_build_object(
                              CAST(:control_key AS text),
                              CAST(:operation AS text)
                            ),
                       true),
                       updated_at=now()
                 WHERE id=CAST(:run_id AS uuid)
                   AND payload->>'kind'=:kind
                   AND NOT COALESCE(decision->'control_idempotency','{}'::jsonb)
                           ? CAST(:control_key AS text)
                RETURNING id
                """
            ),
            {
                "run_id": run_id, "kind": RUN_KIND,
                "control_key": key, "operation": operation,
            },
        )
    ).first()
    if row:
        await session.commit()
        return True

    existing = (
        await session.execute(
            text(
                """
                SELECT decision->'control_idempotency'->>CAST(:control_key AS text)
                  FROM seo_agent.tasks
                 WHERE id=CAST(:run_id AS uuid)
                   AND payload->>'kind'=:kind
                """
            ),
            {"run_id": run_id, "kind": RUN_KIND, "control_key": key},
        )
    ).scalar_one_or_none()
    await session.commit()
    if existing == operation:
        return False
    if existing:
        raise ValueError(
            f"idempotency key is already bound to strategy run operation {existing}"
        )
    raise ValueError("strategy run not found")


async def _release_control_request(
    session: AsyncSession, *, run_id: str, operation: str, key: str
) -> None:
    """Remove a control receipt when the claimed operation fails before completion."""
    lock_key = f"strategy-run-control:{run_id}:{key}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key}
    )
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET decision=jsonb_set(
                   decision,
                   '{control_idempotency}',
                   COALESCE(decision->'control_idempotency','{}'::jsonb)
                     - CAST(:control_key AS text),
                   true
               ),
                   updated_at=now()
             WHERE id=CAST(:run_id AS uuid)
               AND payload->>'kind'=:kind
               AND decision->'control_idempotency'->>CAST(:control_key AS text)
                     = CAST(:operation AS text)
            """
        ),
        {
            "run_id": run_id,
            "kind": RUN_KIND,
            "control_key": key,
            "operation": operation,
        },
    )
    await session.commit()


async def _load_run_actions(
    session: AsyncSession, *, run_id: str
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT payload
                  FROM seo_agent.tasks
                 WHERE task_type = 'review'
                   AND payload->>'kind' = 'strategy_action'
                   AND payload->>'run_id' = :run_id
                 ORDER BY created_at ASC
                """
            ),
            {"run_id": run_id},
        )
    ).mappings().all()
    return [dict(row["payload"] or {}) for row in rows]


async def _reconcile_terminal_run(
    session: AsyncSession,
    *,
    run_id: str,
    current_status: str,
    next_status: str,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Close a recoverable terminal Run after its persisted Actions converge.

    A Run can become ``partial`` while its last Action is in the deliberately
    blocked ``unknown_remote_state`` state. A later read-only recovery may
    prove that Action was applied. This narrow compare-and-set permits only the
    resulting ``partial`` -> ``completed`` correction; normal terminal-state
    transitions remain forbidden.
    """
    if current_status != "partial" or next_status != "completed":
        raise ValueError("unsupported terminal strategy run reconciliation")
    patch = {
        "status": next_status,
        "current_stage": next_status,
        **(updates or {}),
    }
    row = (
        await session.execute(
            text(
                """
                UPDATE seo_agent.tasks
                   SET status = :db_status,
                       decision = decision || CAST(:patch AS jsonb),
                       finished_at = now(),
                       updated_at = now()
                 WHERE id = CAST(:run_id AS uuid)
                   AND task_type = 'review'
                   AND payload->>'kind' = :kind
                   AND decision->>'status' = :current_status
                RETURNING id::text AS id, payload, decision, created_at, updated_at,
                          started_at, finished_at, error_message
                """
            ),
            {
                "run_id": run_id,
                "kind": RUN_KIND,
                "current_status": current_status,
                "db_status": _DB_STATUS[next_status],
                "patch": json.dumps(patch, ensure_ascii=False),
            },
        )
    ).mappings().first()
    if not row:
        await session.commit()
        raise ValueError("strategy run changed concurrently or was not found")
    await session.commit()
    return _serialize_run(dict(row))


async def reconcile_strategy_run(
    session: AsyncSession,
    *,
    run_id: str,
    action_loader: ActionLoader | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Advance a post-approval run from persisted Action outcomes.

    This is the single coordination seam used by Action execution and by the
    public recovery endpoint. Compare-and-set races are retried from the latest
    persisted state, so concurrent terminal Actions cannot strand their Run.
    """
    claimed_control = False
    if idempotency_key:
        claimed_control = await _claim_control_request(
            session, run_id=run_id, operation="reconcile", key=idempotency_key
        )
        if not claimed_control:
            replay = await get_strategy_run(session, run_id=run_id)
            if not replay:
                raise ValueError("strategy run not found")
            return {**replay, "idempotency_replayed": True}

    try:
        loader = action_loader or _load_run_actions
        for _attempt in range(5):
            run = await get_strategy_run(session, run_id=run_id)
            if not run:
                raise ValueError("strategy run not found")
            if run["status"] == "partial":
                actions = await loader(session, run_id=run_id)
                if actions and all(
                    action.get("status") == "completed" for action in actions
                ):
                    observations = [
                        str(action["observation_id"])
                        for action in actions
                        if action.get("observation_id")
                    ]
                    return await _reconcile_terminal_run(
                        session,
                        run_id=run_id,
                        current_status="partial",
                        next_status="completed",
                        updates={
                            "action_outcome_counts": {
                                "completed": len(actions),
                                "blocked": 0,
                                "failed": 0,
                            },
                            "observation_ids": observations,
                            "next_action": "none",
                        },
                    )
            if run["status"] in TERMINAL_RUN_STATUSES:
                return {**run, "reconcile_replayed": True}
            if run["status"] not in {
                "awaiting_approval",
                "executing",
                "verifying",
                "observing",
            }:
                return {**run, "reconcile_replayed": True, "next_action": "start"}
            try:
                return await _reconcile_run_actions(
                    session, run=run, action_loader=loader
                )
            except ValueError as error:
                if str(error) != "strategy run changed concurrently or was not found":
                    raise
                if hasattr(session, "rollback"):
                    await session.rollback()
        raise ValueError("strategy run reconciliation remained busy after 5 attempts")
    except Exception:
        if claimed_control:
            if hasattr(session, "rollback"):
                await session.rollback()
            await _release_control_request(
                session,
                run_id=run_id,
                operation="reconcile",
                key=str(idempotency_key),
            )
        raise


async def _reconcile_run_actions(
    session: AsyncSession, *, run: dict[str, Any], action_loader: ActionLoader
) -> dict[str, Any]:
    """Advance post-approval stages from persisted unified action outcomes."""
    run_id = run.get("run_id")
    actions = await action_loader(session, run_id=run_id)
    if not actions:
        return {**run, "start_replayed": True, "next_action": "approve_actions"}
    statuses = {str(action.get("status") or "") for action in actions}
    terminal = {"completed", "blocked", "failed", "canceled"}
    counts = {
        "completed": sum(a.get("status") == "completed" for a in actions),
        "blocked": sum(a.get("status") == "blocked" for a in actions),
        "failed": sum(a.get("status") in {"failed", "canceled"} for a in actions),
    }
    observations = [
        str(action["observation_id"]) for action in actions
        if action.get("status") == "completed" and action.get("observation_id")
    ]
    post_approval = statuses - {"planned", "previewing", "previewed"}
    if run["status"] == "awaiting_approval" and post_approval:
        run = await _complete_stage(
            session, run_id=run_id, current="awaiting_approval",
            next_status="executing",
            updates={"action_outcome_counts": counts},
        )
        await _stage_started(session, run_id, "executing")
    if not statuses.issubset(terminal):
        return {**run, "start_replayed": True, "next_action": "complete_actions"}
    if run["status"] == "executing":
        run = await _complete_stage(
            session, run_id=run_id, current="executing", next_status="verifying",
            updates={"action_outcome_counts": counts},
        )
        await _stage_started(session, run_id, "verifying")
    if run["status"] == "verifying":
        if counts["completed"] == 0:
            return await _complete_stage(
                session, run_id=run_id, current="verifying",
                next_status="failed" if counts["failed"] else "partial",
                updates={"action_outcome_counts": counts, "next_action": "resolve_action_failures"},
            )
        run = await _complete_stage(
            session, run_id=run_id, current="verifying", next_status="observing",
            updates={"action_outcome_counts": counts, "observation_ids": observations},
        )
        await _stage_started(session, run_id, "observing")
    if run["status"] == "observing":
        final = "completed" if counts["completed"] == len(actions) else "partial"
        return await _complete_stage(
            session, run_id=run_id, current="observing", next_status=final,
            updates={
                "action_outcome_counts": counts,
                "observation_ids": observations,
                "next_action": "none" if final == "completed" else "resolve_action_failures",
            },
        )
    return run


async def _advance_if_not_canceled(
    session: AsyncSession, *, run_id: str, current: str, next_status: str
) -> dict[str, Any]:
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        raise ValueError("strategy run not found")
    if run.get("cancel_requested") or run["status"] == "canceled":
        if run["status"] == "canceled":
            return run
        return await transition_strategy_run(
            session, run_id=run_id, current_status=run["status"], next_status="canceled"
        )
    advanced = await transition_strategy_run(
        session, run_id=run_id, current_status=current, next_status=next_status
    )
    await append_strategy_run_event(
        session,
        run_id=run_id,
        stage=current,
        event_type="stage_completed",
        message=f"Stage {current} completed",
    )
    return advanced


def _summarize_plan(planned: dict[str, Any]) -> dict[str, Any]:
    coverage = dict(planned.get("coverage_matrix") or {})
    site_scope = list(planned.get("site_scope") or [])
    decisions = list(coverage.get("decisions") or planned.get("decisions") or [])
    discovered = int(coverage.get("discovered_site_count") or len(site_scope))
    decided = int(coverage.get("decided_site_count") or len(decisions))
    counts = {
        "executed": 0,
        "awaiting_approval": 0,
        "execute_now": 0,
        "deferred": 0,
        "hold": 0,
        "configuration_repair": 0,
        "failed": 0,
    }
    for decision in decisions:
        action = str(decision.get("action") or decision.get("strategy_type") or "")
        schedule_class = str(decision.get("schedule_class") or "")
        if action == "hold" or schedule_class == "hold":
            counts["hold"] += 1
        elif action == "configuration_repair":
            counts["configuration_repair"] += 1
        elif action == "failed":
            counts["failed"] += 1
    executable_decisions = list(planned.get("executable_decisions") or [])
    plan_items = list((planned.get("plan") or {}).get("items") or [])
    counts["execute_now"] = len(executable_decisions)
    counts["awaiting_approval"] = len(executable_decisions)
    counts["deferred"] = sum(
        item.get("schedule_class") == "deferred" for item in plan_items
    )
    plan = dict(planned.get("plan") or {})
    return {
        "discovered_site_count": discovered,
        "decided_site_count": decided,
        "source_audit_batch_id": plan.get("source_audit_batch_id")
        or planned.get("source_audit_batch_id"),
        "analysis_batch_id": planned.get("analysis_batch_id"),
        "plan_id": plan.get("id"),
        "counts": counts,
        "site_results": decisions,
        "executable_decisions": executable_decisions,
    }


def _bind_planned_actions(planned: dict[str, Any]) -> dict[str, Any]:
    coverage = _attach_action_bindings(
        dict(planned.get("coverage_matrix") or {}),
        dict(planned.get("plan") or {}),
    )
    return {
        **planned,
        "decisions": list(coverage.get("decisions") or []),
        "coverage_matrix": coverage,
        "executable_decisions": [
            _strategy_item_action(item)
            for item in (planned.get("plan") or {}).get("items") or []
            if item.get("schedule_class") == "execute_now"
        ],
    }


def _attach_action_bindings(
    coverage: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Bind each selected site decision to the exact persisted seo_strategy row.

    ``generate_strategies`` persists one ``seo_strategy`` task per selected
    candidate, but the coverage matrix historically retained only the candidate
    ID.  Unified Actions need the persisted task identity so approval, article
    generation, publishing, and recovery all operate on the same immutable
    decision rather than trying to rediscover a "latest" row later.
    """
    by_option = {
        str(item.get("option_id")): item
        for item in plan.get("items") or []
        if item.get("option_id") and item.get("id")
    }
    by_candidate = {
        str(item.get("candidate_id")): item
        for item in plan.get("items") or []
        if item.get("candidate_id") and item.get("id")
    }
    decisions: list[dict[str, Any]] = []
    for raw in coverage.get("decisions") or []:
        decision = dict(raw)
        option_id = str(decision.get("selected_option_id") or "")
        candidate_id = str(decision.get("selected_candidate_id") or "")
        source = by_option.get(option_id) or by_candidate.get(candidate_id)
        if source:
            action_type = str(
                source.get("action_type")
                or decision.get("action")
                or source.get("strategy_type")
                or ""
            )
            decision.update(
                {
                    "action": action_type,
                    "source_strategy_task_id": str(source["id"]),
                    "plan_id": source.get("plan_id") or plan.get("id"),
                    "strategy_run_id": source.get("strategy_run_id")
                    or plan.get("strategy_run_id"),
                    "schedule_class": source.get("schedule_class")
                    or decision.get("schedule_class"),
                    "target_asset_id": (
                        source.get("target_asset_id")
                        or source.get("post_id")
                        or source.get("article_id")
                    ),
                    "remote_object_id": source.get("remote_object_id"),
                    "connector_id": source.get("connector_id"),
                    "connector_type": source.get("connector_type"),
                    "page_type": source.get("page_type"),
                    "expected_fields": list(source.get("expected_fields") or []),
                    "editorial_action": source.get("editorial_action"),
                    "action_type": action_type,
                    "target_url": source.get("target_url")
                    or source.get("canonical_url")
                    or ((source.get("evidence") or {}).get("site_content") or {}).get(
                        "published_url"
                    ),
                    "strategy_fingerprint": source.get("strategy_fingerprint"),
                    "evidence_fingerprint": source.get("evidence_fingerprint"),
                    "query": source.get("query"),
                    "strategy_type": source.get("strategy_type"),
                    "strategy_decision": {
                        key: value
                        for key, value in source.items()
                        if key
                        not in {
                            "id",
                            "candidate_id",
                            "plan_id",
                            "status",
                            "created_at",
                            "updated_at",
                        }
                    },
                }
            )
        decisions.append(decision)
    return {**coverage, "decisions": decisions}


def _strategy_item_action(item: dict[str, Any]) -> dict[str, Any]:
    action_type = str(item.get("action_type") or item.get("strategy_type") or "")
    return {
        **item,
        "action": action_type,
        "source_strategy_task_id": str(item["id"]),
        "target_asset_id": (
            item.get("target_asset_id")
            or item.get("post_id")
            or item.get("article_id")
        ),
        "strategy_decision": {
            key: value
            for key, value in item.items()
            if key
            not in {
                "id",
                "candidate_id",
                "plan_id",
                "status",
                "created_at",
                "updated_at",
            }
        },
    }


def _restrict_plan_to_discovered_sites(
    planned: dict[str, Any],
    discovered_sites: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep planner coverage identical to the run's enabled-site discovery snapshot."""
    discovered_ids = {str(site["id"]) for site in discovered_sites}
    coverage = dict(planned.get("coverage_matrix") or {})
    decisions = [
        decision
        for decision in (coverage.get("decisions") or planned.get("decisions") or [])
        if str(decision.get("site_id")) in discovered_ids
    ]
    return {
        **planned,
        "site_scope": discovered_sites,
        "decisions": decisions,
        "coverage_matrix": {
            **coverage,
            "discovered_site_count": len(discovered_sites),
            "decided_site_count": len(decisions),
            "decisions": decisions,
            "complete": len(decisions) == len(discovered_sites),
        },
    }


def _serialize_run(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("payload") or {})
    decision = dict(row.get("decision") or {})
    return {
        "ok": True,
        "run_id": str(row["id"]),
        "status": decision.get("status", "queued"),
        "current_stage": decision.get("current_stage", decision.get("status", "queued")),
        "business_id": payload.get("business_id"),
        "site_ids": payload.get("site_ids"),
        "scope": payload.get("scope"),
        "mode": payload.get("mode"),
        "requested_by": payload.get("requested_by"),
        "idempotency_key": payload.get("idempotency_key"),
        "action_budget": payload.get("action_budget"),
        "site_quotas": payload.get("site_quotas") or {},
        "approval_policy": payload.get("approval_policy"),
        "root_run_id": payload.get("root_run_id"),
        "attempt": payload.get("attempt", 1),
        "discovered_site_count": decision.get("discovered_site_count", 0),
        "decided_site_count": decision.get("decided_site_count", 0),
        "discovered_sites": decision.get("discovered_sites") or [],
        "capability_snapshot": decision.get("capability_snapshot"),
        "stage_outputs": decision.get("stage_outputs") or {},
        "evidence_snapshot_id": decision.get("evidence_snapshot_id"),
        "evidence_snapshot": decision.get("evidence_snapshot"),
        "evidence_refreshes": decision.get("evidence_refreshes") or [],
        "run_local_options": decision.get("run_local_options") or [],
        "run_local_option_count": int(
            decision.get("run_local_option_count") or 0
        ),
        "run_local_option_submission": decision.get(
            "run_local_option_submission"
        ),
        "action_ids": decision.get("action_ids") or [],
        "source_audit_batch_id": decision.get("source_audit_batch_id"),
        "analysis_batch_id": decision.get("analysis_batch_id"),
        "plan_id": decision.get("plan_id"),
        "counts": decision.get("counts") or {},
        "site_results": decision.get("site_results") or [],
        "exceptions": decision.get("exceptions") or [],
        "observation_ids": decision.get("observation_ids") or [],
        "cancel_requested": bool(decision.get("cancel_requested")),
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "updated_at": _iso(row.get("updated_at")),
        "next_action": decision.get("next_action", "poll"),
        "error": row.get("error_message"),
    }


def _serialize_event(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("payload") or {})
    return {
        "event_id": str(row["id"]),
        "run_id": payload.get("run_id"),
        "stage": payload.get("stage"),
        "event_type": payload.get("event_type"),
        "site_id": payload.get("site_id"),
        "message": payload.get("message"),
        "data": payload.get("data") or {},
        "created_at": _iso(row.get("created_at")),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value
