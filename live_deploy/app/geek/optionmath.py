"""
live_deploy — Black-Scholes for Geek mode's volatility views: an
option's price, the volatility its price implies, and where a trade's P&L
came from. Pure; never read by a strategy.

Index options are European, so Black-Scholes fits: no dividends, a flat
risk-free rate (RATE), time to expiry in calendar years to 15:30 IST on
the expiry day.

Where a leg's P&L came from (attribute), by revaluing it one change at a
time, so the parts add up to the price change exactly:
  time decay = BS(S0, σ0, T1) − BS(S0, σ0, T0)   — only the clock moved
  the move   = BS(S1, σ0, T1) − BS(S0, σ0, T1)   — then the index moved
  volatility = P1 − BS(S1, σ0, T1)               — the rest: implied volatility changed
with σ0 the volatility implied by the entry price. A seller's P&L is the
negative of the price change.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
RATE = 0.065
EXPIRY_AT = time(15, 30)
MIN_T = 1 / (365 * 24 * 60)   # a minute: at expiry an option is worth what it's in the money


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def years_to(expiry: date, at: datetime) -> float:
    end = datetime.combine(expiry, EXPIRY_AT, IST)
    return max(MIN_T, (end - at.astimezone(IST)).total_seconds() / (365 * 86400))


def price(kind: str, s: float, k: float, t: float, vol: float, r: float = RATE) -> float:
    """Black-Scholes price of a CE or PE."""
    if t <= MIN_T or vol <= 0:
        return max(0.0, s - k) if kind == "CE" else max(0.0, k - s)
    sq = vol * math.sqrt(t)
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / sq
    d2 = d1 - sq
    if kind == "CE":
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def vega(s: float, k: float, t: float, vol: float, r: float = RATE) -> float:
    """How much the price moves for one whole unit of volatility (1.0 =
    100 points); the same for a CE and a PE. Divide by 100 for a point."""
    if t <= MIN_T or vol <= 0 or s <= 0 or k <= 0:
        return 0.0
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    return s * math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) * math.sqrt(t)


def implied_vol(kind: str, premium: float, s: float, k: float, t: float, r: float = RATE) -> Optional[float]:
    """The volatility (a fraction: 0.15 = 15%) that prices the option at
    `premium`, or None when no volatility can (below what it's worth in
    the money, or a nonsense price)."""
    if premium is None or premium <= 0 or s <= 0 or k <= 0:
        return None
    lo, hi = 0.005, 5.0
    if premium < price(kind, s, k, t, lo, r) - 1e-6 or premium > price(kind, s, k, t, hi, r):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if price(kind, s, k, t, mid, r) < premium:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 6)


def attribute(kind: str, k: float, expiry: date, t0: datetime, s0: float, p0: float,
              t1: datetime, s1: float, p1: float, qty: float, sold: bool) -> Optional[dict]:
    """One leg, entry (t0, index s0, premium p0) to exit (t1, s1, p1):
    {time, move, vol, total, iv0} in rupees for its side and quantity.
    The parts add up to its P&L. None without an implied volatility."""
    T0, T1 = years_to(expiry, t0), years_to(expiry, t1)
    iv = implied_vol(kind, p0, s0, k, T0)
    if iv is None:
        return None
    b0 = price(kind, s0, k, T0, iv)
    b_time = price(kind, s0, k, T1, iv)
    b_move = price(kind, s1, k, T1, iv)
    sign = -1.0 if sold else 1.0
    parts = {"time": (b_time - b0), "move": (b_move - b_time), "vol": (p1 - b_move)}
    # b0 is p0 up to the solver's precision: put that sliver in volatility, so the parts sum exactly
    parts["vol"] += b0 - p0
    out = {k_: round(v * qty * sign, 2) for k_, v in parts.items()}
    out["total"] = round((p1 - p0) * qty * sign, 2)
    out["iv0"] = round(iv * 100, 2)
    return out


# ── Black-76: an option priced off its future, not the spot ─────────────
#
# An index option is priced off the forward, which its future tracks; the
# spot only gets there through an assumed rate. With the future's own
# price F the rate drops out of the forward and only discounts:
#   price = e^(−rT) · BS(F, K, T, σ, r = 0)

def fwd_price(kind: str, f: float, k: float, t: float, vol: float, r: float = RATE) -> float:
    return math.exp(-r * t) * price(kind, f, k, t, vol, 0.0)


def fwd_implied_vol(kind: str, premium: float, f: float, k: float, t: float, r: float = RATE) -> Optional[float]:
    if premium is None or premium <= 0:
        return None
    return implied_vol(kind, premium * math.exp(r * t), f, k, t, 0.0)


def fwd_greeks(kind: str, f: float, k: float, t: float, vol: float, r: float = RATE) -> tuple[float, float]:
    """(delta, gamma) against the future: per point, and per point²."""
    if t <= MIN_T or vol <= 0 or f <= 0 or k <= 0:
        itm = (f > k) if kind == "CE" else (f < k)
        return ((1.0 if kind == "CE" else -1.0) if itm else 0.0), 0.0
    sq = vol * math.sqrt(t)
    d1 = (math.log(f / k) + vol * vol / 2 * t) / sq
    disc = math.exp(-r * t)
    delta = disc * (_ncdf(d1) if kind == "CE" else _ncdf(d1) - 1)
    gamma = disc * math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (f * sq)
    return delta, gamma


def quick_implied_vol(kind: str, premium: float, s: float, k: float, t: float, fwd: bool = False,
                      r: float = RATE) -> Optional[float]:
    """implied_vol (or fwd_implied_vol when `fwd`: s is then the future),
    by Newton's method from 20% — a few steps instead of eighty halvings,
    for solving every leg at every step of a replay. Falls back to the
    bisection where Newton doesn't settle (deep in or out of the money)."""
    if premium is None or premium <= 0 or s is None or s <= 0 or k <= 0:
        return None
    p, rr = (premium * math.exp(r * t), 0.0) if fwd else (premium, r)
    if p < price(kind, s, k, t, 0.005, rr) - 1e-6 or p > price(kind, s, k, t, 5.0, rr):
        return None
    vol = 0.2
    for _ in range(12):
        diff = price(kind, s, k, t, vol, rr) - p
        if abs(diff) < 1e-7:
            return round(vol, 6)
        v = vega(s, k, t, vol, rr)
        if v < 1e-8:
            break
        vol -= diff / v
        if not 0.005 <= vol <= 5.0:
            break
    return implied_vol(kind, p, s, k, t, rr)
