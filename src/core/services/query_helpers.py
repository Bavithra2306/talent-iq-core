"""Shared SQL helpers."""

import re

from sqlalchemy import String, cast


def skill_clause(skills_column, skill: str):
    """True when any skill in a JSON list contains `skill` as whole word(s), case-insensitive.

    "GST" matches "GST filing", "Java" does not match "JavaScript", "C++" matches "C++".
    """
    escaped = re.sub(r"([^A-Za-z0-9 ])", r"\\\1", skill.strip())
    pattern = rf'(?:"|[^[:alnum:]"]){escaped}(?:"|[^[:alnum:]"])'
    return cast(skills_column, String).op("~*")(pattern)
