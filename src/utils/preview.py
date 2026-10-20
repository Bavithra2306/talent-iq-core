"""Browser previews of stored files.

Browsers show PDFs and images themselves but download Word files. A DOCX is
therefore turned into a simple web page (mammoth, free / BSD licence): headings,
lists, tables and images are kept, the exact layout is not. The original file stays
available as a download.
"""

import html
import io
import re

import mammoth

# The page can show text, styles and embedded images, and nothing else: no scripts,
# no outside requests (a document's links can't run code).
_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
_UNSAFE_LINK = re.compile(r'href="\s*(?:javascript|data|vbscript):[^"]*"', re.IGNORECASE)

_STYLE = """
body { font: 15px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #1f2937;
       background: #f5f3ff; margin: 0; }
.page { max-width: 820px; margin: 24px auto; background: #fff; padding: 40px 48px;
        border-radius: 12px; box-shadow: 0 1px 3px rgb(0 0 0 / 10%); }
.bar { max-width: 820px; margin: 16px auto 0; color: #6b7280; font-size: 13px; }
h1, h2, h3 { color: #3b0a7a; line-height: 1.3; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #e5e7eb; padding: 6px 8px; vertical-align: top; }
img { max-width: 100%; }
"""


def docx_to_html(data: bytes, title: str) -> str:
    """A self-contained, script-free HTML page for viewing a Word document."""
    result = mammoth.convert_to_html(io.BytesIO(data))
    body = _UNSAFE_LINK.sub('href="#"', result.value) or "<p><em>This document has no text.</em></p>"
    name = html.escape(title)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"{_CSP}\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{name}</title><style>{_STYLE}</style></head><body>"
        f"<div class=\"bar\">Preview of {name}. Use Download to get the original Word file.</div>"
        f"<div class=\"page\">{body}</div></body></html>"
    )
