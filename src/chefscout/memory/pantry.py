"""What the user has at home, for one conversation only.

    data/users/<user_id>/conversations/<conversation_id>.pantry.json

A new conversation has a new id, so it starts with an empty pantry.
"""

from __future__ import annotations

from pathlib import Path

from chefscout.core.history import safe_id
from chefscout.memory.storage import locked, normalize, read_json, write_json


class PantryStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, user_id: str, conversation_id: str) -> Path:
        user = safe_id(user_id, "user id")
        conv = safe_id(conversation_id, "conversation id")
        return self.data_dir / "users" / user / "conversations" / f"{conv}.pantry.json"

    def items(self, user_id: str, conversation_id: str) -> list[str]:
        return list(read_json(self.path(user_id, conversation_id), []))

    def update(
        self,
        user_id: str,
        conversation_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        clear: bool = False,
    ) -> list[str]:
        path = self.path(user_id, conversation_id)
        with locked(path):
            current = [] if clear else self.items(user_id, conversation_id)
            drop = {normalize(x) for x in remove or []}
            current = [x for x in current if x not in drop]
            for item in map(normalize, add or []):
                if item and item not in current:
                    current.append(item)
            write_json(path, current)
        return current
