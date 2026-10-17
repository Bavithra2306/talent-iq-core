"""Turning sourced text (profiles, dataset rows, web pages) into documents for ingestion."""

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from fpdf import FPDF

# A fixed creation date makes identical text produce identical bytes, so the
# ingestion pipeline's SHA-256 check skips profiles that were already sourced.
_FIXED_DATE = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _latin1(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")


def text_to_pdf(title: str, body: str, footer: str | None = None) -> bytes:
    pdf = FPDF()
    pdf.set_creation_date(_FIXED_DATE)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(0, 8, _latin1(title[:150]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for line in body.splitlines():
        line = line.rstrip()
        # Break very long unbroken tokens (URLs) so fpdf can wrap them.
        line = re.sub(r"(\S{90})", r"\1 ", line)
        pdf.multi_cell(0, 5, _latin1(line) or " ", new_x="LMARGIN", new_y="NEXT")
    if footer:
        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 8)
        pdf.multi_cell(0, 4, _latin1(footer), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


class _TextAndLinks(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        if tag in self._BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor).strip()))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._anchor.append(data.strip())


def html_text_and_links(html: str) -> tuple[str, list[tuple[str, str]]]:
    parser = _TextAndLinks()
    parser.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text, parser.links
