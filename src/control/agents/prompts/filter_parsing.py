"""Turns a recruiter's free-text search ("5yrs experince in chenai") into structured filters.

The corrected sentence is shown to the user, who confirms it before it is used.
"""

from pydantic import BaseModel, Field

from src.constants.categories import SUGGESTED_CATEGORIES

SYSTEM_PROMPT_TEMPLATE = """You convert a recruiter's search text into structured candidate filters.

First fix spelling and expand shorthand ("5yrs" = 5 years, "chenai" = Chennai, "pythn" = Python, "b.tech" stays B.Tech). Then extract:
- category: field of work; prefer one of: {categories}. null if not stated.
- location: a city or region, spelled correctly. null if not stated.
- min_experience: minimum years of experience as a number ("5 years", "5+ years", "at least 5" -> 5; "3 to 6 years" -> 3). null if not stated.
- max_experience: maximum years ("less than 3 years", "up to 4", "3 to 6 years" -> 6). null if not stated.
- education: a qualification or degree wording ("B.Tech", "MBA"). null if not stated.
- must_have_skills: skills the candidate must have, canonical names, no duplicates. [] if none.
- top_k: how many candidates the user wants ("top 10"). null if not stated.

Rules:
- Use only what the text says; never add filters that were not asked for.
- Professions and job titles ("nurses", "developers", "accountants") are NOT skills: use them only to set category. Put something in must_have_skills only when the text names a skill, tool or certification (e.g. Python, Docker, ACLS, Tally).
- "corrected_text": the search rewritten as a clean, short sentence in plain English that states exactly the filters you extracted.
- Respond with ONLY this JSON:
{{"corrected_text": str, "category": str|null, "location": str|null, "min_experience": number|null, "max_experience": number|null, "education": str|null, "must_have_skills": [str], "top_k": int|null}}"""


def system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(categories=", ".join(SUGGESTED_CATEGORIES))


class ParsedFilter(BaseModel):
    corrected_text: str = ""
    category: str | None = None
    location: str | None = None
    min_experience: float | None = Field(default=None, ge=0)
    max_experience: float | None = Field(default=None, ge=0)
    education: str | None = None
    must_have_skills: list[str] = Field(default_factory=list)
    top_k: int | None = Field(default=None, ge=1, le=100)
