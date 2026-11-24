"""Stage 5: the HTTP API with a fake model (no key, no network)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeClient, text_msg, tool_msg
from tests.pages import FakeFetcher

from chefscout.bootstrap import build_agent
from chefscout.server.app import create_app


def parse_sse(body: str) -> list[tuple[str, dict]]:
    out = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def make(settings, responses):
    client = FakeClient(responses)
    app = create_app(
        settings,
        agent_factory=lambda s: build_agent(settings, client=client, stores=s, fetcher=FakeFetcher()),
    )
    return TestClient(app), client


def test_chat_streams_progress_answer_cards_and_done(settings):
    api, _ = make(
        settings,
        [
            tool_msg(
                ("read_recipes", {"urls": ["https://site-a.co.il/shakshuka", "https://site-b.co.il/salad"]}),
                searched=["https://site-a.co.il/shakshuka", "https://site-b.co.il/salad"],
            ),
            tool_msg(("show_recipes", {"recipes": [{"id": "R1", "note": "חסר פלפל אדום"}, {"id": "R2"}]})),
            text_msg("הנה שני רעיונות"),
        ],
    )
    r = api.post("/api/chat", json={"message": "רעיון לארוחת ערב"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]
    # web search, read, show: one progress line each
    assert kinds == ["start", "progress", "progress", "progress", "answer", "recipes", "done"]
    cards = dict(events)["recipes"]
    assert {c["title"] for c in cards} == {"שקשוקה ביתית קלה", "סלט ישראלי קצוץ"}
    assert any(c["note"] == "חסר פלפל אדום" for c in cards) and all(
        c["url"].startswith("https://") for c in cards
    )
    conv = dict(events)["start"]["conversation_id"]
    assert len(api.get(f"/api/conversations/{conv}/recipes").json()) == 2


def test_chat_continues_the_same_conversation(settings):
    api, client = make(settings, [text_msg("אחת"), text_msg("שתיים")])
    conv = dict(parse_sse(api.post("/api/chat", json={"message": "היי"}).text))["start"]["conversation_id"]
    api.post("/api/chat", json={"message": "ועוד", "conversation_id": conv})
    assert len(client.calls[-1]["messages"]) == 3


def test_missing_key_gives_a_friendly_hebrew_error(settings):
    api = TestClient(create_app(settings))  # real factory, no key in settings
    events = parse_sse(api.post("/api/chat", json={"message": "היי"}).text)
    assert events[-1][0] == "error" and "חסר מפתח API" in events[-1][1]["message"]


def test_model_failure_does_not_crash_the_stream(settings):
    class Boom(Exception):
        pass

    class BrokenClient(FakeClient):
        def create(self, **kwargs):
            raise Boom("down")

    app = create_app(
        settings,
        agent_factory=lambda s: build_agent(settings, client=BrokenClient(), stores=s, fetcher=FakeFetcher()),
    )
    events = parse_sse(TestClient(app).post("/api/chat", json={"message": "היי"}).text)
    assert events[-1] == ("error", {"message": "משהו השתבש. אפשר לנסות שוב."})


@pytest.mark.parametrize(
    "payload", [{"message": ""}, {"message": "x" * 2001}, {"message": "hi", "conversation_id": "../x"}]
)
def test_bad_chat_requests_are_rejected(settings, payload):
    api, _ = make(settings, [])
    assert api.post("/api/chat", json=payload).status_code in (400, 422)


def test_profile_endpoints(settings):
    api, _ = make(settings, [])
    assert api.get("/api/profile").json()["allergies"] == []
    p = api.post("/api/profile/remember", json={"kind": "avoid", "value": "טונה"}).json()
    assert p["avoid"] == ["טונה"]
    assert api.post("/api/profile/forget", json={"kind": "avoid", "value": "טונה"}).json()["avoid"] == []
    assert api.post("/api/profile/remember", json={"kind": "password", "value": "x"}).status_code == 422
    cooked = api.post(
        "/api/cooked", json={"title": "שקשוקה", "url": "https://a.co.il", "rating": "loved"}
    ).json()
    assert cooked["cooked"][0]["title"] == "שקשוקה" and cooked["cooked"][0]["rating"] == "loved"


def test_pantry_endpoints_are_per_conversation(settings):
    api, _ = make(settings, [])
    conv = api.post("/api/conversations").json()["conversation_id"]
    assert api.post(f"/api/conversations/{conv}/pantry", json={"add": ["ביצים", "לחם"]}).json()["items"] == [
        "ביצים",
        "לחם",
    ]
    other = api.post("/api/conversations").json()["conversation_id"]
    assert api.get(f"/api/conversations/{other}/pantry").json()["items"] == []
    assert api.get("/api/conversations/..%2Fx/pantry").status_code in (400, 404)
