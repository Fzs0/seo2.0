"""Prometheus 指标埋点中间件。

暴露：
- http_requests_total{method, endpoint, status}
- http_request_duration_seconds{method, endpoint} (Histogram)
- business_decision_total{decision}
- external_call_duration_seconds{client}
"""
import time
from collections.abc import Awaitable, Callable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, Response as StarletteResponse

registry = CollectorRegistry()

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
    registry=registry,
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    registry=registry,
)
business_decision_total = Counter(
    "business_decision_total",
    "Business decisions made (e.g. new_article / update_article / hold)",
    ["decision"],
    registry=registry,
)
external_call_duration_seconds = Histogram(
    "external_call_duration_seconds",
    "External HTTP call latency in seconds",
    ["client"],
    registry=registry,
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录每次请求的 method/endpoint/status 与耗时。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = time.perf_counter()
        method = request.method
        endpoint = request.url.path
        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            elapsed = time.perf_counter() - start
            http_requests_total.labels(method=method, endpoint=endpoint, status=status).inc()
            http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(elapsed)
        return response


def metrics_response() -> StarletteResponse:
    return StarletteResponse(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)