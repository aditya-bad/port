"""
live_deploy — index futures: which contracts to keep, which one prices a
set of option legs, and its price now.

Why futures: an index option is priced off the forward, which the future
tracks — not the spot. A strike's real distance, how much an option moves
for a move in the market (its delta), and the "market move" part of a
trade's P&L are all truer against the future. The basis (future − spot)
is the market's carry: it shrinks to nothing at expiry.

Why stored: Kite drops a futures contract's history the day it expires,
like an option's (app/candles.py stores both). Kept, for every traded
index: the current and next month, and the future of every option leg
held that day (by the rule below) — filled every evening by the candle
backfill, back to KEEP_DAYS on the first run.

Which future prices a leg (pick):
  1. the future with the leg's own expiry date, if one is listed;
  2. else the monthly future of the leg's expiry month (a weekly option
     takes its month's future);
  3. else none — a leg further out than any listed future (next year's
     December) is valued off the spot and its future is ignored.
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


async def listed(dispatcher, underlying: str, on: date, n: Optional[int] = None) -> list[dict]:
    """The futures of an index still listed on `on` (the next `n`, or all),
    nearest first, from Kite's instrument master. Raises NoKiteSession."""
    ex = options_exchange_for(underlying)
    cache = await OptionsResolver(dispatcher, exchange=ex)._ensure_instruments(ex)
    rows = [r for r in cache["by_name"].get(underlying, []) if r.get("instrument_type") == "FUT" and r["expiry"] >= on]
    rows = sorted(rows, key=lambda r: r["expiry"])
    return rows[:n] if n else rows


async def remember(pool, rows: list[dict]) -> None:
    """Note each future (with its real expiry) in the contracts table."""
    await queries.upsert_contracts(pool, [{
        "instrument_token": int(r["instrument_token"]), "tradingsymbol": r["tradingsymbol"],
        "exchange": r.get("exchange"), "underlying": r.get("name"), "expiry": r["expiry"],
        "strike": None, "instrument_type": "FUT"} for r in rows])


async def held_expiries(pool, on: date) -> dict[str, set]:
    """{underlying: {expiry, ...}} of every option leg held on `on`: its
    expiry from the contracts table, else its symbol (a monthly symbol
    gives its month, which is all the month rule needs)."""
    from .candles import parse_contract
    start = datetime.combine(on, datetime.min.time(), IST)
    out: dict[str, set] = {}
    for r in await queries.instruments_held_between(pool, start, start + timedelta(days=1)):
        c = parse_contract(r["symbol"] or "")
        if not c or c["instrument_type"] not in ("CE", "PE"):
            continue
        row = await queries.get_contract(pool, int(r["instrument_token"]))
        exp = (row["expiry"] if row is not None and row["expiry"] else None) or c["expiry"]
        out.setdefault(c["underlying"], set()).add(exp)
    return out


async def to_keep(pool, dispatcher, on: date) -> list[dict]:
    """The futures the evening backfill keeps, remembered in the contracts
    table: for every traded index, the current and next month, and the
    future of every option leg held on `on` (pick's rule; a leg with none
    listed is skipped)."""
    legs = await held_expiries(pool, on)
    out = []
    for u in sorted(set(await traded_underlyings(pool)) | set(legs)):
        rows = await listed(dispatcher, u, on)
        keep = {int(r["instrument_token"]): r for r in rows[:2]}
        for e in legs.get(u, ()):
            f = pick(rows, e, on)
            if f is not None:
                keep[int(f["instrument_token"])] = f
        await remember(pool, list(keep.values()))
        out += list(keep.values())
    return out


async def known(pool, underlying: str) -> list[dict]:
    """The futures of an index the app knows (stored or remembered),
    by expiry."""
    rows = await pool.fetch(
        "SELECT instrument_token, tradingsymbol, expiry FROM contracts "
        "WHERE instrument_type = 'FUT' AND underlying = $1 ORDER BY expiry", underlying)
    return [dict(r) for r in rows]


def pick(futures: list[dict], leg_expiry: Optional[date], on: date) -> Optional[dict]:
    """The future that prices a leg expiring on `leg_expiry`: the one with
    that very expiry; else the monthly future of that month; else None (no
    future that far out: ignored). With no leg (`leg_expiry` None): the
    nearest future still live on `on`."""
    if leg_expiry is None:
        live = [f for f in futures if f["expiry"] and f["expiry"] >= on]
        return live[0] if live else None
    exact = [f for f in futures if f["expiry"] == leg_expiry]
    if exact:
        return exact[0]
    month = [f for f in futures if f["expiry"] and (f["expiry"].year, f["expiry"].month) == (leg_expiry.year, leg_expiry.month)]
    return max(month, key=lambda f: f["expiry"]) if month else None   # the monthly: the month's last expiry


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
