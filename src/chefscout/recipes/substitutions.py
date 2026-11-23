"""Kitchen substitutes from a curated table, with scaled amounts, filtered by the user's profile.

"200 גרם מרגרינה" -> "150 גרם שמן קנולה או חמניות (¾ מהכמות)"
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from chefscout.memory.profile import Profile
from chefscout.recipes.matching import expand, mentions, rule_foods

_TABLE_FILE = Path(__file__).resolve().parent.parent / "data" / "substitutions.json"
_UNICODE_FRACTIONS = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3"}
# A plain fraction must be tried first, or "1/2" is read as "1".
_AMOUNT = re.compile(r"^\s*(\d+/\d+|\d+(?:[.,]\d+)?(?:\s+\d+/\d+)?)\s*(.*)$")
# Only these words count as a unit; anything else is the ingredient name, not part of the amount.
_UNITS = {
    "גרם",
    "ג",
    "ג'",
    "קילו",
    'ק"ג',
    "קג",
    "כוס",
    "כוסות",
    "כף",
    "כפות",
    "כפית",
    "כפיות",
    'מ"ל',
    "מל",
    "ליטר",
    "חבילה",
    "חבילות",
    "קופסה",
    "קופסת",
    "קופסאות",
    "g",
    "kg",
    "ml",
    "cup",
    "cups",
    "tbsp",
    "tsp",
}
MAX_OPTIONS = 3


@dataclass(frozen=True)
class Option:
    use: str
    factor: float | None
    ratio: str
    contains: tuple[str, ...]
    note: str


@lru_cache
def table() -> dict[str, list[Option]]:
    raw = json.loads(_TABLE_FILE.read_text(encoding="utf-8"))
    return {
        key: [Option(o["use"], o["factor"], o["ratio"], tuple(o["contains"]), o["note"]) for o in options]
        for key, options in raw.items()
        if not key.startswith("_")
    }


# Other ways a recipe names a table entry (Hebrew plurals that change the ending, common names).
_ALIASES: dict[str, list[str]] = {
    "ביצה": ["ביצים"],
    "מיץ לימון": ["לימון"],
    "פלפל אדום": ["גמבה"],
    "גבינה צהובה": ["צהובה מגוררת"],
}


def find_entry(ingredient: str) -> str | None:
    """The table key this ingredient line is about; the most specific match wins."""
    best: tuple[int, str] | None = None
    for key in table():
        for term in [key, *_ALIASES.get(key, [])]:
            if mentions(ingredient, term) and (best is None or len(term) > best[0]):
                best = (len(term), key)
    return best[1] if best else None


def parse_amount(text: str) -> tuple[Fraction, str] | None:
    """ "200 גרם" -> (200, "גרם"); "1 1/2 כוס" -> (3/2, "כוס"); "½ כוס" -> (1/2, "כוס")."""
    for sym, frac in _UNICODE_FRACTIONS.items():
        text = text.replace(sym, f" {frac}")
    m = _AMOUNT.match(text.strip())
    if not m:
        return None
    number = sum((Fraction(part.replace(",", ".")) for part in m.group(1).split()), Fraction(0))
    rest = m.group(2).split()
    unit = rest[0] if rest and rest[0].lower() in _UNITS else ""
    return number, unit


def format_amount(value: Fraction) -> str:
    whole, rest = divmod(value, 1)
    if rest == 0:
        return str(int(whole))
    nice = {
        Fraction(1, 2): "½",
        Fraction(1, 4): "¼",
        Fraction(3, 4): "¾",
        Fraction(1, 3): "⅓",
        Fraction(2, 3): "⅔",
    }
    if rest in nice:
        return f"{int(whole)} {nice[rest]}".strip() if whole else nice[rest]
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def _conflict(option: Option, profile: Profile) -> str | None:
    """The profile entry this option would break, if any.

    Allergies / never-eat: the option's curated "contains" tags (so "שמנת צמחית" is fine for a milk
    allergy), plus the entry's own name in the option text ("שמן קוקוס" for a coconut allergy).
    Kitchen rules: the foods the rule names, in the text or the tags.
    """
    for entry in [*profile.allergies, *profile.avoid]:
        related = expand(entry)
        if mentions(option.use, entry) or any(
            tag in related or entry in expand(tag) for tag in option.contains
        ):
            return entry
    for rule in profile.kitchen_rules:
        foods = rule_foods(rule)
        if any(mentions(option.use, f) for f in foods) or any(tag in foods for tag in option.contains):
            return rule
    return None


def suggest(ingredient: str, amount: str | None, profile: Profile) -> str:
    key = find_entry(ingredient)
    if key is None:
        return (
            f"no verified substitute for '{ingredient}' in the table. You may suggest a common one, "
            "say it is a general tip, and respect the profile."
        )
    parsed = parse_amount(amount) if amount else parse_amount(ingredient)
    lines, skipped = [], []
    for option in table()[key]:
        conflict = _conflict(option, profile)
        if conflict:
            skipped.append(f"{option.use} ({conflict})")
            continue
        qty = ""
        if parsed and option.factor is not None:
            number, unit = parsed
            qty = (
                f"{format_amount(number * Fraction(option.factor).limit_denominator(8))} {unit}".strip() + " "
            )
        note = f" - {option.note}" if option.note else ""
        lines.append(f"{qty}{option.use} ({option.ratio}){note}")
        if len(lines) == MAX_OPTIONS:
            break
    head = f"substitutes for {key}" + (f" ({amount or ingredient})" if parsed else "")
    if not lines:
        return f"{head}: none fit the profile (skipped: {', '.join(skipped)})"
    out = f"{head}:\n" + "\n".join(f"- {line}" for line in lines)
    if skipped:
        out += f"\nskipped (profile): {', '.join(skipped)}"
    return out
