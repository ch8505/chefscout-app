"""Context providers: what memory goes into each turn (and what stays out).

- The profile's hard rules go in every turn: a wrong recipe is worse than a few tokens.
- Recently loved recipes go in only when the message is about cooking something again.
- The pantry goes in only when it is not empty.
- The current plan (ideas with their ids) goes in while this conversation has one,
  so a follow-up like "swap only the salad" knows what to change.
- History: explicitly rejected dishes (blocked) and recent suggestions (a variety hint only).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from chefscout.core.tools import ToolContext
from chefscout.memory.history import HistoryStore
from chefscout.memory.pantry import PantryStore
from chefscout.memory.profile import ProfileStore
from chefscout.recipes.plan import Dish, PlanStore

AGAIN = re.compile(r"שוב|אהבתי|כמו פעם|פעם קודמת|בפעם שעברה|מה הכנתי|again|loved|last time", re.IGNORECASE)


def profile_provider(profiles: ProfileStore):
    def provide(ctx: ToolContext, text: str) -> str:
        p = profiles.load(ctx.user_id)
        lines = []
        if p.allergies:
            lines.append(f"allergies (never include): {', '.join(p.allergies)}")
        if p.avoid:
            lines.append(f"never eats: {', '.join(p.avoid)}")
        if p.diet:
            lines.append(f"diet: {p.diet}")
        if p.kitchen_rules:
            lines.append(f"kitchen rules: {', '.join(p.kitchen_rules)}")
        if p.likes:
            lines.append(f"likes: {', '.join(p.likes)}")
        if AGAIN.search(text):
            loved = profiles.cooked(ctx.user_id, only_loved=True, limit=5)
            if loved:
                lines.append("loved before: " + "; ".join(f"{c.title} ({c.url or 'no link'})" for c in loved))
        body = "\n".join(lines) if lines else "(nothing saved yet)"
        return f"<profile>\n{body}\n</profile>"

    return provide


def pantry_provider(pantry: PantryStore):
    def provide(ctx: ToolContext, text: str) -> str | None:
        items = pantry.items(ctx.user_id, ctx.conversation_id)
        return f"<pantry>{', '.join(items)}</pantry>" if items else None

    return provide


def _dish(d: Dish) -> str:
    recipe = {"found": f"recipe {d.recipe_id}", "not_found": "no recipe found"}.get(d.search_status, "")
    status = d.status if d.status != "proposed" else ""
    extra = ", ".join(x for x in (status, recipe) if x)
    return f"{d.id} {d.role}: {d.name} [{d.kosher}]" + (f" ({extra})" if extra else "")


def plan_provider(plans: PlanStore):
    def provide(ctx: ToolContext, text: str) -> str | None:
        plan = plans.load(ctx.user_id, ctx.conversation_id)
        if plan is None:
            return None
        req = plan.request
        asked = [f"intent {plan.intent}", req.meal_time]
        asked += [
            f"{req.kosher}" if req.kosher != "any" else "",
            f"up to {req.max_minutes} min" if req.max_minutes else "",
        ]
        lines = [" | ".join(x for x in asked if x and x != "any")]
        for m in plan.meals:
            lines.append(f"{m.id} {m.title} [{m.kosher}]")
            lines += [f"  {_dish(d)}" for d in m.active_dishes()]
        lines += [_dish(d) for d in plan.ideas if d.active]
        return "<current_plan>\n" + "\n".join(lines) + "\n</current_plan>"

    return provide


def history_provider(history: HistoryStore, profiles: ProfileStore, settings):
    def provide(ctx: ToolContext, text: str) -> str | None:
        parts = []
        rejected = history.rejected_names(ctx.user_id, settings.rejected_block_days)
        if rejected:
            parts.append(f"<rejected>do not suggest: {', '.join(rejected[-20:])}</rejected>")
        recent = history.recently_suggested(ctx.user_id, settings.recent_suggestions_hint)
        since = (date.today() - timedelta(days=settings.cooked_soft_days)).isoformat()
        cooked = [c.title for c in profiles.cooked(ctx.user_id, limit=10) if c.cooked_on >= since]
        hints = []
        if recent:
            hints.append(f"suggested lately: {', '.join(recent)}")
        if cooked:
            hints.append(f"cooked lately: {', '.join(cooked)}")
        if hints:
            parts.append(
                "<recently_suggested>prefer other ideas when one is as good; repeating is allowed, "
                "and is fine when the user asks for it.\n" + "\n".join(hints) + "</recently_suggested>"
            )
        return "\n".join(parts) or None

    return provide
