"""
live_deploy — Tools › Compare with underlying: how closely ETFs or stocks
follow an index (or any instrument), from Kite's candles.

Everything is put on one scale: the % change since the first candle all
of them have. For each one compared, the gap is its % change minus the
underlying's, candle by candle (in percentage points): flat at 0 is
perfect tracking. The numbers:

  ret          its % change over the period; base_ret the underlying's
  diff         ret − base_ret: the tracking difference, in points
  te           how much the gap moves candle to candle: the standard
               deviation of (its return − the underlying's) per candle;
               te_year: that × √252, for daily candles (a yearly figure)
  max_gap      the widest gap on any candle (signed) and when
  corr, beta   of its candle returns against the underlying's
  traded       its average traded value a candle (close × volume), ₹

Only candles every series has are used (a holiday on one exchange, a
candle a thin ETF didn't trade): `dropped` says how many of the
underlying's were left out.

Measured from traded prices, not NAV (Kite has no NAV for ETFs). An
index from Kite is its price index, without dividends, which ETFs keep:
one that tracks well drifts slowly above it.
"""

from __future__ import annotations

import math
from typing import Optional

from ..options.resolver import INDEX_SPOT_SYMBOL, OptionsResolver

SHORTCUTS = ("NIFTY", "BANKNIFTY", "SENSEX", "BANKEX")
MAX_WITH = 8
YEAR = 252


async def shortcut(dispatcher, key: str) -> Optional[dict]:
    """One of the four indices LinckTrade trades: {token, symbol, exchange}."""
    if key not in SHORTCUTS:
        return None
    exchange, symbol = INDEX_SPOT_SYMBOL[key]
    cache = await OptionsResolver(dispatcher)._ensure_instruments(exchange)
    row = next((r for r in cache["data"] if r.get("tradingsymbol") == symbol), None)
    return None if row is None else {"token": int(row["instrument_token"]), "symbol": symbol, "exchange": exchange}


def _std(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _r(v: Optional[float], dp: int = 4) -> Optional[float]:
    return None if v is None or not math.isfinite(v) else round(v, dp)


def compare(base: list[dict], others: list[tuple[int, list[dict]]], interval: str = "day") -> dict:
    """base, others: candles [{t, c, v?}] oldest first. Aligned on the
    candles every series has; see the module docstring for the figures."""
    def by_t(cs):
        return {c["t"]: c for c in cs if c.get("c") and c["c"] > 0}
    b = by_t(base)
    o = [(tok, by_t(cs)) for tok, cs in others]
    times = sorted(t for t in b if all(t in m for _, m in o))
    out = {"interval": interval, "t": times, "dropped": len(b) - len(times), "base": None, "others": []}
    if len(times) < 2:
        out["error"] = ("Not enough candles in common to compare: " + ("the underlying has none in this period"
                        if not b else "pick a longer period, or check each one traded in it"))
        return out
    bc = [b[t]["c"] for t in times]
    bp = [(c / bc[0] - 1) * 100 for c in bc]
    br = [bc[i] / bc[i - 1] - 1 for i in range(1, len(bc))]
    out["base"] = {"c": bc, "pct": [_r(x) for x in bp], "ret": _r(bp[-1])}
    for tok, m in o:
        oc = [m[t]["c"] for t in times]
        op = [(c / oc[0] - 1) * 100 for c in oc]
        gap = [x - y for x, y in zip(op, bp)]
        orr = [oc[i] / oc[i - 1] - 1 for i in range(1, len(oc))]
        d = [(x - y) * 100 for x, y in zip(orr, br)]           # per candle, in points
        te = _std(d)
        sb, so = _std(br), _std(orr)
        mb, mo = sum(br) / len(br), sum(orr) / len(orr)
        cov = sum((x - mb) * (y - mo) for x, y in zip(br, orr)) / (len(br) - 1) if len(br) > 1 else None
        k = max(range(len(gap)), key=lambda i: abs(gap[i]))
        traded = [m[t]["c"] * m[t].get("v", 0) for t in times if m[t].get("v")]
        out["others"].append({
            "token": tok, "c": oc, "pct": [_r(x) for x in op], "gap": [_r(x) for x in gap],
            "stats": {
                "ret": _r(op[-1]), "base_ret": _r(bp[-1]), "diff": _r(op[-1] - bp[-1]),
                "te": _r(te), "te_year": _r(te * math.sqrt(YEAR)) if te is not None and interval == "day" else None,
                "max_gap": _r(gap[k]), "max_gap_at": times[k], "mean_gap": _r(sum(gap) / len(gap)),
                "corr": _r(cov / (sb * so)) if cov is not None and sb and so else None,
                "beta": _r(cov / (sb * sb)) if cov is not None and sb else None,
                "traded": _r(sum(traded) / len(traded), 0) if traded else None,
                "candles": len(times),
            },
        })
    return out
