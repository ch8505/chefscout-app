"""Removes links from the answer that did not come from a recipe page we actually read."""

from __future__ import annotations

import logging
import re

from chefscout.core.tools import ToolContext
from chefscout.recipes.store import RecipeStore

log = logging.getLogger(__name__)

_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"https?://[^\s)\]>\"']+")


def link_guard(store: RecipeStore):
    def apply(ctx: ToolContext, text: str) -> str:
        known = {u.rstrip("/") for u in store.known_urls(ctx.user_id, ctx.conversation_id)}
        if ctx.turn.get("ideas_only") and not ctx.turn.get("shown"):
            known = set()  # an idea is not a recipe: an ideas answer has no links at all

        def ok(url: str) -> bool:
            return url.rstrip("/.,") in known

        def md(m: re.Match[str]) -> str:
            return m.group(0) if ok(m.group(2)) else m.group(1)

        def bare(m: re.Match[str]) -> str:
            if ok(m.group(0)):
                return m.group(0)
            log.warning("removed unverified link from answer: %s", m.group(0))
            return ""

        text = _MD_LINK.sub(md, text)
        # bare urls outside markdown links (markdown targets were already kept or removed)
        parts = re.split(r"(\[[^\]]+\]\(https?://[^)\s]+\))", text)
        return "".join(p if p.startswith("[") else _URL.sub(bare, p) for p in parts).strip()

    return apply
