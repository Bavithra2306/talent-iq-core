"""Text embeddings (Gemini, 768 dims) for skills / roles, used for cosine similarity.

Only one embedding model is used on purpose: vectors from different models live in
different spaces and can't be compared. Each stored row records `model_version`.
"""

import asyncio
import logging
import math
import random
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import openai
from openai import AsyncOpenAI

from src.config.settings import settings

logger = logging.getLogger("talentiq.embeddings")

_client: AsyncOpenAI | None = None
_semaphore = asyncio.Semaphore(2)

# Gemini's free daily quota: every text counts. Google's retry hints show it resetting
# at 00:00 UTC (the AI Studio page says midnight Pacific); the zone is configurable.
QUOTA_TZ = ZoneInfo(settings.embedding_quota_timezone)
DISPLAY_TZ = ZoneInfo(settings.display_timezone)
_paused_until: datetime | None = None  # set when Google says the daily quota is used up
_sent: dict[date, int] = {}  # texts sent per quota day (in this process)
_budget_lock = asyncio.Lock()


class EmbeddingError(Exception):
    pass


class EmbeddingQuotaExceeded(EmbeddingError):
    """The daily embedding quota is used up; work resumes at `resume_at`."""

    def __init__(self, resume_at: datetime):
        self.resume_at = resume_at
        super().__init__(f"Daily embedding quota reached; resumes after {format_time(resume_at)}.")


# ---------- daily budget ----------

def _quota_today() -> date:
    return datetime.now(QUOTA_TZ).date()


def next_reset() -> datetime:
    """Next quota reset (midnight in the quota time zone), as a UTC datetime."""
    tomorrow = _quota_today() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0), QUOTA_TZ).astimezone(UTC)


def format_time(moment: datetime) -> str:
    return moment.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M %Z")


async def _used_today_from_db() -> int:
    """Texts embedded today according to the database (survives restarts)."""
    from src.data.clients.postgres import AsyncSessionLocal
    from src.data.repositories import job_embedding_repository, resume_repository

    start = datetime.combine(_quota_today(), time(0), QUOTA_TZ)
    async with AsyncSessionLocal() as db:
        resumes = await resume_repository.count_embeddings_since(db, start)
        jobs = await job_embedding_repository.count_since(db, start)
    return resumes + 3 * jobs  # a job embeds up to three texts


async def quota_status() -> dict:
    used = max(_sent.get(_quota_today(), 0), await _used_today_from_db())
    paused = _paused_until is not None and datetime.now(UTC) < _paused_until
    return {
        "daily_limit": settings.embedding_daily_limit,
        "used_today": used,
        "remaining_today": 0 if paused else max(0, settings.embedding_daily_limit - used),
        "paused": paused,
        "resumes_at": format_time(_paused_until if paused else next_reset()),
    }


async def _reserve(count: int) -> None:
    """Raise EmbeddingQuotaExceeded instead of sending a request that can't succeed today."""
    global _paused_until
    async with _budget_lock:
        now = datetime.now(UTC)
        if _paused_until is not None and now < _paused_until:
            raise EmbeddingQuotaExceeded(_paused_until)
        today = _quota_today()
        if today not in _sent:
            _sent.clear()
            _sent[today] = await _used_today_from_db()
        if _sent[today] + count > settings.embedding_daily_limit:
            _paused_until = next_reset()
            raise EmbeddingQuotaExceeded(_paused_until)
        _sent[today] += count


def _retry_delay_seconds(message: str) -> float | None:
    """Parse Google's 'Please retry in 6h11m51.2s' hint."""
    match = re.search(r"retry in\s*(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?", message)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def model_version() -> str:
    return f"{settings.embedding_model}@{settings.embedding_dimensions}"


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise EmbeddingError("GOOGLE_GEMINI_API_KEY is not configured.")
        _client = AsyncOpenAI(
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )
    return _client


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


async def _embed_batch(texts: list[str], attempts: int = 4) -> list[list[float]]:
    global _paused_until
    await _reserve(len(texts))
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with _semaphore:
                response = await _get_client().embeddings.create(
                    model=settings.embedding_model,
                    input=texts,
                    dimensions=settings.embedding_dimensions,
                )
            # Results come back in input order (Gemini leaves `index` empty).
            vectors = [item.embedding for item in response.data]
            if len(vectors) != len(texts) or any(
                len(v) != settings.embedding_dimensions for v in vectors
            ):
                raise EmbeddingError("Unexpected embedding response shape.")
            return [_normalize(v) for v in vectors]
        except openai.RateLimitError as exc:
            delay = _retry_delay_seconds(str(exc))
            if delay is not None and delay > 120:
                # A daily quota, not a per-minute one: retrying now only wastes time.
                _paused_until = datetime.now(UTC) + timedelta(seconds=delay)
                logger.warning("embedding daily quota reached; paused until %s", format_time(_paused_until))
                raise EmbeddingQuotaExceeded(_paused_until) from exc
            last = exc
            logger.warning("embedding attempt %d rate-limited", attempt)
            await asyncio.sleep(min(4 * attempt, 20) + random.random())
        except (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as exc:
            last = exc
            logger.warning("embedding attempt %d failed: %s", attempt, type(exc).__name__)
            await asyncio.sleep(min(4 * attempt, 20) + random.random())
        except openai.APIStatusError as exc:
            raise EmbeddingError(f"Embedding request failed ({exc.status_code}).") from exc
    raise EmbeddingError("Embedding service is busy; try again shortly.") from last


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed many texts (batched). Empty strings are not allowed by the API, so callers filter them."""
    if not texts:
        return []
    size = settings.embedding_batch_size
    batches = [texts[i : i + size] for i in range(0, len(texts), size)]
    results = await asyncio.gather(*[_embed_batch(batch) for batch in batches])
    return [vector for batch in results for vector in batch]


async def embed_text(text: str) -> list[float]:
    return (await embed_texts([text]))[0]
