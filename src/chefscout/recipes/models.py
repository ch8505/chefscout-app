"""Recipe data as read from a site, and the result of checking it for one user."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Recipe(BaseModel):
    url: str
    site: str
    title: str
    ingredients: list[str]
    image: str | None = None
    total_minutes: int | None = None
    servings: str | None = None


class RuleHit(BaseModel):
    ingredient: str
    rule: str  # the profile entry it hit, e.g. "בלי מרגרינה"


class RecipeCheck(BaseModel):
    status: Literal["ok", "blocked"]
    blocked_by: list[RuleHit] = Field(default_factory=list)  # allergies / never-eat
    needs_substitute: list[RuleHit] = Field(default_factory=list)  # kitchen rules
    missing: list[str] = Field(default_factory=list)  # ingredient lines not in the pantry
    have: list[str] = Field(default_factory=list)


class CheckedRecipe(BaseModel):
    id: str  # short id for the model, e.g. "R3"
    recipe: Recipe
    check: RecipeCheck
    note: str = ""  # the model's short note when shown (e.g. what to substitute)
