"""Polite, safe HTTP fetching for resume sourcing.

- Only http(s) to public addresses: private, loopback, link-local and reserved IPs are
  refused, also after redirects (so a pasted URL can't reach internal services).
- robots.txt is honoured for pages and files fetched from user-supplied sites.
- At most one request per host every `min_interval` seconds; size and time limits.
"""

import asyncio
import ipaddress
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass

from src.config.settings import settings

logger = logging.getLogger("talentiq.sourcing")


class FetchError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.retryable, self.retry_after = retryable, retry_after


# Portal unreachable, timed out, rate-limited (429) or a server error (5xx): try again.
RETRY_DELAYS = (2.0, 5.0)  # seconds before the 2nd and 3rd attempt
MAX_RETRY_AFTER = 30.0


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    data: bytes
    headers: dict


def _check_public(url: str) -> None:
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Only http(s) links are allowed.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve {parts.hostname}.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise FetchError(f"{parts.hostname} points to a private or internal address.")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public(newurl)  # re-validate every hop
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


# Hosts that publish rate limits for API use get shorter gaps (GitHub only with a token).
_HOST_INTERVALS = {
    "api.stackexchange.com": 0.1,  # limit: 30 requests a second
    "datasets-server.huggingface.co": 0.3,
}
_GITHUB_HOSTS = {"api.github.com": 0.25, "raw.githubusercontent.com": 0.25}  # 5,000 an hour with a token


class PoliteFetcher:
    def __init__(self, min_interval: float = 2.0):
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = asyncio.Lock()

    async def _wait_turn(self, host: str) -> None:
        async with self._lock:
            interval = _HOST_INTERVALS.get(host, self.min_interval)
            if settings.github_token and host in _GITHUB_HOSTS:
                interval = _GITHUB_HOSTS[host]
            wait = self._last.get(host, 0) + interval - time.monotonic()
            self._last[host] = time.monotonic() + max(wait, 0)
        if wait > 0:
            await asyncio.sleep(wait)

    def _robots_allows(self, url: str) -> bool:
        parts = urllib.parse.urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                _check_public(base + "/robots.txt")
                request = urllib.request.Request(base + "/robots.txt",
                                                 headers={"User-Agent": settings.sourcing_user_agent})
                with _opener.open(request, timeout=10) as response:
                    parser.parse(response.read(200_000).decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as exc:
                # 4xx: no robots.txt -> allowed; 5xx: be careful -> disallow
                parser = None if exc.code < 500 else False
            except Exception:
                parser = None
            self._robots[base] = parser
        parser = self._robots[base]
        if parser is False:
            return False
        return parser is None or parser.can_fetch(settings.sourcing_user_agent, url)

    def _get(self, url: str, headers: dict, max_bytes: int, check_robots: bool) -> FetchResult:
        _check_public(url)
        if check_robots and not self._robots_allows(url):
            raise FetchError("robots.txt does not allow fetching this page.")
        request = urllib.request.Request(url, headers={"User-Agent": settings.sourcing_user_agent, **headers})
        try:
            with _opener.open(request, timeout=settings.sourcing_timeout_seconds) as response:
                length = int(response.headers.get("Content-Length") or 0)
                if length > max_bytes:
                    raise FetchError(f"File is larger than {max_bytes // (1024 * 1024)} MB.")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise FetchError(f"File is larger than {max_bytes // (1024 * 1024)} MB.")
                return FetchResult(response.geturl(), response.status,
                                   (response.headers.get("Content-Type") or "").split(";")[0].strip().lower(),
                                   data, dict(response.headers))
        except urllib.error.HTTPError as exc:
            host = urllib.parse.urlparse(url).netloc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            message = {429: f"{host} is rate-limiting requests (HTTP 429).",
                       403: f"{host} blocked the request (HTTP 403)."}.get(exc.code, f"HTTP {exc.code} from {host}.")
            raise FetchError(message, retryable=exc.code == 429 or exc.code >= 500,
                             retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FetchError(f"Could not reach {urllib.parse.urlparse(url).netloc}: {getattr(exc, 'reason', exc)}",
                             retryable=True) from exc

    async def get(self, url: str, *, headers: dict | None = None, max_bytes: int | None = None,
                  check_robots: bool = True) -> FetchResult:
        host = urllib.parse.urlparse(url).netloc
        for attempt, delay in enumerate((*RETRY_DELAYS, None), start=1):
            await self._wait_turn(host)
            try:
                return await asyncio.to_thread(self._get, url, headers or {},
                                               max_bytes or settings.max_upload_bytes, check_robots)
            except FetchError as exc:
                if not exc.retryable or delay is None:
                    if attempt > 1:
                        raise FetchError(f"{exc} (tried {attempt} times)") from exc
                    raise
                wait = min(MAX_RETRY_AFTER, max(delay, exc.retry_after or 0))
                logger.warning("fetch failed, retrying", extra={"url": url, "attempt": attempt,
                                                                "wait_seconds": wait, "error": str(exc)})
                await asyncio.sleep(wait)
        raise RuntimeError("unreachable")
