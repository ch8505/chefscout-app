"""The gate every proposed idea passes before it is saved: profile, kashrut, meal template, variety, time.

Two levels:
- errors (safety and must-haves): allergies / never-eat, meat with dairy, the kashrut or time the user
  asked for, a recent explicit rejection, a meal with no main dish, the same main ingredient twice.
  They send the proposal back to the model; each one remembers its dish (bad_dishes), so if the model
  cannot fix it, the rest of the proposal can still be saved without that dish.
- notes (judgment): a usual dish that is missing (pasta + salad is a complete lunch), low variety,
  fewer ideas than usual. Information only, never a block.
The same checks run for a new plan and for a single replacement dish, so a follow-up can never
slip in something that breaks the profile.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

from chefscout.memory.profile import Profile
from chefscout.recipes.kosher import classify, fits, meal_category
from chefscout.recipes.matching import check_recipe, same_food
from chefscout.recipes.matching import similar_names as same_name
from chefscout.recipes.plan import DishDraft, Intent, MealDraft, Plan, RequestParams, template_for
from chefscout.recipes.timing import close_limit, time_fit

MAX_MEALS = 2
MAX_IDEAS = 3
Variety = Literal["high", "medium", "low"]


class Report(BaseModel):
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    variety: Variety | None = None
    variety_notes: list[str] = Field(default_factory=list)
    # The dishes (by identity, not name: two meals may share a dish name) that have an error of their own.
    _bad: list[DishDraft] = PrivateAttr(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def dish_error(self, dish: DishDraft, message: str) -> None:
        self.errors.append(message)
        if not self.is_bad(dish):
            self._bad.append(dish)

    def is_bad(self, dish: DishDraft) -> bool:
        return any(dish is b for b in self._bad)

    @property
    def bad_names(self) -> list[str]:
        return [b.name for b in self._bad]

    def text(self) -> str:
        """Short summary for the model."""
        parts = [f"ERROR: {e}" for e in self.errors] + [f"note: {w}" for w in self.warnings]
        if self.variety:
            why = f" ({'; '.join(self.variety_notes)})" if self.variety_notes else ""
            parts.append(f"variety: {self.variety}{why}")
        return "\n".join(parts)


# ---- one dish ------------------------------------------------------------------------


def check_dish(
    d: DishDraft, request: RequestParams, profile: Profile, rejected: Iterable[str], report: Report
) -> None:
    lines = [d.name, *d.key_ingredients]
    hits = check_recipe(lines, profile, [])
    for hit in hits.blocked_by:
        report.dish_error(d, f"'{d.name}' breaks the profile: {hit.rule} ({hit.ingredient})")
    for hit in hits.needs_substitute:
        report.warnings.append(f"'{d.name}' needs a substitute for {hit.ingredient} [{hit.rule}]")

    k = classify(lines)
    if k.category == "mixed":
        report.dish_error(d, f"'{d.name}' mixes meat and dairy")
    elif k.category in ("meat", "dairy") and d.kosher != k.category:
        found = (k.meat or k.dairy or k.optional_dairy)[0]
        report.dish_error(d, f"'{d.name}' is marked {d.kosher} but has {found} ({k.category})")
    if not fits(request.kosher, d.kosher):
        report.dish_error(d, f"'{d.name}' is {d.kosher}, but the request is {request.kosher}")

    fit = time_fit(d.est_minutes, request.max_minutes)
    if fit == "over":
        report.dish_error(
            d,
            f"'{d.name}' takes about {d.est_minutes} min; the user asked for up to {request.max_minutes} "
            f"(at most {close_limit(request.max_minutes)})",
        )
    elif fit == "close":
        report.warnings.append(f"'{d.name}' is a bit over the time ({d.est_minutes} min)")

    for name in rejected:
        if same_name(d.name, name):
            report.dish_error(d, f"'{d.name}' was rejected by the user recently. suggest something else")
            break


def _unique_names(dishes: list[DishDraft], report: Report) -> None:
    for i, a in enumerate(dishes):
        for b in dishes[i + 1 :]:
            if same_name(a.name, b.name):
                report.dish_error(b, f"'{a.name}' and '{b.name}' are the same dish. make them different")


# ---- one meal ------------------------------------------------------------------------


def _main_of(dishes: list[DishDraft]) -> DishDraft | None:
    return next((d for d in dishes if d.role in ("main", "full_meal")), None)


def check_meal(meal: MealDraft, request: RequestParams, report: Report) -> None:
    template = template_for(request.meal_time)
    roles = [d.role for d in meal.dishes]
    covered = set(roles) | ({"main", "side"} if "full_meal" in roles else set())
    missing = [r for r in template.required if r not in covered]
    if missing:
        report.errors.append(f"meal '{meal.title}' needs: {', '.join(missing)}")
    usual = [r for r in template.suggested if r not in covered]
    if usual:
        report.warnings.append(
            f"meal '{meal.title}' has no {', '.join(usual)}; fine if the meal is complete as it is"
        )
    doubled = sorted({r for r in roles if roles.count(r) > 1})
    if doubled or ("full_meal" in roles and "main" in roles):
        report.errors.append(f"meal '{meal.title}' has more than one {', '.join(doubled) or 'main dish'}")
    extra = [
        r
        for r in roles
        if r not in {*template.required, *template.suggested, *template.optional, "full_meal"}
    ]
    if extra:
        report.warnings.append(f"meal '{meal.title}' adds {', '.join(extra)}")

    kinds = [d.kosher for d in meal.dishes]
    if meal_category(kinds) == "mixed":  # the meal's own kind is derived from its dishes
        report.errors.append(f"meal '{meal.title}' mixes meat and dairy dishes")
    if request.kosher in ("meat", "dairy") and request.kosher not in kinds:
        report.errors.append(f"meal '{meal.title}' has no {request.kosher} dish, but the user asked for one")


# ---- variety -------------------------------------------------------------------------


def _overlap(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    shared = sum(1 for x in a if any(same_food(x, y) for y in b))
    return shared / min(len(a), len(b))


def _variety(pairs: list[tuple[DishDraft, DishDraft, str, str]], report: Report) -> None:
    """Soft signals between two dishes (or meals): method, style, shared ingredients."""
    for a, b, style_a, style_b in pairs:
        if a.method == b.method:
            report.variety_notes.append(f"'{a.name}' and '{b.name}' are both {a.method}")
        if style_a and style_b and style_a == style_b:
            report.variety_notes.append(f"same style: {style_a}")
        if _overlap(a.key_ingredients, b.key_ingredients) >= 0.5:
            report.variety_notes.append(f"'{a.name}' and '{b.name}' share many ingredients")
    n = len(report.variety_notes)
    report.variety = "high" if n == 0 else "medium" if n == 1 else "low"


# ---- whole proposal --------------------------------------------------------------------


def validate_ideas(
    intent: Intent,
    request: RequestParams,
    profile: Profile,
    meals: list[MealDraft] | None = None,
    ideas: list[DishDraft] | None = None,
    rejected: Iterable[str] = (),
) -> Report:
    report = Report()
    rejected = list(rejected)
    meals, ideas = meals or [], ideas or []

    if intent == "meal":
        if ideas or not meals:
            report.errors.append("a meal request needs meals (not single ideas)")
        if len(meals) > MAX_MEALS:
            report.errors.append(f"propose at most {MAX_MEALS} meals")
        elif len(meals) == 1:
            report.warnings.append("the user gets 2 meals by default; only 1 was proposed")
        for meal in meals:
            check_meal(meal, request, report)
        dishes = [d for m in meals for d in m.dishes]
        mains = [(m, d) for m in meals if (d := _main_of(m.dishes))]
        for i, (_, a) in enumerate(mains):
            for _, b in mains[i + 1 :]:
                if same_food(a.base, b.base):
                    report.dish_error(
                        b,
                        f"both meals are based on {a.base} ('{a.name}', '{b.name}'). "
                        "change one main ingredient",
                    )
        if len(mains) == 2:
            (ma, a), (mb, b) = mains
            _variety([(a, b, ma.style, mb.style)], report)
    else:
        if meals or not ideas:
            report.errors.append("this request needs single ideas (not meals)")
        if len(ideas) > MAX_IDEAS:
            report.errors.append(f"propose at most {MAX_IDEAS} ideas")
        elif intent != "recipe" and len(ideas) < MAX_IDEAS:
            report.warnings.append(f"the user gets {MAX_IDEAS} ideas by default; {len(ideas)} proposed")
        dishes = ideas
        for i, a in enumerate(ideas):
            for b in ideas[i + 1 :]:
                if same_food(a.base, b.base):
                    report.dish_error(b, f"'{a.name}' and '{b.name}' are both based on {a.base}")
        if len(ideas) > 1:
            _variety([(a, b, "", "") for i, a in enumerate(ideas) for b in ideas[i + 1 :]], report)

    for d in dishes:
        check_dish(d, request, profile, rejected, report)
    _unique_names(dishes, report)
    return report


def validate_replacement(
    plan: Plan, dish_id: str, draft: DishDraft, profile: Profile, rejected: Iterable[str] = ()
) -> Report:
    """One new dish in place of an existing one. The rest of the plan is not checked again."""
    report = Report()
    old = plan.dish(dish_id)
    if old is None:
        report.errors.append(f"unknown dish id '{dish_id}'")
        return report
    if not old.active:
        report.errors.append(f"{dish_id} is already {old.status}")
    if draft.role != old.role:
        report.errors.append(f"replace {dish_id} with the same kind of dish ({old.role})")

    check_dish(draft, plan.request, profile, rejected, report)

    meal = plan.meal_of(dish_id)
    siblings = meal.active_dishes() if meal else [d for d in plan.ideas if d.active]
    others = [d for d in siblings if d.id != dish_id]
    if same_name(draft.name, old.name):
        report.errors.append(f"'{draft.name}' is the dish being replaced. suggest a different one")
    for d in [d for d in plan.all_dishes() if d.active and d.id != dish_id]:
        if same_name(draft.name, d.name):
            report.errors.append(f"'{draft.name}' is already in the plan ({d.id})")

    if meal is not None:
        category = meal_category([*(d.kosher for d in others), draft.kosher])
        if category == "mixed":
            report.errors.append(f"'{draft.name}' ({draft.kosher}) would mix meat and dairy in {meal.id}")
        if draft.role in ("main", "full_meal"):
            for m in plan.meals:
                other_main = _main_of(m.active_dishes()) if m.id != meal.id else None
                if other_main and same_food(draft.base, other_main.base):
                    report.errors.append(f"{m.id} is already based on {other_main.base}")
    else:
        for d in others:
            if same_food(draft.base, d.base):
                report.errors.append(f"'{d.name}' is already based on {draft.base}")
    return report
