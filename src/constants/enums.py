import enum


class JobStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class FileType(str, enum.Enum):
    PDF = "pdf"
    SCANNED_PDF = "scanned_pdf"
    DOCX = "docx"


class EmbeddingKind(str, enum.Enum):
    SKILL = "skill"
    ROLE = "role"


class CandidateStatus(str, enum.Enum):
    SOURCED = "sourced"
    SHORTLISTED = "shortlisted"
    APPROVED = "approved"
    REJECTED = "rejected"
    INTERVIEW_SCHEDULED = "interview_scheduled"


class PipelineStage(str, enum.Enum):
    """Stage changes of a sourcing/shortlisting run, logged in audit_logs.stage."""

    CREATED = "created"
    COLLECTING = "collecting"
    READING = "reading"
    SCORING = "scoring"
    SHORTLISTED = "shortlisted"
    EXPORTED = "exported"
    PARTLY_COMPLETED = "partly_completed"
    FAILED = "failed"


class ActionCategory(str, enum.Enum):
    AUTH = "auth"
    SCRAPING = "scraping"
    EXTRACTION = "extraction"
    SCORING = "scoring"
    SHORTLISTING = "shortlisting"
    EXPORTING = "exporting"


class Action(str, enum.Enum):
    # auth
    SIGN_IN = "sign_in"
    SIGN_UP = "sign_up"
    SIGN_OUT = "sign_out"
    PASSWORD_CHANGED = "password_changed"
    # scraping
    SCRAPING_STARTED = "scraping_started"
    SCRAPING_COMPLETED = "scraping_completed"
    SCRAPING_PARTIALLY_COMPLETED = "scraping_partially_completed"
    SCRAPING_FAILED = "scraping_failed"
    # extraction
    PARSING_STARTED = "parsing_started"
    PARSING_COMPLETED = "parsing_completed"
    PARSING_FAILED = "parsing_failed"
    EXTRACTION_STARTED = "extraction_started"
    EXTRACTION_COMPLETED = "extraction_completed"
    EXTRACTION_FAILED = "extraction_failed"
    # scoring
    SCORING_STARTED = "scoring_started"
    SCORING_FINISHED = "scoring_finished"
    SCORING_FAILED = "scoring_failed"
    # shortlisting
    SHORTLISTING_CREATED = "shortlisting_created"
    CANDIDATE_ADDED_TO_SHORTLIST = "candidate_added_to_shortlist"
    # export
    SHORTLIST_EXPORT_STARTED = "shortlist_export_started"
    SHORTLIST_EXPORT_COMPLETED = "shortlist_export_completed"
    SHORTLIST_EXPORT_FAILED = "shortlist_export_failed"
    # review (recruiter <-> hiring manager)
    CANDIDATE_REMOVED_FROM_SHORTLIST = "candidate_removed_from_shortlist"
    NOTES_UPDATED = "notes_updated"
    SHORTLIST_SHARED = "shortlist_shared"
    CANDIDATE_APPROVED = "candidate_approved"
    CANDIDATE_REJECTED = "candidate_rejected"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    SHORTLIST_RETURNED = "shortlist_returned"
