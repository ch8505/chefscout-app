"""Conversations on disk: one append-only JSONL file per conversation.

    data/users/<user_id>/conversations/<conversation_id>.jsonl

Event types: "message", "compaction" (a summary replaces everything before it)
and "usage" (per-turn cost record). Lines are only ever appended.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from chefscout.core.errors import InvalidIdError

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_id(value: str, kind: str = "id") -> str:
    """Ids become file names, so allow only a strict character set (no path traversal)."""
    if not isinstance(value, str) or not _SAFE_ID.match(value):
        raise InvalidIdError(f"invalid {kind}: {value!r}")
    return value


def new_conversation_id() -> str:
    return uuid.uuid4().hex


def to_dict(block: Any) -> dict[str, Any]:
    """SDK content block -> plain JSON dict that the API accepts back unchanged."""
    if isinstance(block, dict):
        return block
    return block.model_dump(mode="json", exclude_none=True)


class ConversationStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, user_id: str, conversation_id: str) -> Path:
        user = safe_id(user_id, "user id")
        conv = safe_id(conversation_id, "conversation id")
        return self.data_dir / "users" / user / "conversations" / f"{conv}.jsonl"

    def _append(self, user_id: str, conversation_id: str, event: dict[str, Any]) -> None:
        p = self.path(user_id, conversation_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def append_message(self, user_id: str, conversation_id: str, message: dict[str, Any]) -> None:
        self._append(user_id, conversation_id, {"type": "message", "message": message})

    def append_compaction(self, user_id: str, conversation_id: str, summary: str, replaced: int) -> None:
        self._append(
            user_id,
            conversation_id,
            {"type": "compaction", "summary": summary, "replaced_messages": replaced},
        )

    def append_usage(self, user_id: str, conversation_id: str, usage: dict[str, Any]) -> None:
        self._append(user_id, conversation_id, {"type": "usage", **usage})

    def events(self, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
        p = self.path(user_id, conversation_id)
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def load_messages(self, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
        """The messages the model should see: everything after the last compaction."""
        messages: list[dict[str, Any]] = []
        for ev in self.events(user_id, conversation_id):
            if ev["type"] == "compaction":
                messages = []
            elif ev["type"] == "message":
                messages.append(ev["message"])
        return messages

    def exists(self, user_id: str, conversation_id: str) -> bool:
        return self.path(user_id, conversation_id).exists()
