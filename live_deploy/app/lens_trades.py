"""
LinckLens: your trades, from Zerodha Console tradebook files (equity and
mutual funds), and what they show that Kite's holdings can't: when each
lot was bought, what each sale made, short or long term, money-weighted
returns (XIRR), how much went in month by month, and whether the trades
add up to what Kite says you hold.

Pure: rows in, figures out. Storing the rows is app/lens.py's job
(lens_trades, migration 0044).

A file's columns (Console › Reports › Tradebook, CSV):
  symbol, isin, trade_date, exchange, segment, series, trade_type,
  auction, quantity, price, trade_id, order_id, order_execution_time

Lots are matched first in, first out, by ISIN (a symbol can be renamed;
its ISIN stays), the way the tax rules count them. A tradebook has no
bonuses, splits, IPO allotments or transfers in or out: where those
happened the trades won't add up to the holding, and the comparison with
Kite's holdings says so rather than guessing.
"""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

COLUMNS = ["symbol", "isin", "trade_date", "exchange", "segment", "series", "trade_type", "auction",
           "quantity", "price", "trade_id", "order_id", "order_execution_time"]
REQUIRED = ["symbol", "trade_date", "trade_type", "quantity", "price", "trade_id"]
LONG_TERM_DAYS = 365          # equity shares, equity ETFs and equity funds: over 12 months
# Debt and liquid funds (and debt ETFs) bought from April 2023 are taxed at
# your slab rate however long they're held: no long term for them.
SLAB = {"mf_debt", "mf_liquid", "debt"}
EPS = 1e-6


# ── Reading a file ───────────────────────────────────────────────────

def _day(v) -> Optional[date]:
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


def _when(v) -> Optional[datetime]:
    v = (v or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            pass
    return None


def parse(text: str, source: str = "") -> tuple[list[dict], list[str]]:
    """A tradebook file -> (trades, problems). Every row that can't be
    read is named with its line number; the rest are kept."""
    text = (text or "").lstrip("﻿")
    sample = text[:2048]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    rd = csv.reader(io.StringIO(text), delimiter=delim)
    head, out, bad = None, [], []
    for line_no, row in enumerate(rd, start=1):
        cells = [c.strip() for c in row]
        if not any(cells):
            continue
        if head is None:
            names = [c.lower().replace(" ", "_") for c in cells]
            if "symbol" in names and "trade_id" in names:
                head = names
                continue
            if len(cells) >= len(REQUIRED):      # no header line: Console's column order
                head = COLUMNS
            else:
                continue
        r = {k: (cells[i] if i < len(cells) else "") for i, k in enumerate(head)}
        missing = [k for k in REQUIRED if not r.get(k)]
        if missing:
            bad.append(f"line {line_no}: no {', '.join(missing)}")
            continue
        d, kind = _day(r["trade_date"]), r["trade_type"].lower()
        try:
            qty, px = float(r["quantity"]), float(r["price"])
        except ValueError:
            bad.append(f"line {line_no}: quantity or price isn't a number")
            continue
        if d is None or kind not in ("buy", "sell") or qty <= 0 or px < 0:
            bad.append(f"line {line_no}: " + ("bad trade_date" if d is None else f"trade_type {r['trade_type']!r}" if kind not in ("buy", "sell") else "quantity or price out of range"))
            continue
        out.append({
            "symbol": r["symbol"], "isin": r.get("isin") or None, "trade_date": d,
            "exchange": (r.get("exchange") or "").upper(), "segment": (r.get("segment") or "").upper(),
            "series": r.get("series") or None, "trade_type": kind,
            "auction": (r.get("auction") or "").strip().lower() in ("true", "1", "yes"),
            "quantity": qty, "price": px, "trade_id": r["trade_id"], "order_id": r.get("order_id") or None,
            "executed_at": _when(r.get("order_execution_time")) or datetime.combine(d, datetime.min.time()),
            "source": source or None,
        })
    if head is None and not out:
        bad.append("no tradebook rows found: expected Console's columns (symbol, isin, trade_date, … trade_id …)")
    return out, bad


def kind_of(t: dict) -> str:
    """equity, mf, or other (F&O, currency, commodity: kept, not analysed)."""
    seg = (t.get("segment") or "").upper()
    if seg == "MF":
        return "mf"
    if seg in ("EQ", "BE", "") or (t.get("series") or "").upper() in ("EQ", "BE", "BZ", "SM", "ST", "GB"):
        return "equity" if seg not in ("FO", "CDS", "COM", "MCX") else "other"
    return "other"


def key_of(t: dict) -> str:
    return t.get("isin") or f"{t.get('exchange', '')}:{t['symbol']}"


# ── Lots, first in first out ─────────────────────────────────────────

def lots(trades: list[dict], assets: Optional[dict] = None) -> dict[str, dict]:
    """{key: {symbol, isin, kind, open: [lot], closed: [match], buys, sells,
    oversold}}. An open lot: {day, qty, price}. A closed match: {buy_day,
    sell_day, qty, buy, sell, pnl, days, term}. assets: {isin: what Kite's
    lists say it is (app/lens.py's asset keys)}, for the term. A sale
    beyond what the trades bought (a bonus, an allotment, a transfer in) is counted in
    `oversold`, with no cost to match it to."""
    by: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by[key_of(t)].append(t)
    out = {}
    for k, ts in by.items():
        ts = sorted(ts, key=lambda t: (t["trade_date"], t.get("executed_at") or datetime.min, 0 if t["trade_type"] == "buy" else 1))
        kind = kind_of(ts[-1])
        asset = (assets or {}).get(ts[-1].get("isin"))
        open_: list[dict] = []
        closed, oversold = [], 0.0
        buys = sells = 0.0
        for t in ts:
            q, px, d = float(t["quantity"]), float(t["price"]), t["trade_date"]
            if t["trade_type"] == "buy":
                open_.append({"day": d, "qty": q, "price": px})
                buys += q * px
                continue
            sells += q * px
            while q > EPS and open_:
                lot = open_[0]
                take = min(q, lot["qty"])
                days = (d - lot["day"]).days
                closed.append({"buy_day": lot["day"], "sell_day": d, "qty": take, "buy": lot["price"], "sell": px,
                               "pnl": take * (px - lot["price"]), "days": days, "term": term(days, kind, asset, lot["day"])})
                lot["qty"] -= take
                q -= take
                if lot["qty"] <= EPS:
                    open_.pop(0)
            if q > EPS:
                oversold += q
        last = ts[-1]
        out[k] = {"key": k, "symbol": last["symbol"], "isin": last.get("isin"), "kind": kind, "asset": asset,
                  "exchange": last.get("exchange"), "open": open_, "closed": closed, "trades": len(ts),
                  "first_day": ts[0]["trade_date"], "last_day": ts[-1]["trade_date"],
                  "bought": buys, "sold": sells, "oversold": oversold}
    return out


def term(days: int, kind: str, asset: Optional[str] = None, bought: Optional[date] = None) -> str:
    """long or short, by the 12-month rule for listed shares, ETFs and
    equity funds; slab for a debt or liquid fund (or debt ETF) bought from
    April 2023, which has no long term."""
    if asset in SLAB and (bought is None or bought >= date(2023, 4, 1)):
        return "slab"
    return "long" if days > LONG_TERM_DAYS else "short"


def fy(d: date) -> str:
    """India's financial year: April to March. 2025-26 for 10 Jan 2026."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


# ── Money-weighted return ────────────────────────────────────────────

def xirr(flows: list[tuple[date, float]]) -> Optional[float]:
    """The yearly rate that makes these cash flows (paid in negative,
    taken out and held now positive) worth nothing today. None when it
    can't be had: no money in and out, or less than a week of history."""
    flows = [(d, a) for d, a in flows if abs(a) > EPS]
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    d0 = min(d for d, _ in flows)
    if (max(d for d, _ in flows) - d0).days < 7:
        return None
    ys = [((d - d0).days / 365.0, a) for d, a in flows]

    def npv(r):
        return sum(a / (1 + r) ** y for y, a in ys)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f = npv(mid)
        if abs(f) < 1e-7:
            break
        if f_lo * f < 0:
            hi, f_hi = mid, f
        else:
            lo, f_lo = mid, f
    r = (lo + hi) / 2
    return None if math.isnan(r) else r * 100


# ── The whole picture ────────────────────────────────────────────────

def _r(v, n=2):
    return None if v is None else round(v, n)


def analyse(trades: list[dict], holdings: Optional[dict] = None, today: Optional[date] = None,
            prices: Optional[dict] = None) -> dict:
    """Everything the trades say, against what's held now.

    holdings: {isin: {qty, value, ltp, symbol}} from Kite (shares and
    funds) for the value now and the comparison; prices: {isin: price} for
    anything traded and still open that Kite doesn't list."""
    today = today or date.today()
    holdings, prices = holdings or {}, prices or {}
    assets = {i: h.get("asset") for i, h in holdings.items() if h.get("asset")}
    L = lots([t for t in trades if kind_of(t) in ("equity", "mf")], assets)
    other = [t for t in trades if kind_of(t) == "other"]

    per, realized, upcoming = [], [], []
    flows_by: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for t in trades:
        if kind_of(t) not in ("equity", "mf"):
            continue
        amt = float(t["quantity"]) * float(t["price"])
        flows_by[key_of(t)].append((t["trade_date"], -amt if t["trade_type"] == "buy" else amt))

    for k, x in L.items():
        held = holdings.get(x["isin"]) if x["isin"] else None
        open_qty = sum(l["qty"] for l in x["open"])
        cost = sum(l["qty"] * l["price"] for l in x["open"])
        px = (held or {}).get("ltp") or prices.get(x["isin"])
        value = open_qty * px if px is not None else None
        real = sum(c["pnl"] for c in x["closed"])
        flows = list(flows_by[k])
        if value:
            flows.append((today, value))
        # what each open lot is: its age, and when it turns long term
        lots_now = []
        for l in x["open"]:
            age = (today - l["day"]).days
            lt_on = l["day"] + timedelta(days=LONG_TERM_DAYS + 1)
            gain = (px - l["price"]) * l["qty"] if px is not None else None
            tm = term(age, x["kind"], x["asset"], l["day"])
            lots_now.append({"day": l["day"].isoformat(), "qty": _r(l["qty"], 4), "price": _r(l["price"], 4), "days": age,
                             "term": tm, "long_term_on": lt_on.isoformat() if tm != "slab" else None, "gain": _r(gain)})
            if tm == "short" and (lt_on - today).days <= 60 and gain is not None and gain > 0:
                upcoming.append({"symbol": x["symbol"], "isin": x["isin"], "kind": x["kind"], "qty": _r(l["qty"], 4),
                                 "bought": l["day"].isoformat(), "long_term_on": lt_on.isoformat(),
                                 "in_days": (lt_on - today).days, "gain": _r(gain)})
        for c in x["closed"]:
            realized.append({"symbol": x["symbol"], "isin": x["isin"], "kind": x["kind"], "fy": fy(c["sell_day"]),
                             "sell_day": c["sell_day"].isoformat(), "buy_day": c["buy_day"].isoformat(),
                             "qty": _r(c["qty"], 4), "buy": _r(c["buy"], 4), "sell": _r(c["sell"], 4),
                             "pnl": _r(c["pnl"]), "days": c["days"], "term": c["term"]})
        held_qty = (held or {}).get("qty")
        # a yearly rate from a few months says little (+30% in two months
        # is +380% "a year"): under a year, the return over the time held
        span = ((today if open_qty > EPS else x["last_day"]) - x["first_day"]).days
        ret = ((value or 0.0) + x["sold"] - x["bought"]) / x["bought"] * 100 if x["bought"] and (value is not None or open_qty <= EPS) else None
        per.append({
            "key": k, "symbol": x["symbol"], "isin": x["isin"], "kind": x["kind"], "exchange": x["exchange"],
            "trades": x["trades"], "first_day": x["first_day"].isoformat(), "last_day": x["last_day"].isoformat(),
            "bought": _r(x["bought"]), "sold": _r(x["sold"]),
            "open_qty": _r(open_qty, 4), "open_cost": _r(cost), "avg": _r(cost / open_qty, 4) if open_qty > EPS else None,
            "price": _r(px, 4), "value": _r(value), "unrealized": _r(value - cost) if value is not None else None,
            "realized": _r(real), "xirr": _r(xirr(flows)) if span >= LONG_TERM_DAYS else None,
            "return_pct": _r(ret), "days": span,
            "held_qty": held_qty, "oversold": _r(x["oversold"], 4),
            "lots": lots_now,
            "avg_days_open": _r(sum(l["qty"] * (today - l["day"]).days for l in x["open"]) / open_qty, 0) if open_qty > EPS else None,
        })

    # totals and money-weighted returns, by kind and overall
    def flows_of(kinds):
        out = []
        for p in per:
            if p["kind"] in kinds:
                out += flows_by[p["key"]]
                if p["value"]:
                    out.append((today, p["value"]))
        return out

    def total(kinds):
        ps = [p for p in per if p["kind"] in kinds]
        if not ps:
            return None
        value = sum(p["value"] or 0 for p in ps if p["open_qty"] > EPS)
        return {"bought": _r(sum(p["bought"] for p in ps)), "sold": _r(sum(p["sold"] for p in ps)),
                "open_cost": _r(sum(p["open_cost"] for p in ps)), "value": _r(value),
                "unpriced": [p["symbol"] for p in ps if p["open_qty"] > EPS and p["price"] is None],
                "realized": _r(sum(p["realized"] for p in ps)),
                "unrealized": _r(sum(p["unrealized"] or 0 for p in ps)),
                "xirr": _r(xirr(flows_of(kinds))), "count": len(ps),
                "open": sum(1 for p in ps if p["open_qty"] > EPS)}

    # realized gains by financial year, short and long term
    years: dict[str, dict] = {}
    for r in realized:
        y = years.setdefault(r["fy"], {"fy": r["fy"], "short": 0.0, "long": 0.0, "slab": 0.0,
                                       "sales": 0, "gains": 0.0, "losses": 0.0})
        y[r["term"]] += r["pnl"]
        y["gains" if r["pnl"] >= 0 else "losses"] += r["pnl"]
        y["sales"] += 1
    by_year = [{k: (_r(v) if isinstance(v, float) else v) for k, v in y.items()} for _, y in sorted(years.items())]

    # money in and out, month by month, and what was in at each month's end
    months: dict[str, dict] = {}
    for t in sorted((t for t in trades if kind_of(t) in ("equity", "mf")), key=lambda t: t["trade_date"]):
        m = months.setdefault(t["trade_date"].strftime("%Y-%m"), {"month": t["trade_date"].strftime("%Y-%m"), "bought": 0.0, "sold": 0.0,
                                                                   "equity": 0.0, "mf": 0.0, "trades": 0})
        amt = float(t["quantity"]) * float(t["price"])
        m["bought" if t["trade_type"] == "buy" else "sold"] += amt
        m[kind_of(t)] += amt if t["trade_type"] == "buy" else -amt
        m["trades"] += 1
    run = 0.0
    flow = []
    for k in sorted(months):
        m = months[k]
        run += m["bought"] - m["sold"]
        flow.append({**{a: (_r(b) if isinstance(b, float) else b) for a, b in m.items()}, "net_in": _r(run)})

    # closed trades: how they went
    sells: dict[tuple, dict] = {}
    for r in realized:
        s = sells.setdefault((r["isin"] or r["symbol"], r["sell_day"]), {"symbol": r["symbol"], "kind": r["kind"], "day": r["sell_day"],
                                                                      "pnl": 0.0, "cost": 0.0, "days": 0.0, "qty": 0.0})
        s["pnl"] += r["pnl"]
        s["cost"] += r["qty"] * r["buy"]
        s["days"] += r["days"] * r["qty"]
        s["qty"] += r["qty"]
    sales = [{"symbol": s["symbol"], "kind": s["kind"], "day": s["day"], "pnl": _r(s["pnl"]),
              "pct": _r(s["pnl"] / s["cost"] * 100) if s["cost"] else None, "days": round(s["days"] / s["qty"]) if s["qty"] else None}
             for s in sells.values()]
    wins = [s for s in sales if s["pnl"] > 0]
    losses = [s for s in sales if s["pnl"] < 0]
    behaviour = {
        "sales": len(sales), "wins": len(wins), "losses": len(losses),
        "win_rate": _r(len(wins) / len(sales) * 100, 1) if sales else None,
        "avg_win": _r(sum(s["pnl"] for s in wins) / len(wins)) if wins else None,
        "avg_loss": _r(sum(s["pnl"] for s in losses) / len(losses)) if losses else None,
        "avg_days_winners": _r(sum(s["days"] for s in wins) / len(wins), 0) if wins else None,
        "avg_days_losers": _r(sum(s["days"] for s in losses) / len(losses), 0) if losses else None,
        "best": sorted(sales, key=lambda s: -s["pnl"])[:5], "worst": sorted(sales, key=lambda s: s["pnl"])[:5],
        "buys": sum(1 for t in trades if kind_of(t) in ("equity", "mf") and t["trade_type"] == "buy"),
        "sells": sum(1 for t in trades if kind_of(t) in ("equity", "mf") and t["trade_type"] == "sell"),
    }

    # do the trades add up to what Kite holds?
    recon = []
    seen = set()
    for p in per:
        if not p["isin"]:
            continue
        seen.add(p["isin"])
        h = holdings.get(p["isin"])
        hq = (h or {}).get("qty") or 0.0
        tol = 0.01 if p["kind"] == "mf" else 0.001          # fund units: three decimals a purchase
        if abs(hq - p["open_qty"]) > tol or p["oversold"] > EPS:
            recon.append({"symbol": p["symbol"], "isin": p["isin"], "kind": p["kind"], "trades_qty": p["open_qty"],
                          "held_qty": hq, "oversold": p["oversold"],
                          "why": ("sold more than the trades bought: a bonus, a split, an IPO allotment or a transfer in"
                                  if p["oversold"] > EPS else
                                  "held but more than the trades bought: an older file missing, a bonus or split, an allotment"
                                  if hq > p["open_qty"] else
                                  "the trades say more is held than Kite does: a later sale missing, a transfer out, or a merger")})
    for isin, h in holdings.items():
        if isin and isin not in seen and (h.get("qty") or 0) > 0:
            recon.append({"symbol": h.get("symbol"), "isin": isin, "kind": h.get("kind"), "trades_qty": 0.0,
                          "held_qty": h["qty"], "oversold": 0.0, "why": "held, with no trades in the files imported"})

    first = min((t["trade_date"] for t in trades), default=None)
    last = max((t["trade_date"] for t in trades), default=None)
    return {
        "as_of": today.isoformat(), "first_day": first.isoformat() if first else None, "last_day": last.isoformat() if last else None,
        "count": len(trades), "other": {"count": len(other), "segments": sorted({t.get("segment") or "?" for t in other})},
        "totals": {"all": total(("equity", "mf")), "equity": total(("equity",)), "mf": total(("mf",))},
        "holdings": sorted(per, key=lambda p: (-(p["value"] or 0), p["symbol"])),
        "realized": sorted(realized, key=lambda r: r["sell_day"], reverse=True),
        "by_year": by_year, "months": flow, "behaviour": behaviour,
        "long_term_soon": sorted(upcoming, key=lambda u: u["in_days"]),
        "reconcile": recon,
    }


# ── Your shares day by day, against the same money in Nifty ──────────

def _close_on(series: list[tuple[date, float]], d: date, i: int) -> tuple[Optional[float], int]:
    """The last close on or before d, walking forward from index i."""
    while i + 1 < len(series) and series[i + 1][0] <= d:
        i += 1
    return (series[i][1] if series and series[i][0] <= d else None), i


def value_curve(trades: list[dict], prices: dict[str, list[tuple[date, float]]], nifty: list[tuple[date, float]],
                today: Optional[date] = None) -> Optional[dict]:
    """Shares only (funds have no NAV history from Kite): what you held
    each day (from the trades) at that day's close, the money put in so
    far, and the same buys and sells made in NIFTY 50 instead, on the
    same days, for the same amounts.

    prices: {key_of(trade): [(day, close)]}. A share with no prices is
    left out, its money too, and named."""
    eq = sorted((t for t in trades if kind_of(t) == "equity"), key=lambda t: (t["trade_date"], t.get("executed_at") or datetime.min))
    if not eq or len(nifty) < 2:
        return None
    left_out = sorted({t["symbol"] for t in eq if not prices.get(key_of(t))})
    eq = [t for t in eq if prices.get(key_of(t))]
    if not eq:
        return {"days": [], "left_out": left_out}
    start = eq[0]["trade_date"]
    days = [d for d, _ in nifty if d >= start and (today is None or d <= today)]
    if len(days) < 2:
        return None
    qty: dict[str, float] = defaultdict(float)
    idx: dict[str, int] = defaultdict(int)
    ni = 0
    units_n = invested = 0.0
    out_v, out_in, out_n = [], [], []
    ti = 0
    for d in days:
        n_close, ni = _close_on(nifty, d, ni)
        while ti < len(eq) and eq[ti]["trade_date"] <= d:
            t = eq[ti]
            amt = float(t["quantity"]) * float(t["price"])
            sign = 1 if t["trade_type"] == "buy" else -1
            qty[key_of(t)] += sign * float(t["quantity"])
            invested += sign * amt
            if n_close:
                units_n += sign * amt / n_close
            ti += 1
        v = 0.0
        for k, q in qty.items():
            if abs(q) < EPS:
                continue
            c, idx[k] = _close_on(prices[k], d, idx[k])
            if c is None:      # not listed yet that day: at what it cost
                c = next((float(t["price"]) for t in eq if key_of(t) == k), 0.0)
            v += q * c
        out_v.append(round(v, 2))
        out_in.append(round(invested, 2))
        out_n.append(round(units_n * (n_close or 0), 2))
    return {"days": [d.isoformat() for d in days], "value": out_v, "invested": out_in, "nifty": out_n,
            "left_out": left_out, "now": {"value": out_v[-1], "invested": out_in[-1], "nifty": out_n[-1],
                                          "vs_nifty": round(out_v[-1] - out_n[-1], 2)}}
