"""LLM access through OpenRouter's OpenAI-compatible API.

chat_json() asks for JSON, validates it against a Pydantic model and retries on
rate limits, server errors, timeouts and invalid output. OpenRouter itself
falls through `llm_fallback_models` when the primary model is unavailable.
"""

import asyncio
import json
import logging
import random
import re
from typing import TypeVar

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from src.config.settings import settings

logger = logging.getLogger("talentiq.llm")

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The LLM could not produce a valid answer after all retries."""


_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output.")
    return json.loads(text[start : end + 1])


class LLMClient:
    def __init__(self):
        self._client: AsyncOpenAI | None = None
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrency)

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            if not settings.llm_api_key:
                raise LLMError("OPENROUTER_API_KEY is not configured.")
            self._client = AsyncOpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,  # retries handled below
            )
        return self._client

    def _extra_body(self) -> dict:
        body: dict = {"models": [settings.llm_model, *settings.llm_fallback_models]}
        if settings.llm_provider_order:
            body["provider"] = {
                "order": settings.llm_provider_order,
                "allow_fallbacks": True,
            }
        return body

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        response_model: type[T],
        max_tokens: int = 2000,
        attempts: int = 4,
    ) -> T:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                async with self._semaphore:
                    response = await self.client.chat.completions.create(
                        model=settings.llm_model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0,
                        max_tokens=max_tokens,
                        extra_body=self._extra_body(),
                    )
                content = response.choices[0].message.content
                result = response_model.model_validate(_extract_json(content))
                logger.info("llm ok model=%s attempt=%d", response.model, attempt)
                return result
            except (ValueError, ValidationError) as exc:
                # Bad JSON or wrong shape: ask again.
                last_error = exc
                logger.warning("llm invalid output attempt=%d: %s", attempt, exc)
            except _RETRYABLE as exc:
                last_error = exc
                logger.warning("llm retryable error attempt=%d: %s", attempt, exc)
            except openai.APIStatusError as exc:
                # 4xx other than 429 (bad key, no credits, bad request) won't fix itself.
                raise LLMError(f"LLM request failed ({exc.status_code}).") from exc

            if attempt < attempts:
                await asyncio.sleep(min(2 ** attempt, 20) + random.random())

        raise LLMError("The LLM did not return a valid answer.") from last_error


llm = LLMClient()
