"""Prompt for AI step 2: extract the fields of documents already known to be resumes
(step 1, resume_check, decided that; or the recruiter confirmed it).

Several resumes go into one request (to stay inside free-tier requests-per-minute
limits); results come back in a list matched by `index`.
"""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.constants.categories import SUGGESTED_CATEGORIES

# Resume text beyond this is cut (a resume rarely needs more; keeps requests small).
MAX_RESUME_CHARS = 7000

SYSTEM_PROMPT_TEMPLATE = """You are an expert recruitment assistant. You receive several resumes, each starting with "### Document N". A profile made from a public GitHub or Stack Overflow page counts as a resume; ignore a closing note saying it was generated from a public profile. For EACH document extract:
- name: the person's full name. Many resumes are anonymised: if no personal name is written, use null. Never use a job title, company or heading as the name.
- email: lower-case email address, null if absent.
- phone: phone number as written, null if absent.
- location: the candidate's current city / region, null if absent.
- category: the candidate's field of work. Prefer one of: {categories}. If none fits, give your own short name (1-3 words).
- current_title: the most recent job title held. Use "Fresher" only when the document says the person is a student, fresher or recent graduate; if no job is mentioned, use null.
- total_experience_years: total YEARS OF WORK EXPERIENCE as a number with at most one decimal, computed from the job dates (today is {today}; "Present" means today). Count overlapping jobs once. Internships count as work. 0 for freshers. Do not count education. null when there are no job dates or durations to work from.
- skills: the candidate's skills as short canonical names ("PostgreSQL", "Patient care"), no duplicates, at most 40, most important first. Include tools, technologies, clinical / professional skills, and certifications.
- roles: one entry per job held, most recent first: {{"title": str, "company": str | null, "industry": str | null, "years": number | null, "start_year": int | null, "end_year": int | null, "current": bool, "skills": [str]}}
  - years: the duration in years (e.g. 1.5).
  - start_year / end_year: calendar years as written; end_year is null and current is true for a job held now ("Present").
  - industry: the employer's industry in 1-3 words ("Banking", "Hospital", "E-commerce", "IT services"), null if it cannot be told.
  - skills: the skills (same canonical names as above) the resume says were used in THIS job, at most 10; [] if none are named.
- education: one entry per degree: {{"degree": str, "institution": str | null, "year": str | null}}.

Rules:
- Use only information in the document. Never invent values; use null or [] when something is missing.
- Keep the documents separate; never mix details between them.
- Respond with ONLY this JSON, one item per document, in the same order:
{{"results": [{{"index": N, "name": ..., "email": ..., "phone": ..., "location": ..., "category": ..., "current_title": ..., "total_experience_years": ..., "skills": [...], "roles": [...], "education": [...]}}]}}"""


def system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        categories=", ".join(SUGGESTED_CATEGORIES), today=date.today().isoformat()
    )


class Role(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=False)

    title: str
    company: str | None = None
    industry: str | None = None
    years: float | None = None
    start_year: int | None = None
    end_year: int | None = None
    current: bool = False
    skills: list[str] = Field(default_factory=list)

    @field_validator("start_year", "end_year", mode="before")
    @classmethod
    def year_or_none(cls, value):
        """"Present", "2019-03" or junk: keep a plausible 4-digit year only."""
        match = re.search(r"(19|20)\d{2}", str(value)) if value is not None else None
        return int(match.group()) if match else None

    @field_validator("current", mode="before")
    @classmethod
    def as_bool(cls, value):
        return bool(value) and str(value).lower() not in ("false", "no", "0")


class Education(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    degree: str
    institution: str | None = None
    year: str | None = None


class ExtractedResume(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    index: int
    document_type: Literal["resume", "jd", "other"] = "resume"
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    category: str | None = None
    current_title: str | None = None
    total_experience_years: float | None = None
    skills: list[str] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)


class ResumeBatch(BaseModel):
    results: list[ExtractedResume]


def build_user_prompt(texts: list[str]) -> str:
    """texts are numbered 1..N in the order given."""
    return "\n\n".join(
        f"### Document {i}\n{text[:MAX_RESUME_CHARS]}" for i, text in enumerate(texts, start=1)
    )
