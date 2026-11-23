"""Unit tests: tool registry, history store, caching, usage."""

from __future__ import annotations

import json

import pytest
from tests.fakes import usage

from chefscout.core import caching
from chefscout.core.errors import InvalidIdError, ToolError
from chefscout.core.history import ConversationStore, safe_id
from chefscout.core.tools import MAX_RESULT_CHARS, Tool, ToolContext, ToolRegistry
from chefscout.core.usage import WEB_SEARCH_USD, UsageMeter, call_cost

CTX = ToolContext(user_id="u1", conversation_id="c1")


def make_tool(name="echo", handler=None, required=("text",)):
    return Tool(
        name=name,
        description="test tool",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": list(required),
        },
        handler=handler or (lambda args, ctx: f"echo:{args['text']}"),
    )


# ---- tools -------------------------------------------------------------------


def test_registry_runs_a_tool():
    reg = ToolRegistry()
    reg.register(make_tool())
    out = reg.run("echo", {"text": "hi"}, "t1", CTX)
    assert (out.content, out.is_error) == ("echo:hi", False)
    assert out.to_block() == {"type": "tool_result", "tool_use_id": "t1", "content": "echo:hi"}


def test_registry_rejects_duplicate_names():
    reg = ToolRegistry()
    reg.register(make_tool())
    with pytest.raises(ValueError):
        reg.register(make_tool())


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("nope", {}, "unknown tool"),
        ("echo", {}, "missing: text"),
        ("echo", {"text": "a", "extra": 1}, "unknown: extra"),
    ],
)
def test_bad_calls_become_is_error_results(name, args, expected):
    reg = ToolRegistry()
    reg.register(make_tool())
    out = reg.run(name, args, "t1", CTX)
    assert out.is_error and expected in out.content
    assert out.to_block()["is_error"] is True


def test_tool_error_and_crash_are_reported_without_internals():
    def fails(args, ctx):
        raise ToolError("site is down")

    def crashes(args, ctx):
        raise KeyError("secret internal detail")

    reg = ToolRegistry()
    reg.register(make_tool("fails", fails, required=()))
    reg.register(make_tool("crashes", crashes, required=()))
    assert reg.run("fails", {}, "t", CTX).content == "error: site is down"
    crashed = reg.run("crashes", {}, "t", CTX)
    assert crashed.is_error and "secret" not in crashed.content


def test_long_results_are_truncated():
    reg = ToolRegistry()
    reg.register(make_tool(handler=lambda a, c: "x" * 10_000))
    assert len(reg.run("echo", {"text": "a"}, "t", CTX).content) < MAX_RESULT_CHARS + 50


# ---- history -----------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", "x" * 65, "ש"])
def test_ids_that_could_escape_the_data_dir_are_rejected(bad):
    with pytest.raises(InvalidIdError):
        safe_id(bad)


def test_store_is_append_only_and_replays_after_compaction(tmp_path):
    store = ConversationStore(tmp_path)
    m = lambda role, t: {"role": role, "content": [{"type": "text", "text": t}]}  # noqa: E731
    store.append_message("u1", "c1", m("user", "a"))
    store.append_message("u1", "c1", m("assistant", "b"))
    store.append_compaction("u1", "c1", "summary", replaced=2)
    store.append_message("u1", "c1", m("user", "c"))
    store.append_usage("u1", "c1", {"cost_usd": 0.001})
    assert [e["type"] for e in store.events("u1", "c1")] == [
        "message",
        "message",
        "compaction",
        "message",
        "usage",
    ]
    assert store.load_messages("u1", "c1") == [m("user", "c")]
    assert store.load_messages("u1", "other") == []


# ---- caching -----------------------------------------------------------------


def test_three_breakpoints_on_copies_only():
    tools = [{"name": "a"}, {"name": "b"}]
    system = [{"type": "text", "text": "s"}]
    messages = [{"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "text", "text": "y"}]}]
    t, s, m = caching.apply(tools, system, messages)
    assert t[-1]["cache_control"] == s[-1]["cache_control"] == m[-1]["content"][-1]["cache_control"]
    assert "cache_control" not in m[-1]["content"][0]
    assert "cache_control" not in json.dumps([tools, system, messages])  # originals untouched


def test_thinking_blocks_are_never_marked():
    msgs = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "t"}, {"type": "thinking", "thinking": ""}],
        }
    ]
    _, _, m = caching.apply([], [], msgs)
    assert "cache_control" not in m[0]["content"][1] and "cache_control" in m[0]["content"][0]


# ---- usage -------------------------------------------------------------------


def test_cost_math_for_haiku():
    assert call_cost("claude-haiku-5-5", 1_000_000, 0, 0, 0, 0) == pytest.approx(0.10)
    assert call_cost("claude-haiku-5-5", 0, 1_000_000, 0, 0, 0) == pytest.approx(0.50)
    assert call_cost("claude-haiku-5-5", 0, 0, 0, 0, 3) == pytest.approx(3 * WEB_SEARCH_USD)
    assert call_cost("unknown-model", 1000, 1000, 0, 0, 0) == 0.0


def test_meter_accumulates_a_turn():
    meter = UsageMeter("claude-haiku-5-5")
    meter.record(usage(100, 10, cache_read=900, cache_write=50))
    meter.record(usage(5, 20, cache_read=1000))
    t = meter.turn
    assert (t.model_calls, t.input_tokens, t.cache_read_tokens, t.output_tokens) == (2, 105, 1900, 30)
    assert meter.last_request_tokens == 1005
