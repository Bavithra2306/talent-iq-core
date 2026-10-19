"""Rule-based parts of scoring (no LLM): experience, education and the weighted total.

Every component score is 0-100. Skill and role scores come from the LLM
(see matching_service); experience and education are computed here.
"""

import re
from dataclasses import dataclass

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


def experience_score(candidate_years: float | None, required_years: float | None) -> tuple[float, str]:
    """100 when the candidate meets the minimum, otherwise proportional (3 of 5 years = 60)."""
    if not required_years:
        return 100.0, "No minimum experience required."
    if candidate_years is None:
        return 0.0, "The resume does not state the years of experience."
    if candidate_years >= required_years:
        note = f"{candidate_years:g} years meets the {required_years:g}-year requirement."
        if candidate_years >= required_years * 2 and candidate_years - required_years >= 3:
            note += " Well above the minimum (possibly overqualified)."
        return 100.0, note
    score = round(100 * candidate_years / required_years, 2)
    return score, f"{candidate_years:g} years is below the {required_years:g}-year requirement."


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
