"""Tool registry: one place that knows every tool, its schema and how to run it.

Adding a feature = defining a Tool and registering it. The loop never changes.

    registry = ToolRegistry()
    registry.register(Tool(name="get_pantry", description="...", input_schema={...}, handler=fn))
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from chefscout.core.errors import ToolError

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 2000  # hard cap per tool_result keeps the context small


@dataclass(frozen=True)
class ToolContext:
    """What a tool may know about the current request."""

    user_id: str
    conversation_id: str
    services: dict[str, Any] = field(default_factory=dict)  # stores, providers, settings


Handler = Callable[[dict[str, Any], ToolContext], str]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler

    def to_api(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass(frozen=True)
class ToolOutcome:
    name: str
    tool_use_id: str
    content: str
    is_error: bool

    def to_block(self) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            block["is_error"] = True
        return block


def _check_args(tool: Tool, args: dict[str, Any]) -> None:
    """Light validation: required keys present, no unknown keys. Types are the tool's job."""
    schema = tool.input_schema
    props = schema.get("properties", {})
    missing = [k for k in schema.get("required", []) if k not in args]
    unknown = [k for k in args if props and k not in props]
    if missing or unknown:
        parts = []
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        if unknown:
            parts.append(f"unknown: {', '.join(unknown)}")
        raise ToolError(f"bad arguments for {tool.name} ({'; '.join(parts)})")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool
        return tool

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        """Stable order (registration order) so the prompt prefix caches."""
        return [t.to_api() for t in self._tools.values()]

    def run(self, name: str, args: dict[str, Any], tool_use_id: str, ctx: ToolContext) -> ToolOutcome:
        tool = self._tools.get(name)
        try:
            if tool is None:
                raise ToolError(f"unknown tool '{name}'")
            _check_args(tool, args)
            content, is_error = tool.handler(args, ctx), False
        except ToolError as e:
            content, is_error = f"error: {e}", True
        except Exception as e:  # a bug in a tool must not crash the conversation
            log.exception("tool %s crashed", name)
            content, is_error = f"internal error in {name}: {type(e).__name__}", True
        if len(content) > MAX_RESULT_CHARS:
            content = content[:MAX_RESULT_CHARS] + " ...[truncated]"
        log.info("tool %s -> %s%d chars", name, "ERROR " if is_error else "", len(content))
        return ToolOutcome(name, tool_use_id, content, is_error)

    def run_all(self, tool_uses: list[dict[str, Any]], ctx: ToolContext) -> list[ToolOutcome]:
        """Runs every tool_use block of one assistant message, in order."""
        return [self.run(b["name"], b.get("input") or {}, b["id"], ctx) for b in tool_uses]
