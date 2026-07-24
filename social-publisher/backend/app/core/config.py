from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+asyncpg://publisher:publisher@127.0.0.1:55432/social_publisher"
    )
    database_pool_size: int = 5
    database_max_overflow: int = 5
    connector_secret_key: str = ""
    social_executor_url: str = "http://127.0.0.1:4317"
    social_executor_shared_secret: str = ""
    frontend_origin: str = "http://127.0.0.1:5173"

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
