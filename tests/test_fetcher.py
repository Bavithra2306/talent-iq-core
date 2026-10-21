"""BRD case 7: a portal that is unreachable or rate-limited is retried before failing."""

import pytest

from src.handlers.http_clients.sourcing import fetcher as fetcher_module
from src.handlers.http_clients.sourcing.fetcher import FetchError, FetchResult, PoliteFetcher


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(fetcher_module.asyncio, "sleep", instant)


def _fetcher(outcomes):
    f = PoliteFetcher(min_interval=0)
    calls = []

    def fake_get(url, headers, max_bytes, check_robots):
        calls.append(url)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    f._get = fake_get
    return f, calls


async def test_retries_then_succeeds():
    ok = FetchResult("https://x.test", 200, "text/html", b"ok", {})
    f, calls = _fetcher([FetchError("down", retryable=True), FetchError("429", retryable=True), ok])
    assert (await f.get("https://x.test")).data == b"ok"
    assert len(calls) == 3


async def test_gives_up_after_three_attempts_with_a_clear_message():
    f, calls = _fetcher([FetchError("Could not reach x.test", retryable=True)] * 3)
    with pytest.raises(FetchError, match="tried 3 times"):
        await f.get("https://x.test")
    assert len(calls) == 3


async def test_does_not_retry_errors_that_will_not_change():
    f, calls = _fetcher([FetchError("robots.txt does not allow fetching this page.")])
    with pytest.raises(FetchError):
        await f.get("https://x.test")
    assert len(calls) == 1
