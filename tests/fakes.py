"""A fake Messages API client built from the real anthropic response types."""

from __future__ import annotations

import copy
import itertools
from types import SimpleNamespace
from typing import Any

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

_ids = itertools.count(1)


def usage(inp: int = 100, out: int = 20, cache_read: int = 0, cache_write: int = 0) -> Usage:
    return Usage(
        input_tokens=inp,
        output_tokens=out,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


def text_msg(text: str, stop: str = "end_turn", u: Usage | None = None) -> Message:
    return _msg([TextBlock(type="text", text=text)], stop, u)


def tool_msg(
    *calls: tuple[str, dict[str, Any]], u: Usage | None = None, searched: list[str] | None = None
) -> Any:
    """The model calls tools. searched=[urls]: it first ran a web search that returned those urls."""
    if searched is not None:
        return raw_msg([*search_blocks(searched), *tool_use_blocks(calls)], "tool_use", u)
    blocks = [ToolUseBlock(type="tool_use", id=f"toolu_{next(_ids)}", name=n, input=a) for n, a in calls]
    return _msg(blocks, "tool_use", u)


def search_blocks(urls: list[str], query: str = "מתכון") -> list[dict[str, Any]]:
    sid = f"srvtoolu_{next(_ids)}"
    return [
        {"type": "server_tool_use", "id": sid, "name": "web_search", "input": {"query": query}},
        {
            "type": "web_search_tool_result",
            "tool_use_id": sid,
            "content": [
                {"type": "web_search_result", "url": url, "title": "t", "encrypted_content": "x"}
                for url in urls
            ],
        },
    ]


def tool_use_blocks(calls: tuple[tuple[str, dict[str, Any]], ...]) -> list[dict[str, Any]]:
    return [{"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": n, "input": a} for n, a in calls]


def raw_msg(content: list[dict[str, Any]], stop: str, u: Usage | None = None) -> SimpleNamespace:
    """For block types that are awkward to build (server tools): plain dicts."""
    return SimpleNamespace(content=content, stop_reason=stop, usage=u or usage())


def _msg(content: list[Any], stop: str, u: Usage | None) -> Message:
    return Message(
        id=f"msg_{next(_ids)}",
        type="message",
        role="assistant",
        model="claude-haiku-5-5",
        content=content,
        stop_reason=stop,
        stop_sequence=None,
        usage=u or usage(),
    )


class FakeClient:
    """Plays responses in order. Summary requests (no tools) get a fixed summary."""

    def __init__(self, responses: list[Any] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(copy.deepcopy(kwargs))
        if "tools" not in kwargs:
            return text_msg("SUMMARY of earlier talk")
        if not self.responses:
            raise AssertionError("FakeClient ran out of responses")
        return self.responses.pop(0)
