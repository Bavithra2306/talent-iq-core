"""LLM access across free-tier providers (Gemini, Groq, OpenRouter), all via OpenAI-compatible APIs.

A *lane* is one provider + model with its own requests-per-minute budget. For each
request the client picks the first lane (in priority order) that is not cooling down
and still has budget in the last 60 seconds. If a lane answers 429 / 5xx / times out,
it cools down and the request moves to the next lane. Invalid JSON is retried on the
next lane too. If every lane is busy the request waits for the soonest free slot.
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TypeVar

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from src.config.settings import settings

logger = logging.getLogger("talentiq.llm")

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """No lane could produce a valid answer."""


_TRANSIENT = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output.")
    return json.loads(text[start : end + 1])


@dataclass
class Lane:
    provider: str
    model: str
    client: AsyncOpenAI
    rpm: int
    extra_body: dict = field(default_factory=dict)
    cooldown_until: float = 0.0
    calls: deque = field(default_factory=deque)  # monotonic times of calls in the last 60s

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"

    def _trim(self, now: float) -> None:
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()

    def wait_time(self, now: float) -> float:
        """Seconds until this lane can take a request (0 = now)."""
        self._trim(now)
        wait = max(0.0, self.cooldown_until - now)
        if len(self.calls) >= self.rpm:
            wait = max(wait, 60 - (now - self.calls[0]))
        return wait

    def reserve(self, now: float) -> None:
        self.calls.append(now)


class LLMClient:
    def __init__(self):
        self._lanes: list[Lane] | None = None
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
        self._lock = asyncio.Lock()

    # ---- setup ----
    def _build_lanes(self) -> list[Lane]:
        providers = [
            ("gemini", settings.gemini_api_key, settings.gemini_base_url,
             settings.gemini_models, settings.gemini_rpm, {}),
            ("groq", settings.groq_api_key, settings.groq_base_url,
             settings.groq_models, settings.groq_rpm, {"reasoning_effort": "low"}),
            ("openrouter", settings.openrouter_api_key, settings.openrouter_base_url,
             settings.openrouter_models, settings.openrouter_rpm, {}),
        ]
        lanes = []
        for name, key, base_url, models, rpm, extra in providers:
            if not key:
                continue
            client = AsyncOpenAI(
                base_url=base_url, api_key=key,
                timeout=settings.llm_timeout_seconds, max_retries=0,
            )
            for model in models:
                lanes.append(Lane(name, model, client, rpm, extra_body=dict(extra)))
        if not lanes:
            raise LLMError("No LLM API key is configured.")
        logger.info("llm lanes: %s", [lane.name for lane in lanes])
        return lanes

    @property
    def lanes(self) -> list[Lane]:
        if self._lanes is None:
            self._lanes = self._build_lanes()
        return self._lanes

    def strong_lane(self) -> Lane | None:
        """Gemini lane for the stronger model used for explanations."""
        if not settings.gemini_api_key:
            return None
        base = next((l for l in self.lanes if l.provider == "gemini"), None)
        if base is None:
            return None
        for lane in self.lanes:
            if lane.name == f"gemini/{settings.llm_strong_model}":
                return lane
        lane = Lane("gemini", settings.llm_strong_model, base.client, 7)
        self.lanes.insert(len(self.lanes), lane)
        return lane

    # ---- lane choice ----
    async def _reserve_lane(self, candidates: list[Lane], deadline: float) -> Lane:
        while True:
            async with self._lock:
                now = time.monotonic()
                waits = [lane.wait_time(now) for lane in candidates]
                for lane, wait in zip(candidates, waits):
                    if wait == 0:  # first free lane in priority order
                        lane.reserve(now)
                        return lane
                soonest = min(waits)
            if time.monotonic() + soonest > deadline:
                raise LLMError("All LLM lanes are rate-limited; try again shortly.")
            await asyncio.sleep(min(max(soonest, 0.2), 2.0))

    # ---- main call ----
    async def chat_json(self, **kwargs) -> T:
        result, _ = await self.chat_json_with_model(**kwargs)
        return result

    async def chat_json_with_model(
        self,
        *,
        system: str,
        user: str,
        response_model: type[T],
        max_tokens: int = 2000,
        strong: bool = False,
        max_wait_seconds: float = 90.0,
    ) -> tuple[T, str]:
        """Like chat_json, but also returns the lane (provider/model) that answered."""
        deadline = time.monotonic() + max_wait_seconds
        candidates = list(self.lanes)
        if strong and (lane := self.strong_lane()):
            candidates = [lane] + [l for l in candidates if l is not lane]
        tried: set[str] = set()
        last_error: Exception | None = None

        for _ in range(len(candidates) * 2):
            pool = [l for l in candidates if l.name not in tried] or candidates
            lane = await self._reserve_lane(pool, deadline)
            try:
                async with self._semaphore:
                    response = await lane.client.chat.completions.create(
                        model=lane.model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        response_format={"type": "json_object"},
                        temperature=0,
                        max_tokens=max_tokens,
                        extra_body=lane.extra_body or None,
                    )
                result = response_model.model_validate(
                    extract_json(response.choices[0].message.content)
                )
                logger.info("llm ok lane=%s tokens=%s", lane.name,
                            getattr(response.usage, "total_tokens", "?"))
                return result, lane.name
            except (ValueError, ValidationError) as exc:
                last_error = exc
                tried.add(lane.name)
                logger.warning("llm invalid output lane=%s: %s", lane.name, str(exc)[:160])
            except _TRANSIENT as exc:
                last_error = exc
                tried.add(lane.name)
                lane.cooldown_until = time.monotonic() + settings.llm_lane_cooldown_seconds
                logger.warning("llm lane=%s cooling down: %s", lane.name, type(exc).__name__)
            except openai.APIStatusError as exc:
                # 400/401/402/403/404: this lane is misconfigured or out of quota; try another.
                last_error = exc
                tried.add(lane.name)
                lane.cooldown_until = time.monotonic() + 300
                logger.error("llm lane=%s failed (%s); disabled for 5 min", lane.name, exc.status_code)
            if time.monotonic() > deadline:
                break

        raise LLMError("The LLM did not return a valid answer.") from last_error


llm = LLMClient()
