"""The agent end to end with a fake model: loop, context, history, compaction, events."""

from __future__ import annotations

import json

import pytest
from tests.fakes import FakeClient, raw_msg, text_msg, tool_msg

from chefscout.bootstrap import build_agent
from chefscout.core.agent import Agent, AgentConfig
from chefscout.core.errors import MissingApiKeyError
from chefscout.core.history import ConversationStore
from chefscout.core.tools import Tool, ToolRegistry

PANTRY = {"items": []}


def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="add_item",
            description="add to pantry",
            input_schema={"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]},
            handler=lambda args, ctx: PANTRY["items"].append(args["item"]) or f"added {args['item']}",
        )
    )
    reg.register(
        Tool(
            name="list_items",
            description="list pantry",
            input_schema={"type": "object", "properties": {}},
            handler=lambda args, ctx: ", ".join(PANTRY["items"]) or "empty",
        )
    )
    return reg


def make_agent(tmp_path, client, *, threshold=100_000, max_iterations=8, providers=(), server_tools=()):
    PANTRY["items"] = []
    return Agent(
        client=client,
        config=AgentConfig(
            model="claude-haiku-5-5",
            effort="low",
            max_output_tokens=8000,
            max_iterations=max_iterations,
            compaction_threshold_tokens=threshold,
            system_prompt="SYSTEM",
        ),
        registry=registry(),
        store=ConversationStore(tmp_path),
        context_providers=providers,
        server_tools=server_tools,
    )


def test_tool_calls_run_and_all_results_return_in_one_message(tmp_path):
    client = FakeClient(
        [tool_msg(("add_item", {"item": "eggs"}), ("list_items", {})), text_msg("יש לך ביצים")]
    )
    agent = make_agent(tmp_path, client)
    r = agent.send(user_id="u1", text="הוסף ביצים")
    assert r.text == "יש לך ביצים"
    assert r.tools_used == ["add_item", "list_items"] and r.usage.model_calls == 2
    second = client.calls[1]
    results = second["messages"][2]
    assert results["role"] == "user" and [b["type"] for b in results["content"]] == ["tool_result"] * 2
    assert second["model"] == "claude-haiku-5-5" and second["output_config"] == {"effort": "low"}
    assert [t["name"] for t in second["tools"]] == ["add_item", "list_items"]


def test_tool_errors_go_back_to_the_model_and_the_turn_continues(tmp_path):
    client = FakeClient([tool_msg(("add_item", {}), ("ghost", {})), text_msg("סליחה")])
    r = make_agent(tmp_path, client).send(user_id="u1", text="x")
    blocks = client.calls[1]["messages"][2]["content"]
    assert all(b["is_error"] for b in blocks) and r.text == "סליחה"
    assert [o["is_error"] for o in r.tool_outputs] == [True, True]


def test_pause_turn_from_a_server_tool_is_continued(tmp_path):
    paused = raw_msg(
        [{"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "שקשוקה"}}],
        "pause_turn",
    )
    client = FakeClient([paused, text_msg("מצאתי")])
    r = make_agent(
        tmp_path, client, server_tools=[{"type": "web_search_20250305", "name": "web_search"}]
    ).send(user_id="u1", text="חפש שקשוקה")
    assert r.text == "מצאתי" and r.tools_used == ["web_search"] and len(client.calls) == 2
    assert client.calls[1]["messages"][-1]["role"] == "assistant"  # paused turn sent back as-is
    assert client.calls[0]["tools"][-1]["name"] == "web_search"


def test_runaway_loop_stops_with_a_friendly_message(tmp_path):
    client = FakeClient([tool_msg(("list_items", {})) for _ in range(3)])
    r = make_agent(tmp_path, client, max_iterations=3).send(user_id="u1", text="x")
    assert r.stop_reason == "tool_use" and "משהו השתבש" in r.text and len(client.calls) == 3


def test_refusal_returns_a_safe_message(tmp_path):
    client = FakeClient([raw_msg([], "refusal")])
    r = make_agent(tmp_path, client).send(user_id="u1", text="x")
    assert r.stop_reason == "refusal" and "לא יכול" in r.text


def test_context_providers_come_before_the_user_text(tmp_path):
    client = FakeClient([text_msg("ok")])
    providers = [lambda ctx, text: "<profile>allergies: peanuts</profile>", lambda ctx, text: None]
    make_agent(tmp_path, client, providers=providers).send(user_id="u1", text="מה לבשל?")
    content = client.calls[0]["messages"][0]["content"]
    assert [b["text"] for b in content] == ["<profile>allergies: peanuts</profile>", "מה לבשל?"]


def test_conversation_resumes_on_a_new_agent_instance(tmp_path):
    first = make_agent(tmp_path, FakeClient([text_msg("one")])).send(user_id="u1", text="a")
    client = FakeClient([text_msg("two")])
    second = make_agent(tmp_path, client).send(user_id="u1", text="b", conversation_id=first.conversation_id)
    assert second.conversation_id == first.conversation_id
    assert len(client.calls[0]["messages"]) == 3  # user, assistant, new user


def test_users_and_conversations_are_isolated(tmp_path):
    a = make_agent(tmp_path, FakeClient([text_msg("one")])).send(user_id="alice", text="secret")
    client = FakeClient([text_msg("two")])
    make_agent(tmp_path, client).send(user_id="bob", text="hi", conversation_id=a.conversation_id)
    assert "secret" not in json.dumps(client.calls[0]["messages"])


def test_compaction_past_the_threshold_is_billed_and_recorded(tmp_path):
    agent = make_agent(tmp_path, FakeClient([text_msg("one"), text_msg("two")]), threshold=10)
    first = agent.send(user_id="u1", text="first message")
    second = agent.send(user_id="u1", text="second", conversation_id=first.conversation_id)
    assert second.usage.model_calls == 2  # summary + answer
    sent = agent.client.calls[-1]["messages"]
    assert len(sent) == 1 and "SUMMARY of earlier talk" in sent[0]["content"][0]["text"]
    kinds = [e["type"] for e in agent.store.events("u1", first.conversation_id)]
    assert kinds == ["message", "message", "usage", "compaction", "message", "message", "usage"]


def test_events_are_emitted_in_order(tmp_path):
    events = []
    client = FakeClient([tool_msg(("list_items", {})), text_msg("ריק")])
    make_agent(tmp_path, client).send(user_id="u1", text="x", emit=events.append)
    assert [e.kind for e in events] == ["tool_call", "tool_result", "text", "done"]
    assert events[-1].data["usage"]["model_calls"] == 2


def test_history_on_disk_has_no_cache_markers(tmp_path):
    client = FakeClient([tool_msg(("list_items", {})), text_msg("ok")])
    agent = make_agent(tmp_path, client)
    r = agent.send(user_id="u1", text="x")
    assert "cache_control" not in agent.store.path("u1", r.conversation_id).read_text(encoding="utf-8")
    assert "cache_control" in json.dumps(client.calls[1])


def test_build_agent_without_a_key_fails_clearly(settings):
    with pytest.raises(MissingApiKeyError):
        build_agent(settings)
    assert build_agent(settings, client=FakeClient()).config.model == "claude-haiku-5-5"
