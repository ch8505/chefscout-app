"""Stage 9d: idea tools through the agent (scripted fake model, no web, $0)."""

from __future__ import annotations

import json

from tests import pages
from tests.fakes import FakeClient, text_msg, tool_msg

from chefscout.bootstrap import build_agent, build_stores

U, C = "me", "c1"


def d(role, name, base, kosher="parve", method="stovetop", keys=None, minutes=None, query=""):
    out = {"role": role, "name": name, "base": base, "kosher": kosher, "method": method}
    out["key_ingredients"] = keys or [base]
    if minutes:
        out["est_minutes"] = minutes
    if query:
        out["query"] = query
    return out


CHICKEN = {
    "title": "ארוחה מהתנור",
    "style": "ביתי",
    "dishes": [
        d("main", "עוף בתנור עם בצל", "עוף", "meat", "oven", ["עוף", "בצל"], query="מתכון עוף בתנור"),
        d("side", "אורז לבן", "אורז"),
        d("salad", "סלט ירקות", "ירקות", method="raw"),
    ],
}
LENTILS = {
    "title": "ארוחה מהסיר",
    "dishes": [
        d("main", "מג'דרה", "עדשים", keys=["עדשים", "אורז", "בצל"]),
        d("side", "פתיתים", "פתיתים"),
        d("salad", "סלט כרוב", "כרוב", method="raw"),
    ],
}
MEALS = {"intent": "meal", "request": {"meal_time": "lunch"}, "meals": [CHICKEN, LENTILS]}


def run(settings, *turns, stores=None, text="מה לבשל לצהריים?"):
    """One user turn: each item is a list of (tool, args) the fake model calls, then a short answer."""
    stores = stores or build_stores(settings)
    client = FakeClient([*(tool_msg(*calls) for calls in turns), text_msg("הנה")])
    agent = build_agent(settings, client=client, stores=stores, fetcher=pages.FakeFetcher())
    result = agent.send(user_id=U, text=text, conversation_id=C)
    return result, stores, client


def outputs(result):
    return [(o["content"], o["is_error"]) for o in result.tool_outputs]


# ---- propose ---------------------------------------------------------------------------


def test_meal_request_saves_two_meals_without_any_search(settings):
    r, stores, client = run(settings, [("propose_ideas", MEALS)])
    [(out, err)] = outputs(r)
    assert not err and "Do NOT search yet" in out and "M1.D1 main: עוף בתנור עם בצל" in out
    plan = stores.plans.load(U, C)
    assert [m.id for m in plan.meals] == ["M1", "M2"] and plan.meal("M1").kosher == "meat"
    assert r.tools_used == ["propose_ideas"]  # no read_recipes, no web search
    assert {e.status for e in stores.history.entries(U)} == {"suggested"}
    assert len(stores.history.entries(U)) == 6
    # The profile still goes in with every turn, also an ideas turn.
    assert "<profile>" in json.dumps(client.calls[0]["messages"], ensure_ascii=False)


PASTA_MEAL = {  # the real case from the first test run: a complete dairy lunch without a side
    "title": "ארוחה חלבית קלילה",
    "style": "איטלקי",
    "dishes": [
        d("main", "פסטה ברוטב עגבניות וגבינה", "פסטה", "dairy", keys=["פסטה", "עגבניות", "גבינה"]),
        d("salad", "סלט ירוק", "חסה", method="raw"),
    ],
}


def test_a_complete_meal_without_a_side_is_accepted(settings):
    meals = {**MEALS, "meals": [CHICKEN, PASTA_MEAL]}
    r, stores, _ = run(settings, [("propose_ideas", meals)])
    [(out, err)] = outputs(r)
    assert not err and "has no side; fine if the meal is complete" in out
    assert len(stores.plans.load(U, C).meals) == 2


def test_a_bad_plan_gets_two_fixes_then_is_saved_without_the_bad_dish(settings):
    twice_chicken = {**MEALS, "meals": [CHICKEN, {**CHICKEN, "title": "עוד עוף"}]}
    calls = [[("propose_ideas", twice_chicken)]] * 3
    r, stores, _ = run(settings, *calls)
    (o1, e1), (o2, e2), (o3, e3) = outputs(r)
    assert e1 and e2 and "both meals are based on" in o1 and "call propose_ideas once more" in o2
    assert not e3 and "left out:" in o3 and "Do not mention checks" in o3
    plan = stores.plans.load(U, C)
    assert [m.title for m in plan.meals] == ["ארוחה מהתנור"]  # the repeated meal was left out


def test_rescue_keeps_the_meal_and_drops_only_the_dish_that_breaks_the_profile(settings):
    stores = build_stores(settings)
    stores.profiles.remember(U, "avoid", "טונה")
    tuna_salad = d("salad", "סלט טונה", "טונה", keys=["טונה", "ביצה"], method="raw")
    meals = {**MEALS, "meals": [{**LENTILS, "dishes": [*LENTILS["dishes"][:2], tuna_salad]}, CHICKEN]}
    r, stores, _ = run(settings, *[[("propose_ideas", meals)]] * 3, stores=stores)
    assert "left out: סלט טונה" in outputs(r)[2][0]
    plan = stores.plans.load(U, C)
    assert [x.name for x in plan.meal("M1").dishes] == ["מג'דרה", "פתיתים"]  # the meal stays, without tuna
    assert all("טונה" not in x.name for x in plan.all_dishes())


def test_when_nothing_can_be_saved_the_model_is_told_not_to_talk_about_checks(settings):
    wrong_shape = {"intent": "meal", "ideas": [d("main", "מג'דרה", "עדשים")]}
    r, stores, _ = run(settings, *[[("propose_ideas", wrong_shape)]] * 3)
    last, err = outputs(r)[2]
    assert err and "STOP" in last and "Do NOT tell the user about checks" in last
    assert stores.plans.load(U, C) is None and stores.history.entries(U) == []


def test_profile_blocks_an_idea_in_the_gate(settings):
    stores = build_stores(settings)
    stores.profiles.remember(U, "avoid", "טונה")
    ideas = {
        "intent": "dish_ideas",
        "ideas": [d("main", "פסטה עם טונה", "פסטה", keys=["פסטה", "טונה"]), d("main", "שקשוקה", "ביצים")],
    }
    r, stores, _ = run(settings, [("propose_ideas", ideas)], stores=stores)
    [(out, err)] = outputs(r)
    assert err and "breaks the profile: טונה" in out


def test_invalid_input_is_a_clear_error(settings):
    r, _, _ = run(
        settings, [("propose_ideas", {"intent": "meal", "meals": [{"title": "x", "dishes": [{}]}]})]
    )
    [(out, err)] = outputs(r)
    assert err and "invalid input: meals.0.title" in out


def test_a_specific_recipe_request_is_selected_for_search_at_once(settings):
    cake = {"intent": "recipe", "ideas": [d("dessert", "עוגת שוקולד", "שוקולד", query="מתכון עוגת שוקולד")]}
    r, stores, _ = run(settings, [("propose_ideas", cake)], text="מתכון לעוגת שוקולד")
    [(out, _)] = outputs(r)
    assert "search now for:\nD1: מתכון עוגת שוקולד" in out
    assert stores.plans.load(U, C).dish("D1").status == "selected"


# ---- select ----------------------------------------------------------------------------


def test_select_a_whole_meal_or_one_dish(settings):
    _, stores, _ = run(settings, [("propose_ideas", MEALS)])
    r, stores, _ = run(settings, [("select_dishes", {"meal_id": "m1"})], stores=stores, text="את הראשונה")
    [(out, _)] = outputs(r)
    assert "M1.D1: עוף בתנור עם בצל - search: מתכון עוף בתנור" in out
    assert "M1.D2: אורז לבן - search: מתכון אורז לבן" in out
    plan = stores.plans.load(U, C)
    assert [x.status for x in plan.meal("M1").dishes] == ["selected"] * 3
    assert {x.status for x in plan.meal("M2").dishes} == {"proposed"}

    run(settings, [("select_dishes", {"dish_ids": ["M2.D3"]})], stores=stores, text="רק את הסלט מהשנייה")
    plan = stores.plans.load(U, C)
    assert [x.status for x in plan.meal("M2").dishes] == ["proposed", "proposed", "selected"]


# ---- replace ---------------------------------------------------------------------------


def test_swapping_a_dish_is_not_a_rejection(settings):
    _, stores, _ = run(settings, [("propose_ideas", MEALS)])
    swap = {"dish_id": "M1.D2", "dish": d("side", "תפוחי אדמה בתנור", "תפוחי אדמה", method="oven")}
    r, stores, _ = run(settings, [("replace_dish", swap)], stores=stores, text="תחליף את התוספת")
    [(out, err)] = outputs(r)
    assert not err and "M1.D2 -> M1.D4" in out and "every other dish stays" in out
    assert stores.plans.load(U, C).dish("M1.D2").status == "replaced"
    assert stores.history.rejected_names(U, 60) == []
    assert "אורז לבן" in stores.history.recently_suggested(U)


def test_rejecting_while_swapping_blocks_the_old_dish_only(settings):
    _, stores, _ = run(settings, [("propose_ideas", MEALS)])
    swap = {"dish_id": "M2.D1", "reason": "rejected", "dish": d("main", "קציצות דגים", "דג", keys=["דג"])}
    r, stores, _ = run(settings, [("replace_dish", swap)], stores=stores, text="לא אוהבת מג'דרה")
    assert "not 'never'" in outputs(r)[0][0]
    assert stores.plans.load(U, C).dish("M2.D1").status == "rejected"
    assert stores.history.rejected_names(U, 60) == ["מג'דרה"]
    assert stores.profiles.load(U).avoid == []


def test_replacement_goes_through_the_gate(settings):
    _, stores, _ = run(settings, [("propose_ideas", MEALS)])
    dairy = {"dish_id": "M1.D2", "dish": d("side", "פירה עם חמאה", "תפוחי אדמה", "dairy", keys=["חמאה"])}
    r, stores, _ = run(settings, [("replace_dish", dairy)], stores=stores)
    [(out, err)] = outputs(r)
    assert err and "would mix meat and dairy" in out
    assert stores.plans.load(U, C).dish("M1.D2").status == "proposed"


def test_not_found_replaces_only_that_dish_and_only_once(settings):
    _, stores, _ = run(settings, [("propose_ideas", MEALS)])
    run(settings, [("select_dishes", {"meal_id": "M1"})], stores=stores)
    first = {"dish_id": "M1.D2", "reason": "not_found", "dish": d("side", "קוסקוס", "קוסקוס")}
    second = {"dish_id": "M1.D4", "reason": "not_found", "dish": d("side", "בורגול", "בורגול")}
    r, stores, _ = run(settings, [("replace_dish", first)], [("replace_dish", second)], stores=stores)
    (o1, e1), (o2, e2) = outputs(r)
    assert not e1 and "search only for M1.D4" in o1
    assert e2 and "do not try again" in o2
    plan = stores.plans.load(U, C)
    assert [x.id for x in plan.meal("M1").active_dishes()] == ["M1.D1", "M1.D4", "M1.D3"]


def test_replacing_after_recipes_were_found_keeps_them(settings):
    _, stores, _ = run(settings, [("propose_ideas", MEALS)])
    run(settings, [("select_dishes", {"meal_id": "M1"})], stores=stores)
    plan = stores.plans.load(U, C)
    for x in plan.meal("M1").dishes:  # stage 9e links recipes; set directly here
        x.recipe_id, x.search_status, x.candidates = f"R{x.id[-1]}", "found", [f"R{x.id[-1]}"]
    stores.plans._save(U, C, plan)

    swap = {"dish_id": "M1.D3", "dish": d("salad", "סלט חסה", "חסה", method="raw")}
    run(settings, [("replace_dish", swap)], stores=stores)
    r, stores, _ = run(settings, [("select_dishes", {"meal_id": "M1"})], stores=stores)
    [(out, _)] = outputs(r)
    assert "M1.D1: עוף בתנור עם בצל - already has recipe R1, no new search" in out
    assert "M1.D2: אורז לבן - already has recipe R2, no new search" in out
    assert "M1.D4: סלט חסה - search:" in out


# ---- more options --------------------------------------------------------------------------


def test_more_options_uses_read_recipes_first_then_one_new_search(settings):
    _, stores, _ = run(settings, [("propose_ideas", MEALS)])
    plan = stores.plans.load(U, C)
    salad = plan.dish("M1.D3")
    salad.recipe_id, salad.search_status = "R1", "found"
    salad.candidates, salad.shown_recipes = ["R1", "R4", "R6"], ["R1"]
    stores.plans._save(U, C, plan)

    r, stores, _ = run(settings, [("more_options", {"dish_id": "M1.D3"})], stores=stores, text="עוד סלטים?")
    assert "R4, R6" in outputs(r)[0][0] and "no search needed" in outputs(r)[0][0]

    plan = stores.plans.load(U, C)
    plan.dish("M1.D3").shown_recipes = ["R1", "R4", "R6"]
    stores.plans._save(U, C, plan)
    r, stores, _ = run(settings, [("more_options", {"dish_id": "M1.D3"})], stores=stores)
    assert "search once more for this dish only" in outputs(r)[0][0]
    assert stores.plans.load(U, C).dish("M1.D1") == plan.dish("M1.D1")  # the rest is untouched


def test_tools_are_registered_in_a_fixed_order(settings):
    names = build_agent(settings, client=FakeClient()).registry.names()
    assert names[5:9] == ["propose_ideas", "replace_dish", "select_dishes", "more_options"]
    assert names[-1] == "record_feedback"
