"""Prompt caching breakpoints.

Render order is tools -> system -> messages. We mark:
  1. the last tool              (fixed list)
  2. the system prompt          (fixed text)
  3. the last cacheable block of the last message (rolling: the next call reads the history from cache)
3 of the 4 allowed. We mark copies, so stored history never contains cache_control.
"""

from __future__ import annotations

import copy
from typing import Any

EPHEMERAL = {"type": "ephemeral"}
# Blocks that accept cache_control. Thinking blocks do not.
_CACHEABLE = {"text", "tool_result", "tool_use", "image", "document"}


def apply(
    tools: list[dict[str, Any]], system: list[dict[str, Any]], messages: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    tools, system, messages = copy.deepcopy(tools), copy.deepcopy(system), copy.deepcopy(messages)
    if tools:
        tools[-1]["cache_control"] = EPHEMERAL
    if system:
        system[-1]["cache_control"] = EPHEMERAL
    if messages:
        for block in reversed(messages[-1]["content"]):
            if block.get("type") in _CACHEABLE:
                block["cache_control"] = EPHEMERAL
                break
    return tools, system, messages
