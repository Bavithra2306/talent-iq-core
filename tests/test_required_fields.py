"""A sourced resume needs a name and a way to contact the person; otherwise it waits for the recruiter."""

from src.control.agents.prompts.resume_extraction import ExtractedResume
from src.core.services import resume_service


def resume(**fields) -> ExtractedResume:
    return ExtractedResume(index=1, skills=["Python"], **fields)


def test_name_and_email_is_enough():
    assert resume_service.missing_required(resume(name="Asha Rao", email="asha@example.com")) is None


def test_name_and_phone_is_enough():
    assert resume_service.missing_required(resume(name="Asha Rao", phone="+91 98765 43210")) is None


def test_no_contact_goes_to_review():
    assert resume_service.missing_required(resume(name="Asha Rao")) == "no email or phone number"
    assert resume_service.missing_required(resume(name="Asha Rao", email="not an email", phone="12")) == "no email or phone number"


def test_no_name_goes_to_review():
    assert resume_service.missing_required(resume(email="asha@example.com")) == "no name"
