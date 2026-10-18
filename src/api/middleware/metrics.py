"""Metrics middleware: records count and latency for every request.

Requests are grouped by route template (``/api/v1/jobs/{job_id}``), not by the
concrete URL, so ids do not create one entry per record.
"""

import time

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.routing import Match

from src.observability.metrics import metrics


def _route_template(request: Request) -> str:
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return str(getattr(route, "path", request.url.path))
    return "unmatched"


class MetricsMiddleware(BaseHTTPMiddleware):
    """Times each request and stores the result in the metrics registry."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            metrics.record(f"{request.method} {_route_template(request)}", status_code, duration_ms)


def register_metrics_middleware(app: FastAPI) -> None:
    """Adds the metrics middleware to the app."""
    app.add_middleware(MetricsMiddleware)
