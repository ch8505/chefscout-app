"""Idea tools: propose meals or dishes without a web search, swap one dish, choose what to search for.

    propose_ideas  -> the gate (recipes/validate.py) -> a saved plan with stable ids
    replace_dish   -> the same gate for one dish; everything else in the plan stays
    select_dishes  -> marks the chosen meal or dishes and says what to search for
    more_options   -> more real recipes for one dish (from those already read, or one new search)

Nothing here searches the web or shows a link: an idea is not a recipe.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from chefscout.core.errors import ToolError
from chefscout.core.tools import Tool, ToolContext
from chefscout.recipes.plan import Dish, DishDraft, MealDraft, Plan, PlanStore, RequestParams
from chefscout.recipes.validate import validate_ideas, validate_replacement

MAX_FIXES = 1  # one corrected proposal per turn, so a bad plan cannot loop and cost money


class _Proposal(BaseModel):
    intent: str
    request: RequestParams = Field(default_factory=RequestParams)
    meals: list[MealDraft] = Field(default_factory=list)
    ideas: list[DishDraft] = Field(default_factory=list)


def _parse(model: type[BaseModel], data: Any) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:4]
        )
        raise ToolError(f"invalid input: {problems}") from None


def _plans(ctx: ToolContext) -> PlanStore:
    return ctx.services["plans"]


def _rejected(ctx: ToolContext) -> list[str]:
    days = ctx.services["settings"].rejected_block_days
    return ctx.services["history"].rejected_names(ctx.user_id, days)


def _id(value: Any) -> str:
    return str(value or "").strip().upper()


def _dish_line(d: Dish) -> str:
    time = f"~{d.est_minutes} min" if d.est_minutes else "?"
    return f"{d.id} {d.role}: {d.name} ({d.kosher}, {time})"


def plan_summary(plan: Plan) -> str:
    """Short text of the active plan, for the model."""
    lines = []
    for m in plan.meals:
        lines.append(f"{m.id} {m.title} [{m.kosher}]")
        lines += [f"  {_dish_line(d)}" for d in m.active_dishes()]
    lines += [_dish_line(d) for d in plan.ideas if d.active]
    return "\n".join(lines)


def _guard_retries(ctx: ToolContext, report_text: str) -> None:
    fails = ctx.turn.get("failed_proposals", 0) + 1
    ctx.turn["failed_proposals"] = fails
    if fails > MAX_FIXES:
        raise ToolError(
            report_text + "\nSTOP: do not propose again in this turn. Tell the user briefly what is not "
            "possible (or offer fewer dishes) in plain words."
        )
    raise ToolError(report_text + "\nfix these and call propose_ideas once more")


# ---- tools ----------------------------------------------------------------------------


def propose_ideas(args: dict[str, Any], ctx: ToolContext) -> str:
    p: _Proposal = _parse(_Proposal, args)
    if p.intent not in ("meal", "dish_ideas", "pantry", "vague", "recipe"):
        raise ToolError("intent must be meal, dish_ideas, pantry, vague or recipe")
    profile = ctx.services["profiles"].load(ctx.user_id)
    report = validate_ideas(p.intent, p.request, profile, p.meals, p.ideas, rejected=_rejected(ctx))  # type: ignore[arg-type]
    if not report.ok:
        _guard_retries(ctx, report.text())

    plan = _plans(ctx).new_plan(
        ctx.user_id,
        ctx.conversation_id,
        p.intent,
        p.request,
        meals=p.meals,
        ideas=p.ideas,  # type: ignore[arg-type]
    )
    history = ctx.services["history"]
    for d in plan.all_dishes():
        history.record(ctx.user_id, "dish", d.name, "suggested", ref=f"{ctx.conversation_id}:v{plan.version}")

    if p.intent == "recipe":  # a specific dish: go straight to the search
        picked = _plans(ctx).select(ctx.user_id, ctx.conversation_id, dish_ids=[d.id for d in plan.ideas])
        return "saved. search now for:\n" + "\n".join(f"{d.id}: {d.query or d.name}" for d in picked)

    ctx.turn["ideas_only"] = True  # no recipe reading and no links in this turn
    notes = f"\n{report.text()}" if report.text() else ""
    return (
        f"saved ideas (shown to the user as idea cards, no links):\n{plan_summary(plan)}{notes}\n"
        "Now answer briefly. Do NOT search yet: the user chooses first (select_dishes)."
    )


def replace_dish(args: dict[str, Any], ctx: ToolContext) -> str:
    dish_id = _id(args.get("dish_id"))
    reason = args.get("reason") or "user"
    if reason not in ("user", "rejected", "not_found"):
        raise ToolError("reason must be user, rejected or not_found")
    draft: DishDraft = _parse(DishDraft, args.get("dish"))
    store = _plans(ctx)
    plan = store.load(ctx.user_id, ctx.conversation_id)
    if plan is None:
        raise ToolError("there is no plan yet. propose ideas first")
    old = plan.dish(dish_id)
    if reason == "not_found" and old is not None and old.reason == "not_found":
        raise ToolError(
            f"{dish_id} already replaced a dish with no recipe. do not try again: tell the user that no real "
            "recipe was found for it and suggest a simple option from home (e.g. bread or rice)"
        )
    profile = ctx.services["profiles"].load(ctx.user_id)
    report = validate_replacement(plan, dish_id, draft, profile, rejected=_rejected(ctx))
    if not report.ok:
        _guard_retries(ctx, report.text())

    new = store.replace(ctx.user_id, ctx.conversation_id, dish_id, draft, reason=reason)
    status = "rejected" if reason == "rejected" else "replaced"
    ctx.services["history"].record(ctx.user_id, "dish", old.name, status, ref=ctx.conversation_id)
    ctx.services["history"].record(ctx.user_id, "dish", new.name, "suggested", ref=ctx.conversation_id)

    out = f"{dish_id} -> {new.id} {new.role}: {new.name}. every other dish stays as it is."
    if new.status == "selected":
        out += f" search only for {new.id}: {new.query or new.name}"
    if reason == "rejected":
        out += " (the old dish is blocked for a while; this is not 'never')"
    return out


def select_dishes(args: dict[str, Any], ctx: ToolContext) -> str:
    meal_id = _id(args.get("meal_id")) or None
    dish_ids = [_id(d) for d in args.get("dish_ids") or []]
    picked = _plans(ctx).select(ctx.user_id, ctx.conversation_id, meal_id=meal_id, dish_ids=dish_ids)
    lines = []
    for d in picked:
        if d.search_status == "found":
            lines.append(f"{d.id}: {d.name} - already has recipe {d.recipe_id}, no new search")
        else:
            lines.append(f"{d.id}: {d.name} - search: {d.query or 'מתכון ' + d.name}")
    return "selected:\n" + "\n".join(lines) + "\nsearch only for the dishes that need it, then read_recipes."


def more_options(args: dict[str, Any], ctx: ToolContext) -> str:
    dish_id = _id(args.get("dish_id"))
    plan = _plans(ctx).load(ctx.user_id, ctx.conversation_id)
    dish = plan.dish(dish_id) if plan else None
    if dish is None or not dish.active:
        raise ToolError(f"unknown or inactive dish id '{dish_id}'")
    unseen = [r for r in dish.candidates if r not in dish.shown_recipes]
    if unseen:
        return (
            f"recipes already read for {dish.id} and not shown yet: {', '.join(unseen[:2])}. "
            "show them with show_recipes. no search needed."
        )
    ctx.turn.setdefault("more_options", set()).add(dish.id)
    return (
        f"no unread options left for {dish.id}. search once more for this dish only, with different words "
        f"than '{dish.query or dish.name}', and skip urls already read. the rest of the plan stays."
    )


# ---- schemas --------------------------------------------------------------------------

_DISH = {
    "type": "object",
    "properties": {
        "role": {"type": "string", "enum": ["main", "side", "salad", "soup", "dessert", "full_meal"]},
        "name": {"type": "string", "description": "A common Hebrew dish name found on recipe sites."},
        "base": {"type": "string", "description": "The ONE main ingredient, e.g. עוף / עדשים / פסטה."},
        "key_ingredients": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "method": {
            "type": "string",
            "enum": ["oven", "stovetop", "grill", "fried", "raw", "no_cook", "slow"],
        },
        "kosher": {"type": "string", "enum": ["meat", "dairy", "parve"]},
        "est_minutes": {"type": "integer"},
        "query": {"type": "string", "description": "Hebrew search query, e.g. 'מתכון מג׳דרה'."},
    },
    "required": ["role", "name", "base", "key_ingredients", "method", "kosher"],
}
_REQUEST = {
    "type": "object",
    "properties": {
        "meal_time": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "shabbat", "any"]},
        "kosher": {"type": "string", "enum": ["meat", "dairy", "parve", "any"]},
        "max_minutes": {"type": "integer"},
        "servings": {"type": "integer"},
        "use_pantry": {"type": "boolean"},
    },
}

TOOLS = [
    Tool(
        name="propose_ideas",
        description=(
            "Save ideas BEFORE any web search. intent: meal = a whole meal ('מה לבשל לצהריים', 'ארוחה "
            "לשבת') -> 2 different meals, each with the dishes the meal needs; dish_ideas = 'ideas for a "
            "main dish / something light' -> 3 different dishes; pantry = from what is at home -> 3 dishes; "
            "vague = 'something tasty' -> 3 different directions; recipe = one specific dish the user named "
            "('a chocolate cake recipe') -> 1 idea, then search right away. The code checks the profile, "
            "kashrut (never meat with dairy), time and variety, and returns ids."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["meal", "dish_ideas", "pantry", "vague", "recipe"]},
                "request": _REQUEST,
                "meals": {
                    "type": "array",
                    "maxItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "style": {"type": "string"},
                            "dishes": {"type": "array", "items": _DISH, "maxItems": 5},
                        },
                        "required": ["title", "dishes"],
                    },
                },
                "ideas": {"type": "array", "items": _DISH, "maxItems": 3},
            },
            "required": ["intent"],
        },
        handler=propose_ideas,
    ),
    Tool(
        name="replace_dish",
        description=(
            "Swap ONE dish (by id) for a new dish of the same role. Every other dish, id and recipe stays. "
            "reason: user = the user asked to swap it (not a dislike); rejected = they said they do not like "
            "it; not_found = no real recipe was found for it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "dish_id": {"type": "string"},
                "reason": {"type": "string", "enum": ["user", "rejected", "not_found"]},
                "dish": _DISH,
            },
            "required": ["dish_id", "dish"],
        },
        handler=replace_dish,
    ),
    Tool(
        name="select_dishes",
        description=(
            "The user chose: a whole meal (meal_id, e.g. M1 -> all its dishes) or single dishes (dish_ids). "
            "Returns what to search for. Dishes that already have a recipe are not searched again."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "meal_id": {"type": "string"},
                "dish_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        handler=select_dishes,
    ),
    Tool(
        name="more_options",
        description="The user wants more recipes for ONE dish only. Says whether a new search is needed.",
        input_schema={
            "type": "object",
            "properties": {"dish_id": {"type": "string"}},
            "required": ["dish_id"],
        },
        handler=more_options,
    ),
]
