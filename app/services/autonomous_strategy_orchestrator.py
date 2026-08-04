"""AI-led strategy research and proposal orchestration.

The interface in this module is the only new-strategy seam:

``capture_research_portfolio`` -> ``submit_proposed_actions`` ->
``build_reviewed_plan`` / ``review_zero_action``.

Editorial choices belong to the AI caller.  This implementation validates
scope, provenance, target identity, exact-target conflicts, capabilities and
safe execution capacity.  It never queries or writes the legacy candidate
pool and never substitutes a different topic.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_effect_service import (
    normalize_canonical_url,
    strategy_identity,
)


ACTION_TYPES = frozenset(
    {
        "new_article",
        "update_article",
        "on_page_fix",
        "hold",
        "configuration_repair",
    }
)
CONCRETE_RESEARCH_ACTIONS = frozenset(
    {"new_article", "update_article", "on_page_fix"}
)
OPPORTUNITY_OUTCOMES = frozenset({"qualified", "rejected", "blocked"})
TARGET_BLOCK_SCOPES = frozenset({"url", "topic"})
REQUIRED_HOLD_RESEARCH_SURFACES = frozenset(
    {
        "existing_articles",
        "new_topics",
        "product_pages",
        "category_pages",
        "on_page",
    }
)
EVIDENCE_LEVELS = frozenset({"high", "medium", "low", "unsafe"})
TARGET_SCOPED_BLOCKERS = frozenset(
    {"TARGET_COOLDOWN", "TOPIC_COOLDOWN", "TARGET_ACTIVE", "TOPIC_ACTIVE"}
)
SCHEDULE_REQUESTS = frozenset(
    {"execute_now", "deferred", "hold", "configuration_repair"}
)
CONCRETE_ON_PAGE_ACTIONS = frozenset(
    {"homepage_seo", "product_seo", "category_seo", "product_image_alt"}
)
EXECUTABLE_ACTIONS = frozenset(
    {"new_article", "update_article", *CONCRETE_ON_PAGE_ACTIONS}
)
EVIDENCE_SOURCE_TYPES = frozenset(
    {
        "gsc",
        "ga4",
        "site_api",
        "serpapi",
        "public_search",
        "semrush_ui",
        "other",
    }
)
COLLECTION_STATUSES = frozenset({"success", "partial", "empty", "failed"})
EVIDENCE_FRESHNESS = frozenset({"current", "recent", "lagging", "unknown"})
EVIDENCE_CONFLICT_STATUSES = frozenset(
    {"resolved", "unresolved", "deferred", "human_review_required"}
)
FACT_SCOPES = frozenset(
    {
        "product_fact",
        "first_party_performance",
        "user_behavior",
        "intent",
        "competitor_estimate",
        "other",
    }
)
SITE_HARD_BLOCK_CODES = frozenset(
    {
        "CAPABILITY_MISSING",
        "SITE_DISABLED",
        "SITE_CONFIGURATION_INCOMPLETE",
    }
)
VERIFIED_DEFER_REASON_CODES = frozenset(
    {
        "TARGET_CONFLICT_ACTIVE",
        "EVIDENCE_CONFLICT_UNRESOLVED",
        "SAFETY_CEILING_EXCEEDED",
        "SITE_SAFETY_CEILING_EXCEEDED",
    }
)
SECOND_CHANNEL_SOURCE_TYPES = frozenset(
    {"site_api", "serpapi", "public_search", "semrush_ui", "other"}
)
CONTRACT_VERSION = "ai-led-strategy-v2"


class StrategyContractError(ValueError):
    """A stable, user-actionable strategy contract failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class EvidenceAdapter(Protocol):
    """Internal seam for API, database, GUI, public-search and fixture evidence."""

    async def capture(
        self,
        *,
        site: dict[str, Any],
        research_questions: list[str],
    ) -> list[dict[str, Any]]: ...


class FixedEvidenceAdapter:
    """Deterministic evidence Adapter used by tests and saved-snapshot replay."""

    def __init__(self, sources: list[dict[str, Any]]):
        self._sources = [dict(source) for source in sources]

    async def capture(
        self,
        *,
        site: dict[str, Any],
        research_questions: list[str],
    ) -> list[dict[str, Any]]:
        return [dict(source) for source in self._sources]


async def collect_evidence(
    adapter: EvidenceAdapter,
    *,
    site: dict[str, Any],
    research_questions: list[str],
) -> list[dict[str, Any]]:
    """Collect through one seam and normalize every Adapter result identically."""
    return [
        normalize_evidence_source(source)
        for source in await adapter.capture(
            site=dict(site),
            research_questions=list(research_questions),
        )
    ]


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _required_text(value: Any, *, field: str, code: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise StrategyContractError(code, f"{field} is required")
    return result


def _string_list(value: Any, *, field: str, code: str) -> list[str]:
    if not isinstance(value, list):
        raise StrategyContractError(code, f"{field} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _reject_legacy_candidate_keys(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "candidate_id":
                raise StrategyContractError(
                    "LEGACY_CANDIDATE_REFERENCE_REJECTED",
                    f"{path}.{key} is retired from the formal strategy interface",
                )
            if key == "keyword_id" and path != "payload.evidence_refs":
                raise StrategyContractError(
                    "KEYWORD_DECISION_REFERENCE_REJECTED",
                    f"{path}.{key} cannot participate in formal strategy decisions",
                )
            _reject_legacy_candidate_keys(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_legacy_candidate_keys(item, path=f"{path}[{index}]")


def normalize_evidence_source(raw: dict[str, Any]) -> dict[str, Any]:
    source_type = _required_text(
        raw.get("source_type"),
        field="source_type",
        code="EVIDENCE_PROVENANCE_INCOMPLETE",
    )
    if source_type not in EVIDENCE_SOURCE_TYPES:
        raise StrategyContractError(
            "EVIDENCE_PROVENANCE_INCOMPLETE",
            f"unsupported evidence source_type: {source_type}",
        )
    collection_status = _required_text(
        raw.get("collection_status"),
        field="collection_status",
        code="EVIDENCE_PROVENANCE_INCOMPLETE",
    )
    if collection_status not in COLLECTION_STATUSES:
        raise StrategyContractError(
            "EVIDENCE_PROVENANCE_INCOMPLETE",
            f"unsupported collection_status: {collection_status}",
        )
    fact_scope = str(raw.get("fact_scope") or "other").strip()
    if fact_scope not in FACT_SCOPES:
        raise StrategyContractError(
            "EVIDENCE_PROVENANCE_INCOMPLETE",
            f"unsupported fact_scope: {fact_scope}",
        )
    freshness = str(raw.get("freshness") or "unknown").strip()
    if freshness not in EVIDENCE_FRESHNESS:
        raise StrategyContractError(
            "EVIDENCE_PROVENANCE_INCOMPLETE",
            f"unsupported freshness: {freshness}",
        )
    captured_at = _required_text(
        raw.get("captured_at"),
        field="captured_at",
        code="EVIDENCE_PROVENANCE_INCOMPLETE",
    )
    try:
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise StrategyContractError(
            "EVIDENCE_PROVENANCE_INCOMPLETE",
            "captured_at must be an ISO-8601 timestamp",
        ) from error
    artifact_refs = _string_list(
        raw.get("artifact_refs") or [],
        field="artifact_refs",
        code="EVIDENCE_PROVENANCE_INCOMPLETE",
    )
    filters = raw.get("filters") or {}
    if not isinstance(filters, dict):
        raise StrategyContractError(
            "EVIDENCE_PROVENANCE_INCOMPLETE", "filters must be an object"
        )
    data_window = raw.get("data_window") or {}
    if not isinstance(data_window, dict):
        raise StrategyContractError(
            "EVIDENCE_PROVENANCE_INCOMPLETE", "data_window must be an object"
        )
    if source_type == "semrush_ui":
        missing = [
            field
            for field, value in (
                ("market", raw.get("market")),
                ("device", raw.get("device")),
                ("source_name", raw.get("source_name")),
                ("artifact_refs", artifact_refs),
            )
            if not value
        ]
        if missing:
            raise StrategyContractError(
                "EVIDENCE_PROVENANCE_INCOMPLETE",
                "semrush_ui evidence is missing: " + ", ".join(missing),
            )
    return {
        "source_type": source_type,
        "source_name": _required_text(
            raw.get("source_name"),
            field="source_name",
            code="EVIDENCE_PROVENANCE_INCOMPLETE",
        ),
        "captured_at": captured_at,
        "data_window": data_window,
        "market": str(raw.get("market") or "").strip() or None,
        "language": str(raw.get("language") or "").strip() or None,
        "device": str(raw.get("device") or "").strip() or None,
        "dimensions": _string_list(
            raw.get("dimensions") or [],
            field="dimensions",
            code="EVIDENCE_PROVENANCE_INCOMPLETE",
        ),
        "filters": filters,
        "freshness": freshness,
        "fact_scope": fact_scope,
        "artifact_refs": artifact_refs,
        "collection_status": collection_status,
        "limitations": _string_list(
            raw.get("limitations") or [],
            field="limitations",
            code="EVIDENCE_PROVENANCE_INCOMPLETE",
        ),
        "decision_use": _required_text(
            raw.get("decision_use"),
            field="decision_use",
            code="EVIDENCE_PROVENANCE_INCOMPLETE",
        ),
    }


def _intent_key(value: Any) -> str:
    tokens = re.findall(r"[a-z0-9]+", str(value or "").casefold())
    return "-".join(tokens)


def _evidence_level(value: Any, *, field: str) -> str:
    level = str(value or "medium").strip().casefold()
    if level not in EVIDENCE_LEVELS:
        raise StrategyContractError(
            "RESEARCH_EVIDENCE_INSUFFICIENT",
            f"{field} must be high, medium, low, or unsafe",
        )
    return level


def _normalize_site_hard_blocker(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise StrategyContractError(
            "SITE_HARD_BLOCKER_INVALID",
            "site hard blockers must be structured objects",
        )
    code = _required_text(
        raw.get("code"),
        field="hard_blockers.code",
        code="SITE_HARD_BLOCKER_INVALID",
    )
    if code not in SITE_HARD_BLOCK_CODES:
        raise StrategyContractError(
            "SITE_HARD_BLOCKER_INVALID",
            f"{code} is not a validated site-level hard blocker",
        )
    message = _required_text(
        raw.get("message") or raw.get("reason"),
        field="hard_blockers.message",
        code="SITE_HARD_BLOCKER_INVALID",
    )
    return {
        "code": code,
        "scope": "site",
        "message": message,
        "unlock_condition": (
            str(raw.get("unlock_condition") or "").strip() or None
        ),
    }


def _normalize_material_option(
    raw: dict[str, Any],
    *,
    site: dict[str, Any],
) -> dict[str, Any]:
    option_id = _required_text(
        raw.get("option_id"),
        field="material_options.option_id",
        code="CONCRETE_OPPORTUNITY_REQUIRED",
    )
    action = _required_text(
        raw.get("action"),
        field="material_options.action",
        code="CONCRETE_OPPORTUNITY_REQUIRED",
    )
    if action not in CONCRETE_RESEARCH_ACTIONS:
        raise StrategyContractError(
            "CONCRETE_OPPORTUNITY_REQUIRED",
            "material options must be concrete new_article, update_article, "
            "or on_page_fix opportunities; Hold is a final site outcome",
        )
    user_intent = _required_text(
        raw.get("user_intent"),
        field="material_options.user_intent",
        code="CONCRETE_OPPORTUNITY_REQUIRED",
    )
    evidence_refs = _string_list(
        raw.get("evidence_refs") or [],
        field="material_options.evidence_refs",
        code="RESEARCH_EVIDENCE_INSUFFICIENT",
    )
    if not evidence_refs:
        raise StrategyContractError(
            "RESEARCH_EVIDENCE_INSUFFICIENT",
            "every concrete opportunity requires evidence_refs",
        )
    target_identity = dict(raw.get("target_identity") or {})
    target_url = str(target_identity.get("target_url") or "").strip() or None
    remote_object_id = (
        str(target_identity.get("remote_object_id") or "").strip() or None
    )
    local_object_id = (
        str(target_identity.get("local_object_id") or "").strip() or None
    )
    intent_key = _intent_key(target_identity.get("intent_key") or user_intent)
    topic_cluster = _intent_key(target_identity.get("topic_cluster"))
    canonical_url: str | None = None
    shared_identity: dict[str, str] | None = None
    if action in {"update_article", "on_page_fix"}:
        if target_url:
            try:
                canonical_url = normalize_canonical_url(
                    target_url,
                    site_url=site.get("base_url"),
                    site_domain=site.get("domain"),
                )
            except ValueError as error:
                raise StrategyContractError(
                    "TARGET_IDENTITY_UNRESOLVED", str(error)
                ) from error
        if not (canonical_url or remote_object_id or local_object_id):
            raise StrategyContractError(
                "CONCRETE_OPPORTUNITY_REQUIRED",
                f"{action} opportunity requires a URL or stable object identity",
            )
        target_scope = canonical_url or remote_object_id or local_object_id
    else:
        if not intent_key or not topic_cluster:
            raise StrategyContractError(
                "CONCRETE_OPPORTUNITY_REQUIRED",
                "new_article opportunity requires intent_key and topic_cluster",
            )
        target_scope = f"{intent_key}|{topic_cluster}"
    outcome = _required_text(
        raw.get("outcome"),
        field="material_options.outcome",
        code="CONCRETE_OPPORTUNITY_REQUIRED",
    )
    if outcome not in OPPORTUNITY_OUTCOMES:
        raise StrategyContractError(
            "CONCRETE_OPPORTUNITY_REQUIRED",
            f"unsupported material option outcome: {outcome}",
        )
    evidence_level = _evidence_level(
        raw.get("evidence_level"), field="material_options.evidence_level"
    )
    if outcome == "qualified" and evidence_level == "unsafe":
        raise StrategyContractError(
            "UNSAFE_EXPERIMENT_REJECTED",
            "an unsafe opportunity cannot be qualified for execution",
        )
    reason = _required_text(
        raw.get("reason"),
        field="material_options.reason",
        code="CONCRETE_OPPORTUNITY_REQUIRED",
    )
    blocker_code = str(raw.get("blocker_code") or "").strip() or None
    block_scope = str(raw.get("block_scope") or "").strip() or None
    if outcome == "blocked" and not blocker_code:
        raise StrategyContractError(
            "CONCRETE_OPPORTUNITY_REQUIRED",
            "blocked opportunity requires blocker_code",
        )
    if blocker_code in TARGET_SCOPED_BLOCKERS and block_scope not in TARGET_BLOCK_SCOPES:
        raise StrategyContractError(
            "COOLDOWN_SCOPE_OVERBROAD",
            "cooldown and active-observation blockers must name an exact url or topic scope",
        )
    if outcome != "blocked" and (blocker_code or block_scope):
        raise StrategyContractError(
            "CONCRETE_OPPORTUNITY_REQUIRED",
            "only blocked opportunities may declare blocker_code or block_scope",
        )
    identity = {
        "target_url": canonical_url or target_url,
        "remote_object_id": remote_object_id,
        "local_object_id": local_object_id,
        "intent_key": intent_key or None,
        "topic_cluster": topic_cluster or None,
    }
    return {
        "option_id": option_id,
        "action": action,
        "action_type": str(raw.get("action_type") or "").strip() or None,
        "target_identity": identity,
        "scope_key": _stable_hash(
            {
                "site_id": str(site["id"]),
                "action": action,
                "target": target_scope,
            }
        ),
        "user_intent": user_intent,
        "evidence_refs": evidence_refs,
        "evidence_level": evidence_level,
        "outcome": outcome,
        "reason": reason,
        "blocker_code": blocker_code,
        "block_scope": block_scope,
    }


def normalize_research_portfolio(
    portfolio: list[dict[str, Any]],
    *,
    discovered_sites: list[dict[str, Any]],
    evidence_snapshot_id: str,
) -> list[dict[str, Any]]:
    _reject_legacy_candidate_keys(portfolio)
    if not portfolio:
        raise StrategyContractError(
            "RESEARCH_PORTFOLIO_INCOMPLETE",
            "research portfolio must include every discovered site",
        )
    sites_by_id = {str(site["id"]): site for site in discovered_sites}
    rows_by_site: dict[str, dict[str, Any]] = {}
    for raw in portfolio:
        site_id = _required_text(
            raw.get("site_id"),
            field="site_id",
            code="RESEARCH_PORTFOLIO_INCOMPLETE",
        )
        if site_id not in sites_by_id:
            raise StrategyContractError(
                "SITE_OUT_OF_SCOPE",
                f"research site is outside the Run scope: {site_id}",
            )
        if site_id in rows_by_site:
            raise StrategyContractError(
                "RESEARCH_PORTFOLIO_INCOMPLETE",
                f"research portfolio contains duplicate site: {site_id}",
            )
        site = sites_by_id[site_id]
        sources = [
            normalize_evidence_source(dict(item))
            for item in (raw.get("evidence_sources") or [])
        ]
        actions_considered = _string_list(
            raw.get("actions_considered") or [],
            field="actions_considered",
            code="RESEARCH_PORTFOLIO_INCOMPLETE",
        )
        invalid_actions = sorted(set(actions_considered) - ACTION_TYPES)
        if invalid_actions:
            raise StrategyContractError(
                "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                "unsupported actions_considered: " + ", ".join(invalid_actions),
            )
        material_options: list[dict[str, Any]] = []
        seen_option_ids: set[str] = set()
        for option in raw.get("material_options") or []:
            if not isinstance(option, dict):
                raise StrategyContractError(
                    "RESEARCH_PORTFOLIO_INCOMPLETE",
                    "material_options must contain objects",
                )
            normalized_option = _normalize_material_option(option, site=site)
            if normalized_option["option_id"] in seen_option_ids:
                raise StrategyContractError(
                    "CONCRETE_OPPORTUNITY_REQUIRED",
                    "material option IDs must be unique within a site",
                )
            seen_option_ids.add(normalized_option["option_id"])
            material_options.append(normalized_option)
        action_assessments = raw.get("action_assessments") or {}
        if not isinstance(action_assessments, dict):
            raise StrategyContractError(
                "RESEARCH_PORTFOLIO_INCOMPLETE",
                "action_assessments must be an object",
            )
        normalized_assessments: dict[str, dict[str, Any]] = {}
        for action, assessment in action_assessments.items():
            if action not in ACTION_TYPES or not isinstance(assessment, dict):
                raise StrategyContractError(
                    "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                    f"unsupported action assessment: {action}",
                )
            normalized_assessments[action] = {
                "outcome": _required_text(
                    assessment.get("outcome"),
                    field=f"action_assessments.{action}.outcome",
                    code="RESEARCH_PORTFOLIO_INCOMPLETE",
                ),
                "reason": _required_text(
                    assessment.get("reason"),
                    field=f"action_assessments.{action}.reason",
                    code="RESEARCH_PORTFOLIO_INCOMPLETE",
                ),
                "evidence_refs": _string_list(
                    assessment.get("evidence_refs") or [],
                    field=f"action_assessments.{action}.evidence_refs",
                    code="RESEARCH_EVIDENCE_INSUFFICIENT",
                ),
            }
        raw_exhaustion = raw.get("opportunity_exhaustion") or {}
        if not isinstance(raw_exhaustion, dict):
            raise StrategyContractError(
                "CONCRETE_OPPORTUNITY_EXHAUSTION_REQUIRED",
                "opportunity_exhaustion must be an object",
            )
        opportunity_exhaustion = {
            "surfaces_checked": _string_list(
                raw_exhaustion.get("surfaces_checked") or [],
                field="opportunity_exhaustion.surfaces_checked",
                code="CONCRETE_OPPORTUNITY_EXHAUSTION_REQUIRED",
            ),
            "evaluated_option_ids": _string_list(
                raw_exhaustion.get("evaluated_option_ids") or [],
                field="opportunity_exhaustion.evaluated_option_ids",
                code="CONCRETE_OPPORTUNITY_EXHAUSTION_REQUIRED",
            ),
            "conclusion": str(raw_exhaustion.get("conclusion") or "").strip()
            or None,
        }
        normalized_conflicts: list[dict[str, Any]] = []
        for conflict in raw.get("evidence_conflicts") or []:
            if not isinstance(conflict, dict):
                raise StrategyContractError(
                    "EVIDENCE_PROVENANCE_INCOMPLETE",
                    "evidence_conflicts must contain objects",
                )
            conflict_status = _required_text(
                conflict.get("status"),
                field="evidence_conflicts.status",
                code="EVIDENCE_CONFLICT_UNRESOLVED",
            )
            if conflict_status not in EVIDENCE_CONFLICT_STATUSES:
                raise StrategyContractError(
                    "EVIDENCE_CONFLICT_UNRESOLVED",
                    f"unsupported evidence conflict status: {conflict_status}",
                )
            normalized_conflicts.append(
                {
                    "question": _required_text(
                        conflict.get("question"),
                        field="evidence_conflicts.question",
                        code="EVIDENCE_CONFLICT_UNRESOLVED",
                    ),
                    "source_refs": _string_list(
                        conflict.get("source_refs") or [],
                        field="evidence_conflicts.source_refs",
                        code="EVIDENCE_CONFLICT_UNRESOLVED",
                    ),
                    "status": conflict_status,
                    "resolution": str(conflict.get("resolution") or "").strip()
                    or None,
                    "decision_impact": _required_text(
                        conflict.get("decision_impact"),
                        field="evidence_conflicts.decision_impact",
                        code="EVIDENCE_CONFLICT_UNRESOLVED",
                    ),
                }
            )
        row_snapshot_id = str(
            raw.get("evidence_snapshot_id") or evidence_snapshot_id
        ).strip()
        if row_snapshot_id != evidence_snapshot_id:
            raise StrategyContractError(
                "EVIDENCE_CONFLICT_UNRESOLVED",
                f"site {site_id} references a different evidence snapshot",
            )
        rows_by_site[site_id] = {
            "site_id": site_id,
            "site_language": str(
                raw.get("site_language") or site.get("language_code") or ""
            ).strip()
            or None,
            "site_market": str(
                raw.get("site_market") or site.get("market") or ""
            ).strip()
            or None,
            "evidence_snapshot_id": evidence_snapshot_id,
            "research_questions": _string_list(
                raw.get("research_questions") or [],
                field="research_questions",
                code="RESEARCH_PORTFOLIO_INCOMPLETE",
            ),
            "actions_considered": actions_considered,
            "material_options": material_options,
            "opportunity_exhaustion": opportunity_exhaustion,
            "action_assessments": normalized_assessments,
            "hard_blockers": [
                _normalize_site_hard_blocker(item)
                for item in (raw.get("hard_blockers") or [])
            ],
            "sources_attempted": _string_list(
                raw.get("sources_attempted") or [],
                field="sources_attempted",
                code="RESEARCH_PORTFOLIO_INCOMPLETE",
            ),
            "evidence_sources": sources,
            "missing_evidence": _string_list(
                raw.get("missing_evidence") or [],
                field="missing_evidence",
                code="RESEARCH_PORTFOLIO_INCOMPLETE",
            ),
            "evidence_conflicts": normalized_conflicts,
            "research_conclusion": _required_text(
                raw.get("research_conclusion"),
                field="research_conclusion",
                code="RESEARCH_PORTFOLIO_INCOMPLETE",
            ),
        }
    missing = sorted(set(sites_by_id) - set(rows_by_site))
    if missing:
        raise StrategyContractError(
            "RESEARCH_PORTFOLIO_INCOMPLETE",
            "research portfolio is missing sites: " + ", ".join(missing),
        )
    return [rows_by_site[str(site["id"])] for site in discovered_sites]


def _site_host(site: dict[str, Any]) -> str:
    raw = str(site.get("base_url") or site.get("domain") or "").strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").casefold().removeprefix("www.")


def _normalize_target_identity(
    *,
    business_id: str,
    site: dict[str, Any],
    action: str,
    concrete_action: str,
    target_identity: dict[str, Any],
    user_intent: str,
    topic: str | None,
) -> dict[str, Any]:
    target_url = str(target_identity.get("target_url") or "").strip() or None
    remote_object_id = (
        str(target_identity.get("remote_object_id") or "").strip() or None
    )
    local_object_id = (
        str(target_identity.get("local_object_id") or "").strip() or None
    )
    raw_intent = str(
        target_identity.get("intent_key") or user_intent or topic or ""
    ).strip()
    intent_key = _intent_key(raw_intent)
    canonical_url: str | None = None
    shared_identity: dict[str, str] | None = None
    if action in {"update_article", "on_page_fix"}:
        if target_url:
            try:
                canonical_url = normalize_canonical_url(
                    target_url,
                    site_url=site.get("base_url"),
                    site_domain=site.get("domain"),
                )
            except ValueError as error:
                raise StrategyContractError(
                    "TARGET_IDENTITY_UNRESOLVED", str(error)
                ) from error
        object_identity = canonical_url or remote_object_id or local_object_id
        if not object_identity:
            raise StrategyContractError(
                "TARGET_IDENTITY_UNRESOLVED",
                f"{action} requires a URL or stable object identity",
            )
        if action == "update_article" and canonical_url:
            shared_identity = strategy_identity(
                business_id,
                site_id=str(site["id"]),
                market=site.get("market"),
                language_code=site.get("language_code"),
                target_url=canonical_url,
                site_url=site.get("base_url"),
                site_domain=site.get("domain"),
                action="update_article",
            )
        else:
            lock_scope = "url_or_object"
            lock_material = object_identity
    elif action == "new_article":
        if not intent_key:
            raise StrategyContractError(
                "TARGET_IDENTITY_UNRESOLVED",
                "new_article requires a stable search intent",
            )
        shared_identity = strategy_identity(
            business_id,
            site_id=str(site["id"]),
            market=site.get("market"),
            language_code=site.get("language_code"),
            topic_cluster_id=intent_key,
            query=intent_key,
            action="new_article",
        )
    else:
        lock_scope = "site_result"
        lock_material = concrete_action
    if shared_identity is not None:
        scope_key = shared_identity["scope_key"]
        lock_scope = shared_identity["lock_scope"]
    else:
        scope_key = _stable_hash(
            {
                "business_id": business_id,
                "site_id": str(site["id"]),
                "scope": lock_scope,
                "target": lock_material,
            }
        )
    return {
        "target_url": canonical_url or target_url,
        "canonical_url": canonical_url or "",
        "remote_object_id": remote_object_id,
        "local_object_id": local_object_id,
        "intent_key": intent_key or None,
        "topic_cluster": (
            str(target_identity.get("topic_cluster") or "").strip() or None
        ),
        "scope_key": scope_key,
        "lock_key": scope_key,
        "lock_scope": lock_scope,
    }


def normalize_proposed_actions(
    actions: list[dict[str, Any]],
    *,
    business_id: str,
    discovered_sites: list[dict[str, Any]],
    research_portfolio: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _reject_legacy_candidate_keys(actions)
    if not actions:
        raise StrategyContractError(
            "RESEARCH_PORTFOLIO_INCOMPLETE",
            "proposed actions must include every discovered site",
        )
    sites_by_id = {str(site["id"]): site for site in discovered_sites}
    research_sites = {str(item["site_id"]) for item in research_portfolio}
    unresolved_conflict_sites = {
        str(item["site_id"])
        for item in research_portfolio
        if any(
            conflict.get("status") != "resolved"
            for conflict in item.get("evidence_conflicts") or []
        )
    }
    covered_sites: set[str] = set()
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for sequence, raw in enumerate(actions, start=1):
        site_id = _required_text(
            raw.get("site_id"),
            field="site_id",
            code="SITE_OUT_OF_SCOPE",
        )
        site = sites_by_id.get(site_id)
        if not site or site_id not in research_sites:
            raise StrategyContractError(
                "SITE_OUT_OF_SCOPE",
                f"proposed action site is outside researched Run scope: {site_id}",
            )
        action = _required_text(
            raw.get("action"),
            field="action",
            code="ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
        )
        if action not in ACTION_TYPES:
            raise StrategyContractError(
                "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                f"unsupported proposed action: {action}",
            )
        schedule_request = _required_text(
            raw.get("schedule_request"),
            field="schedule_request",
            code="ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
        )
        if schedule_request not in SCHEDULE_REQUESTS:
            raise StrategyContractError(
                "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                f"unsupported schedule request: {schedule_request}",
            )
        expected = {
            "hold": "hold",
            "configuration_repair": "configuration_repair",
        }.get(action)
        if expected and schedule_request != expected:
            raise StrategyContractError(
                "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                f"{action} requires schedule_request={expected}",
            )
        if schedule_request in {"hold", "configuration_repair"} and (
            action != schedule_request
        ):
            raise StrategyContractError(
                "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                "hold/configuration_repair schedules must match the action",
            )
        action_type = str(raw.get("action_type") or "").strip() or None
        concrete_action = action_type if action == "on_page_fix" else action
        if action == "on_page_fix" and concrete_action not in CONCRETE_ON_PAGE_ACTIONS:
            raise StrategyContractError(
                "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                "on_page_fix requires a supported concrete action_type",
            )
        expected_fields = list(raw.get("expected_fields") or [])
        if action in {"new_article", "update_article"}:
            article_required_fields = (
                "title",
                "body",
                "meta_title",
                "meta_description",
            )
            requested_article_fields = [str(field) for field in expected_fields]
            if {field.casefold() for field in requested_article_fields} & {
                "images",
                "image_alts",
            }:
                requested_article_fields.extend(["images", "image_alts"])
            expected_fields = list(
                dict.fromkeys(
                    [*article_required_fields, *requested_article_fields]
                )
            )
        if action == "on_page_fix":
            page_type_by_action = {
                "homepage_seo": "homepage",
                "product_seo": "product",
                "category_seo": "category",
                "product_image_alt": "product",
            }
            expected_page_type = page_type_by_action[concrete_action]
            if raw.get("page_type") != expected_page_type:
                raise StrategyContractError(
                    "TARGET_IDENTITY_UNRESOLVED",
                    f"{concrete_action} requires page_type={expected_page_type}",
                )
            if not expected_fields:
                raise StrategyContractError(
                    "TARGET_IDENTITY_UNRESOLVED",
                    "on_page_fix requires expected_fields",
                )
            protected = {
                "url",
                "slug",
                "handle",
                "canonical",
                "redirect",
                "delete",
                "price",
                "inventory",
                "variant",
                "variants",
                "collection_membership",
            }
            requested_protected = sorted(
                protected & {str(field).casefold() for field in expected_fields}
            )
            if requested_protected:
                raise StrategyContractError(
                    "PROTECTED_FIELD_REQUESTED",
                    "protected fields are not writable: "
                    + ", ".join(requested_protected),
                )
        topic = str(raw.get("topic") or "").strip() or None
        if action in {"new_article", "update_article"} and not topic:
            raise StrategyContractError(
                "TARGET_IDENTITY_UNRESOLVED",
                f"{action} requires a topic",
            )
        target_identity = dict(raw.get("target_identity") or {})
        user_intent = _required_text(
            raw.get("user_intent"),
            field="user_intent",
            code="RESEARCH_EVIDENCE_INSUFFICIENT",
        )
        identity = _normalize_target_identity(
            business_id=business_id,
            site=site,
            action=action,
            concrete_action=concrete_action,
            target_identity=target_identity,
            user_intent=user_intent,
            topic=topic,
        )
        evidence_refs = _string_list(
            raw.get("evidence_refs") or [],
            field="evidence_refs",
            code="RESEARCH_EVIDENCE_INSUFFICIENT",
        )
        if not evidence_refs:
            raise StrategyContractError(
                "RESEARCH_EVIDENCE_INSUFFICIENT",
                "every proposed action requires evidence_refs",
            )
        decision_reason = _required_text(
            raw.get("decision_reason"),
            field="decision_reason",
            code="RESEARCH_EVIDENCE_INSUFFICIENT",
        )
        option_id = "proposal-" + _stable_hash(
            {
                "site_id": site_id,
                "action": action,
                "concrete_action": concrete_action,
                "identity": identity,
                "topic": topic,
            }
        )[:32]
        if option_id in seen_ids:
            raise StrategyContractError(
                "TARGET_CONFLICT_ACTIVE",
                "duplicate proposed action target in the same Run",
            )
        seen_ids.add(option_id)
        covered_sites.add(site_id)
        risk_level = str(raw.get("risk_level") or "medium").strip()
        evidence_level = _evidence_level(
            raw.get("evidence_level"), field="evidence_level"
        )
        if action in CONCRETE_RESEARCH_ACTIONS and evidence_level == "unsafe":
            raise StrategyContractError(
                "UNSAFE_EXPERIMENT_REJECTED",
                "an unsafe proposed action cannot enter the executable plan",
            )
        corrective_of_action_id = (
            str(raw.get("corrective_of_action_id") or "").strip() or None
        )
        if corrective_of_action_id and action != "update_article":
            raise StrategyContractError(
                "CORRECTIVE_ACTION_INVALID",
                "only update_article may repair a failed content Action",
            )
        if corrective_of_action_id:
            try:
                corrective_of_action_id = str(UUID(corrective_of_action_id))
            except ValueError as error:
                raise StrategyContractError(
                    "CORRECTIVE_ACTION_INVALID",
                    "corrective_of_action_id must be a Strategy Action UUID",
                ) from error
        normalized.append(
            {
                "option_id": option_id,
                "proposal_sequence": sequence,
                "option_origin": "ai_proposed_action",
                "contract_version": CONTRACT_VERSION,
                "site_id": site_id,
                "site_name": site.get("name"),
                "editorial_action": action,
                "strategy_type": concrete_action,
                "action_type": concrete_action,
                "requested_schedule_class": schedule_request,
                "schedule_class": schedule_request,
                "topic": topic,
                "query": topic,
                "title": str(raw.get("title") or topic or action).strip(),
                "user_intent": user_intent,
                "reason": decision_reason,
                "recommended_action": decision_reason,
                "decision_reason": decision_reason,
                "evidence_refs": evidence_refs,
                "evidence_level": evidence_level,
                "evidence": {
                    "refs": evidence_refs,
                    "research_portfolio_hash": _stable_hash(research_portfolio),
                },
                "alternatives_considered": list(
                    raw.get("alternatives_considered") or []
                ),
                "hypothesis": str(raw.get("hypothesis") or "").strip() or None,
                "success_metrics": _string_list(
                    raw.get("success_metrics") or [],
                    field="success_metrics",
                    code="RESEARCH_EVIDENCE_INSUFFICIENT",
                ),
                "reevaluation_condition": (
                    str(raw.get("reevaluation_condition") or "").strip() or None
                ),
                "priority": str(raw.get("priority") or "P2"),
                "risk_level": risk_level,
                "risk_gate_passed": risk_level != "high",
                "corrective_of_action_id": corrective_of_action_id,
                "corrective_action_validated": False,
                "evidence_conflict_unresolved": site_id
                in unresolved_conflict_sites,
                "page_type": raw.get("page_type"),
                "target_asset_id": raw.get("target_asset_id")
                or identity.get("local_object_id"),
                "connector_id": raw.get("connector_id"),
                "connector_type": raw.get("connector_type"),
                "expected_fields": expected_fields,
                "target_identity": identity,
                **identity,
                "strategy_fingerprint": _stable_hash(
                    {
                        "site_id": site_id,
                        "action": concrete_action,
                        "scope_key": identity["scope_key"],
                        "reason": decision_reason,
                    }
                ),
                "evidence_fingerprint": _stable_hash(evidence_refs),
                "policy_version": CONTRACT_VERSION,
                "score": 0,
            }
        )
    missing = sorted(set(sites_by_id) - covered_sites)
    if missing:
        raise StrategyContractError(
            "RESEARCH_PORTFOLIO_INCOMPLETE",
            "proposed actions are missing sites: " + ", ".join(missing),
        )
    return normalized


def _capability_gate(
    action: dict[str, Any],
    *,
    site: dict[str, Any],
    capability: dict[str, Any],
) -> dict[str, Any]:
    item = dict(action)
    concrete_action = str(item.get("action_type") or item.get("strategy_type") or "")
    if not bool(site.get("strategy_enabled")):
        return {
            **item,
            "strategy_type": "configuration_repair",
            "action_type": "configuration_repair",
            "editorial_action": "configuration_repair",
            "schedule_class": "configuration_repair",
            "reason_code": "SITE_DISABLED",
            "block_reason": "Site strategy is disabled.",
            "unlock_condition": "Enable strategy after site configuration review.",
        }
    issues = list(capability.get("configuration_issues") or [])
    permission = (capability.get("supported_actions") or {}).get(concrete_action)
    adapter_identity = (capability.get("action_adapters") or {}).get(
        concrete_action
    )
    declared_connector = (
        str((adapter_identity or {}).get("connector_type") or "").casefold()
        if isinstance(adapter_identity, dict)
        else ""
    )
    article_action = concrete_action in {"new_article", "update_article"}
    article_adapter_unavailable = article_action and (
        not isinstance(adapter_identity, dict)
        or adapter_identity.get("read") is not True
        or adapter_identity.get("write") is not True
        or adapter_identity.get("readback") is not True
        or not declared_connector
    )
    article_fields = set(
        ((capability.get("supported_fields") or {}).get("articles") or ())
    )
    image_connector = (capability.get("connectors") or {}).get("images") or {}
    requested_article_fields = {
        str(field).casefold() for field in (item.get("expected_fields") or ())
    }
    inline_media_requested = bool(
        requested_article_fields & {"images", "image_alts"}
    )
    cover_media_requested = "cover_image" in requested_article_fields
    media_connector_available = (
        image_connector.get("status") == "available"
        and image_connector.get("write") is True
        and (
            image_connector.get("upload") is True
            or image_connector.get("ingest") is True
        )
    )
    article_media_unavailable = article_action and (
        (
            inline_media_requested
            and not (
                {"images", "image_alts"} <= article_fields
                and media_connector_available
            )
        )
        or (
            cover_media_requested
            and not (
                "cover_image" in article_fields and media_connector_available
            )
        )
    )
    adapter_unavailable = (
        concrete_action not in EXECUTABLE_ACTIONS
        or article_adapter_unavailable
        or article_media_unavailable
        or (
            concrete_action in CONCRETE_ON_PAGE_ACTIONS
            and (
                not isinstance(adapter_identity, dict)
                or adapter_identity.get("readback") is not True
                or (
                    item.get("connector_type")
                    and str(adapter_identity.get("connector_type") or "").casefold()
                    != str(item.get("connector_type") or "").casefold()
                )
            )
        )
    )
    if article_action and not adapter_unavailable:
        # A proposal may describe the site's business role (for example
        # ``main``), but connector identity is a verified capability fact. Bind
        # every executable article plan to that fact so an AI/source label can
        # neither select nor accidentally conflict with the runtime adapter.
        item["connector_type"] = str(adapter_identity["connector_type"])
    if item.get("editorial_action") in {"hold", "configuration_repair"}:
        return item
    if adapter_unavailable or permission not in {
        "approval_required",
        "allowed",
        "execute",
    }:
        result = "configuration_repair" if issues or adapter_unavailable else "hold"
        return {
            **item,
            "original_action": concrete_action,
            "strategy_type": result,
            "action_type": result,
            "editorial_action": result,
            "schedule_class": result,
            "reason_code": "CAPABILITY_MISSING",
            "block_reason": (
                "Required connector capability or independent readback is unavailable."
            ),
            "unlock_condition": "Repair and refresh the site capability snapshot.",
            "capability_issues": issues,
        }
    return item


def review_proposed_actions(
    actions: list[dict[str, Any]],
    *,
    discovered_sites: list[dict[str, Any]],
    capability_snapshot: dict[str, Any],
    scope_locks: dict[str, str],
    safety_action_ceiling: int,
    site_safety_ceilings: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Apply hard gates and capacity without re-ranking AI proposals."""
    sites_by_id = {str(site["id"]): site for site in discovered_sites}
    capabilities = {
        str(item.get("site_id")): item
        for item in capability_snapshot.get("sites") or []
    }
    ceiling = max(0, min(int(safety_action_ceiling), 200))
    # A site quota is a current-wave safety gate, not an editorial budget.
    # The unified Action worker may only have one remote write in flight per
    # site; additional qualified actions remain persisted as Deferred.
    site_ceilings = {
        str(key): min(1, max(0, int(value)))
        for key, value in (site_safety_ceilings or {}).items()
    }
    execute_count = 0
    site_execute_counts: dict[str, int] = {}
    reviewed: list[dict[str, Any]] = []
    for raw in sorted(
        actions, key=lambda item: int(item.get("proposal_sequence") or 0)
    ):
        site_id = str(raw["site_id"])
        item = _capability_gate(
            raw,
            site=sites_by_id[site_id],
            capability=capabilities.get(site_id) or {},
        )
        current_schedule = str(item.get("schedule_class") or "")
        if current_schedule in {"hold", "configuration_repair"}:
            item["wave_number"] = None
            item["reevaluation_condition"] = (
                item.get("reevaluation_condition")
                or item.get("unlock_condition")
                or "Re-evaluate after the blocking condition is resolved."
            )
        elif (
            item.get("scope_key") in scope_locks
            and item.get("corrective_action_validated") is not True
        ):
            item.update(
                {
                    "schedule_class": "deferred",
                    "reason_code": "TARGET_CONFLICT_ACTIVE",
                    "schedule_reason": scope_locks[str(item["scope_key"])],
                    "reevaluation_condition": (
                        item.get("reevaluation_condition")
                        or "Re-evaluate after the exact target conflict or cooldown ends."
                    ),
                    "wave_number": None,
                }
            )
        elif item.get("risk_gate_passed") is False:
            item.update(
                {
                    "schedule_class": "hold",
                    "reason_code": "HIGH_RISK_REVIEW_REQUIRED",
                    "schedule_reason": "High-risk content requires human approval.",
                    "reevaluation_condition": "Resume after high-risk review.",
                    "wave_number": None,
                }
            )
        elif item.get("evidence_conflict_unresolved") is True:
            item.update(
                {
                    "schedule_class": "deferred",
                    "reason_code": "EVIDENCE_CONFLICT_UNRESOLVED",
                    "schedule_reason": (
                        "Material evidence conflict requires resolution before execution."
                    ),
                    "reevaluation_condition": (
                        item.get("reevaluation_condition")
                        or "Resolve or explicitly escalate the conflicting evidence."
                    ),
                    "wave_number": None,
                }
            )
        elif execute_count >= ceiling:
            item.update(
                {
                    "schedule_class": "deferred",
                    "reason_code": "SAFETY_CEILING_EXCEEDED",
                    "schedule_reason": (
                        "Qualified action exceeded the runaway safety ceiling."
                    ),
                    "reevaluation_condition": (
                        item.get("reevaluation_condition")
                        or "Resume in a later wave after verified readback."
                    ),
                    "wave_number": None,
                }
            )
        elif site_execute_counts.get(site_id, 0) >= site_ceilings.get(
            site_id, min(1, ceiling)
        ):
            item.update(
                {
                    "schedule_class": "deferred",
                    "reason_code": "SITE_SAFETY_CEILING_EXCEEDED",
                    "schedule_reason": (
                        "Qualified action exceeded the site write safety ceiling."
                    ),
                    "reevaluation_condition": (
                        item.get("reevaluation_condition")
                        or "Resume after the site's earlier Action passes readback."
                    ),
                    "wave_number": None,
                }
            )
        elif current_schedule == "deferred":
            item.update(
                {
                    "schedule_class": "deferred",
                    "reason_code": "DEFERRED_JUSTIFICATION_REQUIRED",
                    "schedule_reason": (
                        "The AI requested Deferred, but no backend-verifiable "
                        "target conflict, evidence conflict, or safety ceiling "
                        "requires postponement."
                    ),
                    "reevaluation_condition": (
                        "Revise the research and choose Execute Now, or supply "
                        "evidence that activates a verifiable backend gate."
                    ),
                    "wave_number": None,
                }
            )
        else:
            site_wave = site_execute_counts.get(site_id, 0) + 1
            item.update(
                {
                    "schedule_class": "execute_now",
                    "schedule_reason": item.get("reason"),
                    "reevaluation_condition": None,
                    "wave_number": site_wave,
                }
            )
            execute_count += 1
            site_execute_counts[site_id] = site_wave
        reviewed.append(item)
    return reviewed


def build_site_results(
    discovered_sites: list[dict[str, Any]],
    reviewed_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_site: dict[str, list[dict[str, Any]]] = {}
    for item in reviewed_actions:
        by_site.setdefault(str(item["site_id"]), []).append(item)
    results: list[dict[str, Any]] = []
    order = {
        "execute_now": 0,
        "deferred": 1,
        "configuration_repair": 2,
        "hold": 3,
    }
    for site in discovered_sites:
        site_id = str(site["id"])
        items = by_site.get(site_id) or []
        if not items:
            raise StrategyContractError(
                "PORTFOLIO_COVERAGE_INCOMPLETE",
                f"reviewed plan has no result for site {site_id}",
            )
        primary = sorted(
            items,
            key=lambda item: (
                order.get(str(item.get("schedule_class")), 9),
                int(item.get("proposal_sequence") or 0),
            ),
        )[0]
        results.append(
            {
                "site_id": site_id,
                "site_name": site.get("name"),
                "action": primary.get("strategy_type"),
                "schedule_class": primary.get("schedule_class"),
                "reason": (
                    primary.get("schedule_reason")
                    or primary.get("block_reason")
                    or primary.get("reason")
                ),
                "reason_code": primary.get("reason_code"),
                "unlock_condition": primary.get("unlock_condition"),
                "reevaluation_condition": primary.get(
                    "reevaluation_condition"
                ),
                "selected_option_id": primary.get("option_id"),
                "qualified_option_count": sum(
                    item.get("schedule_class") in {"execute_now", "deferred"}
                    for item in items
                ),
                "option_count": len(items),
                "option_ids": [item.get("option_id") for item in items],
                "scheduled_actions": [
                    {
                        "option_id": item.get("option_id"),
                        "action": item.get("strategy_type"),
                        "schedule_class": item.get("schedule_class"),
                        "reason_code": item.get("reason_code"),
                    }
                    for item in items
                ],
            }
        )
    return results


def review_zero_action(
    *,
    research_portfolio: list[dict[str, Any]],
    reviewed_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    executable_or_verified_deferred = [
        item
        for item in reviewed_actions
        if item.get("schedule_class") == "execute_now"
        or (
            item.get("schedule_class") == "deferred"
            and item.get("reason_code") in VERIFIED_DEFER_REASON_CODES
        )
    ]
    if not research_portfolio:
        return {
            "required": True,
            "result": "research_revision_required",
            "reason_codes": ["RESEARCH_PORTFOLIO_INCOMPLETE"],
            "missing_evidence": [
                {
                    "site_id": None,
                    "code": "RESEARCH_PORTFOLIO_INCOMPLETE",
                    "missing": "research_portfolio",
                }
            ],
        }
    missing: list[dict[str, Any]] = []
    research_by_site = {
        str(item["site_id"]): item for item in research_portfolio
    }
    actions_by_site: dict[str, list[dict[str, Any]]] = {}
    for action in reviewed_actions:
        actions_by_site.setdefault(str(action["site_id"]), []).append(action)
    hard_blocked_sites = 0
    for site_id, research in research_by_site.items():
        actions = actions_by_site.get(site_id) or []
        unjustified_deferred = [
            action
            for action in actions
            if action.get("schedule_class") == "deferred"
            and action.get("reason_code") == "DEFERRED_JUSTIFICATION_REQUIRED"
        ]
        if unjustified_deferred:
            missing.append(
                {
                    "site_id": site_id,
                    "code": "DEFERRED_JUSTIFICATION_REQUIRED",
                    "option_ids": sorted(
                        str(action.get("option_id") or "")
                        for action in unjustified_deferred
                    ),
                    "missing": (
                        "backend-verifiable defer gate or Execute Now schedule"
                    ),
                }
            )
            continue
        if any(
            action.get("schedule_class") == "execute_now"
            or (
                action.get("schedule_class") == "deferred"
                and action.get("reason_code") in VERIFIED_DEFER_REASON_CODES
            )
            for action in actions
        ):
            continue
        hard_codes = {
            str(action.get("reason_code") or "")
            for action in actions
            if action.get("reason_code")
        }
        has_hard_blocker = bool(research.get("hard_blockers")) or bool(
            hard_codes & SITE_HARD_BLOCK_CODES
        )
        if has_hard_blocker:
            hard_blocked_sites += 1
            continue
        considered = set(research.get("actions_considered") or [])
        if considered != ACTION_TYPES:
            missing.append(
                {
                    "site_id": site_id,
                    "code": "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                    "missing_actions": sorted(ACTION_TYPES - considered),
                }
            )
        material_options = list(research.get("material_options") or [])
        if not material_options:
            missing.append(
                {
                    "site_id": site_id,
                    "code": "CONCRETE_OPPORTUNITY_EXHAUSTION_REQUIRED",
                    "missing": (
                        "concrete target or topic opportunities with per-option outcomes"
                    ),
                }
            )
        qualified_option_ids = sorted(
            str(option.get("option_id"))
            for option in material_options
            if option.get("outcome") == "qualified"
        )
        if qualified_option_ids:
            missing.append(
                {
                    "site_id": site_id,
                    "code": "QUALIFIED_OPPORTUNITY_NOT_SCHEDULED",
                    "qualified_option_ids": qualified_option_ids,
                }
            )
        exhaustion = dict(research.get("opportunity_exhaustion") or {})
        surfaces_checked = set(exhaustion.get("surfaces_checked") or [])
        evaluated_option_ids = set(exhaustion.get("evaluated_option_ids") or [])
        material_option_ids = {
            str(option.get("option_id") or "") for option in material_options
        }
        missing_surfaces = sorted(
            REQUIRED_HOLD_RESEARCH_SURFACES - surfaces_checked
        )
        if (
            missing_surfaces
            or evaluated_option_ids != material_option_ids
            or not exhaustion.get("conclusion")
        ):
            missing.append(
                {
                    "site_id": site_id,
                    "code": "CONCRETE_OPPORTUNITY_EXHAUSTION_REQUIRED",
                    "missing_surfaces": missing_surfaces,
                    "unevaluated_option_ids": sorted(
                        material_option_ids - evaluated_option_ids
                    ),
                    "unknown_option_ids": sorted(
                        evaluated_option_ids - material_option_ids
                    ),
                    "missing_conclusion": not bool(exhaustion.get("conclusion")),
                }
            )
        material_signatures = {
            (
                str(option.get("option_id") or ""),
                str(option.get("scope_key") or ""),
                str(option.get("outcome") or ""),
            )
            for option in material_options
        }
        if len(material_signatures) != len(material_options):
            missing.append(
                {
                    "site_id": site_id,
                    "code": "RESEARCH_EVIDENCE_INSUFFICIENT",
                    "missing": "materially distinct options",
                }
            )
        assessments = dict(research.get("action_assessments") or {})
        missing_assessments = sorted(ACTION_TYPES - set(assessments))
        incomplete_assessments = sorted(
            action
            for action, assessment in assessments.items()
            if not assessment.get("reason") or not assessment.get("evidence_refs")
        )
        if missing_assessments or incomplete_assessments:
            missing.append(
                {
                    "site_id": site_id,
                    "code": "ACTION_SPACE_ARTIFICIALLY_RESTRICTED",
                    "missing_action_assessments": missing_assessments,
                    "incomplete_action_assessments": incomplete_assessments,
                }
            )
        if research.get("missing_evidence"):
            missing.append(
                {
                    "site_id": site_id,
                    "code": "RESEARCH_EVIDENCE_INSUFFICIENT",
                    "missing": list(research["missing_evidence"]),
                }
            )
        unresolved_conflicts = [
            conflict
            for conflict in research.get("evidence_conflicts") or []
            if conflict.get("status") != "resolved"
        ]
        if unresolved_conflicts:
            missing.append(
                {
                    "site_id": site_id,
                    "code": "EVIDENCE_CONFLICT_UNRESOLVED",
                    "missing": "resolve or explicitly defer material evidence conflicts",
                    "conflicts": unresolved_conflicts,
                }
            )
        successful_sources = [
            source
            for source in research.get("evidence_sources") or []
            if source.get("collection_status") in {"success", "partial"}
        ]
        if not successful_sources:
            missing.append(
                {
                    "site_id": site_id,
                    "code": "RESEARCH_EVIDENCE_INSUFFICIENT",
                    "missing": "successful evidence source",
                }
            )
        elif not any(
            source.get("source_type") in SECOND_CHANNEL_SOURCE_TYPES
            for source in successful_sources
        ):
            missing.append(
                {
                    "site_id": site_id,
                    "code": "RESEARCH_EVIDENCE_INSUFFICIENT",
                    "missing": "second evidence channel",
                }
            )
        if not qualified_option_ids:
            missing.append(
                {
                    "site_id": site_id,
                    "code": "SAFE_EXPERIMENT_REQUIRED",
                    "missing": (
                        "a safe learning action or a validated site-level hard blocker"
                    ),
                }
            )
    if missing:
        return {
            "required": True,
            "result": "research_revision_required",
            "reason_codes": sorted({item["code"] for item in missing}),
            "missing_evidence": missing,
        }
    if executable_or_verified_deferred:
        return {
            "required": False,
            "result": "not_required",
            "reason_codes": [],
            "missing_evidence": [],
        }
    all_configuration_repairs = bool(reviewed_actions) and all(
        item.get("schedule_class") == "configuration_repair"
        for item in reviewed_actions
    )
    if (
        hard_blocked_sites == len(research_by_site)
        and all_configuration_repairs
    ):
        return {
            "required": True,
            "result": "configuration_skipped",
            "reason_codes": ["CONFIGURATION_REPAIR_SKIPPED"],
            "missing_evidence": [],
        }
    if hard_blocked_sites == len(research_by_site):
        return {
            "required": True,
            "result": "hard_blocked",
            "reason_codes": ["HARD_BLOCKED"],
            "missing_evidence": [],
        }
    # Every non-hard-blocked site without an executable or verified Deferred
    # action has already received SAFE_EXPERIMENT_REQUIRED above. This fallback
    # is defensive and must never silently restore the retired all-Hold path.
    return {
        "required": True,
        "result": "research_revision_required",
        "reason_codes": ["SAFE_EXPERIMENT_REQUIRED"],
        "missing_evidence": [
            {
                "site_id": None,
                "code": "SAFE_EXPERIMENT_REQUIRED",
                "missing": (
                    "a safe learning action or a validated site-level hard blocker"
                ),
            }
        ],
    }


async def evaluate_zero_action_review(
    session: AsyncSession,
    *,
    run: dict[str, Any],
    reviewed_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply the zero-action contract without permitting soft site-wide Hold."""
    research_portfolio = list(run.get("research_portfolio") or [])
    return review_zero_action(
        research_portfolio=research_portfolio,
        reviewed_actions=reviewed_actions,
    )


async def _patch_run(
    session: AsyncSession,
    *,
    run_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET decision=decision || CAST(:patch AS jsonb), updated_at=now()
             WHERE id=CAST(:run_id AS uuid)
               AND task_type='review'
               AND payload->>'kind'='strategy_run'
            """
        ),
        {
            "run_id": run_id,
            "patch": json.dumps(patch, ensure_ascii=False, default=str),
        },
    )
    await session.commit()
    from app.services.strategy_run_service import get_strategy_run

    result = await get_strategy_run(session, run_id=run_id)
    if not result:
        raise StrategyContractError(
            "STRATEGY_RUN_NOT_FOUND", "strategy run not found"
        )
    return result


async def capture_research_portfolio(
    session: AsyncSession,
    *,
    run_id: str,
    requested_by: str,
    idempotency_key: str,
    evidence_snapshot_id: str,
    portfolio: list[dict[str, Any]],
) -> dict[str, Any]:
    from app.services.strategy_run_service import (
        get_strategy_run,
        transition_strategy_run,
    )

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"strategy-research:{run_id}"},
    )
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        raise StrategyContractError(
            "STRATEGY_RUN_NOT_FOUND", "strategy run not found"
        )
    if str(run.get("evidence_snapshot_id") or "") != evidence_snapshot_id:
        raise StrategyContractError(
            "EVIDENCE_CONFLICT_UNRESOLVED",
            "research must bind the current Run evidence snapshot",
        )
    normalized = normalize_research_portfolio(
        portfolio,
        discovered_sites=list(run.get("discovered_sites") or []),
        evidence_snapshot_id=evidence_snapshot_id,
    )
    request_hash = _stable_hash(
        {
            "requested_by": requested_by,
            "evidence_snapshot_id": evidence_snapshot_id,
            "portfolio": normalized,
        }
    )
    receipt = dict(run.get("research_receipt") or {})
    if (
        receipt.get("idempotency_key") == idempotency_key
        and receipt.get("request_hash") == request_hash
    ):
        return {**run, "idempotency_replayed": True}
    if receipt.get("idempotency_key") == idempotency_key:
        raise StrategyContractError(
            "DECISION_INPUT_CHANGED",
            "idempotency key is bound to different research",
        )
    if run["status"] not in {"ai_researching", "research_revision_required"}:
        raise StrategyContractError(
            "STRATEGY_RUN_STAGE_INVALID",
            "research can only be captured during ai_researching or revision",
        )
    revision = int(run.get("research_revision") or 0) + 1
    patch = {
        "research_portfolio": normalized,
        "research_portfolio_hash": _stable_hash(normalized),
        "research_revision": revision,
        "research_receipt": {
            "requested_by": requested_by,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "contract_version": CONTRACT_VERSION,
        },
        "proposed_actions": [],
        "reviewed_actions": [],
        "zero_action_review": None,
        "next_action": "submit_proposed_actions",
    }
    if run["status"] == "research_revision_required":
        result = await transition_strategy_run(
            session,
            run_id=run_id,
            current_status="research_revision_required",
            next_status="ai_researching",
            updates=patch,
        )
    else:
        result = await _patch_run(session, run_id=run_id, patch=patch)
    return {**result, "idempotency_replayed": False}


async def submit_proposed_actions(
    session: AsyncSession,
    *,
    run_id: str,
    requested_by: str,
    idempotency_key: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    from app.services.strategy_run_service import (
        get_strategy_run,
        transition_strategy_run,
    )

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"strategy-proposals:{run_id}"},
    )
    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        raise StrategyContractError(
            "STRATEGY_RUN_NOT_FOUND", "strategy run not found"
        )
    receipt = dict(run.get("proposed_action_receipt") or {})
    research = list(run.get("research_portfolio") or [])
    if receipt:
        normalized = normalize_proposed_actions(
            actions,
            business_id=str(run["business_id"]),
            discovered_sites=list(run.get("discovered_sites") or []),
            research_portfolio=research,
        )
        request_hash = _stable_hash(
            {"requested_by": requested_by, "actions": normalized}
        )
        if (
            receipt.get("idempotency_key") == idempotency_key
            and receipt.get("request_hash") == request_hash
        ):
            return {**run, "idempotency_replayed": True}
        if receipt.get("idempotency_key") == idempotency_key:
            raise StrategyContractError(
                "DECISION_INPUT_CHANGED",
                "idempotency key is bound to different proposed actions",
            )
        if run["status"] != "ai_researching":
            raise StrategyContractError(
                "DECISION_INPUT_CHANGED",
                "proposed actions are immutable after submission; revise research first",
            )
    if run["status"] != "ai_researching":
        raise StrategyContractError(
            "STRATEGY_RUN_STAGE_INVALID",
            "proposed actions require captured research in ai_researching",
        )
    if not research:
        raise StrategyContractError(
            "RESEARCH_PORTFOLIO_INCOMPLETE",
            "capture the research portfolio before proposed actions",
        )
    normalized = normalize_proposed_actions(
        actions,
        business_id=str(run["business_id"]),
        discovered_sites=list(run.get("discovered_sites") or []),
        research_portfolio=research,
    )
    request_hash = _stable_hash(
        {"requested_by": requested_by, "actions": normalized}
    )
    result = await transition_strategy_run(
        session,
        run_id=run_id,
        current_status="ai_researching",
        next_status="proposed_actions_submitted",
        updates={
            "proposed_actions": normalized,
            "proposed_action_count": len(normalized),
            "proposed_action_hash": _stable_hash(normalized),
            "proposed_action_receipt": {
                "requested_by": requested_by,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "contract_version": CONTRACT_VERSION,
            },
            "next_action": "start_safety_review",
        },
    )
    return {**result, "idempotency_replayed": False}


def _required_strategy_data(strategy_type: str) -> list[str]:
    shared = [
        "research_portfolio",
        "evidence_snapshot",
        "capability_snapshot",
        "independent_readback",
    ]
    if strategy_type in {"new_article", "update_article"}:
        return [*shared, "site_content", "site_knowledge"]
    if strategy_type in CONCRETE_ON_PAGE_ACTIONS:
        return [*shared, "site_asset", "seo_audit"]
    return shared


async def _read_formal_plan(
    session: AsyncSession,
    *,
    plan_row: dict[str, Any],
) -> dict[str, Any]:
    plan_id = str(plan_row["id"])
    plan_payload = dict(plan_row.get("payload") or {})
    plan_decision = dict(plan_row.get("decision") or {})
    rows = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, status, priority, score,
                       site_id::text AS site_id, title, payload, decision,
                       created_at, updated_at
                  FROM seo_agent.tasks
                 WHERE task_type='review'
                   AND payload->>'kind'='seo_strategy'
                   AND payload->>'plan_id'=:plan_id
                 ORDER BY (payload->>'proposal_sequence')::integer NULLS LAST,
                          created_at ASC
                """
            ),
            {"plan_id": plan_id},
        )
    ).mappings().all()
    items: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        payload = dict(row.pop("payload") or {})
        decision = dict(row.pop("decision") or {})
        items.append(
            {
                **row,
                **decision,
                "option_id": payload.get("option_id"),
                "plan_id": plan_id,
                "strategy_run_id": payload.get("strategy_run_id"),
                "schedule_class": payload.get("schedule_class"),
            }
        )
    return {
        "id": plan_id,
        "business_id": plan_payload.get("business_id"),
        "analysis_batch_id": plan_payload.get("analysis_batch_id"),
        "source_audit_batch_id": plan_payload.get("source_audit_batch_id"),
        "source_audit_scanned_at": plan_payload.get("source_audit_scanned_at"),
        "action_budget": plan_decision.get("safety_action_ceiling", 0),
        "site_quotas": plan_decision.get("site_safety_ceilings") or {},
        "strategy_run_id": plan_payload.get("strategy_run_id"),
        "proposed_action_ids": plan_decision.get("proposed_action_ids") or [],
        "site_results": plan_decision.get("site_results") or [],
        "research_portfolio_hash": plan_decision.get(
            "research_portfolio_hash"
        ),
        "proposed_action_hash": plan_decision.get("proposed_action_hash"),
        "strategy_task_ids": plan_decision.get("strategy_task_ids") or [],
        "execute_now_strategy_ids": plan_decision.get(
            "execute_now_strategy_ids"
        )
        or [],
        "deferred_strategy_ids": plan_decision.get("deferred_strategy_ids")
        or [],
        "plan_date": plan_decision.get("plan_date"),
        "planned_actions": len(
            plan_decision.get("execute_now_strategy_ids") or []
        ),
        "deferred_actions": len(
            plan_decision.get("deferred_strategy_ids") or []
        ),
        "status": "active",
        "created_at": plan_row.get("created_at"),
        "items": items,
        "idempotency_replayed": True,
    }


async def _persist_formal_plan(
    session: AsyncSession,
    *,
    run: dict[str, Any],
    reviewed_actions: list[dict[str, Any]],
    site_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist one replayable formal Plan without candidate or keyword lineage."""
    run_id = str(run["run_id"])
    business_id = str(run["business_id"])
    proposed_action_hash = str(run.get("proposed_action_hash") or "")
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text AS id, payload, decision, created_at
                  FROM seo_agent.tasks
                 WHERE task_type='review'
                   AND payload->>'kind'='strategy_plan'
                   AND payload->>'strategy_run_id'=:run_id
                   AND decision->>'proposed_action_hash'=:proposed_action_hash
                   AND status IN ('queued', 'running', 'done', 'blocked')
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            ),
            {
                "run_id": run_id,
                "proposed_action_hash": proposed_action_hash,
            },
        )
    ).mappings().first()
    if existing:
        return await _read_formal_plan(session, plan_row=dict(existing))

    planned_options = [
        dict(option)
        for option in reviewed_actions
        if option.get("schedule_class") in {"execute_now", "deferred"}
    ]
    proposed_action_ids = [
        str(option["option_id"])
        for option in reviewed_actions
        if option.get("option_id")
    ]
    plan_date = datetime.now(timezone.utc).date().isoformat()
    plan_payload = {
        "kind": "strategy_plan",
        "business_id": business_id,
        "strategy_run_id": run_id,
        "analysis_batch_id": run_id,
        "source_audit_batch_id": None,
        "source_audit_scanned_at": (
            (run.get("evidence_snapshot") or {}).get("captured_at")
        ),
        "plan_contract_version": "ai-led-v1",
        "decision_source": "ai_proposed_actions",
    }
    initial_decision = {
        "safety_action_ceiling": int(run.get("action_budget") or 0),
        "site_safety_ceilings": dict(run.get("site_quotas") or {}),
        "proposed_action_ids": proposed_action_ids,
        "site_results": site_results,
        "research_portfolio_hash": run.get("research_portfolio_hash"),
        "proposed_action_hash": proposed_action_hash,
        "strategy_task_ids": [],
        "execute_now_strategy_ids": [],
        "deferred_strategy_ids": [],
        "plan_date": plan_date,
    }
    inserted = (
        await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, title, payload, decision)
                VALUES
                  ('review', 'queued', 'P2', :title,
                   CAST(:payload AS jsonb), CAST(:decision AS jsonb))
                RETURNING id::text AS id, created_at
                """
            ),
            {
                "title": f"AI strategy plan: {business_id}",
                "payload": json.dumps(plan_payload, ensure_ascii=False),
                "decision": json.dumps(initial_decision, ensure_ascii=False),
            },
        )
    ).mappings().one()
    plan_id = str(inserted["id"])
    items: list[dict[str, Any]] = []
    for option in planned_options:
        strategy = {
            key: value
            for key, value in option.items()
            if key
            not in {
                "id",
                "status",
                "created_at",
                "updated_at",
            }
        }
        strategy["business_id"] = business_id
        schedule_class = str(strategy["schedule_class"])
        task_payload = {
            "kind": "seo_strategy",
            "business_id": business_id,
            "strategy_run_id": run_id,
            "analysis_batch_id": run_id,
            "option_origin": "ai_proposed_action",
            "option_id": str(strategy["option_id"]),
            "plan_id": plan_id,
            "schedule_class": schedule_class,
            "schedule_reason": strategy.get("schedule_reason"),
            "wave_number": strategy.get("wave_number"),
            "reevaluation_condition": strategy.get(
                "reevaluation_condition"
            ),
            "scope_key": strategy.get("scope_key"),
            "lock_scope": strategy.get("lock_scope"),
            "lock_key": strategy.get("lock_key"),
            "proposal_sequence": strategy.get("proposal_sequence"),
            "research_portfolio_hash": run.get("research_portfolio_hash"),
            "proposed_action_hash": proposed_action_hash,
        }
        task_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO seo_agent.tasks
                      (task_type, status, priority, score, site_id, title,
                       payload, required_data, decision)
                    VALUES
                      ('review', 'queued', :priority, 0,
                       CAST(:site_id AS uuid), :title, CAST(:payload AS jsonb),
                       :required_data, CAST(:decision AS jsonb))
                    RETURNING id::text
                    """
                ),
                {
                    "priority": strategy.get("priority") or "P2",
                    "site_id": strategy["site_id"],
                    "title": strategy.get("title"),
                    "payload": json.dumps(task_payload, ensure_ascii=False),
                    "required_data": _required_strategy_data(
                        str(strategy.get("strategy_type") or "")
                    ),
                    "decision": json.dumps(strategy, ensure_ascii=False),
                },
            )
        ).scalar_one()
        items.append(
            {
                **strategy,
                "id": str(task_id),
                "plan_id": plan_id,
                "strategy_run_id": run_id,
                "status": "pending",
            }
        )
    strategy_ids = [str(item["id"]) for item in items]
    execute_now_ids = [
        str(item["id"])
        for item in items
        if item.get("schedule_class") == "execute_now"
    ]
    deferred_ids = [
        str(item["id"])
        for item in items
        if item.get("schedule_class") == "deferred"
    ]
    final_decision = {
        **initial_decision,
        "strategy_task_ids": strategy_ids,
        "execute_now_strategy_ids": execute_now_ids,
        "deferred_strategy_ids": deferred_ids,
    }
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET decision=CAST(:decision AS jsonb), updated_at=now()
             WHERE id=CAST(:plan_id AS uuid)
            """
        ),
        {
            "plan_id": plan_id,
            "decision": json.dumps(final_decision, ensure_ascii=False),
        },
    )
    await session.execute(
        text(
            """
            UPDATE seo_agent.tasks
               SET status='canceled', finished_at=now(), updated_at=now(),
                   decision=decision || '{"superseded": true}'::jsonb
             WHERE task_type='review'
               AND payload->>'strategy_run_id'=:run_id
               AND payload->>'kind' IN ('strategy_plan', 'seo_strategy')
               AND id<>CAST(:plan_id AS uuid)
               AND COALESCE(payload->>'plan_id', id::text)
                   <>CAST(:plan_id_text AS text)
               AND status IN ('queued', 'running', 'blocked')
            """
        ),
        {
            "run_id": run_id,
            "plan_id": plan_id,
            "plan_id_text": plan_id,
        },
    )
    return {
        "id": plan_id,
        "business_id": business_id,
        "analysis_batch_id": run_id,
        "source_audit_batch_id": None,
        "source_audit_scanned_at": plan_payload[
            "source_audit_scanned_at"
        ],
        "action_budget": initial_decision["safety_action_ceiling"],
        "site_quotas": initial_decision["site_safety_ceilings"],
        "strategy_run_id": run_id,
        "proposed_action_ids": proposed_action_ids,
        "site_results": site_results,
        "research_portfolio_hash": run.get("research_portfolio_hash"),
        "proposed_action_hash": proposed_action_hash,
        "strategy_task_ids": strategy_ids,
        "execute_now_strategy_ids": execute_now_ids,
        "deferred_strategy_ids": deferred_ids,
        "plan_date": plan_date,
        "planned_actions": len(execute_now_ids),
        "deferred_actions": len(deferred_ids),
        "status": "active",
        "created_at": inserted["created_at"],
        "items": items,
        "idempotency_replayed": False,
    }


async def build_reviewed_plan(
    session: AsyncSession,
    *,
    run: dict[str, Any],
) -> dict[str, Any]:
    """Review AI proposals and persist the one formal Plan for the Run."""
    from app.services.strategy_effect_service import load_scope_locks
    from app.services.strategy_action_adapter_router import (
        StrategyActionAdapterRouter,
    )
    from app.services.strategy_action_service import (
        SQLActionStore,
        reconcile_stale_actions,
    )

    proposed = list(run.get("proposed_actions") or [])
    if not proposed:
        raise StrategyContractError(
            "RESEARCH_PORTFOLIO_INCOMPLETE",
            "formal planning requires submitted Proposed Actions",
        )
    stale_action_reconciliation = await reconcile_stale_actions(
        SQLActionStore(session),
        business_id=str(run["business_id"]),
        adapter=StrategyActionAdapterRouter(session),
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"ai-formal-plan:{run['business_id']}"},
    )
    proposed = await _validate_corrective_action_lineage(
        session,
        run=run,
        proposed_actions=proposed,
    )
    scope_locks = await load_scope_locks(
        session,
        business_id=str(run["business_id"]),
        exclude_strategy_run_id=str(run["run_id"]),
    )
    reviewed = review_proposed_actions(
        proposed,
        discovered_sites=list(run.get("discovered_sites") or []),
        capability_snapshot=dict(run.get("capability_snapshot") or {}),
        scope_locks=scope_locks,
        safety_action_ceiling=int(run.get("action_budget") or 0),
        site_safety_ceilings=dict(run.get("site_quotas") or {}),
    )
    site_results = build_site_results(
        list(run.get("discovered_sites") or []), reviewed
    )
    plan = await _persist_formal_plan(
        session,
        run=run,
        reviewed_actions=reviewed,
        site_results=site_results,
    )
    executable = [
        item
        for item in plan.get("items") or []
        if item.get("schedule_class") == "execute_now"
    ]
    return {
        "business_id": run["business_id"],
        "site_scope": list(run.get("discovered_sites") or []),
        "analysis_batch_id": run["run_id"],
        "plan": plan,
        "items": plan.get("items") or [],
        "reviewed_actions": reviewed,
        "stale_action_reconciliation": stale_action_reconciliation,
        "coverage_matrix": {
            "complete": True,
            "discovered_site_count": len(run.get("discovered_sites") or []),
            "decided_site_count": len(site_results),
            "decisions": site_results,
            "options": reviewed,
        },
        "decisions": site_results,
        "executable_decisions": executable,
        "planned_actions": len(executable),
        "deferred_actions": sum(
            item.get("schedule_class") == "deferred" for item in reviewed
        ),
    }


async def _validate_corrective_action_lineage(
    session: AsyncSession,
    *,
    run: dict[str, Any],
    proposed_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Authorize a narrow repair of one confirmed partially applied Action."""
    validated: list[dict[str, Any]] = []
    for proposed in proposed_actions:
        item = dict(proposed)
        parent_id = str(item.get("corrective_of_action_id") or "").strip()
        if not parent_id:
            validated.append(item)
            continue
        try:
            parent_id = str(UUID(parent_id))
        except ValueError as error:
            raise StrategyContractError(
                "CORRECTIVE_ACTION_INVALID",
                "corrective_of_action_id must be a Strategy Action UUID",
            ) from error
        parent = (
            await session.execute(
                text(
                    """
                    SELECT status, payload
                      FROM seo_agent.tasks
                     WHERE id=CAST(:action_id AS uuid)
                       AND task_type='review'
                       AND payload->>'kind'='strategy_action'
                    """
                ),
                {"action_id": parent_id},
            )
        ).mappings().first()
        if not parent:
            raise StrategyContractError(
                "CORRECTIVE_ACTION_INVALID",
                "the failed parent Strategy Action does not exist",
            )
        payload = dict(parent.get("payload") or {})
        parent_result = str(payload.get("result") or "")
        recovery_status = str(payload.get("recovery_status") or "")
        same_identity = (
            str(payload.get("business_id") or "") == str(run.get("business_id") or "")
            and str(payload.get("site_id") or "") == str(item.get("site_id") or "")
            and str(payload.get("action_type") or "") == "update_article"
            and str(payload.get("scope_key") or "") == str(item.get("scope_key") or "")
            and str(payload.get("target_url") or "").rstrip("/")
            == str(item.get("target_url") or "").rstrip("/")
        )
        repairable = (
            str(parent.get("status") or "") in {"failed", "blocked"}
            and parent_result in {"readback_mismatch", "blocked"}
            and recovery_status in {"partially_applied", "readback_mismatch"}
        )
        if not same_identity or not repairable:
            raise StrategyContractError(
                "CORRECTIVE_ACTION_INVALID",
                "corrective Action must match one failed partially-applied update target",
            )
        active_child = (
            await session.execute(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1
                        FROM seo_agent.tasks
                       WHERE task_type='review'
                         AND (
                           (
                             payload->>'kind'='strategy_action'
                             AND payload->>'corrective_of_action_id'=:action_id
                           )
                           OR (
                             payload->>'kind'='seo_strategy'
                             AND decision->>'corrective_of_action_id'=:action_id
                           )
                          )
                          AND COALESCE(
                                payload->>'strategy_run_id',
                                payload->>'run_id',
                                ''
                              ) <> :run_id
                          AND status IN ('queued','running','blocked')
                         AND COALESCE(payload->>'recovery_status','')
                               NOT IN ('confirmed_not_applied','confirmed_absent')
                    )
                    """
                ),
                {
                    "action_id": parent_id,
                    "run_id": str(run.get("run_id") or ""),
                },
            )
        ).scalar_one()
        if active_child:
            raise StrategyContractError(
                "CORRECTIVE_ACTION_ACTIVE",
                "an unresolved corrective Action already exists for this parent",
            )
        item.update(
            {
                "corrective_of_action_id": parent_id,
                "corrective_action_validated": True,
                "corrective_reason": "repair_confirmed_partial_article_write",
            }
        )
        validated.append(item)
    return validated


async def review_zero_action_run(
    session: AsyncSession,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Return and, when possible, persist the current zero-action review."""
    from app.services.strategy_run_service import get_strategy_run

    run = await get_strategy_run(session, run_id=run_id)
    if not run:
        raise StrategyContractError(
            "STRATEGY_RUN_NOT_FOUND", "strategy run not found"
        )
    if run.get("zero_action_review"):
        return {
            "run_id": run_id,
            "status": run["status"],
            "zero_action_review": run["zero_action_review"],
            "idempotency_replayed": True,
        }
    if not run.get("reviewed_actions"):
        raise StrategyContractError(
            "ALL_HOLD_REVIEW_REQUIRED",
            "safety review and formal planning must finish before zero-action review",
        )
    result = await evaluate_zero_action_review(
        session,
        run=run,
        reviewed_actions=list(run.get("reviewed_actions") or []),
    )
    updated = await _patch_run(
        session,
        run_id=run_id,
        patch={"zero_action_review": result},
    )
    return {
        "run_id": run_id,
        "status": updated["status"],
        "zero_action_review": result,
        "idempotency_replayed": False,
    }


__all__ = [
    "ACTION_TYPES",
    "CONTRACT_VERSION",
    "StrategyContractError",
    "build_reviewed_plan",
    "build_site_results",
    "capture_research_portfolio",
    "normalize_evidence_source",
    "normalize_proposed_actions",
    "normalize_research_portfolio",
    "review_proposed_actions",
    "review_zero_action",
    "review_zero_action_run",
    "submit_proposed_actions",
]
