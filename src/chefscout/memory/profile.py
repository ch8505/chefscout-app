"""The lasting user profile: data/users/<user_id>/profile.json"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from chefscout.core.errors import ToolError
from chefscout.core.history import safe_id
from chefscout.memory.storage import locked, normalize, read_json, write_json

ListKind = Literal["allergy", "avoid", "kitchen_rule", "like"]
LIST_KINDS: dict[str, str] = {  # kind -> Profile field
    "allergy": "allergies",
    "avoid": "avoid",
    "kitchen_rule": "kitchen_rules",
    "like": "likes",
}
PreferenceKind = Literal["allergy", "avoid", "kitchen_rule", "like", "diet"]
PREFERENCE_KINDS = [*LIST_KINDS, "diet"]
Rating = Literal["loved", "ok", "disliked"]


class CookedRecipe(BaseModel):
    title: str
    url: str | None = None
    cooked_on: str = Field(default_factory=lambda: date.today().isoformat())
    rating: Rating | None = None
    note: str = ""


class Change(BaseModel):
    at: str = Field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    action: str
    kind: str
    value: str
    old: str | None = None


class Profile(BaseModel):
    allergies: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)  # foods the user never wants
    diet: str | None = None
    kitchen_rules: list[str] = Field(default_factory=list)  # e.g. "no margarine"
    likes: list[str] = Field(default_factory=list)
    cooked: list[CookedRecipe] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.allergies or self.avoid or self.diet or self.kitchen_rules or self.likes or self.cooked
        )


class ProfileStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, user_id: str) -> Path:
        return self.data_dir / "users" / safe_id(user_id, "user id") / "profile.json"

    def load(self, user_id: str) -> Profile:
        return Profile.model_validate(read_json(self.path(user_id), {}))

    def _save(self, user_id: str, profile: Profile) -> None:
        write_json(self.path(user_id), profile.model_dump(mode="json"))

    # ---- preferences ----------------------------------------------------------
    def remember(self, user_id: str, kind: str, value: str) -> str:
        value = normalize(value)
        if not value:
            raise ToolError("value must not be empty")
        if kind not in PREFERENCE_KINDS:
            raise ToolError(f"unknown kind '{kind}'. allowed: {', '.join(PREFERENCE_KINDS)}")
        with locked(self.path(user_id)):
            p = self.load(user_id)
            if kind == "diet":
                if p.diet == value:
                    return f"diet already {value}"
                old, p.diet = p.diet, value
                p.changes.append(Change(action="set", kind=kind, value=value, old=old))
                msg = f"diet: {old or '-'} -> {value}"
            else:
                items = getattr(p, LIST_KINDS[kind])
                if value in items:
                    return f"already saved: {kind} {value}"
                items.append(value)
                p.changes.append(Change(action="add", kind=kind, value=value))
                msg = f"saved {kind}: {value}"
                # "never X" and "likes X" cannot both be true; the newest statement wins.
                opposite = {"avoid": "likes", "like": "avoid"}.get(kind)
                if opposite and value in getattr(p, opposite):
                    getattr(p, opposite).remove(value)
                    p.changes.append(Change(action="remove", kind=opposite, value=value))
                    msg += f" (removed from {opposite})"
            self._save(user_id, p)
        return msg

    def forget(self, user_id: str, kind: str, value: str) -> str:
        value = normalize(value)
        if kind not in PREFERENCE_KINDS:
            raise ToolError(f"unknown kind '{kind}'")
        with locked(self.path(user_id)):
            p = self.load(user_id)
            if kind == "diet":
                if p.diet is None:
                    return "no diet saved"
                p.changes.append(Change(action="remove", kind=kind, value=p.diet))
                p.diet = None
            else:
                items = getattr(p, LIST_KINDS[kind])
                if value not in items:
                    return f"not found: {kind} {value}. saved: {', '.join(items) or '-'}"
                items.remove(value)
                p.changes.append(Change(action="remove", kind=kind, value=value))
            self._save(user_id, p)
        return f"removed {kind}: {value or 'diet'}"

    # ---- cooked recipes ---------------------------------------------------------
    def add_cooked(
        self, user_id: str, title: str, url: str | None = None, rating: str | None = None, note: str = ""
    ) -> str:
        title = " ".join((title or "").split())
        if not title:
            raise ToolError("title must not be empty")
        if rating not in (None, "loved", "ok", "disliked"):
            raise ToolError("rating must be loved, ok or disliked")
        with locked(self.path(user_id)):
            p = self.load(user_id)
            p.cooked.append(CookedRecipe(title=title, url=url, rating=rating, note=note))  # type: ignore[arg-type]
            self._save(user_id, p)
        return f"saved cooked recipe: {title}" + (f" ({rating})" if rating else "")

    def cooked(self, user_id: str, only_loved: bool = False, limit: int = 10) -> list[CookedRecipe]:
        items = self.load(user_id).cooked
        if only_loved:
            items = [c for c in items if c.rating == "loved"]
        return items[-limit:][::-1]  # newest first
