"""
live_deploy — Geek mode's Replay: how a cycle (or a continuous chain of
them) actually went, played back step by step. Every deployment has it.

What's replayed, on one time axis:
  - the index (with each open leg's strike drawn against it) and India
    VIX, from Kite's history (app/vix.py; in memory, never stored);
  - every leg's price: its stored 1-minute candles (app/candles.py), else
    the app's own 5-minute marks;
  - the P&L at each step: what was booked plus the open legs at that
    step's prices, and where it came from — time decay, the index's move
    and volatility — by revaluing each option leg one change at a time at
    the volatility its entry price implied (app/geek/optionmath.py);
  - each event, labelled for what it was: a new cycle (or entry), an
    adjustment, a roll, a hedge, a leg closed, the exit — with the rule
    that fired, the numbers it saw, the fills, what it booked, and how
    the index, VIX and P&L had moved since the event before.

An episode is the strategy's own unit: a cycle (the strangle), a trading
day (the adjusting straddles), else a trade as Geek mode counts them
(common.trades_of) — so adjustments and rolls stay inside their episode. A chain is consecutive episodes, played
as one: nights and weekends are skipped (steps are market candles).

Params: from, to (episode numbers, 1-based; default the latest one).
"""

from __future__ import annotations

import bisect
import math
from datetime import date, datetime, time, timedelta
from typing import Optional

from . import common
from .common import IST, Ctx, _ist, _iso, label, saw
from .optionmath import fwd_implied_vol, fwd_price, implied_vol, price as bs_price, years_to
from .volatility import expiry_of, spot_of

INTERVAL_MIN = {"minute": 1, "5minute": 5, "15minute": 15, "60minute": 60}


def market_minutes(a: datetime, b: datetime) -> float:
    a, b = _ist(a), _ist(b)
    total, d = 0.0, a.date()
    while d <= b.date():
        if d.weekday() < 5:
            lo = max(a, datetime.combine(d, time(9, 15), IST))
            hi = min(b, datetime.combine(d, time(15, 30), IST))
            if hi > lo:
                total += (hi - lo).total_seconds() / 60
        d += timedelta(days=1)
    return total


def pick_interval(minutes: float) -> str:
    """The finest candles that keep a replay under ~1,500 steps."""
    if minutes <= 1500:
        return "minute"
    if minutes <= 7500:
        return "5minute"
    if minutes <= 22500:
        return "15minute"
    return "60minute"


def _qty(lots: list) -> float:
    return sum(float(x["qty"]) for x in lots if x["opening"])


def _cycle_of(p: dict) -> Optional[int]:
    c = (p.get("metadata") or {}).get("cycle_id")
    try:
        return int(c) if c is not None else None
    except (TypeError, ValueError):
        return None


def group_by_of(strategy_name: str) -> Optional[str]:
    from ..strategies.registry import get_strategy_class
    cls = get_strategy_class(strategy_name)
    return getattr(cls, "ADJUSTMENT_GROUP_BY", None) if cls else None


def units(b: dict, group_by: Optional[str]) -> list[dict]:
    """The episodes to replay, oldest first, each {t, trigger, ids}: a
    strategy's own unit where it has one — its cycle (positions'
    cycle_id) or its trading day — so adjustments and rolls stay inside
    it; otherwise a trade as Geek mode counts them (common.trades_of)."""
    if group_by not in ("cycle_id", "day"):
        return common.trades_of(b)
    first_lot: dict = {}
    for lot in b["lots"]:
        if lot["opening"]:
            first_lot.setdefault(lot["position_id"], lot)
    groups: dict = {}
    for p in sorted(b["positions"], key=lambda p: p["opened_at"]):
        key = _cycle_of(p) if group_by == "cycle_id" else _ist(p["opened_at"]).date()
        groups.setdefault(key if key is not None else ("leg", p["id"]), []).append(p)
    out = []
    for ps in groups.values():
        lot = first_lot.get(ps[0]["id"])
        out.append({"t": ps[0]["opened_at"], "trigger": lot["trigger"] if lot else "trade", "ids": [p["id"] for p in ps]})
    return sorted(out, key=lambda u: u["t"])


def episodes(b: dict, now: datetime, group_by: Optional[str] = None) -> list[dict]:
    """Every episode, oldest first: when, how many actions of each kind,
    what it booked, the cycle number where there is one."""
    pos = {p["id"]: p for p in b["positions"]}
    out = []
    for i, t in enumerate(units(b, group_by)):
        ids = set(t["ids"])
        legs = [pos[x] for x in ids if x in pos]
        acts = [a for a in b["actions"] if any(f["position_id"] in ids for f in a["fills"])]
        closed = [p["closed_at"] for p in legs if p["closed_at"] is not None]
        is_open = any(p["closed_at"] is None for p in legs)
        cyc = next((c for c in (_cycle_of(p) for p in legs) if c is not None), None)
        kinds = [kind_of(a, i, acts, legs) for a in acts]
        fixed = common.repaired_ids(b["lots"])
        out.append({
            "n": i + 1, "cycle": cyc,
            "label": f"Cycle {cyc}" if cyc is not None else
                     f"Session {_ist(t['t']):%d %b}" if group_by == "day" else f"Trade {i + 1}",
            "t0": _iso(t["t"]), "t1": None if is_open else _iso(max(closed)) if closed else None, "open": is_open,
            "trigger": label(t["trigger"]), "legs": len(legs),
            "adjustments": sum(1 for k in kinds if k in ("adjustment", "roll", "added", "hedge", "leg_closed")),
            "booked": round(sum(float(p["realized_pnl"]) for p in legs if p["closed_at"] is not None), 2),
            "repaired": any(p["id"] in fixed for p in legs),
        })
    return out


def kind_of(a: dict, _n: int, acts: list, legs: list) -> str:
    """What one action was, within its episode: new (the first), an
    adjustment or roll (closes and opens), a hedge or added leg (opens
    only), a leg closed or the exit (closes only; the exit leaves nothing
    open)."""
    trig = (a["trigger"] or "").lower()
    if a is acts[0]:
        return "new"
    if a["kind"] == "switch":
        return "roll" if "roll" in trig else "adjustment"
    if a["kind"] == "entry":
        return "hedge" if "hedge" in trig else "added"
    t = a["t_last"]
    still = [p for p in legs if p["opened_at"] <= t and (p["closed_at"] is None or p["closed_at"] > t)]
    return "leg_closed" if still else "exit"


KIND_LABEL = {"new": "New cycle", "entry": "Entry", "adjustment": "Adjustment", "roll": "Roll", "hedge": "Hedge",
              "added": "Leg added", "leg_closed": "Leg closed", "exit": "Exit"}


def _opt_kind(symbol: str) -> Optional[str]:
    return symbol[-2:] if symbol and symbol[-2:] in ("CE", "PE") else None


def strike_of(meta: Optional[dict], parsed: dict) -> Optional[float]:
    """A leg's strike: what its fill recorded, else what its symbol says."""
    m = meta or {}
    for v in (m.get("strike"), (m.get("target_basis") or {}).get("selected_strike"), parsed.get("strike")):
        try:
            if v:
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def bar_of(t: datetime, step: timedelta) -> datetime:
    """The start of the bar `t` falls in, on a grid anchored at 09:15."""
    base = t.replace(hour=9, minute=15, second=0, microsecond=0)
    return base + step * math.floor((t - base) / step)


class Series:
    """A price over time, read as of any moment (the last value at or
    before it)."""

    def __init__(self, pts: list[tuple[datetime, float]]):
        pts = sorted(pts)
        self.t = [p[0] for p in pts]
        self.v = [p[1] for p in pts]

    def at(self, t: datetime, stale: timedelta = timedelta(days=5)) -> Optional[float]:
        i = bisect.bisect_right(self.t, t) - 1
        if i < 0 or t - self.t[i] > stale:
            return None
        return self.v[i]

    def __bool__(self):
        return bool(self.t)


def split_leg(leg: dict, t1: datetime, s1: float, p1: float) -> Optional[tuple[float, float, float]]:
    """(time decay, the market's move, volatility) of one leg from entry to
    (t1, s1, p1), in rupees for its side and size; None without an
    implied volatility at entry. A leg with its future (leg["fwd"]) is
    valued off it with Black-76 — s0/s1 are then the future's prices —
    else off the spot."""
    iv = leg.get("iv")
    if iv is None or s1 is None or p1 is None:
        return None
    k, kind, exp = leg["k"], leg["kind"], leg["expiry"]
    T0, T1 = years_to(exp, leg["t0"]), years_to(exp, t1)
    value = fwd_price if leg.get("fwd") else bs_price
    b0 = value(kind, leg["s0"], k, T0, iv)
    bt = value(kind, leg["s0"], k, T1, iv)
    bm = value(kind, s1, k, T1, iv)
    sign = (-1.0 if leg["sold"] else 1.0) * leg["qty"]
    return ((bt - b0) * sign, (bm - bt) * sign, (p1 - bm + b0 - leg["p0"]) * sign)


def build(b: dict, sel: list[dict], index: list, vix: list, prices: dict, step: timedelta,
          contract_exp: dict, now: datetime, cycles: bool = False, fut: Optional[list] = None,
          fut_expiries: Optional[set] = None) -> dict:
    """The replay of the episodes `sel` (from common.trades_of): frames,
    legs and events. `index`/`vix`/`fut`: [{t, c}] candles at `step` (fut:
    the future that prices the legs, app/futures.py);
    `prices`: {position id: [(t, price)]}."""
    pos = {p["id"]: p for p in b["positions"]}
    ids = [i for t in sel for i in t["ids"]]
    legs = [pos[i] for i in dict.fromkeys(ids) if i in pos]
    idset = set(p["id"] for p in legs)
    lots = [x for x in b["lots"] if x["position_id"] in idset]
    fixed = {str(x) for x in common.repaired_ids(lots)}
    acts = [a for a in b["actions"] if any(f["position_id"] in idset for f in a["fills"])]
    start = min(_ist(t["t"]) for t in sel)
    end = max([_ist(p["closed_at"]) for p in legs if p["closed_at"] is not None] + [start])
    if any(p["closed_at"] is None for p in legs):
        end = max(end, now)

    # a candle's close is known at its end: each series is read as of then
    idx = Series([(_ist(c["t"]) + step, float(c["c"])) for c in index])
    vx = Series([(_ist(c["t"]) + step, float(c["c"])) for c in vix])
    fx = Series([(_ist(c["t"]) + step, float(c["c"])) for c in (fut or [])])
    ohlc = {_ist(c["t"]): c for c in index}
    # the steps: the index's candles across the window, else every price we have
    times = [t for t in ohlc if start - step < t <= end]
    if not times:
        times = sorted({_ist(t) for s in prices.values() for t, _ in s if start - step < _ist(t) <= end})
    if not times:   # no prices at all: step through the events themselves (booked P&L only)
        times = [_ist(a["t"]) - step for a in acts if start - step < _ist(a["t"]) <= end + step]
    else:           # an event no candle covers (a gap, a stall) gets a bar of its own
        have = sorted(times)
        for a in acts:
            t = _ist(a["t"])
            k = bisect.bisect_right(have, t) - 1
            if k < 0 or t >= have[k] + step:
                times.append(bar_of(t, step))
    times = sorted(set(times))

    # each leg: entry, exit, size, strike, and its implied volatility at entry
    from ..candles import parse_contract
    L = []
    for p in legs:
        mine = [x for x in lots if x["position_id"] == p["id"]]
        opens = [x for x in mine if x["opening"]]
        closes = [x for x in mine if not x["opening"]]
        c = parse_contract(p["symbol"]) or {}
        t0 = _ist(p["opened_at"])
        s0 = (spot_of(opens[0]["metadata"]) if opens else None) or idx.at(t0)
        leg = {"id": str(p["id"]), "symbol": p["symbol"], "side": p["side"], "sold": p["side"] == "short",
               "qty": _qty(mine) or float(p["qty"]), "p0": float(p["avg_entry_price"]), "t0": t0, "s0": s0,
               "t1": _ist(p["closed_at"]), "realized": float(p["realized_pnl"]) if p["closed_at"] is not None else None,
               "kind": c.get("instrument_type") or _opt_kind(p["symbol"]), "k": strike_of(p.get("metadata"), c), "cycle": _cycle_of(p),
               "p1": (sum(float(x["price"]) * float(x["qty"]) for x in closes) / sum(float(x["qty"]) for x in closes))
               if closes else None,
               "px": Series([(_ist(t), float(v)) for t, v in prices.get(p["id"], [])])}
        leg["expiry"] = expiry_of(opens[0]["metadata"] if opens else {}, contract_exp.get(int(p["instrument_token"])), p["symbol"]) \
            if leg["kind"] in ("CE", "PE") else None
        leg["iv"] = None
        # the future at entry: the last finished bar, else the bar it opened in
        f0 = (fx.at(t0, stale=timedelta(days=4)) or fx.at(t0 + step)) if fx else None
        # valued off the future only if it's this leg's own (app/futures.pick)
        leg["fwd"] = f0 is not None and (fut_expiries is None or leg["expiry"] in fut_expiries)
        leg["spot0"] = s0
        if leg["fwd"]:
            leg["s0"] = f0   # valued off its future from here on
        if leg["kind"] in ("CE", "PE") and leg["k"] and leg["expiry"] and leg["s0"]:
            T0 = years_to(leg["expiry"], t0)
            leg["iv"] = (fwd_implied_vol if leg["fwd"] else implied_vol)(leg["kind"], leg["p0"], leg["s0"], leg["k"], T0)
        L.append(leg)

    frames = []
    for T in times:
        asof = T + step                      # a candle's close is known at its end
        booked = openp = 0.0
        sp = [0.0, 0.0, 0.0]
        split_ok = True
        held = []
        s1 = idx.at(asof)
        f1 = fx.at(asof) if fx else None
        under = lambda g, t=None: (fx.at(t) if t else f1) if g["fwd"] else (idx.at(t) if t else s1)  # noqa: E731
        for j, g in enumerate(L):
            if g["t0"] > asof:
                continue
            if g["t1"] is not None and g["t1"] <= asof:
                booked += g["realized"]
                parts = split_leg(g, g["t1"], under(g, g["t1"]) or under(g), g["p1"])
            else:
                px = g["px"].at(asof)
                mark = px if px is not None else g["p0"]
                pnl = (mark - g["p0"]) * g["qty"] * (-1 if g["sold"] else 1)
                openp += pnl
                held.append([j, round(mark, 2), round(pnl, 2), px is None])
                parts = split_leg(g, asof, under(g), px) if px is not None else None
            if parts is None:
                split_ok = False
            else:
                sp = [a + b for a, b in zip(sp, parts)]
        c = ohlc.get(T)
        frames.append({"t": _iso(T), "idx": s1, "vix": vx.at(asof), "fut": f1, "booked": round(booked, 2),
                       "o": float(c["o"]) if c else None, "h": float(c["h"]) if c else None, "l": float(c["l"]) if c else None,
                       "pnl": round(booked + openp, 2), "legs": held,
                       "split": [round(x, 2) for x in sp] if split_ok and any(g["iv"] for g in L) else None})

    tt = [_ist(datetime.fromisoformat(f["t"])) for f in frames]

    def frame_at(t: datetime) -> int:
        i = bisect.bisect_right(tt, _ist(t)) - 1
        return max(0, i)

    # events, labelled within their own episode
    events = []
    by_ep = []
    for n, t in enumerate(sel):
        tids = set(t["ids"])
        eacts = [a for a in acts if any(f["position_id"] in tids for f in a["fills"])]
        elegs = [pos[i] for i in tids if i in pos]
        by_ep.append((n, eacts, elegs))
    seen = set()
    cyc_mode = cycles
    prev = None
    # an action shared by two episodes (a checkpoint exit and re-entry)
    # belongs to the one it starts
    owner: dict = {}
    for n, eacts, _ in by_ep:
        for a in eacts:
            if eacts and a is eacts[0]:
                owner[id(a)] = n
            else:
                owner.setdefault(id(a), n)
    for n, eacts, elegs in by_ep:
        for a in eacts:
            if id(a) in seen or owner[id(a)] != n:
                continue
            seen.add(id(a))
            k = kind_of(a, n, eacts, elegs)
            if k == "new" and not cyc_mode:
                k = "entry"
            fi = frame_at(a["t"])
            f = frames[fi] if frames else {}
            spot = next((spot_of(x["metadata"]) for x in a["fills"] if spot_of(x["metadata"])), None) or f.get("idx")
            vix_here = next(((x["metadata"].get("context") or {}).get("vix") for x in a["fills"]
                             if (x["metadata"].get("context") or {}).get("vix")), None) or f.get("vix")
            mine = [x for x in a["fills"] if x["position_id"] in idset]
            before = [x for x in a["fills"] if x["position_id"] not in idset]
            fill = lambda x: {"action": x["action"], "symbol": x["symbol"], "qty": float(x["qty"]),  # noqa: E731
                              "price": float(x["price"]), "opening": x["opening"], "pnl": x["pnl"]}
            booked = [x["pnl"] for x in mine if not x["opening"]]
            cyc = next((_cycle_of(pos[x["position_id"]]) for x in sorted(mine, key=lambda x: not x["opening"])
                        if x["position_id"] in pos), None)
            ev = {
                "t": _iso(a["t"]), "frame": fi, "kind": k, "label": KIND_LABEL[k], "episode": n,
                "rule": label(a["trigger"]), "why": saw(a["trigger_values"]),
                "fills": [fill(x) for x in mine],
                "booked": round(sum(booked), 2) if booked and all(v is not None for v in booked) else None,
                "idx": spot, "vix": vix_here, "pnl": f.get("pnl"), "cycle": cyc,
                # written by a stall repair (app/repair), not traded live
                "repaired": any("repair" in (x.get("metadata") or {}) for x in a["fills"]),
            }
            # the same action also closed the episode before (a checkpoint exit and re-entry)
            if before:
                bp = [x["pnl"] for x in before if not x["opening"]]
                ev["closed_before"] = {
                    "fills": [fill(x) for x in before],
                    "booked": round(sum(bp), 2) if bp and all(v is not None for v in bp) else None,
                    "cycle": next((_cycle_of(pos[x["position_id"]]) for x in before if x["position_id"] in pos), None),
                }
            if prev is not None:
                d = lambda k_: None if ev[k_] is None or prev[k_] is None else round(ev[k_] - prev[k_], 2)  # noqa: E731
                ev["since"] = {"idx": d("idx"), "vix": d("vix"), "pnl": d("pnl"), "t": prev["t"]}
            events.append(ev)
            prev = ev
    events.sort(key=lambda e: e["t"])
    by_leg: dict = {}
    for a in acts:
        for x in a["fills"]:
            pid = str(x["position_id"])
            if pid in {g["id"] for g in L}:
                e = next((ev for ev in events if ev["t"] == _iso(a["t"])), None)
                tag = {"kind": e["kind"] if e else None, "label": e["label"] if e else None, "rule": label(a["trigger"])}
                by_leg.setdefault(pid, {})["open" if x["opening"] else "close"] = tag

    return {
        "frames": frames,
        "legs": [{"symbol": g["symbol"], "side": g["side"], "qty": g["qty"], "entry": round(g["p0"], 2),
                  "kind": g["kind"], "strike": g["k"], "t0": _iso(g["t0"]), "t1": _iso(g["t1"]),
                  "exit": round(g["p1"], 2) if g["p1"] is not None else None, "realized": g["realized"],
                  "s0": round(g["spot0"], 2) if g["spot0"] else None,
                  "f0": round(g["s0"], 2) if g["fwd"] else None,
                  "opened_by": by_leg.get(g["id"], {}).get("open"), "closed_by": by_leg.get(g["id"], {}).get("close"),
                  "iv": round(g["iv"] * 100, 2) if g["iv"] else None, "cycle": g["cycle"],
                  "priced": bool(g["px"]), "repaired": g["id"] in fixed} for g in L],
        "events": events,
        "start": _iso(start), "end": _iso(end),
        "split_legs": sum(1 for g in L if g["iv"]),
        "unpriced": [g["symbol"] for g in L if not g["px"]],
    }


async def replay(ctx: Ctx) -> dict:
    now = (ctx.now or datetime.now(IST)).astimezone(IST)
    b = await common.book(ctx)
    gb = group_by_of(ctx.dep["strategy_name"])
    eps = episodes(b, now, gb)
    trades = units(b, gb)
    out = {"episodes": eps, "selected": None}
    if not trades:
        return out
    n = len(trades)

    def num(k, d):
        try:
            return max(1, min(n, int(ctx.params.get(k, d))))
        except (TypeError, ValueError):
            return d
    a = num("from", n)
    z = num("to", a)
    a, z = min(a, z), max(a, z)
    sel = trades[a - 1:z]
    out["selected"] = [a, z]

    pos = {p["id"]: p for p in b["positions"]}
    legs = [pos[i] for t in sel for i in t["ids"] if i in pos]
    start = min(_ist(t["t"]) for t in sel)
    end = max([_ist(p["closed_at"]) for p in legs if p["closed_at"] is not None] + [start])
    if any(p["closed_at"] is None for p in legs):
        end = max(end, now)
    interval = pick_interval(market_minutes(start, end))
    step = timedelta(minutes=INTERVAL_MIN[interval])
    lo, hi = start - step * 2, end + step

    # the index and VIX from Kite (in memory)
    toks = ctx.config.get("instrument_tokens") or []
    index_token = int(toks[0]) if toks else None
    from .. import vix as vixmod
    from ..options import NoKiteSession
    svc = vixmod.state.get("service")
    index, vix, market_error = [], [], None
    try:
        if svc is None:
            raise NoKiteSession("no VIX service")
        if index_token is not None:
            index = await svc.candles(index_token, lo.date(), hi.date(), interval, now)
        vix = await svc.vix(lo.date(), hi.date(), interval, now)
    except NoKiteSession:
        market_error = "Kite isn't logged in, so the index and VIX history can't be fetched. Log in and this fills in."
    except Exception as e:   # one source failing never hides the rest
        market_error = f"The index/VIX history couldn't be fetched ({e})."
    index = [c for c in index if lo <= _ist(c["t"]) <= hi]
    vix = [c for c in vix if lo <= _ist(c["t"]) <= hi]

    # each leg's prices: stored candles, else the app's marks
    prices: dict = {}
    if ctx.candles is not None:
        for p in legs:
            a0 = _ist(p["opened_at"]) - step
            z0 = (_ist(p["closed_at"]) if p["closed_at"] is not None else now) + step
            try:
                rows, _src, _err = await ctx.candles.series(int(p["instrument_token"]), a0, z0, interval, symbol=p["symbol"])
            except Exception:
                rows = []
            if rows:
                prices[p["id"]] = [(datetime.fromtimestamp(r["t"] / 1000, IST) + step, float(r["c"])) for r in rows]
    missing = [p["id"] for p in legs if p["id"] not in prices]
    if missing:
        from ..db import queries
        for r in await queries.list_marks_for_positions(ctx.pool, missing, lo, hi):
            prices.setdefault(r["position_id"], []).append((r["marked_at"], float(r["price"])))
    contract_exp = {r["instrument_token"]: r["expiry"] for r in await ctx.pool.fetch(
        "SELECT instrument_token, expiry FROM contracts WHERE instrument_token = ANY($1::bigint[])",
        list({int(p["instrument_token"]) for p in legs}))} if legs else {}

    # the future that prices these legs (app/futures.py): its stored candles
    from .. import futures as F
    fut, fut_info, fwd_exps = [], None, set()
    und = F.underlying_of(ctx.config)
    if und and ctx.candles is not None:
        first = {}
        for x in b["lots"]:
            if x["opening"]:
                first.setdefault(x["position_id"], x)
        exps = [e for e in (expiry_of((first.get(p["id"]) or {}).get("metadata") or {}, contract_exp.get(int(p["instrument_token"])), p["symbol"])
                            for p in legs) if e]
        known_f = await F.known(ctx.pool, und)
        # the nearest-expiring leg's future; legs of another month (a
        # calendar) or with none listed that far out are valued off the spot
        f = next((x for x in (F.pick(known_f, e, start.date()) for e in sorted(set(exps))) if x), None)
        if f:
            fwd_exps = {e for e in exps if (F.pick(known_f, e, start.date()) or {}).get("instrument_token") == f["instrument_token"]}
            try:
                rows, _src, _err = await ctx.candles.series(int(f["instrument_token"]), lo, hi, interval, symbol=f["tradingsymbol"])
            except Exception:
                rows = []
            fut = [{"t": datetime.fromtimestamp(r["t"] / 1000, IST), "c": float(r["c"])} for r in rows]
            fut_info = {"symbol": f["tradingsymbol"], "expiry": f["expiry"].isoformat(), "bars": len(fut)}

    out.update(build(b, sel, index, vix, prices, step, contract_exp, now, cycles=gb == "cycle_id", fut=fut,
                     fut_expiries=fwd_exps if fut else None))
    out["future"] = fut_info
    out.update({"interval": interval, "index_name": _index_name(ctx), "market_error": market_error,
                "no_index": not index})
    return out


def _index_name(ctx: Ctx) -> str:
    from ..repair.market import INDEX_LABELS
    toks = ctx.config.get("instrument_tokens") or []
    return INDEX_LABELS.get(int(toks[0]), "Index") if toks else "Index"
