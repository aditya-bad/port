"""
live_deploy — Geek mode's Volatility view, for every strategy: how India
VIX (app/vix.py) bears on this deployment.

* every trade with VIX when it opened and when it closed, and the results
  by VIX at entry and by what VIX did during the trade;
* ₹ per VIX point: the slope of the deployment's daily P&L (its end of day
  equity, so open positions count) against VIX's daily change;
* expected vs actual move: each day, the move VIX priced for the next day
  (VIX / √365 per cent) against how far the index actually closed from
  the day before, and the deployment's P&L on days the market moved more
  or less than priced;
* week by week: VIX's open, close and high, the index's move, the P&L;
* each option's implied volatility from its own fill, against VIX, and
  where each trade's P&L came from: time decay, the index's move, and
  volatility (app/geek/optionmath.attribute).

Whether a deployment sells or buys premium decides which way VIX cuts,
so each trade says which it did. VIX is built from Nifty options: for a
BankNifty, Sensex or Bankex deployment it is a stand-in, and the view
says so. VIX and the index come from Kite's history when the view is
opened (app/vix.py: in memory, never stored). Read only.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Optional

from ..db import queries
from ..vix import expected_move_pct
from . import common
from .common import IST, Ctx, _iso, _ist, label

NIFTY_TOKEN = 256265
MIN_DAYS = 10          # days with both a P&L and a VIX change, for ₹ per VIX point
BANDS = (["Under 12", "12–15", "15–18", "18–22", "Over 22"], [12, 15, 18, 22])
CHANGE = (["Fell over 1 point", "Within a point", "Rose over 1 point"], [-1, 1])
MOVED = (["Under half what VIX priced", "Half to all of it", "Up to 1.5× it", "Over 1.5× it"], [0.5, 1.0, 1.5])


def bucket(v, spec) -> Optional[str]:
    if v is None:
        return None
    labels, edges = spec
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


def ols(xs: list, ys: list) -> Optional[dict]:
    """Least squares y = a + b·x: {slope, intercept, r, n}, or None."""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return {"slope": round(b, 2), "intercept": round(my - b * mx, 2),
            "r": round(sxy / math.sqrt(sxx * syy), 3) if syy else 0.0, "n": n}


def groups(rows: list[dict], specs: list[tuple]) -> list[dict]:
    """Closed rows added up per bucket: specs = [(key, label, fn(row) -> bucket, order)]."""
    out = []
    for key, name, fn, order in specs:
        agg: dict = {}
        for r in rows:
            if r.get("pnl") is None:
                continue
            k = fn(r)
            if k is None:
                continue
            g = agg.setdefault(k, {"label": k, "n": 0, "wins": 0, "pnl": 0.0})
            g["n"] += 1
            g["wins"] += 1 if r["pnl"] > 0 else 0
            g["pnl"] = round(g["pnl"] + r["pnl"], 2)
        if not agg:
            continue
        for g in agg.values():
            g["avg"] = round(g["pnl"] / g["n"], 2)
            g["win_rate"] = round(g["wins"] / g["n"] * 100, 1)
        keys = sorted(agg, key=lambda k: order.index(k) if k in order else len(order))
        out.append({"key": key, "label": name, "buckets": [agg[k] for k in keys], "covered": sum(g["n"] for g in agg.values())})
    return out


def daily(vix_days: dict, idx_days: dict, equity: dict) -> list[dict]:
    """Per market day: VIX open/close and change, the move it priced the
    evening before, the index's actual close-to-close move, and the
    deployment's P&L (change in end-of-day equity). Pure.
    vix_days {day: (o, h, l, c)}, idx_days {day: (o, h, l, c)}, equity {day: total value}."""
    days = sorted(set(vix_days) | set(idx_days) | set(equity))
    out, prev_vix, prev_idx, prev_eq = [], None, None, None
    for d in days:
        v, x, e = vix_days.get(d), idx_days.get(d), equity.get(d)
        row = {"day": d.isoformat(), "vix_open": v[0] if v else None, "vix_close": v[3] if v else None,
               "vix_high": v[1] if v else None,
               "dvix": round(v[3] - prev_vix, 2) if v and prev_vix is not None else None,
               "implied_pct": round(expected_move_pct(prev_vix), 3) if prev_vix else None,
               "move_pct": round(abs(x[3] / prev_idx - 1) * 100, 3) if x and prev_idx else None,
               "index_close": x[3] if x else None,
               "pnl": round(e - prev_eq, 2) if e is not None and prev_eq is not None else None}
        row["ratio"] = round(row["move_pct"] / row["implied_pct"], 2) if row["move_pct"] is not None and row["implied_pct"] else None
        out.append(row)
        prev_vix = v[3] if v else prev_vix
        prev_idx = x[3] if x else prev_idx
        prev_eq = e if e is not None else prev_eq
    return out


def weekly(days: list[dict], trades: list[dict]) -> list[dict]:
    """Days and trades -> weeks (Monday first), oldest first. Pure."""
    by: dict = defaultdict(list)
    for r in days:
        d = date.fromisoformat(r["day"])
        by[d - timedelta(days=d.weekday())].append(r)
    closed: dict = defaultdict(list)
    for t in trades:
        if t.get("pnl") is not None and t.get("end"):
            d = datetime.fromisoformat(t["end"]).astimezone(IST).date()
            closed[d - timedelta(days=d.weekday())].append(t)
    out = []
    prev_close_idx = None
    for w in sorted(by):
        rs = by[w]
        vs = [r for r in rs if r["vix_close"] is not None]
        xs = [r for r in rs if r["index_close"] is not None]
        pnl = [r["pnl"] for r in rs if r["pnl"] is not None]
        ts = closed.get(w, [])
        row = {"week": w.isoformat(),
               "vix_open": vs[0]["vix_open"] if vs else None, "vix_close": vs[-1]["vix_close"] if vs else None,
               "vix_high": max(r["vix_high"] for r in vs) if vs else None,
               "index_move_pct": round((xs[-1]["index_close"] / prev_close_idx - 1) * 100, 2) if xs and prev_close_idx else None,
               "pnl": round(sum(pnl), 2) if pnl else None, "trades": len(ts), "wins": sum(1 for t in ts if t["pnl"] > 0)}
        row["dvix"] = round(row["vix_close"] - row["vix_open"], 2) if vs else None
        out.append(row)
        if xs:
            prev_close_idx = xs[-1]["index_close"]
    return out


def filters(rows: list[dict]) -> list[dict]:
    """What-if entry filters, in hindsight: for each whole VIX level in the
    range traded, the closed trades' P&L had it traded only at or above
    it, and only below it. Pure."""
    rs = [r for r in rows if r.get("pnl") is not None and r.get("vix_in") is not None]
    if len(rs) < 6:
        return []
    out = []
    for lvl in range(math.ceil(min(r["vix_in"] for r in rs)), math.floor(max(r["vix_in"] for r in rs)) + 1):
        above = [r["pnl"] for r in rs if r["vix_in"] >= lvl]
        below = [r["pnl"] for r in rs if r["vix_in"] < lvl]
        if not above or not below:
            continue
        out.append({"level": lvl, "above_n": len(above), "above_pnl": round(sum(above), 2),
                    "above_win": round(sum(1 for x in above if x > 0) / len(above) * 100, 1),
                    "below_n": len(below), "below_pnl": round(sum(below), 2),
                    "below_win": round(sum(1 for x in below if x > 0) / len(below) * 100, 1)})
    return out


def at_this_level(rows: list[dict], vix: Optional[float]) -> Optional[dict]:
    """How its trades did when VIX was within a point of where it is now."""
    if vix is None:
        return None
    rs = [r for r in rows if r.get("pnl") is not None and r.get("vix_in") is not None and abs(r["vix_in"] - vix) <= 1]
    if not rs:
        return {"n": 0, "low": round(vix - 1, 2), "high": round(vix + 1, 2)}
    return {"n": len(rs), "low": round(vix - 1, 2), "high": round(vix + 1, 2), "pnl": round(sum(r["pnl"] for r in rs), 2),
            "avg": round(sum(r["pnl"] for r in rs) / len(rs), 2), "win_rate": round(sum(1 for r in rs if r["pnl"] > 0) / len(rs) * 100, 1)}


def spikes(days: list[dict], jump: float = 0.10) -> Optional[dict]:
    """P&L on days VIX closed 10%+ above the day before, against the rest."""
    prev, spike, rest = None, [], []
    for r in days:
        if r["vix_close"] is not None and prev and r["pnl"] is not None:
            (spike if r["vix_close"] / prev - 1 >= jump else rest).append(r["pnl"])
        if r["vix_close"] is not None:
            prev = r["vix_close"]
    if not spike or not rest:
        return None
    return {"spike_n": len(spike), "spike_avg": round(sum(spike) / len(spike), 2), "spike_worst": round(min(spike), 2),
            "rest_n": len(rest), "rest_avg": round(sum(rest) / len(rest), 2)}


def spot_of(meta: dict) -> Optional[float]:
    """The index level a fill recorded, if it did."""
    m = meta or {}
    for v in ((m.get("context") or {}).get("spot"), m.get("entry_spot"), (m.get("target_basis") or {}).get("underlying_price"),
              (m.get("trigger_values") or {}).get("underlying_price")):
        if v:
            return float(v)
    return None


def expiry_of(meta: dict, contract_expiry, symbol: str) -> Optional[date]:
    """A leg's expiry: what the fill recorded, else the contract list, else
    a weekly symbol's own date (a monthly symbol only says the month)."""
    e = (meta or {}).get("expiry")
    if e:
        try:
            return date.fromisoformat(str(e)[:10])
        except ValueError:
            pass
    if contract_expiry:
        return contract_expiry if isinstance(contract_expiry, date) else date.fromisoformat(str(contract_expiry)[:10])
    from ..candles import _WEEKLY, parse_contract
    if _WEEKLY.match(symbol or ""):
        c = parse_contract(symbol)
        return c["expiry"] if c else None
    return None


def leg_of(p: dict, lots: list[dict], expiry: Optional[date], spot_at) -> Optional[dict]:
    """One position as an option leg: kind, strike, expiry, entry (time,
    index, premium), exit if closed, quantity and side. None when it isn't
    an option or the strike, expiry or index level can't be known."""
    from ..candles import parse_contract
    c = parse_contract(p["symbol"])
    if not c or c["instrument_type"] not in ("CE", "PE") or not c.get("strike") or expiry is None:
        return None
    opens = [x for x in lots if x["opening"]]
    closes = [x for x in lots if not x["opening"]]
    if not opens:
        return None
    t0 = opens[0]["executed_at"]
    s0 = spot_of(opens[0]["metadata"]) or spot_at(t0)
    if not s0:
        return None
    qty = sum(float(x["qty"]) for x in opens)
    leg = {"kind": c["instrument_type"], "k": c["strike"], "expiry": expiry, "t0": t0, "s0": s0,
           "p0": float(p["avg_entry_price"]), "qty": qty, "sold": p["side"] == "short", "symbol": p["symbol"]}
    if p["closed_at"] is not None and closes:
        cq = sum(float(x["qty"]) for x in closes)
        leg["t1"] = closes[-1]["executed_at"]
        leg["p1"] = sum(float(x["price"]) * float(x["qty"]) for x in closes) / cq if cq else None
        leg["s1"] = spot_of(closes[-1]["metadata"]) or spot_at(leg["t1"])
    return leg


def attribution_total(rows: list[dict]) -> Optional[dict]:
    """The split added up over every trade that has one, against all the
    closed trades' P&L (what it couldn't split: legs without a known
    strike, expiry or index level, or with no implied volatility)."""
    split = [r for r in rows if r.get("attr")]
    closed = [r for r in rows if r.get("pnl") is not None]
    if not split:
        return None
    out = {k: round(sum(r["attr"][k] for r in split), 2) for k in ("time", "move", "vol", "total")}
    out.update({"n": len(split), "closed": len(closed), "pnl_all": round(sum(r["pnl"] for r in closed), 2)})
    return out


def side_of(fills: list[dict]) -> str:
    """Sold or bought premium, net: what the opening fills took in against
    what they paid. A hedged strangle or a credit spread takes in more
    than it pays (a seller, hurt by VIX rising); a calendar pays more for
    the far expiry than it takes for the near one (a buyer)."""
    credit = sum((1 if f["action"] == "sell" else -1) * float(f["price"]) * float(f["qty"]) for f in fills if f["opening"])
    return "sold" if credit > 0 else "bought" if credit < 0 else "both"


async def volatility(ctx: Ctx) -> dict:
    now = (ctx.now or datetime.now(IST)).astimezone(IST)
    dep = ctx.dep
    toks = ctx.config.get("instrument_tokens") or []
    index_token = int(toks[0]) if toks else None
    created = _ist(dep.get("created_at")) or now - timedelta(days=365)
    b = await common.book(ctx)
    pos = {p["id"]: p for p in b["positions"]}
    first = min([created] + [_ist(p["opened_at"]) for p in b["positions"]])
    start = datetime.combine(first.date() - timedelta(days=7), time(0), IST)

    # VIX and the index, from Kite (app/vix.py; kept in memory, never
    # stored): 15-minute VIX for each trade's entry and exit, daily for
    # the rest.
    from .. import vix as vixmod
    from ..options import NoKiteSession
    svc = vixmod.state.get("service")
    v15, vday, iday, i15, vix_error = [], [], [], [], None
    try:
        if svc is None:
            raise NoKiteSession("no VIX service")
        v15 = await svc.vix(start.date(), now.date(), "15minute", now)
        vday = await svc.vix(start.date(), now.date(), "day", now)
        if index_token is not None:
            iday = await svc.candles(index_token, start.date(), now.date(), "day", now)
            i15 = await svc.candles(index_token, start.date(), now.date(), "15minute", now)
        if svc.token is None:
            vix_error = svc.last_error
    except NoKiteSession:
        vix_error = "Kite isn't logged in, so VIX history can't be fetched. Log in and this view fills in."
    vt = [r["t"] + timedelta(minutes=15) for r in v15]   # a candle's close is known at its end
    vc = [r["c"] for r in v15]

    def vix_at(t) -> Optional[float]:
        if t is None:
            return None
        i = bisect.bisect_right(vt, t) - 1
        return round(vc[i], 2) if i >= 0 and t - vt[i] <= timedelta(days=4) else None

    it = [r["t"] + timedelta(minutes=15) for r in i15]
    ic = [r["c"] for r in i15]

    def spot_at(t) -> Optional[float]:
        if t is None or not it:
            return None
        i = bisect.bisect_right(it, t) - 1
        if i < 0:   # before the first finished candle: that candle's open
            return i15[0]["o"] if it[0] - t <= timedelta(minutes=15) else None
        return ic[i] if t - it[i] <= timedelta(days=4) else None

    # each option leg's strike, expiry and index level, for its implied volatility
    from .optionmath import attribute, implied_vol, years_to
    lots_by: dict = defaultdict(list)
    for lot in b["lots"]:
        lots_by[lot["position_id"]].append(lot)
    contract_exp = {r["instrument_token"]: r["expiry"] for r in await ctx.pool.fetch(
        "SELECT instrument_token, expiry FROM contracts WHERE instrument_token = ANY($1::bigint[])",
        list({int(p["instrument_token"]) for p in b["positions"]}))} if b["positions"] else {}
    legs = {}
    for p in b["positions"]:
        ls = sorted(lots_by.get(p["id"], []), key=lambda x: x["executed_at"])
        first_meta = ls[0]["metadata"] if ls else {}
        lg = leg_of(p, ls, expiry_of(first_meta, contract_exp.get(int(p["instrument_token"])), p["symbol"]), spot_at)
        if lg:
            lg["iv0"] = implied_vol(lg["kind"], lg["p0"], lg["s0"], lg["k"], years_to(lg["expiry"], lg["t0"]))
            if lg.get("p1") is not None and lg.get("s1"):
                lg["attr"] = attribute(lg["kind"], lg["k"], lg["expiry"], lg["t0"], lg["s0"], lg["p0"],
                                       lg["t1"], lg["s1"], lg["p1"], lg["qty"], lg["sold"])
            legs[p["id"]] = lg

    vix_days = {r["t"].date(): (r["o"], r["h"], r["l"], r["c"]) for r in vday}
    idx_days = {r["t"].date(): (r["o"], r["h"], r["l"], r["c"]) for r in iday}
    equity = {}
    for r in await queries.list_snapshots(ctx.pool, dep["id"], limit=1000):
        equity[_ist(r["snapshot_at"]).date()] = float(r["total_value"])
    pnl_source = "equity"
    if len(equity) < 3:   # no end-of-day snapshots (yet): what was booked each day
        pnl_source, booked = "booked", defaultdict(float)
        for p in b["positions"]:
            if p["closed_at"] is not None:
                booked[_ist(p["closed_at"]).date()] += float(p["realized_pnl"] or 0)
        run, equity = ctx.capital, {}
        for d in sorted(set(vix_days) | set(booked)):
            if d >= first.date():
                run += booked.get(d, 0.0)
                equity[d] = run

    rows = []
    for tr in common.trades_of(b):
        ps = [pos[i] for i in tr["ids"] if i in pos]
        if not ps:
            continue
        is_open = any(p["closed_at"] is None for p in ps)
        end = None if is_open else max(p["closed_at"] for p in ps)
        fills = [f for a in tr["actions"] for f in a["fills"]]
        vin = (tr["first"]["metadata"].get("context") or {}).get("vix") or vix_at(tr["t"])
        vout = vix_at(end) if end else None
        lgs = [legs[p["id"]] for p in ps if p["id"] in legs and legs[p["id"]].get("iv0")]
        w = sum(lg["p0"] * lg["qty"] for lg in lgs)
        iv = round(sum(lg["iv0"] * lg["p0"] * lg["qty"] for lg in lgs) / w * 100, 2) if w else None
        # where its P&L came from: only when every leg is closed and could be split
        attr = None
        if not is_open and ps and all(p["id"] in legs and legs[p["id"]].get("attr") for p in ps):
            attr = {k: round(sum(legs[p["id"]]["attr"][k] for p in ps), 2) for k in ("time", "move", "vol", "total")}
        rows.append({"iv": iv, "iv_legs": len(lgs), "attr": attr,
                     "t": _iso(tr["t"]), "end": _iso(end), "label": label(tr["trigger"]), "symbols": [p["symbol"] for p in ps],
                     "side": side_of(fills), "open": is_open,
                     "pnl": None if is_open else round(sum(float(p["realized_pnl"] or 0) for p in ps), 2),
                     "vix_in": vin, "vix_out": vout,
                     "dvix": round(vout - vin, 2) if vin is not None and vout is not None else None})
    for r in rows:
        r["iv_spread"] = round(r["iv"] - r["vix_in"], 2) if r["iv"] is not None and r["vix_in"] is not None else None
    known = [r for r in rows if r["vix_in"] is not None]
    thirds = common.thirds([r["vix_in"] for r in known if r["pnl"] is not None])
    if thirds and thirds[0] >= thirds[1]:
        thirds = None   # VIX barely varied: no thirds to tell apart
    specs = [("band", "VIX at entry", lambda r: bucket(r["vix_in"], BANDS), BANDS[0]),
             ("change", "What VIX did during the trade", lambda r: bucket(r["dvix"], CHANGE), CHANGE[0])]
    if thirds:
        a, c = thirds
        order = [f"Low · up to {a:g}", f"Middle · {a:g}–{c:g}", f"High · above {c:g}"]
        specs.insert(1, ("thirds", "VIX at entry, in thirds of this deployment’s trades",
                         lambda r: None if r["vix_in"] is None else order[0] if r["vix_in"] <= a else order[1] if r["vix_in"] <= c else order[2], order))

    cuts = common.thirds([r["iv_spread"] for r in known if r["pnl"] is not None and r["iv_spread"] is not None])
    if cuts and cuts[0] < cuts[1]:
        a2, c2 = cuts
        f = lambda x: f"{'+' if x >= 0 else '−'}{abs(x):g}"  # noqa: E731
        rich = [f"Cheapest third · up to {f(a2)}", f"Middle · {f(a2)} to {f(c2)}", f"Richest third · above {f(c2)}"]
        specs.append(("rich", "Premium against VIX: implied volatility less VIX, in points",
                      lambda r: None if r["iv_spread"] is None else rich[0] if r["iv_spread"] <= a2 else rich[1] if r["iv_spread"] <= c2 else rich[2], rich))

    days = daily(vix_days, idx_days, equity)
    pairs = [(r["dvix"], r["pnl"]) for r in days if r["dvix"] is not None and r["pnl"] is not None]
    per_point = ols([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= MIN_DAYS else None
    moved = [r for r in days if r["ratio"] is not None and r["pnl"] is not None]
    move_groups = []
    for lab in MOVED[0]:
        rs = [r for r in moved if bucket(r["ratio"], MOVED) == lab]
        if rs:
            move_groups.append({"label": lab, "n": len(rs), "pnl": round(sum(r["pnl"] for r in rs), 2),
                                "avg": round(sum(r["pnl"] for r in rs) / len(rs), 2),
                                "wins": sum(1 for r in rs if r["pnl"] > 0)})
    for g in move_groups:
        g["win_rate"] = round(g["wins"] / g["n"] * 100, 1)

    year = sorted(c for d, (_, _, _, c) in vix_days.items() if d >= now.date() - timedelta(days=365))
    live = svc.last() if svc is not None else None
    cur = live["price"] if live else vc[-1] if vc else None
    sides = {r["side"] for r in rows}
    closed_known = [r for r in known if not r["open"]]
    here = at_this_level(closed_known, round(cur, 2) if cur is not None else None)
    rows.reverse()
    return {
        "now": {"vix": round(cur, 2) if cur is not None else None,
                "live": bool(live),
                "percentile": round(bisect.bisect_left(year, cur) / len(year) * 100) if year and cur is not None else None,
                "expected_move_pct": round(expected_move_pct(cur), 2) if cur else None,
                "year_low": year[0] if year else None, "year_high": year[-1] if year else None},
        "side": next(iter(sides)) if len(sides) == 1 else "both" if sides else None,
        "proxy": index_token is not None and index_token != NIFTY_TOKEN, "pnl_source": pnl_source,
        "has_vix": bool(vday), "has_index": bool(idx_days), "error": vix_error,
        "rows": rows, "groups": groups([r for r in known if not r["open"]], specs),
        "per_point": per_point, "here": here, "filters": filters(closed_known), "spikes": spikes(days),
        "days": days[-260:], "moved": move_groups, "weeks": weekly(days, rows)[-26:],
        "attribution": attribution_total(rows),
        "summary": {"trades": len(rows), "with_vix": len(known), "with_iv": sum(1 for r in rows if r["iv"] is not None),
                    "days_moved_more": sum(1 for r in moved if r["ratio"] > 1), "days_compared": len(moved)},
    }
