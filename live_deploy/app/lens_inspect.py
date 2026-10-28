"""
LinckLens Inspector: the deeper reads of your trades and holdings.

- own_money(): how much of what you hold is your own money, and how much
  is profit you booked and put back in. Every sale's money goes into a
  pot; every buy is paid from the pot first and only the rest is new
  money of yours. So reusing a sale's money (or selling and buying back to
  use the yearly tax-free long-term gain) raises what "invested" shows,
  but not your own money.
- tax_free(): long-term gains booked each financial year on what the
  ₹1.25 lakh exemption covers (listed shares, equity ETFs, equity funds,
  REITs and InvITs; ₹1 lakh before 2024-25), what's left of it this year,
  and the lots that would use it, oldest first as a sale takes them.
- compare(): your figures with and without some holdings: had you never
  bought them (from the trades), and today's holdings without them over
  the last year (from the daily prices).
- contributions(): each holding, by how much your return would change
  without it.

Pure: trades, holdings and prices in, figures out (app/lens.py feeds it).
A tradebook doesn't show money moved in or out of the account: the pot
assumes a sale's money stayed there to be reused.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from typing import Optional

from . import lens_trades as T

EPS = 1e-6
HARVEST_DAYS = 7               # sold and bought back within a week: a harvest
EXEMPT = {"stock", "etf", "reit", "mf_equity", "mf_elss", "mf_index", "mf_hybrid"}   # what s.112A's exemption covers
MAYBE = {"mf_hybrid"}          # only if the fund is equity-oriented


def _r(v, n=2):
    return None if v is None else round(v, n)


def exemption(fy: str) -> int:
    """The yearly tax-free long-term gain on listed equity: ₹1.25 lakh
    from 2024-25, ₹1 lakh before."""
    return 125000 if int(fy[:4]) >= 2024 else 100000


def asset_of(t: dict, holdings: dict) -> str:
    """What a traded instrument is: Kite's own word when it's held, else
    read from its symbol or the fund's name."""
    from .lens import asset_of_fund, asset_of_share
    h = holdings.get(t.get("isin")) or {}
    if h.get("asset"):
        return h["asset"]
    return asset_of_fund(None, t["symbol"]) if T.kind_of(t) == "mf" else asset_of_share(t["symbol"])


def _ordered(trades: list[dict]) -> list[dict]:
    """Oldest first; at the same moment, sales before buys (a sale's money
    can pay for a buy the same day)."""
    return sorted((t for t in trades if T.kind_of(t) in ("equity", "mf")),
                  key=lambda t: (t.get("executed_at") or datetime.combine(t["trade_date"], datetime.min.time()),
                                 0 if t["trade_type"] == "sell" else 1))


# ── Your own money ───────────────────────────────────────────────────

def own_money(trades: list[dict], holdings: Optional[dict] = None, today: Optional[date] = None) -> Optional[dict]:
    today, holdings = today or date.today(), holdings or {}
    ts = _ordered(trades)
    if not ts:
        return None
    pot = fresh = reused = booked = cost_now = bought = sold = unmatched = 0.0
    lots: dict[str, deque] = defaultdict(deque)
    flows: list[tuple[date, float]] = []
    months: dict[str, dict] = {}
    years: dict[str, dict] = {}
    sells_by_key: dict[str, list] = defaultdict(list)
    for t in ts:
        k, q, px, d = T.key_of(t), float(t["quantity"]), float(t["price"]), t["trade_date"]
        amt = q * px
        y = years.setdefault(T.fy(d), {"fy": T.fy(d), "fresh": 0.0, "reused": 0.0, "booked": 0.0, "bought": 0.0, "sold": 0.0})
        if t["trade_type"] == "buy":
            use = min(pot, amt)
            pot -= use
            reused += use
            new = amt - use
            fresh += new
            bought += amt
            cost_now += amt
            if new > EPS:
                flows.append((d, -new))
            lots[k].append([q, px, d])
            y["fresh"] += new
            y["reused"] += use
            y["bought"] += amt
        else:
            pot += amt
            sold += amt
            rem, cost = q, 0.0
            while rem > EPS and lots[k]:
                lot = lots[k][0]
                take = min(rem, lot[0])
                cost += take * lot[1]
                lot[0] -= take
                rem -= take
                if lot[0] <= EPS:
                    lots[k].popleft()
            if rem > EPS:                       # sold more than the trades bought: no cost known
                unmatched += rem * px
                cost += rem * px                # counted at no gain rather than guessed
            booked += amt - cost
            cost_now -= cost
            y["booked"] += amt - cost
            y["sold"] += amt
            sells_by_key[k].append((d, q, amt - cost))
        m = months.setdefault(d.strftime("%Y-%m"), {})
        m.update({"month": d.strftime("%Y-%m"), "own": fresh, "cost": cost_now, "pot": pot})
    # what's held from the trades, at today's price where Kite has it
    value, unpriced = 0.0, []
    for k, ls in lots.items():
        q = sum(l[0] for l in ls)
        if q <= EPS:
            continue
        isin = k if not k.count(":") else None
        h = holdings.get(isin) if isin else None
        if h and h.get("ltp"):
            value += q * h["ltp"]
        else:
            value += sum(l[0] * l[1] for l in ls)
            sym = next((t["symbol"] for t in ts if T.key_of(t) == k), k)
            unpriced.append(sym)
    gain = value + pot - fresh
    # sold and bought back within a week: gains (or losses) booked on purpose
    harvests = []
    buys_by_key: dict[str, list] = defaultdict(list)
    for t in ts:
        if t["trade_type"] == "buy":
            buys_by_key[T.key_of(t)].append(t)
    for k, sells in sells_by_key.items():
        for d, q, g in sells:
            back = [b for b in buys_by_key[k] if d <= b["trade_date"] <= d + timedelta(days=HARVEST_DAYS)]
            if back:
                bq = sum(float(b["quantity"]) for b in back)
                sym = back[0]["symbol"]
                harvests.append({"symbol": sym, "sold_on": d.isoformat(), "bought_on": back[0]["trade_date"].isoformat(),
                                 "qty": _r(q, 4), "bought_qty": _r(bq, 4), "booked": _r(g), "kind": "gain" if g >= 0 else "loss"})
    harvests.sort(key=lambda h: h["sold_on"], reverse=True)
    return {
        "own": _r(fresh), "reused": _r(reused), "pot": _r(pot), "bought": _r(bought), "sold": _r(sold),
        "booked": _r(booked), "cost_now": _r(cost_now), "value": _r(value),
        "profit_in_holdings": _r(cost_now - (fresh - pot)),      # = booked profit put back to work
        "gain": _r(gain), "gain_pct": _r(gain / fresh * 100) if fresh > EPS else None,
        "xirr": _r(T.xirr(flows + [(today, value + pot)])),
        "times_turned": _r(bought / fresh, 2) if fresh > EPS else None,
        "unmatched": _r(unmatched), "unpriced": sorted(set(unpriced)),
        "months": [{k: (_r(v) if isinstance(v, float) else v) for k, v in months[m].items()} for m in sorted(months)],
        "years": [{k: (_r(v) if isinstance(v, float) else v) for k, v in years[y].items()} for y in sorted(years)],
        "harvests": harvests,
        "first_day": ts[0]["trade_date"].isoformat(),
    }


# ── The tax-free long-term gain ──────────────────────────────────────

def tax_free(trades: list[dict], holdings: Optional[dict] = None, today: Optional[date] = None) -> Optional[dict]:
    today, holdings = today or date.today(), holdings or {}
    ts = [t for t in trades if T.kind_of(t) in ("equity", "mf")]
    if not ts:
        return None
    assets = {t.get("isin"): asset_of(t, holdings) for t in ts if t.get("isin")}
    L = T.lots(ts, assets)
    years: dict[str, dict] = {}
    for k, x in L.items():
        a = assets.get(x["isin"])
        if a not in EXEMPT:
            continue
        for c in x["closed"]:
            y = years.setdefault(T.fy(c["sell_day"]), {"fy": T.fy(c["sell_day"]), "long": 0.0, "short": 0.0})
            y[c["term"]] = y.get(c["term"], 0.0) + c["pnl"]
    out_years = []
    for fy_ in sorted(years):
        y = years[fy_]
        ex = exemption(fy_)
        out_years.append({"fy": fy_, "long": _r(y["long"]), "short": _r(y["short"]), "exemption": ex,
                          "used": _r(min(max(y["long"], 0.0), ex)), "over": _r(max(y["long"] - ex, 0.0))})
    this = T.fy(today)
    ex = exemption(this)
    done = years.get(this, {}).get("long", 0.0)
    room = max(ex - max(done, 0.0), 0.0)
    fy_end = date(int(this[:4]) + 1, 3, 31)
    # what's held long term now, oldest first (a sale takes the oldest units)
    offers = []
    for k, x in L.items():
        a = assets.get(x["isin"])
        h = holdings.get(x["isin"]) or {}
        px = h.get("ltp")
        if a not in EXEMPT or not px or not x["open"]:
            continue
        lt, q, gain = [], 0.0, 0.0
        for l in x["open"]:
            if (today - l["day"]).days <= T.LONG_TERM_DAYS:
                break
            lt.append(l)
            q += l["qty"]
            gain += l["qty"] * (px - l["price"])
        if q > EPS:
            offers.append({"key": k, "symbol": x["symbol"], "isin": x["isin"], "asset": a, "kind": x["kind"], "price": px,
                           "lots": lt, "qty": q, "gain": gain, "value": q * px})
    plan, left = [], room
    for o in sorted((o for o in offers if o["gain"] > EPS), key=lambda o: -o["gain"] / o["value"]):
        if left <= EPS:
            break
        q = g = 0.0
        step = 1.0 if o["kind"] == "equity" else 0.001
        for l in o["lots"]:
            per = o["price"] - l["price"]
            if per <= 0 or g + l["qty"] * per <= left:
                q += l["qty"]
                g += l["qty"] * per
                continue
            n = math.floor((left - g) / per / step) * step
            q += n
            g += n * per
            break
        if q > EPS and g > EPS:
            plan.append({"symbol": o["symbol"], "isin": o["isin"], "qty": _r(q, 3), "sale": _r(q * o["price"]), "gain": _r(g),
                         "maybe": o["asset"] in MAYBE})
            left -= g
    return {
        "fy": this, "exemption": ex, "booked": _r(done), "room": _r(room), "days_left": (fy_end - today).days,
        "plan": plan, "plan_gain": _r(sum(p["gain"] for p in plan)),
        "available": [{"symbol": o["symbol"], "qty": _r(o["qty"], 3), "gain": _r(o["gain"]), "value": _r(o["value"]),
                       "maybe": o["asset"] in MAYBE} for o in sorted(offers, key=lambda o: -o["gain"])],
        "years": out_years,
    }


# ── With and without ─────────────────────────────────────────────────

def aligned(rows: list[dict], candles: dict, days: list[date]) -> dict[str, list[float]]:
    """{isin: [qty × close each day]} for today's shares with prices over
    all the days (a newer listing would jump the line: left out)."""
    from .lens import _closes
    out = {}
    for r in rows:
        s = _closes(candles.get(r.get("token")) or [])
        if not s or s[0][0] > days[0] + timedelta(days=7):
            continue
        vals, i, last = [], 0, s[0][1]
        for d in days:
            while i < len(s) and s[i][0] <= d:
                last = s[i][1]
                i += 1
            vals.append(r["qty"] * last)
        out[r.get("isin") or r["symbol"]] = vals
    return out


def year_figures(series: dict[str, list[float]], days: list[date], nifty: list[float]) -> Optional[dict]:
    from .lens import _drawdown, _sd
    if not series:
        return None
    v = [sum(xs) for xs in zip(*series.values())]
    if not v or v[0] <= 0:
        return None
    rets = [math.log(b / a) for a, b in zip(v, v[1:]) if a > 0 and b > 0]
    sd = _sd(rets)
    return {"ret": _r((v[-1] / v[0] - 1) * 100), "nifty": _r((nifty[-1] / nifty[0] - 1) * 100) if nifty else None,
            "vol": _r(sd * math.sqrt(252) * 100, 1) if sd is not None else None,
            "drawdown": _drawdown(v, days)["pct"], "count": len(series)}


def _spread(rows: list[dict]) -> dict:
    total = sum(r["value"] for r in rows)
    if not total:
        return {"effective": None, "top": None, "top_weight": None}
    ws = sorted(((r["value"] / total, r) for r in rows), key=lambda x: -x[0])
    return {"effective": _r(1 / sum(w * w for w, _ in ws), 1), "top": ws[0][1].get("symbol") or ws[0][1].get("fund"),
            "top_weight": _r(ws[0][0] * 100, 1)}


def side(trades: list[dict], held: dict, rows: list[dict], series: dict, days: list[date], nifty: list[float],
         today: date, curve_prices: Optional[dict] = None, nifty_series=None) -> dict:
    """One column of the comparison."""
    out: dict = {}
    if trades:
        a = T.analyse(trades, held, today)
        tt = a["totals"]["all"] or {}
        own = own_money(trades, held, today) or {}
        out["trades"] = {"xirr": tt.get("xirr"), "booked": tt.get("realized"), "on_paper": tt.get("unrealized"),
                         "own": own.get("own"), "own_gain": own.get("gain"), "own_gain_pct": own.get("gain_pct"),
                         "own_xirr": own.get("xirr")}
        if curve_prices is not None and nifty_series:
            c = T.value_curve(trades, curve_prices, nifty_series, today)
            out["trades"]["vs_nifty"] = c["now"]["vs_nifty"] if c and c.get("now") else None
    value = sum(r["value"] for r in rows)
    out["now"] = {"value": _r(value), "pnl": _r(sum(r["pnl"] for r in rows)), "count": len(rows), **_spread(rows)}
    out["year"] = year_figures(series, days, nifty)
    return out


def compare(trades: list[dict], h: dict, candles: dict, nifty_candles: list[dict], today: date, without: set,
            curve_prices: Optional[dict] = None) -> dict:
    """Everything with, and without the holdings named (by ISIN)."""
    from .lens import YEAR_DAYS, _closes
    held = _held(h)
    rows = [r for r in (h.get("equity") or []) + (h.get("mf") or [])]
    n = [x for x in _closes(nifty_candles) if x[0] >= today - timedelta(days=YEAR_DAYS)]
    days, nifty = [d for d, *_ in n], [c for _, c, _, _ in n]
    series = aligned(h.get("equity") or [], candles, days) if len(days) > 20 else {}
    nifty_series = [(d, c) for d, c, _, _ in _closes(nifty_candles)]
    keep = [t for t in trades if (t.get("isin") or t["symbol"]) not in without]
    return {
        "without": sorted(without),
        "with": side(trades, held, rows, series, days, nifty, today, curve_prices, nifty_series),
        "without_them": side(keep, held, [r for r in rows if (r.get("isin") or "") not in without],
                             {k: v for k, v in series.items() if k not in without}, days, nifty, today, curve_prices, nifty_series),
        "left_out_of_year": sorted(r["symbol"] for r in h.get("equity") or [] if (r.get("isin") or r["symbol"]) not in series),
    }


def contributions(trades: list[dict], h: dict, candles: dict, nifty_candles: list[dict], today: date) -> list[dict]:
    """Each holding (now or traded), and how much your figures change
    without it: XIRR from the trades, the year of today's shares from the
    prices. Positive: it added; negative: it held you back."""
    from .lens import YEAR_DAYS, _closes
    held = _held(h)
    n = [x for x in _closes(nifty_candles) if x[0] >= today - timedelta(days=YEAR_DAYS)]
    days = [d for d, *_ in n]
    series = aligned(h.get("equity") or [], candles, days) if len(days) > 20 else {}
    v0 = [sum(xs) for xs in zip(*series.values())] if series else []
    base_year = (v0[-1] / v0[0] - 1) * 100 if v0 and v0[0] > 0 else None
    base_xirr = ((T.analyse(trades, held, today)["totals"]["all"] or {}).get("xirr")) if trades else None
    names: dict[str, dict] = {}
    for r in (h.get("equity") or []) + (h.get("mf") or []):
        if r.get("isin"):
            names[r["isin"]] = {"symbol": r.get("symbol") or r.get("fund"), "weight": r.get("weight"), "held": True,
                                "kind": "mf" if "fund" in r else "equity", "pnl": r.get("pnl")}
    for t in trades:
        if T.kind_of(t) in ("equity", "mf") and t.get("isin") and t["isin"] not in names:
            names[t["isin"]] = {"symbol": t["symbol"], "weight": None, "held": False, "kind": T.kind_of(t), "pnl": None}
    out = []
    for isin, nm in names.items():
        row = {"isin": isin, **nm, "xirr_without": None, "xirr_change": None, "year_without": None, "year_change": None}
        keep = [t for t in trades if t.get("isin") != isin]
        if trades and base_xirr is not None and len(keep) < len(trades):      # no trades of its own: nothing to take out
            x = ((T.analyse(keep, held, today)["totals"]["all"] or {}).get("xirr")) if keep else None
            row["xirr_without"] = x
            row["xirr_change"] = _r(base_xirr - x) if x is not None else None
        if isin in series and base_year is not None:
            s = series[isin]
            a, b = v0[0] - s[0], v0[-1] - s[-1]
            if a > 0:
                y = (b / a - 1) * 100
                row["year_without"] = _r(y)
                row["year_change"] = _r(base_year - y)
        out.append(row)
    out.sort(key=lambda r: (r["xirr_change"] if r["xirr_change"] is not None else (r["year_change"] or 0)))
    return out


def _held(h: dict) -> dict:
    from .lens import Lens
    return Lens._held_by_isin(h)
