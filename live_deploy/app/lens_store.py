"""
LinckLens's price history, kept in the database (migration 0043): one
daily candle per instrument per day, for the shares you hold (and have
held, from imported trades) and NIFTY 50 to compare against. No minute
candles, no other instruments.

Each instrument's year is read from Kite once; after that only the days
since it was last read, at the 17:00 sync (DeploymentManager.
candle_backfill_loop), or when a page asks and a day is missing.
lens_instruments.fetched_through is the last day Kite has been asked
for: a day after the close is complete; before it, today isn't counted.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("live_deploy.lens_store")

IST = ZoneInfo("Asia/Kolkata")
YEAR_DAYS = 365
FIRST_READ_DAYS = YEAR_DAYS + 10      # a little before the year, so its first day has a close before it
DAY_DONE = time(15, 45)               # a day's daily candle is final by then
MAX_DAYS_PER_REQUEST = 1900           # Kite: up to 2000 days of daily candles a request


def last_complete_day(now: datetime) -> date:
    """The last weekday whose candle is final at `now` (IST)."""
    n = now.astimezone(IST)
    d = n.date() if n.time() >= DAY_DONE else n.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def to_read(fetched_through: Optional[date], first: Optional[date], now: datetime,
            since: Optional[date] = None) -> Optional[tuple[date, date]]:
    """The days to ask Kite for, or None: the whole year (or from `since`,
    a first trade, if earlier) the first time; afterwards the days after
    the last one read, to today. Nothing when it's all in."""
    today = now.astimezone(IST).date()
    want_from = min(since or today, today - timedelta(days=FIRST_READ_DAYS))
    if fetched_through is None or first is None:
        return want_from, today
    if since and since < first:
        return want_from, today          # an older trade than what's kept: read back to it
    if fetched_through >= last_complete_day(now):
        return None
    return fetched_through + timedelta(days=1), today


async def candles(pool, tokens: list[int], since: date) -> dict[int, list[dict]]:
    """{token: [{date, open, high, low, close, volume}]} from `since`, oldest first."""
    out: dict[int, list[dict]] = {int(t): [] for t in tokens}
    if not tokens:
        return out
    rows = await pool.fetch(
        "SELECT instrument_token, day, open, high, low, close, volume FROM lens_daily "
        "WHERE instrument_token = ANY($1::bigint[]) AND day >= $2 ORDER BY instrument_token, day",
        [int(t) for t in tokens], since)
    for r in rows:
        out[int(r["instrument_token"])].append({"date": r["day"], "open": r["open"], "high": r["high"],
                                                "low": r["low"], "close": r["close"], "volume": r["volume"]})
    return out


async def kept(pool, tokens: list[int]) -> dict[int, dict]:
    """{token: {symbol, first, last, fetched_through}} for what's kept."""
    if not tokens:
        return {}
    rows = await pool.fetch(
        "SELECT i.instrument_token, i.tradingsymbol, i.fetched_through, "
        "(SELECT min(day) FROM lens_daily d WHERE d.instrument_token = i.instrument_token) AS first, "
        "(SELECT max(day) FROM lens_daily d WHERE d.instrument_token = i.instrument_token) AS last "
        "FROM lens_instruments i WHERE i.instrument_token = ANY($1::bigint[])", [int(t) for t in tokens])
    return {int(r["instrument_token"]): {"symbol": r["tradingsymbol"], "first": r["first"], "last": r["last"],
                                         "fetched_through": r["fetched_through"]} for r in rows}


async def save(pool, token: int, symbol: str, exchange: Optional[str], rows: list[dict], fetched_through: date) -> int:
    """Kite's daily candles for one instrument, and how far it's been read."""
    vals = []
    for c in rows or []:
        d = c.get("date")
        if isinstance(d, datetime):
            d = d.astimezone(IST).date() if d.tzinfo else d.date()
        if d is None or c.get("close") is None:
            continue
        vals.append((int(token), d, float(c.get("open") or c["close"]), float(c.get("high") or c["close"]),
                     float(c.get("low") or c["close"]), float(c["close"]), int(c["volume"]) if c.get("volume") is not None else None))
    async with pool.acquire() as conn:
        async with conn.transaction():
            if vals:
                await conn.executemany(
                    "INSERT INTO lens_daily (instrument_token, day, open, high, low, close, volume) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (instrument_token, day) DO UPDATE SET "
                    "open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close, "
                    "volume = EXCLUDED.volume", vals)
            await conn.execute(
                "INSERT INTO lens_instruments (instrument_token, tradingsymbol, exchange, fetched_through, updated_at) "
                "VALUES ($1, $2, $3, $4, now()) ON CONFLICT (instrument_token) DO UPDATE SET "
                "tradingsymbol = EXCLUDED.tradingsymbol, exchange = COALESCE(EXCLUDED.exchange, lens_instruments.exchange), "
                "fetched_through = GREATEST(COALESCE(lens_instruments.fetched_through, EXCLUDED.fetched_through), EXCLUDED.fetched_through), "
                "updated_at = now()", int(token), symbol, exchange, fetched_through)
    return len(vals)


async def bring_up_to_date(pool, call: Callable[..., Awaitable[Any]], want: list[dict], now: datetime,
                           progress: Optional[dict] = None) -> dict:
    """Read from Kite what isn't kept yet for each of `want`
    ([{token, symbol, exchange, since?}]): the year the first time, then
    only the days since. One instrument failing doesn't stop the rest;
    it's named. Returns {asked, rows, failed: [symbol: error]}."""
    have = await kept(pool, [w["token"] for w in want])
    done_day = last_complete_day(now)
    asked, rows, failed = 0, 0, {}
    if progress is not None:
        progress.update({"total": len(want), "done": 0})
    for w in want:
        k = have.get(int(w["token"])) or {}
        span = to_read(k.get("fetched_through"), k.get("first"), now, w.get("since"))
        if span is not None:
            frm, to = span
            got: list[dict] = []
            try:
                while frm <= to:
                    end = min(to, frm + timedelta(days=MAX_DAYS_PER_REQUEST))
                    asked += 1
                    got += await call("historical_data", int(w["token"]), frm, end, "day") or []
                    frm = end + timedelta(days=1)
                rows += await save(pool, int(w["token"]), w["symbol"], w.get("exchange"), got, min(done_day, to))
            except Exception as e:
                failed[w["symbol"]] = f"{type(e).__name__}: {str(e)[:120]}"
                logger.warning("LinckLens prices for %s: %s", w["symbol"], failed[w["symbol"]])
        if progress is not None:
            progress["done"] += 1
    return {"asked": asked, "rows": rows, "failed": failed}
