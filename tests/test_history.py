"""Stage 9c: suggestion history. Not chosen is not disliked; only an explicit rejection blocks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests import pages
from tests.fakes import FakeClient, text_msg, tool_msg

from chefscout.bootstrap import build_agent, build_stores
from chefscout.memory.history import MAX_ENTRIES, HistoryStore
from chefscout.recipes.plan import DishDraft, RequestParams

U = "me"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, tzinfo=UTC)

    def __call__(self):
        return self.now

    def days(self, n):
        self.now += timedelta(days=n)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def history(tmp_path, clock):
    return HistoryStore(tmp_path, clock=clock)


def test_not_chosen_is_never_blocked(history):
    history.record(U, "dish", "מג'דרה", "suggested", ref="c1")
    history.record(U, "dish", "אורז לבן", "replaced", ref="c1")
    history.record(U, "recipe", "שקשוקה", "shown", url="https://a.co.il/s", ref="c1")
    assert history.rejected_names(U, 60) == [] and history.rejected_urls(U, 60) == set()
    assert history.recently_suggested(U) == ["שקשוקה", "אורז לבן", "מג'דרה"]  # newest first


def test_explicit_rejection_blocks_for_a_while(history, clock):
    history.record(U, "dish", "מג'דרה", "rejected", ref="c1")
    history.record(U, "recipe", "פשטידה", "rejected", url="https://a.co.il/p", ref="c1")
    assert history.rejected_names(U, 60) == ["מג'דרה"]
    assert history.rejected_urls(U, 60) == {"https://a.co.il/p"}
    assert "מג'דרה" not in history.recently_suggested(U)  # blocked items are not hints
    clock.days(61)
    assert history.rejected_names(U, 60) == [] and history.rejected_urls(U, 60) == set()


def test_saving_after_rejecting_unblocks_and_the_reverse(history, clock):
    history.record(U, "dish", "שניצל", "rejected", ref="c1")
    clock.days(1)
    history.record(U, "dish", "שניצל", "saved", ref="c2")
    assert history.rejected_names(U, 60) == [] and [e.title for e in history.saved(U)] == ["שניצל"]
    clock.days(1)
    history.record(U, "dish", "שניצל", "rejected", ref="c3")
    assert history.rejected_names(U, 60) == ["שניצל"] and history.saved(U) == []


def test_saved_is_newest_first_and_never_blocked(history, clock):
    history.record(U, "dish", "מרק עדשים", "saved")
    clock.days(400)
    history.record(U, "recipe", "עוגת גבינה", "saved", url="https://b.co.il/c")
    assert [e.title for e in history.saved(U)] == ["עוגת גבינה", "מרק עדשים"]


def test_the_same_record_twice_is_one_entry(history):
    for _ in range(3):
        history.record(U, "dish", "  מג'דרה ", "suggested", ref="c1:v1")
    history.record(U, "dish", "מג'דרה", "suggested", ref="c1:v2")  # a new plan version is new
    assert len(history.entries(U)) == 2


def test_history_survives_a_restart_and_is_trimmed(tmp_path, clock):
    h = HistoryStore(tmp_path, clock=clock)
    h.record(U, "dish", "חשוב", "rejected")
    for i in range(MAX_ENTRIES + 5):
        h.record(U, "dish", f"רעיון {i}", "suggested")
    again = HistoryStore(tmp_path, clock=clock)
    assert len(again.entries(U)) == MAX_ENTRIES
    assert again.rejected_names(U, 60) == ["חשוב"]  # hard entries are never trimmed


# ---- through the agent ------------------------------------------------------------------


def _agent(settings, client):
    stores = build_stores(settings)
    return build_agent(settings, client=client, stores=stores, fetcher=pages.FakeFetcher()), stores


def test_shown_recipes_are_recorded_as_soft_hints(settings):
    client = FakeClient(
        [
            tool_msg(("read_recipes", {"urls": ["https://site-a.co.il/shakshuka"]})),
            tool_msg(("show_recipes", {"recipes": [{"id": "R1"}]})),
            text_msg("הנה"),
        ]
    )
    agent, stores = _agent(settings, client)
    agent.send(user_id=U, text="שקשוקה")
    [entry] = stores.history.entries(U)
    assert entry.status == "shown" and entry.url == "https://site-a.co.il/shakshuka"
    assert stores.history.rejected_urls(U, 60) == set()


def test_rejecting_a_dish_marks_it_in_the_plan_and_blocks_it(settings):
    stores = build_stores(settings)
    conv = "c1"
    d = DishDraft(role="main", name="מג'דרה", base="עדשים", kosher="parve", method="stovetop")
    stores.plans.new_plan(U, conv, "dish_ideas", RequestParams(), ideas=[d])
    client = FakeClient(
        [tool_msg(("record_feedback", {"feedback": "rejected", "target": "D1"})), text_msg("הבנתי")]
    )
    agent = build_agent(settings, client=client, stores=stores, fetcher=pages.FakeFetcher())
    r = agent.send(user_id=U, text="לא אוהבת מג'דרה", conversation_id=conv)
    assert "is not 'never'" in r.tool_outputs[0]["content"]
    assert stores.plans.load(U, conv).dish("D1").status == "rejected"
    assert stores.history.rejected_names(U, 60) == ["מג'דרה"]
    assert stores.profiles.load(U).avoid == []  # rejecting is not "never": the profile is untouched


def test_saving_a_recipe_and_bad_targets(settings):
    client = FakeClient(
        [
            tool_msg(("read_recipes", {"urls": ["https://site-b.co.il/salad"]})),
            tool_msg(
                ("record_feedback", {"feedback": "saved", "target": "r1"}),
                ("record_feedback", {"feedback": "saved", "target": "M9.D9"}),
                ("record_feedback", {"feedback": "rejected"}),
            ),
            text_msg("שמרתי"),
        ]
    )
    agent, stores = _agent(settings, client)
    r = agent.send(user_id=U, text="שמרי לי את הסלט")
    out = [o["content"] for o in r.tool_outputs[1:]]
    assert out[0] == "saved: סלט ישראלי קצוץ"
    assert "unknown id" in out[1] and "give a target id or a title" in out[2]
    assert [e.url for e in stores.history.saved(U)] == ["https://site-b.co.il/salad"]


def test_feedback_tool_is_registered_last(settings):
    names = build_agent(settings, client=FakeClient()).registry.names()
    assert names[-1] == "record_feedback"
