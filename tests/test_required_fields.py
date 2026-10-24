"""Every resume needs name, email, phone, skills, role and education. Experience may be missing (a fresher)."""

from src.control.agents.prompts.resume_extraction import Education, ExtractedResume, Role
from src.core.services import resume_service


def resume(**fields) -> ExtractedResume:
    complete = dict(name="Asha Rao", email="asha@example.com", phone="+91 98765 43210", skills=["Python"],
                    current_title="Developer", education=[Education(degree="B.E.")])
    return ExtractedResume(index=1, **{**complete, **fields})


def test_a_complete_resume_has_nothing_missing():
    assert resume_service.missing_required(resume()) == []


def test_experience_is_not_required():
    assert resume_service.missing_required(resume(total_experience_years=None)) == []


def test_each_required_field_is_checked():
    assert resume_service.missing_required(resume(name=None)) == ["name"]
    assert resume_service.missing_required(resume(email=None)) == ["email"]
    assert resume_service.missing_required(resume(email="not an email")) == ["email"]
    assert resume_service.missing_required(resume(phone=None)) == ["phone number"]
    assert resume_service.missing_required(resume(phone="12")) == ["phone number"]
    assert resume_service.missing_required(resume(skills=[])) == ["skills"]
    assert resume_service.missing_required(resume(current_title=None)) == ["role"]
    assert resume_service.missing_required(resume(education=[])) == ["education"]


def test_a_role_in_the_work_history_counts():
    assert resume_service.missing_required(resume(current_title=None, roles=[Role(title="Engineer")])) == []


def test_everything_missing_is_listed():
    empty = ExtractedResume(index=1)
    assert resume_service.missing_required(empty) == ["name", "email", "phone number", "skills", "role", "education"]
