from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Bar Order Bot"
    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./barbot.db"

    bot_token: str | None = None
    mini_app_base_url: str | None = None
    leaderboard_pseudonym_secret: str | None = None
    webhook_base_url: str | None = None
    webhook_secret: str = Field(default="development-webhook-secret", min_length=8)

    staff_token: str = "development-staff-token"
    admin_token: str = "development-admin-token"
    owner_token: str = "development-owner-token"
    analyst_token: str = "development-analyst-token"

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_transcription_model: str = "gpt-4o-mini-transcribe"

    assistant_enabled: bool = True
    assistant_api_key: str | None = None
    assistant_base_url: str | None = None
    assistant_model: str | None = "google/gemini-3.1-flash-lite"
    assistant_timeout_seconds: float = Field(default=25.0, ge=3.0, le=90.0)
    cocktail_db_api_key: str | None = None

    @property
    def effective_cocktail_db_api_key(self) -> str | None:
        return self.cocktail_db_api_key or None

    staff_warning_minutes: int = Field(default=5, ge=1, le=120)
    staff_critical_minutes: int = Field(default=10, ge=2, le=240)
    auto_progress_enabled: bool = True
    auto_progress_poll_seconds: int = Field(default=10, ge=2, le=60)

    seed_demo: bool = True
    sql_echo: bool = False
    log_level: str = "INFO"

    @property
    def webhook_url(self) -> str | None:
        if not self.webhook_base_url:
            return None
        return f"{self.webhook_base_url.rstrip('/')}/telegram/webhook"

    @property
    def effective_assistant_api_key(self) -> str | None:
        return self.assistant_api_key or self.openai_api_key

    @property
    def effective_assistant_base_url(self) -> str:
        return (self.assistant_base_url or self.openai_base_url).rstrip("/")

    @property
    def mini_app_url(self) -> str | None:
        if not self.mini_app_base_url:
            return None
        return f"{self.mini_app_base_url.rstrip('/')}/miniapp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
