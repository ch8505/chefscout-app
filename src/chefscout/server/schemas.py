"""Request and response models of the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from chefscout.memory.profile import CookedRecipe, PreferenceKind, Rating
from chefscout.recipes.models import CheckedRecipe

MAX_MESSAGE_CHARS = 2000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    conversation_id: str | None = None


class PreferenceRequest(BaseModel):
    kind: PreferenceKind
    value: str = Field(default="", max_length=100)


class PantryRequest(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=50)
    remove: list[str] = Field(default_factory=list, max_length=50)
    clear: bool = False


class CookedRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    rating: Rating | None = None
    note: str = Field(default="", max_length=300)


class ProfileView(BaseModel):
    allergies: list[str]
    avoid: list[str]
    diet: str | None
    kitchen_rules: list[str]
    likes: list[str]
    cooked: list[CookedRecipe]


class SubstituteNeed(BaseModel):
    ingredient: str
    rule: str


class RecipeCard(BaseModel):
    id: str
    title: str
    url: str
    site: str
    image: str | None
    minutes: int | None
    servings: str | None
    missing: list[str]
    needs_substitute: list[SubstituteNeed]
    note: str

    @classmethod
    def from_checked(cls, item: CheckedRecipe) -> RecipeCard:
        r, c = item.recipe, item.check
        return cls(
            id=item.id,
            title=r.title,
            url=r.url,
            site=r.site,
            image=r.image,
            minutes=r.total_minutes,
            servings=r.servings,
            missing=c.missing[:8],
            needs_substitute=[
                SubstituteNeed(ingredient=h.ingredient, rule=h.rule) for h in c.needs_substitute
            ],
            note=item.note,
        )
