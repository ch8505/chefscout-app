"""Streams one chat turn to the browser as Server-Sent Events (SSE).

Events: progress (a friendly Hebrew label per tool), answer, recipes (cards), done, error.
The agent runs in a worker thread; its events go through a queue to the HTTP response.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from chefscout.core.agent import Agent
from chefscout.core.errors import ChefScoutError, MissingApiKeyError
from chefscout.core.events import AgentEvent
from chefscout.core.history import new_conversation_id
from chefscout.memory.storage import locked
from chefscout.server.schemas import RecipeCard

log = logging.getLogger(__name__)

PROGRESS_LABELS = {
    "web_search": "מחפש מתכונים ברשת…",
    "read_recipes": "קורא את המתכונים ובודק שהם מתאימים לך…",
    "show_recipes": "מכין את הכרטיסים…",
    "suggest_substitute": "מחפש תחליף…",
    "remember_preference": "שומר בפרופיל…",
    "forget_preference": "מעדכן את הפרופיל…",
    "update_pantry": "מעדכן מה יש בבית…",
    "save_cooked_recipe": "שומר שהכנת…",
    "get_cooked_recipes": "בודק מה הכנת בעבר…",
}

_DONE = object()


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, MissingApiKeyError):
        return "חסר מפתח API. יש להוסיף אותו לקובץ .env (ראו .env.example) ולהפעיל מחדש את השרת."
    name = type(exc).__name__
    if name == "RateLimitError":
        return "יותר מדי בקשות כרגע. אפשר לנסות שוב בעוד דקה."
    if name in ("APIConnectionError", "APITimeoutError"):
        return "אין חיבור לשירות של Claude. כדאי לבדוק את החיבור לאינטרנט (או סינון כמו נטפרי)."
    if name == "AuthenticationError":
        return "מפתח ה-API לא תקין. כדאי לבדוק אותו בקובץ .env."
    if name == "PermissionDeniedError" or "credit" in str(exc).lower():
        return "אין הרשאה או שנגמר הקרדיט בחשבון ה-API. כדאי לבדוק ב-Claude Console."
    return "משהו השתבש. אפשר לנסות שוב."


def stream_turn(
    *,
    get_agent: Callable[[], Agent],
    user_id: str,
    message: str,
    conversation_id: str | None,
) -> Iterator[str]:
    conv = conversation_id or new_conversation_id()
    events: queue.Queue[Any] = queue.Queue()

    def emit(event: AgentEvent) -> None:
        events.put(event)

    def work() -> None:
        try:
            agent = get_agent()
            with locked(agent.store.path(user_id, conv)):  # one turn at a time per conversation
                result = agent.send(user_id=user_id, text=message, conversation_id=conv, emit=emit)
            if "show_recipes" in result.tools_used:
                shown = agent.services["recipes"].last_shown(user_id, conv)
                events.put(("recipes", [RecipeCard.from_checked(s).model_dump() for s in shown]))
        except (ChefScoutError, Exception) as exc:
            log.exception("chat turn failed")
            events.put(("error", {"message": friendly_error(exc)}))
        finally:
            events.put(_DONE)

    threading.Thread(target=work, name=f"turn-{conv[:8]}", daemon=True).start()
    yield sse("start", {"conversation_id": conv})
    pending_done: dict[str, Any] | None = None
    while (item := events.get()) is not _DONE:
        if isinstance(item, tuple):
            yield sse(item[0], item[1])
        elif item.kind == "tool_call":
            label = PROGRESS_LABELS.get(item.data["name"])
            if label:
                yield sse("progress", {"label": label, "tool": item.data["name"]})
        elif item.kind == "text":
            yield sse("answer", {"text": item.data["text"]})
        elif item.kind == "done":
            pending_done = item.data  # sent last, after the recipe cards
    if pending_done is not None:
        yield sse("done", pending_done)
