"""Turns exceptions into consistent JSON error responses.

Every error body has the same shape: ``{"detail": ..., "code": ..., "request_id": ...}``.
Expected errors (``AppError``) keep their message; unexpected ones are logged with
the traceback and the client only sees a generic message plus the request id,
which can be searched for in the server logs.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.exceptions import AppError
from src.observability.logging import request_id_var

logger = logging.getLogger("talentiq.errors")


def _body(detail: object, code: str) -> dict[str, object]:
    body: dict[str, object] = {"detail": detail, "code": code}
    request_id = request_id_var.get()
    if request_id:
        body["request_id"] = request_id
    return body


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Expected business errors raised by services."""
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.warning("%s %s: %s", request.method, request.url.path, exc.message)
    return JSONResponse(status_code=exc.status_code, content=_body(exc.message, exc.code))


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """``HTTPException`` raised by routes and dependencies (auth, roles)."""
    assert isinstance(exc, StarletteHTTPException)
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(exc.detail, f"http_{exc.status_code}"),
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Request body / query validation errors (keeps FastAPI's field list)."""
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(status_code=422, content=_body(exc.errors(), "validation_error"))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Anything unexpected: log the traceback, hide internals from the client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=_body("Something went wrong on our side. Please try again.", "internal_error"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Registers all exception handlers on the app."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
