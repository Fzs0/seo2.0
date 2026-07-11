"""规则加载器：从 PG 的 seo_agent.v_active_rule_set 读最新生效的 payload，缓存到内存。

热加载：每 rule_auto_reload_seconds 秒检查一次 PG 中的 effective_at 是否变化；
POST /admin/reload 触发立即重读。

payload 结构与 workflows/seo-standard.json 完全等价。
"""
import asyncio
import json
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.engine import settings as _settings  # noqa: F401  保留用于未来扩展

logger = structlog.get_logger(__name__)


class RuleStore:
    """进程内单例，承载当前生效的 payload。"""

    def __init__(self) -> None:
        self._payload: dict[str, Any] = {}
        self._version: str = ""
        self._loaded_at: datetime | None = None
        self._effective_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def payload(self) -> dict[str, Any]:
        return self._payload

    @property
    def version(self) -> str:
        return self._version

    @property
    def loaded_at(self) -> datetime | None:
        return self._loaded_at

    @property
    def effective_at(self) -> datetime | None:
        return self._effective_at

    async def load_from_db(self, session: AsyncSession | None = None) -> dict[str, Any]:
        """从 PG 读 v_active_rule_set 的 payload，覆盖内存。"""
        own_session = session is None
        sess = session or SessionLocal()
        try:
            result = await sess.execute(
                text(
                    "SELECT id, version, payload, effective_at "
                    "FROM seo_agent.v_active_rule_set "
                    "LIMIT 1"
                )
            )
            row = result.mappings().first()
            if not row:
                logger.warning("no_active_rule_set_in_db")
                return {}
            async with self._lock:
                payload = row["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                self._payload = payload
                self._version = row["version"]
                self._effective_at = row["effective_at"]
                self._loaded_at = datetime.utcnow()
            logger.info(
                "rule_store_loaded",
                version=self._version,
                effective_at=str(self._effective_at),
                keys=sorted(self._payload.keys()),
            )
            return self._payload
        finally:
            if own_session:
                await sess.close()

    async def reload_if_changed(self) -> bool:
        """检查 PG 中 active 版本的 effective_at 是否比内存里的新；是则重载。"""
        async with SessionLocal() as sess:
            result = await sess.execute(
                text(
                    "SELECT version, effective_at "
                    "FROM seo_agent.v_active_rule_set LIMIT 1"
                )
            )
            row = result.mappings().first()
        if not row:
            return False
        db_effective = row["effective_at"]
        if db_effective and (self._effective_at is None or db_effective > self._effective_at):
            await self.load_from_db()
            return True
        return False

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """按点路径读 payload，例如 'scoring.formula.demandCoeff'。"""
        node: Any = self._payload
        for part in dotted_key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


_store = RuleStore()


def get_store() -> RuleStore:
    """全局唯一 store。FastAPI 启动时调一次 load_from_db 即可。"""
    return _store