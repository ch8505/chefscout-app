"""Composition root: the one place that wires settings, stores, tools and the agent together.

Features plug in here (a new tool, a context provider, an output filter) without touching the core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chefscout.config import Settings
from chefscout.context import pantry_provider, profile_provider
from chefscout.core.agent import Agent, AgentConfig
from chefscout.core.history import ConversationStore
from chefscout.core.llm import build_client
from chefscout.core.tools import ToolRegistry
from chefscout.memory.pantry import PantryStore
from chefscout.memory.profile import ProfileStore
from chefscout.prompts import SYSTEM_PROMPT
from chefscout.recipes.fetch import PageFetcher
from chefscout.recipes.guard import link_guard
from chefscout.recipes.store import RecipeStore
from chefscout.tools import memory_tools, recipe_tools, substitution_tools


@dataclass(frozen=True)
class Stores:
    conversations: ConversationStore
    profiles: ProfileStore
    pantry: PantryStore
    recipes: RecipeStore


def build_stores(settings: Settings) -> Stores:
    return Stores(
        conversations=ConversationStore(settings.data_dir),
        profiles=ProfileStore(settings.data_dir),
        pantry=PantryStore(settings.data_dir),
        recipes=RecipeStore(settings.data_dir),
    )


def build_registry() -> ToolRegistry:
    """All our tools, in a fixed order (keeps the prompt cache stable)."""
    registry = ToolRegistry()
    for tool in [*memory_tools.TOOLS, *recipe_tools.TOOLS, *substitution_tools.TOOLS]:
        registry.register(tool)
    return registry


def web_search_tool(settings: Settings) -> dict[str, Any]:
    """Claude's server-side web search, focused on Israel."""
    return {
        "type": settings.web_search_tool,
        "name": "web_search",
        "max_uses": settings.max_web_searches,
        "user_location": {"type": "approximate", "country": "IL", "timezone": "Asia/Jerusalem"},
    }


def build_agent(
    settings: Settings,
    client: Any | None = None,
    stores: Stores | None = None,
    fetcher: Any | None = None,
) -> Agent:
    stores = stores or build_stores(settings)
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
        store=stores.conversations,
        context_providers=[profile_provider(stores.profiles), pantry_provider(stores.pantry)],
        server_tools=[web_search_tool(settings)] if settings.max_web_searches > 0 else [],
        output_filters=[link_guard(stores.recipes)],
        services={
            "settings": settings,
            "profiles": stores.profiles,
            "pantry": stores.pantry,
            "recipes": stores.recipes,
            "fetcher": fetcher if fetcher is not None else PageFetcher(),
        },
    )
