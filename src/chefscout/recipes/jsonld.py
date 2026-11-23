"""Read a recipe from a web page's structured data (schema.org Recipe in JSON-LD).

Most recipe sites publish it for search engines. A page without it is skipped:
we never guess a recipe from free text.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlparse

from chefscout.recipes.models import Recipe

_SCRIPT = re.compile(
    r"<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
_DURATION = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")


class NoRecipeData(ValueError):
    """The page has no schema.org Recipe."""


def _is_recipe(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k.lower() == "recipe" for k in kinds)


def _walk(node: Any):
    """Yields every dict in a JSON-LD tree (handles @graph and nested lists)."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _text(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("text") or ""
    return " ".join(_TAGS.sub(" ", html.unescape(str(value or ""))).split())


def _minutes(value: Any) -> int | None:
    m = _DURATION.match(str(value or "").strip())
    if not m or not any(m.groups()):
        return None
    days, hours, minutes = (int(g) if g else 0 for g in m.groups())
    total = days * 1440 + hours * 60 + minutes
    return total or None


def _image(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url")
    return value if isinstance(value, str) and value.startswith("http") else None


def parse_recipe(page_html: str, url: str) -> Recipe:
    for block in _SCRIPT.findall(page_html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            if not _is_recipe(node):
                continue
            ingredients = [_text(i) for i in node.get("recipeIngredient") or node.get("ingredients") or []]
            ingredients = [i for i in ingredients if i]
            title = _text(node.get("name"))
            if not title or not ingredients:
                continue
            total = _minutes(node.get("totalTime"))
            if total is None:
                parts = [_minutes(node.get("prepTime")), _minutes(node.get("cookTime"))]
                total = sum(p for p in parts if p) or None
            servings = node.get("recipeYield")
            return Recipe(
                url=url,
                site=urlparse(url).netloc.removeprefix("www."),
                title=title,
                ingredients=ingredients,
                image=_image(node.get("image")),
                total_minutes=total,
                servings=_text(servings) or None,
            )
    raise NoRecipeData("no schema.org Recipe on this page")
