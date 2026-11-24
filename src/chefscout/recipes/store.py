"""Recipes read in one conversation, with their short ids (R1, R2 ...) and which ones were shown.

    data/users/<user_id>/conversations/<conversation_id>.recipes.json

The UI builds recipe cards from here, never from the model's text.
It also keeps the urls that web search returned in this conversation: only those may be read.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from chefscout.core.history import safe_id
from chefscout.memory.storage import locked, read_json, write_json
from chefscout.recipes.models import CheckedRecipe, Recipe, RecipeCheck

MAX_SOURCES = 300


def normalize_url(url: str) -> str:
    """One spelling per page: lowercase host without "www.", no fragment, no trailing slash."""
    parts = urlsplit((url or "").strip())
    host = parts.netloc.lower().removeprefix("www.")
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), parts.query, ""))


class _State(BaseModel):
    recipes: dict[str, CheckedRecipe] = Field(default_factory=dict)  # id -> recipe
    shown: list[list[str]] = Field(default_factory=list)  # one list of ids per show_recipes call
    sources: list[str] = Field(default_factory=list)  # normalized urls from web search results


class RecipeStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, user_id: str, conversation_id: str) -> Path:
        user = safe_id(user_id, "user id")
        conv = safe_id(conversation_id, "conversation id")
        return self.data_dir / "users" / user / "conversations" / f"{conv}.recipes.json"

    def _load(self, user_id: str, conversation_id: str) -> _State:
        return _State.model_validate(read_json(self.path(user_id, conversation_id), {}))

    def add(
        self,
        user_id: str,
        conversation_id: str,
        recipe: Recipe,
        check: RecipeCheck,
        **extra: object,
    ) -> CheckedRecipe:
        """Save a read recipe. The same url again keeps its id (no duplicate cards)."""
        path = self.path(user_id, conversation_id)
        with locked(path):
            state = self._load(user_id, conversation_id)
            existing = next((r for r in state.recipes.values() if r.recipe.url == recipe.url), None)
            rid = existing.id if existing else f"R{len(state.recipes) + 1}"
            item = CheckedRecipe(id=rid, recipe=recipe, check=check, **extra)  # type: ignore[arg-type]
            state.recipes[rid] = item
            write_json(path, state.model_dump(mode="json"))
        return item

    def items(self, user_id: str, conversation_id: str) -> list[CheckedRecipe]:
        return list(self._load(user_id, conversation_id).recipes.values())

    def get(self, user_id: str, conversation_id: str, rid: str) -> CheckedRecipe | None:
        return self._load(user_id, conversation_id).recipes.get(rid)

    def mark_shown(
        self,
        user_id: str,
        conversation_id: str,
        notes: dict[str, str],
        alternatives: set[str] | frozenset[str] = frozenset(),
    ) -> list[CheckedRecipe]:
        path = self.path(user_id, conversation_id)
        with locked(path):
            state = self._load(user_id, conversation_id)
            for rid, note in notes.items():
                state.recipes[rid].note = note
                state.recipes[rid].alternative = rid in alternatives
            state.shown.append(list(notes))
            write_json(path, state.model_dump(mode="json"))
        return [state.recipes[rid] for rid in notes]

    def last_shown(self, user_id: str, conversation_id: str) -> list[CheckedRecipe]:
        state = self._load(user_id, conversation_id)
        return [state.recipes[rid] for rid in state.shown[-1]] if state.shown else []

    def all_shown_ids(self, user_id: str, conversation_id: str) -> set[str]:
        return {rid for group in self._load(user_id, conversation_id).shown for rid in group}

    def known_urls(self, user_id: str, conversation_id: str) -> set[str]:
        return {r.recipe.url for r in self._load(user_id, conversation_id).recipes.values()}

    # ---- sources -----------------------------------------------------------------------

    def add_sources(self, user_id: str, conversation_id: str, urls: list[str]) -> None:
        path = self.path(user_id, conversation_id)
        with locked(path):
            state = self._load(user_id, conversation_id)
            for url in map(normalize_url, urls):
                if url and url not in state.sources:
                    state.sources.append(url)
            state.sources = state.sources[-MAX_SOURCES:]
            write_json(path, state.model_dump(mode="json"))

    def sources(self, user_id: str, conversation_id: str) -> set[str]:
        return set(self._load(user_id, conversation_id).sources)


def search_url_collector(store: RecipeStore):
    """Block observer: remembers every url that a web search returned in this conversation."""

    def observe(ctx, block: dict) -> None:
        if block.get("type") != "web_search_tool_result":
            return
        content = block.get("content")
        if isinstance(content, list):  # an error result is a dict, not a list
            urls = [r.get("url", "") for r in content if isinstance(r, dict)]
            store.add_sources(ctx.user_id, ctx.conversation_id, [u for u in urls if u])

    return observe
