"""GitHub: search public user profiles through the REST API.

If a profile links a resume/CV PDF it is downloaded (robots.txt honoured); otherwise a
resume-like document is generated from the public profile, repositories and profile
README. Without a token GitHub allows 10 searches/min and 60 requests/hour.
"""

import json
import re
import urllib.parse

from src.config.settings import settings
from src.handlers.http_clients.sourcing.fetcher import FetchError
from src.handlers.http_clients.sourcing.sources.base import (
    SourcedFile,
    SourceResult,
    fetcher,
    is_resume_file,
)
from src.utils.documents import html_text_and_links, text_to_pdf

API = "https://api.github.com"
_RESUME_LINK = re.compile(r"(resume|cv|curriculum)", re.I)


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


async def _api(path: str) -> tuple[dict | list, int]:
    result = await fetcher.get(API + path, headers=_headers(), check_robots=False, max_bytes=5_000_000)
    remaining = int(result.headers.get("X-RateLimit-Remaining", "1") or 1)
    return json.loads(result.data), remaining


def _resume_links(readme: str, blog: str | None) -> list[str]:
    links = re.findall(r"https?://[^\s)\"'<>\]]+", readme or "")
    if blog:
        links.insert(0, blog if blog.startswith("http") else "https://" + blog)
    found = []
    for link in links:
        path = urllib.parse.urlparse(link).path.lower()
        if path.endswith(".pdf") and (_RESUME_LINK.search(link) or len(links) == 1):
            found.append(link)
    return found[:2]


async def collect(query: str, max_results: int) -> SourceResult:
    result = SourceResult()
    if "type:" not in query:
        query = f"{query} type:user"  # people only, never company / organisation accounts
    users: list[dict] = []
    remaining = 1
    try:
        for page in range(1, (max_results - 1) // 100 + 2):
            search, remaining = await _api(
                f"/search/users?q={urllib.parse.quote(query)}&per_page={min(100, max_results)}&page={page}")
            items = search.get("items") or []
            users += items
            if len(items) < 100 or len(users) >= max_results:
                break
    except FetchError as exc:
        if not users:
            result.errors.append(f"GitHub search failed: {exc}")
            result.partial = True
            return result

    for user in users[:max_results]:
        login = user["login"]
        if remaining < 3:
            result.partial = True
            result.errors.append("GitHub rate limit reached; add GITHUB_TOKEN for more requests.")
            break
        try:
            profile, remaining = await _api(f"/users/{login}")
            repos, remaining = await _api(f"/users/{login}/repos?sort=updated&per_page=10")
        except FetchError as exc:
            result.errors.append(f"{login}: {exc}")
            result.partial = True
            continue

        readme = ""
        try:
            raw = await fetcher.get(f"https://raw.githubusercontent.com/{login}/{login}/HEAD/README.md",
                                    check_robots=False, max_bytes=300_000)
            readme = raw.data.decode("utf-8", "replace")
        except FetchError:
            pass  # most users have no profile README

        # A real resume linked from the profile beats a generated one.
        linked = None
        for link in _resume_links(readme, profile.get("blog")):
            try:
                fetched = await fetcher.get(link)
                name = f"github_{login}_resume.pdf"
                if is_resume_file(fetched.data, name):
                    linked = SourcedFile(name, fetched.data, "github", link)
                    break
            except FetchError as exc:
                result.errors.append(f"{login}: resume link skipped ({exc})")
        if linked:
            result.files.append(linked)
            continue

        languages, topics, projects = [], [], []
        for repo in repos if isinstance(repos, list) else []:
            if repo.get("fork"):
                continue
            if repo.get("language") and repo["language"] not in languages:
                languages.append(repo["language"])
            topics += [t for t in repo.get("topics") or [] if t not in topics]
            desc = (repo.get("description") or "").strip()
            projects.append(f"- {repo['name']}: {desc} ({repo.get('language') or 'n/a'}, "
                            f"{repo.get('stargazers_count', 0)} stars)")
        readme_text = html_text_and_links(readme)[0] if "<" in readme else readme
        body = "\n".join(filter(None, [
            f"Location: {profile.get('location')}" if profile.get("location") else None,
            f"Email: {profile.get('email')}" if profile.get("email") else None,
            f"Website: {profile.get('blog')}" if profile.get("blog") else None,
            f"GitHub: {profile.get('html_url')}",
            "", "SUMMARY", profile.get("bio") or "Software developer with public projects on GitHub.",
            f"Current company: {profile.get('company')}" if profile.get("company") else None,
            "", "SKILLS", ", ".join(languages + topics[:15]) or "not listed",
            "", "PROJECTS", *projects[:10],
            "", "PROFILE README", readme_text[:3000] if readme_text else "none",
        ]))
        pdf = text_to_pdf(
            profile.get("name") or login, body,
            footer=f"Generated from the public GitHub profile {profile.get('html_url')}; "
                   "not a resume written by the candidate.")
        result.files.append(SourcedFile(f"github_{login}.pdf", pdf, "github", profile.get("html_url") or login))
    return result
