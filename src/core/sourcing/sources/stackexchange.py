"""Stack Exchange: top answerers of a tag (e.g. "python") with their public profiles.

Profiles are turned into resume-like documents. 300 requests/day without a key,
10,000 with STACKEXCHANGE_KEY. Content is CC BY-SA, so the profile link is kept.
"""

import gzip
import json
import urllib.parse

from src.config.settings import settings
from src.core.sourcing.documents import html_text_and_links, text_to_pdf
from src.core.sourcing.fetcher import FetchError
from src.core.sourcing.sources.base import SourcedFile, SourceResult, fetcher

API = "https://api.stackexchange.com/2.3"
_filter_id: str | None = None


async def _api(path: str, **params) -> dict:
    if settings.stackexchange_key:
        params["key"] = settings.stackexchange_key
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    result = await fetcher.get(url, check_robots=False, max_bytes=5_000_000)
    data = result.data
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return json.loads(data)


async def _about_me_filter() -> str:
    """The default filter leaves out about_me; create (once) a filter that includes it."""
    global _filter_id
    if _filter_id is None:
        created = await _api("/filters/create", include="user.about_me", base="default", unsafe="false")
        _filter_id = created["items"][0]["filter"]
    return _filter_id


async def collect(tag: str, site: str, max_results: int) -> SourceResult:
    result = SourceResult()
    try:
        top = await _api(f"/tags/{urllib.parse.quote(tag)}/top-answerers/all_time",
                         site=site, pagesize=max_results)
        entries = top.get("items") or []
        if not entries:
            result.errors.append(f"No users found for tag '{tag}' on {site}.")
            return result
        ids = ";".join(str(e["user"]["user_id"]) for e in entries)
        users = await _api(f"/users/{ids}", site=site, filter=await _about_me_filter(),
                           pagesize=max_results)
    except (FetchError, KeyError, ValueError) as exc:
        result.errors.append(f"Stack Exchange request failed: {exc}")
        result.partial = True
        return result

    if users.get("quota_remaining", 100) < 5:
        result.partial = True
        result.errors.append("Stack Exchange daily quota nearly used; add STACKEXCHANGE_KEY.")
    scores = {e["user"]["user_id"]: e for e in entries}
    for user in users.get("items") or []:
        about = html_text_and_links(user.get("about_me") or "")[0]
        answered = scores.get(user["user_id"], {})
        body = "\n".join(filter(None, [
            f"Location: {user.get('location')}" if user.get("location") else None,
            f"Website: {user.get('website_url')}" if user.get("website_url") else None,
            f"Profile: {user.get('link')}",
            "", "SUMMARY",
            f"Top answerer on {site} for '{tag}' ({answered.get('post_count', '?')} answers, "
            f"score {answered.get('score', '?')}); reputation {user.get('reputation')}.",
            "", "SKILLS", tag,
            "", "ABOUT", about[:3000] or "not provided",
        ]))
        pdf = text_to_pdf(
            user.get("display_name") or f"User {user['user_id']}", body,
            footer=f"Generated from the public Stack Exchange profile {user.get('link')} (CC BY-SA); "
                   "not a resume written by the candidate.")
        result.files.append(SourcedFile(f"stackexchange_{site}_{user['user_id']}.pdf", pdf,
                                        "stackexchange", user.get("link") or str(user["user_id"])))
    return result
