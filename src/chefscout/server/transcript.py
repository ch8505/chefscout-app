"""What the user saw in one conversation, so a page refresh shows the whole chat again.

    data/users/<user_id>/conversations/<conversation_id>.transcript.json

The model's history (messages with tool calls and context blocks) is not what the user saw:
the answer there is before the output filters, and button notes are mixed in. So the page keeps
its own short record: the user's text, the answer as shown, the ideas list as it was then, and the
ids of the recipe cards. Cards are rebuilt from the recipe store when read, so their "missing"
list follows today's pantry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chefscout.core.history import safe_id
from chefscout.memory.storage import locked, read_json, write_json

MAX_ENTRIES = 300


class TranscriptStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, user_id: str, conversation_id: str) -> Path:
        user = safe_id(user_id, "user id")
        conv = safe_id(conversation_id, "conversation id")
        return self.data_dir / "users" / user / "conversations" / f"{conv}.transcript.json"

    def entries(self, user_id: str, conversation_id: str) -> list[dict[str, Any]]:
        return list(read_json(self.path(user_id, conversation_id), []))

    def add(self, user_id: str, conversation_id: str, *entries: dict[str, Any]) -> None:
        path = self.path(user_id, conversation_id)
        with locked(path):
            items = [*self.entries(user_id, conversation_id), *entries]
            write_json(path, items[-MAX_ENTRIES:])
