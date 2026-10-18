"""Request logging middleware.

Gives every request an id (taken from ``X-Request-ID`` or generated), returns it
in the response header, and writes one log line per request with method, path,
status and duration. Service logs written during the request carry the same id.
"""

import logging
import time
import uuid

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from src.observability.logging import request_id_var

logger = logging.getLogger("talentiq.request")

REQUEST_ID_HEADER = "X-Request-ID"
_QUIET_PATHS = {"/api/v1/health"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Assigns a request id and logs each request once it finishes."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        if request.url.path not in _QUIET_PATHS:
            level = logging.WARNING if response.status_code >= 500 else logging.INFO
            logger.log(
                level,
                "%s %s -> %s (%.0f ms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 1),
                    "client": client_ip(request),
                },
            )
        return response


def client_ip(request: Request) -> str:
    """Returns the caller's IP, looking through the frontend's nginx proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def register_logging_middleware(app: FastAPI) -> None:
    """Adds the request logging middleware to the app."""
    app.add_middleware(RequestLoggingMiddleware)
