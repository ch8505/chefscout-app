"""Recipe tools: read real recipe pages, and choose which ones to show as cards.

Every recipe is checked in code: the profile (hard rules), kashrut (never meat with dairy, also
against the rest of the meal), the time the user asked for, and explicit rejections.
Only urls that a web search returned in this conversation (or saved / cooked ones) may be read.
With a dish_id the recipe is linked to that planned dish; without one it is a stand-alone recipe.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from chefscout.core.errors import ToolError
from chefscout.core.tools import Tool, ToolContext
from chefscout.recipes.jsonld import NoRecipeData, parse_recipe
from chefscout.recipes.kosher import classify, fits
from chefscout.recipes.matching import check_recipe
from chefscout.recipes.models import CheckedRecipe, Recipe, RecipeCheck, RuleHit
from chefscout.recipes.plan import Dish, Plan
from chefscout.recipes.store import normalize_url
from chefscout.recipes.timing import time_fit

log = logging.getLogger(__name__)

MAX_URLS = 6
MAX_SHOWN = 3  # stand-alone recipes
MAX_SHOWN_FOR_DISHES = 6  # a whole meal, plus "more options"
KOSHER_RULE = "כשרות"
REJECTED_RULE = "נדחה לאחרונה"


def _short(line: str, n: int = 40) -> str:
    return line if len(line) <= n else line[: n - 1] + "…"


def _summary(item: CheckedRecipe, seen_before: bool = False) -> str:
    r, c = item.recipe, item.check
    head = f"{item.id}|{r.title}|{r.total_minutes or '?'} min|{r.site}"
    if c.status == "blocked":
        why = ", ".join(f"{h.rule} ({_short(h.ingredient, 25)})" for h in c.blocked_by)
        return f"{head}|BLOCKED: {why}"
    parts = [head, "OK"]
    if c.needs_substitute:
        parts.append(
            "substitute: " + "; ".join(f"{_short(h.ingredient)} [{h.rule}]" for h in c.needs_substitute)
        )
    parts.append(f"missing ({len(c.missing)}): " + ("; ".join(_short(m) for m in c.missing[:6]) or "none"))
    if item.time_fit not in ("any", "ok"):
        parts.append(f"time: {item.time_fit}")
    if seen_before:
        parts.append("shown to the user before (prefer another if one is as good)")
    return "|".join(parts)


# ---- context ----------------------------------------------------------------------------


def _kosher_need(plan: Plan | None, dish: Dish | None) -> str:
    """What the recipe must fit: the request, and the other dishes of the same meal."""
    if plan is None:
        return "any"
    need = plan.request.kosher if plan.request.kosher in ("meat", "dairy") else "any"
    meal = plan.meal_of(dish.id) if dish else None
    if meal is not None:
        kinds = {d.kosher for d in meal.active_dishes() if d.id != dish.id}
        if "meat" in kinds:
            need = "meat"
        elif "dairy" in kinds:
            need = "dairy"
    return need


def _allowed_urls(ctx: ToolContext) -> set[str]:
    """Search results of this conversation, recipes already read, saved and cooked ones."""
    store = ctx.services["recipes"]
    urls = store.sources(ctx.user_id, ctx.conversation_id)
    urls |= {normalize_url(u) for u in store.known_urls(ctx.user_id, ctx.conversation_id)}
    history = ctx.services.get("history")
    if history is not None:
        urls |= {normalize_url(e.url) for e in history.saved(ctx.user_id) if e.url}
    cooked = ctx.services["profiles"].load(ctx.user_id).cooked
    urls |= {normalize_url(c.url) for c in cooked if c.url}
    return urls


def _dish_for_reading(ctx: ToolContext, dish_id: str) -> tuple[Plan, Dish]:
    plan = ctx.services["plans"].load(ctx.user_id, ctx.conversation_id)
    dish = plan.dish(dish_id) if plan else None
    if dish is None or not dish.active:
        raise ToolError(f"unknown or inactive dish id '{dish_id}'")
    if dish.status != "selected":
        raise ToolError(f"{dish_id} was not chosen yet. the user chooses first (select_dishes)")
    if dish.search_status == "found" and dish.id not in ctx.turn.get("more_options", set()):
        raise ToolError(
            f"{dish_id} already has recipe {dish.recipe_id}. do not search again; "
            "use more_options if the user asked for more"
        )
    return plan, dish


# ---- read -------------------------------------------------------------------------------


def read_recipes(args: dict[str, Any], ctx: ToolContext) -> str:
    urls = list(dict.fromkeys(u.strip() for u in args.get("urls", []) if u and u.strip()))
    if not urls:
        raise ToolError("give 1-6 recipe page urls from the search results")
    if ctx.turn.get("ideas_only"):
        raise ToolError("ideas were just proposed: do not read recipes now, the user chooses first")
    urls = urls[:MAX_URLS]
    dish_id = str(args.get("dish_id") or "").strip().upper()
    plan, dish = _dish_for_reading(ctx, dish_id) if dish_id else (None, None)
    if plan is None:
        plan = (
            ctx.services["plans"].load(ctx.user_id, ctx.conversation_id) if "plans" in ctx.services else None
        )

    fetcher = ctx.services["fetcher"]
    profile = ctx.services["profiles"].load(ctx.user_id)
    pantry = ctx.services["pantry"].items(ctx.user_id, ctx.conversation_id)
    store = ctx.services["recipes"]
    history = ctx.services.get("history")
    settings = ctx.services["settings"]
    rejected = history.rejected_urls(ctx.user_id, settings.rejected_block_days) if history else set()
    seen = (
        {normalize_url(e.url) for e in history.entries(ctx.user_id)
         if e.status == "shown" and e.url and e.ref != ctx.conversation_id}
        if history else set()
    )  # fmt: skip
    allowed = _allowed_urls(ctx)
    need = _kosher_need(plan, dish)
    max_minutes = plan.request.max_minutes if plan else None

    def check(ingredients: list[str], url: str) -> tuple[RecipeCheck, str]:
        c = check_recipe(ingredients, profile, pantry)
        k = classify(ingredients)
        hits: list[RuleHit] = []
        if k.category == "mixed":
            hits.append(RuleHit(ingredient=(k.meat + k.dairy)[0], rule=f"{KOSHER_RULE}: בשר וחלב"))
        elif not fits(need, k.category):
            line = (k.meat or k.dairy or k.optional_dairy)[0]
            hits.append(
                RuleHit(
                    ingredient=line, rule=f"{KOSHER_RULE}: ארוחה {'בשרית' if need == 'meat' else 'חלבית'}"
                )
            )
        elif k.optional_dairy and k.category == "meat":
            c.needs_substitute.append(
                RuleHit(ingredient=k.optional_dairy[0], rule=f"{KOSHER_RULE}: תחליף פרווה")
            )
        if normalize_url(url) in rejected:
            hits.append(RuleHit(ingredient="-", rule=REJECTED_RULE))
        if hits:
            c.blocked_by += hits
            c.status = "blocked"
        return c, k.category

    def fetch(url: str) -> Recipe | str:
        """Network part, run in parallel: the parsed recipe, or a line saying why not."""
        if normalize_url(url) not in allowed:
            return f"-|{url}|NOT FROM SEARCH: read only urls from this conversation's web_search results"
        try:
            return parse_recipe(fetcher.get(url), url)
        except NoRecipeData:
            return f"-|{url}|SKIPPED: no recipe data on page"
        except ToolError as e:
            return f"-|{url}|FAILED: {e}"

    with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
        fetched = list(pool.map(fetch, urls))

    # Checked and saved in url order, so the ids (R1, R2 ...) do not depend on network timing.
    results: list[tuple[str, CheckedRecipe | None]] = []
    for url, recipe in zip(urls, fetched, strict=True):
        if isinstance(recipe, str):
            results.append((recipe, None))
            continue
        c, category = check(recipe.ingredients, url)
        item = store.add(
            ctx.user_id,
            ctx.conversation_id,
            recipe,
            c,
            dish_id=dish.id if dish else None,
            kosher=category,
            time_fit=time_fit(recipe.total_minutes, max_minutes),
        )
        results.append((_summary(item, normalize_url(url) in seen), item))
    lines = [line for line, _ in results]

    if dish is not None:
        good = [i.id for _, i in results if i is not None and i.check.status == "ok"]
        candidates = list(dict.fromkeys([*dish.candidates, *good]))
        status = "found" if candidates else "not_found"
        ctx.services["plans"].update_dish(
            ctx.user_id, ctx.conversation_id, dish.id, candidates=candidates, search_status=status
        )
        if status == "not_found":
            lines.append(
                f"no usable recipe for {dish.id}. replace only this dish (replace_dish reason=not_found) "
                "or tell the user. never invent a recipe or a link"
            )
    return "id|title|time|site|status|details\n" + "\n".join(lines)


# ---- show -------------------------------------------------------------------------------


def show_recipes(args: dict[str, Any], ctx: ToolContext) -> str:
    picks = args.get("recipes") or []
    for_dishes = any(p.get("dish_id") for p in picks)
    limit = MAX_SHOWN_FOR_DISHES if for_dishes else MAX_SHOWN
    if not picks or len(picks) > limit:
        raise ToolError(f"choose 1-{limit} recipes")
    store = ctx.services["recipes"]
    already = store.all_shown_ids(ctx.user_id, ctx.conversation_id)
    notes: dict[str, str] = {}
    alternatives: set[str] = set()
    links: list[tuple[str, str]] = []  # (dish_id, recipe id)
    for p in picks:
        rid = str(p.get("id", "")).strip().upper()
        item = store.get(ctx.user_id, ctx.conversation_id, rid)
        if item is None:
            raise ToolError(f"unknown recipe id '{rid}'. use ids from read_recipes")
        if item.check.status == "blocked":
            raise ToolError(f"{rid} is blocked by the user's profile and cannot be shown")
        dish_id = str(p.get("dish_id") or item.dish_id or "").strip().upper()
        if item.dish_id and dish_id != item.dish_id:
            raise ToolError(f"{rid} was read for {item.dish_id}, not for {dish_id}")
        if dish_id and rid in already:
            raise ToolError(f"{rid} was already shown in this conversation. choose another one")
        alternative = bool(p.get("alternative"))
        if item.time_fit == "over":
            if not alternative:
                raise ToolError(
                    f"{rid} takes {item.recipe.total_minutes} min, much longer than asked. "
                    "show it only with alternative=true, and only if no recipe fits the time"
                )
            in_time = [
                r for r in _other_candidates(ctx, item) if r.time_fit != "over" and r.id not in already
            ]
            if in_time:
                raise ToolError(
                    f"there are recipes in time for this dish: {', '.join(r.id for r in in_time)}"
                )
            alternatives.add(rid)
        notes[rid] = " ".join(str(p.get("note", "")).split())[:200]
        if dish_id:
            links.append((dish_id, rid))

    shown = store.mark_shown(ctx.user_id, ctx.conversation_id, notes, alternatives)
    ctx.turn["shown"] = True
    plans = ctx.services.get("plans")
    for dish_id, rid in links:
        dish = plans.load(ctx.user_id, ctx.conversation_id).dish(dish_id)
        if dish is None:
            continue
        plans.update_dish(
            ctx.user_id,
            ctx.conversation_id,
            dish_id,
            recipe_id=dish.recipe_id or rid,  # the first card stays the dish's main recipe
            shown_recipes=list(dict.fromkeys([*dish.shown_recipes, rid])),
            search_status="found",
        )
    history = ctx.services.get("history")
    if history is not None:  # "shown" is a soft variety hint, never a block
        for s in shown:
            history.record(
                ctx.user_id, "recipe", s.recipe.title, "shown", url=s.recipe.url, ref=ctx.conversation_id
            )
    return "shown as cards (title, link, time, missing): " + ", ".join(
        f"{s.id} {s.recipe.title}" + (" (alternative)" if s.id in alternatives else "") for s in shown
    )


def reveal_candidates(ctx: ToolContext, dish_id: str, limit: int = 2) -> list[CheckedRecipe]:
    """More options for one dish with no model call: good recipes already read and not shown yet.

    An empty list means a new search is needed (the caller asks the model). The in-time ones come
    first; a much longer recipe is shown only as a marked alternative.
    """
    plans, store = ctx.services["plans"], ctx.services["recipes"]
    plan = plans.load(ctx.user_id, ctx.conversation_id)
    dish = plan.dish(dish_id) if plan else None
    if dish is None or not dish.active:
        raise ToolError(f"unknown or inactive dish id '{dish_id}'")
    already = store.all_shown_ids(ctx.user_id, ctx.conversation_id)
    items = [store.get(ctx.user_id, ctx.conversation_id, rid) for rid in dish.candidates]
    unseen = [i for i in items if i and i.check.status == "ok" and i.id not in already]
    unseen.sort(key=lambda i: i.time_fit == "over")  # in time first
    picked = unseen[:limit]
    if not picked:
        return []
    alternatives = {i.id for i in picked if i.time_fit == "over"}
    shown = store.mark_shown(ctx.user_id, ctx.conversation_id, {i.id: "" for i in picked}, alternatives)
    plans.update_dish(
        ctx.user_id,
        ctx.conversation_id,
        dish.id,
        recipe_id=dish.recipe_id or picked[0].id,
        shown_recipes=list(dict.fromkeys([*dish.shown_recipes, *(i.id for i in picked)])),
        search_status="found",
    )
    history = ctx.services.get("history")
    if history is not None:
        for s in shown:
            history.record(
                ctx.user_id, "recipe", s.recipe.title, "shown", url=s.recipe.url, ref=ctx.conversation_id
            )
    return shown


def _other_candidates(ctx: ToolContext, item: CheckedRecipe) -> list[CheckedRecipe]:
    """Good recipes read for the same dish (or, without a dish, in this conversation)."""
    return [
        r
        for r in ctx.services["recipes"].items(ctx.user_id, ctx.conversation_id)
        if r.id != item.id and r.check.status == "ok" and r.dish_id == item.dish_id
    ]


TOOLS = [
    Tool(
        name="read_recipes",
        description=(
            "Open recipe pages found with web_search IN THIS CONVERSATION and read the real recipe "
            "(title, ingredients, time). The code checks each one against the user's allergies and "
            "never-eat list, kashrut (never meat with dairy, also with the rest of the meal) and recent "
            "rejections (BLOCKED recipes must not be suggested), flags kitchen-rule ingredients that need a "
            "substitute, the time asked for, and what is missing compared with the pantry. Pass 3-6 recipe "
            "page urls (not home pages). dish_id = the chosen dish these recipes are for (e.g. M1.D2)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_URLS},
                "dish_id": {"type": "string"},
            },
            "required": ["urls"],
        },
        handler=read_recipes,
    ),
    Tool(
        name="show_recipes",
        description=(
            "Show recipes from read_recipes to the user as cards with title, photo, time, link and missing "
            "items: up to 3 stand-alone recipes, or one per chosen dish (dish_id). Call it once before your "
            "final answer. note = one short line in the user's language, e.g. what is missing and what to "
            "use instead. alternative=true only for a recipe much longer than asked, when none fits the time."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "recipes": {
                    "type": "array",
                    "maxItems": MAX_SHOWN_FOR_DISHES,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "dish_id": {"type": "string"},
                            "note": {"type": "string"},
                            "alternative": {"type": "boolean"},
                        },
                        "required": ["id"],
                    },
                }
            },
            "required": ["recipes"],
        },
        handler=show_recipes,
    ),
]
