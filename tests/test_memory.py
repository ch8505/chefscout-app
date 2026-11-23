"""Stage 2: lasting profile, per-conversation pantry, memory tools and context."""

from __future__ import annotations

import pytest
from tests.fakes import FakeClient, text_msg, tool_msg

from chefscout.bootstrap import build_agent, build_stores
from chefscout.context import pantry_provider, profile_provider
from chefscout.core.errors import ToolError
from chefscout.core.tools import ToolContext
from chefscout.memory.pantry import PantryStore
from chefscout.memory.profile import ProfileStore


def ctx(conv="c1", user="u1", **services):
    return ToolContext(user_id=user, conversation_id=conv, services=services)


# ---- profile -------------------------------------------------------------------


def test_profile_survives_a_new_store_instance(tmp_path):
    ProfileStore(tmp_path).remember("u1", "avoid", "  טונה ")
    ProfileStore(tmp_path).remember("u1", "allergy", "Peanuts")
    p = ProfileStore(tmp_path).load("u1")
    assert p.avoid == ["טונה"] and p.allergies == ["peanuts"]
    assert [c.action for c in p.changes] == ["add", "add"]


def test_duplicates_are_ignored_and_forget_works(tmp_path):
    s = ProfileStore(tmp_path)
    s.remember("u1", "kitchen_rule", "בלי מרגרינה")
    assert "already" in s.remember("u1", "kitchen_rule", "בלי  מרגרינה")
    assert "not found" in s.forget("u1", "kitchen_rule", "בלי חמאה")
    s.forget("u1", "kitchen_rule", "בלי מרגרינה")
    assert s.load("u1").kitchen_rules == []


def test_newest_statement_wins_between_like_and_avoid(tmp_path):
    s = ProfileStore(tmp_path)
    s.remember("u1", "like", "טונה")
    msg = s.remember("u1", "avoid", "טונה")
    p = s.load("u1")
    assert p.avoid == ["טונה"] and p.likes == [] and "removed from likes" in msg


def test_diet_change_keeps_the_old_value_in_history(tmp_path):
    s = ProfileStore(tmp_path)
    s.remember("u1", "diet", "צמחונית")
    s.remember("u1", "diet", "צמחונית שאוכלת דגים")
    change = s.load("u1").changes[-1]
    assert (change.old, change.value) == ("צמחונית", "צמחונית שאוכלת דגים")


def test_bad_values_are_tool_errors(tmp_path):
    s = ProfileStore(tmp_path)
    with pytest.raises(ToolError):
        s.remember("u1", "password", "x")
    with pytest.raises(ToolError):
        s.remember("u1", "avoid", "   ")
    with pytest.raises(ToolError):
        s.add_cooked("u1", "שקשוקה", rating="amazing")


def test_cooked_recipes_newest_first_and_loved_filter(tmp_path):
    s = ProfileStore(tmp_path)
    s.add_cooked("u1", "שקשוקה", url="https://a.co.il/1", rating="loved")
    s.add_cooked("u1", "פשטידה", rating="disliked")
    assert [c.title for c in s.cooked("u1")] == ["פשטידה", "שקשוקה"]
    assert [c.title for c in s.cooked("u1", only_loved=True)] == ["שקשוקה"]


# ---- pantry --------------------------------------------------------------------


def test_pantry_belongs_to_one_conversation(tmp_path):
    p = PantryStore(tmp_path)
    p.update("u1", "c1", add=["ביצים", "עגבניות", "ביצים"])
    p.update("u1", "c1", remove=["עגבניות"], add=["לחם"])
    assert p.items("u1", "c1") == ["ביצים", "לחם"]
    assert p.items("u1", "c2") == []  # a new conversation starts empty
    assert p.update("u1", "c1", clear=True) == []


# ---- context ---------------------------------------------------------------------


def test_profile_context_has_hard_rules_and_loved_only_when_relevant(tmp_path):
    s = ProfileStore(tmp_path)
    s.remember("u1", "allergy", "בוטנים")
    s.remember("u1", "avoid", "טונה")
    s.add_cooked("u1", "שקשוקה של אמא", url="https://a.co.il/1", rating="loved")
    provide = profile_provider(s)
    plain = provide(ctx(), "מה לבשל היום?")
    assert "בוטנים" in plain and "טונה" in plain and "שקשוקה" not in plain
    assert "שקשוקה של אמא" in provide(ctx(), "תביא לי שוב משהו שאהבתי")


def test_empty_profile_says_so_and_empty_pantry_adds_nothing(tmp_path):
    assert "nothing saved" in profile_provider(ProfileStore(tmp_path))(ctx(), "היי")
    assert pantry_provider(PantryStore(tmp_path))(ctx(), "היי") is None


# ---- through the agent ---------------------------------------------------------------


def test_lasting_fact_reaches_a_new_conversation_but_pantry_does_not(settings):
    stores = build_stores(settings)
    client = FakeClient(
        [
            tool_msg(
                ("remember_preference", {"kind": "avoid", "value": "טונה"}),
                ("update_pantry", {"add": ["ביצים", "עגבניות"]}),
            ),
            text_msg("שמרתי"),
            text_msg("רעיון לארוחה"),
        ]
    )
    agent = build_agent(settings, client=client, stores=stores)
    first = agent.send(user_id="u1", text="לעולם אל תציע לי טונה. יש לי ביצים ועגבניות")
    agent.send(user_id="u1", text="מה לבשל?")  # a NEW conversation
    sent = " ".join(b["text"] for b in client.calls[-1]["messages"][0]["content"])
    assert "טונה" in sent and "<pantry>" not in sent
    assert stores.pantry.items("u1", first.conversation_id) == ["ביצים", "עגבניות"]


def test_registry_has_the_memory_tools(settings):
    names = build_agent(settings, client=FakeClient()).registry.names()
    assert names[:5] == [
        "remember_preference",
        "forget_preference",
        "update_pantry",
        "save_cooked_recipe",
        "get_cooked_recipes",
    ]


def test_prompt_tells_the_model_to_ask_when_unclear(settings):
    system = build_agent(settings, client=FakeClient()).loop_config.system[0]["text"]
    assert "Do not decide by yourself" in system and "לתמיד או רק להיום" in system
