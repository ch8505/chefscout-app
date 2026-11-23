"""Tools that change what ChefScout remembers. Reading needs no tool: it comes in the context."""

from __future__ import annotations

from typing import Any

from chefscout.core.tools import Tool, ToolContext
from chefscout.memory.pantry import PantryStore
from chefscout.memory.profile import PREFERENCE_KINDS, ProfileStore


def _profiles(ctx: ToolContext) -> ProfileStore:
    return ctx.services["profiles"]


def _pantry(ctx: ToolContext) -> PantryStore:
    return ctx.services["pantry"]


def remember_preference(args: dict[str, Any], ctx: ToolContext) -> str:
    return _profiles(ctx).remember(ctx.user_id, args["kind"], args["value"])


def forget_preference(args: dict[str, Any], ctx: ToolContext) -> str:
    return _profiles(ctx).forget(ctx.user_id, args["kind"], args.get("value", ""))


def update_pantry(args: dict[str, Any], ctx: ToolContext) -> str:
    items = _pantry(ctx).update(
        ctx.user_id,
        ctx.conversation_id,
        add=args.get("add"),
        remove=args.get("remove"),
        clear=bool(args.get("clear", False)),
    )
    return f"pantry ({len(items)}): {', '.join(items) or 'empty'}"


def save_cooked_recipe(args: dict[str, Any], ctx: ToolContext) -> str:
    return _profiles(ctx).add_cooked(
        ctx.user_id, args["title"], url=args.get("url"), rating=args.get("rating"), note=args.get("note", "")
    )


def get_cooked_recipes(args: dict[str, Any], ctx: ToolContext) -> str:
    items = _profiles(ctx).cooked(ctx.user_id, only_loved=bool(args.get("only_loved", False)))
    if not items:
        return "no cooked recipes saved"
    return "\n".join(
        f"{c.cooked_on}|{c.title}|{c.rating or '-'}|{c.url or '-'}" + (f"|{c.note}" if c.note else "")
        for c in items
    )


_LIST = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    Tool(
        name="remember_preference",
        description=(
            "Save a LASTING fact to the user's profile, kept across all conversations: an allergy, "
            "a food they never want (avoid), their diet, a kitchen rule (e.g. 'no margarine'), or a like. "
            "Call it only when the user clearly means it permanently ('always', 'never', 'I am allergic'). "
            "If it is unclear whether they mean always or only for now, ASK the user first and do not call "
            "this tool. What they have at home now goes to update_pantry, never here."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": PREFERENCE_KINDS},
                "value": {"type": "string", "description": "Short, in the user's language, e.g. 'טונה'."},
            },
            "required": ["kind", "value"],
        },
        handler=remember_preference,
    ),
    Tool(
        name="forget_preference",
        description="Remove a lasting fact from the profile when the user says it no longer applies.",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": PREFERENCE_KINDS},
                "value": {"type": "string", "description": "The saved value. Not needed for diet."},
            },
            "required": ["kind"],
        },
        handler=forget_preference,
    ),
    Tool(
        name="update_pantry",
        description=(
            "Update what the user has at home RIGHT NOW. Kept for this conversation only. "
            "Use add/remove for changes, clear=true to start over."
        ),
        input_schema={
            "type": "object",
            "properties": {"add": _LIST, "remove": _LIST, "clear": {"type": "boolean"}},
        },
        handler=update_pantry,
    ),
    Tool(
        name="save_cooked_recipe",
        description=(
            "Record that the user cooked a recipe, with their rating if they gave one "
            "(loved / ok / disliked). Use the exact title and url of a recipe shown earlier."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "url": {"type": "string"},
                "rating": {"type": "string", "enum": ["loved", "ok", "disliked"]},
                "note": {"type": "string"},
            },
            "required": ["title"],
        },
        handler=save_cooked_recipe,
    ),
    Tool(
        name="get_cooked_recipes",
        description="List recipes the user cooked before (newest first): date|title|rating|url.",
        input_schema={"type": "object", "properties": {"only_loved": {"type": "boolean"}}},
        handler=get_cooked_recipes,
    ),
]
