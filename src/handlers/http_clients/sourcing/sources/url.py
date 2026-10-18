"""User-supplied links: resume files directly, or pages (portfolios) that link to them.

A PDF/DOCX link is downloaded as is. For a web page, links on the same site that look
like a resume/CV file are followed; if there are none, the page text itself is used
(e.g. a personal portfolio). robots.txt is honoured and each site is paced.
"""

import re
import urllib.parse
from pathlib import PurePosixPath

from src.handlers.http_clients.sourcing.fetcher import FetchError
from src.handlers.http_clients.sourcing.sources.base import (
    DOCX_MAGIC,
    PDF_MAGIC,
    SourcedFile,
    SourceResult,
    fetcher,
)
from src.utils.documents import html_text_and_links, text_to_pdf

_RESUME_HINT = re.compile(r"(resume|résumé|cv|curriculum)", re.I)
MAX_LINKS_PER_PAGE = 5


def _file_kind(data: bytes, content_type: str, url: str) -> str | None:
    path = urllib.parse.urlparse(url).path.lower()
    if data.startswith(PDF_MAGIC):
        return "pdf"
    if data.startswith(DOCX_MAGIC) and (path.endswith(".docx") or "wordprocessingml" in content_type):
        return "docx"
    return None


def _filename(url: str, kind: str) -> str:
    stem = PurePosixPath(urllib.parse.urlparse(url).path).stem or "resume"
    host = urllib.parse.urlparse(url).hostname or "site"
    return f"url_{host}_{stem}"[:120] + f".{kind}"


async def collect(urls: list[str], follow_links: bool = True) -> SourceResult:
    result = SourceResult()
    for url in urls:
        try:
            page = await fetcher.get(url)
        except FetchError as exc:
            result.errors.append(f"{url}: {exc}")
            result.partial = True
            continue

        kind = _file_kind(page.data, page.content_type, page.url)
        if kind:
            result.files.append(SourcedFile(_filename(page.url, kind), page.data, "url", page.url))
            continue
        if "html" not in page.content_type:
            result.errors.append(f"{url}: not a PDF, DOCX or web page ({page.content_type or 'unknown type'}).")
            continue

        html = page.data.decode("utf-8", "replace")
        text, links = html_text_and_links(html)
        found = 0
        if follow_links:
            host = urllib.parse.urlparse(page.url).hostname
            for href, anchor in links:
                target = urllib.parse.urljoin(page.url, href)
                path = urllib.parse.urlparse(target).path.lower()
                if urllib.parse.urlparse(target).hostname != host or not path.endswith((".pdf", ".docx")):
                    continue
                if not (_RESUME_HINT.search(target) or _RESUME_HINT.search(anchor)):
                    continue
                try:
                    linked = await fetcher.get(target)
                except FetchError as exc:
                    result.errors.append(f"{target}: {exc}")
                    continue
                linked_kind = _file_kind(linked.data, linked.content_type, linked.url)
                if linked_kind:
                    result.files.append(SourcedFile(_filename(linked.url, linked_kind), linked.data, "url", linked.url))
                    found += 1
                if found >= MAX_LINKS_PER_PAGE:
                    break
        if found == 0:
            if len(text) < 300:
                result.errors.append(f"{url}: no resume file or enough text on the page.")
                continue
            title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            pdf = text_to_pdf((title.group(1).strip() if title else page.url)[:120], text[:20000],
                              footer=f"Captured from {page.url}; not a file uploaded by the candidate.")
            result.files.append(SourcedFile(_filename(page.url, "pdf"), pdf, "url", page.url))
    return result
