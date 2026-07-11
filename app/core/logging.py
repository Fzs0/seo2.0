"""结构化日志（structlog）+ JSON 输出。所有日志含 trace_id 与模块名。"""
import logging
import sys
from typing import Any

import structlog
from structlog.contextvars import merge_contextvars


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """初始化全局日志配置。

    - json_output=True：JSON 行（含 timestamp / level / trace_id / module / event）
    - json_output=False：控制台彩色（仅 local 开发用）
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, stream=sys.stdout, format="%(message)s")

    shared_processors: list[Any] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.CallsiteParameterAdder(
            parameters=[structlog.processors.CallsiteParameter.MODULE],
        ),
    ]
    renderer: Any
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)