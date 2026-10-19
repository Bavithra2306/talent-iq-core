from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TalentIQ Core"
    app_env: str = "development"
    debug: bool = True

    # Server logs: JSON lines on stdout (see src/observability/logging).
    log_level: str = "INFO"
    log_json: bool = True

    # Requests per client IP per minute (0 = no limit). "Heavy" = uploads, matching,
    # sourcing and other endpoints that call the LLM or parse files.
    rate_limit_per_minute: int = 600
    rate_limit_heavy_per_minute: int = 30

    database_url: str

    # Auth service (for the user directory: picking a hiring manager to share with).
    auth_service_url: str = "http://auth:8000"

    # Must match the auth service so access cookies can be verified here.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "talentiq-auth"
    jwt_audience: str = "talentiq-core"

    # LLM providers (all OpenAI-compatible). Requests go to "lanes" (provider + model)
    # in priority order; each lane has its own requests-per-minute budget and a
    # cool-down after errors. A lane without an API key is skipped.
    gemini_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("GOOGLE_GEMINI_API_KEY", "GEMINI_API_KEY")
    )
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_models: list[str] = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    gemini_rpm: int = 15  # documented free-tier limit per model

    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_models: list[str] = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    groq_rpm: int = 10  # tokens-per-minute (8000) is the real limit, so stay low

    openrouter_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENROUTER_API_KEY", "LLM_API_KEY")
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_models: list[str] = [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3.5-super-vl:free",
    ]
    openrouter_rpm: int = 6

    # Stronger model for writing explanations (phase 4); same Gemini key.
    llm_strong_model: str = "gemini-3.5-flash"

    llm_max_concurrency: int = 8
    llm_timeout_seconds: float = 60.0
    llm_lane_cooldown_seconds: float = 30.0

    # Embeddings: Gemini only. Different embedding models can't be compared with each
    # other, so there is deliberately no fallback to another model.
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768  # columns are vector(768); change both together
    embedding_batch_size: int = 50
    embedding_daily_limit: int = 1000  # Gemini free tier: texts per day
    embedding_quota_timezone: str = "UTC"  # day boundary of that quota
    display_timezone: str = "Asia/Kolkata"  # how reset times are shown to users
    embedding_backfill_minutes: int = 20  # how often pending embeddings are retried

    # Resume ingestion
    parse_workers: int = 4  # parser worker processes
    # True: read text-layer PDFs with pypdf (about 3 ms) and use Docling only for DOCX,
    # scanned and garbled PDFs. False: run every PDF through Docling's layout model
    # (about 3.5 s per file on CPU, so 100 resumes take several minutes).
    pdf_fast_path: bool = True
    extract_batch_size: int = 8  # resumes per LLM request
    max_resumes_per_upload: int = 150  # files per upload (or files inside one zip)

    # Resume sourcing (all optional keys raise the free rate limits)
    github_token: str | None = None
    stackexchange_key: str | None = None
    kaggle_username: str | None = None
    kaggle_key: str | None = None
    sourcing_user_agent: str = "TalentIQ-capstone/0.1 (resume sourcing; contact: admin)"
    sourcing_timeout_seconds: float = 20.0

    # Matching / shortlisting
    score_batch_size: int = 8  # candidates per LLM scoring request
    explain_batch_size: int = 5  # candidates per LLM explanation request
    pool_multiplier: int = 3  # candidates sent to the LLM = top_k x this ...
    pool_min: int = 30  # ... but at least this many
    pool_max: int = 100  # ... and at most this many

    # File storage: "local" now, "gcs" later (falls back to local on error).
    storage_backend: str = "local"
    local_storage_dir: str = "storage"
    gcs_bucket: str | None = None
    max_upload_bytes: int = 5 * 1024 * 1024  # one resume file
    max_upload_total_bytes: int = 350 * 1024 * 1024  # a whole upload, or a zip once unpacked
    max_jd_bytes: int = 10 * 1024 * 1024  # one JD file

    model_config = SettingsConfigDict(
        env_file=".env.docker",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
