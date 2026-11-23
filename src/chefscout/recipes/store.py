"""Recipes read in one conversation, with their short ids (R1, R2 ...) and which ones were shown.

    data/users/<user_id>/conversations/<conversation_id>.recipes.json

The UI builds recipe cards from here, never from the model's text.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from chefscout.core.history import safe_id
from chefscout.memory.storage import locked, read_json, write_json
from chefscout.recipes.models import CheckedRecipe, Recipe, RecipeCheck


class _State(BaseModel):
    recipes: dict[str, CheckedRecipe] = Field(default_factory=dict)  # id -> recipe
    shown: list[list[str]] = Field(default_factory=list)  # one list of ids per show_recipes call


class RecipeStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, user_id: str, conversation_id: str) -> Path:
        user = safe_id(user_id, "user id")
        conv = safe_id(conversation_id, "conversation id")
        return self.data_dir / "users" / user / "conversations" / f"{conv}.recipes.json"

    def _load(self, user_id: str, conversation_id: str) -> _State:
        return _State.model_validate(read_json(self.path(user_id, conversation_id), {}))

    def add(self, user_id: str, conversation_id: str, recipe: Recipe, check: RecipeCheck) -> CheckedRecipe:
        path = self.path(user_id, conversation_id)
        with locked(path):
            state = self._load(user_id, conversation_id)
            existing = next((r for r in state.recipes.values() if r.recipe.url == recipe.url), None)
            rid = existing.id if existing else f"R{len(state.recipes) + 1}"
            item = CheckedRecipe(id=rid, recipe=recipe, check=check)
            state.recipes[rid] = item
            write_json(path, state.model_dump(mode="json"))
        return item

    def get(self, user_id: str, conversation_id: str, rid: str) -> CheckedRecipe | None:
        return self._load(user_id, conversation_id).recipes.get(rid)

    def mark_shown(self, user_id: str, conversation_id: str, notes: dict[str, str]) -> list[CheckedRecipe]:
        path = self.path(user_id, conversation_id)
        with locked(path):
            state = self._load(user_id, conversation_id)
            for rid, note in notes.items():
                state.recipes[rid].note = note
            state.shown.append(list(notes))
            write_json(path, state.model_dump(mode="json"))
        return [state.recipes[rid] for rid in notes]

    def last_shown(self, user_id: str, conversation_id: str) -> list[CheckedRecipe]:
        state = self._load(user_id, conversation_id)
        return [state.recipes[rid] for rid in state.shown[-1]] if state.shown else []

    def known_urls(self, user_id: str, conversation_id: str) -> set[str]:
        return {r.recipe.url for r in self._load(user_id, conversation_id).recipes.values()}
