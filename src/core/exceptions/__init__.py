"""Domain errors raised by services.

Services raise these instead of FastAPI's ``HTTPException`` so business logic
stays independent of the web layer. The error-handler middleware turns each one
into a JSON response ``{"detail": message, "code": code}`` with ``status_code``.
"""

from src.core.exceptions.base import (
    AppError,
    BadRequestError,
    ConflictError,
    FileTooLargeError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
    UnsupportedFileError,
)

__all__ = [
    "AppError",
    "BadRequestError",
    "ConflictError",
    "FileTooLargeError",
    "NotFoundError",
    "ServiceUnavailableError",
    "UnprocessableError",
    "UnsupportedFileError",
]
