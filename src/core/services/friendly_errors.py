"""Plain-language messages for failures that people can see (the activity log, upload results,
run status). The technical detail is not shown there: it stays in the server log, where the code
that catches the error writes it with ``logger.exception``.
"""

import asyncio

from sqlalchemy.exc import SQLAlchemyError

from src.core.exceptions.base import AppError
from src.handlers.http_clients.embedding_client import EmbeddingError
from src.handlers.http_clients.llm_client import LLMError
from src.handlers.http_clients.sourcing.fetcher import FetchError


def friendly_error(exc: BaseException, doing: str = "this step") -> str:
    """A short sentence a recruiter can understand: what went wrong and what to do next.

    ``doing`` completes "Something went wrong while ...", e.g. "scoring the candidates".
    """
    if isinstance(exc, (AppError, FetchError)):
        return str(exc)  # these messages were already written for people
    if isinstance(exc, LLMError):
        return "The AI service is busy or unavailable. Please try again in a minute."
    if isinstance(exc, EmbeddingError):
        return "The search service is busy. Please try again later."
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "This took too long and was stopped. Please try again."
    if isinstance(exc, SQLAlchemyError) or type(exc).__module__.startswith("asyncpg"):
        return "We couldn't save the results. Please try again."
    return f"Something went wrong while {doing}. Please try again. If it keeps happening, tell your admin."
