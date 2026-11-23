"""Context window management: past a threshold, summarize the whole conversation.

Runs at the start of a turn, before the new user message. The summary replaces
all earlier messages (they stay on disk behind a compaction marker) and is put
in the next user message. No old assistant message survives, so no old
thinking block is replayed into a changed prefix.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "Summarize this cooking-assistant conversation for the assistant itself, in at most 120 words. "
    "Keep: what the user asked for, recipes suggested (titles and URLs), decisions, open requests. "
    "Saved profile facts need no detail. Write in the user's language. Output only the summary."
)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough size when no request has been measured yet (about 4 characters per token)."""
    return len(json.dumps(messages, ensure_ascii=False)) // 4


def transcript(messages: list[dict[str, Any]], result_chars: int = 200) -> str:
    """Plain-text view of the conversation; tool results are shortened."""
    lines: list[str] = []
    for m in messages:
        for b in m["content"]:
            kind = b.get("type")
            if kind == "text":
                lines.append(f"{m['role']}: {b['text']}")
            elif kind in ("tool_use", "server_tool_use"):
                lines.append(
                    f"assistant called {b['name']}({json.dumps(b.get('input'), ensure_ascii=False)})"
                )
            elif kind == "tool_result":
                content = b.get("content")
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                lines.append(f"tool result: {text[:result_chars]}")
    return "\n".join(lines)


class Compactor:
    def __init__(self, client: Any, model: str, threshold_tokens: int):
        self.client = client
        self.model = model
        self.threshold = threshold_tokens

    def needed(self, messages: list[dict[str, Any]], last_request_tokens: int) -> bool:
        size = max(last_request_tokens, estimate_tokens(messages))
        return bool(messages) and size > self.threshold

    def summarize(self, messages: list[dict[str, Any]]) -> tuple[str, Any]:
        """Returns (summary, usage) so the caller can bill the extra call."""
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SUMMARY_PROMPT,
            messages=[{"role": "user", "content": transcript(messages)}],
            output_config={"effort": "low"},
        )
        summary = "".join(b.text for b in resp.content if b.type == "text").strip()
        log.info("compacted %d messages into %d chars", len(messages), len(summary))
        return summary, resp.usage
