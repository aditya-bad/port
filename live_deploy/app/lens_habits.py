"""
LinckLens › Habits: what your tradebook says about how you trade, in
rupees and naming the stocks, not generic advice.

Pure: trades (app/lens_trades.parse's rows) and, for the findings that
need them, daily prices in; figures out. Three kinds:

From the trades alone
  costs      what the trades cost in charges, estimated at Zerodha's
             published rates (RATES), and the tax on what was booked, by
             financial year
  holding    how sales went by how long the shares were held
  averaging  holdings you bought more of below what you'd paid, against
             the ones you didn't
  round_trips  a share sold and bought back within 30 days: higher or lower
  intraday   bought and sold the same day (taxed as business income)
  spread     how many holdings, how big the largest, how much you traded
             against what you held, year by year
  funds      your fund buying: regular monthly buys (SIPs), months missed,
             lump sums
  orders     when in the day you trade, and orders that filled in parts

With daily prices (every share traded, sold ones too)
  after_sale  what each sold share did over the next 1, 3 and 12 months:
              sold too early, or got out in time
  buy_timing  bought after a run-up or after a fall, near the year's high
              or low, and how those buys went
  vs_nifty    each closed trade against Nifty 50 over the same days
  in_day      where in the day's range your buys and sells were

Kite's daily prices are adjusted for splits and bonuses; a tradebook's
aren't. So every move after a trade is measured inside Kite's series,
from the close on the trade's day, never from the trade's own price; and
where in a day's range a trade was is only read where its price lies
inside that day's range.

And `summary`: the strongest findings, ranked by the rupees involved.
A finding is made only when there's enough behind it (MIN_*).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median
from typing import Optional

from . import lens_trades as LT

EPS = 1e-6

# Zerodha's published charges (zerodha.com/charges), late 2024 onwards.
# Estimates: a tradebook has no contract notes. Update here if they change.
RATES = {
    "delivery": {"stt": 0.001, "stt_buy": True, "stamp_buy": 0.00015, "brokerage_pct": 0.0, "brokerage_cap": 0.0},
    "intraday": {"stt": 0.00025, "stt_buy": False, "stamp_buy": 0.00003, "brokerage_pct": 0.0003, "brokerage_cap": 20.0},
}
EXCHANGE = {"NSE": 0.0000297, "BSE": 0.0000375}
SEBI = 0.000001
GST = 0.18
DP_PER_SALE = 15.34          # per share sold out of the demat, per day (₹13 + GST)
MF_STAMP = 0.00005           # fund purchases: 0.005% stamp duty, nothing else
# Listed equity, by sale date: short term, long term, and the long-term rate's start
TAX = [(date(2024, 7, 23), 0.20, 0.125), (date(1900, 1, 1), 0.15, 0.10)]

MIN_SALES = 5                # a holding-period bucket or a group of sales
MIN_GROUP = 3                # stocks in a comparison group
ROUND_TRIP_DAYS = 30
RUN = 0.10                   # a run-up or fall: 10% over the 20 market days before
LOOKBACK = 20


def _r(v, n=2):
    return None if v is None else round(v, n)


def _amt(t: dict) -> float:
    return float(t["quantity"]) * float(t["price"])


def _rates_on(d: date) -> tuple[float, float]:
    return next((st, lt) for start, st, lt in TAX if d >= start)


def _eq(trades: list[dict]) -> list[dict]:
    return sorted((t for t in trades if LT.kind_of(t) == "equity"),
                  key=lambda t: (t["trade_date"], t.get("executed_at") or datetime.min))


# ── costs and tax ─────────────────────────────────────────────────────

def _day_sides(trades: list[dict]) -> dict[tuple, list[float]]:
    """{(key, day): [qty bought, qty sold]} for equity."""
    by = defaultdict(lambda: [0.0, 0.0])
    for t in _eq(trades):
        by[(LT.key_of(t), t["trade_date"])][0 if t["trade_type"] == "buy" else 1] += float(t["quantity"])
    return by


def costs(trades: list[dict], realized: list[dict]) -> dict:
    """Charges by financial year (estimated), and tax on what was booked."""
    sides = _day_sides(trades)
    years: dict[str, dict] = {}

    def y_of(d):
        fy = LT.fy(d)
        return years.setdefault(fy, {"fy": fy, "turnover": 0.0, "stt": 0.0, "stamp": 0.0, "exchange": 0.0, "sebi": 0.0,
                                     "gst": 0.0, "brokerage": 0.0, "dp": 0.0, "charges": 0.0, "orders": 0})
    dp_days = set()
    for t in sorted(trades, key=lambda t: t["trade_date"]):
        kind = LT.kind_of(t)
        if kind == "other":
            continue
        y = y_of(t["trade_date"])
        amt = _amt(t)
        y["turnover"] += amt
        if kind == "mf":
            if t["trade_type"] == "buy":
                y["stamp"] += amt * MF_STAMP
            continue
        k = (LT.key_of(t), t["trade_date"])
        bq, sq = sides[k]
        same_day = min(bq, sq)        # bought and sold the same day: charged as intraday
        frac_i = same_day / (bq if t["trade_type"] == "buy" else sq)
        for part, frac in (("intraday", frac_i), ("delivery", 1 - frac_i)):
            if frac <= EPS:
                continue
            R, v = RATES[part], amt * frac
            br = min(v * R["brokerage_pct"], R["brokerage_cap"]) if R["brokerage_pct"] else 0.0
            ex = v * EXCHANGE.get((t.get("exchange") or "NSE").upper(), EXCHANGE["NSE"])
            sebi = v * SEBI
            y["brokerage"] += br
            y["stt"] += v * R["stt"] if (t["trade_type"] == "sell" or R["stt_buy"]) else 0.0
            y["stamp"] += v * R["stamp_buy"] if t["trade_type"] == "buy" else 0.0
            y["exchange"] += ex
            y["sebi"] += sebi
            y["gst"] += GST * (br + ex + sebi)
        # shares leaving the demat: once per share per day
        if t["trade_type"] == "sell" and sq > same_day + EPS and k not in dp_days:
            dp_days.add(k)
            y["dp"] += DP_PER_SALE
    orders = defaultdict(set)
    for t in trades:
        if LT.kind_of(t) != "other":
            orders[LT.fy(t["trade_date"])].add(t.get("order_id") or t["trade_id"])
    for fy, y in years.items():
        y["orders"] = len(orders[fy])
        y["charges"] = sum(y[k] for k in ("stt", "stamp", "exchange", "sebi", "gst", "brokerage", "dp"))

    # tax on what was booked: listed equity only (funds at slab, and
    # same-day trades as business income, aren't estimated)
    from .lens_inspect import exemption
    tax: dict[str, dict] = {}
    for r in realized:
        if r["kind"] != "equity" or r["term"] == "slab" or r["days"] == 0:
            continue
        fy = r["fy"]
        x = tax.setdefault(fy, {"st_gain": 0.0, "st_loss": 0.0, "lt_gain": 0.0, "lt_loss": 0.0, "st_tax_base": [], "lt_tax_base": []})
        side = "st" if r["term"] == "short" else "lt"
        if r["pnl"] >= 0:
            x[f"{side}_gain"] += r["pnl"]
            x[f"{side}_tax_base"].append((r["pnl"], date.fromisoformat(r["sell_day"])))
        else:
            x[f"{side}_loss"] += -r["pnl"]
    out_years = []
    for fy in sorted(set(years) | set(tax)):
        y = years.get(fy) or {"fy": fy, "turnover": 0.0, "charges": 0.0, "orders": 0}
        x = tax.get(fy)
        est = None
        if x:
            # losses set off within the year: short-term losses against
            # short then long gains; long-term losses against long only
            st, lt = x["st_gain"], x["lt_gain"]
            stl, ltl = x["st_loss"], x["lt_loss"]
            use = min(st, stl); st -= use; stl -= use
            use = min(lt, stl); lt -= use; stl -= use
            use = min(lt, ltl); lt -= use; ltl -= use
            lt_taxable = max(0.0, lt - exemption(fy))

            def rate_of(base, i):
                tot = sum(p for p, _ in base)
                return sum(p * _rates_on(d)[i] for p, d in base) / tot if tot else _rates_on(date(int(fy[:4]) + 1, 3, 31))[i]
            st_tax = st * rate_of(x["st_tax_base"], 0)
            lt_tax = lt_taxable * rate_of(x["lt_tax_base"], 1)
            est = {"short_net": _r(st), "long_net": _r(lt), "exempt_used": _r(min(lt, exemption(fy))),
                   "short_tax": _r(st_tax), "long_tax": _r(lt_tax), "tax": _r(st_tax + lt_tax),
                   "losses_left": _r(stl + ltl)}
        booked = sum(r["pnl"] for r in realized if r["fy"] == fy)
        out_years.append({**{k: (_r(v) if isinstance(v, float) else v) for k, v in y.items()}, "booked": _r(booked), "tax": est,
                          "charges_pct_of_booked": _r(y["charges"] / booked * 100, 1) if booked > 0 else None})
    total_charges = sum(y["charges"] or 0 for y in out_years)
    total_tax = sum((y["tax"] or {}).get("tax") or 0 for y in out_years)
    booked = sum(r["pnl"] for r in realized)
    return {"years": out_years, "charges": _r(total_charges), "tax": _r(total_tax), "booked": _r(booked),
            "kept": _r(booked - total_charges - total_tax),
            "rates": {"stt_delivery_pct": 0.1, "stamp_buy_pct": 0.015, "dp_per_sale": DP_PER_SALE, "nse_exchange_pct": 0.00297}}


# ── how long you held, and how it went ──────────────────────────────

BUCKETS = [("Under a month", 0, 30), ("1–6 months", 30, 183), ("6–12 months", 183, 366), ("Over a year", 366, 10 ** 6)]


def holding(realized: list[dict]) -> dict:
    eq = [r for r in realized if r["kind"] == "equity"]
    rows = []
    for label, lo, hi in BUCKETS:
        rs = [r for r in eq if lo <= r["days"] < hi]
        cost = sum(r["qty"] * r["buy"] for r in rs)
        pnl = sum(r["pnl"] for r in rs)
        rows.append({"label": label, "sales": len(rs), "pnl": _r(pnl), "cost": _r(cost),
                     "return_pct": _r(pnl / cost * 100, 1) if cost else None,
                     "win_rate": _r(sum(1 for r in rs if r["pnl"] > 0) / len(rs) * 100, 0) if rs else None,
                     "enough": len(rs) >= MIN_SALES})
    ok = [b for b in rows if b["enough"] and b["return_pct"] is not None]
    best = max(ok, key=lambda b: b["return_pct"]) if len(ok) >= 2 else None
    worst = min(ok, key=lambda b: b["return_pct"]) if len(ok) >= 2 else None
    return {"buckets": rows, "best": best and best["label"], "worst": worst and worst["label"]}


# ── averaging down ───────────────────────────────────────────────────

def averaging(trades: list[dict], per: list[dict], drop: float = 0.05) -> dict:
    """Holdings you bought more of at least `drop` below your average cost
    at the time, against those you never did, by how each finished (booked
    plus on paper, over what it cost)."""
    by = defaultdict(list)
    for t in _eq(trades):
        by[LT.key_of(t)].append(t)
    outcome = {p["key"]: p for p in per if p["kind"] == "equity"}
    added, never = [], []
    for k, ts in by.items():
        p = outcome.get(k)
        if not p or (p["open_qty"] > EPS and p["value"] is None):
            continue
        qty = cost = 0.0
        adds = []
        for t in ts:
            q, px = float(t["quantity"]), float(t["price"])
            if t["trade_type"] == "buy":
                if qty > EPS and px <= cost / qty * (1 - drop):
                    adds.append({"day": t["trade_date"].isoformat(), "price": _r(px), "avg": _r(cost / qty), "below_pct": _r((1 - px / (cost / qty)) * 100, 1)})
                qty += q
                cost += q * px
            else:
                take = min(q, qty)
                if qty > EPS:
                    cost -= cost / qty * take
                qty -= take
        result = (p["realized"] or 0) + (p["unrealized"] or 0)
        row = {"symbol": p["symbol"], "result": _r(result), "bought": p["bought"], "pct": _r(result / p["bought"] * 100, 1) if p["bought"] else None,
               "adds": adds, "open": p["open_qty"] > EPS}
        (added if adds else never).append(row)

    def summ(rows):
        if not rows:
            return None
        res = sum(r["result"] for r in rows)
        cost = sum(r["bought"] for r in rows)
        return {"count": len(rows), "result": _r(res), "winners": sum(1 for r in rows if r["result"] > 0),
                "return_pct": _r(res / cost * 100, 1) if cost else None,
                "median_pct": _r(median([r["pct"] for r in rows if r["pct"] is not None]), 1) if any(r["pct"] is not None for r in rows) else None}
    return {"added": sorted(added, key=lambda r: r["result"]), "never": len(never),
            "added_summary": summ(added), "never_summary": summ(never),
            "enough": len(added) >= MIN_GROUP and len(never) >= MIN_GROUP}


# ── round trips ──────────────────────────────────────────────────────

def round_trips(trades: list[dict]) -> dict:
    """A sale followed within ROUND_TRIP_DAYS by a buy of the same share:
    what the round trip did, (sale price − buy-back price) × the shares
    bought back. Positive: bought back cheaper."""
    by = defaultdict(list)
    for t in _eq(trades):
        by[LT.key_of(t)].append(t)
    out = []
    for k, ts in by.items():
        for i, s in enumerate(ts):
            if s["trade_type"] != "sell":
                continue
            left = float(s["quantity"])
            for b in ts[i + 1:]:
                gap = (b["trade_date"] - s["trade_date"]).days
                if gap > ROUND_TRIP_DAYS or left <= EPS:
                    break
                if b["trade_type"] != "buy" or gap == 0:
                    continue
                q = min(left, float(b["quantity"]))
                left -= q
                out.append({"symbol": s["symbol"], "sold": s["trade_date"].isoformat(), "sold_at": _r(float(s["price"])),
                            "bought_back": b["trade_date"].isoformat(), "back_at": _r(float(b["price"])), "qty": _r(q, 4),
                            "days": gap, "effect": _r((float(s["price"]) - float(b["price"])) * q)})
    eff = sum(r["effect"] for r in out)
    return {"trips": sorted(out, key=lambda r: r["effect"]), "effect": _r(eff),
            "cheaper": sum(1 for r in out if r["effect"] > 0), "dearer": sum(1 for r in out if r["effect"] < 0)}


# ── same-day trades ──────────────────────────────────────────────────

def intraday(trades: list[dict]) -> dict:
    """Shares bought and sold the same day: P&L on the matched quantity at
    that day's average buy and sell prices."""
    by = defaultdict(lambda: {"bq": 0.0, "bv": 0.0, "sq": 0.0, "sv": 0.0, "symbol": ""})
    for t in _eq(trades):
        x = by[(LT.key_of(t), t["trade_date"])]
        x["symbol"] = t["symbol"]
        side = "b" if t["trade_type"] == "buy" else "s"
        x[side + "q"] += float(t["quantity"])
        x[side + "v"] += _amt(t)
    days = []
    for (k, d), x in by.items():
        q = min(x["bq"], x["sq"])
        if q <= EPS:
            continue
        pnl = q * (x["sv"] / x["sq"] - x["bv"] / x["bq"])
        days.append({"symbol": x["symbol"], "day": d.isoformat(), "qty": _r(q, 4), "pnl": _r(pnl)})
    return {"count": len(days), "pnl": _r(sum(d["pnl"] for d in days)),
            "wins": sum(1 for d in days if d["pnl"] > 0), "days": sorted(days, key=lambda d: d["day"], reverse=True)}


# ── how spread out, and how much traded ──────────────────────────────

def spread(trades: list[dict]) -> dict:
    """At each month's end, from the trades (at cost): holdings, the
    largest one's share; and each financial year's sales against the
    average held (turnover)."""
    eq = _eq(trades)
    if not eq:
        return {"months": [], "years": []}
    qty, cost = defaultdict(float), defaultdict(float)
    sym = {}
    months, held_by_fy = [], defaultdict(list)
    sold_by_fy = defaultdict(float)
    i = 0
    d = date(eq[0]["trade_date"].year, eq[0]["trade_date"].month, 1)
    end = eq[-1]["trade_date"]
    while d <= end + timedelta(days=31):
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        while i < len(eq) and eq[i]["trade_date"] < nxt:
            t = eq[i]
            k, q, px = LT.key_of(t), float(t["quantity"]), float(t["price"])
            sym[k] = t["symbol"]
            if t["trade_type"] == "buy":
                qty[k] += q
                cost[k] += q * px
            else:
                take = min(q, qty[k])
                if qty[k] > EPS:
                    cost[k] -= cost[k] / qty[k] * take
                qty[k] -= take
                sold_by_fy[LT.fy(t["trade_date"])] += q * px
            i += 1
        open_ = {k: c for k, c in cost.items() if qty[k] > EPS and c > EPS}
        tot = sum(open_.values())
        top = max(open_, key=open_.get) if open_ else None
        months.append({"month": d.strftime("%Y-%m"), "holdings": len(open_), "at_cost": _r(tot),
                       "largest": sym.get(top), "largest_pct": _r(open_[top] / tot * 100, 1) if top else None})
        held_by_fy[LT.fy(nxt - timedelta(days=1))].append(tot)
        d = nxt
        if d > end and i >= len(eq):
            break
    years = []
    for fy in sorted(held_by_fy):
        avg = sum(held_by_fy[fy]) / len(held_by_fy[fy])
        years.append({"fy": fy, "avg_held": _r(avg), "sold": _r(sold_by_fy[fy]),
                      "turnover": _r(sold_by_fy[fy] / avg, 2) if avg > EPS else None})
    spread_out = [m for m in months if m["holdings"] >= 2]
    peak = max(spread_out, key=lambda m: m["largest_pct"] or 0) if spread_out else None
    return {"months": months, "years": years, "peak": peak}


# ── funds ────────────────────────────────────────────────────────────

def funds(trades: list[dict], today: date) -> dict:
    """Per fund: months with a purchase, a regular monthly buy (a SIP: the
    same amount within 10% in at least 3 months), the months it skipped,
    and lump sums (over 3× the usual amount)."""
    by = defaultdict(list)
    for t in trades:
        if LT.kind_of(t) == "mf" and t["trade_type"] == "buy":
            by[LT.key_of(t)].append(t)
    out = []
    for k, ts in by.items():
        per_month = defaultdict(float)
        for t in ts:
            per_month[t["trade_date"].strftime("%Y-%m")] += _amt(t)
        amounts = list(per_month.values())
        usual = median(amounts)
        regular = [m for m, a in per_month.items() if abs(a - usual) <= usual * 0.10]
        sip = len(regular) >= 3
        missed = []
        if sip:
            first, last = min(regular), max(max(per_month), today.strftime("%Y-%m"))
            y, m = map(int, first.split("-"))
            while f"{y:04d}-{m:02d}" <= last:
                key = f"{y:04d}-{m:02d}"
                if key not in per_month and key != today.strftime("%Y-%m"):
                    missed.append(key)
                m += 1
                if m > 12:
                    y, m = y + 1, 1
            # stopped: nothing in the last 2 months is "stopped", not "missed"
            stopped = len(missed) >= 2 and missed[-1] == (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m") and missed[-2] == (today.replace(day=1) - timedelta(days=32)).strftime("%Y-%m")
        else:
            stopped = False
        lumps = [{"month": m, "amount": _r(a)} for m, a in sorted(per_month.items()) if a > usual * 3]
        out.append({"fund": ts[-1]["symbol"], "months": len(per_month), "usual": _r(usual), "sip": sip,
                    "missed": missed, "stopped": stopped, "lump_sums": lumps, "total": _r(sum(amounts))})
    sold = [t for t in trades if LT.kind_of(t) == "mf" and t["trade_type"] == "sell"]
    return {"funds": sorted(out, key=lambda f: -f["total"]), "redemptions": len(sold)}


# ── when you trade ───────────────────────────────────────────────────

SLOTS = [("First 15 minutes", (9, 0), (9, 30)), ("Morning", (9, 30), (12, 0)), ("Afternoon", (12, 0), (14, 30)), ("Last hour", (14, 30), (16, 0))]


def orders(trades: list[dict]) -> dict:
    eq = [t for t in _eq(trades) if t.get("executed_at") and (t["executed_at"].hour, t["executed_at"].minute) != (0, 0)]
    slots = []
    for label, a, b in SLOTS:
        ts = [t for t in eq if a <= (t["executed_at"].hour, t["executed_at"].minute) < b]
        slots.append({"label": label, "buys": sum(1 for t in ts if t["trade_type"] == "buy"),
                      "sells": sum(1 for t in ts if t["trade_type"] == "sell"), "value": _r(sum(_amt(t) for t in ts))})
    parts = defaultdict(int)
    for t in _eq(trades):
        if t.get("order_id"):
            parts[t["order_id"]] += 1
    split = [n for n in parts.values() if n > 1]
    return {"timed": len(eq), "slots": slots, "orders": len(parts), "split_orders": len(split),
            "most_parts": max(split) if split else None,
            "open_share": _r((slots[0]["buys"] + slots[0]["sells"]) / len(eq) * 100, 0) if eq else None}


# ── with prices ──────────────────────────────────────────────────────

def _series(c: list[dict]) -> list[tuple]:
    return [(x["date"], float(x["close"]), float(x["high"]), float(x["low"])) for x in c or [] if x.get("close") is not None]


def _at(s: list[tuple], d: date) -> Optional[int]:
    """Index of the last day on or before d."""
    lo, hi = 0, len(s) - 1
    if not s or s[0][0] > d:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if s[mid][0] <= d:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _after(s: list[tuple], d: date, days: int, today: date) -> Optional[float]:
    target = d + timedelta(days=days)
    if target > today:
        return None
    i = _at(s, target)
    return s[i][1] if i is not None and s[i][0] > d else None


def after_sale(realized: list[dict], prices: dict, nifty: list[tuple], today: date, keys: dict) -> dict:
    """Each sale (a share, a day) and what the share did after: the rupees
    you'd have now if you'd kept what you sold, over 1, 3 and 12 months,
    and Nifty's move over the same days for scale."""
    sales: dict[tuple, dict] = {}
    for r in realized:
        if r["kind"] != "equity":
            continue
        k = (r["isin"] or r["symbol"], r["sell_day"])
        s = sales.setdefault(k, {"symbol": r["symbol"], "key": keys.get(r["isin"] or r["symbol"]), "day": r["sell_day"],
                                 "qty": 0.0, "value": 0.0, "pnl": 0.0})
        s["qty"] += r["qty"]
        s["value"] += r["qty"] * r["sell"]
        s["pnl"] += r["pnl"]
    rows = []
    for s in sales.values():
        ser = prices.get(s["key"]) or []
        d = date.fromisoformat(s["day"])
        if not ser:
            continue
        px = s["value"] / s["qty"]
        i0 = _at(ser, d)
        if i0 is None or ser[i0][0] < d - timedelta(days=7):
            continue
        base = ser[i0][1]         # the sale day's close, in the series' own (adjusted) terms
        row = {"symbol": s["symbol"], "day": s["day"], "qty": _r(s["qty"], 4), "price": _r(px), "booked": _r(s["pnl"]), "won": s["pnl"] > 0}
        for label, n in (("m1", 30), ("m3", 91), ("m12", 365)):
            later = _after(ser, d, n, today)
            ni, nl = _at(nifty, d), _after(nifty, d, n, today)
            move = None if later is None else later / base - 1
            row[label] = None if later is None else {"move_pct": _r(move * 100, 1), "missed": _r(move * s["value"]),
                                                     "nifty_pct": _r((nl / nifty[ni][1] - 1) * 100, 1) if ni is not None and nl else None}
        rows.append(row)

    def side(won):
        xs = [r for r in rows if r["won"] == won and r["m3"]]
        if not xs:
            return None
        return {"sales": len(xs), "missed_3m": _r(sum(r["m3"]["missed"] for r in xs)),
                "kept_rising": sum(1 for r in xs if r["m3"]["move_pct"] > 0),
                "median_move_3m": _r(median([r["m3"]["move_pct"] for r in xs]), 1)}
    m12 = [r for r in rows if r["m12"]]
    return {"sales": sorted(rows, key=lambda r: -(r["m3"]["missed"] if r["m3"] else 0)), "winners": side(True), "losers": side(False),
            "missed_12m": _r(sum(r["m12"]["missed"] for r in m12)) if m12 else None, "with_12m": len(m12)}


def buy_timing(trades: list[dict], prices: dict, per: list[dict], today: date) -> dict:
    """Each buy: the 20 market days before it (a run-up, a fall, or
    neither) and where it was in the 52 weeks before (0% the low, 100% the
    high); and how those buys went, to today's price or the day sold."""
    last = {p["key"]: p for p in per}
    buys = []
    for t in _eq(trades):
        if t["trade_type"] != "buy":
            continue
        k = LT.key_of(t)
        s = prices.get(k) or []
        i = _at(s, t["trade_date"] - timedelta(days=1))
        b = _at(s, t["trade_date"])
        if i is None or i < LOOKBACK or b is None or s[b][0] != t["trade_date"]:
            continue
        before = (s[i][1] / s[i - LOOKBACK][1] - 1)
        year = [x for x in s[:i + 1] if x[0] >= t["trade_date"] - timedelta(days=365)]
        hi, lo = max(x[2] for x in year), min(x[3] for x in year)
        bought = s[b][1]          # the buy day's close, in the series' own terms
        pos = min(1.0, max(0.0, (bought - lo) / (hi - lo))) if hi > lo else None
        p = last.get(k) or {}
        end_day = today if p.get("open_qty", 0) > EPS else date.fromisoformat(p["last_day"]) if p.get("last_day") else None
        j = _at(s, end_day) if end_day else None
        if j is None:
            continue
        ret = s[j][1] / bought - 1
        buys.append({"symbol": t["symbol"], "day": t["trade_date"].isoformat(), "price": _r(float(t["price"])),
                     "before_pct": _r(before * 100, 1), "in_range_pct": _r(pos * 100, 0) if pos is not None else None,
                     "after_pct": _r(ret * 100, 1), "after_rs": _r(ret * _amt(t)), "amount": _r(_amt(t)),
                     "kind": "run_up" if before >= RUN else "fall" if before <= -RUN else "flat"})

    def grp(rows):
        if not rows:
            return None
        amt = sum(r["amount"] for r in rows)
        return {"buys": len(rows), "amount": _r(amt), "result": _r(sum(r["after_rs"] for r in rows)),
                "return_pct": _r(sum(r["after_rs"] for r in rows) / amt * 100, 1) if amt else None,
                "enough": len(rows) >= MIN_SALES}
    near_high = [b for b in buys if b["in_range_pct"] is not None and b["in_range_pct"] >= 80]
    near_low = [b for b in buys if b["in_range_pct"] is not None and b["in_range_pct"] <= 20]
    return {"buys": len(buys), "run_up": grp([b for b in buys if b["kind"] == "run_up"]), "fall": grp([b for b in buys if b["kind"] == "fall"]),
            "flat": grp([b for b in buys if b["kind"] == "flat"]), "near_high": grp(near_high), "near_low": grp(near_low),
            "chased": sorted([b for b in buys if b["kind"] == "run_up"], key=lambda b: b["after_rs"])[:5]}


def vs_nifty(realized: list[dict], nifty: list[tuple]) -> dict:
    """Each closed lot against Nifty 50 bought and sold on the same days
    with the same money: ahead or behind, in rupees."""
    rows = []
    for r in realized:
        if r["kind"] != "equity" or r["days"] < 1:
            continue
        a, b = _at(nifty, date.fromisoformat(r["buy_day"])), _at(nifty, date.fromisoformat(r["sell_day"]))
        if a is None or b is None:
            continue
        cost = r["qty"] * r["buy"]
        n = cost * (nifty[b][1] / nifty[a][1] - 1)
        rows.append({"symbol": r["symbol"], "buy_day": r["buy_day"], "sell_day": r["sell_day"], "pnl": r["pnl"], "nifty": _r(n), "vs": _r(r["pnl"] - n)})
    if not rows:
        return {"trades": 0}
    by = defaultdict(float)
    for x in rows:
        by[x["symbol"]] += x["vs"]
    return {"trades": len(rows), "beat": sum(1 for x in rows if x["vs"] > 0), "vs": _r(sum(x["vs"] for x in rows)),
            "pnl": _r(sum(x["pnl"] for x in rows)), "nifty": _r(sum(x["nifty"] for x in rows)),
            "best": [{"symbol": s, "vs": _r(v)} for s, v in sorted(by.items(), key=lambda x: -x[1])[:3] if v > 0],
            "worst": [{"symbol": s, "vs": _r(v)} for s, v in sorted(by.items(), key=lambda x: x[1])[:3] if v < 0]}


def in_day(trades: list[dict], prices: dict) -> dict:
    """Where in each day's range (0% its low, 100% its high) you bought and
    sold, and what buying above the day's close cost against buying at it."""
    out = {"buy": [], "sell": []}
    extra, outside = 0.0, 0
    for t in _eq(trades):
        s = prices.get(LT.key_of(t)) or []
        i = _at(s, t["trade_date"])
        if i is None or s[i][0] != t["trade_date"] or s[i][2] <= s[i][3]:
            continue
        _, close, hi, lo = s[i]
        px = float(t["price"])
        if not lo * 0.995 <= px <= hi * 1.005:      # adjusted since (a split, a bonus): can't place it
            outside += 1
            continue
        out[t["trade_type"]].append(min(1.0, max(0.0, (px - lo) / (hi - lo))))
        extra += (px - close) * float(t["quantity"]) * (1 if t["trade_type"] == "buy" else -1)
    f = lambda xs: _r(median(xs) * 100, 0) if xs else None  # noqa: E731
    return {"buys": len(out["buy"]), "sells": len(out["sell"]), "buy_median_pct": f(out["buy"]), "sell_median_pct": f(out["sell"]),
            "vs_close": _r(extra), "left_out": outside}


# ── all of it, and the strongest findings ────────────────────────────

def analyse(trades: list[dict], analysis: dict, today: date, prices: Optional[dict] = None,
            nifty: Optional[list[dict]] = None) -> dict:
    """analysis: lens_trades.analyse's answer for the same trades. prices:
    {key_of(trade): Kite daily candles}; nifty: Nifty 50's. Without them
    only the findings from the trades alone are made."""
    realized, per = analysis["realized"], analysis["holdings"]
    out = {
        "costs": costs(trades, realized), "holding": holding(realized), "averaging": averaging(trades, per),
        "round_trips": round_trips(trades), "intraday": intraday(trades), "spread": spread(trades),
        "funds": funds(trades, today), "orders": orders(trades), "priced": False,
    }
    if prices is not None and nifty:
        P = {k: _series(v) for k, v in prices.items()}
        N = _series(nifty)
        keys = {p["isin"] or p["symbol"]: p["key"] for p in per}
        out.update({"after_sale": after_sale(realized, P, N, today, keys), "buy_timing": buy_timing(trades, P, per, today),
                    "vs_nifty": vs_nifty(realized, N), "in_day": in_day(trades, P), "priced": True})
    out["summary"] = summary(out)
    return out


def _inr(v: float) -> str:
    v = round(v)
    n = s = str(abs(v))     # Indian grouping: 12,34,567
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"{'−' if v < 0 else ''}₹{s}"


def _pct(v: float) -> str:
    return f"{'−' if v < 0 else '+'}{abs(v):.1f}%"


def _names(rows, key="symbol", n=3):
    xs = []
    for r in rows:
        if r[key] not in xs:
            xs.append(r[key])
    return ", ".join(xs[:n]) + (f" and {len(xs) - n} more" if len(xs) > n else "")


def summary(h: dict) -> list[dict]:
    """The findings the data clearly supports, the biggest in rupees first:
    {key, title, text, rupees, tone, section}."""
    F = []

    def add(key, title, text, rupees, tone, section):
        F.append({"key": key, "title": title, "text": text, "rupees": _r(rupees), "tone": tone, "section": section})

    c = h["costs"]
    if c["charges"] and c["booked"] and c["charges"] > 0:
        share = c["charges"] / c["booked"] * 100 if c["booked"] > 0 else None
        add("costs", "What trading cost you",
            f"About {_inr(c['charges'])} in charges and {_inr(c['tax'])} in tax on gains, estimated"
            + (f": {share:.0f}% of the {_inr(c['booked'])} you booked." if share is not None else f", on top of {_inr(c['booked'])} booked."),
            c["charges"] + (c["tax"] or 0), "bad" if share is not None and share > 20 else "info", "costs")
    a = h.get("after_sale")
    if a and a.get("winners") and a["winners"]["sales"] >= MIN_SALES and a["winners"]["missed_3m"] > 0 and a["winners"]["kept_rising"] / a["winners"]["sales"] >= 0.6:
        w = a["winners"]
        top = [r for r in a["sales"] if r["won"] and r["m3"] and r["m3"]["missed"] > 0]
        add("sold_early", "You sell winners early",
            f"{w['kept_rising']} of {w['sales']} shares you sold at a profit kept rising: held 3 months more they’d have made {_inr(w['missed_3m'])} more. Most: {_names(top)}.",
            w["missed_3m"], "bad", "after_sale")
    if a and a.get("losers") and a["losers"]["sales"] >= MIN_SALES and a["losers"]["missed_3m"] < 0:
        l = a["losers"]
        add("cut_losses", "Your loss-cutting paid",
            f"Shares you sold at a loss fell further: over the next 3 months, holding on would have cost {_inr(-l['missed_3m'])} more.",
            -l["missed_3m"], "good", "after_sale")
    b = h.get("buy_timing")
    if b and b.get("run_up") and b["run_up"]["enough"] and b.get("flat") and b["flat"]["enough"] \
            and b["run_up"]["return_pct"] is not None and b["flat"]["return_pct"] is not None and b["run_up"]["return_pct"] < b["flat"]["return_pct"] - 5:
        r, f = b["run_up"], b["flat"]
        add("chasing", "Buying after a run-up hasn’t worked",
            f"{r['buys']} buys came after a 10%+ rise in the month before: they’re {_pct(r['return_pct'])} since, against {_pct(f['return_pct'])} for your other buys. Worst: {_names(b['chased'])}.",
            abs(r["amount"] * (f["return_pct"] - r["return_pct"]) / 100), "bad", "buy_timing")
    v = h.get("vs_nifty")
    if v and v.get("trades", 0) >= MIN_SALES:
        add("vs_nifty", "Your picks against Nifty 50",
            f"Your closed trades made {_inr(v['pnl'])}; the same money in Nifty 50 over the same days would have made {_inr(v['nifty'])}. "
            f"{v['beat']} of {v['trades']} beat it." + (f" Furthest behind: {_names(v['worst'])}." if v["vs"] < 0 and v["worst"] else f" Best: {_names(v['best'])}." if v["best"] else ""),
            abs(v["vs"]), "good" if v["vs"] > 0 else "bad", "vs_nifty")
    av = h["averaging"]
    if av["enough"] and av["added_summary"]["return_pct"] is not None and av["never_summary"]["return_pct"] is not None \
            and abs(av["added_summary"]["return_pct"] - av["never_summary"]["return_pct"]) >= 3:
        s1, s2 = av["added_summary"], av["never_summary"]
        worse = s1["return_pct"] < s2["return_pct"]
        add("averaging", "Averaging down " + ("hasn’t paid" if worse else "has paid"),
            f"{s1['count']} holdings you bought more of after a 5%+ fall came to {_inr(s1['result'])} ({_pct(s1['return_pct'])} on what you put in), "
            f"against {_pct(s2['return_pct'])} for the {s2['count']} you didn’t add to." + (f" Worst: {_names(av['added'])}." if worse else ""),
            abs(s1["result"]), "bad" if worse else "good", "averaging")
    hd = h["holding"]
    if hd["best"] and hd["worst"] and hd["best"] != hd["worst"]:
        bb = next(x for x in hd["buckets"] if x["label"] == hd["best"])
        ww = next(x for x in hd["buckets"] if x["label"] == hd["worst"])
        add("holding", "How long you hold matters",
            f"Sales held {bb['label'].lower()} returned {_pct(bb['return_pct'])}; those held {ww['label'].lower()} {_pct(ww['return_pct'])} ({_inr(ww['pnl'])} over {ww['sales']} sales).",
            abs(ww["pnl"] or 0), "info", "holding")
    rt = h["round_trips"]
    if len(rt["trips"]) >= MIN_GROUP and rt["effect"] < 0:
        add("round_trips", "Buying back cost you",
            f"{len(rt['trips'])} times you sold a share and bought it back within a month; {rt['dearer']} times dearer, {_inr(-rt['effect'])} in all. {_names(rt['trips'])}.",
            -rt["effect"], "bad", "round_trips")
    it = h["intraday"]
    if it["count"] >= MIN_GROUP:
        add("intraday", "Same-day trades",
            f"{it['count']} times you bought and sold a share the same day, for {_inr(it['pnl'])} ({it['wins']} wins). They’re taxed as business income, at your slab rate.",
            abs(it["pnl"]), "good" if it["pnl"] > 0 else "bad", "intraday")
    sp = h["spread"]
    if sp.get("peak") and (sp["peak"]["largest_pct"] or 0) >= 35 and sp["peak"]["holdings"] >= 2:
        p = sp["peak"]
        add("concentration", "One share carried a lot",
            f"In {p['month']} {p['largest']} was {p['largest_pct']:.0f}% of what you held (at cost), across {p['holdings']} holdings.",
            (p["at_cost"] or 0) * p["largest_pct"] / 100, "info", "spread")
    fd = h["funds"]
    gaps = [f for f in fd["funds"] if f["sip"] and (f["missed"] or f["stopped"])]
    if gaps:
        add("sip_gaps", "SIP months missed",
            "; ".join(f"{f['fund']}: {len(f['missed'])} month{'s' if len(f['missed']) != 1 else ''} without its usual {_inr(f['usual'])}" + (" (stopped)" if f["stopped"] else "") for f in gaps[:3]) + ".",
            sum(len(f["missed"]) * f["usual"] for f in gaps), "info", "funds")
    d = h.get("in_day")
    if d and d["buys"] >= MIN_SALES * 2 and d["buy_median_pct"] is not None and d["buy_median_pct"] >= 70:
        add("in_day", "You buy near the day’s high",
            f"Your median buy was {d['buy_median_pct']}% of the way from the day’s low to its high; against each day’s close you paid {_inr(d['vs_close'])} more.",
            abs(d["vs_close"]), "bad", "in_day")
    F.sort(key=lambda f: -(f["rupees"] or 0))
    return F
