"""Alembic env.py：从 .env 读 DATABASE_URL，用 psycopg 同步驱动。

注意：
- FastAPI 用 asyncpg（异步），但 Alembic migration 是**同步**流程。
- 同步用 psycopg v3。
- search_path 通过 connect_args 直接在 URL 里设（最稳，避免事务隔离问题）。
- alembic_version 表也建在 seo_agent schema 下。
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# 让 alembic 能 import app 包
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 从 .env 读 DATABASE_URL
try:
    from dotenv import dotenv_values
    env_vars = dotenv_values(ROOT / ".env") or {}
except ImportError:
    env_vars = {}

database_url = env_vars.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
if not database_url:
    raise SystemExit("DATABASE_URL not set; check .env or environment")

# Alembic 需要 sync 驱动。把 postgresql+asyncpg:// 替换为 postgresql+psycopg://
if database_url.startswith("postgresql+asyncpg://"):
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)

# 在 URL 里加 search_path 参数（避免 SET search_path 与 alembic transaction 冲突）
# PostgreSQL DSN: postgresql+psycopg://user:pass@host:port/db?options=-c search_path%3Dseo_agent
# psycopg 会把 -c 作为命令传给服务端
if "?" not in database_url:
    database_url = database_url + "?options=-c%20search_path%3Dseo_agent"
elif "options=" not in database_url:
    database_url = database_url + "&options=-c%20search_path%3Dseo_agent"

# this is the Alembic Config object
config = context.config
# config.set_main_option 会走 configparser 解析，需要 %% 转义
import shlex
_safe_url = database_url.replace("%", "%%")
config.set_main_option("sqlalchemy.url", _safe_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 我们没有用 SQLAlchemy declarative Base（schema 改用裸 SQL DDL），
# 所以 target_metadata 留 None。autogenerate 不适用。
target_metadata = None

# alembic_version 表建在 seo_agent schema 下
VERSION_TABLE_SCHEMA = "seo_agent"


def run_migrations_offline() -> None:
    """offline 模式：只生成 SQL 不连 DB。"""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=VERSION_TABLE_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """online 模式：连 PG 实际跑 migration。"""
    # 用 create_engine 直接构造，connect_args 通过 URL 的 options 传（更稳）
    connectable = create_engine(database_url, poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=VERSION_TABLE_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()