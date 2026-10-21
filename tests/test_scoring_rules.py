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
