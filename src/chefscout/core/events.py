"""Events the agent emits while it works. The server will stream them to the browser."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal["text", "tool_call", "tool_result", "compaction", "done", "error"]


@dataclass(frozen=True)
class AgentEvent:
    kind: EventKind
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[AgentEvent], None]


def ignore(_: AgentEvent) -> None:
    """Default sink: drop events."""
