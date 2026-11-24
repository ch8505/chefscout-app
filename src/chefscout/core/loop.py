"""The agent loop: call the model until it stops asking for tools.

- stop_reason "tool_use": run our tools, send ALL results back in ONE user message.
- stop_reason "pause_turn": a server tool (web search) paused a long turn; send it back to continue.
- anything else ends the turn.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from chefscout.core import caching
from chefscout.core.events import AgentEvent, EventSink
from chefscout.core.history import to_dict
from chefscout.core.tools import ToolContext, ToolRegistry
from chefscout.core.usage import UsageMeter

log = logging.getLogger(__name__)


@dataclass
class LoopConfig:
    model: str
    effort: str
    max_output_tokens: int
    max_iterations: int
    system: list[dict[str, Any]]
    server_tools: list[dict[str, Any]] = field(default_factory=list)  # e.g. web search
    # Called with every block the model returns, e.g. to remember which urls a web search found.
    block_observers: list[Callable[[ToolContext, dict[str, Any]], None]] = field(default_factory=list)


@dataclass
class LoopResult:
    text: str
    stop_reason: str
    tools_used: list[str]
    tool_outputs: list[dict[str, Any]]  # name, input, content, is_error (for the UI's recipe cards)


def run_turn(
    *,
    client: Any,
    config: LoopConfig,
    registry: ToolRegistry,
    messages: list[dict[str, Any]],
    ctx: ToolContext,
    meter: UsageMeter,
    persist: Callable[[dict[str, Any]], None],
    emit: EventSink,
) -> LoopResult:
    """messages must end with the new user message. persist(message) stores each new message."""
    tools_used: list[str] = []
    tool_outputs: list[dict[str, Any]] = []
    stop_reason = "max_iterations"
    for iteration in range(1, config.max_iterations + 1):
        tools, system, msgs = caching.apply(registry.schemas() + config.server_tools, config.system, messages)
        log.debug("iteration %d: %d messages", iteration, len(msgs))
        resp = client.messages.create(
            model=config.model,
            max_tokens=config.max_output_tokens,
            system=system,
            tools=tools,
            messages=msgs,
            output_config={"effort": config.effort},
        )
        meter.record(resp.usage)
        assistant = {"role": "assistant", "content": [to_dict(b) for b in resp.content]}
        messages.append(assistant)
        persist(assistant)
        stop_reason = resp.stop_reason or "unknown"

        for b in assistant["content"]:
            for observe in config.block_observers:
                observe(ctx, b)
            if b.get("type") == "server_tool_use":
                tools_used.append(b["name"])
                emit(AgentEvent("tool_call", {"name": b["name"], "input": b.get("input", {})}))

        if stop_reason == "pause_turn":
            continue
        if stop_reason != "tool_use":
            break

        tool_uses = [b for b in assistant["content"] if b.get("type") == "tool_use"]
        for b in tool_uses:
            emit(AgentEvent("tool_call", {"name": b["name"], "input": b.get("input", {})}))
        outcomes = registry.run_all(tool_uses, ctx)
        for b, o in zip(tool_uses, outcomes, strict=True):
            tools_used.append(o.name)
            tool_outputs.append(
                {"name": o.name, "input": b.get("input", {}), "content": o.content, "is_error": o.is_error}
            )
            emit(AgentEvent("tool_result", {"name": o.name, "is_error": o.is_error}))
        results = {"role": "user", "content": [o.to_block() for o in outcomes]}  # one message
        messages.append(results)
        persist(results)
    else:
        log.warning("turn stopped after %d iterations", config.max_iterations)

    if stop_reason in ("max_tokens", "refusal", "max_iterations"):
        log.warning("turn ended with stop_reason=%s", stop_reason)
    text = "".join(b["text"] for b in messages[-1]["content"] if b.get("type") == "text").strip()
    if messages[-1]["role"] != "assistant" or not text:
        text = _fallback_text(stop_reason)
    return LoopResult(text, stop_reason, tools_used, tool_outputs)


def _fallback_text(stop_reason: str) -> str:
    if stop_reason == "refusal":
        return "מצטער, אני לא יכול לעזור בבקשה הזאת."
    return "מצטער, משהו השתבש באמצע. אפשר לנסות שוב?"
