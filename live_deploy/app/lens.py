"""
live_deploy — LinckLens: what the Kite account holds, beside LinckTrade
(the deployments), and what can be said about it.

- holdings(): the shares (kite.holdings) and mutual funds
  (kite.mf_holdings), named from Kite's instrument lists, the funds'
  scheme type, plan and option (kite.mf_instruments), the SIPs
  (kite.mf_sips) and the account's funds (kite.margins). Read from Kite
  once a day (warm_loop, after the day's login), again on Refresh and at
  the 17:00 sync, and kept in between. Shares are priced from LinckLens's
  own Kite websocket (app/lens_feed.py) as ticks arrive; funds stand at
  the NAV Kite gave.
- history(): a year of daily prices for every share held and NIFTY 50,
  from the database (app/lens_store.py): read from Kite once per
  instrument, then only the days since, at the 17:00 sync. From it each
  share's returns, 52-week range, volatility and beta, and the shares
  together against Nifty.

Kite gives no past holdings and no mutual fund NAV history: the year is
TODAY's shares at past prices, and the funds stand at their last NAV.
Every figure built that way says so where it's shown.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from . import lens_store

logger = logging.getLogger("live_deploy.lens")

IST = ZoneInfo("Asia/Kolkata")
RETRY_FAILED_S = 600          # a failed price read is tried again after this
NIFTY = 256265
NIFTY_NAME = "NIFTY 50"
PERIODS = [("1W", 7), ("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)]
YEAR_DAYS = 365


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _r(v, n=2):
    return None if v is None else round(v, n)


def _inr(v: float) -> str:
    """₹ in Indian digit grouping, whole rupees: ₹1,77,309."""
    n = f"{abs(v):.0f}"
    head, tail = n[:-3], n[-3:]
    while len(head) > 2:
        tail = f"{head[-2:]},{tail}"
        head = head[:-2]
    return f"{'-' if v < 0 else ''}₹{head + ',' if head else ''}{tail}"


def _day(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


# ── What each holding is ─────────────────────────────────────────────────

ASSETS = {   # key -> label, in the order the allocation lists them
    "stock": "Stocks", "etf": "Equity ETFs", "gold": "Gold", "silver": "Silver", "debt": "Debt & liquid",
    "reit": "REITs & InvITs",
    "mf_equity": "Equity funds", "mf_elss": "Tax-saver funds (ELSS)", "mf_index": "Index funds",
    "mf_hybrid": "Hybrid funds", "mf_debt": "Debt funds", "mf_liquid": "Liquid funds", "mf_fof": "Fund of funds",
    "mf_other": "Other funds",
}
_DEBT = re.compile(r"LIQUID|GILT|BOND|SDL|TBILL|T-BILL|MONEY ?MARKET|OVERNIGHT|CPSE ?BOND|BHARAT ?BOND|EBBETF|LIQUIDBEES", re.I)


def asset_of_share(symbol: str, name: str = "") -> str:
    """What a listed holding is, from its symbol and Kite's name for it
    (Kite gives no asset class): a sovereign gold bond, a gold, silver or
    debt ETF, an equity ETF, a REIT or InvIT, or a stock."""
    s, n = (symbol or "").upper(), (name or "").upper()
    both = f"{s} {n}"
    if s.startswith("SGB"):
        return "gold"
    if "REIT" in both or "INVIT" in both:
        return "reit"
    etf = "ETF" in both or s.endswith("BEES") or "BEES" in n or s.endswith("IETF")
    if "GOLD" in both and etf:          # a company with GOLD in its name is a stock
        return "gold"
    if "SILVER" in both and etf:
        return "silver"
    if etf:
        return "debt" if _DEBT.search(both) else "etf"
    return "stock"


def asset_of_fund(scheme_type: Optional[str], name: str = "") -> str:
    """A fund's kind, from Kite's scheme type, else its name."""
    t, n = (scheme_type or "").lower(), (name or "").upper()
    if "elss" in t or "ELSS" in n or "TAX SAVER" in n:
        return "mf_elss"
    if "index" in t or " INDEX" in n or "NIFTY" in n and "INDEX" in n:
        return "mf_index"
    if "liquid" in t or "overnight" in t or "LIQUID" in n or "OVERNIGHT" in n:
        return "mf_liquid"
    if "fof" in t or "fund of fund" in t or "FUND OF FUND" in n or " FOF" in n:
        return "mf_fof"
    if "hybrid" in t or "balanced" in t or "HYBRID" in n or "BALANCED" in n:
        return "mf_hybrid"
    if "debt" in t or "income" in t or "gilt" in t:
        return "mf_debt"
    if "equity" in t or "growth" in t:
        return "mf_equity"
    return "mf_other"


# ── The holdings ─────────────────────────────────────────────────────────

def equity_row(h: dict, name: str = "") -> dict:
    """One share holding. The quantity is what's settled plus what's
    bought and not yet delivered (T1), as Kite shows it."""
    qty = _f(h.get("quantity")) + _f(h.get("t1_quantity"))
    avg, ltp, prev = _f(h.get("average_price")), _f(h.get("last_price")), _f(h.get("close_price"))
    invested, value = qty * avg, qty * ltp
    day = (ltp - prev) * qty if prev else None
    symbol = h.get("tradingsymbol")
    return {
        "symbol": symbol, "name": name or None, "exchange": h.get("exchange"), "isin": h.get("isin"),
        "token": h.get("instrument_token"), "asset": asset_of_share(symbol, name),
        "qty": qty, "t1_qty": _f(h.get("t1_quantity")), "pledged_qty": _f(h.get("collateral_quantity")),
        "discrepancy": bool(h.get("discrepancy")),
        "avg": round(avg, 4), "ltp": round(ltp, 4), "prev_close": round(prev, 4) if prev else None,
        "invested": round(invested, 2), "value": round(value, 2), "pnl": round(value - invested, 2),
        "pnl_pct": round((value - invested) / invested * 100, 2) if invested else None,
        "day_change": None if day is None else round(day, 2),
        "day_change_pct": round((ltp / prev - 1) * 100, 2) if prev else None,
    }


def mf_row(h: dict, meta: Optional[dict] = None) -> dict:
    """One mutual fund holding, at its last published NAV, with what Kite's
    fund list says of it (its AMC, scheme type, Direct or Regular plan,
    Growth or IDCW option)."""
    meta = meta or {}
    units, avg, nav = _f(h.get("quantity")), _f(h.get("average_price")), _f(h.get("last_price"))
    invested, value = units * avg, units * nav
    when = h.get("last_price_date")
    fund = h.get("fund") or meta.get("name")
    plan = (meta.get("plan") or "").lower() or ("regular" if "REGULAR" in (fund or "").upper()
                                                else "direct" if "DIRECT" in (fund or "").upper() else None)
    option = (meta.get("dividend_type") or "").lower() or None
    return {
        "fund": fund, "folio": h.get("folio"), "isin": h.get("tradingsymbol"),
        "amc": meta.get("amc"), "scheme_type": meta.get("scheme_type"), "plan": plan, "option": option,
        "asset": asset_of_fund(meta.get("scheme_type"), fund or ""),
        "units": units, "pledged_units": _f(h.get("pledged_quantity")),
        "avg": round(avg, 4), "nav": round(nav, 4),
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
        out["advancing"] = sum(1 for r in rows if (r.get("day_change") or 0) > 0)
        out["declining"] = sum(1 for r in rows if (r.get("day_change") or 0) < 0)
    return out


MONTHLY = {"monthly": 1.0, "weekly": 52 / 12, "fortnightly": 26 / 12, "quarterly": 1 / 3, "daily": 21.0}


def sip_row(s: dict) -> dict:
    freq = (s.get("frequency") or "").lower()
    amount = _f(s.get("instalment_amount"))
    status = (s.get("status") or "").upper()
    nxt = _day(s.get("next_instalment"))
    return {
        "id": s.get("sip_id"), "fund": s.get("fund"), "isin": s.get("tradingsymbol"), "status": status,
        "frequency": freq or None, "amount": amount, "day": s.get("instalment_day"),
        "monthly": round(amount * MONTHLY.get(freq, 0), 2) if status == "ACTIVE" else 0.0,
        "completed": int(_f(s.get("completed_instalments"))), "pending": int(_f(s.get("pending_instalments"))) if s.get("pending_instalments") is not None else None,
        "next": nxt.isoformat() if nxt else None, "created": _day(s.get("created")).isoformat() if _day(s.get("created")) else None,
        "step_up": s.get("step_up") or None,
    }


def account_of(margins: Optional[dict]) -> Optional[dict]:
    """The equity segment's money, from kite.margins(): what's free, the
    cash in it, what pledged holdings add, what's in use."""
    if not margins:
        return None
    eq = margins.get("equity") or margins
    av, ut = eq.get("available") or {}, eq.get("utilised") or {}
    return {
        "net": _r(_f(eq.get("net"))), "cash": _r(_f(av.get("live_balance") or av.get("cash"))),
        "opening": _r(_f(av.get("opening_balance"))), "collateral": _r(_f(av.get("collateral"))),
        "used": _r(_f(ut.get("debits"))), "payout": _r(_f(ut.get("payout"))),
        "commodity_net": _r(_f((margins.get("commodity") or {}).get("net"))) if margins.get("commodity") else None,
    }


def allocation(eq: list[dict], funds: list[dict]) -> list[dict]:
    """What it's in, by kind, largest first, as a share of everything."""
    total = sum(r["value"] for r in eq + funds) or 0
    by: dict[str, dict] = {}
    for r in eq + funds:
        a = by.setdefault(r["asset"], {"key": r["asset"], "label": ASSETS.get(r["asset"], r["asset"]), "value": 0.0, "count": 0})
        a["value"] += r["value"]
        a["count"] += 1
    out = sorted(by.values(), key=lambda a: -a["value"])
    for a in out:
        a["value"] = round(a["value"], 2)
        a["weight"] = round(a["value"] / total * 100, 2) if total else None
    return out


def concentration(rows: list[dict]) -> dict:
    """How much rides on a few: the largest holding's share, the top five's,
    and the effective number of equal holdings it amounts to (1 / sum of
    squared weights)."""
    total = sum(r["value"] for r in rows)
    if not total:
        return {"count": len(rows), "top": None, "top5": None, "effective": None}
    ws = sorted(((r["value"] / total, r) for r in rows), key=lambda x: -x[0])
    top = ws[0][1]
    return {
        "count": len(rows), "top": {"name": top.get("symbol") or top.get("fund"), "weight": round(ws[0][0] * 100, 2)},
        "top5": round(sum(w for w, _ in ws[:5]) * 100, 2),
        "effective": round(1 / sum(w * w for w, _ in ws), 1),
    }


def build(equity: list[dict], mf: list[dict], *, names: Optional[dict] = None, mf_meta: Optional[dict] = None,
          sips: Optional[list] = None, margins: Optional[dict] = None) -> dict:
    """Pure: Kite's answers -> the holdings, largest first, with their
    weights and totals, how it's spread, the SIPs, the account's funds and
    what needs a look."""
    names, mf_meta = names or {}, mf_meta or {}
    eq = sorted((equity_row(h, names.get(h.get("instrument_token")) or names.get(h.get("tradingsymbol")) or "")
                 for h in equity or []), key=lambda r: -r["value"])
    funds = sorted((mf_row(h, mf_meta.get(h.get("tradingsymbol"))) for h in mf or []), key=lambda r: -r["value"])
    total = sum(r["value"] for r in eq + funds)
    for r in eq + funds:
        r["weight"] = round(r["value"] / total * 100, 2) if total else None
    sip_rows = sorted((sip_row(s) for s in sips or []), key=lambda s: (s["status"] != "ACTIVE", -s["amount"]))
    t_eq, t_mf = totals(eq, day=True), totals(funds)
    out = {
        "equity": eq, "mf": funds, "sips": sip_rows,
        "sip_monthly": round(sum(s["monthly"] for s in sip_rows), 2),
        "account": account_of(margins),
        "totals": {"equity": t_eq, "mf": t_mf, "all": totals(eq + funds)},
        "allocation": allocation(eq, funds), "concentration": concentration(eq + funds),
    }
    out["attention"] = attention(out)
    return out


# ── A year of prices ─────────────────────────────────────────────────────

def _closes(candles: list[dict]) -> list[tuple[date, float, float, float]]:
    """[(day, close, high, low)], oldest first, one a day."""
    by = {}
    for c in candles or []:
        d = _day(c.get("date"))
        if d is not None and c.get("close") is not None:
            by[d] = (d, float(c["close"]), float(c.get("high") or c["close"]), float(c.get("low") or c["close"]))
    return [by[d] for d in sorted(by)]


def _at(series: list[tuple], d: date) -> Optional[float]:
    """The last close on or before `d`."""
    best = None
    for row in series:
        if row[0] > d:
            break
        best = row[1]
    return best


def _returns(series: list[tuple]) -> dict[date, float]:
    """{day: log return from the close before}."""
    out = {}
    for a, b in zip(series, series[1:]):
        if a[1] > 0 and b[1] > 0:
            out[b[0]] = math.log(b[1] / a[1])
    return out


def _sd(xs: list[float]) -> Optional[float]:
    if len(xs) < 20:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _beta(r: dict, m: dict) -> Optional[float]:
    days = [d for d in r if d in m]
    if len(days) < 40:
        return None
    a, b = [r[d] for d in days], [m[d] for d in days]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    var = sum((y - mb) ** 2 for y in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / var if var else None


def _spark(series: list[tuple], last: Optional[float], today: Optional[date] = None, n: int = 60) -> tuple[list[float], list[str]]:
    """The year in at most n points (and the price now), with their days."""
    pts = [(d, c) for d, c, _, _ in series]
    if last and pts:
        # the price now: a day of its own, or in place of today's candle
        pts = pts + [(today, last)] if today and pts[-1][0] < today else pts[:-1] + [(pts[-1][0], last)]
    if len(pts) > n:
        step = (len(pts) - 1) / (n - 1)
        pts = [pts[round(i * step)] for i in range(n)]
    return [round(c, 2) for _, c in pts], [d.isoformat() for d, _ in pts]


def share_stats(candles: list[dict], ltp: float, today: date, nifty: Optional[list[tuple]] = None) -> dict:
    """One share over the year: its return over each period (to its price
    now), its 52-week high and low and where it sits between them, how far
    it is off the high, its volatility (annualised, from daily moves), its
    beta against Nifty and a small line of the year."""
    s = [x for x in _closes(candles) if x[0] >= today - timedelta(days=YEAR_DAYS)]
    if not s:
        return {"ret": {}, "spark": [], "days": 0}
    now = ltp or s[-1][1]
    first_day = s[0][0]
    ret = {}
    for k, days in PERIODS:
        start = today - timedelta(days=days)
        base = _at(s, start) if first_day <= start else None
        ret[k] = _r((now / base - 1) * 100) if base else None
    hi = max([h for _, _, h, _ in s] + [now])
    lo = min([low for _, _, _, low in s] + [now])
    r = _returns(s)
    sd = _sd(list(r.values()))
    beta = _beta(r, _returns(nifty)) if nifty else None
    spark, spark_days = _spark(s, now, today)
    return {
        "ret": ret, "hi52": _r(hi), "lo52": _r(lo),
        "range_pos": _r((now - lo) / (hi - lo) * 100, 1) if hi > lo else None,
        "off_high": _r((now / hi - 1) * 100) if hi else None,
        "vol": _r(sd * math.sqrt(252) * 100, 1) if sd is not None else None,
        "beta": _r(beta), "spark": spark, "spark_days": spark_days, "days": len(s), "since": first_day.isoformat(),
    }


def _drawdown(values: list[float], days: list[date]) -> dict:
    peak, peak_i, worst, at = -math.inf, 0, 0.0, (0, 0)
    for i, v in enumerate(values):
        if v > peak:
            peak, peak_i = v, i
        dd = v / peak - 1 if peak > 0 else 0
        if dd < worst:
            worst, at = dd, (peak_i, i)
    return {"pct": _r(worst * 100), "from": days[at[0]].isoformat() if values else None,
            "to": days[at[1]].isoformat() if values else None}


def portfolio_curve(eq: list[dict], candles: dict, nifty: list[dict], today: date) -> Optional[dict]:
    """Today's shares at each day's close over the year, against NIFTY 50,
    both from 100. Only shares with prices for the whole year are in it
    (a newer listing would jump the line); the others are named."""
    n = [x for x in _closes(nifty) if x[0] >= today - timedelta(days=YEAR_DAYS)]
    if len(n) < 20:
        return None
    days = [d for d, *_ in n]
    covered, left_out = [], []
    for r in eq:
        s = _closes(candles.get(r.get("token")) or [])
        if s and s[0][0] <= days[0] + timedelta(days=7):
            covered.append((r, s))
        else:
            left_out.append(r["symbol"])
    if not covered:
        return None
    values = []
    for d in days:
        values.append(sum(r["qty"] * (_at(s, d) or s[0][1]) for r, s in covered))
    p0, n0 = values[0], n[0][1]
    port = [v / p0 * 100 for v in values]
    nif = [c / n0 * 100 for _, c, _, _ in n]
    periods = []
    for k, dd in PERIODS:
        start = today - timedelta(days=dd)
        i = next((j for j in range(len(days) - 1, -1, -1) if days[j] <= start), None)
        if i is None:
            continue
        periods.append({"k": k, "from": days[i].isoformat(),
                        "portfolio": _r((values[-1] / values[i] - 1) * 100), "nifty": _r((n[-1][1] / n[i][1] - 1) * 100)})
    pr = {days[i]: math.log(values[i] / values[i - 1]) for i in range(1, len(values)) if values[i - 1] > 0}
    nr = _returns(n)
    sd = _sd(list(pr.values()))
    return {
        "days": [d.isoformat() for d in days], "portfolio": [round(v, 2) for v in port], "nifty": [round(v, 2) for v in nif],
        "value": [round(v, 2) for v in values], "periods": periods,
        "vol": _r(sd * math.sqrt(252) * 100, 1) if sd is not None else None,
        "beta": _r(_beta(pr, nr)), "drawdown": _drawdown(values, days), "nifty_drawdown": _drawdown([c for _, c, _, _ in n], days),
        "covered": len(covered), "left_out": left_out,
        "covered_value": round(sum(r["value"] for r, _ in covered), 2),
    }


def insights(h: dict, candles: dict, nifty_candles: list[dict], today: date, nifty_ltp: Optional[float] = None) -> dict:
    """Pure: the holdings and a year of prices -> each share's year, the
    shares together against Nifty, and what needs a look with both.
    nifty_ltp: Nifty now, when the feed has it (else its last close)."""
    n = [x for x in _closes(nifty_candles) if x[0] >= today - timedelta(days=YEAR_DAYS)]
    shares = {r["symbol"]: share_stats(candles.get(r.get("token")) or [], r["ltp"], today, n) for r in h["equity"]}
    out = {"shares": shares, "curve": portfolio_curve(h["equity"], candles, nifty_candles, today),
           "nifty": share_stats(nifty_candles, nifty_ltp or (n[-1][1] if n else 0), today) if n else None}
    out["attention"] = attention(h, out)
    return out


# ── What needs a look ────────────────────────────────────────────────────

def _names(rows, key="symbol", n=6):
    names = [r[key] for r in rows]
    return names[:n] + ([f"{len(names) - n} more"] if len(names) > n else [])


def attention(h: dict, ins: Optional[dict] = None) -> list[dict]:
    """Things worth a look, each with why and which holdings: what costs
    more than it needs to, rides on one name, sits in a deep loss or near
    its yearly low, lags the market, isn't delivered or is pledged, cash
    left idle. tone: warn (act on it, perhaps) or info (know it)."""
    eq, funds = h["equity"], h["mf"]
    total = h["totals"]["all"]["value"] or 0
    out = []

    def add(key, tone, title, detail, items=None):
        out.append({"key": key, "tone": tone, "title": title, "detail": detail, "items": items or []})

    regular = [f for f in funds if f.get("plan") == "regular"]
    if regular:
        v = sum(f["value"] for f in regular)
        add("regular_plans", "warn", f"{len(regular)} fund{'s are' if len(regular) > 1 else ' is a'} Regular plan{'s' if len(regular) > 1 else ''}, {_inr(v)}",
            "A Regular plan pays a distributor's commission out of the fund every year; the Direct plan of the same fund "
            "doesn't, so it returns a little more. Moving is a sale and a new purchase: check exit loads and tax first.",
            _names(regular, "fund"))
    idcw = [f for f in funds if f.get("option") and f["option"] not in ("growth",)]
    if idcw:
        add("idcw", "info", f"{len(idcw)} fund{'s pay' if len(idcw) > 1 else ' pays'} out (IDCW) rather than grow",
            "IDCW payouts are taxed at your slab as they're paid; a Growth option keeps the gains invested until you sell.",
            _names(idcw, "fund"))
    # one company: a fund or an ETF already spreads over many
    for r in [r for r in eq if r["asset"] == "stock" and (r.get("weight") or 0) > 20]:
        add(f"heavy:{r['symbol']}", "warn", f"{r['symbol']} is {r['weight']:.0f}% of everything",
            "One company carries a fifth or more of the whole: its bad day is the portfolio's bad day.", [r["symbol"]])
    deep = sorted([r for r in eq if (r.get("pnl_pct") or 0) <= -25], key=lambda r: r["pnl_pct"])
    if deep:
        add("deep_loss", "warn", f"{len(deep)} share{'s are' if len(deep) > 1 else ' is'} down 25% or more from cost",
            "Worth asking of each whether you'd buy it today at this price. A loss can also offset gains for tax.",
            [f"{r['symbol']} {r['pnl_pct']:+.0f}%" for r in deep[:6]])
    small = [r for r in eq + funds if r.get("weight") is not None and r["weight"] < 0.5]
    if len(small) >= 3:
        add("small", "info", f"{len(small)} holdings are under 0.5% each, {sum(r['weight'] for r in small):.1f}% together",
            "Too small to move the whole either way: keep them on purpose, or fold them into ones that matter.",
            _names([{"n": r.get("symbol") or r.get("fund")} for r in small], "n"))
    t1 = [r for r in eq if r.get("t1_qty")]
    if t1:
        add("t1", "info", f"{len(t1)} share{'s' if len(t1) > 1 else ''} bought, not yet delivered (T1)",
            "Delivered to your demat on the next settlement day; until then it can't be pledged.", _names(t1))
    pledged = [r for r in eq if r.get("pledged_qty")]
    if pledged:
        add("pledged", "info", f"{len(pledged)} share{'s are' if len(pledged) > 1 else ' is'} pledged for margin",
            "Pledged shares back margin for trading; they're still yours and still earn, but can't be sold until unpledged.", _names(pledged))
    if any(r.get("discrepancy") for r in eq):
        add("discrepancy", "warn", "Kite flags a discrepancy in a holding",
            "Kite's holdings say a quantity doesn't match the depository's. Check it in Console.",
            [r["symbol"] for r in eq if r.get("discrepancy")])
    acc = h.get("account")
    if acc and acc.get("cash") and total and acc["cash"] > 0.05 * total and acc["cash"] > 10000:
        add("idle_cash", "info", f"{_inr(acc['cash'])} cash in the account",
            f"{acc['cash'] / total * 100:.0f}% of what you hold sits as cash in the trading account, earning nothing there.")
    paused = [s for s in h.get("sips") or [] if s["status"] == "PAUSED"]
    if paused:
        add("sips_paused", "info", f"{len(paused)} SIP{'s are' if len(paused) > 1 else ' is'} paused", "", _names(paused, "fund"))

    if ins:
        stats = ins.get("shares") or {}
        nifty_1y = ((ins.get("nifty") or {}).get("ret") or {}).get("1Y")
        low = [r for r in eq if (stats.get(r["symbol"]) or {}).get("range_pos") is not None and stats[r["symbol"]]["range_pos"] <= 10]
        if low:
            add("near_low", "warn", f"{len(low)} share{'s are' if len(low) > 1 else ' is'} near the 52-week low",
                "In the bottom tenth of the year's range. Is it the business, or the market?",
                [f"{r['symbol']} {stats[r['symbol']]['off_high']:+.0f}% off its high" for r in low[:6]])
        # range_pos is None when the year never moved (a liquid ETF): no high to be near
        high = [r for r in eq if (stats.get(r["symbol"]) or {}).get("range_pos") is not None and stats[r["symbol"]]["off_high"] >= -2]
        if high:
            add("near_high", "good", f"{len(high)} share{'s are' if len(high) > 1 else ' is'} at or near the 52-week high",
                "Within 2% of the year's high.", _names(high))
        if nifty_1y is not None:
            lag = sorted([r for r in eq if ((stats.get(r["symbol"]) or {}).get("ret") or {}).get("1Y") is not None
                          and stats[r["symbol"]]["ret"]["1Y"] < nifty_1y - 20], key=lambda r: stats[r["symbol"]]["ret"]["1Y"])
            if lag:
                add("lagging", "warn", f"{len(lag)} share{'s' if len(lag) > 1 else ''} trail Nifty by 20 points or more over a year",
                    f"Nifty is {nifty_1y:+.1f}% in a year.", [f"{r['symbol']} {stats[r['symbol']]['ret']['1Y']:+.0f}%" for r in lag[:6]])
        wild = [r for r in eq if ((stats.get(r["symbol"]) or {}).get("vol") or 0) >= 45 and (r.get("weight") or 0) >= 2]
        if wild:
            add("volatile", "info", f"{len(wild)} sizeable holding{'s swing' if len(wild) > 1 else ' swings'} hard",
                "Annual volatility of 45% or more, at 2% or more of the whole: expect moves of a third either way in a year.",
                [f"{r['symbol']} {stats[r['symbol']]['vol']:.0f}%" for r in wild[:6]])
    order = {"warn": 0, "good": 1, "info": 2}
    return sorted(out, key=lambda a: order[a["tone"]])


# ── Reading it from Kite ─────────────────────────────────────────────────

def year_answer(h: dict, candles: dict, today: date, *, total: int, error: Optional[str] = None,
                nifty_ltp: Optional[float] = None) -> dict:
    """What /v2/lens/history answers once the year is in."""
    return {"day": today.isoformat(), "done": total, "total": total, "error": error, "state": "ready",
            "insights": insights(h, candles, candles.get(NIFTY) or [], today, nifty_ltp)}


def priced(equity: list[dict], price: Callable[[Any], Optional[dict]]) -> list[dict]:
    """Kite's holdings with each share at its feed price now (and the
    previous close the feed gave), where the feed has one."""
    out = []
    for h in equity or []:
        p = price(h.get("instrument_token"))
        if p and p.get("ltp"):
            h = {**h, "last_price": p["ltp"], **({"close_price": p["close"]} if p.get("close") else {})}
        out.append(h)
    return out


def inspect_answer(trades: list[dict], h: Optional[dict], err: Optional[str], candles: dict, today: date) -> dict:
    """What /v2/lens/inspect answers (app/lens_inspect.py's reads)."""
    from . import lens_inspect as I
    held = Lens._held_by_isin(h)
    rows = [] if h is None else [{"isin": r["isin"], "symbol": r["symbol"], "kind": "equity", "weight": r.get("weight"), "value": r["value"]}
                                 for r in h["equity"] if r.get("isin")] + [
        {"isin": f["isin"], "symbol": f["fund"], "kind": "mf", "weight": f.get("weight"), "value": f["value"]} for f in h["mf"] if f.get("isin")]
    return {
        "as_of": today.isoformat(), "holdings_error": err, "trades": len(trades),
        "own": I.own_money(trades, held, today),
        "tax": I.tax_free(trades, held, today),
        "holdings": rows,
        "contributions": I.contributions(trades, h, candles, candles.get(NIFTY) or [], today) if h is not None else [],
        "year_ready": bool(candles.get(NIFTY)),
    }


class Lens:
    """Reads the account from Kite. `call(name, *args)` makes one Kite
    call (the app's: kite.<name>(*args) through the throttle). pool: the
    database, for the year of prices. feed: app/lens_feed.LensFeed, the
    live prices (None: Kite's holdings prices as read)."""

    def __init__(self, dispatcher, call: Optional[Callable[..., Any]] = None, today: Optional[Callable[[], date]] = None,
                 *, pool=None, feed=None, now: Optional[Callable[[], datetime]] = None):
        self.dispatcher, self.pool, self.feed = dispatcher, pool, feed
        self.call = call or self._kite
        self.now = now or (lambda: datetime.now(IST))
        self.today = today or (lambda: self.now().astimezone(IST).date())
        self._raw: Optional[dict] = None          # Kite's answers as last read
        self._daily: dict[str, tuple[date, Any]] = {}
        self._read_lock = asyncio.Lock()
        self.reads = 0
        self.hist: dict = {"task": None, "progress": None, "failed": {}, "retry": {}, "error": None}

    async def _kite(self, what: str, *args):
        from .options import OptionsResolver
        r = OptionsResolver(self.dispatcher)
        kite = r._kite()
        return await r._kite_call(getattr(kite, what), *args)

    async def _once_a_day(self, key: str, what: str, *args):
        got = self._daily.get(key)
        if got and got[0] == self.today():
            return got[1]
        val = await self.call(what, *args)
        self._daily[key] = (self.today(), val)
        return val

    async def _optional(self, missing: list, label: str, fn):
        try:
            return await fn()
        except Exception as e:
            missing.append(f"{label}: {type(e).__name__}: {str(e)[:120]}")
            return None

    # ── the account ────────────────────────────────────────────────────

    async def _read(self) -> None:
        equity = await self.call("holdings")
        mf = await self.call("mf_holdings")
        missing: list[str] = []
        names: dict = {}
        for ex in sorted({h.get("exchange") or "NSE" for h in equity or []}):
            rows = await self._optional(missing, f"{ex} names", lambda ex=ex: self._once_a_day(f"instruments:{ex}", "instruments", ex)) or []
            for row in rows:
                if row.get("name"):
                    names[row.get("instrument_token")] = row["name"]
        meta = {}
        if mf:
            rows = await self._optional(missing, "fund details", lambda: self._once_a_day("mf_instruments", "mf_instruments")) or []
            meta = {r.get("tradingsymbol"): r for r in rows}
        sips = await self._optional(missing, "SIPs", lambda: self.call("mf_sips"))
        margins = await self._optional(missing, "account funds", lambda: self.call("margins"))
        self.reads += 1
        self._raw = {"day": self.today(), "at": datetime.now().astimezone().isoformat(), "equity": equity or [], "mf": mf or [],
                     "names": names, "meta": meta, "sips": sips, "margins": margins, "missing": missing}
        if self.feed is not None:
            want = {NIFTY: NIFTY_NAME}
            want.update({h["instrument_token"]: h.get("tradingsymbol") for h in equity or [] if h.get("instrument_token")})
            self.feed.want(want)

    def _price(self, token) -> Optional[dict]:
        return self.feed.price(token) if self.feed is not None and token else None

    def _answer(self) -> dict:
        r = self._raw
        out = build(priced(r["equity"], self._price), r["mf"], names=r["names"], mf_meta=r["meta"], sips=r["sips"], margins=r["margins"])
        out["missing"] = list(r["missing"])
        out["as_of"] = r["at"]
        if self.feed is not None:
            st = self.feed.status
            out["live"] = {"connected": st["connected"], "priced": sum(1 for x in r["equity"] if self._price(x.get("instrument_token"))),
                           "of": len(r["equity"]), "last_tick_at": st["last_tick_at"]}
        return out

    async def holdings(self, fresh: bool = False) -> dict:
        """The account: read from Kite on the first ask of the day, on
        Refresh (fresh) and at the 17:00 sync; in between, what was read,
        with each share at its live price."""
        async with self._read_lock:
            if fresh or self._raw is None or self._raw["day"] != self.today():
                await self._read()
        return self._answer()

    async def warm_loop(self, every: float = 60.0) -> None:
        """Read the account once a day as soon as Kite is logged in, so the
        feed follows the shares before anyone opens LinckLens."""
        retry_at = 0.0
        while True:
            try:
                logged_in = bool(getattr(self.dispatcher, "access_token", None)) and getattr(self.dispatcher, "connected", False)
                stale = self._raw is None or self._raw["day"] != self.today()
                if logged_in and stale and time.monotonic() >= retry_at:
                    await self.holdings()
            except Exception as e:
                logger.warning("LinckLens: reading the account failed (%s); trying again in 10 minutes", e)
                retry_at = time.monotonic() + RETRY_FAILED_S
            await asyncio.sleep(every)

    # ── the year of prices ─────────────────────────────────────────────

    def _wanted(self, h: dict) -> list[dict]:
        return [{"token": NIFTY, "symbol": NIFTY_NAME, "exchange": "NSE"}] + [
            {"token": r["token"], "symbol": r["symbol"], "exchange": r.get("exchange")} for r in h["equity"] if r.get("token")]

    async def history(self) -> dict:
        """A year of prices for every share held and NIFTY 50, from the
        database; what isn't there yet (a first look, a share bought
        since, a day missed) is read from Kite in the background, and this
        answers with its progress until it's in."""
        h = await self.holdings()
        today, want = self.today(), self._wanted(h)
        base = {"day": today.isoformat(), "total": len(want)}
        if self.pool is None:
            return {**base, "done": 0, "error": "No database for the year of prices", "state": "error"}
        task = self.hist["task"]
        if task is not None and not task.done():
            p = self.hist["progress"] or {}
            return {**base, "done": p.get("done", 0), "error": None, "state": "loading"}
        have = await lens_store.kept(self.pool, [w["token"] for w in want])
        now, mono = self.now(), time.monotonic()
        due = [w for w in want
               if lens_store.to_read((have.get(w["token"]) or {}).get("fetched_through"), (have.get(w["token"]) or {}).get("first"), now) is not None
               and self.hist["retry"].get(w["token"], 0) <= mono]
        if due:
            self._start_reading(due)
            return {**base, "done": len(want) - len(due), "error": None, "state": "loading"}
        candles = await lens_store.candles(self.pool, [w["token"] for w in want], today - timedelta(days=lens_store.FIRST_READ_DAYS))
        failed = {w["symbol"]: self.hist["failed"][w["symbol"]] for w in want if w["symbol"] in self.hist["failed"]}
        if not candles.get(NIFTY):
            err = failed.get(NIFTY_NAME) or "Kite gave no NIFTY 50 prices"
            return {**base, "done": len(want) - 1, "error": f"NIFTY 50 prices: {err}", "state": "error"}
        error = ("Prices couldn't be read for " + ", ".join(f"{k} ({v})" for k, v in failed.items())) if failed else None
        nifty_now = (self._price(NIFTY) or {}).get("ltp")
        return year_answer(h, candles, today, total=len(want), error=error, nifty_ltp=nifty_now)

    def _start_reading(self, due: list[dict]) -> None:
        self.hist["progress"] = {"total": len(due), "done": 0}
        self.hist["task"] = asyncio.create_task(self._read_prices(due), name="lens-prices")

    async def _read_prices(self, due: list[dict]) -> dict:
        r = await lens_store.bring_up_to_date(self.pool, self.call, due, self.now(), self.hist["progress"])
        retry_at = time.monotonic() + RETRY_FAILED_S
        for w in due:
            if w["symbol"] in r["failed"]:
                self.hist["failed"][w["symbol"]] = r["failed"][w["symbol"]]
                self.hist["retry"][w["token"]] = retry_at
            else:
                self.hist["failed"].pop(w["symbol"], None)
                self.hist["retry"].pop(w["token"], None)
        return r

    async def topup(self) -> dict:
        """The 17:00 sync: the account read again, then each instrument's
        days since it was last read (one Kite call each; nothing for one
        that's up to date)."""
        h = await self.holdings(fresh=True)
        task = self.hist["task"]
        if task is not None and not task.done():
            await task
        self.hist["progress"] = {"total": 0, "done": 0}
        r = await self._read_prices(self._wanted(h))
        logger.info("LinckLens 17:00 sync: %d Kite call(s), %d day(s) stored, %d failed", r["asked"], r["rows"], len(r["failed"]))
        return r

    # ── your trades (Console tradebook files, app/lens_trades.py) ──────

    async def import_trades(self, files: list[dict]) -> list[dict]:
        """Each file read and stored: what it held, what was new, what was
        already there (a trade is stored once however often it's imported),
        and the rows that couldn't be read."""
        from . import lens_trades
        out = []
        for f in files:
            name = (f.get("name") or "").strip()[:200] or None
            rows, problems = lens_trades.parse(f.get("text") or "", name or "")
            first = min((r["trade_date"] for r in rows), default=None)
            last = max((r["trade_date"] for r in rows), default=None)
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    imp = await conn.fetchval(
                        "INSERT INTO lens_imports (filename, rows, added, skipped, first_day, last_day, problems) "
                        "VALUES ($1, $2, 0, 0, $3, $4, $5) RETURNING id", name, len(rows), first, last, problems[:50])
                    added = 0
                    for r in rows:
                        added += await conn.fetchval(
                            "INSERT INTO lens_trades (import_id, symbol, isin, trade_date, exchange, segment, series, trade_type, "
                            "auction, quantity, price, trade_id, order_id, executed_at) "
                            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14) "
                            "ON CONFLICT (exchange, segment, trade_id, isin, trade_type) DO NOTHING RETURNING 1",
                            imp, r["symbol"], r["isin"] or "", r["trade_date"], r["exchange"], r["segment"], r["series"],
                            r["trade_type"], r["auction"], r["quantity"], r["price"], r["trade_id"], r["order_id"],
                            r["executed_at"]) or 0
                    await conn.execute("UPDATE lens_imports SET added = $2, skipped = $3 WHERE id = $1", imp, added, len(rows) - added)
            out.append({"id": imp, "name": name, "rows": len(rows), "added": added, "skipped": len(rows) - added,
                        "first_day": first.isoformat() if first else None, "last_day": last.isoformat() if last else None,
                        "problems": problems})
        return out

    async def remove_import(self, import_id: int) -> bool:
        """An import and the trades it brought (a trade another file also
        held goes with it; import that file again to have it back)."""
        return (await self.pool.execute("DELETE FROM lens_imports WHERE id = $1", import_id)) != "DELETE 0"

    async def _trades(self) -> list[dict]:
        rows = await self.pool.fetch("SELECT * FROM lens_trades ORDER BY trade_date, executed_at, id")
        return [{"symbol": r["symbol"], "isin": r["isin"] or None, "trade_date": r["trade_date"], "exchange": r["exchange"],
                 "segment": r["segment"], "series": r["series"], "trade_type": r["trade_type"], "auction": r["auction"],
                 "quantity": r["quantity"], "price": r["price"], "trade_id": r["trade_id"], "order_id": r["order_id"],
                 "executed_at": r["executed_at"]} for r in rows]

    @staticmethod
    def _held_by_isin(h: Optional[dict]) -> dict:
        out = {}
        for r in (h or {}).get("equity") or []:
            if r.get("isin"):
                out[r["isin"]] = {"qty": r["qty"], "ltp": r["ltp"], "value": r["value"], "symbol": r["symbol"], "kind": "equity", "asset": r.get("asset")}
        for f in (h or {}).get("mf") or []:
            if f.get("isin"):
                out[f["isin"]] = {"qty": f["units"], "ltp": f["nav"], "value": f["value"], "symbol": f["fund"], "kind": "mf", "asset": f.get("asset")}
        return out

    async def trades_report(self) -> dict:
        """What the imported trades say (lens_trades.analyse) against what
        Kite holds now, and the shares day by day against the same money in
        Nifty once their prices are in."""
        from . import lens_trades
        imports = [dict(r) for r in await self.pool.fetch("SELECT * FROM lens_imports ORDER BY id DESC")]
        for i in imports:
            for k in ("first_day", "last_day", "imported_at"):
                i[k] = i[k].isoformat() if i[k] else None
        trades = await self._trades()
        if not trades:
            return {"imports": imports, "analysis": None, "curve": None, "holdings_error": None}
        h, err = None, None
        try:
            h = await self.holdings()
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        analysis = lens_trades.analyse(trades, self._held_by_isin(h), self.today())
        return {"imports": imports, "analysis": analysis, "curve": await self._trade_curve(trades, analysis),
                "holdings_error": err}

    async def _tokens_for(self, keys: list[dict]) -> dict[str, dict]:
        """{key: {token, symbol, exchange}} for traded shares: from what's
        kept, else Kite's instrument list for that exchange (once a day)."""
        known = {(r["exchange"] or "", r["tradingsymbol"]): int(r["instrument_token"]) for r in await self.pool.fetch(
            "SELECT instrument_token, tradingsymbol, exchange FROM lens_instruments")}
        out = {}
        for k in keys:
            tok = known.get((k["exchange"], k["symbol"]))
            if tok is None:
                try:
                    rows = await self._once_a_day(f"instruments:{k['exchange'] or 'NSE'}", "instruments", k["exchange"] or "NSE")
                except Exception:
                    rows = []
                tok = next((int(r["instrument_token"]) for r in rows or [] if r.get("tradingsymbol") == k["symbol"]), None)
            if tok is not None:
                out[k["key"]] = {"token": tok, "symbol": k["symbol"], "exchange": k["exchange"]}
        return out

    async def _trade_curve(self, trades: list[dict], analysis: dict) -> Optional[dict]:
        from . import lens_trades
        per = [p for p in analysis["holdings"] if p["kind"] == "equity"]
        if not per:
            return None
        keys = [{"key": p["key"], "symbol": p["symbol"], "exchange": p["exchange"] or "NSE",
                 "since": date.fromisoformat(p["first_day"]),
                 "until": None if p["open_qty"] > 1e-6 else date.fromisoformat(p["last_day"])} for p in per]
        toks = await self._tokens_for(keys)
        first = min(k["since"] for k in keys)
        want = [{"token": NIFTY, "symbol": NIFTY_NAME, "exchange": "NSE", "since": first}] + [
            {**toks[k["key"]], "since": k["since"], "until": k["until"]} for k in keys if k["key"] in toks]
        task = self.hist.get("trades_task")
        if task is not None and not task.done():
            p = self.hist.get("trades_progress") or {}
            return {"state": "loading", "done": p.get("done", 0), "total": p.get("total", len(want))}
        have = await lens_store.kept(self.pool, [w["token"] for w in want])
        now, mono = self.now(), time.monotonic()
        due = [w for w in want if lens_store.to_read((have.get(w["token"]) or {}).get("fetched_through"),
                                                      (have.get(w["token"]) or {}).get("first"), now, w.get("since"), w.get("until")) is not None
               and self.hist["retry"].get(w["token"], 0) <= mono]
        if due:
            self.hist["trades_progress"] = {"total": len(due), "done": 0}

            async def read():
                r = await lens_store.bring_up_to_date(self.pool, self.call, due, self.now(), self.hist["trades_progress"])
                for w in due:
                    if w["symbol"] in r["failed"]:
                        self.hist["retry"][w["token"]] = time.monotonic() + RETRY_FAILED_S
            self.hist["trades_task"] = asyncio.create_task(read(), name="lens-trade-prices")
            return {"state": "loading", "done": 0, "total": len(due)}
        candles = await lens_store.candles(self.pool, [w["token"] for w in want], first - timedelta(days=7))
        series = {k: [(c["date"], c["close"]) for c in candles.get(v["token"]) or []] for k, v in toks.items()}
        c = lens_trades.value_curve(trades, series, [(c["date"], c["close"]) for c in candles.get(NIFTY) or []], self.today())
        if c is None:
            return {"state": "error", "error": "Kite gave no NIFTY 50 prices back to your first trade"}
        return {"state": "ready", **c, "unknown": sorted(k["symbol"] for k in keys if k["key"] not in toks)}

    # ── Inspector (app/lens_inspect.py) ────────────────────────────────

    async def _inspect_inputs(self) -> tuple:
        """The trades, the holdings (None without Kite), the daily prices
        kept for today's shares and Nifty, and the traded shares' prices
        for the line against Nifty. Nothing here is read from Kite but the
        holdings (kept for the day)."""
        from . import lens_trades as T
        trades = await self._trades()
        h, err = None, None
        try:
            h = await self.holdings()
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        today = self.today()
        tokens = [NIFTY] + [r["token"] for r in (h or {}).get("equity") or [] if r.get("token")]
        first = min((t["trade_date"] for t in trades), default=today - timedelta(days=lens_store.FIRST_READ_DAYS))
        since = min(first, today - timedelta(days=lens_store.FIRST_READ_DAYS))
        known = {(r["exchange"] or "", r["tradingsymbol"]): int(r["instrument_token"]) for r in await self.pool.fetch(
            "SELECT instrument_token, tradingsymbol, exchange FROM lens_instruments")}
        traded = {T.key_of(t): known.get((t.get("exchange") or "NSE", t["symbol"])) for t in trades if T.kind_of(t) == "equity"}
        candles = await lens_store.candles(self.pool, list({*tokens, *[v for v in traded.values() if v]}), since)
        curve_prices = {k: [(c["date"], c["close"]) for c in candles.get(tok) or []] for k, tok in traded.items() if tok}
        curve_prices = {k: v for k, v in curve_prices.items() if v}
        return trades, h, err, candles, curve_prices

    async def inspect(self) -> dict:
        trades, h, err, candles, _ = await self._inspect_inputs()
        return inspect_answer(trades, h, err, candles, self.today())

    async def whatif(self, without: set) -> dict:
        from . import lens_inspect as I
        trades, h, err, candles, curve_prices = await self._inspect_inputs()
        if h is None:
            return {"error": f"Kite isn’t connected, so today’s holdings can’t be read ({err})"}
        return I.compare(trades, h, candles, candles.get(NIFTY) or [], self.today(), set(without), curve_prices)
