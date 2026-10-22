"""Errors people can see are plain sentences, and logging never crashes a run."""


from src.constants.enums import Action, ActionCategory, PipelineStage
from src.core.exceptions.base import UnprocessableError
from src.core.services import audit_service
from src.core.services.friendly_errors import friendly_error
from src.handlers.http_clients.llm_client import LLMError
from src.handlers.http_clients.sourcing.fetcher import FetchError


def test_a_technical_error_becomes_a_plain_sentence():
    text = friendly_error(AttributeError("'str' object has no attribute 'value'"), "scoring the candidates")
    assert text.startswith("Something went wrong while scoring the candidates.")
    assert "attribute" not in text and "'str'" not in text


def test_known_failures_get_specific_advice():
    assert "AI service is busy" in friendly_error(LLMError("all lanes failed"))
    assert "took too long" in friendly_error(TimeoutError())
    assert "couldn't save" in friendly_error(__import__("sqlalchemy").exc.SQLAlchemyError("boom"))


def test_messages_already_written_for_people_are_kept():
    assert friendly_error(UnprocessableError("The file could not be read.")) == "The file could not be read."
    assert friendly_error(FetchError("github.com is rate-limiting requests (HTTP 429).")).startswith("github.com")


def test_audit_enums_accept_members_text_and_names():
    assert audit_service._enum(ActionCategory, "scoring") is ActionCategory.SCORING
    assert audit_service._enum(ActionCategory, ActionCategory.SCORING) is ActionCategory.SCORING
    assert audit_service._enum(PipelineStage, "FAILED") is PipelineStage.FAILED
    assert audit_service._enum(Action, "not an action") is None
    assert audit_service._enum(Action, None) is None


async def test_logging_with_plain_strings_does_not_crash(monkeypatch):
    def no_database():
        raise RuntimeError("no database in unit tests")

    monkeypatch.setattr(audit_service, "AsyncSessionLocal", no_database)
    # Before the fix this raised "'str' object has no attribute 'value'" and ended the whole run.
    await audit_service.log_event(user_id=None, category="scoring", action="scoring_failed", stage="failed")
