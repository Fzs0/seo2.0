"""Build the immutable, role-aware input shared by every article generation stage."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.loader import get_store
from app.services.site_service import get_site


CONTEXT_VERSION = "article-generation-context-v2"

DEFAULT_GENERATION_POLICY: dict[str, Any] = {
    "business_type": "general",
    "risk_level": "standard",
    "keyword_triggers": {
        "health": ["health", "medical", "disease", "pregnan", "bacteria", "mold"],
        "safety": ["safe", "safety", "danger", "risk", "injury"],
        "law": ["legal", "law", "regulation", "age limit", "underage"],
        "finance": ["financial", "investment", "investing", "tax", "loan", "mortgage", "insurance"],
        "battery": ["battery", "dispose", "disposal", "recycle", "lithium"],
    },
    "claim_terms": [
        "health", "medical", "safe", "safety", "legal", "law", "underage", "battery", "dispose", "recycle",
        "bacteria", "mold", "financial", "investment", "tax", "loan", "mortgage", "insurance",
    ],
    "allowed_sources": [],
    "required_modules": [],
    "forbidden_claims": [],
    "generic_opening_patterns": [],
}


def context_hash(context: dict[str, Any]) -> str:
    """Return a stable fingerprint without hashing a previous fingerprint."""
    canonical = {key: value for key, value in context.items() if key != "context_hash"}
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def build_generation_context(
    session: AsyncSession,
    *,
    site_id: str,
    keyword: dict[str, Any],
    approved_strategy: dict[str, Any] | None,
    serp: dict[str, Any] | None,
) -> dict[str, Any]:
    """Freeze only verified site/strategy/SERP inputs for one generation run.

    This deliberately does not create a database table in V1.  The returned
    snapshot is persisted in ``articles.article_parts`` by the caller.
    """
    site = await get_site(session, site_id)
    strategy = approved_strategy or {}
    profile = _confirmed_profile(site)
    generation_policy = _generation_policy(profile)
    site_role = _site_role(site, generation_policy)
    target_asset = _target_asset(keyword, strategy, profile)
    link_plan = _link_plan(strategy)
    risk_topics = _risk_topics(str(keyword.get("keyword") or ""), generation_policy)
    sources = _sources(profile, strategy, risk_topics, generation_policy)
    required_modules = _required_modules(keyword, strategy, serp or {}, site_role, target_asset, link_plan, generation_policy)
    site_profile = _site_profile(site, profile)
    hold_reasons: list[str] = []

    if site_role == "unknown":
        hold_reasons.append("site_generation_role_unknown")
    if site_role == "main" and not target_asset.get("url"):
        hold_reasons.append("main_site_target_asset_missing")
    if site_role == "main" and not target_asset.get("verified_facts"):
        hold_reasons.append("main_site_verified_facts_missing")
    if site and site_role == "content" and not _has_content_profile(site_profile):
        hold_reasons.append("content_site_profile_incomplete")
    if site and site_role == "content" and not _user_questions(keyword, strategy, serp or {}) and not _raw_serp_questions(serp or {}):
        hold_reasons.append("content_user_question_missing")
    if site and site_role == "content" and not _has_serp_or_source_evidence(serp or {}, sources):
        hold_reasons.append("content_serp_or_sources_missing")
    if risk_topics and not sources:
        hold_reasons.append("high_risk_sources_missing")

    context: dict[str, Any] = {
        "context_version": CONTEXT_VERSION,
        "site_role": site_role,
        "site_profile": site_profile,
        "keyword_and_intent": {
            "keyword": keyword.get("keyword"),
            "intent": keyword.get("intent"),
            "market": keyword.get("market"),
            "language_code": keyword.get("language_code"),
            "user_questions": _user_questions(keyword, strategy, serp or {}),
        },
        "metadata_policy": _metadata_policy(keyword, generation_policy),
        "article_goal": {
            "article_type": keyword.get("page_type") or strategy.get("page_type") or strategy.get("strategy_type"),
            "unique_angle": strategy.get("briefDirection") or strategy.get("recommended_action") or "",
            "must_answer": _must_answer(strategy),
            "time_sensitive": _is_time_sensitive(keyword, strategy),
        },
        "target_asset": target_asset,
        "link_policy": {
            "internal_links": link_plan,
            "allow_external_links": bool((site or {}).get("allow_external_links", False)),
            "cross_site_policy": _cross_site_policy(site_role, profile, link_plan),
        },
        "serp_snapshot": _serp_snapshot(serp or {}, keyword, strategy),
        "facts_and_sources": sources,
        "required_modules": required_modules,
        "forbidden_claims": _forbidden_claims(risk_topics, generation_policy),
        "risk_topics": risk_topics,
        "risk_claim_terms": generation_policy.get("claim_terms") or [],
        "requires_claim_sources": bool(risk_topics) or generation_policy.get("risk_level") in {"regulated", "ymyl"},
        "generation_policy": generation_policy,
        "search_inclusion_rules": _string_list(get_store().get("articleGenerationContext.searchInclusionRules", [])),
        "hold_reasons": hold_reasons,
    }
    context["context_hash"] = context_hash(context)
    return context


def context_prompt_block(context: dict[str, Any]) -> str:
    """A single explicit prompt block prevents stages from inventing site context."""
    return json.dumps(context, ensure_ascii=False, indent=2, default=str)


def _confirmed_profile(site: dict[str, Any] | None) -> dict[str, Any]:
    profile = (site or {}).get("knowledge_profile")
    return profile if isinstance(profile, dict) and profile.get("status") == "confirmed" else {}


def _site_profile(site: dict[str, Any] | None, profile: dict[str, Any]) -> dict[str, Any]:
    policy = _generation_policy(profile)
    return {
        "site_id": str((site or {}).get("id") or ""),
        "site_key": (site or {}).get("site_key") or "",
        "name": (site or {}).get("name") or "",
        "positioning": profile.get("positioning") or (site or {}).get("content_role") or "",
        "audience": profile.get("audience") or "",
        "tone": profile.get("tone") or "",
        "in_scope_topics": _string_list(profile.get("in_scope_topics")) or _string_list((site or {}).get("content_scope")),
        "out_of_scope_topics": _string_list(profile.get("out_of_scope_topics")) + _string_list(profile.get("restricted_topics")),
        "content_types": _string_list(profile.get("content_types")),
        "editorial_rules": _string_list(profile.get("editorial_rules")),
        "internal_link_rules": _string_list(profile.get("internal_link_rules")),
        "business_type": policy.get("business_type") or "general",
        "risk_level": policy.get("risk_level") or "standard",
        "generic_opening_patterns": _string_list(policy.get("generic_opening_patterns")),
        "faq_heading_patterns": _string_list(policy.get("faq_heading_patterns")),
    }


def _site_role(site: dict[str, Any] | None, policy: dict[str, Any]) -> str:
    explicit = str(policy.get("site_role") or "").casefold()
    if explicit in {"main", "commercial", "local_service", "service"}:
        return "main"
    if explicit in {"content", "editorial", "blog"}:
        return "content"
    if not site:
        return "content"
    if site.get("is_main") or site.get("site_type") in {"main", "shopify"}:
        return "main"
    if site.get("site_type") in {"wp", "blog"}:
        return "content"
    return "unknown"


def _target_asset(keyword: dict[str, Any], strategy: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    raw = strategy.get("target_asset") or strategy.get("targetAsset")
    asset = dict(raw) if isinstance(raw, dict) else {}
    url = (
        asset.get("url")
        or keyword.get("target_asset_url")
        or keyword.get("targetAssetUrl")
        or strategy.get("target_asset_url")
        or strategy.get("targetAssetUrl")
    )
    if not url:
        for candidate in profile.get("conversion_targets") or []:
            if isinstance(candidate, str) and candidate.strip():
                url = candidate.strip()
                break
            if isinstance(candidate, dict) and candidate.get("url"):
                url = candidate["url"]
                asset = {**candidate, **asset}
                break
    verified_facts = _string_list(asset.get("facts")) or _string_list(asset.get("verified_facts"))
    core_pages = profile.get("core_pages") if isinstance(profile.get("core_pages"), list) else []
    matched_page = next((page for page in core_pages if isinstance(page, dict) and str(page.get("url") or "").rstrip("/") == str(url or "").rstrip("/")), {})
    if isinstance(matched_page, dict):
        asset = {**matched_page, **asset}
    if not verified_facts:
        verified_facts = _string_list(profile.get("products")) + _string_list(profile.get("services"))
    for verified_asset in profile.get("verified_assets") or []:
        if not isinstance(verified_asset, dict) or not verified_asset.get("url"):
            continue
        if str(verified_asset["url"]).rstrip("/") != str(url or "").rstrip("/"):
            continue
        asset = {**verified_asset, **asset}
        verified_facts = verified_facts or _string_list(verified_asset.get("facts"))
        break
    return {
        "url": str(url or "").strip(),
        "type": asset.get("type") or asset.get("page_type") or keyword.get("asset_status") or "",
        "title": asset.get("title") or "",
        "verified_facts": verified_facts[:12],
    }


def _link_plan(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    plan = strategy.get("internal_link_plan") or strategy.get("internalLinkPlan") or []
    return [dict(item) for item in plan if isinstance(item, dict) and item.get("url")][:8] if isinstance(plan, list) else []


def _generation_policy(profile: dict[str, Any]) -> dict[str, Any]:
    configured = profile.get("generation_policy") if isinstance(profile.get("generation_policy"), dict) else {}
    rule_policy = get_store().get("articleGenerationContext.defaultGenerationPolicy", {}) or {}
    policy = {**DEFAULT_GENERATION_POLICY, **(rule_policy if isinstance(rule_policy, dict) else {}), **configured}
    triggers = {**DEFAULT_GENERATION_POLICY["keyword_triggers"]}
    for source in (rule_policy, configured):
        if isinstance(source, dict) and isinstance(source.get("keyword_triggers"), dict):
            triggers.update(source["keyword_triggers"])
    policy["keyword_triggers"] = triggers
    policy["claim_terms"] = list(dict.fromkeys(_string_list(DEFAULT_GENERATION_POLICY["claim_terms"]) + _string_list(policy.get("claim_terms"))))
    policy["allowed_sources"] = [item for item in policy.get("allowed_sources") or [] if isinstance(item, (str, dict))]
    return policy


def _risk_topics(keyword: str, policy: dict[str, Any]) -> list[str]:
    lowered = keyword.casefold()
    groups = policy.get("keyword_triggers") or {}
    return [str(name) for name, terms in groups.items() if any(str(term).casefold() in lowered for term in _string_list(terms))]


def _sources(profile: dict[str, Any], strategy: dict[str, Any], risk_topics: list[str], policy: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[Any] = []
    for container in (profile, strategy, policy):
        for key in ("facts_and_sources", "factsAndSources", "sources", "approved_sources", "approvedSources", "allowed_sources"):
            value = container.get(key) if isinstance(container, dict) else None
            candidates.extend(value if isinstance(value, list) else [value] if value else [])
    results: list[dict[str, str]] = []
    for source in candidates:
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            results.append({"url": source, "label": source, "source_type": "approved"})
        elif isinstance(source, dict) and source.get("url"):
            results.append({
                "url": str(source["url"]),
                "label": str(source.get("label") or source.get("title") or source["url"]),
                "source_type": str(source.get("source_type") or "approved"),
            })

    reference_rules = get_store().get("references", {}) or {}
    for trigger in (reference_rules.get("triggers") or {}).values() if isinstance(reference_rules, dict) else []:
        if not isinstance(trigger, dict) or not trigger.get("url"):
            continue
        terms = " ".join(str(item) for item in trigger.get("terms") or []).casefold()
        policy_terms = [term for topic in risk_topics for term in _string_list((policy.get("keyword_triggers") or {}).get(topic))]
        if any(_related_term(term, source_term) for term in policy_terms for source_term in _string_list(trigger.get("terms"))):
            results.append({"url": str(trigger["url"]), "label": str(trigger.get("label") or trigger["url"]), "source_type": "policy_reference"})
    unique: dict[str, dict[str, str]] = {item["url"]: item for item in results}
    return list(unique.values())[:12]


def _required_modules(
    keyword: dict[str, Any], strategy: dict[str, Any], serp: dict[str, Any], site_role: str, target_asset: dict[str, Any], link_plan: list[dict[str, Any]], policy: dict[str, Any],
) -> list[str]:
    explicit = strategy.get("required_modules") or strategy.get("requiredModules")
    modules = _string_list(explicit) or _string_list(policy.get("required_modules"))
    page_type = " ".join(str(keyword.get(key) or "") for key in ("page_type", "page_role", "intent")).casefold()
    query = str(keyword.get("keyword") or "").casefold()
    if not modules and ("compare" in query or " vs " in query or "comparison" in page_type):
        modules.append("comparison_table")
    if not modules and (serp.get("related_questions") or serp.get("people_also_ask")):
        modules.append("faq")
    if site_role == "main" and (target_asset.get("url") or link_plan):
        modules.append("contextual_internal_link")
    return list(dict.fromkeys(modules))


def _user_questions(keyword: dict[str, Any], strategy: dict[str, Any], serp: dict[str, Any]) -> list[str]:
    values = _string_list(strategy.get("user_questions")) + _string_list(strategy.get("user_question"))
    values += [question for question in _raw_serp_questions(serp) if _question_matches_keyword(question, str(keyword.get("keyword") or ""))]
    return list(dict.fromkeys([value.strip() for value in values if value.strip()]))[:6]


def _raw_serp_questions(serp: dict[str, Any]) -> list[str]:
    return [str(item.get("question") or item.get("title") or "").strip() for item in (serp.get("related_questions") or []) if isinstance(item, dict) and str(item.get("question") or item.get("title") or "").strip()]


def _question_matches_keyword(question: str, keyword: str) -> bool:
    """Do not make a neighbouring SERP question mandatory for this article."""
    stop_words = {"about", "best", "choose", "guide", "how", "overthinking", "the", "to", "what", "which", "with", "without"}
    keyword_terms = [term for term in re.findall(r"[a-z0-9]{3,}", keyword.casefold()) if term not in stop_words]
    question_terms = [term for term in re.findall(r"[a-z0-9]{3,}", question.casefold()) if term not in stop_words]
    if not keyword_terms or not question_terms:
        return False
    matches = {left for left in keyword_terms if any(_related_term(left, right) for right in question_terms)}
    return len(matches) >= min(2, len(set(keyword_terms)))


def _must_answer(strategy: dict[str, Any]) -> list[str]:
    return _string_list(strategy.get("must_answer")) + _string_list(strategy.get("mustAnswer"))


def _serp_snapshot(serp: dict[str, Any], keyword: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any]:
    evidence = strategy.get("evidence") if isinstance(strategy.get("evidence"), dict) else {}
    audit = evidence.get("content_audit") if isinstance(evidence.get("content_audit"), dict) else {}
    return {
        "id": str(serp.get("id") or ""),
        "source": serp.get("source") or "",
        "market": keyword.get("google_gl") or keyword.get("market") or "",
        "language": keyword.get("google_hl") or keyword.get("language_code") or "",
        "competitor_gap": audit.get("competitor_gap") or strategy.get("competitor_gap") or {},
        "people_also_ask": _user_questions({}, {}, serp),
    }


def _forbidden_claims(risk_topics: list[str], policy: dict[str, Any]) -> list[str]:
    common = ["Never invent product specifications, prices, stock, delivery terms, rankings, URLs, or first-hand testing."]
    if risk_topics:
        common.append("Do not make a regulated, health, safety, legal, financial, or compliance claim unless supported by an allowed source in this context.")
    return common + _string_list(policy.get("forbidden_claims"))


def _is_time_sensitive(keyword: dict[str, Any], strategy: dict[str, Any]) -> bool:
    if strategy.get("time_sensitive") is not None:
        return bool(strategy.get("time_sensitive"))
    query = str(keyword.get("keyword") or "").casefold()
    return any(term in query for term in ("best", "latest", "new", "top ", "202", "20"))


def _metadata_policy(keyword: dict[str, Any], policy: dict[str, Any]) -> dict[str, int]:
    language = str(keyword.get("language_code") or "").casefold()
    configured = policy.get("metadata_length") if isinstance(policy.get("metadata_length"), dict) else {}
    if language in {"zh", "ja", "ko"}:
        return {"min": int(configured.get("min", 50)), "max": int(configured.get("max", 160))}
    return {"min": int(configured.get("min", 120)), "max": int(configured.get("max", 160))}


def _has_serp_or_source_evidence(serp: dict[str, Any], sources: list[dict[str, str]]) -> bool:
    return bool(
        serp.get("id")
        or serp.get("organic_results")
        or serp.get("related_questions")
        or serp.get("related_searches")
        or sources
    )


def _related_term(left: str, right: str) -> bool:
    def stem(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
        for suffix in ("ing", "es", "s"):
            if normalized.endswith(suffix) and len(normalized) > len(suffix) + 2:
                normalized = normalized[: -len(suffix)]
                break
        return normalized[:-1] if normalized.endswith("e") and len(normalized) > 3 else normalized

    return bool(left and right and (stem(left) == stem(right) or stem(left) in stem(right) or stem(right) in stem(left)))


def _cross_site_policy(site_role: str, profile: dict[str, Any], link_plan: list[dict[str, Any]]) -> str:
    explicit = _string_list(profile.get("cross_site_link_policy"))
    if explicit:
        return explicit[0]
    if site_role == "main":
        return "Use only approved commercial target or internal-link-plan URLs."
    return "Do not add cross-site links unless an approved link plan supplies the URL."


def _has_content_profile(profile: dict[str, Any]) -> bool:
    return bool(profile.get("positioning") and (profile.get("audience") or profile.get("in_scope_topics")))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[\n,;；]+", value) if part.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
