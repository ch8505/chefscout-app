"""Token usage and cost, per model call and per user turn."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

log = logging.getLogger(__name__)

# USD per 1M tokens. Cache write = 5-minute TTL. Verify against the pricing page
# before relying on the totals: Haiku 5.5 cache prices use the standard 1.25x / 0.1x multipliers.
PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.10},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
}
WEB_SEARCH_USD = 10.0 / 1000  # per search request


@dataclass
class TurnUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0
    model_calls: int = 0
    cost_usd: float = 0.0

    @property
    def context_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def call_cost(model: str, inp: int, out: int, cache_read: int, cache_write: int, searches: int) -> float:
    p = PRICES.get(model)
    if p is None:
        log.warning("no price for model %s; cost reported as 0", model)
        return 0.0
    tokens = (
        inp * p["input"] + out * p["output"] + cache_read * p["cache_read"] + cache_write * p["cache_write"]
    )
    return tokens / 1e6 + searches * WEB_SEARCH_USD


class UsageMeter:
    """Accumulates one turn. last_request_tokens feeds the compaction check."""

    def __init__(self, model: str):
        self.model = model
        self.turn = TurnUsage()
        self.last_request_tokens = 0

    def record(self, usage: Any, *, counts_as_context: bool = True) -> None:
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        searches = (getattr(server, "web_search_requests", 0) or 0) if server else 0
        cost = call_cost(self.model, inp, out, cr, cw, searches)
        t = self.turn
        t.input_tokens += inp
        t.output_tokens += out
        t.cache_read_tokens += cr
        t.cache_write_tokens += cw
        t.web_searches += searches
        t.model_calls += 1
        t.cost_usd = round(t.cost_usd + cost, 6)
        if counts_as_context:
            self.last_request_tokens = inp + cr + cw
        log.info(
            "model call #%d: in=%d cache_read=%d cache_write=%d out=%d searches=%d cost=$%.5f",
            t.model_calls,
            inp,
            cr,
            cw,
            out,
            searches,
            cost,
        )
