"""LLM step 3: score each JD skill and the role against several candidates at once, and
explain each candidate in the same call.

The LLM only judges *how well a JD skill / role is covered* using a fixed scale; all
averaging and weighting happens in code so results are reproducible and re-weightable.
Nice-to-have skills are judged too, but only so they can be mentioned: they never change
the score.
"""

from pydantic import BaseModel, Field, field_validator

LEVELS = (0, 50, 75, 100)

SYSTEM_PROMPT = """You are a strict, fair recruitment assessor. You compare candidates with ONE job.

For every JD skill, decide how well the candidate's listed skills and roles cover it, using ONLY this scale:
- 100 = exact: the candidate lists the same skill (synonyms and spelling variants count: "Postgres" = "PostgreSQL", "JS" = "JavaScript").
- 75 = strongly related: a closely related skill that clearly transfers (e.g. MySQL for PostgreSQL, Flask for FastAPI, Azure for AWS, staff nurse for ICU nurse tasks).
- 50 = partly related: the same broad area but a notable gap (e.g. general SQL for PostgreSQL, Selenium for pytest).
- 0 = different or missing: nothing relevant.

For the ROLE, compare the JD job title with the candidate's current title and past roles with the same scale:
100 = same role, 75 = closely related role, 50 = same field but a different role, 0 = unrelated.

Then explain the candidate to a recruiter, using the levels you gave and the profile similarity, experience and education facts provided:
- "summary": 2-3 plain sentences: why this candidate fits the job (or not) and what holds the score down. Mention nice-to-have skills the candidate has (they do not change the score but are worth knowing). Do not write any score or percentage numbers.
- "strengths": up to 3 short phrases, e.g. "Python and FastAPI as asked".
- "gaps": up to 3 short phrases on what this job asks for that the resume lacks, e.g. "No Kubernetes"; [] if nothing is missing.

Rules:
- Judge only from what is listed; do not assume skills that are not mentioned.
- Never use or mention name, gender, age, photo or other personal details.
- Do not be generous: when unsure between two levels, choose the lower one.
- For each JD skill, name the candidate skill that matched ("matched"), or null when level is 0.
- The "skills" array must contain exactly one entry per JD skill, in the same order as listed (required skills first, then nice-to-have), with "jd_skill" copied exactly.
- Respond with ONLY this JSON, one item per candidate, in order:
{"results": [{"index": N, "skills": [{"jd_skill": str, "level": 0|50|75|100, "matched": str|null}], "role": {"level": 0|50|75|100, "matched_role": str|null}, "summary": str, "strengths": [str], "gaps": [str]}]}"""


class SkillMatch(BaseModel):
    jd_skill: str = ""
    level: int = 0
    matched: str | None = None

    @field_validator("level", mode="before")
    @classmethod
    def snap(cls, value):
        """Snap to the nearest allowed level so a stray 80 or 25 can't distort scores."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0
        return min(LEVELS, key=lambda level: abs(level - number))


class RoleMatch(BaseModel):
    level: int = 0
    matched_role: str | None = None

    @field_validator("level", mode="before")
    @classmethod
    def snap(cls, value):
        return SkillMatch.snap(value)


class CandidateMatch(BaseModel):
    index: int
    skills: list[SkillMatch] = Field(default_factory=list)
    role: RoleMatch = Field(default_factory=RoleMatch)
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class MatchBatch(BaseModel):
    results: list[CandidateMatch]


def build_user_prompt(
    *, title: str, category: str | None, required: list[str], nice: list[str],
    candidates: list[dict],
) -> str:
    """candidates: dicts with current_title, roles (list of 'Title (2 yrs)'), skills, and the
    facts for the explanation: similarity (0-100), experience and education notes."""
    lines = ["JOB", f"Title: {title}"]
    if category:
        lines.append(f"Category: {category}")
    lines.append("Required skills:")
    lines += [f"{i}. {s}" for i, s in enumerate(required, start=1)]
    if nice:
        lines.append("Nice-to-have skills (mention only, they do not change the score):")
        lines += [f"{i}. {s}" for i, s in enumerate(nice, start=len(required) + 1)]
    lines.append("\nCANDIDATES")
    for position, c in enumerate(candidates, start=1):
        lines.append(f"### Candidate {position}")
        lines.append(f"Current title: {c.get('current_title') or 'unknown'}")
        lines.append("Roles (most recent first): " + ("; ".join(c.get("roles") or []) or "none listed"))
        lines.append("Skills: " + (", ".join(c.get("skills") or []) or "none listed"))
        if c.get("similarity") is not None:
            lines.append(f"Profile similarity to the job: {c['similarity']}%")
        if c.get("experience"):
            lines.append(f"Experience: {c['experience']}")
        if c.get("education"):
            lines.append(f"Education: {c['education']}")
    return "\n".join(lines)
