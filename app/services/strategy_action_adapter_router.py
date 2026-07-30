"""Exact platform/action routing for the unified Strategy Action lifecycle."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_action_service import (
    ActionAdapter,
    BlockedActionAdapter,
)
from app.services.strategy_article_action_adapter import (
    ARTICLE_ACTIONS,
    StrategyArticleActionAdapter,
)
from app.services.strategy_on_page_action_adapter import (
    OEMAPPS_ON_PAGE_ACTIONS,
    OEMAppsOnPageActionAdapter,
    ShopifyProductSeoActionAdapter,
)


class StrategyActionAdapterRouter:
    """Route only an explicitly registered connector/action pair.

    The router is deliberately small: identity resolution and remote behavior
    remain inside each platform adapter. Unknown pairs receive the existing
    zero-write blocked adapter.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    def resolve(self, action: dict[str, Any]) -> ActionAdapter:
        action_type = str(action.get("action_type") or "").casefold()
        connector_type = str(action.get("connector_type") or "").casefold()
        if action_type in ARTICLE_ACTIONS:
            return StrategyArticleActionAdapter(self.session)
        if connector_type == "oemapps" and action_type in OEMAPPS_ON_PAGE_ACTIONS:
            return OEMAppsOnPageActionAdapter(self.session)
        if connector_type in {"shopify", "shopify_admin"} and (
            action_type == "product_seo"
        ):
            return ShopifyProductSeoActionAdapter(self.session)
        return BlockedActionAdapter()

    async def preview(
        self, action: dict[str, Any], patch: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.resolve(action).preview(action, patch)

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        return await self.resolve(action).execute(action)

    async def recover(self, action: dict[str, Any]) -> dict[str, Any]:
        return await self.resolve(action).recover(action)


__all__ = ["StrategyActionAdapterRouter"]
