from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TalentIQ Core"
    app_env: str = "development"
    debug: bool = True

    database_url: str

    # Must match the auth service so access cookies can be verified here.
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "talentiq-auth"
    jwt_audience: str = "talentiq-core"

    # LLM via OpenRouter (OpenAI-compatible API). OpenRouter tries the fallback
    # models in order when the primary fails or is rate-limited.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENROUTER_API_KEY", "LLM_API_KEY")
    )
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    # e.g. ["Groq"] for paid models; free models pick their own provider
    llm_provider_order: list[str] = []
    llm_fallback_models: list[str] = [
        "nvidia/nemotron-3.5-super-vl:free",
        "google/gemma-4-31b-it:free",
    ]
    llm_max_concurrency: int = 8
    llm_timeout_seconds: float = 60.0

    # Embeddings (OpenAI-compatible). Columns are vector(768); change both together.
    embedding_base_url: str = "https://openrouter.ai/api/v1"
    embedding_api_key: str | None = None  # falls back to llm_api_key
    embedding_model: str = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
    embedding_dimensions: int = 768

    # File storage: "local" now, "gcs" later (falls back to local on error).
    storage_backend: str = "local"
    local_storage_dir: str = "storage"
    gcs_bucket: str | None = None
    max_upload_bytes: int = 10 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env.docker",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
