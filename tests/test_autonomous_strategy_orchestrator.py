from __future__ import annotations

from copy import deepcopy

import pytest

from app.services import autonomous_strategy_orchestrator as orchestrator
from app.services import strategy_effect_service
from app.services.autonomous_strategy_orchestrator import (
    ACTION_TYPES,
    FixedEvidenceAdapter,
    StrategyContractError,
    build_reviewed_plan,
    build_site_results,
    collect_evidence,
    normalize_proposed_actions,
    normalize_research_portfolio,
    review_proposed_actions,
    review_zero_action,
)


class _NoRows:
    pass


def _site(
    site_id: str = "11111111-1111-1111-1111-111111111111",
    *,
    enabled: bool = True,
    language: str = "en",
    market: str = "US",
):
    return {
        "id": site_id,
        "name": f"Site {site_id[-4:]}",
        "site_type": "main",
        "strategy_enabled": enabled,
        "language_code": language,
        "market": market,
        "domain": f"{site_id[-4:]}.example.com",
        "base_url": f"https://{site_id[-4:]}.example.com",
    }


def _source(source_type: str = "public_search"):
    return {
        "source_type": source_type,
        "source_name": "Current evidence",
        "captured_at": "2026-07-31T10:00:00+08:00",
        "data_window": {},
        "market": "US",
        "language": "en",
        "device": "desktop",
        "dimensions": [],
        "filters": {},
        "freshness": "current",
        "fact_scope": "intent",
        "artifact_refs": ["logs/evidence.json"],
        "collection_status": "success",
        "limitations": [],
        "decision_use": "Validate current user intent.",
    }


def _research(site: dict, *, sources=None):
    return {
        "site_id": site["id"],
        "site_language": site["language_code"],
        "site_market": site["market"],
        "evidence_snapshot_id": "snapshot-1",
        "research_questions": ["Which unmet intent fits this site now?"],
        "actions_considered": sorted(ACTION_TYPES),
        "material_options": [
            {
                "option_id": "new-distinct-use-case",
                "action": "new_article",
                "target_identity": {
                    "intent_key": "distinct product use case",
                    "topic_cluster": "product use cases",
                },
                "user_intent": "Understand a distinct product use case.",
                "evidence_refs": ["public-search-1", "product-api-1"],
                "outcome": "qualified",
                "reason": "Current intent and product evidence support a distinct article.",
            },
            {
                "option_id": "existing-product-page",
                "action": "on_page_fix",
                "target_identity": {
                    "target_url": f"{site['base_url']}/products/example",
                    "remote_object_id": "product-1",
                },
                "user_intent": "Evaluate an existing product page.",
                "evidence_refs": ["product-api-1"],
                "outcome": "rejected",
                "reason": "The current page has no material SEO defect.",
            },
        ],
        "opportunity_exhaustion": {
            "surfaces_checked": ["existing_pages", "new_topics"],
            "evaluated_option_ids": [
                "new-distinct-use-case",
                "existing-product-page",
            ],
            "conclusion": "Existing pages and distinct new topics were both evaluated.",
        },
        "action_assessments": {
            action: {
                "outcome": "considered",
                "reason": f"Assessed {action} against current site evidence.",
                "evidence_refs": ["public-search-1", "product-api-1"],
            }
            for action in ACTION_TYPES
        },
        "hard_blockers": [],
        "sources_attempted": ["public search", "site API"],
        "evidence_sources": sources or [_source(), _source("site_api")],
        "missing_evidence": [],
        "evidence_conflicts": [],
        "research_conclusion": "A distinct content direction is supportable.",
    }


def _proposal(
    site: dict,
    *,
    intent: str,
    schedule_request: str = "execute_now",
):
    return {
        "site_id": site["id"],
        "action": "new_article",
        "target_identity": {
            "intent_key": intent,
            "topic_cluster": intent,
        },
        "schedule_request": schedule_request,
        "topic": intent,
        "title": intent.title(),
        "user_intent": intent,
        "decision_reason": "Current product and intent evidence support it.",
        "evidence_refs": ["public-search-1", "product-api-1"],
        "alternatives_considered": [],
        "hypothesis": "The page can earn qualified discovery traffic.",
        "success_metrics": ["GSC impressions"],
        "priority": "P2",
        "risk_level": "low",
    }


@pytest.mark.asyncio
async def test_research_idempotency_replays_after_run_has_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import strategy_run_service

    site = _site()
    portfolio = [_research(site)]
    normalized = normalize_research_portfolio(
        portfolio,
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    request_hash = orchestrator._stable_hash(
        {
            "requested_by": "codex",
            "evidence_snapshot_id": "snapshot-1",
            "portfolio": normalized,
        }
    )
    run = {
        "run_id": "run-1",
        "status": "completed",
        "evidence_snapshot_id": "snapshot-1",
        "discovered_sites": [site],
        "research_receipt": {
            "idempotency_key": "research-key-1",
            "request_hash": request_hash,
        },
    }

    async def get_run(*_args, **_kwargs):
        return run

    class Session:
        async def execute(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(strategy_run_service, "get_strategy_run", get_run)

    result = await orchestrator.capture_research_portfolio(
        Session(),  # type: ignore[arg-type]
        run_id="run-1",
        requested_by="codex",
        idempotency_key="research-key-1",
        evidence_snapshot_id="snapshot-1",
        portfolio=portfolio,
    )

    assert result["status"] == "completed"
    assert result["idempotency_replayed"] is True


@pytest.mark.asyncio
async def test_proposed_action_idempotency_replays_after_run_has_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import strategy_run_service

    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = [_proposal(site, intent="titanium cup care")]
    normalized = normalize_proposed_actions(
        actions,
        business_id="avinoti",
        discovered_sites=[site],
        research_portfolio=research,
    )
    request_hash = orchestrator._stable_hash(
        {"requested_by": "codex", "actions": normalized}
    )
    run = {
        "run_id": "run-1",
        "status": "completed",
        "business_id": "avinoti",
        "discovered_sites": [site],
        "research_portfolio": research,
        "proposed_action_receipt": {
            "idempotency_key": "proposal-key-1",
            "request_hash": request_hash,
        },
    }

    async def get_run(*_args, **_kwargs):
        return run

    class Session:
        async def execute(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(strategy_run_service, "get_strategy_run", get_run)

    result = await orchestrator.submit_proposed_actions(
        Session(),  # type: ignore[arg-type]
        run_id="run-1",
        requested_by="codex",
        idempotency_key="proposal-key-1",
        actions=actions,
    )

    assert result["status"] == "completed"
    assert result["idempotency_replayed"] is True


@pytest.mark.asyncio
async def test_formal_plan_reconciles_stale_actions_before_loading_scope_locks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Session:
        async def execute(self, _statement, _params=None):
            events.append("plan_lock")
            return _NoRows()

    async def reconcile_stale(_store, **_kwargs):
        events.append("reconcile_stale")
        return {"checked": 1, "canceled": 1, "released": 1}

    async def load_locks(_session, **_kwargs):
        events.append("load_scope_locks")
        return {}

    async def persist(_session, *, run, reviewed_actions, site_results):
        return {
            "id": "plan-1",
            "items": [],
            "source_audit_batch_id": None,
        }

    monkeypatch.setattr(
        "app.services.strategy_action_service.reconcile_stale_actions",
        reconcile_stale,
    )
    monkeypatch.setattr(
        "app.services.strategy_effect_service.load_scope_locks",
        load_locks,
    )
    monkeypatch.setattr(
        "app.services.autonomous_strategy_orchestrator.review_proposed_actions",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.autonomous_strategy_orchestrator.build_site_results",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.autonomous_strategy_orchestrator._persist_formal_plan",
        persist,
    )

    result = await build_reviewed_plan(
        Session(),  # type: ignore[arg-type]
        run={
            "run_id": "run-1",
            "business_id": "exdivo",
            "proposed_actions": [{"option_id": "proposal-1"}],
            "discovered_sites": [],
            "capability_snapshot": {"sites": []},
            "action_budget": 0,
            "site_quotas": {},
        },
    )

    assert events.index("reconcile_stale") < events.index("load_scope_locks")
    assert result["stale_action_reconciliation"]["canceled"] == 1


def _capability(site: dict):
    return {
        "site_id": site["id"],
        "configuration_issues": [],
        "supported_fields": {
            "articles": [
                "title",
                "body",
                "meta_title",
                "meta_description",
                "images",
                "image_alts",
                "cover_image",
            ]
        },
        "supported_actions": {
            "new_article": "approval_required",
            "update_article": "approval_required",
            "homepage_seo": "approval_required",
            "product_seo": "approval_required",
            "category_seo": "approval_required",
            "product_image_alt": "approval_required",
        },
        "action_adapters": {
            "new_article": {
                "connector_type": "custom_openapi",
                "read": True,
                "write": True,
                "readback": True,
            },
            "update_article": {
                "connector_type": "custom_openapi",
                "read": True,
                "write": True,
                "readback": True,
            },
            "product_seo": {"connector_type": "shopify", "readback": True},
            "category_seo": {"connector_type": "shopify", "readback": True},
            "homepage_seo": {"connector_type": "shopify", "readback": True},
            "product_image_alt": {
                "connector_type": "shopify",
                "readback": True,
            },
        },
        "connectors": {
            "images": {
                "status": "available",
                "write": True,
                "upload": True,
                "ingest": False,
            }
        },
    }


def test_article_plan_binds_connector_type_from_capability_snapshot() -> None:
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [_proposal(site, intent="titanium cup care")],
        business_id="avinoti",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={},
        safety_action_ceiling=10,
    )

    assert reviewed[0]["schedule_class"] == "execute_now"
    assert reviewed[0]["connector_type"] == "custom_openapi"


def test_article_plan_becomes_configuration_repair_when_media_preflight_cannot_pass() -> None:
    site = _site()
    capability = _capability(site)
    capability["connectors"]["images"] = {
        "status": "unavailable",
        "write": False,
        "upload": False,
        "ingest": False,
    }
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    proposal = _proposal(site, intent="titanium cup care")
    proposal["expected_fields"] = ["images", "image_alts", "cover_image"]
    actions = normalize_proposed_actions(
        [proposal],
        business_id="avinoti",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [capability]},
        scope_locks={},
        safety_action_ceiling=10,
    )

    assert reviewed[0]["schedule_class"] == "configuration_repair"
    assert reviewed[0]["reason_code"] == "CAPABILITY_MISSING"


def test_article_plan_without_media_changes_does_not_require_media_connector() -> None:
    site = _site()
    capability = _capability(site)
    capability["connectors"]["images"] = {
        "status": "unavailable",
        "write": False,
        "upload": False,
        "ingest": False,
    }
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    proposal = _proposal(site, intent="existing article title correction")
    proposal["action"] = "update_article"
    proposal["expected_fields"] = ["title", "body"]
    proposal["target_identity"] = {
        "target_url": f"{site['base_url']}/blogs/existing-article",
        "remote_object_id": "article-1",
    }
    actions = normalize_proposed_actions(
        [proposal],
        business_id="avinoti",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [capability]},
        scope_locks={},
        safety_action_ceiling=10,
    )

    assert reviewed[0]["schedule_class"] == "execute_now"
    assert actions[0]["expected_fields"] == [
        "title",
        "body",
        "meta_title",
        "meta_description",
    ]


def test_article_plan_pairs_inline_images_with_alt_fields() -> None:
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    proposal = _proposal(site, intent="illustrated titanium cup care")
    proposal["expected_fields"] = ["images"]

    actions = normalize_proposed_actions(
        [proposal],
        business_id="avinoti",
        discovered_sites=[site],
        research_portfolio=research,
    )

    assert actions[0]["expected_fields"] == [
        "title",
        "body",
        "meta_title",
        "meta_description",
        "images",
        "image_alts",
    ]


def test_update_scope_key_matches_effect_lock_with_local_id_and_trailing_slash() -> None:
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    raw = _proposal(site, intent="existing article refresh")
    raw.update(
        {
            "action": "update_article",
            "target_identity": {
                "target_url": f"{site['base_url']}/blog/existing-article/",
                "local_object_id": "local-post-1",
                "remote_object_id": "42",
            },
        }
    )
    action = normalize_proposed_actions(
        [raw],
        business_id="avinoti",
        discovered_sites=[site],
        research_portfolio=research,
    )[0]
    effect_identity = strategy_effect_service.strategy_identity(
        "avinoti",
        site_id=site["id"],
        market=site["market"],
        language_code=site["language_code"],
        target_url=f"{site['base_url']}/blog/existing-article",
        site_url=site["base_url"],
        site_domain=site["domain"],
        action="update_article",
    )

    assert action["scope_key"] == effect_identity["scope_key"]
    assert action["lock_scope"] == effect_identity["lock_scope"] == "url"


def test_research_portfolio_requires_exact_site_coverage_including_disabled_site():
    enabled = _site()
    disabled = _site("22222222-2222-2222-2222-222222222222", enabled=False)

    with pytest.raises(
        StrategyContractError, match="research portfolio is missing sites"
    ) as error:
        normalize_research_portfolio(
            [_research(enabled)],
            discovered_sites=[enabled, disabled],
            evidence_snapshot_id="snapshot-1",
        )

    assert error.value.code == "RESEARCH_PORTFOLIO_INCOMPLETE"


def test_candidate_and_keyword_ids_cannot_enter_formal_proposed_action():
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    raw = _proposal(site, intent="titanium cup care")
    raw["candidate_id"] = "legacy-candidate"

    with pytest.raises(StrategyContractError) as error:
        normalize_proposed_actions(
            [raw],
            business_id="avinoti",
            discovered_sites=[site],
            research_portfolio=research,
        )

    assert error.value.code == "LEGACY_CANDIDATE_REFERENCE_REJECTED"


def test_keywordless_proposal_preserves_ai_topic_without_backend_substitution():
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    topic = "titanium cup care for frequent travelers"

    actions = normalize_proposed_actions(
        [_proposal(site, intent=topic)],
        business_id="avinoti",
        discovered_sites=[site],
        research_portfolio=research,
    )

    assert actions[0]["topic"] == topic
    assert actions[0]["query"] == topic
    assert "candidate_id" not in actions[0]
    assert "keyword_id" not in actions[0]


def test_exact_intent_lock_defers_only_the_matching_target():
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [
            _proposal(site, intent="titanium cup care"),
            _proposal(site, intent="titanium cup coffee taste"),
        ],
        business_id="avinoti",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={actions[0]["scope_key"]: "Exact intent is under observation."},
        safety_action_ceiling=10,
    )

    assert [item["schedule_class"] for item in reviewed] == [
        "deferred",
        "execute_now",
    ]
    assert reviewed[1]["topic"] == "titanium cup coffee taste"


def test_validated_corrective_action_bypasses_only_its_failed_target_lock():
    site = _site()
    failed_action_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    raw = _proposal(site, intent="repair the damaged article")
    raw.update(
        {
            "action": "update_article",
            "target_identity": {
                "target_url": f"{site['base_url']}/blog/damaged-article",
                "local_object_id": "post-1",
                "remote_object_id": "10",
            },
            "corrective_of_action_id": failed_action_id,
        }
    )
    actions = normalize_proposed_actions(
        [raw],
        business_id="exdivo",
        discovered_sites=[site],
        research_portfolio=research,
    )

    assert actions[0]["corrective_of_action_id"] == failed_action_id
    actions[0]["corrective_action_validated"] = True
    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={actions[0]["scope_key"]: "Target is under ordinary cooldown."},
        safety_action_ceiling=10,
    )

    assert reviewed[0]["schedule_class"] == "execute_now"
    assert reviewed[0]["corrective_of_action_id"] == failed_action_id


@pytest.mark.asyncio
async def test_corrective_lineage_requires_exact_failed_partial_parent():
    parent_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    proposed = {
        "site_id": "site-1",
        "action_type": "update_article",
        "scope_key": "scope-1",
        "target_url": "https://example.com/blog/article",
        "corrective_of_action_id": parent_id,
    }

    class Result:
        def __init__(self, row=None, scalar=None):
            self.row = row
            self.scalar = scalar

        def mappings(self):
            return self

        def first(self):
            return self.row

        def scalar_one(self):
            return self.scalar

    class Session:
        def __init__(self):
            self.calls = 0
            self.active_sql = ""
            self.active_params = {}

        async def execute(self, _statement, _params=None):
            self.calls += 1
            if self.calls == 1:
                return Result(
                    row={
                        "status": "blocked",
                        "payload": {
                            "business_id": "exdivo",
                            "site_id": "site-1",
                            "action_type": "update_article",
                            "scope_key": "scope-1",
                            "target_url": "https://example.com/blog/article",
                            "result": "readback_mismatch",
                            "recovery_status": "partially_applied",
                        },
                    }
                )
            self.active_sql = str(_statement)
            self.active_params = dict(_params or {})
            return Result(scalar=False)

    session = Session()
    result = await orchestrator._validate_corrective_action_lineage(
        session,  # type: ignore[arg-type]
        run={"run_id": "repair-run-1", "business_id": "exdivo"},
        proposed_actions=[proposed],
    )

    assert result[0]["corrective_action_validated"] is True
    assert result[0]["corrective_reason"] == (
        "repair_confirmed_partial_article_write"
    )
    assert "payload->>'strategy_run_id'" in session.active_sql
    assert session.active_params["run_id"] == "repair-run-1"


def test_safety_ceiling_preserves_all_qualified_actions_as_deferred():
    sites = [
        _site(f"{index:08d}-1111-1111-1111-{index:012d}")
        for index in range(1, 11)
    ]
    research = normalize_research_portfolio(
        [_research(site) for site in sites],
        discovered_sites=sites,
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [
            _proposal(site, intent=f"distinct intent {index}")
            for index, site in enumerate(sites, start=1)
        ],
        business_id="portfolio",
        discovered_sites=sites,
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=sites,
        capability_snapshot={"sites": [_capability(site) for site in sites]},
        scope_locks={},
        safety_action_ceiling=3,
    )

    assert len(reviewed) == 10
    assert sum(item["schedule_class"] == "execute_now" for item in reviewed) == 3
    assert sum(item["schedule_class"] == "deferred" for item in reviewed) == 7
    assert all(item["schedule_class"] != "hold" for item in reviewed)


def test_one_site_only_schedules_one_remote_write_in_the_current_wave():
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [
            _proposal(site, intent="first independent intent"),
            _proposal(site, intent="second independent intent"),
        ],
        business_id="portfolio",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={},
        safety_action_ceiling=10,
    )

    assert [item["schedule_class"] for item in reviewed] == [
        "execute_now",
        "deferred",
    ]
    assert reviewed[1]["reason_code"] == "SITE_SAFETY_CEILING_EXCEEDED"


def test_unverified_ai_defer_requires_research_revision():
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [
            _proposal(
                site,
                intent="qualified intent without an exact blocker",
                schedule_request="deferred",
            )
        ],
        business_id="portfolio",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={},
        safety_action_ceiling=10,
    )
    result = review_zero_action(
        research_portfolio=research,
        reviewed_actions=reviewed,
    )

    assert reviewed[0]["schedule_class"] == "deferred"
    assert reviewed[0]["reason_code"] == "DEFERRED_JUSTIFICATION_REQUIRED"
    assert result["result"] == "research_revision_required"
    assert result["reason_codes"] == ["DEFERRED_JUSTIFICATION_REQUIRED"]


def test_ai_defer_uses_backend_safety_reason_when_ceiling_is_reached():
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [
            _proposal(
                site,
                intent="qualified intent postponed by a zero safety ceiling",
                schedule_request="deferred",
            )
        ],
        business_id="portfolio",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={},
        safety_action_ceiling=0,
    )
    result = review_zero_action(
        research_portfolio=research,
        reviewed_actions=reviewed,
    )

    assert reviewed[0]["reason_code"] == "SAFETY_CEILING_EXCEEDED"
    assert result["result"] == "not_required"


def test_caller_cannot_raise_one_site_current_wave_above_one_write():
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [
            _proposal(site, intent="first independent intent"),
            _proposal(site, intent="second independent intent"),
        ],
        business_id="portfolio",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={},
        safety_action_ceiling=10,
        site_safety_ceilings={site["id"]: 9},
    )

    assert [item["schedule_class"] for item in reviewed] == [
        "execute_now",
        "deferred",
    ]
    assert reviewed[1]["reason_code"] == "SITE_SAFETY_CEILING_EXCEEDED"


def test_disabled_active_site_is_preserved_as_configuration_repair():
    site = _site(enabled=False)
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [_proposal(site, intent="distinct new intent")],
        business_id="business",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={},
        safety_action_ceiling=3,
    )
    results = build_site_results([site], reviewed)

    assert reviewed[0]["schedule_class"] == "configuration_repair"
    assert reviewed[0]["strategy_type"] == "configuration_repair"
    assert results[0]["action"] == "configuration_repair"


def test_zero_action_review_requires_second_channel_and_full_action_space():
    site = _site()
    research = normalize_research_portfolio(
        [
            {
                **_research(site, sources=[_source("gsc")]),
                "actions_considered": ["hold"],
            }
        ],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    held = [
        {
            "site_id": site["id"],
            "schedule_class": "hold",
            "strategy_type": "hold",
        }
    ]

    result = review_zero_action(
        research_portfolio=research, reviewed_actions=held
    )

    assert result["result"] == "research_revision_required"
    assert "ACTION_SPACE_ARTIFICIALLY_RESTRICTED" in result["reason_codes"]
    assert "RESEARCH_EVIDENCE_INSUFFICIENT" in result["reason_codes"]


def test_zero_action_review_allows_evidence_complete_hold_without_forcing_content():
    site = _site()
    exhausted = _research(site)
    for option in exhausted["material_options"]:
        option["outcome"] = "rejected"
        option["reason"] = "Current evidence does not support this exact opportunity."
    research = normalize_research_portfolio(
        [exhausted],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    held = [
        {
            "site_id": site["id"],
            "schedule_class": "hold",
            "strategy_type": "hold",
        }
    ]

    result = review_zero_action(
        research_portfolio=research, reviewed_actions=held
    )

    assert result["result"] == "all_hold_review_passed"


def test_research_portfolio_rejects_generic_action_categories_as_opportunities():
    site = _site()
    generic = _research(site)
    generic["material_options"] = [
        {
            "action": "new_article",
            "user_intent": "Consider writing something new.",
            "evidence_refs": ["public-search-1"],
        }
    ]

    with pytest.raises(StrategyContractError) as error:
        normalize_research_portfolio(
            [generic],
            discovered_sites=[site],
            evidence_snapshot_id="snapshot-1",
        )

    assert error.value.code == "CONCRETE_OPPORTUNITY_REQUIRED"


def test_research_portfolio_rejects_site_wide_cooldown_for_one_opportunity():
    site = _site()
    overbroad = _research(site)
    option = overbroad["material_options"][0]
    option.update(
        {
            "outcome": "blocked",
            "reason": "A related page is still under observation.",
            "blocker_code": "TOPIC_COOLDOWN",
            "block_scope": "site",
        }
    )

    with pytest.raises(StrategyContractError) as error:
        normalize_research_portfolio(
            [overbroad],
            discovered_sites=[site],
            evidence_snapshot_id="snapshot-1",
        )

    assert error.value.code == "COOLDOWN_SCOPE_OVERBROAD"


def test_exact_target_cooldown_does_not_hide_a_qualified_distinct_topic():
    site = _site()
    researched = _research(site)
    existing_page = researched["material_options"][1]
    existing_page.update(
        {
            "outcome": "blocked",
            "reason": "This exact product page is still under observation.",
            "blocker_code": "TARGET_COOLDOWN",
            "block_scope": "url",
        }
    )
    research = normalize_research_portfolio(
        [researched],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )

    result = review_zero_action(
        research_portfolio=research,
        reviewed_actions=[
            {
                "site_id": site["id"],
                "schedule_class": "hold",
                "strategy_type": "hold",
            }
        ],
    )

    assert result["result"] == "research_revision_required"
    assert result["missing_evidence"][0] == {
        "site_id": site["id"],
        "code": "QUALIFIED_OPPORTUNITY_NOT_SCHEDULED",
        "qualified_option_ids": ["new-distinct-use-case"],
    }


def test_zero_action_review_requires_existing_page_and_new_topic_surfaces():
    site = _site()
    incomplete = _research(site)
    for option in incomplete["material_options"]:
        option["outcome"] = "rejected"
        option["reason"] = "This exact opportunity is not supported."
    incomplete["opportunity_exhaustion"]["surfaces_checked"] = ["existing_pages"]
    research = normalize_research_portfolio(
        [incomplete],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )

    result = review_zero_action(
        research_portfolio=research,
        reviewed_actions=[
            {
                "site_id": site["id"],
                "schedule_class": "hold",
                "strategy_type": "hold",
            }
        ],
    )

    assert result["result"] == "research_revision_required"
    assert result["missing_evidence"][-1]["missing_surfaces"] == ["new_topics"]


def test_zero_action_review_audits_each_held_site_when_another_site_executes():
    executable_site = _site()
    held_site = _site("22222222-2222-2222-2222-222222222222")
    research = normalize_research_portfolio(
        [_research(executable_site), _research(held_site)],
        discovered_sites=[executable_site, held_site],
        evidence_snapshot_id="snapshot-1",
    )
    reviewed = [
        {
            "site_id": executable_site["id"],
            "schedule_class": "execute_now",
            "strategy_type": "new_article",
        },
        {
            "site_id": held_site["id"],
            "schedule_class": "hold",
            "strategy_type": "hold",
        },
    ]

    result = review_zero_action(
        research_portfolio=research,
        reviewed_actions=reviewed,
    )

    assert result["result"] == "research_revision_required"
    assert "QUALIFIED_OPPORTUNITY_NOT_SCHEDULED" in result["reason_codes"]
    assert result["missing_evidence"] == [
        {
            "site_id": held_site["id"],
            "code": "QUALIFIED_OPPORTUNITY_NOT_SCHEDULED",
            "qualified_option_ids": ["new-distinct-use-case"],
        }
    ]


def test_zero_action_review_does_not_hide_unverified_defer_behind_execute_now():
    site = _site()
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )

    result = review_zero_action(
        research_portfolio=research,
        reviewed_actions=[
            {
                "site_id": site["id"],
                "option_id": "execute-option",
                "schedule_class": "execute_now",
                "strategy_type": "new_article",
            },
            {
                "site_id": site["id"],
                "option_id": "unverified-defer-option",
                "schedule_class": "deferred",
                "strategy_type": "update_article",
                "reason_code": "DEFERRED_JUSTIFICATION_REQUIRED",
            },
        ],
    )

    assert result["result"] == "research_revision_required"
    assert result["reason_codes"] == ["DEFERRED_JUSTIFICATION_REQUIRED"]


def test_zero_action_review_skips_pure_configuration_repairs_without_blocking_run():
    site = _site(enabled=False)
    research = normalize_research_portfolio(
        [_research(site)],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    repairs = [
        {
            "site_id": site["id"],
            "schedule_class": "configuration_repair",
            "strategy_type": "configuration_repair",
            "reason_code": "CAPABILITY_MISSING",
        }
    ]

    result = review_zero_action(
        research_portfolio=research,
        reviewed_actions=repairs,
    )

    assert result["result"] == "configuration_skipped"
    assert result["reason_codes"] == ["CONFIGURATION_REPAIR_SKIPPED"]


def test_semrush_gui_requires_reproducible_artifact_provenance():
    site = _site()
    semrush = _source("semrush_ui")
    semrush["artifact_refs"] = []

    with pytest.raises(StrategyContractError) as error:
        normalize_research_portfolio(
            [_research(site, sources=[semrush])],
            discovered_sites=[site],
            evidence_snapshot_id="snapshot-1",
        )

    assert error.value.code == "EVIDENCE_PROVENANCE_INCOMPLETE"


def test_unresolved_evidence_conflict_defers_instead_of_silently_averaging():
    site = _site()
    raw_research = _research(site)
    raw_research["evidence_conflicts"] = [
        {
            "question": "Is this current demand or an old estimate?",
            "source_refs": ["gsc-1", "third-party-1"],
            "status": "unresolved",
            "decision_impact": "Do not publish until time windows are aligned.",
        }
    ]
    research = normalize_research_portfolio(
        [raw_research],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [_proposal(site, intent="conflicting demand intent")],
        business_id="portfolio",
        discovered_sites=[site],
        research_portfolio=research,
    )

    reviewed = review_proposed_actions(
        actions,
        discovered_sites=[site],
        capability_snapshot={"sites": [_capability(site)]},
        scope_locks={},
        safety_action_ceiling=10,
    )
    zero_action = review_zero_action(
        research_portfolio=research,
        reviewed_actions=reviewed,
    )

    assert reviewed[0]["schedule_class"] == "deferred"
    assert reviewed[0]["reason_code"] == "EVIDENCE_CONFLICT_UNRESOLVED"
    assert zero_action["result"] == "not_required"


@pytest.mark.asyncio
async def test_fixed_evidence_adapter_uses_the_same_normalization_seam():
    site = _site()
    sources = await collect_evidence(
        FixedEvidenceAdapter([_source("site_api")]),
        site=site,
        research_questions=["What product fact is current?"],
    )

    assert sources[0]["source_type"] == "site_api"
    assert sources[0]["collection_status"] == "success"


def test_new_site_adds_coverage_without_changing_decision_algorithm():
    first = _site()
    second = _site("22222222-2222-2222-2222-222222222222")
    sites = [first, second]
    research = normalize_research_portfolio(
        [_research(first), _research(second)],
        discovered_sites=sites,
        evidence_snapshot_id="snapshot-1",
    )
    actions = normalize_proposed_actions(
        [
            _proposal(first, intent="first independent intent"),
            _proposal(second, intent="second independent intent"),
        ],
        business_id="growing-portfolio",
        discovered_sites=sites,
        research_portfolio=research,
    )
    reviewed = review_proposed_actions(
        actions,
        discovered_sites=sites,
        capability_snapshot={
            "sites": [_capability(first), _capability(second)]
        },
        scope_locks={},
        safety_action_ceiling=10,
    )

    assert {item["site_id"] for item in build_site_results(sites, reviewed)} == {
        first["id"],
        second["id"],
    }


def test_normalized_payload_is_stable_when_caller_input_is_reused():
    site = _site()
    original = _research(site)
    copied = deepcopy(original)

    normalize_research_portfolio(
        [original],
        discovered_sites=[site],
        evidence_snapshot_id="snapshot-1",
    )

    assert original == copied
