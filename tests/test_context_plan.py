"""Stage 9f: what goes into each turn (current plan, rejections, hints) and the prompt's rules."""

from __future__ import annotations

import json

from tests import pages
from tests.fakes import FakeClient, text_msg, tool_msg
from tests.test_idea_tools import MEALS

from chefscout.bootstrap import build_agent, build_stores
from chefscout.context import history_provider, plan_provider
from chefscout.core.tools import ToolContext
from chefscout.prompts import SYSTEM_PROMPT

U, C = "me", "c1"


def turn(settings, stores, *responses, conv=C, text="..."):
    client = FakeClient([*responses, text_msg("הנה")])
    agent = build_agent(settings, client=client, stores=stores, fetcher=pages.FakeFetcher())
    agent.send(user_id=U, text=text, conversation_id=conv)
    first_user_message = client.calls[0]["messages"][-1]
    return [b["text"] for b in first_user_message["content"]]


def test_the_plan_goes_into_follow_up_turns_of_the_same_conversation_only(settings):
    stores = build_stores(settings)
    blocks = turn(settings, stores, tool_msg(("propose_ideas", MEALS)), text="מה לבשל?")
    assert not any("<current_plan>" in b for b in blocks)  # no plan before the first proposal

    blocks = turn(settings, stores, text="תחליף את התוספת")
    plan = next(b for b in blocks if b.startswith("<current_plan>"))
    assert "intent meal | lunch" in plan
    assert "M1 ארוחה מהתנור [meat]" in plan and "  M1.D2 side: אורז לבן [parve]" in plan
    assert blocks[0].startswith("<profile>") and blocks[-1] == "תחליף את התוספת"

    other = turn(settings, stores, conv="c2", text="שיחה חדשה")
    assert not any("<current_plan>" in b for b in other)


def test_plan_shows_choices_and_found_recipes(settings):
    stores = build_stores(settings)
    turn(settings, stores, tool_msg(("propose_ideas", MEALS)))
    stores.plans.select(U, C, meal_id="M1")
    stores.plans.update_dish(U, C, "M1.D1", recipe_id="R1", search_status="found")
    stores.plans.update_dish(U, C, "M1.D2", search_status="not_found")
    text = plan_provider(stores.plans)(ToolContext(U, C), "")
    assert "M1.D1 main: עוף בתנור עם בצל [meat] (selected, recipe R1)" in text
    assert "M1.D2 side: אורז לבן [parve] (selected, no recipe found)" in text
    assert "M2.D1 main: מג'דרה [parve]\n" in text  # proposed dishes carry no extra label


def test_history_block_separates_blocked_from_hints(settings):
    stores = build_stores(settings)
    provide = history_provider(stores.history, stores.profiles, settings)
    ctx = ToolContext(U, C)
    assert provide(ctx, "") is None  # nothing yet: no block, no tokens

    stores.history.record(U, "dish", "מג'דרה", "rejected")
    stores.history.record(U, "dish", "שניצל", "suggested")
    stores.profiles.add_cooked(U, "שקשוקה", rating="loved")
    text = provide(ctx, "")
    assert "<rejected>do not suggest: מג'דרה</rejected>" in text
    hints = text.split("<recently_suggested>")[1]
    assert "suggested lately: שניצל" in hints and "cooked lately: שקשוקה" in hints
    assert "repeating is allowed" in hints and "מג'דרה" not in hints


def test_history_goes_into_every_conversation(settings):
    stores = build_stores(settings)
    stores.history.record(U, "dish", "מג'דרה", "rejected", ref="old-conversation")
    blocks = turn(settings, stores, conv="new", text="רעיון לצהריים")
    assert any("<rejected>do not suggest: מג'דרה</rejected>" in b for b in blocks)


def test_prompt_keeps_the_existing_rules_and_states_the_new_ones():
    # existing behavior (regression)
    assert "לתמיד או רק להיום?" in SYSTEM_PROMPT and "Do not decide by yourself" in SYSTEM_PROMPT
    assert "never suggest anything that breaks the profile" in SYSTEM_PROMPT
    assert "Do not copy the recipe steps" in SYSTEM_PROMPT and "Do not write urls" in SYSTEM_PROMPT
    # new
    assert "Do NOT search and do NOT write links in this turn" in SYSTEM_PROMPT
    assert "never mixes meat and dairy" in SYSTEM_PROMPT
    assert "is NOT a rejection" in SYSTEM_PROMPT
    for intent in ("intent=meal", "intent=dish_ideas", "intent=pantry", "intent=vague", "intent=recipe"):
        assert intent in SYSTEM_PROMPT


def test_prompt_and_tools_stay_identical_between_turns_for_the_cache(settings):
    stores = build_stores(settings)
    client = FakeClient([text_msg("א"), text_msg("ב")])
    agent = build_agent(settings, client=client, stores=stores, fetcher=pages.FakeFetcher())
    agent.send(user_id=U, text="היי", conversation_id=C)
    agent.send(user_id=U, text="שוב היי", conversation_id=C)
    first, second = client.calls
    assert json.dumps(first["system"]) == json.dumps(second["system"])
    assert json.dumps(first["tools"]) == json.dumps(second["tools"])
