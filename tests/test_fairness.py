"""BRD case 8: two resumes identical except name and gender get the same score.

Personal details never reach the scoring LLM or the rule-based scores, so the LLM sees the
same text for both and the code computes the same numbers."""

from types import SimpleNamespace

from src.control.agents.prompts import match_scoring
from src.core.services import matching_service as svc

JOB = SimpleNamespace(title="Python Developer", category="IT", industry="Banking", skills=["Python", "SQL"],
                      nice_to_have_skills=["Docker"], experience_years=3, education="B.Tech")

PROFILE = dict(current_title="Software Engineer (Python)", total_years_experience=4, location="Chennai",
               category="IT")
EXTRACTION = SimpleNamespace(
    skills=["Python", "SQL", "Docker"],
    education=[{"degree": "B.Tech Computer Science"}],
    roles_and_years=[{"title": "Software Engineer (Python)", "industry": "Banking", "years": 4,
                      "start_year": 2022, "current": True, "skills": ["Python", "SQL"]}],
)


def _pending(name, email, gender):
    candidate = SimpleNamespace(name=name, email=email, contact="+91 90000 00000", gender=gender, **PROFILE)
    p = svc.Pending(candidate, EXTRACTION)
    p.similarity = 0.82
    return p


def _prompt(p):
    return match_scoring.build_user_prompt(
        title=JOB.title, category=JOB.category, industry=JOB.industry, required=JOB.skills,
        nice=JOB.nice_to_have_skills, candidates=[svc._candidate_prompt_data(JOB, p)])


def test_identical_profiles_with_different_name_and_gender_give_the_same_llm_prompt():
    a = _pending("Priya Raman", "priya@example.com", "female")
    b = _pending("Arjun Kumar", "arjun@example.com", "male")
    assert _prompt(a) == _prompt(b)
    for detail in ("Priya", "Arjun", "example.com", "female", "male", "90000"):
        assert detail not in _prompt(a)


def test_identical_profiles_get_the_same_scores():
    a = _pending("Priya Raman", "priya@example.com", "female")
    b = _pending("Arjun Kumar", "arjun@example.com", "male")
    assert svc._experience(JOB, a) == svc._experience(JOB, b)
    same_llm_answer = match_scoring.CandidateMatch(
        index=1,
        skills=[match_scoring.SkillMatch(jd_skill=s, level=100, matched=s) for s in ("Python", "SQL", "Docker")],
        role=match_scoring.RoleMatch(level=100), industry=match_scoring.IndustryMatch(level=100))
    for p in (a, b):
        svc._apply_result(JOB, p, same_llm_answer)
    assert svc.skill_score(a.required) == svc.skill_score(b.required)
    assert a.role == b.role and a.industry == b.industry
