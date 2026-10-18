"""Per-client rate limiting (in memory, sliding one-minute window).

Protects the service and the free LLM quotas from runaway clients. Heavy
endpoints (uploads, matching runs, sourcing, LLM calls) get a lower limit than
normal requests. Limits come from settings (``RATE_LIMIT_PER_MINUTE`` and
``RATE_LIMIT_HEAVY_PER_MINUTE``); 0 turns a limit off.
"""

import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from src.api.middleware.logging import client_ip
from src.config.settings import settings

WINDOW_SECONDS = 60.0
_EXEMPT_PATHS = {"/api/v1/health"}
# POST endpoints that start expensive work (LLM calls, parsing, outbound fetches).
_HEAVY_SUFFIXES = ("/upload", "/extract", "/match", "/nl-filter", "/sourcing/runs")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rejects requests over the per-minute limit with HTTP 429."""

    def __init__(self, app: ASGIApp, per_minute: int, heavy_per_minute: int) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self.heavy_per_minute = heavy_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _allow(self, key: str, limit: int) -> tuple[bool, int]:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            return False, int(WINDOW_SECONDS - (now - hits[0])) + 1
        hits.append(now)
        return True, 0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in _EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        ip = client_ip(request)
        checks = [(f"all:{ip}", self.per_minute)]
        if request.method == "POST" and path.endswith(_HEAVY_SUFFIXES):
            checks.append((f"heavy:{ip}", self.heavy_per_minute))
        for key, limit in checks:
            if limit <= 0:
                continue
            allowed, retry_after = self._allow(key, limit)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many requests. Please wait a moment and try again.",
                        "code": "rate_limited",
                    },
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)


def register_rate_limit_middleware(app: FastAPI) -> None:
    """Adds the rate limiter using limits from settings."""
    app.add_middleware(
        RateLimitMiddleware,
        per_minute=settings.rate_limit_per_minute,
        heavy_per_minute=settings.rate_limit_heavy_per_minute,
    )
