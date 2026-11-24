"""Request and response models of the HTTP API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from chefscout.memory.profile import CookedRecipe, PreferenceKind, Rating
from chefscout.recipes.models import CheckedRecipe
from chefscout.recipes.plan import Dish, Plan

MAX_MESSAGE_CHARS = 2000


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    conversation_id: str | None = None
    # A button press: the same request_id twice is done only once (double clicks, retries).
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")
    action: ChatAction | None = None


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


ROLE_LABELS = {
    "main": "מנה עיקרית",
    "side": "תוספת",
    "salad": "סלט",
    "soup": "מרק",
    "dessert": "קינוח",
    "full_meal": "ארוחה בתבנית אחת",
}
KOSHER_LABELS = {"meat": "בשרי", "dairy": "חלבי", "parve": "פרווה"}


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
    dish_id: str | None = None
    role_label: str | None = None
    time_fit: str = "any"
    alternative: bool = False

    @classmethod
    def from_checked(cls, item: CheckedRecipe, plan: Plan | None = None) -> RecipeCard:
        r, c = item.recipe, item.check
        dish = plan.dish(item.dish_id) if plan and item.dish_id else None
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
            dish_id=item.dish_id,
            role_label=ROLE_LABELS.get(dish.role) if dish else None,
            time_fit=item.time_fit,
            alternative=item.alternative,
        )


# ---- ideas (a plan of meals / dishes, before any recipe) -------------------------------------


class DishView(BaseModel):
    id: str
    role: str
    role_label: str
    name: str
    kosher: str
    kosher_label: str
    est_minutes: int | None
    status: str
    search_status: str
    recipe: RecipeCard | None = None


class MealView(BaseModel):
    id: str
    title: str
    kosher: str
    kosher_label: str
    dishes: list[DishView]


class PlanView(BaseModel):
    intent: str
    version: int
    meals: list[MealView]
    ideas: list[DishView]

    @classmethod
    def build(cls, plan: Plan, recipe: Callable[[str], CheckedRecipe | None]) -> PlanView:
        """Only active dishes; each with its main recipe card when one was found."""

        def dish(d: Dish) -> DishView:
            found = recipe(d.recipe_id) if d.recipe_id else None
            return DishView(
                id=d.id,
                role=d.role,
                role_label=ROLE_LABELS[d.role],
                name=d.name,
                kosher=d.kosher,
                kosher_label=KOSHER_LABELS[d.kosher],
                est_minutes=d.est_minutes,
                status=d.status,
                search_status=d.search_status,
                recipe=RecipeCard.from_checked(found, plan) if found else None,
            )

        return cls(
            intent=plan.intent,
            version=plan.version,
            meals=[
                MealView(
                    id=m.id,
                    title=m.title,
                    kosher=m.kosher,
                    kosher_label=KOSHER_LABELS[m.kosher],
                    dishes=[dish(d) for d in m.active_dishes()],
                )
                for m in plan.meals
            ],
            ideas=[dish(d) for d in plan.ideas if d.active],
        )


# ---- buttons ---------------------------------------------------------------------------------

_ID = r"^[A-Za-z0-9._-]{1,64}$"


class ChatAction(BaseModel):
    """A button on an idea or recipe card. The server turns it into a short note for the model."""

    type: Literal["get_recipes", "replace", "reject", "more"]
    meal_id: str | None = Field(default=None, pattern=_ID)
    dish_id: str | None = Field(default=None, pattern=_ID)


class FeedbackRequest(BaseModel):
    target: str = Field(pattern=_ID)  # a dish id (M1.D2, D1) or a recipe id (R3)
    feedback: Literal["saved", "rejected"]
    request_id: str | None = Field(default=None, pattern=_ID)


class MoreRequest(BaseModel):
    dish_id: str = Field(pattern=_ID)
    request_id: str | None = Field(default=None, pattern=_ID)


class MoreResult(BaseModel):
    cards: list[RecipeCard]
    needs_search: bool  # nothing left from what was read: ask the model for one new search
    duplicate: bool = False


class SavedItem(BaseModel):
    title: str
    url: str | None
    kind: str
    at: str


ChatRequest.model_rebuild()  # ChatAction is defined below ChatRequest
