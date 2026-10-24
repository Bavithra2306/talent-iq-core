"""Public resumes found with Google's official Programmable Search API.

You give a search such as "java developer chennai"; Google returns the addresses of public pages and files that look
like resumes (PDF or Word). Each address is then opened politely by the shared fetcher, the same way the "Your own
links" source does it: robots.txt is honoured and each site is paced. No sign-in is used anywhere.

Needs two free settings: GOOGLE_CSE_API_KEY (an API key) and GOOGLE_CSE_CX (the search engine id). Google allows
about 100 searches a day for free; one search page holds 10 results, so a run uses at most three searches.
"""

import json
import urllib.parse

from src.config.settings import settings
from src.handlers.http_clients.sourcing.fetcher import FetchError
from src.handlers.http_clients.sourcing.sources import url as url_source
from src.handlers.http_clients.sourcing.sources.base import SourceResult, fetcher

API = "https://www.googleapis.com/customsearch/v1"
PAGE_SIZE = 10
MAX_RESULTS = 30  # three search pages
# Sites whose terms forbid automatic collection are left out of the search.
_EXCLUDED = "-site:linkedin.com -site:naukri.com -site:indeed.com -site:glassdoor.com -site:monster.com"


def configured() -> bool:
    return bool(settings.google_cse_api_key and settings.google_cse_cx)


def search_text(query: str) -> str:
    """The Google query: the person's words, restricted to resume-like PDF and Word files."""
    return f'{query.strip()} (resume OR CV OR "curriculum vitae") (filetype:pdf OR filetype:docx) {_EXCLUDED}'


async def _links(query: str, max_results: int, result: SourceResult) -> list[str]:
    links: list[str] = []
    for start in range(1, min(max_results, MAX_RESULTS) + 1, PAGE_SIZE):
        params = {"key": settings.google_cse_api_key, "cx": settings.google_cse_cx, "q": search_text(query),
                  "num": min(PAGE_SIZE, max_results - start + 1), "start": start}
        try:
            page = await fetcher.get(f"{API}?{urllib.parse.urlencode(params)}", check_robots=False, max_bytes=2_000_000)
            data = json.loads(page.data)
        except (FetchError, ValueError) as exc:
            result.errors.append(f"Google search failed: {exc}")
            result.partial = True
            break
        items = data.get("items") or []
        links.extend(item["link"] for item in items if item.get("link"))
        if len(items) < PAGE_SIZE:
            break
    return list(dict.fromkeys(links))  # no repeats, same order


async def collect(query: str, max_results: int) -> SourceResult:
    result = SourceResult()
    if not configured():
        result.errors.append("Google search is not set up yet: add GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX.")
        return result
    links = await _links(query, max_results, result)
    if not links:
        if not result.errors:
            result.note = "Google found no public resume files for that search. Try different words."
        return result
    fetched = await url_source.collect(links[:max_results], follow_links=False)
    for file in fetched.files:
        file.source_site = "google"
    result.files.extend(fetched.files)
    result.errors.extend(fetched.errors)
    result.partial = result.partial or fetched.partial
    return result
