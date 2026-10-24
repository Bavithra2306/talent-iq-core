"""A job starts with the default weights: skills 40, experience 25, role and industry 20, education 15."""

from src.core.services.scoring_rules import Weights
from src.data.models.postgres.scoring_config import ScoringConfig


def test_the_code_default_weights():
    assert Weights().as_dict() == {"skill_pct": 40, "exp_pct": 25, "role_pct": 20, "edu_pct": 15}
    Weights().validate()


def test_a_new_jobs_stored_defaults_match():
    columns = ScoringConfig.__table__.columns
    assert [columns[c].default.arg for c in ("skill_pct", "exp_pct", "role_pct", "edu_pct")] == [40, 25, 20, 15]
    assert [columns[c].server_default.arg for c in ("skill_pct", "exp_pct", "role_pct", "edu_pct")] == ["40", "25", "20", "15"]
