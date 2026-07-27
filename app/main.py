"""FastAPI 应用入口：lifespan、middleware 挂载、路由注册、异常处理。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.core.database_version import require_postgresql_17
from app.core.google_config import get_store as get_google_store
from app.core.logging import configure_logging, get_logger
from app.core.runtime_identity import capture_runtime_identity, runtime_health
from app.engine.loader import get_store
from app.middleware.errors import install_exception_handlers
from app.middleware.metrics import MetricsMiddleware, metrics_response
from app.middleware.trace import TraceIdMiddleware

logger = get_logger(__name__)
settings = get_settings()
runtime_identity = capture_runtime_identity()

# Google 同步调度：每 N 秒跑一次（默认 6 小时 = 21600 秒）。
GOOGLE_SYNC_INTERVAL_SECONDS = 21600
EFFECT_CHECK_INTERVAL_SECONDS = 3600


async def _periodic_reload(stop_event: asyncio.Event) -> None:
    """每 rule_auto_reload_seconds 秒检查一次 PG 是否需要重载规则。"""
    store = get_store()
    interval = max(1, settings.rule_auto_reload_seconds)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            try:
                if await store.reload_if_changed():
                    logger.info("rule_store_auto_reloaded", version=store.version)
            except Exception as e:  # noqa: BLE001
                logger.warning("rule_auto_reload_failed", error=str(e))


async def _periodic_google_sync(stop_event: asyncio.Event) -> None:
    """每 GOOGLE_SYNC_INTERVAL_SECONDS 秒跑一次全量 Google 数据同步。

    只在 google_data_sources 有数据时跑；否则跳过（启动期间第一次拉不到 config 也行）。
    """
    from app.services.google_sync import sync_all_sources

    # 启动后先 sleep 一个 interval，避免跟其他启动任务抢资源
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=GOOGLE_SYNC_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            store = get_google_store()
            if not store.is_loaded():
                logger.info("google_sync_skipped_no_config")
                continue
            async with SessionLocal() as session:
                results = await sync_all_sources(session, days_back=30, trigger="scheduled")
            ok_n = sum(1 for r in results if r.get("ok"))
            logger.info(
                "google_sync_scheduled_done",
                total=len(results),
                ok=ok_n,
                sources=[r.get("domain") for r in results],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("google_sync_scheduled_failed", error=str(e))


async def _periodic_scheduled_automation(stop_event: asyncio.Event) -> None:
    from app.services.automation_service import run_automation_once

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(30, settings.automation_interval_seconds))
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            async with SessionLocal() as session:
                await run_automation_once(session, batch_size=settings.automation_batch_size)
        except Exception as e:  # noqa: BLE001
            logger.warning("scheduled_automation_failed", error=str(e))


async def _periodic_effect_checks(stop_event: asyncio.Event) -> None:
    """独立处理到期的效果检查；不领取策略执行任务，也不写外站。"""
    from app.services.automation_service import run_effect_checks_once

    while not stop_event.is_set():
        try:
            async with SessionLocal() as session:
                await run_effect_checks_once(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("scheduled_effect_check_failed", error=str(e))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=EFFECT_CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, settings.log_json, settings.log_file)
    logger.info("startup_begin", env=settings.app_env, port=settings.app_port)
    postgres_major = await require_postgresql_17(engine)
    logger.info("database_version_gate_passed", postgres_major=postgres_major)

    store = get_store()
    try:
        await store.load_from_db()
    except Exception as e:  # noqa: BLE001
        logger.warning("rule_store_initial_load_failed", error=str(e))

    # 启动时尝试加载 google-data-sources（失败也不影响启动）
    try:
        gstore = get_google_store()
        if gstore.load_error():
            logger.warning("google_config_load_error", error=gstore.load_error())
        else:
            logger.info("google_config_loaded", sources=len(gstore.list_all()))
    except Exception as e:  # noqa: BLE001
        logger.warning("google_config_init_failed", error=str(e))

    try:
        from app.services.automation_service import recover_interrupted_executions
        from app.services.keyword_ai_service import clear_unreviewed_assignments

        async with SessionLocal() as session:
            await clear_unreviewed_assignments(session)
            recovered = await recover_interrupted_executions(session)
        if recovered:
            logger.warning("interrupted_executions_recovered", count=recovered)
    except Exception as e:  # noqa: BLE001
        logger.warning("interrupted_execution_recovery_failed", error=str(e))

    stop_event = asyncio.Event()
    reload_task = asyncio.create_task(_periodic_reload(stop_event))
    google_sync_task = asyncio.create_task(_periodic_google_sync(stop_event))
    effect_check_task = asyncio.create_task(_periodic_effect_checks(stop_event))
    scheduled_automation_task = (
        asyncio.create_task(_periodic_scheduled_automation(stop_event))
        if settings.automation_enabled
        else None
    )
    app.state.reload_stop = stop_event
    app.state.reload_task = reload_task
    app.state.google_sync_task = google_sync_task
    app.state.effect_check_task = effect_check_task
    app.state.scheduled_automation_task = scheduled_automation_task

    logger.info("startup_done", rule_version=store.version)
    try:
        yield
    finally:
        logger.info("shutdown_begin")
        stop_event.set()
        for task in (reload_task, google_sync_task, effect_check_task, scheduled_automation_task):
            if task is None:
                continue
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.TimeoutError:
                task.cancel()
        logger.info("shutdown_done")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="2.0.0",
        lifespan=lifespan,
    )

    if settings.app_env != "prod":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
            allow_origin_regex=r"chrome-extension://[a-p]{32}",
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(MetricsMiddleware)
    app.add_middleware(TraceIdMiddleware)

    install_exception_handlers(app)

    # 路由（避免循环导入，放在函数体内）
    from app.api.admin.manage import router as admin_router
    from app.api.v1.analytics import router as v1_analytics_router
    from app.api.v1.business import router as v1_business_router
    from app.api.v1.connectors import router as v1_connectors_router
    from app.api.v1.endpoints import router as v1_router
    from app.api.v1.social import router as v1_social_router
    from app.api.v1.site_capabilities import router as v1_site_capabilities_router
    from app.api.v1.strategy_actions import router as v1_strategy_actions_router
    from app.api.v1.strategy_runs import router as v1_strategy_runs_router

    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(v1_business_router, prefix="/api/v1")
    app.include_router(v1_connectors_router, prefix="/api/v1")
    app.include_router(v1_social_router, prefix="/api/v1")
    app.include_router(v1_analytics_router, prefix="/api/v1")
    app.include_router(v1_site_capabilities_router, prefix="/api/v1")
    app.include_router(v1_strategy_actions_router, prefix="/api/v1")
    app.include_router(v1_strategy_runs_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/admin")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": "true",
            "name": settings.app_name,
            "env": settings.app_env,
            **runtime_health(runtime_identity),
        }

    @app.get("/metrics")
    async def metrics():
        return metrics_response()

    return app


app = create_app()
