"""What was suggested to the user, across conversations: data/users/<user_id>/history.json

Not chosen is not disliked. Only an explicit rejection blocks:

    suggested  an idea was proposed            -> soft variety hint only, never blocked
    shown      a recipe card was shown         -> soft hint (not shown twice in one conversation)
    replaced   the user swapped a dish         -> soft hint
    saved      the user saved it               -> never blocked, listed on request
    rejected   "I don't like this" / button    -> blocked for a while (not "never": that is the profile)

Cooked recipes stay in the profile (ProfileStore.cooked); they are not duplicated here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from chefscout.core.errors import ToolError
from chefscout.core.history import safe_id
from chefscout.memory.storage import locked, normalize, read_json, write_json

Status = Literal["suggested", "shown", "replaced", "saved", "rejected"]
Kind = Literal["dish", "recipe"]
SOFT = ("suggested", "shown", "replaced")
MAX_ENTRIES = 500  # oldest soft entries are dropped first; saved / rejected are kept


def _now() -> datetime:
    return datetime.now(UTC)


class HistoryEntry(BaseModel):
    key: str  # normalized dish name, or the recipe url
    kind: Kind
    title: str
    status: Status
    url: str | None = None
    ref: str = ""  # where it came from, e.g. "<conversation>:v3". (key, status, ref) is unique
    at: datetime = Field(default_factory=_now)


def entry_key(kind: Kind, title: str, url: str | None) -> str:
    return url.strip() if kind == "recipe" and url else normalize(title)


class HistoryStore:
    def __init__(self, data_dir: Path, clock: Callable[[], datetime] = _now):
        self.data_dir = Path(data_dir)
        self.clock = clock

    def path(self, user_id: str) -> Path:
        return self.data_dir / "users" / safe_id(user_id, "user id") / "history.json"

    def entries(self, user_id: str) -> list[HistoryEntry]:
        return [HistoryEntry.model_validate(e) for e in read_json(self.path(user_id), [])]

    def record(
        self,
        user_id: str,
        kind: Kind,
        title: str,
        status: Status,
        url: str | None = None,
        ref: str = "",
    ) -> HistoryEntry:
        """Add one entry. The same (key, status, ref) again only refreshes it (no duplicates)."""
        title = " ".join((title or "").split())
        if not title:
            raise ToolError("title must not be empty")
        key = entry_key(kind, title, url)
        path = self.path(user_id)
        with locked(path):
            items = self.entries(user_id)
            items = [e for e in items if not (e.key == key and e.status == status and e.ref == ref)]
            entry = HistoryEntry(
                key=key, kind=kind, title=title, status=status, url=url, ref=ref, at=self.clock()
            )
            items.append(entry)
            if len(items) > MAX_ENTRIES:
                soft = [e for e in items if e.status in SOFT]
                drop = {id(e) for e in soft[: len(items) - MAX_ENTRIES]}
                items = [e for e in items if id(e) not in drop]
            write_json(path, [e.model_dump(mode="json") for e in items])
        return entry

    # ---- queries ---------------------------------------------------------------------

    def _latest_by_key(self, user_id: str, statuses: tuple[str, ...]) -> dict[str, HistoryEntry]:
        out: dict[str, HistoryEntry] = {}
        for e in self.entries(user_id):
            if e.status in statuses:
                out[e.key] = e  # entries are in time order: the last one wins
        return out

    def rejected(self, user_id: str, days: int) -> list[HistoryEntry]:
        """Explicitly rejected within `days`, unless saved again after that."""
        since = self.clock() - timedelta(days=days)
        latest = self._latest_by_key(user_id, ("rejected", "saved"))
        return [e for e in latest.values() if e.status == "rejected" and e.at >= since]

    def rejected_names(self, user_id: str, days: int) -> list[str]:
        return [e.title for e in self.rejected(user_id, days) if e.kind == "dish"]

    def rejected_urls(self, user_id: str, days: int) -> set[str]:
        return {e.url for e in self.rejected(user_id, days) if e.url}

    def recently_suggested(self, user_id: str, limit: int = 15) -> list[str]:
        """Newest first, unique titles: a variety hint, never a block."""
        blocked = {e.key for e in self.rejected(user_id, days=100000)}
        seen: list[str] = []
        for e in reversed(self.entries(user_id)):
            if e.status in SOFT and e.key not in blocked and e.title not in seen:
                seen.append(e.title)
            if len(seen) >= limit:
                break
        return seen

    def saved(self, user_id: str) -> list[HistoryEntry]:
        """Saved items, newest first."""
        latest = self._latest_by_key(user_id, ("saved", "rejected"))
        return sorted((e for e in latest.values() if e.status == "saved"), key=lambda e: e.at, reverse=True)
