"""The Agent: one user turn = load history, compact if needed, add context, run the loop, save.

The Agent holds no conversation state between calls, so one instance can
serve every request of the web server.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from chefscout.core.compaction import Compactor
from chefscout.core.events import AgentEvent, EventSink, ignore
from chefscout.core.history import ConversationStore, new_conversation_id, safe_id
from chefscout.core.loop import LoopConfig, run_turn
from chefscout.core.tools import ToolContext, ToolRegistry
from chefscout.core.usage import TurnUsage, UsageMeter

log = logging.getLogger(__name__)

# A context provider returns one text block for this turn (or None to add nothing),
# e.g. "<profile>allergies: peanuts</profile>". Providers decide what is relevant.
ContextProvider = Callable[[ToolContext, str], str | None]
# An output filter may rewrite the final answer (e.g. remove links that did not come from a tool).
OutputFilter = Callable[[ToolContext, str], str]


@dataclass(frozen=True)
class AgentConfig:
    model: str
    effort: str
    max_output_tokens: int
    max_iterations: int
    compaction_threshold_tokens: int
    system_prompt: str


@dataclass
class TurnResult:
    conversation_id: str
    text: str
    stop_reason: str
    tools_used: list[str]
    tool_outputs: list[dict[str, Any]]
    usage: TurnUsage


class Agent:
    def __init__(
        self,
        *,
        client: Any,
        config: AgentConfig,
        registry: ToolRegistry,
        store: ConversationStore,
        context_providers: Sequence[ContextProvider] = (),
        server_tools: Sequence[dict[str, Any]] = (),
        output_filters: Sequence[OutputFilter] = (),
        services: dict[str, Any] | None = None,
    ):
        self.client = client
        self.config = config
        self.registry = registry
        self.store = store
        self.context_providers = list(context_providers)
        self.output_filters = list(output_filters)
        self.services = services or {}
        self.compactor = Compactor(client, config.model, config.compaction_threshold_tokens)
        self.loop_config = LoopConfig(
            model=config.model,
            effort=config.effort,
            max_output_tokens=config.max_output_tokens,
            max_iterations=config.max_iterations,
            system=[{"type": "text", "text": config.system_prompt}],
            server_tools=list(server_tools),
        )

    def send(
        self, *, user_id: str, text: str, conversation_id: str | None = None, emit: EventSink = ignore
    ) -> TurnResult:
        user_id = safe_id(user_id, "user id")
        conv = safe_id(conversation_id or new_conversation_id(), "conversation id")
        ctx = ToolContext(user_id=user_id, conversation_id=conv, services=self.services)
        messages = self.store.load_messages(user_id, conv)
        meter = UsageMeter(self.config.model)
        meter.last_request_tokens = self._last_request_tokens(user_id, conv)

        def persist(message: dict[str, Any]) -> None:
            self.store.append_message(user_id, conv, message)

        summary = None
        if self.compactor.needed(messages, meter.last_request_tokens):
            summary, usage = self.compactor.summarize(messages)
            meter.record(usage, counts_as_context=False)
            self.store.append_compaction(user_id, conv, summary, len(messages))
            messages = []
            emit(AgentEvent("compaction", {"replaced": True}))

        user_message = self._user_message(ctx, text, summary)
        messages.append(user_message)
        persist(user_message)

        result = run_turn(
            client=self.client,
            config=self.loop_config,
            registry=self.registry,
            messages=messages,
            ctx=ctx,
            meter=meter,
            persist=persist,
            emit=emit,
        )
        self.store.append_usage(
            user_id, conv, {**meter.turn.to_dict(), "last_request_tokens": meter.last_request_tokens}
        )
        answer = result.text
        for output_filter in self.output_filters:
            answer = output_filter(ctx, answer)
        emit(AgentEvent("text", {"text": answer}))
        emit(AgentEvent("done", {"conversation_id": conv, "usage": meter.turn.to_dict()}))
        log.info("turn done: conv=%s calls=%d cost=$%.5f", conv, meter.turn.model_calls, meter.turn.cost_usd)
        return TurnResult(
            conv, answer, result.stop_reason, result.tools_used, result.tool_outputs, meter.turn
        )

    def _user_message(self, ctx: ToolContext, text: str, summary: str | None) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        if summary:
            blocks.append(
                {"type": "text", "text": f"<conversation_summary>\n{summary}\n</conversation_summary>"}
            )
        for provider in self.context_providers:
            block = provider(ctx, text)
            if block:
                blocks.append({"type": "text", "text": block})
        blocks.append({"type": "text", "text": text})
        return {"role": "user", "content": blocks}

    def _last_request_tokens(self, user_id: str, conv: str) -> int:
        usage = [e for e in self.store.events(user_id, conv) if e["type"] in ("usage", "compaction")]
        if not usage or usage[-1]["type"] == "compaction":
            return 0
        return int(usage[-1].get("last_request_tokens", 0))
