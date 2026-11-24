"""Does an ingredient line mention a food? Hebrew-aware, deliberately strict.

"2 כפות חמאת בוטנים" mentions "בוטנים"; "ובטונה" mentions "טונה" (Hebrew prefix letters);
"בוטן" matches "בוטנים" (final letters and plural endings).
Profile entries expand through data/food_terms.json ("אגוזים" also means שקדים, קשיו ...).
Over-blocking is acceptable; missing an allergen is not.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from chefscout.memory.profile import Profile
from chefscout.recipes.models import RecipeCheck, RuleHit

_TERMS_FILE = Path(__file__).resolve().parent.parent / "data" / "food_terms.json"
_WORD = re.compile(r"[\w'׳-]+")
_FINALS = str.maketrans("ךםןףץ", "כמנפצ")
_PREFIXES = "והבלמשכ"  # one-letter Hebrew prefixes; up to two are stripped ("וב", "שה")
_MAX_SUFFIX = 3  # plural / construct endings: ים, ות, ית ...
# Words in a kitchen rule that are not the food itself ("לא משתמשת במרגרינה" -> "מרגרינה").
_RULE_STOPWORDS = {
    "בלי",
    "ללא",
    "לא",
    "אף",
    "פעם",
    "אני",
    "משתמשת",
    "משתמש",
    "אוכלת",
    "אוכל",
    "רוצה",
    "את",
    "no",
    "without",
    "never",
    "use",
    "dont",
}
# Assumed to be in every kitchen; never reported as missing.
STAPLES = ["מלח", "פלפל שחור", "מים", "שמן", "סוכר"]


def _norm(word: str) -> str:
    return word.lower().translate(_FINALS)


def _tokens(text: str) -> list[str]:
    return [_norm(t) for t in _WORD.findall(text or "")]


def _variants(token: str) -> set[str]:
    """The token with 0, 1 or 2 Hebrew prefix letters removed."""
    out, t = {token}, token
    for _ in range(2):
        if len(t) > 2 and t[0] in _PREFIXES:
            t = t[1:]
            out.add(t)
    return out


# Plural endings (after final-letter normalization) that turn a singular into the searched word:
# recipe "בוטן" vs profile "בוטנים". Kept narrow so "לבן" (white) never matches "לבנה".
_PLURALS = ("ימ", "ות", "ית", "s", "es")


def _word_match(token: str, word: str) -> bool:
    for v in _variants(token):
        if v == word:
            return True
        # recipe word is longer: "בוטנים" / "עגבניות" contain the searched stem
        if len(word) >= 3 and v.startswith(word) and len(v) - len(word) <= _MAX_SUFFIX:
            return True
        # recipe word is the singular of the searched plural
        if len(v) >= 3 and word.startswith(v) and word[len(v) :] in _PLURALS:
            return True
    return False


def mentions(line: str, term: str) -> bool:
    """True if the words of term appear next to each other, in order, in line."""
    words, target = _tokens(line), _tokens(term)
    if not words or not target:
        return False
    return any(
        all(_word_match(words[i + j], target[j]) for j in range(len(target)))
        for i in range(len(words) - len(target) + 1)
    )


def strip_term(line: str, term: str) -> str:
    """The line without every place where term appears ("1 כוס חלב קוקוס", "חלב קוקוס" -> "1 כוס")."""
    words, target = _tokens(line), _tokens(term)
    if not target:
        return " ".join(words)
    out, i = [], 0
    while i < len(words):
        window = words[i : i + len(target)]
        if len(window) == len(target) and all(_word_match(w, t) for w, t in zip(window, target, strict=True)):
            i += len(target)
        else:
            out.append(words[i])
            i += 1
    return " ".join(out)


def similar_names(a: str, b: str) -> bool:
    """Identical or very similar dish names ("סלט ירקות" ~ "סלט ירקות קצוץ", not "סלט" ~ "סלט כרוב")."""
    ta, tb = _tokens(a), _tokens(b)
    if ta == tb:
        return True
    if min(len(ta), len(tb)) >= 2:
        return mentions(a, b) or mentions(b, a)
    return False


def same_food(a: str, b: str) -> bool:
    """True if two foods are the same thing for variety: "חזה עוף" ~ "עוף", "פרגית" ~ "שוקיים"."""
    if mentions(a, b) or mentions(b, a):
        return True
    terms = food_terms()
    for key in _SAME_FOOD_GROUPS:
        group = [key, *terms.get(key, [])]
        if any(mentions(a, t) for t in group) and any(mentions(b, t) for t in group):
            return True
    return False


# Groups whose members count as one base ingredient. "בשר" is deliberately left out:
# chicken and beef are different bases.
_SAME_FOOD_GROUPS = tuple(" ".join(_tokens(k)) for k in ("עוף", "דגים", "פירות ים"))


_NAME_STOPWORDS = {_norm(w) for w in ("עם", "של", "או", "מתכון", "and", "with")}


def named_in(text: str, name: str) -> bool:
    """True if a dish name comes from text: more than half of its words appear there.

    "עוגת שוקולד" in "מתכון לעוגת שוקולד" -> True; "בורקס גבינה" in "מתכון למאפה מלוח" -> False;
    "עוף בתנור" in "משהו עם עוף" -> False (that is a kind of dish, not a dish the user named).
    """
    words = [w for w in _tokens(name) if len(w) > 1 and w not in _NAME_STOPWORDS]
    if not words:
        return True
    hits = sum(mentions(text, w) for w in words)
    return hits * 2 > len(words)


# Words in an ingredient line that describe it but are not the food ("2 בצלים גדולים, קצוצים").
_DESCRIPTORS = {
    _norm(w)
    for w in (  # noqa: SIM905 (one readable list of Hebrew words)
        "גדול גדולה גדולים גדולות קטן קטנה קטנים קטנות בינוני בינונית בינוניים בינוניות "
        "טרי טרייה טריה טריים טריות קצוץ קצוצה קצוצים קצוצות פרוס פרוסה פרוסים פרוסות "
        "מגורר מגוררת מגוררים מגוררות כתוש כתושה כתושים כתושות שלם שלמה שלמים שלמות "
        "חתוך חתוכה חתוכים מרוסק מרוסקת מרוסקים מומס מומסת רך רכה יחידה יחידות קורט קמצוץ "
        "חופן שן שיני לפי הטעם כ- כ בערך"
    ).split()
}


def ingredient_name(line: str) -> str:
    """The food in an ingredient line, for the pantry: "2 בצלים גדולים, קצוצים" -> "בצלים".

    Amounts, units, notes in brackets and words like "קצוץ" are dropped. If nothing is left,
    the line itself is returned (trimmed).
    """
    from chefscout.recipes.substitutions import UNITS  # here, to avoid a circular import

    units = {_norm(u) for u in UNITS}
    text = re.sub(r"\([^)]*\)", " ", line or "")
    text = re.split(r",|\s-\s|\sאו\s", text)[0]
    kept = [
        t
        for t in _WORD.findall(text)
        if not any(ch.isnumeric() for ch in t) and _norm(t) not in units and _norm(t) not in _DESCRIPTORS
    ]
    return " ".join(kept).strip(" -") or " ".join((line or "").split())


@lru_cache
def food_terms() -> dict[str, list[str]]:
    data = json.loads(_TERMS_FILE.read_text(encoding="utf-8"))
    return {" ".join(_tokens(k)): v for k, v in data.items() if not k.startswith("_")}


def expand(entry: str) -> list[str]:
    """A profile entry and the words that mean it ("לבוטנים" -> בוטנים, בוטן, חמאת בוטנים ...)."""
    terms = food_terms()
    toks = _tokens(entry)
    keys = {" ".join(toks)}
    if len(toks) == 1:
        keys |= _variants(toks[0])
    out = [entry]
    for k in keys:
        out.append(k)
        out += terms.get(k, [])
    return list(dict.fromkeys(out))


def rule_foods(rule: str) -> list[str]:
    """The foods a kitchen rule is about: "בלי מרגרינה" -> ["מרגרינה", "margarine", ...]."""
    out: list[str] = []
    for token in _tokens(rule):
        if len(token) > 1 and not (_variants(token) & {_norm(w) for w in _RULE_STOPWORDS}):
            out += expand(token)
    return list(dict.fromkeys(out))


def missing_lines(ingredients: list[str], pantry: list[str]) -> list[str]:
    """Ingredient lines that nothing in the pantry (or the staples) covers."""
    known = [*pantry, *STAPLES]
    return [line for line in ingredients if not any(mentions(line, item) for item in known)]


def _first_hit(ingredients: list[str], terms: list[str]) -> str | None:
    return next((line for line in ingredients if any(mentions(line, t) for t in terms)), None)


def check_recipe(ingredients: list[str], profile: Profile, pantry: list[str]) -> RecipeCheck:
    blocked = [
        RuleHit(ingredient=hit, rule=entry)
        for entry in [*profile.allergies, *profile.avoid]
        if (hit := _first_hit(ingredients, expand(entry)))
    ]
    substitute = [
        RuleHit(ingredient=line, rule=rule)
        for rule in profile.kitchen_rules
        for line in ingredients
        if any(mentions(line, food) for food in rule_foods(rule))
    ]
    missing = missing_lines(ingredients, pantry)
    have = [line for line in ingredients if line not in missing]
    return RecipeCheck(
        status="blocked" if blocked else "ok",
        blocked_by=blocked,
        needs_substitute=substitute,
        missing=missing,
        have=have,
    )
