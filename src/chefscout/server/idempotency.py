"""Do a button press only once: a double click or a retry with the same request_id changes nothing.

    data/users/<user_id>/conversations/<conversation_id>.requests.json

Callers check and record inside the conversation's lock, so two parallel requests with the same id
cannot both run.
"""

from __future__ import annotations

from pathlib import Path

from chefscout.core.history import safe_id
from chefscout.memory.storage import read_json, write_json

KEEP = 100  # recent request ids per conversation


class RequestLog:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def path(self, user_id: str, conversation_id: str) -> Path:
        user = safe_id(user_id, "user id")
        conv = safe_id(conversation_id, "conversation id")
        return self.data_dir / "users" / user / "conversations" / f"{conv}.requests.json"

    def seen(self, user_id: str, conversation_id: str, request_id: str) -> bool:
        return request_id in read_json(self.path(user_id, conversation_id), [])

    def record(self, user_id: str, conversation_id: str, request_id: str) -> None:
        path = self.path(user_id, conversation_id)
        ids = [i for i in read_json(path, []) if i != request_id]
        write_json(path, [*ids, request_id][-KEEP:])

    def first_time(self, user_id: str, conversation_id: str, request_id: str | None) -> bool:
        """True (and recorded) the first time; False for a repeat. No id: always True."""
        if not request_id:
            return True
        if self.seen(user_id, conversation_id, request_id):
            return False
        self.record(user_id, conversation_id, request_id)
        return True
