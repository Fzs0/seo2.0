"""Aggregated, auditable strategy-action exceptions stored in existing task JSONB."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def exception_fingerprint(item: dict[str, Any]) -> str:
    stable = "|".join(
        str(item.get(key) or "")
        for key in ("business_id", "site_id", "type", "stage", "error_code")
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


async def record_exception(session: AsyncSession, item: dict[str, Any]) -> dict[str, Any]:
    """Increment the matching root-cause record or create one without exposing secrets."""
    now = datetime.now(UTC).isoformat()
    payload = {
        **item,
        "exception_id": str(item.get("exception_id") or uuid4()),
        "fingerprint": exception_fingerprint(item),
        "raw_error": _sanitize_error(item.get("raw_error")),
        "first_seen_at": item.get("first_seen_at") or now,
        "last_seen_at": now,
        "occurrence_count": 1,
    }
    payload = sanitize_payload(payload)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"strategy-exception:{payload['fingerprint']}"},
    )
    existing = (
        await session.execute(
            text(
                """
                SELECT id::text, payload
                  FROM seo_agent.tasks
                 WHERE task_type = 'review'
                   AND payload->>'kind' = 'strategy_exception'
                   AND payload->>'fingerprint' = :fingerprint
                 ORDER BY created_at DESC LIMIT 1
                 FOR UPDATE
                """
            ),
            {"fingerprint": payload["fingerprint"]},
        )
    ).mappings().first()
    if existing:
        current = dict(existing["payload"] or {})
        first_seen_at = current.get("first_seen_at") or payload["first_seen_at"]
        occurrence_count = int(current.get("occurrence_count") or 0) + 1
        current.update(payload)
        current["exception_id"] = current.get("exception_id") or payload["exception_id"]
        current["first_seen_at"] = first_seen_at
        current["occurrence_count"] = occurrence_count
        await session.execute(
            text(
                "UPDATE seo_agent.tasks SET payload=CAST(:payload AS jsonb), updated_at=now() "
                "WHERE id=CAST(:id AS uuid)"
            ),
            {"id": existing["id"], "payload": json.dumps(current, ensure_ascii=False)},
        )
        return current
    await session.execute(
        text(
            """
            INSERT INTO seo_agent.tasks
              (id, task_type, status, priority, site_id, target_url, title, payload, decision)
            VALUES
              (CAST(:id AS uuid), 'review', 'queued', :priority,
               CAST(:site_id AS uuid), :target_url, :title,
               CAST(:payload AS jsonb), '{}'::jsonb)
            """
        ),
        {
            "id": str(uuid4()),
            "priority": payload.get("severity") or "P2",
            "site_id": payload.get("site_id"),
            "target_url": payload.get("target_url"),
            "title": f"strategy exception: {payload.get('type') or 'unknown'}",
            "payload": json.dumps({"kind": "strategy_exception", **payload}, ensure_ascii=False),
        },
    )
    return payload


_SENSITIVE_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "api-key",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
}
_SENSITIVE_QUERY_KEYS = _SENSITIVE_KEYS | {"key", "signature", "sig"}


def sanitize_payload(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact credentials before persistence or fingerprinting."""
    if key and key.casefold() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): sanitize_payload(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text_and_url(value)
    return value


def _sanitize_error(value: Any) -> str | None:
    if value is None:
        return None
    return _sanitize_text_and_url(str(value)[:4000])


def _sanitize_text_and_url(message: str) -> str:
    def redact_url(match: re.Match[str]) -> str:
        candidate = match.group(0)
        try:
            split = urlsplit(candidate)
            query = urlencode(
                [
                    (name, "[REDACTED]" if name.casefold() in _SENSITIVE_QUERY_KEYS else value)
                    for name, value in parse_qsl(split.query, keep_blank_values=True)
                ]
            )
            return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))
        except ValueError:
            return candidate

    message = re.sub(r"https?://[^\s<>'\"]+", redact_url, message)
    message = re.sub(
        r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
        r"\1 [REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)\b(authorization|api[_-]?key|password|secret|token|cookie|set-cookie)"
        r"\s*[:=]\s*[^\s,;&]+",
        r"\1=[REDACTED]",
        message,
    )
    return message


__all__ = ["exception_fingerprint", "record_exception", "sanitize_payload"]
