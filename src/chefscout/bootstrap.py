"""Composition root: the one place that wires settings, stores, tools and the agent together.

Features plug in here (a new tool, a new context provider) without touching the core.
"""

from __future__ import annotations

from typing import Any

from chefscout.config import Settings
from chefscout.core.agent import Agent, AgentConfig
from chefscout.core.history import ConversationStore
from chefscout.core.llm import build_client
from chefscout.core.tools import ToolRegistry
from chefscout.prompts import SYSTEM_PROMPT


def build_registry() -> ToolRegistry:
    """All tools the agent can use. Domain tools are registered here in later stages."""
    return ToolRegistry()


def build_agent(settings: Settings, client: Any | None = None) -> Agent:
    return Agent(
        client=client if client is not None else build_client(settings),
        config=AgentConfig(
            model=settings.model,
            effort=settings.effort,
            max_output_tokens=settings.max_output_tokens,
            max_iterations=settings.max_tool_iterations,
            compaction_threshold_tokens=settings.compaction_threshold_tokens,
            system_prompt=SYSTEM_PROMPT,
        ),
        registry=build_registry(),
        store=ConversationStore(settings.data_dir),
        services={"settings": settings},
    )
