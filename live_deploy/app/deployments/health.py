"""
live_deploy — what keeps a running deployment safe from bad data and its
own failures, and how it says so (DeploymentRunner composes these).

TickFilter   every tick checked before the strategy sees it: a price that
             isn't a positive number, a tick with no time, one older than
             the last, an exact repeat — dropped; a jump no index makes
             (more than 6% in a session, 15% across a night) held back
             until the next tick confirms it or it's discarded.
Issues       what the deployment reports about itself (migration 0034),
             one open issue per key, written at most once a minute per
             key, never raising into trading. The health monitor alerts
             on them; the Repair page lists them.
Breaker      a strategy that keeps failing isn't called on every tick:
             after 5 failures in a row it's called again after 1, 2, 4, 8
             and then every 15 minutes, until a call succeeds. The
             runner's own safety net (the intraday square-off) runs on.
BlockedWatch a rule a strategy reports as "blocked" (a trade was due and
             something stopped it) for 10 minutes running becomes an
             issue; it clears when the rule next gets through.
"""

from __future__ import annotations

import logging
import math
import traceback
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("live_deploy.health")


# ── ticks ───────────────────────────────────────────────────────────────

class TickFilter:
    SPIKE = 0.06      # within a session, from the last accepted price
    GAP = 0.15        # across sessions (an overnight gap)
    CONFIRM = 0.015   # a held tick is real if the next lands within this of it

    def __init__(self):
        self.last: dict[int, tuple[datetime, float]] = {}
        self.held: dict[int, dict] = {}
        self.dropped: Counter = Counter()

    @staticmethod
    def price_of(t: dict) -> Optional[float]:
        p = t.get("last_price")
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or p <= 0:
            return None
        return float(p)

    def check(self, t: dict) -> tuple[Optional[dict], Optional[str]]:
        """(the tick to process — None to process nothing now —, why the
        one given was dropped or held, if it was)."""
        p, ts, tok = self.price_of(t), t.get("exchange_timestamp"), t.get("instrument_token")
        if p is None:
            return self._drop("bad_price")
        if not isinstance(ts, datetime):
            return self._drop("no_time")
        last = self.last.get(tok)
        if last is not None:
            if ts < last[0]:
                return self._drop("out_of_order")
            if ts == last[0] and p == last[1]:
                return self._drop("duplicate")
            limit = self.GAP if ts.date() != last[0].date() else self.SPIKE
            if abs(p / last[1] - 1) > limit:
                held = self.held.get(tok)
                if held is not None and abs(p / held["p"] - 1) <= self.CONFIRM:
                    self.held.pop(tok, None)       # two in a row agree: the market really moved
                    self.last[tok] = (ts, p)
                    return t, None
                self.held[tok] = {"p": p, "ts": ts}
                self.dropped["held"] += 1
                return None, "held"
        if self.held.pop(tok, None) is not None:
            self.dropped["spike"] += 1             # the held one never got confirmed
        self.last[tok] = (ts, p)
        return t, None

    def _drop(self, why: str):
        self.dropped[why] += 1
        return None, why


DROP_WORDS = {"bad_price": "with no usable price", "no_time": "with no time", "out_of_order": "older than the last",
              "duplicate": "repeated exactly", "spike": "a jump no index makes", "held": "held for confirmation"}


# ── issues ──────────────────────────────────────────────────────────────

class Issues:
    WRITE_EVERY = timedelta(seconds=60)

    def __init__(self, pool, deployment_id, record_event: Callable[..., Awaitable[None]], name: str = ""):
        self.pool = pool
        self.deployment_id = deployment_id
        self.record_event = record_event
        self.name = name
        self.open: dict[str, datetime] = {}      # key -> when last written
        self.pending: Counter = Counter()        # sightings not yet written
        self.loaded = False
        self.enabled = True

    async def _load(self):
        if self.loaded:
            return
        from ..db import queries
        try:
            self.open = {k: datetime.min.replace(tzinfo=timezone.utc) for k in await queries.open_strategy_issue_keys(self.pool, self.deployment_id)}
            self.loaded = True
        except Exception:
            logger.exception("%s: couldn't read open issues", self.name)

    async def report(self, kind: str, title: str, detail: Optional[str] = None, *, severity: str = "warning",
                     source: str = "runner", data: Optional[dict] = None, day: Optional[date] = None,
                     key: Optional[str] = None, ttl: Optional[timedelta] = None) -> None:
        if not self.enabled:
            return
        from ..db import queries
        key = key or kind
        now = datetime.now(timezone.utc)
        try:
            await self._load()
            last = self.open.get(key)
            if last is not None and now - last < self.WRITE_EVERY:
                self.pending[key] += 1
                return
            n = 1 + self.pending.pop(key, 0)
            new = await queries.upsert_strategy_issue(
                self.pool, self.deployment_id, key, kind, severity, source, title, detail, data, day,
                now + ttl if ttl else None, count=n)
            self.open[key] = now
            if new:
                logger.warning("%s: %s — %s", self.name, title, detail or "")
                await self.record_event("strategy_issue", message=title,
                                        metadata={"key": key, "kind": kind, "severity": severity, "detail": detail,
                                                  "day": day.isoformat() if day else None})
        except Exception:   # reporting never gets in the way of trading
            logger.exception("%s: couldn't record issue %s", self.name, key)

    async def resolve(self, key: str) -> None:
        if not self.enabled:
            return
        from ..db import queries
        try:
            await self._load()
            if key not in self.open:
                return
            self.open.pop(key, None)
            self.pending.pop(key, None)
            if await queries.resolve_strategy_issue(self.pool, self.deployment_id, key):
                await self.record_event("strategy_issue_resolved", message=f"Cleared: {key}", metadata={"key": key})
        except Exception:
            logger.exception("%s: couldn't resolve issue %s", self.name, key)

    def is_open(self, key: str) -> bool:
        return key in self.open


# ── a failing strategy ──────────────────────────────────────────────────

class Breaker:
    TRIP_AFTER = 5
    BACKOFF = [1, 2, 4, 8, 15]   # minutes

    def __init__(self):
        self.fails = 0           # in a row
        self.total = 0
        self.trips = 0
        self.skip_until: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.first_at: Optional[datetime] = None
        self.logged: dict[str, datetime] = {}

    def allow(self, now: datetime) -> bool:
        return self.skip_until is None or now >= self.skip_until

    def failed(self, now: datetime, err: str) -> Optional[datetime]:
        """Note a failure; when the strategy should next be called, if
        it's to be skipped until then."""
        self.fails += 1
        self.total += 1
        self.last_error = err
        self.first_at = self.first_at or now
        if self.fails >= self.TRIP_AFTER:
            wait = self.BACKOFF[min(self.trips, len(self.BACKOFF) - 1)]
            self.trips += 1
            self.skip_until = now + timedelta(minutes=wait)
            return self.skip_until
        return None

    def succeeded(self) -> bool:
        """True if this ends a run of failures."""
        was = self.fails > 0 or self.trips > 0
        self.fails = self.trips = 0
        self.skip_until = None
        self.first_at = None
        return was

    def should_log(self, sig: str, now: datetime, every: timedelta = timedelta(minutes=10)) -> bool:
        last = self.logged.get(sig)
        if last is not None and now - last < every:
            return False
        if len(self.logged) > 200:
            self.logged.clear()
        self.logged[sig] = now
        return True


def describe(e: BaseException) -> tuple[str, str]:
    """(a one-line signature of an exception, where in the app it was
    raised: the innermost frame under app/)."""
    sig = f"{type(e).__name__}: {str(e)[:160]}" if str(e) else type(e).__name__
    where = ""
    for fr in reversed(traceback.extract_tb(e.__traceback__)):
        if "/app/" in fr.filename.replace("\\", "/"):
            where = f"{fr.filename.split('/app/')[-1]}:{fr.lineno} in {fr.name}"
            break
    return sig, where


# ── a rule that stays blocked ───────────────────────────────────────────

class BlockedWatch:
    AFTER = timedelta(minutes=10)

    def __init__(self):
        self.since: dict[str, tuple[datetime, str]] = {}

    def blocked(self, rule: str, ts: datetime, summary: str) -> Optional[tuple[datetime, str]]:
        """Note a blocked check; (since when, why) once it has been
        blocked for AFTER without getting through."""
        first = self.since.setdefault(rule, (ts, summary))
        self.since[rule] = (first[0], summary)
        return self.since[rule] if ts - first[0] >= self.AFTER else None

    def through(self, rule: str) -> bool:
        """The rule got through; True if it had been blocked."""
        return self.since.pop(rule, None) is not None
