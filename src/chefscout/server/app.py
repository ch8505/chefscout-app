"""FastAPI application factory: JSON API under /api, the web page at /."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chefscout import __version__
from chefscout.bootstrap import Stores, build_agent, build_stores
from chefscout.config import Settings, get_settings
from chefscout.core.agent import Agent
from chefscout.core.errors import InvalidIdError, ToolError
from chefscout.core.history import new_conversation_id, safe_id
from chefscout.core.tools import ToolContext
from chefscout.memory.storage import locked
from chefscout.server.chat import stream_turn
from chefscout.server.idempotency import RequestLog
from chefscout.server.schemas import (
    ChatRequest,
    CookedRequest,
    FeedbackRequest,
    PantryRequest,
    PlanView,
    PreferenceRequest,
    ProfileView,
    RecipeCard,
    SavedItem,
)
from chefscout.tools.feedback_tools import record_feedback

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class Health(BaseModel):
    status: str
    version: str
    model: str
    api_key_configured: bool


def create_app(
    settings: Settings | None = None,
    agent_factory: Callable[[Stores], Agent] | None = None,
) -> FastAPI:
    """agent_factory lets tests inject a fake model; by default the real agent is built on first chat."""
    settings = settings or get_settings()
    stores = build_stores(settings)
    factory = agent_factory or (lambda s: build_agent(settings, stores=s))
    user = settings.default_user_id
    requests = RequestLog(settings.data_dir)
    # What the feedback endpoint needs from the stores (the same objects the agent uses).
    services = {
        "settings": settings,
        "recipes": stores.recipes,
        "plans": stores.plans,
        "history": stores.history,
    }
    app = FastAPI(title="ChefScout", version=__version__)
    agent_holder: dict[str, Agent] = {}
    agent_lock = threading.Lock()

    def get_agent() -> Agent:
        with agent_lock:  # built lazily: the server starts even without an API key
            if "agent" not in agent_holder:
                agent_holder["agent"] = factory(stores)
            return agent_holder["agent"]

    def conv_id(value: str) -> str:
        try:
            return safe_id(value, "conversation id")
        except InvalidIdError as e:
            raise HTTPException(status_code=400, detail="invalid conversation id") from e

    # ---- health ----------------------------------------------------------------------
    @app.get("/api/health", response_model=Health)
    def health() -> Health:
        return Health(
            status="ok",
            version=__version__,
            model=settings.model,
            api_key_configured=settings.api_key_configured,
        )

    # ---- chat --------------------------------------------------------------------------
    @app.post("/api/conversations")
    def new_conversation() -> dict[str, str]:
        return {"conversation_id": new_conversation_id()}

    @app.post("/api/chat")
    def chat(req: ChatRequest) -> StreamingResponse:
        conv = conv_id(req.conversation_id) if req.conversation_id else None
        return StreamingResponse(
            stream_turn(
                get_agent=get_agent,
                user_id=user,
                message=req.message.strip(),
                conversation_id=conv,
                action=req.action,
                request_id=req.request_id,
                requests=requests,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/conversations/{conversation_id}/recipes", response_model=list[RecipeCard])
    def last_recipes(conversation_id: str) -> list[RecipeCard]:
        conv = conv_id(conversation_id)
        plan = stores.plans.load(user, conv)
        return [RecipeCard.from_checked(s, plan) for s in stores.recipes.last_shown(user, conv)]

    # ---- ideas (the plan of this conversation) ------------------------------------------------
    def current_plan(conv: str) -> PlanView | None:
        plan = stores.plans.load(user, conv)
        if plan is None:
            return None
        return PlanView.build(plan, lambda rid: stores.recipes.get(user, conv, rid))

    @app.get("/api/conversations/{conversation_id}/plan", response_model=PlanView | None)
    def get_plan(conversation_id: str) -> PlanView | None:
        return current_plan(conv_id(conversation_id))

    @app.post("/api/conversations/{conversation_id}/feedback", response_model=PlanView | None)
    def feedback(conversation_id: str, req: FeedbackRequest) -> PlanView | None:
        """Save or reject an idea or a recipe, straight from a button: no model call, no cost."""
        conv = conv_id(conversation_id)
        ctx = ToolContext(user_id=user, conversation_id=conv, services=services)
        with locked(stores.conversations.path(user, conv)):
            if requests.first_time(user, conv, req.request_id):
                try:
                    record_feedback({"feedback": req.feedback, "target": req.target}, ctx)
                except ToolError as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e
        return current_plan(conv)

    @app.get("/api/history/saved", response_model=list[SavedItem])
    def saved() -> list[SavedItem]:
        return [
            SavedItem(title=e.title, url=e.url, kind=e.kind, at=e.at.isoformat())
            for e in stores.history.saved(user)
        ]

    # ---- pantry (this conversation) --------------------------------------------------------
    @app.get("/api/conversations/{conversation_id}/pantry")
    def get_pantry(conversation_id: str) -> dict[str, list[str]]:
        return {"items": stores.pantry.items(user, conv_id(conversation_id))}

    @app.post("/api/conversations/{conversation_id}/pantry")
    def update_pantry(conversation_id: str, req: PantryRequest) -> dict[str, list[str]]:
        items = stores.pantry.update(
            user, conv_id(conversation_id), add=req.add, remove=req.remove, clear=req.clear
        )
        return {"items": items}

    # ---- profile (lasting) ---------------------------------------------------------------------
    @app.get("/api/profile", response_model=ProfileView)
    def get_profile() -> ProfileView:
        p = stores.profiles.load(user)
        return ProfileView(
            allergies=p.allergies,
            avoid=p.avoid,
            diet=p.diet,
            kitchen_rules=p.kitchen_rules,
            likes=p.likes,
            cooked=stores.profiles.cooked(user, limit=10),
        )

    @app.post("/api/profile/remember", response_model=ProfileView)
    def remember(req: PreferenceRequest) -> ProfileView:
        try:
            stores.profiles.remember(user, req.kind, req.value)
        except ToolError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return get_profile()

    @app.post("/api/profile/forget", response_model=ProfileView)
    def forget(req: PreferenceRequest) -> ProfileView:
        stores.profiles.forget(user, req.kind, req.value)
        return get_profile()

    @app.post("/api/cooked", response_model=ProfileView)
    def cooked(req: CookedRequest) -> ProfileView:
        stores.profiles.add_cooked(user, req.title, url=req.url, rating=req.rating, note=req.note)
        return get_profile()

    # ---- web page -----------------------------------------------------------------------------
    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    log.info("app created: model=%s, data_dir=%s", settings.model, settings.data_dir)
    return app
