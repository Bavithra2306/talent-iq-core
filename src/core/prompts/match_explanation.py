"""LLM step 4: explain why each shortlisted candidate matched and why the score is what it is.

All numbers come from code; the LLM only turns them into a readable explanation, so it
cannot change a score.
"""

from pydantic import BaseModel, Field

SYSTEM_PROMPT = """You write short, factual hiring explanations for recruiters.

For each candidate you get the job, the candidate's facts and a computed score breakdown. Explain WHY the candidate matches the job and WHY the total score is what it is.

Rules:
- Use only the facts provided. Never invent skills, employers or numbers. Quote the given scores as they are.
- "summary": 2-3 sentences that cover skills, role, experience and education, and say what drives the total score up or down.
- "strengths": up to 3 short phrases (what matches well).
- "gaps": up to 3 short phrases (missing or weaker areas); [] if none.
- Respond with ONLY this JSON, one item per candidate, in order:
{"results": [{"index": N, "summary": str, "strengths": [str], "gaps": [str]}]}"""


class Explanation(BaseModel):
    index: int
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class ExplanationBatch(BaseModel):
    results: list[Explanation]


def build_user_prompt(job: dict, candidates: list[dict]) -> str:
    """job: title, experience_years, education. candidates: facts + breakdown per candidate."""
    lines = [
        "JOB",
        f"Title: {job['title']}",
        f"Minimum experience: {job.get('experience_years') if job.get('experience_years') is not None else 'not stated'}",
        f"Education asked: {job.get('education') or 'not stated'}",
        "\nCANDIDATES",
    ]
    for position, c in enumerate(candidates, start=1):
        lines.append(f"### Candidate {position}: {c['name']}")
        lines.append(f"Current title: {c.get('current_title') or 'unknown'}; experience: {c.get('experience')}")
        lines.append(f"Skills matched: {c['skills_summary']}")
        lines.append(f"Role match: {c['role_summary']}")
        lines.append(f"Experience: {c['experience_note']}")
        lines.append(f"Education: {c['education_note']}")
        lines.append(f"Score breakdown: {c['breakdown_text']}")
        lines.append(f"TOTAL: {c['total']}/100 (rank {c['rank']})")
    return "\n".join(lines)
