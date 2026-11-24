"""Meat, dairy or parve: is a dish or recipe בשרי, חלבי or פרווה?

Kashrut is always kept: a meal never mixes meat and dairy, and a recipe that has both is never shown.
Words come from data/food_terms.json ("בשרי" / "חלבי"). Plant-based products ("חלב קוקוס",
"שמנת צמחית") and look-alikes ("חלבון" = egg white) are parve and are removed first.
When in doubt the answer is never parve: "חמאה או מרגרינה" counts as dairy, with a warning.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from chefscout.recipes.matching import food_terms, mentions, strip_term

Category = Literal["meat", "dairy", "parve", "mixed"]

# Parve even though they contain a meat or dairy word.
PARVE_PHRASES = [
    "חלב קוקוס",
    "קרם קוקוס",
    "שמנת קוקוס",
    "חלב שקדים",
    "חלב סויה",
    "חלב שיבולת שועל",
    "חלב אורז",
    "שמנת צמחית",
    "שמנת צמחית להקצפה",
    "גבינה טבעונית",
    "גבינה צמחית",
    "חמאה צמחית",
    "חמאת בוטנים",
    "חמאת שקדים",
    "חמאת קקאו",
    "בשר צמחי",
    "עוף צמחי",
    "נקניק צמחי",
    "שניצל תירס",
    "שניצל צמחי",
    "חלבון",
    "חלבונים",
    "coconut milk",
    "almond milk",
    "soy milk",
    "peanut butter",
]
# A word that makes the whole line plant-based ("נקניקיות טבעוניות", "גבינה פרווה").
PLANT_MARKERS = ["צמחי", "טבעוני", "פרווה", "vegan", "parve", "plant"]
# "חמאה או מרגרינה": the dairy word is only one option.
_OR = re.compile(r"\s(?:או|or)\s")  # not "/": "1/2 כוס חמאה" is an amount


class KosherCheck(BaseModel):
    category: Category
    meat: list[str] = Field(default_factory=list)  # lines with meat
    dairy: list[str] = Field(default_factory=list)  # lines with dairy
    optional_dairy: list[str] = Field(default_factory=list)  # "חמאה או מרגרינה"


def _terms(key: str) -> list[str]:
    return [key, *food_terms().get(key, [])]


def _clean(line: str) -> str:
    """The line without plant-based look-alikes, or "" if the whole line is plant-based."""
    if any(mentions(line, m) for m in PLANT_MARKERS):
        return ""
    for phrase in PARVE_PHRASES:
        if mentions(line, phrase):
            line = strip_term(line, phrase)
    return line


def classify(lines: list[str]) -> KosherCheck:
    meat_words, dairy_words = _terms("בשרי"), _terms("חלבי")
    meat, dairy, optional = [], [], []
    for line in lines:
        cleaned = _clean(line)
        if not cleaned:
            continue
        if any(mentions(cleaned, w) for w in meat_words):
            meat.append(line)
        if any(mentions(cleaned, w) for w in dairy_words):
            # "חמאה או מרגרינה": the dairy word is only one of the options.
            options = [o for o in _OR.split(cleaned) if o.strip()]
            if len(options) > 1 and not all(any(mentions(o, w) for w in dairy_words) for o in options):
                optional.append(line)
            else:
                dairy.append(line)
    if meat and dairy:
        category: Category = "mixed"
    elif meat:
        category = "meat"  # optional dairy in a meat dish: use the non-dairy option
    elif dairy or optional:
        category = "dairy"  # in doubt, never parve
    else:
        category = "parve"
    return KosherCheck(category=category, meat=meat, dairy=dairy, optional_dairy=optional)


def fits(meal: str, dish: str) -> bool:
    """Can a dish of this kind be part of a meal of that kind?"""
    if dish == "mixed":
        return False
    if meal in ("any", dish):
        return True
    return dish == "parve"


def meal_category(dishes: list[str]) -> Category:
    kinds = set(dishes)
    if "mixed" in kinds or {"meat", "dairy"} <= kinds:
        return "mixed"
    if "meat" in kinds:
        return "meat"
    if "dairy" in kinds:
        return "dairy"
    return "parve"
