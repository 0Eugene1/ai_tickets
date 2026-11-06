from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets are read only from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    app_name: str = "ticket-assistant"
    app_env: str = "local"
    log_level: str = "INFO"
    database_path: Path = Path("data/ticket_assistant.db")
    knowledge_path: Path = Path("data/knowledge")

    llm_provider: Literal["mock", "openai_compatible"] = "mock"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 30.0

    telegram_enabled: bool = False
    telegram_bot_token: str | None = Field(default=None, repr=False)
    telegram_chat_id: str | None = None

    metabase_base_url: str | None = None
    metabase_auth_mode: Literal["api_key", "session"] = "api_key"
    metabase_api_token: str | None = Field(default=None, repr=False)
    metabase_performer_filter: str = ""
    metabase_performer_label: str = ""
    metabase_database_id: int = 5
    metabase_polling_enabled: bool = False
    metabase_poll_interval_seconds: float = 60.0
    metabase_empty_ticket_grace_seconds: float = 300.0
    metabase_baseline_on_first_run: bool = True
    metabase_active_statuses: str = "0"
    metabase_max_ticket_age_hours: float = 72.0
    metabase_departure_confirmation_polls: int = 3
    metabase_snapshot_drop_guard_fraction: float = 0.5
    metabase_snapshot_drop_guard_min_size: int = 10
    metabase_max_new_events_per_poll: int = 10
    metabase_rebaseline_after_seconds: float = 3600.0
    metabase_pending_ttl_hours: float = 24.0
    metabase_retry_delays_seconds: str = "300,900,1800,3600"
    metabase_diagnostics_enabled: bool = False
    metabase_diagnostics_max_age_days: float = 90.0
    metabase_utc_offset_hours: int = 7
    metabase_timeout_seconds: float = 30.0
    metabase_allow_insecure_http: bool = False

    # Confluence is read-only. Keep the token only in the local environment/.env.
    confluence_base_url: str | None = None
    confluence_auth_mode: Literal["basic", "bearer"] = "basic"
    confluence_email: str | None = None
    confluence_api_token: str | None = Field(default=None, repr=False)
    confluence_space_keys: str = ""
    confluence_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
