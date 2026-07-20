from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import InvalidInputError
from .schemas import ImportSignalsRequest


SIGNAL_COLUMNS = """
    ms.id, ms.source_id, s.name AS source_name, ms.platform,
    s.channel, ms.external_id, ms.canonical_url, ms.content_kind, ms.title,
    ms.content, ms.author_handle, ms.thread_key, ms.parent_external_id,
    ms.language_code, ms.market, ms.published_at, ms.engagement, ms.metadata,
    ms.status, ms.created_at, ms.updated_at
"""

_WHITESPACE = re.compile(r"\s+")


def signal_content_hash(content: str) -> str:
    normalized = _WHITESPACE.sub(" ", content).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _source_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "channel": row["channel"],
        "source_type": row["source_type"],
        "base_url": row["base_url"],
        "domain": row["domain"],
        "rights_confirmed": row["rights_confirmed"],
        "status": row["status"],
        "metadata": row["metadata"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class MarketSignalService:
    """Deep module for platform-neutral market signal storage and exploration."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def import_signals(self, request: ImportSignalsRequest) -> dict[str, Any]:
        if request.rights_confirmed is not True:
            raise InvalidInputError("rights_confirmed must be true")

        platform = request.platform.strip().lower()
        async with self._session.begin():
            source = (
                await self._session.execute(
                    text(
                        """
                        INSERT INTO knowledge.sources
                            (name, channel, source_type, rights_confirmed, metadata)
                        VALUES (:name, :channel, 'market_signal', true,
                                CAST(:metadata AS jsonb))
                        ON CONFLICT (lower(name), channel) DO UPDATE SET
                            rights_confirmed = knowledge.sources.rights_confirmed,
                            metadata = knowledge.sources.metadata || EXCLUDED.metadata
                        RETURNING id, name, channel, source_type, base_url, domain,
                                  rights_confirmed, status, metadata, created_at, updated_at
                        """
                    ),
                    {
                        "name": request.source_name,
                        "channel": request.channel,
                        "metadata": '{"kind":"market_signal_source"}',
                    },
                )
            ).mappings().one()

            created = 0
            duplicates = 0
            for signal in request.signals:
                inserted_id = (
                    await self._session.execute(
                        text(
                            """
                            INSERT INTO knowledge.market_signals
                                (source_id, platform, external_id, canonical_url, content_kind,
                                 title, content, author_handle, thread_key,
                                 parent_external_id, language_code, market,
                                 published_at, engagement, metadata, content_hash)
                            VALUES
                                (:source_id, :platform, :external_id, :canonical_url, :content_kind,
                                 :title, :content, :author_handle, :thread_key,
                                 :parent_external_id, :language_code, :market,
                                 :published_at, CAST(:engagement AS jsonb),
                                 CAST(:metadata AS jsonb), :content_hash)
                            ON CONFLICT DO NOTHING
                            RETURNING id
                            """
                        ),
                        {
                            "source_id": source["id"],
                            "platform": platform,
                            "external_id": signal.external_id,
                            "canonical_url": signal.canonical_url,
                            "content_kind": signal.content_kind,
                            "title": signal.title,
                            "content": signal.content,
                            "author_handle": signal.author_handle,
                            "thread_key": signal.thread_key,
                            "parent_external_id": signal.parent_external_id,
                            "language_code": signal.language_code,
                            "market": signal.market,
                            "published_at": signal.published_at,
                            "engagement": _json(signal.engagement),
                            "metadata": _json(signal.metadata),
                            "content_hash": signal_content_hash(signal.content),
                        },
                    )
                ).scalar_one_or_none()
                if inserted_id is None:
                    duplicates += 1
                else:
                    created += 1

        return {
            "source": _source_from_row(dict(source)),
            "received": len(request.signals),
            "created": created,
            "duplicates": duplicates,
        }

    async def list_signals(
        self,
        *,
        query: str | None,
        source_id: UUID | None,
        content_kind: str | None,
        date_from: date | None,
        date_to: date | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        if date_from and date_to and date_from > date_to:
            raise InvalidInputError("date_from must be on or before date_to")

        predicates = ["1=1"]
        params: dict[str, Any] = {
            "source_id": source_id,
            "content_kind": content_kind,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "offset": offset,
        }
        if query and query.strip():
            predicates.append(
                """(
                    lower(ms.content) LIKE lower(CAST(:query_pattern AS text)) ESCAPE '\\'
                    OR lower(COALESCE(ms.title, '')) LIKE lower(CAST(:query_pattern AS text)) ESCAPE '\\'
                    OR lower(COALESCE(ms.author_handle, '')) LIKE lower(CAST(:query_pattern AS text)) ESCAPE '\\'
                    OR lower(s.name) LIKE lower(CAST(:query_pattern AS text)) ESCAPE '\\'
                )"""
            )
            params["query_pattern"] = f"%{_escape_like(query.strip())}%"
        if source_id:
            predicates.append("ms.source_id = :source_id")
        if content_kind:
            predicates.append("ms.content_kind = :content_kind")
        if date_from:
            predicates.append("COALESCE(ms.published_at, ms.created_at) >= CAST(:date_from AS date)")
        if date_to:
            predicates.append("COALESCE(ms.published_at, ms.created_at) < CAST(:date_to AS date) + INTERVAL '1 day'")
        where = "WHERE " + " AND ".join(predicates)
        from_sql = """
            FROM knowledge.market_signals ms
            JOIN knowledge.sources s ON s.id = ms.source_id
        """

        total = (
            await self._session.execute(
                text("SELECT count(*)" + from_sql + where), params
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                text(
                    "SELECT "
                    + SIGNAL_COLUMNS
                    + from_sql
                    + where
                    + " ORDER BY COALESCE(ms.published_at, ms.created_at) DESC, ms.id"
                    + " LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        ).mappings().all()
        return {"items": [dict(row) for row in rows], "total": total}

    async def overview(self) -> dict[str, Any]:
        totals = (
            await self._session.execute(
                text(
                    """
                    SELECT
                        count(*) AS total,
                        count(*) FILTER (WHERE COALESCE(published_at, created_at) >= now() - interval '7 days') AS last_7_days,
                        count(*) FILTER (WHERE COALESCE(published_at, created_at) >= now() - interval '30 days') AS last_30_days
                    FROM knowledge.market_signals
                    """
                )
            )
        ).mappings().one()
        by_kind = (
            await self._session.execute(
                text(
                    """
                    SELECT content_kind, count(*) AS count
                    FROM knowledge.market_signals
                    GROUP BY content_kind
                    ORDER BY count DESC, content_kind
                    """
                )
            )
        ).mappings().all()
        by_platform = (
            await self._session.execute(
                text(
                    """
                    SELECT ms.platform, count(*) AS count
                    FROM knowledge.market_signals ms
                    GROUP BY ms.platform
                    ORDER BY count DESC, platform
                    """
                )
            )
        ).mappings().all()
        return {
            "total": totals["total"],
            "last_7_days": totals["last_7_days"],
            "last_30_days": totals["last_30_days"],
            "by_kind": {row["content_kind"]: row["count"] for row in by_kind},
            "by_platform": {row["platform"]: row["count"] for row in by_platform},
        }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
