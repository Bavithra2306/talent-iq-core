"""Prompt for LLM steps 1+2 on resumes: classify each document and extract its fields.

Several resumes go into one request (to stay inside free-tier requests-per-minute
limits); results come back in a list matched by `index`.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.constants.categories import SUGGESTED_CATEGORIES

# Resume text beyond this is cut (a resume rarely needs more; keeps requests small).
MAX_RESUME_CHARS = 7000

SYSTEM_PROMPT_TEMPLATE = """You are an expert recruitment assistant. You receive several documents, each starting with "### Document N". For EACH document do two steps.

Step 1 - classify it:
- "resume": a CV / resume of one person. A profile summary of one person (for example generated from a public GitHub or Stack Overflow profile) also counts as a resume.
- "jd": a job description / job posting.
- "other": anything else.

Step 2 - if it is a "resume", extract (otherwise leave the other fields null / empty):
- name: the person's full name. Many resumes are anonymised: if no personal name is written, use null. Never use a job title, company or heading as the name.
- email: lower-case email address, null if absent.
- phone: phone number as written, null if absent.
- location: the candidate's current city / region, null if absent.
- category: the candidate's field of work. Prefer one of: {categories}. If none fits, give your own short name (1-3 words).
- current_title: the most recent job title held. Use "Fresher" only when the document says the person is a student, fresher or recent graduate; if no job is mentioned, use null.
- total_experience_years: total YEARS OF WORK EXPERIENCE as a number with at most one decimal, computed from the job dates (today is {today}; "Present" means today). Count overlapping jobs once. Internships count as work. 0 for freshers. Do not count education. null when there are no job dates or durations to work from.
- skills: the candidate's skills as short canonical names ("PostgreSQL", "Patient care"), no duplicates, at most 40, most important first. Include tools, technologies, clinical / professional skills, and certifications.
- roles: one entry per job held: {{"title": str, "company": str | null, "years": number | null}} where years is the duration in years (e.g. 1.5). Most recent first.
- education: one entry per degree: {{"degree": str, "institution": str | null, "year": str | null}}.

Rules:
- Use only information in the document. Never invent values; use null or [] when something is missing.
- Keep the documents separate; never mix details between them.
- Respond with ONLY this JSON, one item per document, in the same order:
{{"results": [{{"index": N, "document_type": "resume" | "jd" | "other", "name": ..., "email": ..., "phone": ..., "location": ..., "category": ..., "current_title": ..., "total_experience_years": ..., "skills": [...], "roles": [...], "education": [...]}}]}}"""


def system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        categories=", ".join(SUGGESTED_CATEGORIES), today=date.today().isoformat()
    )


class Role(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=False)

    title: str
    company: str | None = None
    years: float | None = None


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
