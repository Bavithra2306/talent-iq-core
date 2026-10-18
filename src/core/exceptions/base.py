"""Application exception hierarchy (one class per HTTP outcome)."""


class AppError(Exception):
    """Base class for expected, user-facing errors.

    Attributes:
        status_code: HTTP status returned to the client.
        code: Stable machine-readable error code.
        message: Plain-language message shown to the user.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BadRequestError(AppError):
    """The request is malformed (e.g. an empty file)."""

    status_code = 400
    code = "bad_request"


class NotFoundError(AppError):
    """The requested record does not exist."""

    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    """The record is in a state that does not allow this action."""

    status_code = 409
    code = "conflict"


class FileTooLargeError(AppError):
    """An uploaded file is over the size limit."""

    status_code = 413
    code = "file_too_large"


class UnsupportedFileError(AppError):
    """An uploaded file has a type we cannot read."""

    status_code = 415
    code = "unsupported_file"


class UnprocessableError(AppError):
    """The input is well-formed but cannot be used (e.g. invalid weights)."""

    status_code = 422
    code = "unprocessable"


class ServiceUnavailableError(AppError):
    """A dependency (LLM, embeddings, auth service) is unavailable."""

    status_code = 503
    code = "service_unavailable"
