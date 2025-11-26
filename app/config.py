import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_env_file() -> str:
    local_override = _PROJECT_ROOT / ".env.local"
    if local_override.exists():
        return str(local_override)
    override = os.environ.get("CHATBOT_ENV_FILE")
    if override:
        return override
    env_name = os.environ.get("ENV", "development").lower()
    candidate = _PROJECT_ROOT / f".env.{env_name}"
    if env_name not in {"", "development"} and candidate.exists():
        return str(candidate)
    return str(_PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    """Central configuration loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
    )

    env: str = Field("development", alias="ENV")
    database_url: str = Field(
        "postgresql+psycopg2://postgres:postgres@localhost:5432/chatbot",
        alias="DATABASE_URL",
    )
    secret_key: SecretStr = Field(SecretStr("dev-secret"), alias="SECRET_KEY")
    openai_api_key: SecretStr = Field(SecretStr("test-key"), alias="OPENAI_API_KEY")
    app_base_url: str = Field("http://localhost:8000", alias="APP_BASE_URL")
    session_cookie_name: str = Field("chatbot_session", alias="SESSION_COOKIE_NAME")
    session_expire_minutes: int = Field(60 * 24, alias="SESSION_EXPIRE_MINUTES")
    chat_inactivity_warning_seconds: int = Field(
        70,
        alias="CHAT_INACTIVITY_WARNING_SECONDS",
    )
    chat_inactivity_grace_seconds: int = Field(
        60,
        alias="CHAT_INACTIVITY_GRACE_SECONDS",
    )
    chat_inactivity_warning_message: str = Field(
        "Just checking in - I'll close the chat soon if I don't hear back.",
        alias="CHAT_INACTIVITY_WARNING_MESSAGE",
    )
    chat_inactivity_close_message: str = Field(
        "I'll close our chat for now. Feel free to start a new one anytime!",
        alias="CHAT_INACTIVITY_CLOSE_MESSAGE",
    )
    default_model: str = Field("gpt-4o-mini", alias="OPENAI_CHAT_MODEL")
    embedding_model: str = Field("text-embedding-3-large", alias="OPENAI_EMBEDDING_MODEL")
    crawl_max_pages_default: int = 100
    crawl_max_depth_default: int = 3
    crawl_delay_seconds_default: float = 1.0
    cors_allow_origins: Optional[str] = Field(None, alias="CORS_ALLOW_ORIGINS")
    redis_url: Optional[str] = Field(None, alias="REDIS_URL")
    admin_default_email: Optional[str] = Field(None, alias="ADMIN_DEFAULT_EMAIL")
    admin_default_password: Optional[str] = Field(None, alias="ADMIN_DEFAULT_PASSWORD")
    smtp_host: Optional[str] = Field(None, alias="SMTP_HOST")
    smtp_port: int = Field(587, alias="SMTP_PORT")
    smtp_username: Optional[str] = Field(None, alias="SMTP_USERNAME")
    smtp_password: Optional[SecretStr] = Field(None, alias="SMTP_PASSWORD")
    smtp_from_email: Optional[str] = Field(None, alias="SMTP_FROM_EMAIL")
    smtp_use_tls: bool = Field(True, alias="SMTP_USE_TLS")

    @field_validator("app_base_url")
    @classmethod
    def ensure_no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def require_production_secrets(cls, values: "Settings") -> "Settings":
        strict_envs = {"production", "staging"}
        if values.env.lower() not in strict_envs:
            return values

        missing: list[str] = []
        if values.secret_key.get_secret_value() in {"dev-secret", "changeme"}:
            missing.append("SECRET_KEY")
        if values.openai_api_key.get_secret_value() in {"", "test-key", "sk-your-key"}:
            missing.append("OPENAI_API_KEY")
        if values.database_url.startswith("postgresql+psycopg2://postgres:postgres@localhost"):
            missing.append("DATABASE_URL")

        if missing:
            joined = ", ".join(sorted(missing))
            raise ValueError(
                f"Missing required settings for {values.env} environment: {joined}"
            )
        return values


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""

    return Settings()
