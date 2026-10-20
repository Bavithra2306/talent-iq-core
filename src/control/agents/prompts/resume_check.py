"""Prompt for AI step 1 on uploaded or sourced files: is each one a resume?

Only the start of each file is sent, and many files go into one request, so this
check is cheap. Files that pass go to step 2 (resume_extraction); the rest wait for
the recruiter's review.
"""

from typing import Literal

from pydantic import BaseModel

# The start of a document is enough to tell what it is.
CHECK_CHARS = 1800

SYSTEM_PROMPT = """You sort documents for a recruitment team. You receive several documents, each starting with "### Document N" (only the beginning of each is shown).

For EACH document decide what it is:
- "resume": a CV / resume of one person. A profile summary of one person (for example made from a public GitHub or Stack Overflow profile) also counts. Such a profile may end with a note like "Generated from the public GitHub profile ... not a resume written by the candidate": ignore that note, it is still a "resume".
- "jd": a job description or job posting.
- "other": anything else (invoice, letter, minutes, receipt, an organisation's page, ...).

Respond with ONLY this JSON, one item per document, in the same order:
{"results": [{"index": N, "document_type": "resume" | "jd" | "other"}]}"""


class DocumentCheck(BaseModel):
    index: int
    document_type: Literal["resume", "jd", "other"] = "other"


class CheckBatch(BaseModel):
    results: list[DocumentCheck]


def build_user_prompt(texts: list[str]) -> str:
    """texts are numbered 1..N in the order given."""
    return "\n\n".join(
        f"### Document {i}\n{text[:CHECK_CHARS]}" for i, text in enumerate(texts, start=1)
    )
