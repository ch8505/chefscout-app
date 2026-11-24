"""The ideas proposed in one conversation: meals made of dishes, each with a stable id.

    data/users/<user_id>/conversations/<conversation_id>.plan.json

Three layers are kept apart:
- meal templates (data/meal_templates.json): which roles a meal needs, e.g. main + side + salad;
- the plan: the meals and dishes actually proposed (this file);
- recipes found for a dish (RecipeStore), linked by Dish.recipe_id / candidates.

Ids never change: replacing a dish adds a new dish ("M1.D4") and marks the old one "replaced",
so the rest of the meal, and the recipes already found for it, stay as they are.
Every status change goes through PlanStore.transition, so statuses stay consistent.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from chefscout.core.errors import ToolError
from chefscout.core.history import safe_id
from chefscout.memory.storage import locked, read_json, write_json

Intent = Literal["meal", "dish_ideas", "pantry", "vague", "recipe"]
Role = Literal["main", "side", "salad", "soup", "dessert", "full_meal"]
Method = Literal["oven", "stovetop", "grill", "fried", "raw", "no_cook", "slow"]
Kosher = Literal["meat", "dairy", "parve"]
MealTime = Literal["breakfast", "lunch", "dinner", "shabbat", "any"]
DishStatus = Literal["proposed", "selected", "replaced", "rejected"]
SearchStatus = Literal["none", "found", "not_found"]
# user = "swap it" (not a dislike), rejected = "I don't like it", not_found = no real recipe was found
ReplaceReason = Literal["user", "rejected", "not_found"]

# Allowed status changes. "replaced" and "rejected" are final.
TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"selected", "replaced", "rejected"},
    "selected": {"replaced", "rejected"},
    "replaced": set(),
    "rejected": set(),
}
ACTIVE = ("proposed", "selected")

_TEMPLATES_FILE = Path(__file__).resolve().parent.parent / "data" / "meal_templates.json"


class MealTemplate(BaseModel):
    roles: list[Role]
    optional: list[Role] = Field(default_factory=list)


@lru_cache
def templates() -> dict[str, MealTemplate]:
    raw = json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
    return {name: MealTemplate.model_validate(t) for name, t in raw.items()}


def template_for(meal_time: str) -> MealTemplate:
    return templates().get(meal_time) or templates()["any"]


class RequestParams(BaseModel):
    meal_time: MealTime = "any"
    kosher: Kosher | Literal["any"] = "any"
    max_minutes: int | None = Field(default=None, ge=1, le=600)
    servings: int | None = Field(default=None, ge=1, le=50)
    use_pantry: bool = False


class DishDraft(BaseModel):
    """A dish as the model proposes it (no id or status yet)."""

    role: Role
    name: str = Field(min_length=2, max_length=80)
    base: str = Field(min_length=2, max_length=40)  # the one main ingredient, e.g. "עוף"
    key_ingredients: list[str] = Field(default_factory=list, max_length=8)
    method: Method
    kosher: Kosher
    est_minutes: int | None = Field(default=None, ge=1, le=600)
    query: str = Field(default="", max_length=80)  # Hebrew search query, e.g. "מתכון מג'דרה"


class Dish(DishDraft):
    id: str
    status: DishStatus = "proposed"
    replaces: str | None = None
    reason: ReplaceReason | None = None  # why this dish replaced the one before it
    recipe_id: str | None = None  # the recipe shown now for this dish
    candidates: list[str] = Field(default_factory=list)  # good recipes read for it ("more options")
    shown_recipes: list[str] = Field(default_factory=list)
    search_status: SearchStatus = "none"

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


class MealDraft(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    style: str = Field(default="", max_length=40)
    dishes: list[DishDraft] = Field(min_length=1, max_length=5)


class Meal(BaseModel):
    id: str
    title: str
    template: MealTime = "any"
    style: str = ""
    dishes: list[Dish]

    def active_dishes(self) -> list[Dish]:
        return [d for d in self.dishes if d.active]

    @property
    def kosher(self) -> Kosher:
        """Derived from the active dishes, so it stays right after a replacement."""
        kinds = {d.kosher for d in self.active_dishes()}
        return "meat" if "meat" in kinds else "dairy" if "dairy" in kinds else "parve"


class Plan(BaseModel):
    intent: Intent
    request: RequestParams = Field(default_factory=RequestParams)
    meals: list[Meal] = Field(default_factory=list)  # intent "meal"
    ideas: list[Dish] = Field(default_factory=list)  # dish_ideas / pantry / vague / recipe
    version: int = 1

    def all_dishes(self) -> list[Dish]:
        return [d for m in self.meals for d in m.dishes] + list(self.ideas)

    def dish(self, dish_id: str) -> Dish | None:
        return next((d for d in self.all_dishes() if d.id == dish_id), None)

    def meal(self, meal_id: str) -> Meal | None:
        return next((m for m in self.meals if m.id == meal_id), None)

    def meal_of(self, dish_id: str) -> Meal | None:
        return next((m for m in self.meals if any(d.id == dish_id for d in m.dishes)), None)


def build_plan(
    intent: Intent,
    request: RequestParams,
    meals: list[MealDraft] | None = None,
    ideas: list[DishDraft] | None = None,
    version: int = 1,
) -> Plan:
    """Give every meal and dish its id: meals M1, M2; dishes M1.D1..; single ideas D1, D2.."""
    built_meals = [
        Meal(
            id=f"M{i}",
            title=m.title,
            template=request.meal_time,
            style=m.style,
            dishes=[Dish(id=f"M{i}.D{j}", **d.model_dump()) for j, d in enumerate(m.dishes, 1)],
        )
        for i, m in enumerate(meals or [], 1)
    ]
    built_ideas = [Dish(id=f"D{j}", **d.model_dump()) for j, d in enumerate(ideas or [], 1)]
    return Plan(intent=intent, request=request, meals=built_meals, ideas=built_ideas, version=version)


def _next_dish_id(plan: Plan, meal: Meal | None) -> str:
    if meal is not None:
        return f"{meal.id}.D{len(meal.dishes) + 1}"
    return f"D{len(plan.ideas) + 1}"


class PlanStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, user_id: str, conversation_id: str) -> Path:
        user = safe_id(user_id, "user id")
        conv = safe_id(conversation_id, "conversation id")
        return self.data_dir / "users" / user / "conversations" / f"{conv}.plan.json"

    # ---- read / write ----------------------------------------------------------------

    def load(self, user_id: str, conversation_id: str) -> Plan | None:
        raw = read_json(self.path(user_id, conversation_id), None)
        return Plan.model_validate(raw) if raw else None

    def _require(self, user_id: str, conversation_id: str) -> Plan:
        plan = self.load(user_id, conversation_id)
        if plan is None:
            raise ToolError("there is no plan yet. propose ideas first")
        return plan

    def _save(self, user_id: str, conversation_id: str, plan: Plan) -> None:
        write_json(self.path(user_id, conversation_id), plan.model_dump(mode="json"))

    def new_plan(
        self,
        user_id: str,
        conversation_id: str,
        intent: Intent,
        request: RequestParams,
        meals: list[MealDraft] | None = None,
        ideas: list[DishDraft] | None = None,
    ) -> Plan:
        """Replace the conversation's plan with a new one (a new request, not an edit)."""
        path = self.path(user_id, conversation_id)
        with locked(path):
            old = self.load(user_id, conversation_id)
            plan = build_plan(intent, request, meals, ideas, version=(old.version + 1) if old else 1)
            self._save(user_id, conversation_id, plan)
        return plan

    # ---- status changes --------------------------------------------------------------

    @staticmethod
    def _move(dish: Dish, status: DishStatus) -> bool:
        """Apply one status change. The same status again is a no-op (idempotent)."""
        if dish.status == status:
            return False
        if status not in TRANSITIONS[dish.status]:
            raise ToolError(f"{dish.id} is {dish.status} and cannot become {status}")
        dish.status = status
        return True

    def transition(self, user_id: str, conversation_id: str, dish_id: str, status: DishStatus) -> Dish:
        path = self.path(user_id, conversation_id)
        with locked(path):
            plan = self._require(user_id, conversation_id)
            dish = plan.dish(dish_id)
            if dish is None:
                raise ToolError(f"unknown dish id '{dish_id}'")
            if self._move(dish, status):
                plan.version += 1
                self._save(user_id, conversation_id, plan)
        return dish

    def select(
        self,
        user_id: str,
        conversation_id: str,
        meal_id: str | None = None,
        dish_ids: list[str] | None = None,
    ) -> list[Dish]:
        """A whole meal selects all its active dishes; dish ids select only those dishes.

        Returns the selected dishes. Selecting again changes nothing (idempotent).
        """
        if not meal_id and not dish_ids:
            raise ToolError("give a meal_id or dish_ids")
        path = self.path(user_id, conversation_id)
        with locked(path):
            plan = self._require(user_id, conversation_id)
            targets: list[Dish] = []
            if meal_id:
                meal = plan.meal(meal_id)
                if meal is None:
                    raise ToolError(f"unknown meal id '{meal_id}'")
                targets += meal.active_dishes()
            for did in dish_ids or []:
                dish = plan.dish(did)
                if dish is None:
                    raise ToolError(f"unknown dish id '{did}'")
                if not dish.active:
                    raise ToolError(f"{did} is {dish.status}. choose an active dish")
                if dish not in targets:
                    targets.append(dish)
            changed = [self._move(d, "selected") for d in targets]
            if any(changed):
                plan.version += 1
                self._save(user_id, conversation_id, plan)
        return targets

    def replace(
        self,
        user_id: str,
        conversation_id: str,
        dish_id: str,
        draft: DishDraft,
        status: Literal["replaced", "rejected"] = "replaced",
        reason: ReplaceReason = "user",
    ) -> Dish:
        """Swap one dish for a new one. Every other dish, id and recipe stays as it is.

        The new dish keeps the old one's "selected" status, so only it needs a new search.
        """
        if reason == "rejected":
            status = "rejected"
        path = self.path(user_id, conversation_id)
        with locked(path):
            plan = self._require(user_id, conversation_id)
            old = plan.dish(dish_id)
            if old is None:
                raise ToolError(f"unknown dish id '{dish_id}'")
            if not old.active:
                raise ToolError(f"{dish_id} is already {old.status}")
            meal = plan.meal_of(dish_id)
            new = Dish(id=_next_dish_id(plan, meal), replaces=old.id, reason=reason, **draft.model_dump())
            if old.status == "selected":
                new.status = "selected"
            self._move(old, status)
            container = meal.dishes if meal is not None else plan.ideas
            container.insert(container.index(old) + 1, new)
            plan.version += 1
            self._save(user_id, conversation_id, plan)
        return new
