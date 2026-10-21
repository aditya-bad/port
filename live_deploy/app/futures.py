"""
live_deploy — index futures: which contracts to keep, which one prices a
set of option legs, and its price now.

Why futures: an index option is priced off the forward, which the future
tracks — not the spot. A strike's real distance, how much an option moves
for a move in the market (its delta), and the "market move" part of a
trade's P&L are all truer against the future. The basis (future − spot)
is the market's carry: it shrinks to nothing at expiry.

Why stored: Kite drops a futures contract's history the day it expires,
like an option's (app/candles.py stores both). Two contracts per traded
index are kept — the current month and the next — filled every evening by
the candle backfill, and back to KEEP_DAYS on the first run, so both
listed contracts' history survives their expiry.

Which future prices a leg: the first one of the leg's own expiry month or
later — the same month for a monthly option, the current month for a
weekly — else the nearest one listed.
"""

from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .db import queries
from .options import NoKiteSession, OptionsResolver
from .options.resolver import INDEX_SPOT_SYMBOL, options_exchange_for

IST = ZoneInfo("Asia/Kolkata")
KEEP_DAYS = 60          # how far back a listed future's history is filled
QUOTE_TTL_S = 10        # a quote is reused this long (the positions panel polls)

# index spot token -> underlying name (NIFTY 50 -> NIFTY)
_INDEX_TOKENS = {256265: "NIFTY", 260105: "BANKNIFTY", 257801: "FINNIFTY", 288009: "MIDCPNIFTY",
                 265: "SENSEX", 274441: "BANKEX"}


def underlying_of(config: dict) -> Optional[str]:
    """A deployment's underlying, from its config: the strategy's own
    `instrument`/`options_underlying`/`underlying`, else its index token."""
    for k in ("options_underlying", "instrument", "underlying"):
        v = (config or {}).get(k)
        if isinstance(v, str) and v.strip().upper() in INDEX_SPOT_SYMBOL:
            return v.strip().upper()
    toks = (config or {}).get("instrument_tokens") or []
    return _INDEX_TOKENS.get(int(toks[0])) if toks else None


def spot_key(underlying: str) -> str:
    ex, sym = INDEX_SPOT_SYMBOL.get(underlying, ("NSE", underlying))
    return f"{ex}:{sym}"


async def traded_underlyings(pool) -> list[str]:
    """Every index a deployment that isn't stopped trades."""
    rows = await pool.fetch("SELECT config FROM deployments WHERE status <> 'stopped'")
    return sorted({u for u in (underlying_of(r["config"] or {}) for r in rows) if u})


async def listed(dispatcher, underlying: str, on: date, n: int = 2) -> list[dict]:
    """The next `n` futures of an index still listed on `on`, nearest
    first, from Kite's instrument master. Raises NoKiteSession."""
    ex = options_exchange_for(underlying)
    cache = await OptionsResolver(dispatcher, exchange=ex)._ensure_instruments(ex)
    rows = [r for r in cache["by_name"].get(underlying, []) if r.get("instrument_type") == "FUT" and r["expiry"] >= on]
    return sorted(rows, key=lambda r: r["expiry"])[:n]


async def remember(pool, rows: list[dict]) -> None:
    """Note each future (with its real expiry) in the contracts table."""
    await queries.upsert_contracts(pool, [{
        "instrument_token": int(r["instrument_token"]), "tradingsymbol": r["tradingsymbol"],
        "exchange": r.get("exchange"), "underlying": r.get("name"), "expiry": r["expiry"],
        "strike": None, "instrument_type": "FUT"} for r in rows])


async def to_keep(pool, dispatcher, on: date) -> list[dict]:
    """The futures the evening backfill keeps: current and next month of
    every traded index, remembered in the contracts table."""
    out = []
    for u in await traded_underlyings(pool):
        rows = await listed(dispatcher, u, on)
        await remember(pool, rows)
        out += rows
    return out


async def known(pool, underlying: str) -> list[dict]:
    """The futures of an index the app knows (stored or remembered),
    by expiry."""
    rows = await pool.fetch(
        "SELECT instrument_token, tradingsymbol, expiry FROM contracts "
        "WHERE instrument_type = 'FUT' AND underlying = $1 ORDER BY expiry", underlying)
    return [dict(r) for r in rows]


def pick(futures: list[dict], leg_expiry: Optional[date], on: date) -> Optional[dict]:
    """The future that prices legs expiring on `leg_expiry`: the first of
    its month or later (by month: a monthly option known only from its
    symbol carries the month's last day, not its real expiry); else (no
    legs) the nearest still live on `on`."""
    live = [f for f in futures if f["expiry"] and f["expiry"] >= on]
    if leg_expiry is not None:
        same = [f for f in live if (f["expiry"].year, f["expiry"].month) >= (leg_expiry.year, leg_expiry.month)]
        if same:
            return same[0]
    return live[0] if live else None


# ── a price now ──────────────────────────────────────────────────────────

_quotes: dict[tuple, tuple[float, dict]] = {}


async def quotes(dispatcher, keys: list[str]) -> dict:
    """Kite quotes (last price and the day's ohlc), reused for QUOTE_TTL_S.
    {} without a Kite session."""
    k = tuple(sorted(keys))
    hit = _quotes.get(k)
    if hit and _time.monotonic() - hit[0] < QUOTE_TTL_S:
        return hit[1]
    try:
        got = await OptionsResolver(dispatcher).get_quotes(list(keys))
    except NoKiteSession:
        return {}
    except Exception:
        return hit[1] if hit else {}
    _quotes[k] = (_time.monotonic(), got)
    if len(_quotes) > 50:
        _quotes.pop(next(iter(_quotes)))
    return got


async def stored_day(pool, token: int, day: date) -> Optional[dict]:
    """From the stored 1-minute candles: the day's open, its last close,
    when that was, and the previous day's close."""
    start = datetime.combine(day, datetime.min.time(), IST)
    row = await pool.fetchrow(
        "SELECT (array_agg(open ORDER BY minute))[1]::float8 AS o, (array_agg(close ORDER BY minute DESC))[1]::float8 AS c, "
        "max(minute) AS at FROM candles_1m WHERE instrument_token = $1 AND minute >= $2 AND minute < $3",
        token, start, start + timedelta(days=1))
    prev = await pool.fetchrow(
        "SELECT close::float8 AS c FROM candles_1m WHERE instrument_token = $1 AND minute < $2 ORDER BY minute DESC LIMIT 1",
        token, start)
    if (row is None or row["c"] is None) and prev is None:
        return None
    return {"open": row["o"] if row else None, "last": row["c"] if row and row["c"] is not None else (prev["c"] if prev else None),
            "at": row["at"] if row else None, "prev_close": prev["c"] if prev else None}
