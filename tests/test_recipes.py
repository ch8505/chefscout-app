"""Stage 3: parsing real recipe pages, hard filters, pantry match, cards, link guard, safe fetching."""

from __future__ import annotations

import pytest
from tests import pages
from tests.fakes import FakeClient, raw_msg, text_msg, tool_msg

from chefscout.bootstrap import build_agent, build_stores
from chefscout.core.tools import ToolContext
from chefscout.memory.profile import Profile
from chefscout.recipes.fetch import UnsafeUrl, check_url
from chefscout.recipes.guard import link_guard
from chefscout.recipes.jsonld import NoRecipeData, parse_recipe
from chefscout.recipes.matching import check_recipe, expand, mentions, rule_foods
from chefscout.recipes.models import Recipe, RecipeCheck

# ---- JSON-LD ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "html,url,title,minutes,image",
    [
        (
            pages.SHAKSHUKA,
            "https://www.site-a.co.il/s",
            "שקשוקה ביתית קלה",
            25,
            "https://img.example.co.il/shakshuka.jpg",
        ),
        (
            pages.SALAD,
            "https://site-b.co.il/salad",
            "סלט ישראלי קצוץ",
            10,
            "https://img.example.co.il/salad.jpg",
        ),
        (pages.TUNA_PASTA, "https://site-c.co.il/t", "פסטה עם טונה", 20, None),
        (pages.CAKE, "https://site-a.co.il/cake", "עוגת שוקולד", 70, None),
    ],
)
def test_parses_plain_graph_and_list_json_ld(html, url, title, minutes, image):
    r = parse_recipe(html, url)
    assert (r.title, r.total_minutes, r.image) == (title, minutes, image)
    assert r.ingredients and r.site == url.split("/")[2].removeprefix("www.")


def test_page_without_recipe_data_is_skipped_not_guessed():
    with pytest.raises(NoRecipeData):
        parse_recipe(pages.NO_DATA, "https://site-d.co.il/blog")


# ---- matching --------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,term,expected",
    [
        ("2 כפות חמאת בוטנים", "בוטנים", True),
        ("חופן בוטן קלוי", "בוטנים", True),  # singular, final nun
        ("קופסת טונה בשמן", "טונה", True),
        ("ובטונה מסוננת", "טונה", True),  # two Hebrew prefix letters
        ("1 בצל לבן", "חלב", False),
        ("3 עגבניות", "עגבניות", True),
        ("עגבניות שרי", "שרימפס", False),
    ],
)
def test_mentions_is_hebrew_aware(line, term, expected):
    assert mentions(line, term) is expected


def test_entries_expand_to_related_foods():
    assert "שקדים" in expand("אגוזים") and "קשיו" in expand("אגוזים")
    assert "חמאת בוטנים" in expand("לבוטנים")  # prefix on the saved entry
    assert "מרגרינה" in rule_foods("לא משתמשת במרגרינה")


def test_allergy_and_never_eat_block_a_recipe():
    profile = Profile(allergies=["אגוזים"], avoid=["טונה"])
    tuna = check_recipe(["250 גרם פסטה", "קופסת טונה בשמן"], profile, [])
    almonds = check_recipe(["כוס שקדים טחונים"], profile, [])
    assert tuna.status == almonds.status == "blocked"
    assert tuna.blocked_by[0].rule == "טונה" and almonds.blocked_by[0].rule == "אגוזים"


def test_white_onion_is_not_dairy_but_cheese_is():
    profile = Profile(allergies=["חלב"])
    assert check_recipe(["1 בצל לבן", "2 עגבניות"], profile, []).status == "ok"
    assert check_recipe(["100 גרם גבינת פטה"], profile, []).status == "blocked"


def test_kitchen_rule_flags_a_substitute_and_pantry_gives_missing():
    profile = Profile(kitchen_rules=["בלי מרגרינה"])
    c = check_recipe(["200 גרם מרגרינה", "1 כוס סוכר", "3 ביצים", "1 כוס קמח"], profile, ["ביצים"])
    assert c.status == "ok" and c.needs_substitute[0].ingredient == "200 גרם מרגרינה"
    assert c.missing == ["200 גרם מרגרינה", "1 כוס קמח"]  # sugar is a staple, eggs are at home


# ---- tools through the agent -------------------------------------------------------


def _agent(settings, client, fetcher=None):
    stores = build_stores(settings)
    agent = build_agent(settings, client=client, stores=stores, fetcher=fetcher or pages.FakeFetcher())
    return agent, stores


def test_read_then_show_builds_cards_and_hides_blocked(settings):
    urls = list(pages.PAGES)
    client = FakeClient(
        [
            tool_msg(("read_recipes", {"urls": urls})),
            tool_msg(("show_recipes", {"recipes": [{"id": "R1"}, {"id": "R2", "note": "חסר פלפל"}]})),
            text_msg("הנה שלושה רעיונות"),
        ]
    )
    agent, stores = _agent(settings, client)
    stores.profiles.remember("me", "avoid", "טונה")
    stores.profiles.remember("me", "kitchen_rule", "בלי מרגרינה")
    r = agent.send(user_id="me", text="רעיון לארוחת ערב")
    read_out = r.tool_outputs[0]["content"]
    assert "BLOCKED: טונה" in read_out and "SKIPPED" in read_out
    assert "substitute: 200 גרם מרגרינה [בלי מרגרינה]" in read_out
    shown = stores.recipes.last_shown("me", r.conversation_id)
    assert len(shown) == 2 and all(s.check.status == "ok" for s in shown)


def test_show_recipes_refuses_blocked_and_unknown_ids(settings):
    fetcher = pages.FakeFetcher({"https://site-c.co.il/tuna-pasta": pages.TUNA_PASTA})
    client = FakeClient(
        [
            tool_msg(("read_recipes", {"urls": ["https://site-c.co.il/tuna-pasta"]})),
            tool_msg(
                ("show_recipes", {"recipes": [{"id": "R1"}]}), ("show_recipes", {"recipes": [{"id": "R9"}]})
            ),
            text_msg("אין מתכון מתאים"),
        ]
    )
    agent, stores = _agent(settings, client, fetcher)
    stores.profiles.remember("me", "avoid", "טונה")
    r = agent.send(user_id="me", text="פסטה")
    errors = [o["content"] for o in r.tool_outputs if o["is_error"]]
    assert "blocked" in errors[0] and "unknown recipe id" in errors[1]
    assert stores.recipes.last_shown("me", r.conversation_id) == []


def test_answer_keeps_real_links_and_drops_invented_ones(settings):
    client = FakeClient(
        [
            tool_msg(("read_recipes", {"urls": ["https://site-a.co.il/shakshuka"]})),
            text_msg("נסי [שקשוקה](https://site-a.co.il/shakshuka) או https://made-up.example/recipe"),
        ]
    )
    agent, _ = _agent(settings, client)
    r = agent.send(user_id="me", text="שקשוקה")
    assert "https://site-a.co.il/shakshuka" in r.text and "made-up" not in r.text


def test_link_guard_turns_an_unknown_markdown_link_into_plain_text(tmp_path):
    from chefscout.recipes.store import RecipeStore

    store = RecipeStore(tmp_path)
    store.add(
        "me",
        "c1",
        Recipe(url="https://ok.co.il/a", site="ok.co.il", title="t", ingredients=["x"]),
        RecipeCheck(status="ok"),
    )
    guard = link_guard(store)
    ctx = ToolContext(user_id="me", conversation_id="c1")
    assert (
        guard(ctx, "[סלט](https://fake.co.il/x) ו-[טוב](https://ok.co.il/a)")
        == "סלט ו-[טוב](https://ok.co.il/a)"
    )


def test_web_search_is_offered_with_a_budget(settings):
    client = FakeClient([raw_msg([{"type": "text", "text": "שלום"}], "end_turn")])
    agent, _ = _agent(settings, client)
    agent.send(user_id="me", text="היי")
    search = client.calls[0]["tools"][-1]
    assert search["name"] == "web_search" and search["type"] == "web_search_20250305"
    assert search["max_uses"] == 3 and search["user_location"]["country"] == "IL"


# ---- safe fetching -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost:8000/api",
        "file:///etc/passwd",
        "http://192.168.1.1/",
        "ftp://example.com/x",
    ],
)
def test_internal_or_odd_urls_are_refused(url):
    with pytest.raises(UnsafeUrl):
        check_url(url)
