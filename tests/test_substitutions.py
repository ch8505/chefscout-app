"""Stage 4: substitutes with scaled amounts, filtered by the profile."""

from __future__ import annotations

from fractions import Fraction

import pytest
from tests.fakes import FakeClient, text_msg, tool_msg
from tests.pages import FakeFetcher

from chefscout.bootstrap import build_agent, build_stores
from chefscout.memory.profile import Profile
from chefscout.recipes.substitutions import find_entry, format_amount, parse_amount, suggest


@pytest.mark.parametrize(
    "line,key",
    [
        ("200 גרם מרגרינה", "מרגרינה"),
        ("3 ביצים", "ביצה"),
        ("1 כוס שמנת מתוקה", "שמנת מתוקה"),
        ("2 כפות רוטב סויה", "רוטב סויה"),
        ("חצי לימון", "מיץ לימון"),
        ("קילו תפוחי אדמה", None),
    ],
)
def test_finds_the_most_specific_entry(line, key):
    assert find_entry(line) == key


@pytest.mark.parametrize(
    "text,expected",
    [
        ("200 גרם", (Fraction(200), "גרם")),
        ("1 1/2 כוס", (Fraction(3, 2), "כוס")),
        ("½ כוס", (Fraction(1, 2), "כוס")),
        ("2.5 כפות", (Fraction(5, 2), "כפות")),
        ("קורט", None),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_format_amount_is_friendly():
    assert format_amount(Fraction(150)) == "150"
    assert format_amount(Fraction(3, 4)) == "¾"
    assert format_amount(Fraction(9, 8)) == "1.1"


def test_margarine_gets_oil_with_three_quarters_of_the_amount():
    out = suggest("200 גרם מרגרינה", None, Profile())
    assert "150 גרם שמן קנולה או חמניות (¾ מהכמות)" in out
    assert "200 גרם חמאה" in out


def test_options_that_break_the_profile_are_removed():
    out = suggest("1 כוס חלב", None, Profile(allergies=["אגוזים"], avoid=[], kitchen_rules=[]))
    assert "משקה שקדים" not in out.split("skipped")[0] and "skipped (profile): משקה שקדים (אגוזים)" in out
    vegan = suggest("100 גרם חמאה", None, Profile(kitchen_rules=["לא משתמשת במרגרינה"]))
    assert "מרגרינה (1:1)" not in vegan.split("skipped")[0]


def test_milk_allergy_still_allows_plant_cream():
    out = suggest("1 כוס שמנת מתוקה", None, Profile(allergies=["חלב"]))
    assert "קרם קוקוס" in out and "שמנת צמחית" in out


def test_unknown_ingredient_says_there_is_no_verified_substitute():
    assert "no verified substitute" in suggest("קילו תפוחי אדמה", None, Profile())


def test_tool_through_the_agent_uses_the_saved_profile(settings):
    stores = build_stores(settings)
    stores.profiles.remember("me", "allergy", "אגוזים")
    client = FakeClient(
        [tool_msg(("suggest_substitute", {"ingredient": "1 כוס חלב"})), text_msg("אפשר משקה סויה")]
    )
    agent = build_agent(settings, client=client, stores=stores, fetcher=FakeFetcher())
    r = agent.send(user_id="me", text="אין לי חלב")
    out = r.tool_outputs[0]["content"]
    assert "משקה סויה" in out and "skipped (profile): משקה שקדים" in out
