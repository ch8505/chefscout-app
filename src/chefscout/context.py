"""Context providers: what memory goes into each turn (and what stays out).

- The profile's hard rules go in every turn: a wrong recipe is worse than a few tokens.
- Recently loved recipes go in only when the message is about cooking something again.
- The pantry goes in only when it is not empty.
"""

from __future__ import annotations

import re

from chefscout.core.tools import ToolContext
from chefscout.memory.pantry import PantryStore
from chefscout.memory.profile import ProfileStore

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
