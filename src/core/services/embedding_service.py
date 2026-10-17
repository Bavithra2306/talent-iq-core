"""Text embeddings (Gemini, 768 dims) for skills / roles, used for cosine similarity.

Only one embedding model is used on purpose: vectors from different models live in
different spaces and can't be compared. Each stored row records `model_version`.
"""

import asyncio
import logging
import math
import random

import openai
from openai import AsyncOpenAI

from src.config.settings import settings

logger = logging.getLogger("talentiq.embeddings")

_client: AsyncOpenAI | None = None
_semaphore = asyncio.Semaphore(2)


class EmbeddingError(Exception):
    pass


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
        except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
                openai.InternalServerError) as exc:
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
