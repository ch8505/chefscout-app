"""The user's explicit feedback on an idea or a recipe: save it, or reject it."""

from __future__ import annotations

import re
from typing import Any

from chefscout.core.errors import ToolError
from chefscout.core.tools import Tool, ToolContext

_RECIPE_ID = re.compile(r"^R\d+$")


def _target(args: dict[str, Any], ctx: ToolContext) -> tuple[str, str, str | None, str | None]:
    """(kind, title, url, dish_id) for a recipe id, a dish id or a free title."""
    target = str(args.get("target", "")).strip().upper()
    if target and _RECIPE_ID.match(target):
        item = ctx.services["recipes"].get(ctx.user_id, ctx.conversation_id, target)
        if item is None:
            raise ToolError(f"unknown recipe id '{target}'")
        return "recipe", item.recipe.title, item.recipe.url, None
    if target:
        plan = ctx.services["plans"].load(ctx.user_id, ctx.conversation_id)
        dish = plan.dish(target) if plan else None
        if dish is None:
            raise ToolError(f"unknown id '{target}'. use a dish id (M1.D2, D1) or a recipe id (R3)")
        return "dish", dish.name, None, dish.id
    title = " ".join(str(args.get("title", "")).split())
    if not title:
        raise ToolError("give a target id or a title")
    return "dish", title, None, None


def record_feedback(args: dict[str, Any], ctx: ToolContext) -> str:
    feedback = args.get("feedback")
    if feedback not in ("saved", "rejected"):
        raise ToolError("feedback must be saved or rejected")
    kind, title, url, dish_id = _target(args, ctx)
    ctx.services["history"].record(ctx.user_id, kind, title, feedback, url=url, ref=ctx.conversation_id)
    if feedback == "saved":
        return f"saved: {title}"
    if dish_id:
        plan = ctx.services["plans"].load(ctx.user_id, ctx.conversation_id)
        if plan.dish(dish_id).active:
            ctx.services["plans"].transition(ctx.user_id, ctx.conversation_id, dish_id, "rejected")
    days = ctx.services["settings"].rejected_block_days
    return (
        f"rejected: {title}. it will not be suggested for {days} days. this is not 'never': "
        "if the user means never, ask whether it is forever before saving it to the profile"
    )


TOOLS = [
    Tool(
        name="record_feedback",
        description=(
            "Record the user's EXPLICIT feedback on an idea or a recipe. saved = they want to keep it. "
            "rejected = they clearly said they do not like it ('לא אוהבת את זה', 'לא את זה שוב'). "
            "Not choosing an idea, or asking to swap a dish, is NOT a rejection: do not call this then. "
            "target = a dish id (M1.D2, D1) or a recipe id (R3); or give a title instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "feedback": {"type": "string", "enum": ["saved", "rejected"]},
                "target": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["feedback"],
        },
        handler=record_feedback,
    ),
]
