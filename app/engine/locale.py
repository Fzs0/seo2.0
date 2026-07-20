"""市场 / 语言解析。所有预设从 payload.locale 读。"""
from __future__ import annotations

import re
from typing import Any

from app.engine.loader import get_store


def _normalize_market(value: str) -> str:
    token = (value or "").strip().split("/")[0].strip().upper()
    aliases = get_store().get("locale.marketAliases", {"GB": "UK"}) or {}
    return aliases.get(token, token)


def _normalize_language(value: str) -> str:
    return (value or "").strip().lower()


def locale_for_market(market_value: str = "") -> dict[str, Any]:
    raw_market = (market_value or "").strip()
    if not raw_market:
        return {
            "configured": False,
            "rawMarket": raw_market,
            "market": "",
            "countryCode": "",
            "googleGl": "",
            "googleHl": "",
            "language": "",
            "languageCode": "",
            "semrushDatabase": "",
            "warning": "Target market is not selected. Do not run production keyword analysis or article generation.",
        }

    country_part, _, language_part = (part.strip() for part in raw_market.partition("/"))
    market_token = _normalize_market(country_part or raw_market)
    presets = get_store().get("locale.presets", {}) or {}
    languages = get_store().get("locale.languages", {}) or {}

    preset = presets.get(market_token)
    language_preset = languages.get(_normalize_language(language_part))
    fallback_gl = market_token.lower() if len(market_token) == 2 else ""
    fallback_language = language_part or (preset or {}).get("language") or ""
    fallback_language_preset = languages.get(_normalize_language(fallback_language)) or {}
    fallback_language_code = {
        "english": "en",
        "german": "de",
        "french": "fr",
        "spanish": "es",
    }.get(_normalize_language(fallback_language), "")

    return {
        "configured": True,
        "rawMarket": raw_market,
        "market": (preset or {}).get("market") or country_part or raw_market,
        "countryCode": (preset or {}).get("countryCode") or market_token,
        "googleGl": (preset or {}).get("googleGl", fallback_gl),
        "googleHl": (language_preset or {}).get("googleHl") or (preset or {}).get("googleHl") or fallback_language_preset.get("googleHl") or fallback_language_code,
        "language": (language_preset or {}).get("language") or (preset or {}).get("language") or fallback_language,
        "languageCode": (language_preset or {}).get("languageCode") or (preset or {}).get("languageCode") or fallback_language_preset.get("languageCode") or fallback_language_code,
        "semrushDatabase": (preset or {}).get("semrushDatabase", fallback_gl) if preset else fallback_gl,
        "warning": get_store().get("locale.warnings.EU", "") if market_token == "EU" else "",
    }


def locale_for_project(project: dict[str, Any] | None) -> dict[str, Any]:
    return locale_for_market((project or {}).get("market", ""))


def locale_instruction(locale: dict[str, Any]) -> str:
    if not locale.get("configured"):
        return "Locale status: NOT CONFIGURED.\nDo not claim local SERP validation."
    parts = [
        f"Target market: {locale.get('market')} ({locale.get('countryCode')})",
        f"Content language: {locale.get('language') or 'Not specified'} ({locale.get('languageCode') or 'unknown'})",
        f"Google SERP locale: gl={locale.get('googleGl') or 'not-set'}, hl={locale.get('googleHl') or 'not-set'}",
        f"Semrush database: {locale.get('semrushDatabase') or 'not-set / split manually'}",
    ]
    if locale.get("warning"):
        parts.append(f"Locale warning: {locale['warning']}")
    return "\n".join(parts)
