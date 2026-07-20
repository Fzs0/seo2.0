"""Pydantic Settings 配置层。所有可调项从 .env 读取，绝不在代码里硬编码。"""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用总配置。

    严格按 .env.example 的命名空间读；DATABASE_URL 兼容 postgresql+asyncpg:// 与
    postgresql://（asyncpg 兼容）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    app_name: str = "seo-workbench-fastapi"
    app_env: Literal["local", "dev", "prod"] = "local"
    app_port: int = 8000

    # 数据库
    database_url: str = "postgresql+asyncpg://seo:seo_dev_local@localhost:5433/seo_workbench"
    database_pool_size: int = 5
    database_max_overflow: int = 10

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # 日志
    log_level: str = "INFO"
    log_json: bool = True

    # 规则引擎
    rule_auto_reload_seconds: int = 60

    # AI provider
    ai_keyword_analysis_base_url: str = ""
    ai_keyword_analysis_key: str = ""
    ai_keyword_analysis_model: str = "deepseek-chat"
    ai_brief_generation_base_url: str = ""
    ai_brief_generation_key: str = ""
    ai_brief_generation_model: str = "deepseek-chat"
    ai_article_generation_base_url: str = ""
    ai_article_generation_key: str = ""
    ai_article_generation_model: str = "deepseek-chat"

    # SerpApi
    serpapi_key: str = ""

    # Image Providers
    image_pexels_key: str = ""
    image_unsplash_key: str = ""
    image_pixabay_key: str = ""

    # AI Brief 缓存
    ai_brief_cache_ttl_ms: int = 600000
    ai_brief_cache_max: int = 50

    # 外部 HTTP
    http_timeout_seconds: int = 30
    http_retry_max: int = 3
    ai_timeout_seconds: int = 90
    ai_retry_max: int = 2

    # Shopify Admin API（自有店铺 Client Credentials Grant）
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    shopify_api_version: str = "2026-07"

    # Minimal automation loop; disabled until the first manual run is verified.
    automation_enabled: bool = False
    automation_interval_seconds: int = 3600
    automation_batch_size: int = 1
    automation_min_impressions: int = 20

    def is_ai_stage_configured(self, stage: str) -> bool:
        """判断某个 AI 阶段是否真正配置了外部供应商（不配置就回退本地 brief）。"""
        base_url = getattr(self, f"ai_{stage}_base_url", "")
        api_key = getattr(self, f"ai_{stage}_key", "")
        return bool(base_url and api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例读 .env；测试时可用 get_settings.cache_clear() 重置。"""
    return Settings()
