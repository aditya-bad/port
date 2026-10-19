"""
live_deploy — price history for every instrument the app holds or
watches (indices and option/futures contracts), from one store.

Storage: candles_1m (migration 0021) — one 1-minute OHLC row per
instrument per minute, nothing else. A contract held by an intraday and
a positional deployment at once is stored once; 5/15/60-minute candles
are rolled up from the 1-minute rows when read (rollup()), anchored at
09:15 like Kite's. Rows come from three places, best replacing worst:
  - live ticks, consolidated per minute (DeploymentManager.candles_loop)
    for every index and every contract any deployment holds;
  - Kite's historical API, filling whatever minutes are missing — on a
    chart request (series()) while a contract is still listed, and once
    after every close for everything held that day (backfill_day()), so
    a contract's history survives its expiry (Kite has none after);
  - backups imported with custom_scripts/import_candles.py.

Kite calls: one history request in flight at a time app-wide, through
the resolver's process-wide throttle (≈3 requests a second, Kite's
limit, with 429 back-off); a request for a day already covered is never
made, a contract past its expiry is never asked for, and an instrument
Kite had nothing for isn't asked again for an hour.
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
        self.kite_requests = 0                    # for tests and the status endpoint

    # ── Reading ──────────────────────────────────────────────────────

    async def series(self, token: int, start: datetime, end: datetime, interval: str = "minute",
                     *, fetch: bool = True, symbol: Optional[str] = None) -> tuple[list[dict], Optional[str], Optional[str]]:
        """(candles, source, error) for [start, end). Missing minutes are
        filled from Kite first when `fetch` and the contract is still
        listed. source: "stored" (covered), "stored_partial" (some days
        incomplete) or None (nothing); error says why data is missing."""
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

    async def backfill_day(self, day: date, extra_tokens: tuple = ()) -> dict:
        """Everything held on `day` (each distinct instrument once,
        whichever deployments held it) plus `extra_tokens` (the indices):
        fill the day's missing minutes from Kite while it still has them.
        Returns {"instruments": n, "fetched": tokens fetched, "rows": n}."""
        start = datetime.combine(day, SESSION_OPEN, IST)
        end = datetime.combine(day, SESSION_CLOSE, IST)
        held = await queries.instruments_held_between(self.pool, start, end)
        await self.remember_contracts([(int(r["instrument_token"]), r["symbol"]) for r in held])
        targets = {int(r["instrument_token"]): r["symbol"] for r in held}
        for t in extra_tokens:
            targets.setdefault(int(t), None)
        fetched, rows = [], 0
        for token, symbol in sorted(targets.items()):
            before = self.kite_requests
            try:
                n = await self.ensure(token, start, end, symbol=symbol)
            except NoKiteSession:
                logger.warning("Candle backfill for %s stopped: Kite isn't logged in", day)
                break
            except Exception:
                logger.exception("Candle backfill: %s (%s) failed — continuing", symbol or token, token)
                continue
            if self.kite_requests > before:
                fetched.append(token)
            rows += n
        logger.info("Candle backfill %s: %d instrument(s), %d fetched from Kite, %d minute(s) stored",
                    day, len(targets), len(fetched), rows)
        return {"instruments": len(targets), "fetched": fetched, "rows": rows}

    async def remember_contracts(self, items: list[tuple[int, str]]) -> None:
        """Record what each (token, tradingsymbol) is, from the symbol."""
        rows = []
        for token, symbol in items:
            p = parse_contract(symbol or "")
            if p:
                rows.append({"instrument_token": token, "tradingsymbol": symbol, **p})
        await queries.upsert_contracts(self.pool, rows)


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
