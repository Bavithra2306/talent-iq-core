"""Experience, education and the weighted total (all rule-based, no LLM)."""

import pytest

from src.core.services import scoring_rules as rules
from src.core.services.scoring_rules import Weights


def test_default_weights_follow_the_scoring_rule():
    w = Weights()
    assert (w.skill, w.exp, w.role, w.edu) == (40, 25, 20, 15)
    w.validate()


def test_weights_must_add_up_to_100():
    with pytest.raises(ValueError):
        Weights(50, 20, 20, 20).validate()


def test_total_is_the_weighted_sum():
    # 80*40% + 100*20% + 60*25% + 50*15% = 32 + 20 + 15 + 7.5
    assert rules.total_score(80, 100, 60, 50, Weights()) == 74.5


def test_breakdown_points_add_up_to_the_total():
    parts = rules.breakdown(80, 100, 60, 50, Weights())
    assert sum(p["points"] for p in parts.values()) == rules.total_score(80, 100, 60, 50, Weights())


@pytest.mark.parametrize(("have", "need", "score"), [
    (5, 3, 100.0),     # meets the minimum
    (3, 5, 60.0),      # proportional below the minimum
    (None, 3, 0.0),    # not stated on the resume
    (2, None, 100.0),  # the job asks for no minimum
])
def test_experience_score(have, need, score):
    assert rules.experience_score(have, need)[0] == score


@pytest.mark.parametrize(("degree", "required", "score"), [
    ("B.Tech Computer Science", "B.E./B.Tech in Computer Science", 100.0),
    ("M.Sc Computer Science", "Bachelor's degree in Computer Science", 100.0),  # higher level is fine
    ("Diploma in Computer Science", "Bachelor's degree in Computer Science", 50.0),  # one level lower
    ("B.Com", "B.Tech in Computer Science", 75.0),  # right level, different field
])
def test_education_score(degree, required, score):
    assert rules.education_score([{"degree": degree}], required)[0] == score


def test_no_education_requirement_scores_full():
    assert rules.education_score([], None)[0] == 100.0


def test_missing_education_scores_zero():
    assert rules.education_score([], "B.Tech")[0] == 0.0


# ---------- recent experience (BRD case 2 and case 3) ----------

THIS_YEAR = 2026


def _role(title, years, end_year=None, current=False, skills=()):
    return {"title": title, "years": years, "end_year": end_year, "current": current, "skills": list(skills)}


def test_case_2_all_skills_and_6_of_5_years_scores_full():
    roles = [_role("Python Developer", 6, current=True, skills=["Python", "Django"])]
    assert rules.experience_score(6, 5, roles, ["Python", "Django"], THIS_YEAR)[0] == 100.0


def test_case_3_skill_last_used_8_years_ago_lowers_the_score_with_a_reason():
    roles = [_role("Data Analyst", 3, current=True, skills=["Excel"]),
             _role("Java Developer", 4, end_year=2018, skills=["Java"])]
    score, note = rules.experience_score(7, 5, roles, ["Java"], THIS_YEAR)
    assert score < 100
    assert "Java last used in 2018 (8 years ago)" in note


def test_older_jobs_count_for_less():
    roles = [_role("Developer", 2, current=True), _role("Developer", 4, end_year=2014)]
    # 2 recent years count fully; 4 years that ended 12 years ago count a quarter: 2 + 1 = 3
    assert rules.recent_years(roles, THIS_YEAR) == 3.0
    score, note = rules.experience_score(6, 5, roles, [], THIS_YEAR)
    assert score == 60.0 and "when older jobs count less" in note


def test_without_job_dates_total_years_are_used():
    assert rules.experience_score(6, 5, [{"title": "Dev", "years": 6}], ["Python"], THIS_YEAR)[0] == 100.0


def test_a_skill_not_tied_to_a_job_is_not_stale():
    assert rules.stale_skills([_role("Developer", 3, end_year=2015, skills=["C++"])], ["Python"], THIS_YEAR) == []


def test_skill_names_match_whole_words_only():
    assert rules.stale_skills([_role("Web Dev", 3, end_year=2012, skills=["JavaScript"])], ["Java"], THIS_YEAR) == []


def test_stale_penalty_is_capped():
    skills = ["A", "B", "C", "D", "E"]
    assert rules.experience_score(10, None, [_role("Dev", 2, end_year=2010, skills=skills)], skills, THIS_YEAR)[0] == 70.0


# ---------- role and industry fit ----------

def test_role_and_industry_mix():
    assert rules.role_industry_score(100, 50) == 85.0
    assert rules.role_industry_score(75, None) == 75.0  # industry unknown: role only
