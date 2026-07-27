"""业务范围内的关键词分配、站点证据分析和策略审核。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.classifier import keyword_scope
from app.services.strategy_execution import (
    _execution_heartbeat,
    _finish_execution_task,
    _validate_current_strategy,
    cancel_strategy,
    execute_strategy,
)
from app.services.strategy_effect_service import (
    load_scope_locks,
    resolve_strategy_target_url,
    strategy_identity,
)
from app.services.strategy_evidence_refresh_service import (
    refresh_default_strategy_evidence,
)
from app.services.strategy_decision_service import (
    build_on_page_candidates,
    load_on_page_assets,
)
from app.services.strategy_hold_service import (
    EXPANDED_EVIDENCE_SOURCES,
    EvidenceCollector,
    claim_hold_evidence_refresh,
    complete_hold_evidence_refresh,
    evolve_hold_state,
    finalize_hold_run,
    refresh_hold_evidence,
    wait_for_hold_evidence_refresh,
)


async def _load_latest_source_audit(
    session: AsyncSession,
    *,
    business_id: str,
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, payload->>'scanned_at' AS scanned_at FROM seo_agent.tasks "
                "WHERE task_type = 'review' AND payload->>'kind' = 'content_audit_batch' "
                "AND payload->>'business_id' = :business_id "
                "ORDER BY (payload->>'scanned_at')::timestamptz DESC NULLS LAST, created_at DESC LIMIT 1"
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _load_latest_source_audit_after_refresh(
    session: AsyncSession,
    *,
    business_id: str,
    evidence_refreshes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    # Always read this boundary again after collectors finish. A collector may
    # have created a newer content-audit batch even when another source degraded.
    return await _load_latest_source_audit(session, business_id=business_id)


async def generate_strategies(
    session: AsyncSession,
    *,
    business_id: str,
    site_id: str | None = None,
    limit: int = 4,
    action_budget: int | None = None,
    site_quotas: dict[str, int] | None = None,
    min_impressions: int = 20,
    evidence_refreshers: dict[str, EvidenceCollector] | None = None,
) -> dict[str, Any]:
    budget = max(0, min(action_budget if action_budget is not None else limit, 200))
    scope = await session.execute(
        text(
            "SELECT id, name, site_type, strategy_enabled, market, language_code, "
            "domain, base_url, api_base_url FROM seo_agent.sites "
            "WHERE business_id = :business_id AND status = 'active' "
            "AND (CAST(:site_id AS uuid) IS NULL OR id = CAST(:site_id AS uuid)) ORDER BY name"
        ),
        {"business_id": business_id, "site_id": site_id},
    )
    site_scope = [dict(row) for row in scope.mappings().all()]
    scope_by_id = {str(site["id"]): site for site in site_scope}
    if not site_scope:
        raise ValueError(f"业务 {business_id} 没有符合条件的已启用策略站点")
    source_audit = await _load_latest_source_audit(session, business_id=business_id)
    if not source_audit:
        raise ValueError("请先运行当前业务的全站内容扫描")
    strategy_run_id = str(uuid4())
    refresh_claim = await claim_hold_evidence_refresh(
        session,
        business_id=business_id,
        run_id=strategy_run_id,
    )
    evidence_refreshes: list[dict[str, Any]] = []
    if refresh_claim["should_refresh"]:
        try:
            if evidence_refreshers is None:
                evidence_refreshes = await refresh_default_strategy_evidence(
                    session,
                    business_id=business_id,
                    sites=site_scope,
                )
            else:
                refresh_now = datetime.now(ZoneInfo("UTC"))
                for scoped_site in site_scope:
                    evidence_refreshes.append(
                        await refresh_hold_evidence(
                            site_id=str(scoped_site["id"]),
                            collectors=evidence_refreshers,
                            now=refresh_now,
                        )
                    )
        except Exception as error:  # catastrophic collector/orchestrator failure
            evidence_refreshes = _failed_hold_refresh_records(
                site_scope,
                error=error,
                now=datetime.now(ZoneInfo("UTC")),
            )
        refresh_token = str(refresh_claim["refresh_token"])
        completed = await complete_hold_evidence_refresh(
            session,
            business_id=business_id,
            refresh_token=refresh_token,
            refresh_results=evidence_refreshes,
        )
        if not completed:
            raise RuntimeError("Hold evidence refresh token was already consumed or replaced.")
    elif refresh_claim.get("wait_for_refresh"):
        evidence_refreshes = await wait_for_hold_evidence_refresh(
            session,
            business_id=business_id,
            refresh_token=str(refresh_claim["pending_refresh_token"]),
        )
    if evidence_refreshes:
        source_audit = await _load_latest_source_audit_after_refresh(
            session,
            business_id=business_id,
            evidence_refreshes=evidence_refreshes,
        )
        if not source_audit:
            raise ValueError("Evidence refresh completed without a current content audit batch.")
    quarantined = await _quarantine_invalid_assignments(session, business_id)

    rows = await session.execute(
        text(
            """
            SELECT s.id AS site_id, s.name AS site_name, s.site_type, s.business_id, s.market, s.language_code,
                   s.content_role, s.content_scope,
                   COALESCE(s.knowledge_profile, '{}'::jsonb) AS knowledge_profile,
                   k.id AS keyword_id, k.topic_cluster_id,
                   COALESCE(k.keyword, NULLIF(p.primary_keyword, ''), NULLIF(audit.payload#>>'{item,query}', ''), p.title) AS query,
                   k.volume, k.kd, k.intent,
                   k.priority AS keyword_priority, k.score AS keyword_score,
                   q.clicks, q.impressions, q.ctr, q.avg_position, q.last_seen,
                   serp.id AS serp_snapshot_id,
                   jsonb_array_length(COALESCE(serp.organic_results, '[]'::jsonb)) AS serp_result_count,
                   a.id AS article_id, a.title AS article_title,
                   a.status AS article_status, a.published_url AS article_published_url,
                   a.published_post_id AS article_published_post_id,
                   p.id AS post_id, p.title AS post_title, p.url AS post_url,
                   CASE WHEN p.id IS NOT NULL THEN 'exact' END AS post_match_type,
                   audit.id AS audit_task_id, audit.score AS audit_score, audit.decision AS audit_decision,
                   audit.payload->>'scanned_at' AS audit_scanned_at,
                   pa.id AS post_analysis_id, pa.analyzed_at AS post_analysis_analyzed_at
              FROM seo_agent.tasks audit
              JOIN seo_agent.sites s ON s.id = audit.site_id
              LEFT JOIN seo_agent.posts p
                ON p.id = audit.post_id AND p.site_id = audit.site_id
               AND COALESCE(p.status, '') <> 'remote_missing'
              LEFT JOIN LATERAL (
                SELECT candidate.*
                  FROM seo_agent.keywords candidate
                 WHERE candidate.business_id = :business_id
                   AND candidate.assigned_site_id = audit.site_id
                   AND (candidate.id = audit.keyword_id
                        OR (audit.keyword_id IS NULL
                            AND p.primary_keyword IS NOT NULL
                            AND lower(candidate.keyword) = lower(p.primary_keyword)))
                 ORDER BY (candidate.id = audit.keyword_id) DESC, candidate.updated_at DESC
                 LIMIT 1
              ) k ON TRUE
              LEFT JOIN seo_agent.v_gsc_query_28d q
                ON q.site_id = audit.site_id
               AND lower(q.query) = lower(COALESCE(k.keyword, NULLIF(p.primary_keyword, ''), NULLIF(audit.payload#>>'{item,query}', ''), p.title))
              LEFT JOIN LATERAL (
                SELECT id, title, status, published_url, published_post_id
                  FROM seo_agent.articles
                 WHERE site_id = audit.site_id
                   AND ((k.id IS NOT NULL AND lower(primary_keyword) = lower(k.keyword))
                        OR (p.external_id IS NOT NULL AND published_post_id = p.external_id)
                        OR (p.url IS NOT NULL AND published_url = p.url))
                 ORDER BY created_at DESC
                 LIMIT 1
              ) a ON TRUE
              LEFT JOIN LATERAL (
                SELECT id, analyzed_at
                  FROM seo_agent.post_analyses
                 WHERE post_id = p.id
                 ORDER BY analyzed_at DESC
                 LIMIT 1
              ) pa ON TRUE
              LEFT JOIN LATERAL (
                SELECT id, organic_results
                  FROM seo_agent.serp_snapshots
                 WHERE id = audit.serp_snapshot_id OR (k.id IS NOT NULL AND keyword_id = k.id)
                 ORDER BY (id = audit.serp_snapshot_id) DESC, requested_at DESC
                 LIMIT 1
              ) serp ON TRUE
             WHERE audit.task_type = 'review'
               AND (audit.post_id IS NULL OR p.id IS NOT NULL)
               AND audit.payload->>'kind' = 'content_audit'
               AND audit.payload->>'business_id' = :business_id
               AND (audit.payload->>'scanned_at')::timestamptz = CAST(:source_audit_scanned_at AS timestamptz)
               AND audit.decision->>'action' IN ('new_article', 'update_article', 'hold')
               AND s.status = 'active'
               AND s.business_id = :business_id
               AND s.strategy_enabled = true
               AND (CAST(:site_id AS uuid) IS NULL OR s.id = CAST(:site_id AS uuid))
             ORDER BY CASE audit.decision->>'priority'
                        WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 ELSE 4
                      END,
                      audit.score DESC NULLS LAST,
                      COALESCE(q.impressions, 0) DESC,
                      k.score DESC NULLS LAST,
                      audit.created_at DESC
            """
        ),
        {
            "site_id": site_id,
            "business_id": business_id,
            "source_audit_scanned_at": source_audit["scanned_at"],
        },
    )
    candidate_rows = [dict(row) for row in rows.mappings().all()]
    ga4_rows = await session.execute(
        text(
            "SELECT g.site_id, g.sessions, g.conversions FROM seo_agent.v_ga4_site_28d g "
            "JOIN seo_agent.sites s ON s.id = g.site_id "
            "WHERE s.business_id = :business_id AND s.strategy_enabled = true AND s.status = 'active'"
        ),
        {"business_id": business_id},
    )
    ga4_by_site = {str(row["site_id"]): dict(row) for row in ga4_rows.mappings().all()}
    site_content = await _load_site_content(session)
    on_page_assets = await load_on_page_assets(session, business_id=business_id)
    scope_locks = await load_scope_locks(session, business_id=business_id)

    analysis = await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks (task_type, status, priority, title, payload, decision)
            VALUES ('review', 'done', 'P2', :title, CAST(:payload AS jsonb), '{}'::jsonb)
            RETURNING id
            """
        ),
        {
            "title": f"业务策略分析：{business_id}",
            "payload": json.dumps(
                {
                    "kind": "strategy_analysis_batch",
                    "business_id": business_id,
                    "site_scope": site_scope,
                    "source_audit_batch_id": str(source_audit["id"]),
                    "source_audit_scanned_at": source_audit["scanned_at"],
                    "strategy_run_id": strategy_run_id,
                    "hold_refresh_claim": refresh_claim,
                    "min_impressions": max(1, min_impressions),
                    "evidence_refreshes": evidence_refreshes,
                },
                ensure_ascii=False,
                default=str,
            ),
        },
    )
    analysis_batch_id = str(analysis.scalar_one())
    candidates: list[dict[str, Any]] = []
    seen_candidate_keys: set[str] = set()
    for row in candidate_rows:
        site_meta = scope_by_id.get(str(row["site_id"]), {})
        links = _build_internal_link_plan(row["query"], site_content.get(str(row["site_id"]), []))
        strategy = _build_strategy(row, ga4_by_site.get(str(row["site_id"])), links)
        if not strategy:
            continue
        strategy["site_configuration_ready"] = bool(
            site_meta.get("strategy_enabled")
            and (site_meta.get("base_url") or site_meta.get("domain"))
            and site_meta.get("market")
            and site_meta.get("language_code")
        )
        identity = strategy_identity(
            business_id,
            site_id=row.get("site_id"),
            market=row.get("market"),
            language_code=row.get("language_code"),
            topic_cluster_id=row.get("topic_cluster_id"),
            post_id=row.get("post_id"),
            article_id=row.get("article_id"),
            query=row.get("query"),
            action=strategy.get("strategy_type"),
            objective=strategy.get("recommended_action"),
            evidence=strategy.get("evidence"),
            target_url=row.get("article_published_url") or row.get("post_url"),
            site_url=site_meta.get("base_url"),
            site_domain=site_meta.get("domain"),
        )
        candidate_key, evidence_key = identity["strategy_fingerprint"], identity["evidence_fingerprint"]
        if candidate_key in seen_candidate_keys:
            continue
        seen_candidate_keys.add(candidate_key)
        strategy.update({"candidate_key": candidate_key, "evidence_key": evidence_key, **identity})
        if identity["scope_key"] in scope_locks:
            strategy.update({
                "strategy_type": "hold",
                "priority": "Hold",
                "score": 0,
                "confidence": min(float(strategy.get("confidence") or 0.35), 0.35),
                "evidence_level": "insufficient",
                "reason": f"{scope_locks[identity['scope_key']]}；{strategy.get('reason') or ''}",
            })
        required_data = _required_strategy_data(strategy["strategy_type"])
        inserted = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, keyword_id, post_id, article_id,
                   title, payload, required_data, decision)
                VALUES
                   ('review', 'done', :priority, :score, :site_id, :keyword_id, :post_id, :article_id,
                    :title, CAST(:payload AS jsonb), :required_data, CAST(:decision AS jsonb))
                RETURNING id
                """
            ),
            {
                "priority": strategy["priority"],
                "score": strategy["score"],
                "site_id": row["site_id"],
                "keyword_id": row["keyword_id"],
                "post_id": row.get("post_id"),
                "article_id": row.get("article_id"),
                "title": strategy["title"],
                "payload": json.dumps({
                    "kind": "strategy_candidate",
                    "business_id": business_id,
                    "analysis_batch_id": analysis_batch_id,
                    "topic_cluster_id": row.get("topic_cluster_id"),
                    "candidate_key": candidate_key,
                    "evidence_key": evidence_key,
                    **identity,
                    "evidence": strategy["evidence"],
                }, ensure_ascii=False),
                "required_data": required_data,
                "decision": json.dumps({**strategy, "candidate_status": "hold" if strategy["strategy_type"] == "hold" else "available"}, ensure_ascii=False),
            },
        )
        candidate_id = inserted.scalar_one()
        candidates.append({
            "id": str(candidate_id),
            "site_id": str(row["site_id"]),
            "site_name": row["site_name"],
            "keyword_id": str(row["keyword_id"]) if row.get("keyword_id") else None,
            "post_id": str(row["post_id"]) if row.get("post_id") else None,
            "article_id": str(row["article_id"]) if row.get("article_id") else None,
            **strategy,
        })

    for candidate in [
        candidate
        for site_key, assets in on_page_assets.items()
        if (site := scope_by_id.get(site_key))
        and site.get("strategy_enabled")
        for candidate in build_on_page_candidates(site={**site, "business_id": business_id}, assets=assets)
    ]:
        candidate["site_configuration_ready"] = bool(
            site.get("strategy_enabled")
            and (site.get("base_url") or site.get("domain"))
            and site.get("market")
            and site.get("language_code")
        )
        if candidate["strategy_fingerprint"] in seen_candidate_keys:
            continue
        seen_candidate_keys.add(candidate["strategy_fingerprint"])
        if candidate["lock_key"] in scope_locks:
            candidate.update(
                {
                    "in_cooldown": True,
                    "cooldown_reason": scope_locks[candidate["lock_key"]],
                    "candidate_status": "hold",
                }
            )
        inserted = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, title, payload, required_data, decision)
                VALUES
                  ('review', 'done', :priority, :score, CAST(:site_id AS uuid), :title,
                   CAST(:payload AS jsonb), :required_data, CAST(:decision AS jsonb))
                RETURNING id
                """
            ),
            {
                "priority": candidate["priority"],
                "score": candidate["score"],
                "site_id": candidate["site_id"],
                "title": candidate["title"],
                "payload": json.dumps(
                    {
                        "kind": "strategy_candidate",
                        "business_id": business_id,
                        "analysis_batch_id": analysis_batch_id,
                        "candidate_key": candidate["strategy_fingerprint"],
                        "evidence_key": candidate["evidence_fingerprint"],
                        "scope_key": candidate["scope_key"],
                        "lock_scope": candidate["lock_scope"],
                        "lock_key": candidate["lock_key"],
                        "strategy_fingerprint": candidate["strategy_fingerprint"],
                        "evidence_fingerprint": candidate["evidence_fingerprint"],
                        "policy_version": candidate["policy_version"],
                        "evidence": candidate["evidence"],
                    },
                    ensure_ascii=False,
                ),
                "required_data": ["site_asset", "seo_audit"],
                "decision": json.dumps(candidate, ensure_ascii=False),
            },
        )
        candidates.append({**candidate, "id": str(inserted.scalar_one())})

    selected = _select_daily_candidates(candidates, budget, site_quotas)
    coverage_matrix = _build_site_coverage(site_scope, candidates, selected)
    if not coverage_matrix["complete"]:
        raise RuntimeError("Strategy coverage is incomplete; every discovered active site must have one decision.")
    hold_state = evolve_hold_state(
        coverage_matrix["decisions"],
        previous_site_counts=refresh_claim.get("site_consecutive_hold_counts") or {},
        previous_business_count=int(refresh_claim.get("business_consecutive_hold_count") or 0),
        now=datetime.now(ZoneInfo("UTC")),
    )
    coverage_matrix["decisions"] = hold_state["decisions"]
    plan = await _replace_strategy_plan(
        session,
        business_id=business_id,
        analysis_batch_id=analysis_batch_id,
        source_audit_batch_id=str(source_audit["id"]),
        source_audit_scanned_at=source_audit["scanned_at"],
        action_budget=budget,
        site_quotas=site_quotas or {},
        candidates=selected,
    )
    site_hold_flags = {
        str(decision.get("site_id") or ""): decision.get("action") == "hold"
        for decision in coverage_matrix["decisions"]
    }
    coordination = await finalize_hold_run(
        session,
        business_id=business_id,
        run_id=strategy_run_id,
        all_hold=bool(site_hold_flags) and all(site_hold_flags.values()),
        site_hold_flags=site_hold_flags,
        commit=False,
    )
    hold_state.update(
        {
            "site_consecutive_hold_counts": coordination[
                "site_consecutive_hold_counts"
            ],
            "business_consecutive_hold_count": coordination[
                "business_consecutive_hold_count"
            ],
            "anomalies": (
                [
                    {
                        "kind": "strategy_stagnation",
                        "severity": "P2",
                        "consecutive_hold_count": coordination[
                            "business_consecutive_hold_count"
                        ],
                    }
                ]
                if coordination["emit_strategy_stagnation"]
                else []
            ),
            "refresh": coordination.get("refresh"),
        }
    )
    for decision in coverage_matrix["decisions"]:
        if decision.get("action") == "hold":
            decision["consecutive_hold_count"] = coordination[
                "site_consecutive_hold_counts"
            ].get(str(decision.get("site_id") or ""), 0)
    await session.execute(
        text("UPDATE seo_agent.tasks SET decision = CAST(:decision AS jsonb), updated_at = now() WHERE id = CAST(:id AS uuid)"),
        {
            "id": analysis_batch_id,
            "decision": json.dumps(
                {
                    "total_candidates": len(candidates),
                    "planned_actions": len(selected),
                    "coverage_matrix": coverage_matrix,
                    "hold_state": hold_state,
                    "evidence_refreshes": evidence_refreshes,
                },
                ensure_ascii=False,
            ),
        },
    )
    await session.commit()
    return {
        "business_id": business_id,
        "site_scope": site_scope,
        "analysis_batch_id": analysis_batch_id,
        "plan": plan,
        "items": plan["items"],
        "created": len(plan["items"]),
        "candidates": len(candidates),
        "total_candidates": len(candidates),
        "planned_actions": len(plan["items"]),
        "unassigned": 0,
        "quarantined": quarantined,
        "coverage_matrix": coverage_matrix,
        "decisions": coverage_matrix["decisions"],
        "hold_state": hold_state,
        "evidence_refreshes": evidence_refreshes,
    }


def _failed_hold_refresh_records(
    sites: list[dict[str, Any]], *, error: Exception, now: datetime
) -> list[dict[str, Any]]:
    refreshed_at = now.isoformat()
    message = str(error)[:1000]
    return [
        {
            "site_id": str(site.get("id") or ""),
            "refreshed_at": refreshed_at,
            "degraded": True,
            "sources": {
                source: {
                    "status": "failed",
                    "refreshed_at": refreshed_at,
                    "snapshot_id": None,
                    "error": message,
                    "decision_impact": (
                        "candidate confidence may be reduced; refresh failure "
                        "cannot bypass quality or safety gates"
                    ),
                }
                for source in EXPANDED_EVIDENCE_SOURCES
            },
        }
        for site in sites
    ]


def _build_site_coverage(
    site_scope: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates_by_site: dict[str, list[dict[str, Any]]] = {}
    selected_by_site: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        candidates_by_site.setdefault(str(candidate.get("site_id")), []).append(candidate)
    for candidate in selected:
        selected_by_site[str(candidate.get("site_id"))] = candidate

    decisions: list[dict[str, Any]] = []
    discovered_ids: set[str] = set()
    for site in site_scope:
        site_id = str(site["id"])
        discovered_ids.add(site_id)
        site_candidates = candidates_by_site.get(site_id, [])
        selected_candidate = selected_by_site.get(site_id)
        missing_configuration = [
            field
            for field, value in (
                ("base_url", site.get("base_url") or site.get("domain")),
                ("market", site.get("market")),
                ("language_code", site.get("language_code")),
            )
            if not value
        ]
        if not site.get("strategy_enabled"):
            decisions.append(
                {
                    "site_id": site_id,
                    "site_name": site.get("name"),
                    "action": "configuration_repair",
                    "reason": "Site strategy is disabled.",
                    "block_reason": "Site strategy is disabled.",
                    "unlock_condition": "Enable the site strategy after its configuration is reviewed.",
                    "responsibility_type": "site_configuration_owner",
                    "review_by": datetime.now(ZoneInfo("UTC")).date().isoformat(),
                    "alternative_evidence": [
                        "approved site configuration",
                        "verified read-only connector check",
                    ],
                    "consecutive_hold_count": 0,
                    "missing_configuration": ["strategy_enabled"],
                    "reevaluation_trigger": "next_strategy_run",
                    "reevaluation_status": "configuration_incomplete",
                    "last_checked_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                    "candidate_count": len(site_candidates),
                    "selected_candidate_id": None,
                }
            )
        elif missing_configuration:
            decisions.append(
                {
                    "site_id": site_id,
                    "site_name": site.get("name"),
                    "action": "configuration_repair",
                    "reason": "Required site strategy configuration is incomplete.",
                    "block_reason": "Required site strategy configuration is incomplete.",
                    "unlock_condition": f"Provide: {', '.join(missing_configuration)}.",
                    "responsibility_type": "site_configuration_owner",
                    "review_by": datetime.now(ZoneInfo("UTC")).date().isoformat(),
                    "alternative_evidence": [
                        "verified domain ownership",
                        "approved market/language configuration",
                    ],
                    "consecutive_hold_count": 0,
                    "missing_configuration": missing_configuration,
                    "reevaluation_trigger": "next_strategy_run",
                    "reevaluation_status": "configuration_incomplete",
                    "last_checked_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                    "candidate_count": len(site_candidates),
                    "selected_candidate_id": None,
                }
            )
        elif selected_candidate:
            decisions.append(
                {
                    "site_id": site_id,
                    "site_name": site.get("name"),
                    "action": selected_candidate.get("strategy_type"),
                    "reason": selected_candidate.get("reason") or "Selected by the daily strategy plan.",
                    "candidate_count": len(site_candidates),
                    "selected_candidate_id": str(selected_candidate.get("id")),
                }
            )
        elif site_candidates:
            blocked = [
                candidate
                for candidate in site_candidates
                if candidate.get("strategy_type") == "hold"
                or candidate.get("priority") == "Hold"
                or candidate.get("in_cooldown")
                or candidate.get("risk_gate_passed", True) is False
            ]
            fully_blocked = len(blocked) == len(site_candidates)
            first_blocked = blocked[0] if blocked else {}
            decisions.append(
                {
                    "site_id": site_id,
                    "site_name": site.get("name"),
                    "action": "hold",
                    "reason": (
                        first_blocked.get("block_reason")
                        or first_blocked.get("reason")
                        or "Every candidate is blocked by an evidence, safety, or cooldown gate."
                        if fully_blocked
                        else "Candidate exists but is not in the current action budget."
                    ),
                    "unlock_condition": (
                        first_blocked.get("unlock_condition")
                        or "Satisfy the candidate evidence and safety requirements, then re-evaluate."
                        if fully_blocked
                        else "Increase or free the site action budget, then re-evaluate the candidate."
                    ),
                    "candidate_count": len(site_candidates),
                    "selected_candidate_id": None,
                }
            )
        else:
            decisions.append(
                {
                    "site_id": site_id,
                    "site_name": site.get("name"),
                    "action": "hold",
                    "reason": "No executable candidate was found for this site.",
                    "unlock_condition": "Refresh site evidence and candidate discovery, then re-evaluate the site.",
                    "candidate_count": 0,
                    "selected_candidate_id": None,
                }
            )

    decided_ids = {decision["site_id"] for decision in decisions}
    return {
        "complete": decided_ids == discovered_ids and len(decisions) == len(discovered_ids),
        "discovered_sites": len(discovered_ids),
        "decided_sites": len(decided_ids),
        "decisions": decisions,
    }


def _candidate_keys(business_id: str, row: dict[str, Any], strategy: dict[str, Any]) -> tuple[str, str]:
    identity = strategy_identity(
        business_id,
        site_id=row.get("site_id"),
        market=row.get("market"),
        language_code=row.get("language_code"),
        topic_cluster_id=row.get("topic_cluster_id") or row.get("keyword_id"),
        post_id=row.get("post_id"),
        article_id=row.get("article_id"),
        query=row.get("query"),
        action=strategy.get("strategy_type"),
        objective=strategy.get("recommended_action"),
        evidence={key: row.get(key) for key in ("audit_task_id", "serp_snapshot_id", "post_analysis_id")},
        target_url=row.get("article_published_url") or row.get("post_url") or strategy.get("target_url"),
        site_url=row.get("base_url") or row.get("site_url"),
        site_domain=row.get("domain") or row.get("site_domain"),
    )
    return identity["strategy_fingerprint"], identity["evidence_fingerprint"]


def _select_daily_candidates(
    rows: list[dict[str, Any]],
    limit: int,
    site_quotas: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """按预算与站点配额组装今日计划；先覆盖站点再补位。"""
    wanted = max(0, min(limit, 200))
    if not wanted:
        return []
    quotas = {str(key): max(0, int(value)) for key, value in (site_quotas or {}).items()}
    selected: list[dict[str, Any]] = []
    seen_sites: set[str] = set()
    site_counts: dict[str, int] = {}
    priority_weight = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}

    def rank(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
        return (
            float(priority_weight.get(str(row.get("priority") or "P2"), 0)),
            float(row.get("opportunity_score", row.get("score", 0)) or 0),
            float(row.get("readiness_score", row.get("confidence", 0.5)) or 0),
            -float(row.get("risk_score", 0) or 0),
            float(row.get("days_since_last_action", 0) or 0),
        )

    ranked = sorted(rows, key=rank, reverse=True)

    def allowed(row: dict[str, Any]) -> bool:
        site_id = str(row.get("site_id") or "")
        return (
            row.get("strategy_type") != "hold"
            and row.get("priority") != "Hold"
            and row.get("candidate_status") != "executed"
            and not row.get("executed")
            and not row.get("in_cooldown")
            and row.get("risk_gate_passed", True) is not False
            and row.get("site_configuration_ready", True) is not False
            and site_counts.get(site_id, 0) < quotas.get(site_id, wanted)
        )

    def add(row: dict[str, Any]) -> None:
        selected.append(row)
        site_id = str(row.get("site_id") or "")
        site_counts[site_id] = site_counts.get(site_id, 0) + 1

    for row in ranked:
        site_id = str(row.get("site_id") or "")
        if site_id not in seen_sites and allowed(row):
            add(row)
            seen_sites.add(site_id)
        if len(selected) >= wanted:
            return selected
    for row in ranked:
        if row not in selected and allowed(row):
            add(row)
        if len(selected) >= wanted:
            break
    return selected[:wanted]


def _required_strategy_data(strategy_type: str) -> list[str]:
    if strategy_type == "new_article":
        return ["site_content", "site_knowledge", "content_audit", "serp"]
    if strategy_type == "on_page_fix":
        return ["site_asset", "seo_audit"]
    return ["gsc_28d", "ga4_28d", "site_content", "site_knowledge", "content_audit"]


async def _replace_strategy_plan(
    session: AsyncSession,
    *,
    business_id: str,
    analysis_batch_id: str,
    source_audit_batch_id: str,
    source_audit_scanned_at: Any,
    action_budget: int,
    site_quotas: dict[str, int],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:business_id))"), {"business_id": business_id})
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE task_type = 'review' AND status = 'queued' AND payload->>'business_id' = :business_id "
            "AND payload->>'kind' IN ('strategy_plan', 'seo_strategy')"
        ),
        {"business_id": business_id},
    )
    selected_ids = [str(candidate["id"]) for candidate in candidates]
    plan_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    inserted = await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks (task_type, status, priority, title, payload, decision)
            VALUES ('review', 'queued', 'P2', :title, CAST(:payload AS jsonb), CAST(:decision AS jsonb))
            RETURNING id, created_at
            """
        ),
        {
            "title": f"今日策略计划：{business_id}",
            "payload": json.dumps({
                "kind": "strategy_plan",
                "business_id": business_id,
                "analysis_batch_id": analysis_batch_id,
                "source_audit_batch_id": source_audit_batch_id,
                "source_audit_scanned_at": source_audit_scanned_at,
            }, ensure_ascii=False),
            "decision": json.dumps({
                "action_budget": action_budget,
                "site_quotas": site_quotas,
                "selected_candidate_ids": selected_ids,
                "plan_date": plan_date,
            }, ensure_ascii=False),
        },
    )
    plan_row = inserted.mappings().one()
    plan_id = str(plan_row["id"])
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        strategy = {key: value for key, value in candidate.items() if key not in {"id", "site_id", "site_name", "keyword_id", "post_id", "article_id", "status", "created_at", "updated_at", "candidate_status", "executed"}}
        strategy["business_id"] = business_id
        task = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, keyword_id, post_id, article_id,
                   title, payload, required_data, decision)
                VALUES
                  ('review', 'queued', :priority, :score, CAST(:site_id AS uuid), CAST(:keyword_id AS uuid),
                   CAST(:post_id AS uuid), CAST(:article_id AS uuid), :title, CAST(:payload AS jsonb),
                   :required_data, CAST(:decision AS jsonb))
                RETURNING id
                """
            ),
            {
                "priority": strategy.get("priority") or "P2",
                "score": strategy.get("score") or 0,
                "site_id": candidate["site_id"],
                "keyword_id": candidate.get("keyword_id"),
                "post_id": candidate.get("post_id"),
                "article_id": candidate.get("article_id"),
                "title": strategy.get("title"),
                "payload": json.dumps({
                    "kind": "seo_strategy",
                    "business_id": business_id,
                    "analysis_batch_id": analysis_batch_id,
                    "candidate_id": str(candidate["id"]),
                    "plan_id": plan_id,
                    "scope_key": strategy.get("scope_key"),
                    "strategy_fingerprint": strategy.get("strategy_fingerprint"),
                    "evidence_fingerprint": strategy.get("evidence_fingerprint"),
                    "policy_version": strategy.get("policy_version"),
                    "evidence": strategy.get("evidence") or {},
                }, ensure_ascii=False),
                "required_data": _required_strategy_data(str(strategy.get("strategy_type") or "")),
                "decision": json.dumps(strategy, ensure_ascii=False),
            },
        )
        task_id = str(task.scalar_one())
        items.append({
            **candidate,
            **strategy,
            "id": task_id,
            "candidate_id": str(candidate["id"]),
            "plan_id": plan_id,
            "status": "pending",
        })
    return {
        "id": plan_id,
        "business_id": business_id,
        "analysis_batch_id": analysis_batch_id,
        "source_audit_batch_id": source_audit_batch_id,
        "source_audit_scanned_at": source_audit_scanned_at,
        "action_budget": action_budget,
        "site_quotas": site_quotas,
        "selected_candidate_ids": selected_ids,
        "plan_date": plan_date,
        "planned_actions": len(items),
        "status": "active",
        "created_at": plan_row["created_at"],
        "items": items,
    }


async def list_strategy_candidates(
    session: AsyncSession,
    *,
    business_id: str,
    page: int = 1,
    limit: int = 50,
    status: str | None = None,
) -> dict[str, Any]:
    page = max(1, page)
    limit = max(1, min(limit, 200))
    status_filter = status if status in {"available", "selected", "hold", "executed"} else None
    rows = await session.execute(
        text(
            """
            WITH latest AS (
                SELECT id::text AS id FROM seo_agent.tasks
                 WHERE task_type = 'review' AND payload->>'kind' = 'strategy_analysis_batch'
                   AND payload->>'business_id' = :business_id
                   AND status = 'done'
                 ORDER BY created_at DESC LIMIT 1
            ), candidates AS (
                SELECT t.id, t.status, t.priority, t.score, t.site_id, s.name AS site_name,
                       t.keyword_id, t.post_id, t.article_id, t.title, t.decision,
                       t.payload->>'analysis_batch_id' AS analysis_batch_id,
                       t.created_at, t.updated_at,
                       CASE
                         WHEN EXISTS (
                           SELECT 1 FROM seo_agent.tasks strategy
                            WHERE strategy.task_type = 'review'
                              AND strategy.payload->>'kind' = 'seo_strategy'
                              AND strategy.payload->>'candidate_id' = t.id::text
                              AND (strategy.status = 'done' OR strategy.decision ? 'execution_task_id')
                         ) THEN 'executed'
                         WHEN t.decision->>'strategy_type' = 'hold' OR t.priority = 'Hold' THEN 'hold'
                         WHEN EXISTS (
                           SELECT 1 FROM seo_agent.tasks strategy
                            WHERE strategy.task_type = 'review' AND strategy.status = 'queued'
                              AND strategy.payload->>'kind' = 'seo_strategy'
                              AND strategy.payload->>'candidate_id' = t.id::text
                         ) THEN 'selected'
                         ELSE 'available'
                       END AS candidate_status
                  FROM seo_agent.tasks t
                  JOIN seo_agent.sites s ON s.id = t.site_id
                 WHERE t.task_type = 'review' AND t.payload->>'kind' = 'strategy_candidate'
                   AND t.payload->>'business_id' = :business_id
                   AND t.payload->>'analysis_batch_id' = (SELECT id FROM latest)
            )
            SELECT *, count(*) OVER() AS total FROM candidates
             WHERE CAST(:candidate_status AS text) IS NULL OR candidate_status = CAST(:candidate_status AS text)
             ORDER BY score DESC NULLS LAST, created_at DESC
             LIMIT :limit OFFSET :offset
            """
        ),
        {
            "business_id": business_id,
            "candidate_status": status_filter,
            "limit": limit,
            "offset": (page - 1) * limit,
        },
    )
    mapped = [dict(row) for row in rows.mappings().all()]
    total = int(mapped[0].pop("total")) if mapped else 0
    items = []
    for row in mapped:
        candidate_status = row["candidate_status"]
        item = _strategy_row(row)
        item["candidate_status"] = candidate_status
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "analysis_batch_id": items[0].get("analysis_batch_id") if items else None,
    }


async def get_strategy_plan(session: AsyncSession, *, business_id: str) -> dict[str, Any] | None:
    plan = (
        await session.execute(
            text(
                """
                SELECT id, status, payload, decision, created_at, updated_at
                  FROM seo_agent.tasks
                 WHERE task_type = 'review' AND status = 'queued'
                   AND payload->>'kind' = 'strategy_plan' AND payload->>'business_id' = :business_id
                   AND decision->>'plan_date' = (now() AT TIME ZONE 'Asia/Shanghai')::date::text
                 ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    if not plan:
        return None
    payload = dict(plan["payload"] or {})
    decision = dict(plan["decision"] or {})
    strategies = await list_strategies(session, status="pending", limit=200, business_id=business_id)
    plan_id = str(plan["id"])
    items = [item for item in strategies if item.get("plan_id") == plan_id]
    return {
        "id": plan_id,
        "business_id": business_id,
        "analysis_batch_id": payload.get("analysis_batch_id"),
        "source_audit_batch_id": payload.get("source_audit_batch_id"),
        "source_audit_scanned_at": payload.get("source_audit_scanned_at"),
        "action_budget": int(decision.get("action_budget") or 0),
        "site_quotas": decision.get("site_quotas") or {},
        "selected_candidate_ids": decision.get("selected_candidate_ids") or [],
        "plan_date": decision.get("plan_date"),
        "planned_actions": len(items),
        "status": "active",
        "created_at": plan["created_at"],
        "updated_at": plan["updated_at"],
        "items": items,
    }


async def save_strategy_plan(
    session: AsyncSession,
    *,
    business_id: str,
    action_budget: int,
    site_quotas: dict[str, int] | None = None,
    selected_candidate_ids: list[str] | None = None,
) -> dict[str, Any]:
    budget = max(0, min(action_budget, 200))
    quotas = {str(key): max(0, int(value)) for key, value in (site_quotas or {}).items()}
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:business_id))"), {"business_id": business_id})
    analysis_batch = (
        await session.execute(
            text(
                "SELECT id::text AS id, payload->>'source_audit_batch_id' AS source_audit_batch_id, "
                "payload->>'source_audit_scanned_at' AS source_audit_scanned_at "
                "FROM seo_agent.tasks WHERE task_type = 'review' "
                "AND status = 'done' AND payload->>'kind' = 'strategy_analysis_batch' "
                "AND payload->>'business_id' = :business_id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"business_id": business_id},
        )
    ).mappings().first()
    if not analysis_batch:
        raise ValueError("请先生成策略候选池")
    analysis_batch_id = str(analysis_batch["id"])
    rows = await session.execute(
        text(
            """
            SELECT t.id, t.priority, t.score, t.site_id, s.name AS site_name, t.keyword_id,
                   t.post_id, t.article_id, t.title, t.decision, t.created_at, t.updated_at,
                   EXISTS (
                     SELECT 1 FROM seo_agent.tasks strategy
                      WHERE strategy.task_type = 'review'
                        AND strategy.payload->>'kind' = 'seo_strategy'
                        AND strategy.payload->>'candidate_id' = t.id::text
                        AND (strategy.status = 'done' OR strategy.decision ? 'execution_task_id')
                   ) AS executed
              FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id
             WHERE t.task_type = 'review' AND t.payload->>'kind' = 'strategy_candidate'
               AND t.status = 'done'
               AND t.payload->>'business_id' = :business_id
               AND t.payload->>'analysis_batch_id' = :analysis_batch_id
             ORDER BY t.score DESC NULLS LAST, t.created_at DESC
            """
        ),
        {"business_id": business_id, "analysis_batch_id": analysis_batch_id},
    )
    candidates = [_strategy_row(dict(row)) for row in rows.mappings().all()]
    if selected_candidate_ids is None:
        selected = _select_daily_candidates(candidates, budget, quotas)
    else:
        selected_candidate_ids = list(dict.fromkeys(selected_candidate_ids))
        by_id = {str(candidate["id"]): candidate for candidate in candidates}
        missing = [candidate_id for candidate_id in selected_candidate_ids if candidate_id not in by_id]
        if missing:
            raise ValueError("所选策略候选不属于当前业务分析批次")
        selected = [by_id[candidate_id] for candidate_id in selected_candidate_ids]
        if len(selected) > budget:
            raise ValueError("所选策略数量超过今日动作预算")
        counts: dict[str, int] = {}
        for candidate in selected:
            if candidate.get("executed"):
                raise ValueError("已批准或执行的策略候选不能再次加入计划")
            if candidate.get("strategy_type") == "hold" or candidate.get("priority") == "Hold":
                raise ValueError("Hold 候选不能进入今日计划")
            candidate_site = str(candidate["site_id"])
            counts[candidate_site] = counts.get(candidate_site, 0) + 1
            if candidate_site in quotas and counts[candidate_site] > quotas[candidate_site]:
                raise ValueError("所选策略数量超过站点配额")
    plan = await _replace_strategy_plan(
        session,
        business_id=business_id,
        analysis_batch_id=analysis_batch_id,
        source_audit_batch_id=str(analysis_batch["source_audit_batch_id"]),
        source_audit_scanned_at=str(analysis_batch["source_audit_scanned_at"]),
        action_budget=budget,
        site_quotas=quotas,
        candidates=selected,
    )
    await session.commit()
    return plan


async def _quarantine_invalid_assignments(session: AsyncSession, business_id: str) -> int:
    rows = await session.execute(
        text(
            "SELECT k.id, k.keyword, k.market, k.language_code, k.ai_review, "
            "       s.market AS site_market, s.language_code AS site_language "
            "FROM seo_agent.keywords k "
            "LEFT JOIN seo_agent.sites s ON s.id = k.assigned_site_id "
            "WHERE k.assigned_site_id IS NOT NULL AND k.status NOT IN ('dropped', 'published') "
            "AND s.business_id = :business_id AND s.strategy_enabled = true AND s.status = 'active'"
        ),
        {"business_id": business_id},
    )
    invalid = [
        str(row["id"])
        for row in rows.mappings().all()
        if (
            keyword_scope(row["keyword"])["status"] != "relevant"
            and ((row["ai_review"] or {}).get("strategy") or {}).get("relevance") != "relevant"
            or not row["market"]
            or not row["language_code"]
            or not row["site_market"]
            or not row["site_language"]
            or _locale_token(row["market"]) != _locale_token(row["site_market"])
            or _locale_token(row["language_code"]) != _locale_token(row["site_language"])
        )
    ]
    if not invalid:
        return 0
    await session.execute(
        text(
            """
            UPDATE seo_agent.keywords
               SET assigned_site_id = NULL, assigned_site_label = NULL,
                   status = 'hold', priority = 'Hold', content_action = 'needs_scope_review',
                   reason = '已通过复核撤回：关键词缺少业务/市场语种约束，等待重新导入或人工确认。', updated_at = now()
             WHERE id::text = ANY(:ids)
            """
        ),
        {"ids": invalid},
    )
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE keyword_id::text = ANY(:ids) AND status = 'queued' "
            "AND ((task_type = 'review' AND payload->>'kind' IN ('seo_strategy', 'content_audit')) "
            "OR task_type IN ('new_article', 'update_article'))"
        ),
        {"ids": invalid},
    )
    return len(invalid)


def _locale_token(value: Any) -> str:
    return str(value or "").strip().lower().split("/", 1)[0].strip()


async def list_strategies(
    session: AsyncSession,
    *,
    status: str | None = "pending",
    limit: int = 50,
    site_id: str | None = None,
    search: str | None = None,
    strategy_type: str | None = None,
    priority: str | None = None,
    evidence_level: str | None = None,
    business_id: str | None = None,
) -> list[dict[str, Any]]:
    status_sql = {"pending": "queued", "approved": "done", "rejected": "canceled"}.get(status or "")
    where = "WHERE t.task_type = 'review' AND t.payload->>'kind' = 'seo_strategy'"
    params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
    if status_sql:
        where += " AND t.status = :status"
        params["status"] = status_sql
    if business_id:
        where += " AND t.payload->>'business_id' = :business_id AND s.business_id = :business_id"
        params["business_id"] = business_id
    if site_id:
        where += " AND t.site_id = CAST(:site_id AS uuid)"
        params["site_id"] = site_id
    if search:
        where += " AND (t.title ILIKE :search OR t.decision->>'query' ILIKE :search)"
        params["search"] = f"%{search.strip()}%"
    if strategy_type:
        where += " AND t.decision->>'strategy_type' = :strategy_type"
        params["strategy_type"] = strategy_type
    if priority:
        where += " AND t.priority = :priority"
        params["priority"] = priority
    if evidence_level:
        where += " AND t.decision->>'evidence_level' = :evidence_level"
        params["evidence_level"] = evidence_level
    rows = await session.execute(
        text(
            f"""
            SELECT t.id, t.status, t.priority, t.score, t.site_id, s.name AS site_name,
                   t.keyword_id, t.post_id, t.article_id, t.title, t.decision, t.created_at, t.updated_at,
                   t.payload->>'candidate_id' AS candidate_id, t.payload->>'plan_id' AS plan_id,
                   e.status AS execution_status, e.decision->>'current_stage' AS execution_stage,
                   e.logs AS execution_logs,
                   e.error_message AS execution_error,
                   e.started_at AS execution_started_at, e.finished_at AS execution_finished_at,
                   e.run_after AS execution_run_after,
                   (e.payload->>'auto_publish')::boolean AS auto_publish
              FROM seo_agent.tasks t
              LEFT JOIN seo_agent.sites s ON s.id = t.site_id
              LEFT JOIN seo_agent.tasks e
                ON e.id = CAST(t.decision->>'execution_task_id' AS uuid)
               AND e.task_type IN ('new_article', 'update_article')
              {where}
             ORDER BY t.score DESC NULLS LAST, t.created_at DESC
             LIMIT :limit
            """
        ),
        params,
    )
    return [_strategy_row(dict(row)) for row in rows.mappings().all()]


async def review_strategy(session: AsyncSession, *, task_id: str, approved: bool) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT t.id, t.site_id, t.keyword_id, t.post_id, t.article_id, t.title, t.decision, "
                "       s.site_type, s.status AS site_status, s.business_id, s.strategy_enabled, "
                "       t.payload->>'business_id' AS task_business_id, "
                "       t.payload->>'candidate_id' AS candidate_id, t.payload->>'plan_id' AS plan_id, "
                "       t.payload->>'analysis_batch_id' AS analysis_batch_id "
                "FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id "
                "WHERE t.id = CAST(:id AS uuid) AND t.task_type = 'review' AND t.status = 'queued' "
                "AND t.payload->>'kind' = 'seo_strategy' "
                "FOR UPDATE OF t"
            ),
            {"id": task_id},
        )
    ).mappings().first()
    if not row:
        raise ValueError("strategy task not found")
    decision = dict(row["decision"] or {})
    if approved and decision.get("strategy_type") == "on_page_fix":
        if (
            row["site_status"] != "active"
            or not row["strategy_enabled"]
            or not row["business_id"]
            or row["task_business_id"] != row["business_id"]
        ):
            raise ValueError("目标站点已退出当前业务策略范围，请重新扫描并生成策略")
        decision.update(
            {
                "approved": True,
                "execution_task_id": None,
                "execution_status": "blocked_pending_preview",
                "requires_publish": True,
                "blocker": "A safe on-page preview and explicit confirmation are required before execution.",
                "unlock_condition": f"Call /workflow/strategies/{task_id}/on-page/preview, review the snapshot hash, then confirm through the on-page execute API.",
            }
        )
        await session.execute(
            text(
                "UPDATE seo_agent.tasks SET status = 'done', decision = CAST(:decision AS jsonb), "
                "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {
                "id": task_id,
                "decision": json.dumps(decision, ensure_ascii=False),
            },
        )
        await session.commit()
        return _strategy_row({**dict(row), "status": "done", "decision": decision})
    if approved:
        await _validate_current_strategy(session, row=dict(row), decision=decision)
        if decision.get("strategy_type") == "hold" or decision.get("priority") == "Hold":
            raise ValueError("Hold 策略必须先补齐诊断证据，不能批准执行")
        execution_type = "new_article" if decision.get("strategy_type") == "new_article" else "update_article"
        target_url = resolve_strategy_target_url(decision)
        execution_strategy = {**decision, **({"target_url": target_url} if target_url else {})}
        execution = await session.execute(
            text(
                """
                INSERT INTO seo_agent.tasks
                  (task_type, status, priority, score, site_id, keyword_id, post_id, article_id,
                   title, target_url, payload, required_data, decision, logs)
                VALUES
                  (:task_type, 'queued', :priority, :score, :site_id, :keyword_id, :post_id, :article_id,
                   :title, :target_url, CAST(:payload AS jsonb), :required_data, CAST(:decision AS jsonb),
                   jsonb_build_array(jsonb_build_object('stage', 'queued', 'message', '人工审核通过，已进入待执行队列', 'at', now())))
                RETURNING id
                """
            ),
            {
                "task_type": execution_type,
                "priority": decision.get("priority") or "P2",
                "score": decision.get("score") or 0,
                "site_id": row["site_id"],
                "keyword_id": row["keyword_id"],
                "post_id": row["post_id"],
                "article_id": row["article_id"],
                "title": f"执行策略：{row['title']}",
                "target_url": target_url,
                "payload": json.dumps({
                    "strategy_task_id": str(task_id),
                    "strategy": execution_strategy,
                    "scope_key": decision.get("scope_key"),
                    "strategy_fingerprint": decision.get("strategy_fingerprint"),
                    "evidence_fingerprint": decision.get("evidence_fingerprint"),
                    "policy_version": decision.get("policy_version"),
                    "auto_publish": execution_type == "new_article",
                }, ensure_ascii=False),
                "required_data": ["approved_strategy"],
                "decision": json.dumps({"source_strategy_id": str(task_id), "strategy_type": execution_type}, ensure_ascii=False),
            },
        )
        execution_id = execution.scalar_one()
        if row["keyword_id"]:
            await session.execute(
                text(
                    "UPDATE seo_agent.keywords SET assigned_site_id = CAST(:site_id AS uuid), "
                    "status = CASE WHEN :type = 'new_article' THEN 'queued' ELSE status END, updated_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND status NOT IN ('written', 'published')"
                ),
                {"site_id": row["site_id"], "type": execution_type, "id": row["keyword_id"]},
            )
        decision.update({"review_status": "approved", "execution_task_id": str(execution_id)})
        status = "done"
    else:
        decision["review_status"] = "rejected"
        status = "canceled"
        execution_id = None
    await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = :status, decision = CAST(:decision AS jsonb), "
            "finished_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"
        ),
        {"status": status, "decision": json.dumps(decision, ensure_ascii=False), "id": task_id},
    )
    await session.commit()
    return {"ok": True, "status": decision["review_status"], "execution_task_id": execution_id}


async def clear_strategy_queue(session: AsyncSession, *, business_id: str) -> dict[str, int]:
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:business_id))"), {"business_id": business_id})
    running = int(
        (await session.execute(
            text(
                "SELECT count(*) FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id "
                "WHERE s.business_id = :business_id AND t.task_type IN ('new_article', 'update_article') AND t.status = 'running'"
            ),
            {"business_id": business_id},
        )).scalar_one()
        or 0
    )
    if running:
        raise ValueError("仍有任务正在执行，不能清空队列")

    executions = (
        await session.execute(
            text(
                "SELECT t.id::text, t.keyword_id::text, t.payload->>'strategy_task_id' AS review_id "
                "FROM seo_agent.tasks t JOIN seo_agent.sites s ON s.id = t.site_id "
                "WHERE s.business_id = :business_id AND t.task_type IN ('new_article', 'update_article') "
                "AND t.status = 'queued' AND t.article_id IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM seo_agent.articles a WHERE a.task_id = t.id)"
            ),
            {"business_id": business_id},
        )
    ).mappings().all()
    pending_reviews = (
        await session.execute(
            text(
                "SELECT t.id::text, t.keyword_id::text FROM seo_agent.tasks t "
                "WHERE t.task_type = 'review' AND t.status = 'queued' AND t.payload->>'kind' = 'seo_strategy' "
                "AND t.payload->>'business_id' = :business_id "
                "AND NOT EXISTS (SELECT 1 FROM seo_agent.tasks e WHERE e.payload->>'strategy_task_id' = t.id::text OR e.decision->>'source_strategy_id' = t.id::text) "
                "AND NOT EXISTS (SELECT 1 FROM seo_agent.articles a WHERE a.task_id = t.id)"
            ),
            {"business_id": business_id},
        )
    ).mappings().all()
    execution_ids = [row["id"] for row in executions]
    review_ids = [row["review_id"] for row in executions if row["review_id"]] + [row["id"] for row in pending_reviews]
    keyword_ids = [row["keyword_id"] for row in executions if row["keyword_id"]] + [row["keyword_id"] for row in pending_reviews if row["keyword_id"]]

    executions_canceled = 0
    if execution_ids:
        canceled = await session.execute(
            text("UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() WHERE id::text = ANY(:ids) RETURNING id"),
            {"ids": execution_ids},
        )
        executions_canceled = len(canceled.all())
    strategies_canceled = 0
    if review_ids:
        reviews = await session.execute(
            text("UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() WHERE id::text = ANY(:ids) RETURNING id"),
            {"ids": review_ids},
        )
        strategies_canceled = len(reviews.all())
    plans = await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE task_type = 'review' AND status = 'queued' AND payload->>'kind' = 'strategy_plan' "
            "AND payload->>'business_id' = :business_id RETURNING id"
        ),
        {"business_id": business_id},
    )
    plans_cleared = len(plans.all())
    workspace = await session.execute(
        text(
            "UPDATE seo_agent.tasks SET status = 'canceled', finished_at = now(), updated_at = now() "
            "WHERE task_type = 'review' AND status = 'done' "
            "AND payload->>'kind' IN ('strategy_candidate', 'strategy_analysis_batch') "
            "AND payload->>'business_id' = :business_id RETURNING payload->>'kind' AS kind"
        ),
        {"business_id": business_id},
    )
    cleared_kinds = [row[0] for row in workspace.all()]
    if keyword_ids:
        await session.execute(
            text("UPDATE seo_agent.keywords SET status = 'analyzed', updated_at = now() WHERE id::text = ANY(:ids) AND status = 'queued'"),
            {"ids": keyword_ids},
        )
    await session.commit()
    return {
        "executions_canceled": executions_canceled,
        "strategies_canceled": strategies_canceled,
        "plans_cleared": plans_cleared,
        "candidates_cleared": cleared_kinds.count("strategy_candidate"),
        "analysis_batches_cleared": cleared_kinds.count("strategy_analysis_batch"),
    }


async def _load_site_content(session: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    rows = await session.execute(
        text(
            """
            SELECT site_id, url, title, primary_keyword, topic_cluster
              FROM seo_agent.posts
             WHERE url IS NOT NULL AND url <> ''
            UNION ALL
            SELECT site_id, target_url AS url, title, primary_keyword, NULL AS topic_cluster
              FROM seo_agent.articles
             WHERE target_url IS NOT NULL AND target_url <> ''
            """
        )
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows.mappings().all():
        grouped.setdefault(str(row["site_id"]), []).append(dict(row))
    return grouped


def _build_internal_link_plan(query: str, content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_terms = _terms(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in content:
        terms = _terms(" ".join(str(item.get(key) or "") for key in ("title", "primary_keyword", "topic_cluster")))
        overlap = len(query_terms & terms)
        if overlap:
            scored.append((overlap, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "target_url": item["url"],
            "title": item.get("title"),
            "anchor": item.get("primary_keyword") or item.get("title"),
            "reason": "同站点相关主题文章",
        }
        for _, item in scored[:3]
    ]


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]+", value.lower()) if len(term) > 2}


def _build_strategy(row: dict[str, Any], ga4: dict[str, Any] | None, internal_link_plan: list[dict[str, Any]]) -> dict[str, Any] | None:
    impressions = int(row.get("impressions") or 0)
    clicks = int(row.get("clicks") or 0)
    ctr = float(row.get("ctr") or 0)
    position = float(row.get("avg_position") or 0)
    sessions = int((ga4 or {}).get("sessions") or 0)
    conversions = float((ga4 or {}).get("conversions") or 0)
    audit = row.get("audit_decision") or {}
    audit_action = str(audit.get("action") or "")
    profile = row.get("knowledge_profile") or {}
    policy = profile.get("generation_policy") or {}
    risk_level = str(policy.get("risk_level") or "standard").casefold()
    high_risk = risk_level in {"regulated", "ymyl"}
    risk_failures: list[str] = []
    if high_risk:
        if not row.get("market"):
            risk_failures.append("market")
        if not row.get("language_code"):
            risk_failures.append("language")
        if not audit.get("product_facts"):
            risk_failures.append("product_facts")
        if not policy.get("allowed_sources") or not audit.get("authority_sources"):
            risk_failures.append("authoritative_sources")
    explicit_new_evidence = any(
        key in audit
        for key in (
            "product_or_category_match",
            "content_gap",
            "serp_intent_confirmed",
            "cannibalization_risk",
            "product_facts",
        )
    )
    if audit_action == "new_article" and explicit_new_evidence:
        if not audit.get("product_or_category_match"):
            risk_failures.append("business_match")
        if not audit.get("content_gap"):
            risk_failures.append("content_gap")
        if not audit.get("serp_intent_confirmed"):
            risk_failures.append("serp_intent")
        if str(audit.get("cannibalization_risk") or "").casefold() not in {"none", "low", "clear"}:
            risk_failures.append("cannibalization")
        if not audit.get("product_facts"):
            risk_failures.append("product_facts")
    risk_failures = list(dict.fromkeys(risk_failures))
    risk_gate_passed = not risk_failures
    knowledge_confirmed = "knowledge_profile" not in row or profile.get("status") == "confirmed"
    existing_target_without_analysis = bool((row.get("article_id") or row.get("post_id")) and not row.get("post_analysis_id"))
    existing_content = bool(row.get("article_id") or row.get("post_id"))
    evidence_level = "confirmed" if impressions >= 100 and clicks >= 5 else "directional"
    confidence = 0.82 if evidence_level == "confirmed" else 0.58
    if not knowledge_confirmed or audit_action == "hold" or existing_target_without_analysis or not risk_gate_passed:
        strategy_type = "hold"
    elif row.get("article_id"):
        strategy_type = "update_article"
    else:
        strategy_type = audit_action if audit_action in {"new_article", "update_article"} else ("update_article" if existing_content else "new_article")
    priority = "P1" if impressions >= 500 and position <= 20 else "P2"
    if not row.get("last_seen"):
        priority = row.get("keyword_priority") or "Hold"
        confidence = 0.58 if priority != "Hold" else 0.35
        evidence_level = "directional" if priority != "Hold" else "insufficient"
    elif impressions < 50:
        priority = "Hold"
        confidence = 0.35
        evidence_level = "insufficient"
    if strategy_type == "hold":
        priority = "Hold"
        confidence = min(confidence, 0.35)
        evidence_level = "insufficient"
    elif audit.get("priority"):
        priority = audit["priority"]
    if strategy_type != "hold" and audit.get("confidence") is not None:
        confidence = float(audit["confidence"])
    if strategy_type != "hold" and audit.get("evidence_level"):
        evidence_level = audit["evidence_level"]
    if priority == "Hold":
        strategy_type = "hold"
        confidence = min(confidence, 0.35)
        evidence_level = "insufficient"
    score = float(row.get("audit_score") or row.get("keyword_score") or 0) if not row.get("last_seen") else round(min(99, impressions / 10 + max(0, 21 - position) * 2 + (0 if ctr >= 0.03 else 10)), 2)
    if strategy_type == "hold":
        score = 0
    if strategy_type == "hold":
        title = f"暂缓策略：{row['query']}"
        action = "先确认站点知识画像，再决定更新或新建" if not knowledge_confirmed else "先同步已发布文章并完成正文诊断，再决定更新或新建"
    elif strategy_type == "update_article":
        title = f"优化已有内容：{row.get('article_title') or row.get('post_title') or row['query']}"
        action = "优化标题与 Meta，补充搜索意图段落、FAQ 和相关内链"
    else:
        title = f"创建新文章：{row['query']}"
        action = "创建新 Brief，生成文章大纲，加入相关内链并进入生文队列"
    audit_matches_strategy = audit_action == strategy_type
    if strategy_type != "hold" and audit_matches_strategy:
        action = audit.get("recommended_action") or action
    gsc_note = f"GSC 28 天展示 {impressions}、点击 {clicks}、CTR {ctr:.2%}、平均排名 {position:.1f}" if row.get("last_seen") else "该站点暂无 GSC 查询记录"
    serp_note = f"SERP 已缓存 {int(row.get('serp_result_count') or 0)} 条结果" if row.get("serp_snapshot_id") else "SERP 待执行阶段获取"
    positioning = str(profile.get("positioning") or "").strip()
    scope = str(row.get("content_scope") or "").strip() or "、".join(profile.get("in_scope_topics") or [])
    knowledge_note = f"站点定位：{positioning}" if knowledge_confirmed and positioning else "站点知识画像未确认"
    if scope:
        knowledge_note += f"；内容范围：{scope}"
    audit_note = "站点知识画像未确认" if not knowledge_confirmed else (
        "检测到站点文章已存在，但未进入文章诊断快照"
        if existing_target_without_analysis
        else str(audit.get("reason") or "").strip() if audit_matches_strategy
        else "站点库存事实与候选诊断冲突，已按库存事实纠正动作"
    )
    reason = f"{audit_note.rstrip('；。') + '；' if audit_note else ''}{gsc_note}；GA4 会话 {sessions}、转化 {conversions:g}；{serp_note}；{knowledge_note}。"
    execution_evidence = {
        "search_intent": {
            "status": "confirmed" if audit.get("serp_intent_confirmed") else "missing",
            "source": "live_serp" if row.get("serp_snapshot_id") else None,
            "snapshot_id": str(row["serp_snapshot_id"]) if row.get("serp_snapshot_id") else None,
        },
        "topic_basis": {
            "product_or_category_match": bool(audit.get("product_or_category_match")),
            "content_gap": bool(audit.get("content_gap")),
        },
        "cannibalization": {
            "status": str(audit.get("cannibalization_risk") or "unknown").casefold(),
        },
        "product_facts": list(audit.get("product_facts") or []),
        "authority_sources": list(audit.get("authority_sources") or []),
        "evidence_sources": [
            source
            for source, present in (
                ("products_or_collections", audit.get("product_or_category_match")),
                ("live_serp", row.get("serp_snapshot_id")),
                ("content_audit", row.get("audit_task_id")),
            )
            if present
        ],
        "snapshots": {
            "serp": str(row["serp_snapshot_id"]) if row.get("serp_snapshot_id") else None,
            "content_audit": str(row["audit_task_id"]) if row.get("audit_task_id") else None,
            "post_analysis": str(row["post_analysis_id"]) if row.get("post_analysis_id") else None,
        },
    }
    return {
        "business_id": row.get("business_id"),
        "topic_cluster_id": row.get("topic_cluster_id"),
        "strategy_type": strategy_type,
        "title": title,
        "query": row["query"],
        "priority": priority,
        "score": score,
        "opportunity_score": score,
        "readiness_score": confidence if risk_gate_passed else 0.0,
        "risk_score": 0.8 if high_risk else 0.2,
        "risk_level": risk_level,
        "risk_gate_passed": risk_gate_passed,
        "risk_gate_failures": risk_failures,
        "block_reason": (
            f"Strategy safety/evidence gate failed: {', '.join(risk_failures)}"
            if risk_failures
            else None
        ),
        "unlock_condition": (
            "Provide every missing business fact, locale, SERP-intent, cannibalization and authoritative-source requirement."
            if risk_failures
            else None
        ),
        "days_since_last_action": int(row.get("days_since_last_action") or 0),
        "execution_evidence": execution_evidence,
        "confidence": confidence,
        "evidence_level": evidence_level,
        "reason": reason,
        "recommended_action": action,
        "site_context": {
            "content_role": row.get("content_role"),
            "content_scope": row.get("content_scope"),
            "knowledge_profile": profile,
        },
        "internal_link_plan": internal_link_plan,
        "evidence": {
            "gsc": {"impressions": impressions, "clicks": clicks, "ctr": ctr, "avg_position": position},
            "ga4": {"sessions": sessions, "conversions": conversions},
            "serp": {"snapshot_id": str(row["serp_snapshot_id"]) if row.get("serp_snapshot_id") else None, "result_count": int(row.get("serp_result_count") or 0)},
            "site_content": {
                "article_id": str(row["article_id"]) if row.get("article_id") else None,
                "article_status": row.get("article_status"),
                "published_url": row.get("article_published_url") or row.get("post_url"),
                "published_post_id": row.get("article_published_post_id"),
                "post_id": str(row["post_id"]) if row.get("post_id") else None,
                "post_match_type": row.get("post_match_type"),
            },
            "content_audit": {
                "task_id": str(row["audit_task_id"]) if row.get("audit_task_id") else None,
                "scanned_at": row.get("audit_scanned_at"),
                "post_analysis": {
                    "id": str(row["post_analysis_id"]) if row.get("post_analysis_id") else None,
                    "analyzed_at": str(row["post_analysis_analyzed_at"]) if row.get("post_analysis_analyzed_at") else None,
                },
                "competitor_gap": (audit.get("ai") or {}).get("competitor_gap") if audit_matches_strategy else None,
            },
        },
    }


def _strategy_row(row: dict[str, Any]) -> dict[str, Any]:
    decision = row.pop("decision") or {}
    return {**row, **decision, "status": {"queued": "pending", "done": "approved", "canceled": "rejected"}.get(row.get("status"), row.get("status"))}
