"""Prompt for LLM step 1+2 on a JD: confirm the document is a JD and extract its fields."""

from typing import Literal

from pydantic import BaseModel

from src.api.rest.schemas.job_schema import JobDraft
from src.constants.categories import SUGGESTED_CATEGORIES

# Long JDs are cut to keep the request within free-model limits.
MAX_JD_CHARS = 15000

SYSTEM_PROMPT_TEMPLATE = """You are an expert recruitment assistant that reads job descriptions (JDs).

Step 1 - classify the document:
- "jd": a job description / job posting / vacancy.
- "resume": a CV or resume describing one person's experience.
- "other": anything else.

Step 2 - if it is a "jd", extract these fields into "job" (otherwise set "job" to null):
- title: the job title, e.g. "Senior Python Developer".
- category: the broad field of work. Prefer one of: {categories}.
  If none fits, give your own short name (1-3 words). Any field is allowed.
- industry: the employer's industry if stated or obvious, e.g. "Software", "Hospital".
- experience_years: the MINIMUM years of experience required as an integer
  ("5+ years" -> 5, "3-5 years" -> 3, fresher -> 0). null if not stated.
- education: the required qualification as written, e.g. "B.E/B.Tech in Computer Science".
- location: city / region as written, e.g. "Chennai". null if not stated.
- work_mode: one of "onsite", "remote", "hybrid", or null if not stated.
- skills: REQUIRED skills only, as short canonical names ("PostgreSQL", not
  "experience with Postgres databases"). No duplicates.
- nice_to_have_skills: optional / preferred / "good to have" skills, same format.

Rules:
- Use only information in the document. Never invent values; use null or [] instead.
- Do not put a skill in both lists.
- Respond with ONLY a JSON object of this shape:
{{"document_type": "jd" | "resume" | "other", "job": {{ ...fields above... }} | null}}"""

SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(categories=", ".join(SUGGESTED_CATEGORIES))


class JDExtraction(BaseModel):
    document_type: Literal["jd", "resume", "other"]
    job: JobDraft | None = None


def build_user_prompt(text: str) -> str:
    return f"Document:\n\"\"\"\n{text[:MAX_JD_CHARS]}\n\"\"\""
