"""Rule-based parts of scoring (no LLM): experience, education and the weighted total.

Every component score is 0-100. Skill, role and industry levels come from the LLM
(see matching_service); experience, education and the role-and-industry mix are computed here.
"""

import re
from dataclasses import dataclass
from datetime import date

# ---------- weights ----------


@dataclass(frozen=True)
class Weights:
    skill: int = 40
    role: int = 20
    exp: int = 25
    edu: int = 15

    def validate(self) -> None:
        if min(self.skill, self.role, self.exp, self.edu) < 0 or (
            self.skill + self.role + self.exp + self.edu != 100
        ):
            raise ValueError("Weights must be non-negative and add up to 100.")

    def as_dict(self) -> dict:
        return {"skill_pct": self.skill, "role_pct": self.role,
                "exp_pct": self.exp, "edu_pct": self.edu}


ROLE_SHARE, INDUSTRY_SHARE = 0.7, 0.3


def role_industry_score(role: float, industry: float | None) -> float:
    """Role and industry fit: 70% role, 30% industry; role alone when the industry is unknown."""
    return float(role) if industry is None else round(ROLE_SHARE * role + INDUSTRY_SHARE * industry, 2)


def total_score(skill: float, role: float, exp: float, edu: float, weights: Weights) -> float:
    return round(
        (skill * weights.skill + role * weights.role + exp * weights.exp + edu * weights.edu) / 100,
        2,
    )


def breakdown(skill: float, role: float, exp: float, edu: float, weights: Weights) -> dict:
    """Per-component score, weight and weighted points; points add up to the total."""
    parts = {
        "skills": (skill, weights.skill),
        "role": (role, weights.role),
        "experience": (exp, weights.exp),
        "education": (edu, weights.edu),
    }
    return {
        name: {"score": round(score, 2), "weight": weight, "points": round(score * weight / 100, 2)}
        for name, (score, weight) in parts.items()
    }


# ---------- experience ----------


# Older work counts for less: (years since the job ended, weight); anything older = 0.25.
RECENCY_WEIGHTS = ((2, 1.0), (5, 0.75), (8, 0.5))
OLD_WORK_WEIGHT = 0.25
STALE_SKILL_YEARS = 5  # a required skill last used this long ago lowers the score
STALE_SKILL_PENALTY, MAX_STALE_PENALTY = 10.0, 30.0


def _years_since(role: dict, this_year: int) -> int | None:
    if role.get("current"):
        return 0
    end = role.get("end_year")
    return max(0, this_year - end) if isinstance(end, int) else None


def recency_weight(years_ago: int) -> float:
    return next((w for limit, w in RECENCY_WEIGHTS if years_ago <= limit), OLD_WORK_WEIGHT)


def recent_years(roles: list[dict] | None, this_year: int) -> float | None:
    """Work years with older jobs counting less; None when no job has dates."""
    dated = [(r["years"], ago) for r in roles or [] if isinstance(r, dict)
             and isinstance(r.get("years"), (int, float)) and (ago := _years_since(r, this_year)) is not None]
    if not dated:
        return None
    return round(sum(years * recency_weight(ago) for years, ago in dated), 1)


def _same_skill(wanted: str, listed: str) -> bool:
    a, b = wanted.lower().strip(), listed.lower().strip()
    return a == b or bool(re.search(rf"(?<![\w+#]){re.escape(a)}(?![\w+#])", b))


def stale_skills(roles: list[dict] | None, required_skills: list[str] | None, this_year: int) -> list[tuple[str, int]]:
    """Required skills whose most recent use (in a dated job) is STALE_SKILL_YEARS or more ago.
    Skills not tied to any job are unknown, not stale."""
    stale = []
    for skill in required_skills or []:
        last = None
        for role in roles or []:
            if not isinstance(role, dict) or not any(_same_skill(skill, s) for s in role.get("skills") or []):
                continue
            ago = _years_since(role, this_year)
            if ago is not None:
                year = this_year - ago
                last = year if last is None else max(last, year)
        if last is not None and this_year - last >= STALE_SKILL_YEARS:
            stale.append((skill, last))
    return stale


def experience_score(candidate_years: float | None, required_years: float | None,
                     roles: list[dict] | None = None, required_skills: list[str] | None = None,
                     this_year: int | None = None) -> tuple[float, str]:
    """Years against the minimum, with recent work counting more.

    * Older jobs count for less (RECENCY_WEIGHTS) when the resume gives job dates.
    * 100 when the (recency-weighted) years meet the minimum, otherwise proportional.
    * Each required skill last used 5+ years ago takes 10 points off (at most 30).
    """
    this_year = this_year or date.today().year
    stale = stale_skills(roles, required_skills, this_year)
    stale_note = " ".join(f"{s} last used in {y} ({this_year - y} years ago)." for s, y in stale)
    penalty = min(MAX_STALE_PENALTY, STALE_SKILL_PENALTY * len(stale))

    if not required_years:
        score, note = 100.0, "No minimum experience required."
    elif candidate_years is None:
        return 0.0, "The resume does not state the years of experience."
    else:
        recent = recent_years(roles, this_year)
        counted = candidate_years if recent is None else min(candidate_years, recent)
        older = "" if counted >= candidate_years else f" ({counted:g} when older jobs count less)"
        if counted >= required_years:
            score = 100.0
            note = f"{candidate_years:g} years{older} meets the {required_years:g}-year requirement."
            if candidate_years >= required_years * 2 and candidate_years - required_years >= 3:
                note += " Well above the minimum (possibly overqualified)."
        else:
            score = round(100 * counted / required_years, 2)
            note = f"{candidate_years:g} years{older} is below the {required_years:g}-year requirement."
    if penalty:
        score = max(0.0, score - penalty)
        note = f"{note} {stale_note}"
    return round(score, 2), note


# ---------- education ----------

# Case-sensitive abbreviations (too short to match case-insensitively).
_ABBREVIATIONS = [
    (4, r"\bPh\.?\s?D\b"),
    (3, r"\bM\.?\s?(?:E|A|S|Tech|Sc|Com|Ed|Pharm|Phil|Arch|CA)\b"),
    (3, r"\bMBA\b|\bMCA\b|\bMD\b|\bMS\b|\bLLM\b|\bMPT\b|\bPGDM\b"),
    (2, r"\bB\.?\s?(?:E|A|S|Tech|Sc|Com|Ed|Pharm|Arch|CA)\b"),
    (2, r"\bBBA\b|\bBCA\b|\bMBBS\b|\bBDS\b|\bBPT\b|\bLLB\b|\bBHM\b|\bBAMS\b|\bBHMS\b|\bGNM\b"),
]
_WORDS = [
    (4, r"doctorate|doctor of philosophy"),
    (3, r"master|post[- ]?graduate|chartered accountant|m\.?tech|m\.?sc\b"),
    (2, r"bachelor|graduate|undergraduate|b\.?tech|b\.?sc\b|b\.?com\b"),
    (1, r"diploma|certificate|polytechnic|d\.?pharm|\bITI\b|higher secondary|12th"),
]
_GENERIC = {
    "bachelor", "bachelors", "master", "masters", "degree", "related", "field", "equivalent",
    "required", "preferred", "from", "with", "relevant", "discipline", "subject", "any",
    "recognized", "university", "college", "institute", "diploma", "postgraduate", "graduate",
    "tech", "engineering", "science", "sciences", "arts", "commerce",
}


def education_level(text: str | None) -> int:
    """0 unknown, 1 diploma, 2 bachelor, 3 master, 4 doctorate."""
    if not text:
        return 0
    level = 0
    for rank, pattern in _ABBREVIATIONS:
        if re.search(pattern, text):
            level = max(level, rank)
    lowered = text.lower()
    for rank, pattern in _WORDS:
        if re.search(pattern, lowered):
            level = max(level, rank)
    return level


_LEVEL_NAMES = {1: "a diploma", 2: "a bachelor's degree", 3: "a master's degree", 4: "a doctorate"}


def required_education_level(text: str | None) -> int:
    """Level a job asks for. Alternatives ("B.E./B.Tech or MCA") mean any of them is
    enough, so the lowest level named counts."""
    if not text:
        return 0
    parts = re.split(r"\bor\b|/|,|;|\|", text, flags=re.IGNORECASE)
    levels = [lvl for lvl in (education_level(part) for part in parts) if lvl > 0]
    return min(levels) if levels else education_level(text)


def _field_words(text: str | None) -> set[str]:
    return {
        w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _GENERIC
    }


def education_score(candidate_education: list[dict] | None, required: str | None) -> tuple[float, str]:
    """Compare the highest degree with the requirement: same or higher level = 100,
    one level lower = 50, otherwise 0. A different field of study caps the score at 75."""
    if not required or not required.strip():
        return 100.0, "No education requirement."

    degrees = [
        " ".join(filter(None, [d.get("degree"), d.get("institution")]))
        for d in (candidate_education or []) if isinstance(d, dict)
    ]
    degree_text = " ; ".join(degrees)
    candidate_level = max((education_level(d.get("degree")) for d in (candidate_education or [])
                           if isinstance(d, dict)), default=0)
    required_level = required_education_level(required)

    if not degrees:
        return 0.0, "No education details found on the resume."
    if required_level == 0:
        required_level = 2  # a stated requirement without a recognisable level: assume a degree
    if candidate_level == 0:
        return 0.0, f"Education on the resume could not be matched to the requirement ({degree_text})."

    if candidate_level >= required_level:
        score = 100.0
    elif candidate_level == required_level - 1:
        score = 50.0
    else:
        score = 0.0

    have, need = _LEVEL_NAMES[candidate_level], _LEVEL_NAMES[required_level]
    if score == 100:
        note = f"Has {have}, which meets the requirement of {need} ({degree_text})."
    else:
        note = f"Has {have}, below the requirement of {need} ({degree_text})."
    wanted = _field_words(required)
    if score > 75 and wanted and not (wanted & _field_words(degree_text)):
        score = 75.0
        note += " The field of study differs from the one asked for."
    return score, note
