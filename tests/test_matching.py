"""Skill scoring, the LLM level scale, and the prompt the scoring LLM sees."""

from types import SimpleNamespace

from src.control.agents.prompts import match_scoring
from src.control.agents.prompts.resume_extraction import Role
from src.core.services import matching_service as svc


def _skills(*levels):
    return [{"jd_skill": f"s{i}", "level": lvl, "matched": None} for i, lvl in enumerate(levels)]


def test_skill_score_is_the_required_average_and_missing_counts_as_zero():
    assert svc.skill_score(_skills(100, 75, 50, 0)) == 56.25


def test_skill_score_without_required_skills_is_zero():
    assert svc.skill_score([]) == 0.0


def test_nice_to_have_never_changes_the_skill_score():
    # skill_score takes only the required skills: nice-to-have are shown, not scored
    assert svc.skill_score(_skills(100, 50)) == 75.0


def test_combined_similarity_is_60_percent_skills_40_percent_role():
    assert round(svc.combined_similarity(0.9, 0.5), 4) == 0.74
    assert svc.combined_similarity(0.8, None) == 0.8
    assert svc.combined_similarity(None, None) is None


def test_llm_levels_snap_to_the_allowed_scale():
    assert match_scoring.SkillMatch(level=80).level == 75
    assert match_scoring.SkillMatch(level=30).level == 50
    assert match_scoring.SkillMatch(level=10).level == 0
    assert match_scoring.SkillMatch(level="oops").level == 0


def test_explanation_fields_are_optional_in_the_llm_answer():
    result = match_scoring.CandidateMatch(index=1)
    assert result.summary == "" and result.strengths == [] and result.gaps == []


def test_scoring_prompt_has_no_personal_details_and_marks_nice_to_have_as_unscored():
    prompt = match_scoring.build_user_prompt(
        title="Backend Developer", category="IT", required=["Python"], nice=["Docker"],
        candidates=[{"current_title": "Software Engineer", "roles": ["Software Engineer (3 yrs)"],
                     "skills": ["Python"], "similarity": 81, "experience": "3 years meets the 2-year requirement.",
                     "education": "Has a bachelor's degree."}],
    )
    assert "mention only, they do not change the score" in prompt
    assert "Profile similarity to the job: 81%" in prompt
    for personal in ("Name:", "Email", "Phone", "Gender", "Age"):
        assert personal not in prompt


def test_job_signature_changes_when_skills_change():
    class Job:
        title, skills, nice_to_have_skills, industry = "Dev", ["Python"], [], None

    before = svc.job_signature(Job)
    Job.skills = ["Python", "SQL"]
    assert svc.job_signature(Job) != before


def test_resume_roles_keep_plausible_years_only():
    role = Role(title="Dev", start_year="Jan 2019", end_year="Present", current="true")
    assert (role.start_year, role.end_year, role.current) == (2019, None, True)


def test_role_lines_show_industry_and_dates():
    extraction = SimpleNamespace(roles_and_years=[
        {"title": "Analyst", "industry": "Banking", "years": 2, "start_year": 2024, "current": True},
        {"title": "Intern", "years": 0.5}])
    assert svc._role_lines(extraction) == ["Analyst, Banking (2 yrs, 2024-present)", "Intern (0.5 yrs)"]
