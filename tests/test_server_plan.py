"""Stage 9g: ideas over HTTP: the ideas event, buttons (actions), feedback, refresh, double clicks."""

from __future__ import annotations

import json

from tests.fakes import text_msg, tool_msg
from tests.test_idea_tools import MEALS, d
from tests.test_server import make, parse_sse


def chat(api, message, conv=None, **extra):
    r = api.post("/api/chat", json={"message": message, "conversation_id": conv, **extra})
    assert r.status_code == 200
    return parse_sse(r.text)


def last_user_text(client):
    """The text the user typed in the newest turn (the last user message that has text)."""
    for message in reversed(client.calls[-1]["messages"]):
        texts = [b["text"] for b in message["content"] if b.get("type") == "text"]
        if message["role"] == "user" and texts:
            return texts[-1]
    raise AssertionError("no user text")


def test_an_ideas_turn_streams_the_plan_and_no_recipe_cards(settings):
    api, _ = make(settings, [tool_msg(("propose_ideas", MEALS)), text_msg("שתי הצעות. איזו?")])
    events = chat(api, "מה לבשל לצהריים?")
    assert [k for k, _ in events] == ["start", "progress", "answer", "ideas", "done"]
    plan = dict(events)["ideas"]
    assert [m["id"] for m in plan["meals"]] == ["M1", "M2"]
    m1 = plan["meals"][0]
    assert m1["kosher"] == "meat" and m1["kosher_label"] == "בשרי"
    assert [(x["id"], x["role_label"]) for x in m1["dishes"]] == [
        ("M1.D1", "מנה עיקרית"),
        ("M1.D2", "תוספת"),
        ("M1.D3", "סלט"),
    ]
    assert all(x["recipe"] is None for x in m1["dishes"])
    assert "https://" not in json.dumps(plan)  # an idea has no link


def test_a_button_adds_a_short_note_for_the_model(settings):
    api, client = make(
        settings,
        [
            tool_msg(("propose_ideas", MEALS)),
            text_msg("איזו?"),
            tool_msg(("select_dishes", {"meal_id": "M1"})),
            text_msg("מחפש"),
        ],
    )
    conv = dict(chat(api, "מה לבשל?"))["start"]["conversation_id"]
    action = {"type": "get_recipes", "meal_id": "M1"}
    events = chat(api, "תביא מתכונים לארוחה 1", conv, action=action, request_id="req-1")
    assert last_user_text(client).startswith("תביא מתכונים לארוחה 1\n[button] the user chose meal M1")
    statuses = [x["status"] for x in dict(events)["ideas"]["meals"][0]["dishes"]]
    assert statuses == ["selected"] * 3


def test_the_same_button_press_twice_runs_once(settings):
    api, client = make(settings, [tool_msg(("propose_ideas", MEALS)), text_msg("איזו?"), text_msg("אחת")])
    conv = dict(chat(api, "מה לבשל?"))["start"]["conversation_id"]
    action = {"type": "replace", "dish_id": "M1.D2"}
    chat(api, "תחליף את האורז", conv, action=action, request_id="same")
    calls = len(client.calls)
    again = chat(api, "תחליף את האורז", conv, action=action, request_id="same")
    assert len(client.calls) == calls  # no second model call, no cost
    assert [k for k, _ in again] == ["start", "done"] and again[-1][1]["duplicate"] is True


def test_plan_survives_a_refresh(settings):
    api, _ = make(settings, [tool_msg(("propose_ideas", MEALS)), text_msg("איזו?")])
    conv = dict(chat(api, "מה לבשל?"))["start"]["conversation_id"]
    view = api.get(f"/api/conversations/{conv}/plan").json()
    assert view["version"] == 1 and len(view["meals"]) == 2
    assert api.get("/api/conversations/new-one/plan").json() is None


def test_reject_and_save_buttons_need_no_model_and_run_once(settings):
    ideas = {
        "intent": "dish_ideas",
        "ideas": [d("main", "מג'דרה", "עדשים"), d("main", "שקשוקה", "ביצים"), d("main", "פסטה", "פסטה")],
    }
    api, client = make(settings, [tool_msg(("propose_ideas", ideas)), text_msg("שלושה רעיונות")])
    conv = dict(chat(api, "רעיון למנה עיקרית"))["start"]["conversation_id"]
    calls = len(client.calls)

    body = {"target": "D1", "feedback": "rejected", "request_id": "click-1"}
    view = api.post(f"/api/conversations/{conv}/feedback", json=body).json()
    assert [x["id"] for x in view["ideas"]] == ["D2", "D3"]  # the rejected idea is gone
    api.post(f"/api/conversations/{conv}/feedback", json=body)  # double click
    api.post(f"/api/conversations/{conv}/feedback", json={"target": "D2", "feedback": "saved"})
    assert len(client.calls) == calls

    saved = api.get("/api/history/saved").json()
    assert [s["title"] for s in saved] == ["שקשוקה"]
    bad = api.post(f"/api/conversations/{conv}/feedback", json={"target": "M9.D9", "feedback": "saved"})
    assert bad.status_code == 400


def test_bad_actions_and_ids_are_rejected(settings):
    api, _ = make(settings, [])
    r = api.post("/api/chat", json={"message": "x", "action": {"type": "delete_all"}})
    assert r.status_code == 422
    r = api.post("/api/chat", json={"message": "x", "request_id": "../../etc"})
    assert r.status_code == 422
    r = api.post("/api/conversations/c1/feedback", json={"target": "<script>", "feedback": "saved"})
    assert r.status_code == 422


def test_recipe_cards_carry_the_dish_role(settings):
    url = "https://site-a.co.il/shakshuka"
    egg = {"intent": "recipe", "ideas": [d("main", "שקשוקה", "ביצים", query="מתכון שקשוקה")]}
    api, _ = make(
        settings,
        [
            tool_msg(("propose_ideas", egg)),
            tool_msg(("read_recipes", {"urls": [url], "dish_id": "D1"}), searched=[url]),
            tool_msg(("show_recipes", {"recipes": [{"id": "R1", "dish_id": "D1"}]})),
            text_msg("הנה"),
        ],
    )
    events = chat(api, "מתכון לשקשוקה")
    [card] = dict(events)["recipes"]
    assert card["dish_id"] == "D1" and card["role_label"] == "מנה עיקרית" and card["alternative"] is False
    conv = dict(events)["start"]["conversation_id"]
    dish = api.get(f"/api/conversations/{conv}/plan").json()["ideas"][0]
    assert dish["recipe"]["url"] == url  # the plan view shows the found recipe after a refresh
