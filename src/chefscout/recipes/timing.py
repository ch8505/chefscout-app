"""Does a dish or recipe fit the time the user asked for?

up to T                           -> ok
up to max(T + 10, 1.2 T) minutes  -> close   (shown, with "a bit over")
more                              -> over    (only as a marked alternative)
no time on the page               -> unknown (shown as "time not given", never as fitting)
"""

from __future__ import annotations

from typing import Literal

TimeFit = Literal["ok", "close", "over", "unknown", "any"]
CLOSE_EXTRA_MINUTES = 10
CLOSE_FACTOR = 1.2


def close_limit(max_minutes: int) -> int:
    return max(max_minutes + CLOSE_EXTRA_MINUTES, round(max_minutes * CLOSE_FACTOR))


def time_fit(minutes: int | None, max_minutes: int | None) -> TimeFit:
    if max_minutes is None:
        return "any"
    if minutes is None:
        return "unknown"
    if minutes <= max_minutes:
        return "ok"
    if minutes <= close_limit(max_minutes):
        return "close"
    return "over"
