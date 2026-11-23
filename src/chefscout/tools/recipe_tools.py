"""Recipe tools: read real recipe pages, and choose which ones to show as cards."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from chefscout.core.errors import ToolError
from chefscout.core.tools import Tool, ToolContext
from chefscout.recipes.jsonld import NoRecipeData, parse_recipe
from chefscout.recipes.matching import check_recipe
from chefscout.recipes.models import CheckedRecipe

log = logging.getLogger(__name__)

MAX_URLS = 6
MAX_SHOWN = 3


def _short(line: str, n: int = 40) -> str:
    return line if len(line) <= n else line[: n - 1] + "…"


def _summary(item: CheckedRecipe) -> str:
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
    return "|".join(parts)


def read_recipes(args: dict[str, Any], ctx: ToolContext) -> str:
    urls = list(dict.fromkeys(u.strip() for u in args.get("urls", []) if u and u.strip()))
    if not urls:
        raise ToolError("give 1-6 recipe page urls from the search results")
    urls = urls[:MAX_URLS]
    fetcher = ctx.services["fetcher"]
    profile = ctx.services["profiles"].load(ctx.user_id)
    pantry = ctx.services["pantry"].items(ctx.user_id, ctx.conversation_id)
    store = ctx.services["recipes"]

    def one(url: str) -> str:
        try:
            recipe = parse_recipe(fetcher.get(url), url)
        except NoRecipeData:
            return f"-|{url}|SKIPPED: no recipe data on page"
        except ToolError as e:
            return f"-|{url}|FAILED: {e}"
        item = store.add(
            ctx.user_id, ctx.conversation_id, recipe, check_recipe(recipe.ingredients, profile, pantry)
        )
        return _summary(item)

    with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
        lines = list(pool.map(one, urls))
    return "id|title|time|site|status|details\n" + "\n".join(lines)


def show_recipes(args: dict[str, Any], ctx: ToolContext) -> str:
    picks = args.get("recipes") or []
    if not picks or len(picks) > MAX_SHOWN:
        raise ToolError(f"choose 1-{MAX_SHOWN} recipes")
    store = ctx.services["recipes"]
    notes: dict[str, str] = {}
    for p in picks:
        rid = str(p.get("id", "")).strip().upper()
        item = store.get(ctx.user_id, ctx.conversation_id, rid)
        if item is None:
            raise ToolError(f"unknown recipe id '{rid}'. use ids from read_recipes")
        if item.check.status == "blocked":
            raise ToolError(f"{rid} is blocked by the user's profile and cannot be shown")
        notes[rid] = " ".join(str(p.get("note", "")).split())[:200]
    shown = store.mark_shown(ctx.user_id, ctx.conversation_id, notes)
    return "shown as cards (title, link, time, missing): " + ", ".join(
        f"{s.id} {s.recipe.title}" for s in shown
    )


TOOLS = [
    Tool(
        name="read_recipes",
        description=(
            "Open recipe pages found with web_search and read the real recipe (title, ingredients, time). "
            "The code checks each recipe against the user's allergies and never-eat list (BLOCKED recipes "
            "must not be suggested), flags kitchen-rule ingredients that need a substitute, and lists what "
            "is missing compared with the pantry. Pass 3-6 promising recipe page urls (not home pages)."
        ),
        input_schema={
            "type": "object",
            "properties": {"urls": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_URLS}},
            "required": ["urls"],
        },
        handler=read_recipes,
    ),
    Tool(
        name="show_recipes",
        description=(
            "Show up to 3 recipes from read_recipes to the user as cards with title, photo, time, link and "
            "missing items. Call it once before your final answer. note = one short line in the user's "
            "language, e.g. what is missing and what to use instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "recipes": {
                    "type": "array",
                    "maxItems": MAX_SHOWN,
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
                        "required": ["id"],
                    },
                }
            },
            "required": ["recipes"],
        },
        handler=show_recipes,
    ),
]
