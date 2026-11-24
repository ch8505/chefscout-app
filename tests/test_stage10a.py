"""Stage 10a: one shape per turn, a kind of dish gets ideas, "a different version", "I have it",
and the chat survives a refresh. Scripted fake model, fake web, $0."""

from __future__ import annotations

import pytest
from tests.fakes import text_msg, tool_msg
from tests.test_idea_tools import MEALS, d, outputs, run
from tests.test_server_plan import chat, make

from chefscout.recipes.matching import ingredient_name, named_in
from chefscout.server.chat import turn_shape
from chefscout.tools.plan_tools import ideas_brief

URL = "https://site-a.co.il/shakshuka"
URLS = [URL, "https://site-b.co.il/salad"]
EGG = {"intent": "recipe", "ideas": [d("main", "שקשוקה", "ביצים", query="מתכון שקשוקה")]}


# ---- a named dish vs a kind of dish --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name", "named"),
    [
        ("מתכון לעוגת שוקולד", "עוגת שוקולד", True),
        ("איך עושים שקשוקה?", "שקשוקה", True),
        ("מתכון לעוגת שוקולד", "עוגת שוקולד עסיסית", True),  # most of the name is theirs
        ("מתכון למאפה מלוח", "בורקס גבינה", False),
        ("קינוח לשבת", "עוגת גבינה", False),
        ("משהו עם עוף", "עוף בתנור", False),  # a kind of dish: half is not enough
    ],
)
def test_named_in(text, name, named):
    assert named_in(text, name) is named


def test_a_kind_of_dish_cannot_become_one_picked_recipe(settings):
    borekas = {"intent": "recipe", "ideas": [d("main", "בורקס גבינה", "בצק עלים", "dairy")]}
    result, stores, _ = run(settings, [("propose_ideas", borekas)], text="מתכון למאפה מלוח")
    [(text, is_error)] = outputs(result)
    assert is_error and "intent=dish_ideas" in text
    assert stores.plans.load("me", "c1") is None  # nothing was saved or searched


def test_a_named_dish_goes_straight_to_the_search(settings):
    result, _, _ = run(settings, [("propose_ideas", EGG)], text="מתכון לשקשוקה")
    [(text, is_error)] = outputs(result)
    assert not is_error and text.startswith("saved. search now")


def test_a_dish_named_in_the_previous_answer_counts(settings):
    # The agent described a dish; "תביא מתכון" refers to it.
    from tests import pages
    from tests.fakes import FakeClient

    from chefscout.bootstrap import build_agent, build_stores

    stores = build_stores(settings)
    client = FakeClient(
        [text_msg("שקשוקה היא ביצים ברוטב עגבניות."), tool_msg(("propose_ideas", EGG)), text_msg("מחפש")]
    )
    agent = build_agent(settings, client=client, stores=stores, fetcher=pages.FakeFetcher())
    agent.send(user_id="me", text="מה זה שקשוקה?", conversation_id="c1")
    result = agent.send(user_id="me", text="תביא מתכון", conversation_id="c1")
    assert not result.tool_outputs[0]["is_error"]


# ---- "a different version" of the same dish ------------------------------------------------


def test_a_variant_keeps_the_dish_and_asks_for_a_new_search(settings):
    _, stores, _ = run(settings, [("propose_ideas", EGG)], text="מתכון לשקשוקה")
    result, _, _ = run(
        settings,
        [("more_options", {"dish_id": "D1", "variant": "ירוקה, בלי עגבניות"})],
        stores=stores,
        text="x",
    )
    [(text, is_error)] = outputs(result)
    assert not is_error and "ירוקה, בלי עגבניות" in text and "dish_id=D1" in text
    plan = stores.plans.load("me", "c1")
    assert [x.id for x in plan.ideas] == ["D1"] and plan.ideas[0].active  # same dish, same id


def test_variant_button_note(settings):
    api, client = make(settings, [tool_msg(("propose_ideas", EGG)), text_msg("הנה"), text_msg("מחפש")])
    conv = dict(chat(api, "מתכון לשקשוקה"))["start"]["conversation_id"]
    chat(api, "בלי עגבניות", conv, action={"type": "variant", "dish_id": "D1"})
    last = client.calls[-1]["messages"][-1]["content"][-1]["text"]
    assert last.startswith("בלי עגבניות\n[button] the same dish D1") and "variant=" in last


# ---- one shape per turn --------------------------------------------------------------------


def test_turn_shape():
    assert turn_shape(["select_dishes", "web_search", "read_recipes", "show_recipes"], "meal") == "cards"
    assert turn_shape(["propose_ideas", "read_recipes", "show_recipes"], "recipe") == "cards"
    assert turn_shape(["propose_ideas"], "dish_ideas") == "ideas"
    assert turn_shape(["replace_dish"], "meal") == "ideas"
    assert turn_shape(["select_dishes"], "meal") == "text"
    assert turn_shape(["remember_preference"], None) == "text"


def test_choosing_a_meal_brings_cards_and_never_the_ideas_list_again(settings):
    meal = {**MEALS, "meals": [MEALS["meals"][0]]}
    api, _ = make(
        settings,
        [
            tool_msg(("propose_ideas", meal)),
            text_msg("הנה"),
            tool_msg(("select_dishes", {"dish_ids": ["M1.D1"]})),
            tool_msg(("read_recipes", {"urls": [URL], "dish_id": "M1.D1"}), searched=[URL]),
            tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "M1.D1"}]})),
            text_msg("מצאתי מתכון.\nאפשר להחליף את הפלפל.\nועוד שורה.\nועוד אחת."),
        ],
    )
    conv = dict(chat(api, "מה לבשל?"))["start"]["conversation_id"]
    events = chat(api, "מתכון לעוף", conv, action={"type": "get_recipes", "dish_id": "M1.D1"})
    kinds = [k for k, _ in events]
    assert "ideas" not in kinds and "recipes" in kinds
    assert kinds.index("recipes") < kinds.index("plan") < kinds.index("done")
    plan = dict(events)["plan"]
    assert plan["meals"][0]["dishes"][0]["recipe"]["id"] == "R1"  # the old list can mark "has a recipe"
    assert dict(events)["answer"]["text"] == "מצאתי מתכון.\nאפשר להחליף את הפלפל."  # cut to two lines


def test_brief_answers():
    class Ctx:
        turn: dict

    ctx = Ctx()
    ctx.turn = {}
    assert ideas_brief(ctx, "שורה\nועוד\nועוד") == "שורה\nועוד\nועוד"  # a plain answer stays
    ctx.turn = {"ideas_only": True}
    assert ideas_brief(ctx, "שורה\nועוד") == "שורה"
    ctx.turn = {"shown": True}
    assert ideas_brief(ctx, "א\nב\nג") == "א\nב"


# ---- "I have it" ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("2 בצלים גדולים, קצוצים", "בצלים"),
        ("1/2 כוס שמנת מתוקה (או צמחית)", "שמנת מתוקה"),
        ("200 גרם גבינה בולגרית", "גבינה בולגרית"),
        ("½ כפית כמון", "כמון"),
        ("3 שיני שום כתושות", "שום"),
        ("לימון", "לימון"),
    ],
)
def test_ingredient_name(line, name):
    assert ingredient_name(line) == name


def shakshuka(settings):
    api, client = make(
        settings,
        [
            tool_msg(("propose_ideas", EGG)),
            tool_msg(("read_recipes", {"urls": URLS, "dish_id": "D1"}), searched=URLS),
            tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "D1"}]})),
            text_msg("הנה שקשוקה."),
        ],
    )
    events = chat(api, "מתכון לשקשוקה")
    return api, client, dict(events)["start"]["conversation_id"], dict(events)["recipes"]


def test_i_have_it_adds_to_the_pantry_and_updates_the_cards(settings):
    api, client, conv, [card] = shakshuka(settings)
    assert "6 עגבניות בשלות" in card["missing"]
    calls = len(client.calls)
    r = api.post(f"/api/conversations/{conv}/have", json={"line": "6 עגבניות בשלות", "recipe_ids": ["R1"]})
    body = r.json()
    assert body["item"] == "עגבניות בשלות" and "עגבניות בשלות" in body["pantry"]
    [updated] = body["cards"]
    assert "6 עגבניות בשלות" not in updated["missing"] and "4 ביצים" in updated["missing"]
    assert len(client.calls) == calls  # no model call
    # the last cards after a refresh follow the pantry too
    [again] = api.get(f"/api/conversations/{conv}/recipes").json()
    assert again["missing"] == updated["missing"]


def test_have_rejects_bad_input(settings):
    api, _, conv, _ = shakshuka(settings)
    assert api.post(f"/api/conversations/{conv}/have", json={"line": ""}).status_code == 422
    assert api.post("/api/conversations/../have", json={"line": "x"}).status_code in (400, 404)


# ---- the chat survives a refresh -----------------------------------------------------------


def test_transcript_has_what_the_user_saw(settings):
    api, _ = make(
        settings,
        [
            tool_msg(("propose_ideas", MEALS)),
            text_msg("שתי הצעות.\n\n1. עוף"),
            tool_msg(("select_dishes", {"dish_ids": ["M1.D1"]})),
            tool_msg(("read_recipes", {"urls": [URL], "dish_id": "M1.D1"}), searched=[URL]),
            tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "M1.D1"}]})),
            text_msg("הנה."),
        ],
    )
    conv = dict(chat(api, "מה לבשל?"))["start"]["conversation_id"]
    chat(api, "מתכון לעוף", conv, action={"type": "get_recipes", "dish_id": "M1.D1"})
    entries = api.get(f"/api/conversations/{conv}/transcript").json()
    assert [(e["role"], e["text"]) for e in entries] == [
        ("user", "מה לבשל?"),
        ("agent", "שתי הצעות."),  # as shown, after the filters
        ("user", "מתכון לעוף"),  # without the button note
        ("agent", "הנה."),
    ]
    assert entries[1]["ideas"]["meals"][0]["id"] == "M1" and entries[1]["cards"] == []
    assert entries[3]["ideas"] is None and [c["id"] for c in entries[3]["cards"]] == ["R1"]
    assert api.get("/api/conversations/other/transcript").json() == []


def test_more_cards_without_a_model_go_into_the_transcript(settings):
    api, _, conv, _ = shakshuka(settings)
    api.post(f"/api/conversations/{conv}/more", json={"dish_id": "D1"})
    entries = api.get(f"/api/conversations/{conv}/transcript").json()
    assert entries[-1]["role"] == "cards" and [c["id"] for c in entries[-1]["cards"]] == ["R2"]


def test_singular_and_plural_with_a_final_he_match_both_ways():
    from chefscout.recipes.matching import missing_lines

    assert missing_lines(["1 ביצה", "2 עגבניות", "3 גבינות"], ["ביצים", "עגבנייה", "גבינה"]) == []
    assert missing_lines(["4 ביצים"], ["ביצה"]) == []


def test_a_kitchen_rule_ingredient_is_not_listed_again_as_missing():
    from chefscout.memory.profile import Profile
    from chefscout.recipes.matching import check_recipe
    from chefscout.recipes.models import CheckedRecipe, Recipe
    from chefscout.server.schemas import RecipeCard

    lines = ["100 גרם מרגרינה", "1 חבילת בצק עלים"]
    check = check_recipe(lines, Profile(kitchen_rules=["בלי מרגרינה"]), [])
    recipe = Recipe(title="בורקס", url="https://x.co.il/b", site="x.co.il", ingredients=lines)
    card = RecipeCard.from_checked(CheckedRecipe(id="R1", recipe=recipe, check=check), pantry=[])
    assert card.missing == ["1 חבילת בצק עלים"] and card.needs_substitute[0].ingredient == "100 גרם מרגרינה"
    assert ingredient_name("1 חבילת בצק עלים") == "בצק עלים"
