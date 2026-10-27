"""
live_deploy — LinckLens: what the Kite account holds, beside LinckTrade
(the deployments). For now, the long-term holdings: shares (kite.holdings)
and mutual funds (kite.mf_holdings), read live from Kite, kept a minute.
Nothing here trades or is stored.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Callable, Optional

CACHE_S = 60


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def equity_row(h: dict) -> dict:
    """One share holding. The quantity is what's settled plus what's
    bought and not yet delivered (T1), as Kite shows it."""
    qty = _f(h.get("quantity")) + _f(h.get("t1_quantity"))
    avg, ltp, prev = _f(h.get("average_price")), _f(h.get("last_price")), _f(h.get("close_price"))
    invested, value = qty * avg, qty * ltp
    day = (ltp - prev) * qty if prev else None
    return {
        "symbol": h.get("tradingsymbol"), "exchange": h.get("exchange"), "isin": h.get("isin"),
        "qty": qty, "t1_qty": _f(h.get("t1_quantity")), "pledged_qty": _f(h.get("collateral_quantity")),
        "avg": round(avg, 4), "ltp": round(ltp, 4), "prev_close": round(prev, 4) if prev else None,
        "invested": round(invested, 2), "value": round(value, 2), "pnl": round(value - invested, 2),
        "pnl_pct": round((value - invested) / invested * 100, 2) if invested else None,
        "day_change": None if day is None else round(day, 2),
        "day_change_pct": round((ltp / prev - 1) * 100, 2) if prev else None,
    }


def mf_row(h: dict) -> dict:
    """One mutual fund holding, at its last published NAV."""
    units, avg, nav = _f(h.get("quantity")), _f(h.get("average_price")), _f(h.get("last_price"))
    invested, value = units * avg, units * nav
    when = h.get("last_price_date")
    return {
        "fund": h.get("fund"), "folio": h.get("folio"), "isin": h.get("tradingsymbol"),
        "units": units, "avg": round(avg, 4), "nav": round(nav, 4),
        "nav_date": when.isoformat() if isinstance(when, (date, datetime)) else when,
        "invested": round(invested, 2), "value": round(value, 2), "pnl": round(value - invested, 2),
        "pnl_pct": round((value - invested) / invested * 100, 2) if invested else None,
    }


def totals(rows: list[dict], day: bool = False) -> dict:
    invested = sum(r["invested"] for r in rows)
    value = sum(r["value"] for r in rows)
    out = {"count": len(rows), "invested": round(invested, 2), "value": round(value, 2),
           "pnl": round(value - invested, 2), "pnl_pct": round((value - invested) / invested * 100, 2) if invested else None}
    if day:
        known = [r["day_change"] for r in rows if r.get("day_change") is not None]
        change = sum(known)
        before = value - change
        out["day_change"] = round(change, 2) if known else None
        out["day_change_pct"] = round(change / before * 100, 2) if known and before else None
    return out


def build(equity: list[dict], mf: list[dict]) -> dict:
    """Pure: Kite's two answers -> the holdings, largest first, with their
    totals and the whole account's."""
    eq = sorted((equity_row(h) for h in equity or []), key=lambda r: -r["value"])
    funds = sorted((mf_row(h) for h in mf or []), key=lambda r: -r["value"])
    t_eq, t_mf = totals(eq, day=True), totals(funds)
    return {"equity": eq, "mf": funds, "totals": {"equity": t_eq, "mf": t_mf, "all": totals(eq + funds)}}


class Lens:
    """Reads the holdings from Kite through the app's own Kite calls (the
    process-wide throttle), and keeps them CACHE_S seconds."""

    def __init__(self, dispatcher, call: Optional[Callable[[str], Any]] = None):
        self.dispatcher = dispatcher
        self.call = call or self._kite
        self._cache: Optional[tuple[float, dict]] = None

    async def _kite(self, what: str):
        from .options import OptionsResolver
        r = OptionsResolver(self.dispatcher)
        kite = r._kite()
        return await r._kite_call(getattr(kite, what))

    async def holdings(self, fresh: bool = False) -> dict:
        now = time.monotonic()
        if self._cache and not fresh and now - self._cache[0] < CACHE_S:
            return self._cache[1]
        out = build(await self.call("holdings"), await self.call("mf_holdings"))
        out["as_of"] = datetime.now().astimezone().isoformat()
        self._cache = (now, out)
        return out
