"""Streams one chat turn to the browser as Server-Sent Events (SSE).

Events: progress (a friendly Hebrew label per tool), answer, ideas (a new list) or recipes (cards),
plan (state only), done, error.
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
from chefscout.server.idempotency import RequestLog
from chefscout.server.schemas import ChatAction, PlanView, RecipeCard
from chefscout.server.transcript import TranscriptStore

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
    "record_feedback": "שומר את מה שאמרת…",
    "propose_ideas": "חושב על רעיונות שמתאימים לך…",
    "replace_dish": "מחליף את המנה…",
    "select_dishes": "מתכונן לחפש מתכונים…",
    "more_options": "מחפש עוד אפשרויות…",
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


# Tools that bring a NEW ideas list (shown as a list in the chat).
IDEAS_TOOLS = {"propose_ideas", "replace_dish"}
# Tools that change the state of the plan (chosen, replaced, has a recipe): the page updates its lists.
PLAN_TOOLS = {
    *IDEAS_TOOLS,
    "select_dishes",
    "record_feedback",
    "read_recipes",
    "show_recipes",
    "more_options",
}


def action_note(action: ChatAction | None) -> str:
    """A button press, as a short note for the model after the user's visible text."""
    if action is None:
        return ""
    target = action.meal_id or action.dish_id or "?"
    if action.type == "get_recipes" and action.meal_id:
        return f"\n[button] the user chose meal {target}: select_dishes(meal_id={target}), then find recipes."
    if action.type == "get_recipes":
        return f"\n[button] the user chose dish {target}: select_dishes(dish_ids=[{target}]), then a recipe."
    if action.type == "replace":
        return f"\n[button] swap dish {target} for another dish of the same kind (replace_dish reason=user)."
    if action.type == "reject":
        return f"\n[button] the user does not like dish {target}: replace it (replace_dish reason=rejected)."
    if action.type == "variant":
        return (
            f"\n[button] the same dish {target}, made the way the user wrote above: "
            f"more_options(dish_id={target}, variant=<their words>)."
        )
    return f"\n[button] more recipes for dish {target} only (more_options)."


def plan_view(agent: Agent, user_id: str, conv: str) -> dict[str, Any] | None:
    plan = agent.services["plans"].load(user_id, conv)
    if plan is None:
        return None
    recipes = agent.services["recipes"]
    return PlanView.build(plan, lambda rid: recipes.get(user_id, conv, rid)).model_dump()


def turn_shape(tools_used: list[str], intent: str | None) -> str:
    """What one turn shows, and never two of them: "cards", "ideas" or "text".

    Cards win: once recipes were shown, a list of ideas in the same turn is only noise. A specific
    dish (intent=recipe) never shows an ideas list.
    """
    used = set(tools_used)
    if "show_recipes" in used:
        return "cards"
    if IDEAS_TOOLS & used and intent not in (None, "recipe"):
        return "ideas"
    return "text"


def stream_turn(
    *,
    get_agent: Callable[[], Agent],
    user_id: str,
    message: str,
    conversation_id: str | None,
    action: ChatAction | None = None,
    request_id: str | None = None,
    requests: RequestLog | None = None,
    transcript: TranscriptStore | None = None,
) -> Iterator[str]:
    """One turn. Events, in order: start, progress*, answer, then ONE of ideas / recipes (or neither),
    plan (the plan's state, so earlier lists can mark what changed), done."""
    conv = conversation_id or new_conversation_id()
    events: queue.Queue[Any] = queue.Queue()

    def emit(event: AgentEvent) -> None:
        events.put(event)

    def record(*entries: dict[str, Any]) -> None:
        if transcript is not None:
            try:
                transcript.add(user_id, conv, *entries)
            except Exception:  # the chat must not fail because of its record
                log.exception("transcript write failed")

    def work() -> None:
        try:
            agent = get_agent()
            with locked(agent.store.path(user_id, conv)):  # one turn at a time per conversation
                if requests is not None and not requests.first_time(user_id, conv, request_id):
                    events.put(("done", {"conversation_id": conv, "duplicate": True}))
                    return
                record({"role": "user", "text": message})
                text = message + action_note(action)
                result = agent.send(user_id=user_id, text=text, conversation_id=conv, emit=emit)
            plan = agent.services["plans"].load(user_id, conv)
            shape = turn_shape(result.tools_used, plan.intent if plan else None)
            entry: dict[str, Any] = {"role": "agent", "text": result.text}
            if shape == "ideas":
                view = plan_view(agent, user_id, conv)
                events.put(("ideas", view))
                entry["ideas"] = view
            elif shape == "cards":
                shown = agent.services["recipes"].last_shown(user_id, conv)
                pantry = agent.services["pantry"].items(user_id, conv)
                cards = [RecipeCard.from_checked(s, plan, pantry).model_dump() for s in shown]
                events.put(("recipes", cards))
                entry["recipes"] = [s.id for s in shown]
            if plan is not None and PLAN_TOOLS & set(result.tools_used):
                events.put(("plan", plan_view(agent, user_id, conv)))
            record(entry)
        except (ChefScoutError, Exception) as exc:
            log.exception("chat turn failed")
            events.put(("error", {"message": friendly_error(exc)}))
            record({"role": "error", "text": friendly_error(exc)})
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
