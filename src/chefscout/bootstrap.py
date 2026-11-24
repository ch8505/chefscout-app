"""Composition root: the one place that wires settings, stores, tools and the agent together.

Features plug in here (a new tool, a context provider, an output filter) without touching the core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chefscout.config import Settings
from chefscout.context import history_provider, pantry_provider, plan_provider, profile_provider
from chefscout.core.agent import Agent, AgentConfig
from chefscout.core.history import ConversationStore
from chefscout.core.llm import build_client
from chefscout.core.tools import ToolRegistry
from chefscout.memory.history import HistoryStore
from chefscout.memory.pantry import PantryStore
from chefscout.memory.profile import ProfileStore
from chefscout.prompts import SYSTEM_PROMPT
from chefscout.recipes.fetch import PageFetcher
from chefscout.recipes.guard import link_guard
from chefscout.recipes.plan import PlanStore
from chefscout.recipes.store import RecipeStore, search_url_collector
from chefscout.tools import feedback_tools, memory_tools, plan_tools, recipe_tools, substitution_tools


@dataclass(frozen=True)
class Stores:
    conversations: ConversationStore
    profiles: ProfileStore
    pantry: PantryStore
    recipes: RecipeStore
    plans: PlanStore
    history: HistoryStore


def build_stores(settings: Settings) -> Stores:
    return Stores(
        conversations=ConversationStore(settings.data_dir),
        profiles=ProfileStore(settings.data_dir),
        pantry=PantryStore(settings.data_dir),
        recipes=RecipeStore(settings.data_dir),
        plans=PlanStore(settings.data_dir),
        history=HistoryStore(settings.data_dir),
    )


def build_registry() -> ToolRegistry:
    """All our tools, in a fixed order (keeps the prompt cache stable)."""
    registry = ToolRegistry()
    tools = [
        *memory_tools.TOOLS,
        *plan_tools.TOOLS,
        *recipe_tools.TOOLS,
        *substitution_tools.TOOLS,
        *feedback_tools.TOOLS,
    ]
    for tool in tools:
        registry.register(tool)
    return registry


def web_search_tool(settings: Settings) -> dict[str, Any]:
    """Claude's server-side web search. Hebrew queries bring Hebrew sites.

    No user_location by default: the API rejects country code IL ("Country code IL is not supported").
    """
    tool: dict[str, Any] = {
        "type": settings.web_search_tool,
        "name": "web_search",
        "max_uses": settings.max_web_searches,
    }
    if settings.web_search_country:
        tool["user_location"] = {"type": "approximate", "country": settings.web_search_country}
    return tool


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
        context_providers=[
            profile_provider(stores.profiles),
            pantry_provider(stores.pantry),
            plan_provider(stores.plans),
            history_provider(stores.history, stores.profiles, settings),
        ],
        server_tools=[web_search_tool(settings)] if settings.max_web_searches > 0 else [],
        output_filters=[link_guard(stores.recipes), plan_tools.ideas_brief],
        block_observers=[search_url_collector(stores.recipes)],
        services={
            "settings": settings,
            "profiles": stores.profiles,
            "pantry": stores.pantry,
            "recipes": stores.recipes,
            "plans": stores.plans,
            "history": stores.history,
            "fetcher": fetcher if fetcher is not None else PageFetcher(),
        },
    )
