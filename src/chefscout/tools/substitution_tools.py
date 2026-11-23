"""Substitute tool: what to use instead of a missing or not-allowed ingredient, and how much."""

from __future__ import annotations

from typing import Any

from chefscout.core.errors import ToolError
from chefscout.core.tools import Tool, ToolContext
from chefscout.recipes.substitutions import suggest


def suggest_substitute(args: dict[str, Any], ctx: ToolContext) -> str:
    ingredient = " ".join(str(args.get("ingredient", "")).split())
    if not ingredient:
        raise ToolError("ingredient must not be empty")
    profile = ctx.services["profiles"].load(ctx.user_id)
    return suggest(ingredient, args.get("amount"), profile)


TOOLS = [
    Tool(
        name="suggest_substitute",
        description=(
            "Verified substitutes for an ingredient the user lacks or must not use (e.g. a kitchen rule "
            "like 'no margarine'), with scaled amounts. Options that break the user's allergies, never-eat "
            "list or kitchen rules are removed. Pass the ingredient line from read_recipes, "
            "e.g. '200 גרם מרגרינה'. If the table has nothing, it says so."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ingredient": {"type": "string"},
                "amount": {"type": "string", "description": "Optional, e.g. '200 גרם' or '1/2 כוס'."},
            },
            "required": ["ingredient"],
        },
        handler=suggest_substitute,
    ),
]
