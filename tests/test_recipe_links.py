"""Stage 9e: real recipes for chosen dishes: source, kashrut with the meal, time, alternatives."""

from __future__ import annotations

from tests import pages
from tests.fakes import FakeClient, raw_msg, text_msg, tool_msg

from chefscout.bootstrap import build_agent, build_stores
from chefscout.recipes.store import normalize_url

U, C = "me", "c1"
A = "https://chef.co.il/"
WEB = {
    A + "chicken": pages.page(
        {"name": "עוף בתנור עם בצל", "totalTime": "PT25M", "recipeIngredient": ["1 עוף", "3 בצלים"]}
    ),
    A + "chicken-slow": pages.page(
        {"name": "עוף בבישול ארוך", "totalTime": "PT2H30M", "recipeIngredient": ["1 עוף", "יין"]}
    ),
    A + "chicken-butter": pages.page(
        {"name": "עוף בחמאה", "totalTime": "PT30M", "recipeIngredient": ["חזה עוף", "100 גרם חמאה"]}
    ),
    A + "rice": pages.page({"name": "אורז לבן", "totalTime": "PT20M", "recipeIngredient": ["כוס אורז"]}),
    A + "puree": pages.page(
        {"name": "פירה", "totalTime": "PT25M", "recipeIngredient": ["תפוחי אדמה", "50 גרם חמאה"]}
    ),
    A + "salad": pages.page({"name": "סלט ירקות", "prepTime": "PT10M", "recipeIngredient": ["מלפפון"]}),
    A + "salad-2": pages.page({"name": "סלט קצוץ", "prepTime": "PT10M", "recipeIngredient": ["עגבנייה"]}),
}


def d(role, name, base, kosher="parve", method="stovetop"):
    return {
        "role": role,
        "name": name,
        "base": base,
        "kosher": kosher,
        "method": method,
        "key_ingredients": [base],
    }


MEALS = {
    "intent": "meal",
    "request": {"meal_time": "lunch", "max_minutes": 40},
    "meals": [
        {
            "title": "ארוחה מהתנור",
            "dishes": [
                d("main", "עוף בתנור עם בצל", "עוף", "meat", "oven"),
                d("side", "אורז לבן", "אורז"),
                d("salad", "סלט ירקות", "ירקות", method="raw"),
            ],
        },
        {
            "title": "ארוחה מהסיר",
            "dishes": [
                d("main", "מג'דרה", "עדשים"),
                d("side", "פתיתים", "פתיתים"),
                d("salad", "סלט כרוב", "כרוב", method="raw"),
            ],
        },
    ],
}


class World:
    """One conversation across turns, with the same stores and fake web."""

    def __init__(self, settings):
        self.settings = settings
        self.stores = build_stores(settings)
        self.fetcher = pages.FakeFetcher(WEB)

    def turn(self, *responses, text="..."):
        client = FakeClient([*responses, text_msg("הנה [מתכון](https://chef.co.il/chicken)")])
        agent = build_agent(self.settings, client=client, stores=self.stores, fetcher=self.fetcher)
        return agent.send(user_id=U, text=text, conversation_id=C)

    def chosen_meal(self):
        self.turn(tool_msg(("propose_ideas", MEALS)), text="מה לבשל לצהריים?")
        self.turn(tool_msg(("select_dishes", {"meal_id": "M1"})), text="את הראשונה")
        return self

    def plan(self):
        return self.stores.plans.load(U, C)


def out(r, i=0):
    return r.tool_outputs[i]["content"]


# ---- sources -------------------------------------------------------------------------------


def test_only_urls_from_this_conversations_search_can_be_read(settings):
    w = World(settings)
    r = w.turn(
        tool_msg(("read_recipes", {"urls": [A + "rice", A + "salad"]}), searched=[A + "rice"]),
    )
    assert "R1|אורז לבן" in out(r) and "NOT FROM SEARCH" in out(r)
    assert w.fetcher.requested == [A + "rice"]  # the other page was never opened


def test_search_results_are_kept_for_later_turns_and_urls_are_normalized(settings):
    w = World(settings)
    w.turn(tool_msg(("update_pantry", {"add": ["אורז"]}), searched=["https://www.chef.co.il/rice/"]))
    assert normalize_url("https://WWW.chef.co.il/rice/#top") in w.stores.recipes.sources(U, C)
    r = w.turn(tool_msg(("read_recipes", {"urls": [A + "rice"]})))
    assert "R1|אורז לבן" in out(r)


def test_a_search_error_result_is_ignored(settings):
    w = World(settings)
    failed = {"type": "web_search_tool_result_error", "error_code": "unavailable"}
    error = {"type": "web_search_tool_result", "tool_use_id": "s1", "content": failed}
    w.turn(raw_msg([error, {"type": "text", "text": "לא הצלחתי לחפש"}], "end_turn"))
    assert w.stores.recipes.sources(U, C) == set()


def test_ideas_turn_reads_nothing_and_has_no_links(settings):
    w = World(settings)
    r = w.turn(
        tool_msg(("propose_ideas", MEALS)),
        tool_msg(("read_recipes", {"urls": [A + "chicken"]}), searched=[A + "chicken"]),
        text="מה לבשל?",
    )
    assert r.tool_outputs[1]["is_error"] and "the user chooses first" in out(r, 1)
    assert "https://" not in r.text and "מתכון" in r.text


# ---- recipes for dishes ------------------------------------------------------------------


def test_reading_for_a_dish_that_was_not_chosen_is_refused(settings):
    w = World(settings)
    w.turn(tool_msg(("propose_ideas", MEALS)))
    r = w.turn(
        tool_msg(("read_recipes", {"urls": [A + "chicken"], "dish_id": "M2.D1"}), searched=[A + "chicken"])
    )
    assert "was not chosen yet" in out(r)


def test_whole_meal_flow_links_each_recipe_to_its_dish(settings):
    w = World(settings).chosen_meal()
    r = w.turn(
        tool_msg(("read_recipes", {"urls": [A + "chicken"], "dish_id": "M1.D1"}), searched=[A + "chicken"]),
        tool_msg(("read_recipes", {"urls": [A + "rice"], "dish_id": "M1.D2"}), searched=[A + "rice"]),
        tool_msg(("read_recipes", {"urls": [A + "salad"], "dish_id": "M1.D3"}), searched=[A + "salad"]),
        tool_msg(
            (
                "show_recipes",
                {
                    "recipes": [
                        {"id": "R1", "dish_id": "M1.D1"},
                        {"id": "R2", "dish_id": "M1.D2"},
                        {"id": "R3", "dish_id": "M1.D3"},
                    ]
                },
            )
        ),
        text="תביא מתכונים",
    )
    assert not any(o["is_error"] for o in r.tool_outputs)
    plan = w.plan()
    assert [(x.id, x.recipe_id, x.search_status) for x in plan.meal("M1").dishes] == [
        ("M1.D1", "R1", "found"),
        ("M1.D2", "R2", "found"),
        ("M1.D3", "R3", "found"),
    ]
    assert {x.search_status for x in plan.meal("M2").dishes} == {"none"}
    assert "https://chef.co.il/chicken" in r.text  # a real, read link is kept


def test_dairy_recipe_is_blocked_in_a_meat_meal_and_mixed_is_always_blocked(settings):
    w = World(settings).chosen_meal()
    r = w.turn(
        tool_msg(
            ("read_recipes", {"urls": [A + "puree", A + "rice"], "dish_id": "M1.D2"}),
            searched=[A + "puree", A + "rice"],
        )
    )
    assert "BLOCKED: כשרות: ארוחה בשרית (50 גרם חמאה)" in out(r) and "R2|אורז לבן|20 min" in out(r)
    r = w.turn(
        tool_msg(
            ("read_recipes", {"urls": [A + "chicken-butter"], "dish_id": "M1.D1"}),
            searched=[A + "chicken-butter"],
        )
    )
    assert "BLOCKED: כשרות: בשר וחלב" in out(r) and "no usable recipe for M1.D1" in out(r)
    assert w.plan().dish("M1.D1").search_status == "not_found"


def test_found_dish_is_not_searched_again_unless_more_options(settings):
    w = World(settings).chosen_meal()
    salads = [A + "salad", A + "salad-2"]
    w.turn(tool_msg(("read_recipes", {"urls": salads, "dish_id": "M1.D3"}), searched=salads))
    w.turn(tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "M1.D3"}]})))
    r = w.turn(tool_msg(("read_recipes", {"urls": [A + "salad"], "dish_id": "M1.D3"})))
    assert "already has recipe R1" in out(r)

    r = w.turn(
        tool_msg(("more_options", {"dish_id": "M1.D3"})),
        tool_msg(("show_recipes", {"recipes": [{"id": "R2", "dish_id": "M1.D3"}]})),
        text="עוד סלט?",
    )
    assert "R2" in out(r) and "no search needed" in out(r) and not r.tool_outputs[1]["is_error"]
    salad = w.plan().dish("M1.D3")
    assert salad.recipe_id == "R1" and salad.shown_recipes == ["R1", "R2"]  # the first stays the main one
    again = w.turn(tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "M1.D3"}]})))
    assert "already shown" in out(again)


def test_recipe_for_one_dish_cannot_be_shown_for_another(settings):
    w = World(settings).chosen_meal()
    w.turn(tool_msg(("read_recipes", {"urls": [A + "rice"], "dish_id": "M1.D2"}), searched=[A + "rice"]))
    r = w.turn(tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "M1.D3"}]})))
    assert "was read for M1.D2, not for M1.D3" in out(r)


# ---- time ------------------------------------------------------------------------------------


def test_a_much_longer_recipe_is_only_an_alternative_when_nothing_fits(settings):
    w = World(settings).chosen_meal()
    urls = [A + "chicken-slow", A + "chicken"]
    r = w.turn(tool_msg(("read_recipes", {"urls": urls, "dish_id": "M1.D1"}), searched=urls))
    assert "R1|עוף בבישול ארוך|150 min" in out(r) and "time: over" in out(r)

    plain = w.turn(tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "M1.D1"}]})))
    assert "much longer than asked" in out(plain)
    alt = w.turn(
        tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "M1.D1", "alternative": True}]}))
    )
    assert "there are recipes in time for this dish: R2" in out(alt)

    ok = w.turn(
        tool_msg(("show_recipes", {"recipes": [{"id": "R2", "dish_id": "M1.D1"}]})),
        tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "M1.D1", "alternative": True}]})),
    )
    assert not ok.tool_outputs[0]["is_error"]
    assert "(alternative)" in out(ok, 1)  # in-time recipe shown first, so now the long one may follow
    assert w.stores.recipes.get(U, C, "R1").alternative is True


# ---- history ---------------------------------------------------------------------------------


def test_explicitly_rejected_recipe_is_blocked_and_seen_one_is_only_a_hint(settings):
    w = World(settings)
    w.stores.history.record(U, "recipe", "סלט ירקות", "rejected", url=A + "salad", ref="old")
    w.stores.history.record(U, "recipe", "סלט קצוץ", "shown", url=A + "salad-2", ref="old")
    urls = [A + "salad", A + "salad-2"]
    r = w.turn(tool_msg(("read_recipes", {"urls": urls}), searched=urls))
    assert "BLOCKED: נדחה לאחרונה" in out(r)
    assert "R2|סלט קצוץ" in out(r) and "|OK|" in out(r) and "shown to the user before" in out(r)
