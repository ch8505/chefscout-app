"""All settings in one place, read from environment variables or .env (prefix CHEFSCOUT_)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CHEFSCOUT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The key is optional at startup so the server and tests run without it;
    # the agent refuses to call the model until it is set.
    anthropic_api_key: SecretStr | None = None
    model: str = "claude-haiku-5-5"
    effort: Effort = "low"

    # Output cap per model call (includes thinking). Answers are short by design.
    max_output_tokens: int = Field(default=8000, ge=256, le=64000)
    # Safety net: maximum model calls in one user turn.
    max_tool_iterations: int = Field(default=8, ge=1, le=30)
    # Summarize the conversation when a request grows past this many tokens.
    compaction_threshold_tokens: int = Field(default=30000, ge=2000)

    data_dir: Path = Path("data")
    # Single local user for now; the stores are already per-user for sharing later.
    default_user_id: str = "me"
    max_web_searches: int = Field(default=3, ge=0, le=10)
    # Basic version; the dynamic-filtering version is not listed for Haiku 5.5.
    web_search_tool: str = "web_search_20250305"
    # Optional two-letter country for localized results. Empty by default: IL is not supported by the API.
    web_search_country: str | None = None

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @property
    def api_key_configured(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    """Settings for the running app. Tests build their own Settings instead."""
    return Settings()
