"""
live_deploy — price history for charts. Option contracts are stored;
indices and stocks are not.

Why: Kite's historical API keeps serving an index's or a stock's
history indefinitely, but drops an option contract's history the day it
expires. So only OPTIONS (CE/PE) are kept, in candles_1m (migration
0021): one 1-minute OHLC row per contract per minute, nothing else. A
contract held by an intraday and a positional deployment at once is
stored once; 5/15/60-minute candles are rolled up from the 1-minute rows
when read (rollup()), anchored at 09:15 like Kite's. Rows come from:
  - live ticks of every held contract, consolidated per minute
    (DeploymentManager.candles_loop);
  - Kite's historical API, filling whatever minutes are missing — on a
    chart request (series()) while the contract is still listed, and
    once after every close for every contract held that day
    (backfill_day()), so its history survives expiry;
  - backups imported with custom_scripts/import_candles.py.
Everything else (indices, stocks) is fetched from Kite when a chart asks
(at the chart's own interval), cached in memory only, never stored.

Kite calls: one history request in flight at a time app-wide, through
the resolver's process-wide throttle (≈3 requests a second, Kite's
limit, with 429 back-off); a stored day is never re-requested, an
expired contract is never asked for, and an instrument Kite had nothing
for isn't asked again for an hour.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import re
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .db import queries
from .options import NoKiteSession, OptionsResolver

logger = logging.getLogger("live_deploy.candles")

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN, SESSION_CLOSE = time(9, 15), time(15, 30)
INTERVAL_MINUTES = {"minute": 1, "3minute": 3, "5minute": 5, "10minute": 10, "15minute": 15,
                    "30minute": 30, "60minute": 60}
# The daily backfill: once the close (15:30) has settled.
BACKFILL_AT = time(15, 45)
# A day counts as covered at this share of its session minutes (a quiet
# contract may genuinely not trade every minute).
COVERED = 0.9
# Kite serves at most 60 days of 1-minute history per request.
MAX_DAYS_PER_REQUEST = 30
NO_DATA_RETRY_S = 3600


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def session_minutes(start: datetime, end: datetime) -> int:
    """Trading minutes (weekdays, 09:15-15:29 IST) in [start, end)."""
    n, day = 0, start.astimezone(IST).date()
    while day <= end.astimezone(IST).date():
        if day.weekday() < 5:
            o = max(start, datetime.combine(day, SESSION_OPEN, IST))
            c = min(end, datetime.combine(day, SESSION_CLOSE, IST))
            if c > o:
                n += int((c - o).total_seconds() // 60)
        day += timedelta(days=1)
    return n


def rollup(rows, interval: str) -> list[dict]:
    """1-minute rows -> `interval` candles anchored at 09:15 like Kite's
    ({t: bucket start epoch ms, o, h, l, c}); "day" gives one per date."""
    out: dict[int, dict] = {}
    step = INTERVAL_MINUTES.get(interval)
    for r in rows:
        m = r["minute"].astimezone(IST)
        if step is None:   # day
            start = datetime.combine(m.date(), time(0), IST)
        else:
            open_ = datetime.combine(m.date(), SESSION_OPEN, IST)
            k = int(((m - open_).total_seconds() // 60) // step * step)
            start = open_ + timedelta(minutes=k)
        t = _ms(start)
        c = out.get(t)
        if c is None:
            out[t] = {"t": t, "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"]}
        else:
            c["h"], c["l"], c["c"] = max(c["h"], r["h"]), min(c["l"], r["l"]), r["c"]
    return [out[t] for t in sorted(out)]


# NIFTY26O2855000CE (weekly: yy, month code 1-9/O/N/D, dd) or
# NIFTY26NOV22400PE (monthly: yy, MON — expires late in that month).
_WEEKLY = re.compile(r"^([A-Z&]+?)(\d{2})([1-9OND])(\d{2})(\d+(?:\.\d+)?)(CE|PE)$")
_MONTHLY = re.compile(r"^([A-Z&]+?)(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d+(?:\.\d+)?)?(CE|PE|FUT)$")
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def parse_contract(symbol: str) -> Optional[dict]:
    """What a derivatives tradingsymbol says about itself. Monthly
    symbols carry no day, so their expiry is the month's last day — an
    upper bound, which is all the "is it expired" check needs."""
    m = _WEEKLY.match(symbol or "")
    if m:
        und, yy, mo, dd, strike, typ = m.groups()
        month = {"O": 10, "N": 11, "D": 12}.get(mo) or int(mo)
        try:
            exp = date(2000 + int(yy), month, int(dd))
        except ValueError:
            return None
        return {"underlying": und, "expiry": exp, "strike": float(strike), "instrument_type": typ}
    m = _MONTHLY.match(symbol or "")
    if m:
        und, yy, mon, strike, typ = m.groups()
        y, mo = 2000 + int(yy), _MONTHS.index(mon) + 1
        return {"underlying": und, "expiry": date(y, mo, calendar.monthrange(y, mo)[1]),
                "strike": float(strike) if strike else None, "instrument_type": typ}
    return None


class CandleService:
    def __init__(self, pool, dispatcher):
        self.pool = pool
        self.dispatcher = dispatcher
        self._token_locks: dict[int, asyncio.Lock] = {}
        self._kite_lock = asyncio.Lock()          # one history request in flight, app-wide
        self._no_data: dict[tuple[int, date], float] = {}
        self._kite_cache: dict[tuple, tuple[datetime, list]] = {}   # indices/stocks, in memory only
        self.kite_requests = 0
        # The daily backfill as it runs (backfill_status / GET /health):
        # None when idle, else {day, total, done, fetched, rows, current, started_at}
        self.progress: Optional[dict] = None
        self._backfill_lock = asyncio.Lock()
        self._backfill_queue: list[dict] = []   # {"day"} tickets of runs waiting for the lock, oldest first                    # for tests and the status endpoint

    # ── Reading ──────────────────────────────────────────────────────

    async def series(self, token: int, start: datetime, end: datetime, interval: str = "minute",
                     *, fetch: bool = True, symbol: Optional[str] = None) -> tuple[list[dict], Optional[str], Optional[str]]:
        """(candles, source, error) for [start, end). Missing minutes are
        filled from Kite first when `fetch` and the contract is still
        listed. source: "stored" (covered), "stored_partial" (some days
        incomplete) or None (nothing); error says why data is missing."""
        if not await self.is_option(token, symbol):
            return await self._from_kite(token, start, end, interval, fetch)
        err = None
        if fetch:
            try:
                await self.ensure(token, start, end, symbol=symbol)
            except NoKiteSession:
                err = "Kite isn't logged in, so missing history can't be fetched. Log in and reload."
            except Exception as e:
                logger.exception("Candles: filling token %s failed", token)
                err = f"Couldn't fetch the missing history ({e})."
        rows = await queries.list_candles(self.pool, token, start, end)
        if not rows:
            return [], None, err or "No price history stored for this instrument and window."
        missing = await self._missing_days(token, start, end)
        return rollup(rows, interval), ("stored_partial" if missing else "stored"), None

    async def is_option(self, token: int, symbol: Optional[str] = None) -> bool:
        """Only option contracts (CE/PE) are stored. Known from the symbol
        given, else the contracts table, else any position that held it."""
        if symbol is None:
            c = await queries.get_contract(self.pool, token)
            if c is not None and c["instrument_type"]:
                return c["instrument_type"] in ("CE", "PE")
            symbol = await queries.position_symbol(self.pool, token)
        p = parse_contract(symbol or "")
        return bool(p and p["instrument_type"] in ("CE", "PE"))

    async def _from_kite(self, token, start, end, interval, fetch) -> tuple[list[dict], Optional[str], Optional[str]]:
        """An index or a stock: Kite's own history at the chart's interval,
        cached in memory (finished windows kept, live ones for a minute),
        never stored."""
        key = (token, start.isoformat(), end.isoformat(), interval)
        now = datetime.now(timezone.utc)
        hit = self._kite_cache.get(key)
        if hit is not None and (end < now - timedelta(minutes=10) or now - hit[0] < timedelta(seconds=60)):
            return hit[1], "kite", None
        if not fetch:
            return [], None, "Kite isn't logged in, so this history can't be fetched. Log in and reload."
        try:
            out = []
            for a, b in _windows(start, end, interval):
                async with self._kite_lock:
                    self.kite_requests += 1
                    raw = await OptionsResolver(self.dispatcher).get_historical_candles(
                        token, a.astimezone(IST).replace(tzinfo=None), b.astimezone(IST).replace(tzinfo=None), interval)
                for c in raw or []:
                    ts = c["date"]
                    ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts
                    out.append({"t": _ms(ts), "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"]})
        except NoKiteSession:
            return [], None, "Kite isn't logged in, so this history can't be fetched. Log in and reload."
        except Exception as e:
            logger.exception("Candles: Kite history for %s failed", token)
            return [], None, f"Couldn't fetch the history ({e})."
        if not out:
            return [], None, "Kite returned no history for this window."
        if len(self._kite_cache) >= 500:
            self._kite_cache.pop(next(iter(self._kite_cache)))
        self._kite_cache[key] = (now, out)
        return out, "kite", None

    # ── Filling from Kite ────────────────────────────────────────────

    async def _missing_days(self, token: int, start: datetime, end: datetime) -> list[date]:
        now = datetime.now(timezone.utc)
        end = min(end, now - timedelta(minutes=1))
        if end <= start:
            return []
        have = await queries.candle_minutes_per_day(self.pool, token, start, end)
        out, day = [], start.astimezone(IST).date()
        while day <= end.astimezone(IST).date():
            if day.weekday() < 5:
                expected = session_minutes(max(start, datetime.combine(day, time(0), IST)),
                                           min(end, datetime.combine(day + timedelta(days=1), time(0), IST)))
                if expected and have.get(day, 0) < COVERED * expected:
                    out.append(day)
            day += timedelta(days=1)
        return out

    async def _expiry(self, token: int, symbol: Optional[str]) -> Optional[date]:
        c = await queries.get_contract(self.pool, token)
        if c is not None and c["expiry"] is not None:
            return c["expiry"]
        p = parse_contract(symbol) if symbol else None
        return p["expiry"] if p else None

    async def ensure(self, token: int, start: datetime, end: datetime, *, symbol: Optional[str] = None) -> int:
        """Make [start, end) covered, fetching only the days that aren't
        from Kite. Returns the number of minutes stored. Concurrent calls
        for one instrument share the work (the second finds it done)."""
        if not await self.is_option(token, symbol):
            return 0               # only option contracts are ever stored
        today = datetime.now(IST).date()
        expiry = await self._expiry(token, symbol)
        lock = self._token_locks.setdefault(token, asyncio.Lock())
        async with lock:
            days = [d for d in await self._missing_days(token, start, end) if not self._known_empty(token, d)]
            if expiry is not None and expiry < today:
                days = []          # Kite has nothing for an expired contract
            stored = 0
            for chunk in _chunks(days):
                f = max(start, datetime.combine(chunk[0], SESSION_OPEN, IST))
                t = min(end, datetime.combine(chunk[-1], SESSION_CLOSE, IST))
                async with self._kite_lock:
                    self.kite_requests += 1
                    raw = await OptionsResolver(self.dispatcher).get_historical_candles(
                        token, f.astimezone(IST).replace(tzinfo=None), t.astimezone(IST).replace(tzinfo=None), "minute")
                rows = []
                for c in raw or []:
                    ts = c["date"]
                    ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts
                    if ts.astimezone(IST).time() < SESSION_OPEN or ts.astimezone(IST).time() >= SESSION_CLOSE:
                        continue
                    rows.append((token, ts, c["open"], c["high"], c["low"], c["close"], c.get("volume")))
                stored += await queries.upsert_candles(self.pool, rows, "kite")
                got = {r[1].astimezone(IST).date() for r in rows}
                for d in chunk:
                    if d not in got:
                        self._no_data[(token, d)] = _time.monotonic() + (60 if d == today else NO_DATA_RETRY_S)
            return stored

    def _known_empty(self, token: int, day: date) -> bool:
        until = self._no_data.get((token, day))
        if until is None:
            return False
        if _time.monotonic() > until:
            del self._no_data[(token, day)]
            return False
        return True

    # ── After the close ──────────────────────────────────────────────

    async def backfill_day(self, day: date, _ticket: Optional[dict] = None) -> dict:
        """Every option contract held on `day` — each once, whichever
        deployments held it: fill the day's missing minutes from Kite
        while it still has them. One run at a time (a second call waits
        for the first, then finds nothing left to fetch). Progress is in
        self.progress while it runs; the result is saved as the last run.
        Returns {"day", "instruments", "fetched", "rows", "seconds", "error"}."""
        ticket = _ticket or {"day": day}
        if _ticket is None:
            self._backfill_queue.append(ticket)
        try:
            async with self._backfill_lock:
                self._drop(ticket)
                return await self._backfill(day)
        finally:
            self._drop(ticket)   # cancelled while waiting

    def _drop(self, ticket: dict) -> None:
        self._backfill_queue[:] = [t for t in self._backfill_queue if t is not ticket]

    async def _backfill(self, day: date) -> dict:
        start = datetime.combine(day, SESSION_OPEN, IST)
        end = datetime.combine(day, SESSION_CLOSE, IST)
        started = datetime.now(timezone.utc)
        self.progress = {"day": day.isoformat(), "total": 0, "done": 0, "fetched": 0, "rows": 0,
                         "current": None, "started_at": started.isoformat()}
        fetched, rows, error = [], 0, None
        try:
            held = [r for r in await queries.instruments_held_between(self.pool, start, end)
                    if (parse_contract(r["symbol"] or "") or {}).get("instrument_type") in ("CE", "PE")]
            self.progress["total"] = len(held)
            await self.remember_contracts([(int(r["instrument_token"]), r["symbol"]) for r in held])
            for r in sorted(held, key=lambda r: int(r["instrument_token"])):
                token, symbol = int(r["instrument_token"]), r["symbol"]
                self.progress["current"] = symbol
                before = self.kite_requests
                try:
                    n = await self.ensure(token, start, end, symbol=symbol)
                except NoKiteSession:
                    error = "Kite isn't logged in"
                    logger.warning("Candle backfill for %s stopped: Kite isn't logged in", day)
                    break
                except Exception as e:
                    logger.exception("Candle backfill: %s (%s) failed — continuing", symbol, token)
                    error = f"{symbol}: {e}"
                    n = 0
                if self.kite_requests > before:
                    fetched.append(token)
                rows += n
                self.progress.update(done=self.progress["done"] + 1, fetched=len(fetched), rows=rows)
            total = len(held)
        except Exception as e:
            logger.exception("Candle backfill %s failed", day)
            error, total = str(e), self.progress["total"]
        finished = datetime.now(timezone.utc)
        result = {"day": day.isoformat(), "instruments": total, "fetched": fetched, "rows": rows,
                  "seconds": round((finished - started).total_seconds(), 1), "error": error,
                  "finished_at": finished.isoformat(), "complete": error is None}
        try:   # saved before the run stops showing as running, so a reader never sees neither
            await queries.set_app_scratch(self.pool, "candle_backfill_last", {**result, "fetched": len(fetched)})
        except Exception:
            logger.exception("Saving the candle backfill result failed")
        # A run still waiting for the lock (a manual one queued behind the
        # scheduled one) keeps showing as running: clearing it here made the
        # status say "nothing running" until that run began.
        self.progress = self._waiting(self._backfill_queue[0]["day"]) if self._backfill_queue else None
        logger.info("Candle backfill %s: %d option contract(s), %d fetched from Kite, %d minute(s) stored%s",
                    day, total, len(fetched), rows, f" — {error}" if error else "")
        return result

    @staticmethod
    def _waiting(day: date) -> dict:
        return {"day": day.isoformat(), "total": 0, "done": 0, "fetched": 0, "rows": 0,
                "current": None, "started_at": datetime.now(timezone.utc).isoformat()}

    def start_backfill(self, day: date) -> asyncio.Task:
        """Run backfill_day in the background, marked as running from this
        moment (so a status read right after already sees it)."""
        ticket = {"day": day}
        self._backfill_queue.append(ticket)   # now, not when the task first runs
        if self.progress is None:
            self.progress = self._waiting(day)
        return asyncio.create_task(self.backfill_day(day, ticket), name="candle-backfill-now")

    async def backfill_status(self, at: Optional[datetime] = None) -> dict:
        """For the UI: the run in progress (if any), the last run, the next
        scheduled one, and what the store holds."""
        now = (at or datetime.now(IST)).astimezone(IST)
        # Read "running" before anything is awaited: a run saves its result
        # before it stops showing as running, so if none is running now, the
        # result read next is its own. Read last, a run finishing during the
        # reads below showed as not running beside the previous run's result.
        running = dict(self.progress) if self.progress else None
        last = await queries.get_app_scratch(self.pool, "candle_backfill_last")
        nxt = now.date()
        done_today = bool(last and last.get("day") == nxt.isoformat() and last.get("complete"))
        if nxt.weekday() >= 5 or done_today:
            nxt += timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        async with self.pool.acquire() as conn:
            stored = await conn.fetchrow(
                "SELECT count(DISTINCT instrument_token) AS contracts, count(*) AS minutes, min(minute) AS since FROM candles_1m")
        return {
            "running": running,
            "last": last,
            "next_at": datetime.combine(nxt, BACKFILL_AT, IST).isoformat(),
            "stored": {"contracts": stored["contracts"], "minutes": stored["minutes"],
                       "since": stored["since"].isoformat() if stored["since"] else None},
        }

    async def remember_contracts(self, items: list[tuple[int, str]]) -> None:
        """Record what each (token, tradingsymbol) is, from the symbol."""
        rows = []
        for token, symbol in items:
            p = parse_contract(symbol or "")
            if p:
                rows.append({"instrument_token": token, "tradingsymbol": symbol, **p})
        await queries.upsert_contracts(self.pool, rows)


# Kite's longest window per request, by interval.
_MAX_SPAN = {"minute": 60, "3minute": 100, "5minute": 100, "10minute": 100, "15minute": 200,
             "30minute": 200, "60minute": 400, "day": 2000}


def _windows(start: datetime, end: datetime, interval: str) -> list[tuple[datetime, datetime]]:
    span = timedelta(days=_MAX_SPAN.get(interval, 60) - 1)
    out, a = [], start
    while a < end:
        b = min(end, a + span)
        out.append((a, b))
        a = b
    return out


def _chunks(days: list[date]) -> list[list[date]]:
    """Missing days -> request windows: consecutive runs (weekends don't
    break a run), at most MAX_DAYS_PER_REQUEST days each."""
    out: list[list[date]] = []
    for d in sorted(days):
        if out and (d - out[-1][-1]).days <= 3 and (d - out[-1][0]).days < MAX_DAYS_PER_REQUEST:
            out[-1].append(d)
        else:
            out.append([d])
    return out
