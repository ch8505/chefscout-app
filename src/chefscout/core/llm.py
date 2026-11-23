"""Builds the Anthropic client. The only place that touches the API key."""

from __future__ import annotations

from typing import Any

from chefscout.config import Settings
from chefscout.core.errors import MissingApiKeyError


def build_client(settings: Settings) -> Any:
    if not settings.api_key_configured:
        raise MissingApiKeyError("CHEFSCOUT_ANTHROPIC_API_KEY is not set. Put it in .env (see .env.example).")
    import anthropic  # imported lazily: tests and the server can start without it being used

    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
        max_retries=2,
        timeout=90.0,
    )
