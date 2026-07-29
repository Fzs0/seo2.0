"""Read-only aggregation of site capabilities from local persisted facts."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.article_urls import is_oemapps_site
from app.services.site_capability_registry import contract_for_site

HEALTH_STATES = {"available", "degraded", "unavailable", "misconfigured", "forbidden"}
_CONNECTOR_NAMES = ("products", "collections", "articles", "homepage", "images", "gsc", "ga4", "serp")


async def get_business_site_capabilities(
    session: AsyncSession,
    business_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = _utc(now)
    sites = await _load_sites(session, business_id=business_id)
    connectors = await _load_connectors(session, [str(site["id"]) for site in sites])
    by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for connector in connectors:
        by_site[str(connector["site_id"])].append(connector)
    items = [
        build_site_capability(
            site,
            connectors=by_site.get(str(site["id"]), []),
            generated_at=generated_at,
        )
        for site in sites
    ]
    result = {
        "business_id": business_id,
        "generated_at": generated_at.isoformat(),
        "discovered_site_count": len(items),
        "sites": items,
    }
    return result


async def get_site_capabilities(
    session: AsyncSession,
    site_id: str,
    *,
    expected_business_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    sites = await _load_sites(
        session, site_id=site_id, business_id=expected_business_id
    )
    if not sites:
        return None
    connectors = await _load_connectors(session, [site_id])
    return build_site_capability(sites[0], connectors=connectors, generated_at=_utc(now))


def build_site_capability(
    site: dict[str, Any],
    *,
    connectors: list[dict[str, Any]],
    generated_at: datetime,
) -> dict[str, Any]:
    safe_site = dict(site)
    api_config = _mapping(safe_site.get("api_config"))
    safe_site["api_config"] = api_config
    adapters = {str(item.get("adapter") or "").casefold() for item in connectors}
    contract = contract_for_site(safe_site, adapters)
    issues = _configuration_issues(safe_site, connectors)
    capability_health = {
        name: _default_health(name, generated_at) for name in _CONNECTOR_NAMES
    }

    for connector in connectors:
        adapter = str(connector.get("adapter") or "").casefold()
        declared = contract["connector_capabilities"] if adapter == "oemapps" else {}
        for name, access in declared.items():
            capability_health[name] = _connector_health(
                connector, access=access, checked_at=generated_at
            )

    if contract["supported_fields"].get("articles"):
        ready = _article_configuration_ready(safe_site)
        capability_health["articles"] = _local_health(
            available=ready,
            checked_at=generated_at,
            missing_code="article_connector_configuration_missing",
        )
    if is_oemapps_site(safe_site) or _is_wordpress_site(safe_site):
        capability_health["images"] = _local_health(
            available=(
                True
                if is_oemapps_site(safe_site)
                else _article_configuration_ready(safe_site)
            ),
            checked_at=generated_at,
            upload=True,
        )

    actions = dict(contract["supported_actions"])
    for action, connector_name in {
        "product_seo": "products",
        "product_image_alt": "products",
        "category_seo": "collections",
        "homepage_seo": "homepage",
        "new_article": "articles",
        "update_article": "articles",
    }.items():
        if action in actions and capability_health[connector_name]["status"] != "available":
            actions[action] = "forbidden"

    hosts = _canonical_hosts(safe_site)
    if not hosts:
        issues.append(
            _issue(
                "canonical_host_missing",
                "domain_or_base_url",
                "Configure a public site domain or base URL.",
            )
        )

    result = {
        "site_id": str(safe_site["id"]),
        "site_name": safe_site.get("name"),
        "site_role": _site_role(safe_site),
        "status": safe_site.get("status"),
        "strategy_enabled": bool(safe_site.get("strategy_enabled")),
        "domain": safe_site.get("base_url") or safe_site.get("domain"),
        "canonical_hosts": hosts,
        "market": safe_site.get("market"),
        "language_code": safe_site.get("language_code"),
        "content_scope": safe_site.get("content_scope") or [],
        "connectors": capability_health,
        "supported_actions": actions,
        "supported_fields": contract["supported_fields"],
        "side_effects": contract["side_effects"],
        "approval_policy": actions,
        "publishing": {
            "public_url_scheme": "https",
            "query_policy": "drop",
            "canonical_hosts": hosts,
        },
        "data_freshness": {
            name: {
                "checked_at": value["checked_at"],
                "last_success_at": value["last_success_at"],
            }
            for name, value in capability_health.items()
        },
        "configuration_issues": _dedupe_issues(issues),
    }
    result["capability_snapshot_hash"] = _capability_snapshot_hash(result)
    return result


async def _load_sites(
    session: AsyncSession,
    *,
    business_id: str | None = None,
    site_id: str | None = None,
) -> list[dict[str, Any]]:
    where = []
    params: dict[str, Any] = {}
    if business_id is not None:
        where.append("business_id = :business_id")
        params["business_id"] = business_id
    if site_id is not None:
        where.append("id = CAST(:site_id AS uuid)")
        params["site_id"] = site_id
    clause = " AND ".join(where) or "TRUE"
    rows = await session.execute(
        text(
            f"""
            SELECT id, site_key, name, site_type, domain, base_url, api_base_url,
                   market, language_code, content_role, content_scope, business_id,
                   strategy_enabled, is_main, status, api_config, raw, updated_at
              FROM seo_agent.sites
             WHERE {clause}
             ORDER BY is_main DESC, name ASC
            """
        ),
        params,
    )
    return [dict(row) for row in rows.mappings().all()]


async def _load_connectors(
    session: AsyncSession, site_ids: list[str]
) -> list[dict[str, Any]]:
    if not site_ids:
        return []
    rows = await session.execute(
        text(
            """
            SELECT c.id::text, c.site_id::text, c.status,
                   v.config->>'adapter' AS adapter,
                   COALESCE(sec.secret_names, ARRAY[]::text[]) AS secret_names,
                   v.verified_at,
                   latest.finished_at AS last_success_at,
                   latest.error_summary AS last_error
              FROM seo_agent.custom_connectors c
              JOIN seo_agent.custom_connector_versions v
                ON v.connector_id = c.id AND v.version = c.current_version
         LEFT JOIN seo_agent.custom_connector_secrets sec ON sec.connector_id = c.id
         LEFT JOIN LATERAL (
                    SELECT finished_at, error_summary
                      FROM seo_agent.custom_connector_runs r
                     WHERE r.connector_id = c.id AND r.status = 'succeeded'
                     ORDER BY r.started_at DESC LIMIT 1
                   ) latest ON TRUE
             WHERE c.site_id = ANY(CAST(:site_ids AS uuid[]))
            """
        ),
        {"site_ids": site_ids},
    )
    return [dict(row) for row in rows.mappings().all()]


def _connector_health(
    connector: dict[str, Any],
    *,
    access: dict[str, bool],
    checked_at: datetime,
) -> dict[str, Any]:
    secret_names = {str(value) for value in connector.get("secret_names") or []}
    status = str(connector.get("status") or "")
    if "token" not in secret_names:
        health = "misconfigured"
        error_code = "CONNECTOR_TOKEN_MISSING"
        summary = "Required connector credential is not configured."
        unlock = "Configure the encrypted connector token and verify the connector."
    elif status in {"disabled", "schema_changed"}:
        health = "unavailable"
        error_code = f"CONNECTOR_{status.upper()}"
        summary = "Connector is not active."
        unlock = "Test, verify, and activate the connector."
    elif status != "active":
        health = "misconfigured"
        error_code = "CONNECTOR_NOT_ACTIVE"
        summary = "Connector has not been activated."
        unlock = "Test, verify, and activate the connector."
    else:
        health = "available"
        error_code = None
        summary = _sanitize_error_summary(connector.get("last_error"))
        unlock = None
    return {
        "status": health,
        "read": bool(access.get("read")) and health == "available",
        "write": bool(access.get("write")) and health == "available",
        "checked_at": checked_at.isoformat(),
        "last_success_at": _iso(connector.get("last_success_at")),
        "error_code": error_code,
        "error_summary": summary,
        "unlock_condition": unlock,
    }


def _default_health(name: str, checked_at: datetime) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "read": False,
        "write": False,
        **({"upload": False} if name == "images" else {}),
        "checked_at": checked_at.isoformat(),
        "last_success_at": None,
        "error_code": "CAPABILITY_NOT_DECLARED",
        "error_summary": "No local connector contract declares this capability.",
        "unlock_condition": "Configure a supported connector for this site.",
    }


def _local_health(
    *, available: bool, checked_at: datetime, missing_code: str | None = None, upload: bool = False
) -> dict[str, Any]:
    result = {
        "status": "available" if available else "misconfigured",
        "read": available,
        "write": available,
        "checked_at": checked_at.isoformat(),
        "last_success_at": None,
        "error_code": None if available else (missing_code or "CONFIGURATION_MISSING").upper(),
        "error_summary": None if available else "Required local configuration is missing.",
        "unlock_condition": None if available else "Complete and verify the site connector configuration.",
    }
    if upload:
        result["upload"] = available
    return result


def _configuration_issues(
    site: dict[str, Any], connectors: list[dict[str, Any]]
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for field in ("business_id", "market", "language_code"):
        if not site.get(field):
            issues.append(
                _issue(
                    f"{field}_missing",
                    field,
                    f"Configure {field} and rerun capability discovery.",
                )
            )
    for connector in connectors:
        if str(connector.get("adapter") or "").casefold() == "oemapps" and "token" not in {
            str(value) for value in connector.get("secret_names") or []
        }:
            issues.append(
                _issue(
                    "connector_token_missing",
                    "oemapps.token",
                    "Configure the encrypted OEMApps token, test, and activate the connector.",
                )
            )
    return issues


def _canonical_hosts(site: dict[str, Any]) -> list[str]:
    raw = _mapping(site.get("raw"))
    configured = raw.get("canonicalHosts") or raw.get("canonical_hosts") or []
    if isinstance(configured, str):
        configured = [configured]
    candidates = [site.get("domain"), site.get("base_url"), *configured]
    hosts = {_normalize_host(value) for value in candidates}
    return sorted(host for host in hosts if host)


def _normalize_host(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = str(parsed.hostname or "").casefold().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _article_configuration_ready(site: dict[str, Any]) -> bool:
    config = _mapping(site.get("api_config"))
    site_type = str(site.get("site_type") or "")
    public_url = site.get("base_url") or site.get("domain")
    if site_type == "wp":
        return bool(public_url and config.get("username") and config.get("applicationPassword"))
    if site_type == "shopify":
        return bool(public_url)
    return bool(
        public_url
        and (
            config.get("openApiKey")
            or config.get("tokenA")
            or config.get("tokenB")
        )
    )


def _is_wordpress_site(site: dict[str, Any]) -> bool:
    config = _mapping(site.get("api_config"))
    connector_type = str(config.get("connector_type") or "").casefold()
    return str(site.get("site_type") or "").casefold() == "wp" or connector_type in {
        "wp",
        "wordpress",
    }


def _site_role(site: dict[str, Any]) -> str:
    if site.get("is_main"):
        return "main"
    role = str(site.get("content_role") or "").casefold()
    if "vertical" in role:
        return "vertical_blog"
    if site.get("site_type") in {"wp", "blog"}:
        return "brand_blog"
    return "traffic_site"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _issue(code: str, missing: str, unlock: str) -> dict[str, str]:
    return {
        "code": code,
        "missing_configuration": missing,
        "unlock_condition": unlock,
    }


def _dedupe_issues(items: list[dict[str, str]]) -> list[dict[str, str]]:
    return list({item["code"]: item for item in items}.values())


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def _sanitize_error_summary(value: Any) -> str | None:
    if not value:
        return None
    message = str(value)[:500]
    lowered = message.casefold()
    for marker in ("authorization:", "bearer ", "api_key=", "token=", "password="):
        index = lowered.find(marker)
        if index >= 0:
            return message[:index] + marker + "[REDACTED]"
    return message


def _capability_snapshot_hash(capability: dict[str, Any]) -> str:
    """Hash policy facts while ignoring refresh timestamps."""
    connector_policy = {
        name: {
            key: value.get(key)
            for key in ("status", "read", "write", "upload", "error_code")
            if key in value
        }
        for name, value in capability["connectors"].items()
    }
    policy = {
        key: capability.get(key)
        for key in (
            "site_id",
            "strategy_enabled",
            "canonical_hosts",
            "market",
            "language_code",
            "supported_actions",
            "supported_fields",
            "side_effects",
            "configuration_issues",
        )
    }
    policy["connectors"] = connector_policy
    encoded = json.dumps(
        policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


__all__ = [
    "HEALTH_STATES",
    "build_site_capability",
    "get_business_site_capabilities",
    "get_site_capabilities",
]
