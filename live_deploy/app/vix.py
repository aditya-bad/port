"""
live_deploy — India VIX: the 30-day implied volatility of Nifty options,
for Geek mode's volatility views. Never read by a strategy.

* The instrument is looked up by name in Kite's NSE instrument list
  ("INDIA VIX"), never hard-coded, then subscribed like any index, so
  its ticks arrive with the rest.
* History: Kite keeps an index's history indefinitely but doesn't want it
  polled, so it's fetched once into vix_1m (migration 0027) — the last
  year, and back to the first trade — then once after each close for the
  day just ended. A day Kite has nothing for (a holiday) is remembered
  and never asked for again.
* Live: ticks are folded into 1-minute rows as they arrive, and the
  current value is stamped on every fill's market context
  (DeploymentRunner.market_context, under "vix").

VIX is quoted annualised over calendar days, so the move it implies over
one day is VIX / √365 per cent (expected_move); it's built from Nifty
options, so for BankNifty, Sensex and Bankex it is a stand-in only.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .db import queries
from .options import NoKiteSession, OptionsResolver

logger = logging.getLogger("live_deploy.vix")

IST = ZoneInfo("Asia/Kolkata")
EXCHANGE, SYMBOL = "NSE", "INDIA VIX"
HISTORY_DAYS = 365
# A day counts as stored at this many minutes (a full session is 375).
FULL_DAY = 300
MAX_DAYS_PER_REQUEST = 30          # Kite serves at most 60 days of 1-minute history a request
BACKFILL_AT = time(15, 45)         # the day just ended, once the close has settled
SESSION_OPEN, SESSION_CLOSE = time(9, 15), time(15, 30)

# The resolved instrument, for the runner's fill context and the API.
state: dict = {"token": None}


def expected_move_pct(vix: float, days: float = 1.0) -> float:
    """The one-standard-deviation move VIX implies over `days` calendar
    days, in per cent of the index."""
    return vix / math.sqrt(365.0) * math.sqrt(days)


def missing_days(start: date, end: date, counts: dict, empty: set) -> list[date]:
    """Weekdays in [start, end] not yet stored and not known to be empty."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and counts.get(d, 0) < FULL_DAY and d not in empty:
            out.append(d)
        d += timedelta(days=1)
    return out


def chunks(days: list[date]) -> list[list[date]]:
    """Missing days -> runs of at most MAX_DAYS_PER_REQUEST calendar days."""
    out: list[list[date]] = []
    for d in days:
        if out and (d - out[-1][0]).days < MAX_DAYS_PER_REQUEST:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def fold(building: Optional[list], price: float, minute: datetime) -> tuple[Optional[list], Optional[list]]:
    """Fold one tick into the minute being built ([minute, o, h, l, c]).
    Returns (the minute being built, a finished minute or None)."""
    if building is None or building[0] != minute:
        return [minute, price, price, price, price], building
    building[2], building[3], building[4] = max(building[2], price), min(building[3], price), price
    return building, None


class VixService:
    def __init__(self, pool, dispatcher, broadcaster=None):
        self.pool = pool
        self.dispatcher = dispatcher
        self.broadcaster = broadcaster
        self.token: Optional[int] = None
        self.backfilled_through: Optional[date] = None
        self.progress: Optional[dict] = None
        self.last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    # ── The instrument ───────────────────────────────────────────────

    async def resolve(self) -> Optional[int]:
        """Look INDIA VIX up in Kite's NSE instruments and subscribe it."""
        if self.token is not None:
            return self.token
        cache = await OptionsResolver(self.dispatcher)._ensure_instruments(EXCHANGE)
        row = next((r for r in cache["data"] if r.get("tradingsymbol") == SYMBOL), None)
        if row is None:
            self.last_error = f"{SYMBOL} isn't in Kite's {EXCHANGE} instrument list"
            logger.warning(self.last_error)
            return None
        self.token = state["token"] = int(row["instrument_token"])
        try:
            self.dispatcher.add_instruments([{"symbol": SYMBOL, "instrument_token": self.token}])
        except Exception:
            logger.exception("Subscribing %s failed — history still works", SYMBOL)
        logger.info("%s is instrument %s", SYMBOL, self.token)
        return self.token

    def last(self) -> Optional[dict]:
        """The latest tick: {price, at}, or None."""
        if self.token is None:
            return None
        px = (getattr(self.dispatcher, "last_prices", {}) or {}).get(self.token)
        at = (getattr(self.dispatcher, "last_price_times", {}) or {}).get(self.token)
        return None if px is None else {"price": float(px), "at": at.isoformat() if at else None}

    # ── History ──────────────────────────────────────────────────────

    async def backfill(self, now: Optional[datetime] = None) -> dict:
        """Fetch every missing day from a year back (or the first trade, if
        earlier) to the last finished session. One run at a time."""
        now = (now or datetime.now(IST)).astimezone(IST)
        async with self._lock:
            token = await self.resolve()
            if token is None:
                return {"fetched_days": 0, "rows": 0, "error": self.last_error}
            first = await self.pool.fetchval("SELECT MIN(opened_at) FROM positions")
            start = now.date() - timedelta(days=HISTORY_DAYS)
            if first is not None:
                start = min(start, first.astimezone(IST).date())
            end = now.date() if now.time() >= BACKFILL_AT else now.date() - timedelta(days=1)
            counts = await queries.vix_day_counts(self.pool, datetime.combine(start, time(0), IST),
                                                  datetime.combine(end + timedelta(days=1), time(0), IST))
            todo = missing_days(start, end, counts, await queries.vix_no_data_days(self.pool))
            self.progress = {"days": len(todo), "done": 0, "rows": 0}
            rows_total = 0
            for run in chunks(todo):
                a = datetime.combine(run[0], SESSION_OPEN)
                b = datetime.combine(run[-1], SESSION_CLOSE)
                raw = await OptionsResolver(self.dispatcher).get_historical_candles(token, a, b, "minute")
                rows, seen = [], set()
                for c in raw or []:
                    ts = c["date"]
                    ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
                    seen.add(ts.date())
                    rows.append((ts, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])))
                rows_total += await queries.upsert_vix(self.pool, rows, "kite")
                # a day in the window with nothing at all, now finished: a holiday
                await queries.mark_vix_no_data(self.pool, [d for d in run if d not in seen and d < now.date()])
                self.progress["done"] += len(run)
                self.progress["rows"] = rows_total
            self.backfilled_through = end
            self.progress = None
            self.last_error = None
            return {"fetched_days": len(todo), "rows": rows_total, "error": None}

    async def series(self, start: datetime, end: datetime, interval: str = "minute") -> list[dict]:
        from .candles import rollup
        rows = [{"minute": r["minute"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"]}
                for r in await queries.vix_rows(self.pool, start, end)]
        return rollup(rows, interval)

    # ── Live ─────────────────────────────────────────────────────────

    async def record_loop(self) -> None:
        """Fold VIX ticks into 1-minute rows, written as each minute ends."""
        if self.broadcaster is None:
            return
        q = await self.broadcaster.subscribe()
        building: Optional[list] = None
        try:
            while True:
                try:
                    ticks = await asyncio.wait_for(q.get(), timeout=5)
                except asyncio.TimeoutError:
                    ticks = []
                try:
                    now = datetime.now(IST)
                    minute = now.replace(second=0, microsecond=0)
                    if building is not None and building[0] < minute:
                        await queries.upsert_vix(self.pool, [tuple(building)], "ticks")
                        building = None
                    if self.token is None or now.weekday() >= 5 or not (SESSION_OPEN <= now.time() < SESSION_CLOSE):
                        continue
                    for t in ticks if isinstance(ticks, list) else []:
                        if t.get("instrument_token") == self.token and t.get("last_price"):
                            building, done = fold(building, float(t["last_price"]), minute)
                            if done is not None:
                                await queries.upsert_vix(self.pool, [tuple(done)], "ticks")
                except Exception:
                    logger.exception("Recording VIX failed — continuing")
        finally:
            await self.broadcaster.unsubscribe(q)

    async def run_loop(self, every_s: float = 60.0) -> None:
        """Once Kite is logged in: find the instrument, backfill, and again
        after each close."""
        while True:
            try:
                now = datetime.now(IST)
                due = now.date() if now.time() >= BACKFILL_AT else now.date() - timedelta(days=1)
                if getattr(self.dispatcher, "access_token", None) and self.backfilled_through != due:
                    r = await self.backfill(now)
                    if r["rows"]:
                        logger.info("VIX backfill: %d days, %d rows", r["fetched_days"], r["rows"])
            except NoKiteSession:
                pass
            except Exception as e:
                self.last_error = str(e)
                self.progress = None
                logger.exception("VIX backfill failed — retrying in a minute")
            await asyncio.sleep(every_s)
