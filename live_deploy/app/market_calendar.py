"""
live_deploy — the market's sessions: a normal day, and the special ones.

A normal market day (Monday to Friday) trades 09:15 to 15:30 IST. A
special session trades at its own times, and some allow only managing
what's already held:

  MUHURAT TRADING (Diwali): a short evening session, on a Sunday this
  year. 8 Nov 2026, per the NSE and BSE circulars of 19 Oct: pre-open
  14:05-14:20, trading 14:20-15:30 (stocks in the closing auction stop
  at 15:15 and go to the auction). The owner's rule for it: no new
  positions and no intraday trading; only the strangles adjust and
  exit what they hold (manage_only).

Every "is the market open, and from when" decision that matters for
trading asks this module: the runner's tick filter (a tick outside the
session, the pre-open included, is never a trading signal), which
strategies may run on a manage-only day (strategy class attribute
MANAGE_ONLY_SESSION = True), the strangle's entries and its first
minutes, the health monitor's market hours, and the price marks.

A new special session: add it to SPECIAL. Exchange holidays (no session
at all) are marked in the app (Settings › notifications), not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

NORMAL_OPEN, NORMAL_CLOSE = time(9, 15), time(15, 30)


@dataclass(frozen=True)
class Session:
    open: time
    close: time
    manage_only: bool = False      # no new positions, no intraday: only managing what's held
    name: str = ""


NORMAL = Session(NORMAL_OPEN, NORMAL_CLOSE)

SPECIAL: dict[date, Session] = {
    date(2026, 11, 8): Session(time(14, 20), time(15, 30), manage_only=True, name="Muhurat trading"),
}


def session(day: date) -> Optional[Session]:
    """The day's session: a special one, a normal weekday, or None (a weekend)."""
    if day in SPECIAL:
        return SPECIAL[day]
    return NORMAL if day.weekday() < 5 else None


def special(day: date) -> Optional[Session]:
    return SPECIAL.get(day)


def manage_only(day: date) -> bool:
    s = SPECIAL.get(day)
    return bool(s and s.manage_only)


def shifted(day: date, t: time) -> time:
    """A normal day's time of day moved to `day`'s session, by its distance
    from the open (09:20 on a normal day is open + 5 min; on Muhurat,
    14:25). A time at or after the normal close keeps its distance from
    the close instead (15:40 stays 15:40 when both close at 15:30)."""
    s = SPECIAL.get(day)
    if s is None:
        return t
    base = datetime.combine(day, t)
    if t >= NORMAL_CLOSE:
        return (base - datetime.combine(day, NORMAL_CLOSE) + datetime.combine(day, s.close)).time()
    return (base - datetime.combine(day, NORMAL_OPEN) + datetime.combine(day, s.open)).time()


def is_open(at: datetime, until: Optional[time] = None) -> bool:
    """`at` (naive IST, or aware) within its day's session: from the open
    to the close, or to `until` (a normal day's time, shifted) when given."""
    s = session(at.date())
    if s is None:
        return False
    end = shifted(at.date(), until) if until is not None else s.close
    return s.open <= at.time() <= end


def note(day: date) -> str:
    """What a strategy that doesn't run on a manage-only day says, once."""
    s = SPECIAL.get(day)
    return (f"{s.name}: only the strangles adjust and exit what they hold today; no new positions, no intraday trading"
            if s else "")


def minutes_from_open(day: date, minutes: int) -> time:
    s = session(day) or NORMAL
    return (datetime.combine(day, s.open) + timedelta(minutes=minutes)).time()
