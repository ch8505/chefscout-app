"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel

from chefscout import __version__
from chefscout.config import Settings, get_settings

log = logging.getLogger(__name__)


class Health(BaseModel):
    status: str
    version: str
    model: str
    api_key_configured: bool


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="ChefScout", version=__version__)
    app.state.settings = settings

    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        return Health(
            status="ok",
            version=__version__,
            model=settings.model,
            api_key_configured=settings.api_key_configured,
        )

    log.info("app created: model=%s, data_dir=%s", settings.model, settings.data_dir)
    return app
