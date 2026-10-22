"""
live_deploy — Geek mode views every strategy gets.

Built only from what every deployment has: its positions and fills (each
fill's metadata says which rule fired and the numbers it saw, see
app/strategies/trade_meta.py), the decision log (StrategyBase.decide),
the live strategy's status fields and persisted state, and its config
against the registry defaults. A strategy with its own Geek module
(strangle_v2, ...) builds on these and adds views of its own.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from ..db import queries
from ..v2.catalog import FIELDS, humanize

IST = ZoneInfo("Asia/Kolkata")

# Fills of one rule this close together are one action (both legs of a
# straddle, a close and its re-open).
_SAME_ACTION = timedelta(seconds=90)


@dataclass
class Ctx:
    pool: Any
    dep: dict
    runner: Any = None          # DeploymentRunner when running, else None
    now: Optional[datetime] = None
    candles: Any = None
    params: dict = field(default_factory=dict)
    _cache: dict = field(default_factory=dict)

    @property
    def strategy(self):
        return getattr(self.runner, "strategy", None) if self.runner is not None else None

    @property
    def capital(self) -> float:
        return float(self.dep["initial_capital"])

    @property
    def config(self) -> dict:
        return dict(self.dep.get("config") or {})


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _ist(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return _ist(dt).isoformat() if dt is not None else None


def _opt(symbol: str) -> Optional[str]:
    return symbol[-2:] if symbol and symbol[-2:] in ("CE", "PE") else None


def _age(now: datetime, t) -> Optional[float]:
    if t is None:
        return None
    return max(0.0, round((now - _ist(t)).total_seconds(), 1))


def label(rule: Optional[str], names: Optional[dict] = None) -> str:
    """A rule's name: the strategy's own (`names`), else from its key."""
    return (names or {}).get(rule) or humanize(rule or "trade")


def _num(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def saw(values: Optional[dict], n: int = 6) -> str:
    """The numbers a rule saw, in one line: 'close 24,980 · st 25,010'."""
    if not isinstance(values, dict):
        return ""
    parts = [f"{humanize(k).lower()} {_num(v)}" for k, v in values.items()
             if v is not None and not isinstance(v, (dict, list))]
    return " · ".join(parts[:n])


def defaults(strategy_name: str) -> dict:
    from ..strategies.registry import list_strategies
    meta = next((m for m in list_strategies() if m["name"] == strategy_name), {})
    return dict(meta.get("default_config") or {})


# ── the book: fills grouped into actions ────────────────────────────────

async def book(ctx: Ctx) -> dict:
    if "common_book" in ctx._cache:
        return ctx._cache["common_book"]
    dep_id = ctx.dep["id"]
    positions = [dict(r) for r in await queries.list_positions(ctx.pool, dep_id)]
    lots = [dict(r) for r in await queries.list_deployment_lots(ctx.pool, dep_id)]
    by_id = {p["id"]: p for p in positions}
    for lot in lots:
        m = lot.get("metadata") or {}
        lot["metadata"] = m
        lot["trigger"] = m.get("trigger") or lot.get("reason") or "trade"
        lot["opening"] = opening(m.get("action"), lot["position_side"], lot["action"])
        pos = by_id.get(lot["position_id"])
        lot["pnl"] = None
        if not lot["opening"] and pos is not None:
            sign = -1 if lot["position_side"] == "short" else 1
            lot["pnl"] = round((float(lot["price"]) - float(pos["avg_entry_price"])) * float(lot["qty"]) * sign, 2)
    out = {"positions": positions, "lots": lots, "actions": actions(lots)}
    ctx._cache["common_book"] = out
    return out


def repaired_ids(lots: list) -> dict:
    """Positions a repair touched (app/repair), with the day it repaired:
    any of their fills carries metadata.repair. An undone repair deletes
    those fills, so this stays true to what's in the book."""
    out = {}
    for l in lots:
        r = (l.get("metadata") or {}).get("repair")
        if r is not None:
            out.setdefault(l["position_id"], r.get("day") or True)
    return out


def repair_day(fixed: dict, ids) -> Optional[str]:
    """The day a repair wrote into any of `ids` (a position's id or ids),
    else None: the marks link to that day's repair."""
    for i in ids:
        if i in fixed:
            return fixed[i]
    return None


def opening(meta_action, position_side: str, action: str) -> bool:
    """Does this fill open a position? The fill's own metadata says so
    ("open", "sell_open_PE", "buy_close:CE"); otherwise a sell opens a
    short and a buy opens a long."""
    words = set(re.split(r"[_:]", meta_action.lower())) if isinstance(meta_action, str) else set()
    if words & {"open", "close"} and not words >= {"open", "close"}:
        return "open" in words
    return (position_side == "short") == (action == "sell")


def actions(lots: list) -> list[dict]:
    """Fills -> actions, oldest first: fills of the same trigger within
    90 seconds of each other are one action. An action is an entry (all
    opening), an exit (all closing) or a switch (both)."""
    out: list[dict] = []
    for lot in sorted(lots, key=lambda x: x["executed_at"]):
        cur = out[-1] if out else None
        if cur and cur["trigger"] == lot["trigger"] and lot["executed_at"] - cur["t_last"] <= _SAME_ACTION:
            cur["fills"].append(lot)
            cur["t_last"] = lot["executed_at"]
        else:
            out.append({"t": lot["executed_at"], "t_last": lot["executed_at"], "trigger": lot["trigger"], "fills": [lot]})
    for a in out:
        opens = [f for f in a["fills"] if f["opening"]]
        closes = [f for f in a["fills"] if not f["opening"]]
        a["kind"] = "entry" if not closes else "exit" if not opens else "switch"
        a["pnl"] = round(sum(f["pnl"] for f in closes), 2) if closes and all(f.get("pnl") is not None for f in closes) else None
        a["trigger_values"] = next((f["metadata"].get("trigger_values") for f in a["fills"]
                                    if f["metadata"].get("trigger_values")), {}) or {}
    return out


def fill_text(f: dict) -> str:
    verb = "Sold" if f["action"] == "sell" else "Bought"
    return f"{verb} {f['symbol']} ×{_num(float(f['qty']))} @ {float(f['price']):,.2f}"


def action_text(a: dict) -> tuple[str, str]:
    """(what it did, what the rule saw) for one action."""
    what = " · ".join(fill_text(f) for f in a["fills"][:4])
    if len(a["fills"]) > 4:
        what += f" · +{len(a['fills']) - 4} more"
    if a["pnl"] is not None:
        sign = "−" if a["pnl"] < 0 else "+"
        what += f" · booked {sign}₹{abs(a['pnl']):,.0f}"
    src = next((f["metadata"].get("price_source") for f in a["fills"] if f["metadata"].get("price_source")), None)
    why = saw(a["trigger_values"])
    if src:
        why = f"{why} · price from {src}" if why else f"price from {src}"
    return what, why


# ── views ───────────────────────────────────────────────────────────────

async def status_fields(ctx: Ctx) -> tuple[list, str]:
    """The strategy's own live readings ([{label, value}]), and where
    they came from: 'live', 'persisted' or 'unavailable'."""
    s = ctx.strategy
    if s is not None:
        try:
            fields = s.get_status_fields()
        except Exception:
            fields = None
        return list(fields or []), "live" if fields else "unavailable"
    from ..strategies.registry import get_strategy_class
    cls = get_strategy_class(ctx.dep["strategy_name"])
    state = await queries.load_deployment_state(ctx.pool, ctx.dep["id"]) if cls else None
    fields = None
    if state is not None:
        try:
            fields = cls.status_fields_from_state(state)
        except Exception:
            fields = None
    return list(fields or []), "persisted" if fields else "unavailable"


def _state(s) -> Optional[dict]:
    try:
        st = s.get_persistable_state() if s is not None else None
    except Exception:
        return None
    if not isinstance(st, dict):
        return None
    return {k: v for k, v in st.items() if v is None or isinstance(v, (str, int, float, bool))}


async def legs(ctx: Ctx, now: datetime) -> list[dict]:
    """Open positions, with live prices while running."""
    disp = getattr(ctx.runner, "dispatcher", None)
    last = getattr(disp, "last_prices", {}) or {}
    times = getattr(disp, "last_price_times", {}) or {}
    out = []
    for r in await queries.list_open_positions(ctx.pool, ctx.dep["id"]):
        meta = r["metadata"] or {}
        tok = r["instrument_token"]
        px = last.get(tok) if ctx.runner is not None else None
        qty, entry = float(r["qty"]), float(r["avg_entry_price"])
        sign = -1 if r["side"] == "short" else 1
        out.append({"symbol": r["symbol"], "opt": _opt(r["symbol"]), "side": r["side"],
                    "strike": _f(meta.get("strike")), "role": meta.get("leg_role") or meta.get("leg") or r["side"],
                    "qty": qty, "entry": entry, "ltp": px,
                    "pnl": None if px is None else round((px - entry) * qty * sign, 2),
                    "tick_age_s": _age(now, times.get(tok)) if ctx.runner is not None else None,
                    "opened_at": _iso(r["opened_at"])})
    return out


async def latest_decision(ctx: Ctx) -> Optional[dict]:
    rows = await queries.list_decisions(ctx.pool, ctx.dep["id"], limit=1)
    if not rows:
        return None
    d = rows[0]
    return {"t": _iso(d["decided_at"]), "rule": d["rule"], "label": label(d["rule"]),
            "outcome": d["outcome"], "summary": d["summary"]}


async def engine(ctx: Ctx) -> dict:
    now = ctx.now or datetime.now(IST)
    running = ctx.strategy is not None
    open_legs = await legs(ctx, now)
    fields, source = await status_fields(ctx)
    last = await latest_decision(ctx)
    status = ctx.dep.get("status")
    if not running:
        why = {"paused": "Paused", "stopped": "Stopped"}.get(status, "Not running")
        nxt = {"text": f"{why}: nothing is being checked, so there are no live readings", "at": None}
    elif open_legs:
        n = len(open_legs)
        nxt = {"text": f"Holding {n} leg{'s' if n != 1 else ''}: watching for an exit", "at": None}
    else:
        nxt = {"text": "Flat: watching for an entry", "at": None}
    if nxt and last:
        at = _ist(datetime.fromisoformat(last["t"]))
        nxt["detail"] = f"Last check {at.day} {at:%b %H:%M}: {last['label']}, {last['summary']}"
    return {"running": running, "now": now.isoformat(), "legs": open_legs, "hedges": [], "rules": [],
            "state": _state(ctx.strategy), "cooldowns": [], "strikes": None, "spot": _spot(ctx),
            "fields": fields, "fields_source": source, "last_decision": last, "next": nxt,
            "quote_guard": False}


def _spot(ctx: Ctx) -> Optional[float]:
    disp = getattr(ctx.runner, "dispatcher", None)
    toks = ctx.config.get("instrument_tokens") or []
    return (getattr(disp, "last_prices", {}) or {}).get(toks[0]) if disp is not None and toks else None


async def decisions(ctx: Ctx, limit: int = 300, names: Optional[dict] = None) -> dict:
    b = await book(ctx)
    rows = []
    for a in b["actions"]:
        what, why = action_text(a)
        rows.append({"t": _iso(a["t"]), "rule": a["trigger"], "label": label(a["trigger"], names), "outcome": "acted",
                     "kind": a["kind"], "summary": what, "why": why, "data": a["trigger_values"], "cycle_id": None})
    for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], limit=limit):
        rows.append({"t": _iso(d["decided_at"]), "rule": d["rule"], "label": label(d["rule"], names),
                     "outcome": d["outcome"], "kind": "check", "summary": d["summary"], "why": saw(d["data"]),
                     "data": d["data"], "cycle_id": None})
    rows.sort(key=lambda r: r["t"], reverse=True)
    rows = rows[:limit]
    counts = {k: sum(1 for r in rows if r["outcome"] == k) for k in ("acted", "no_action", "blocked")}
    return {"rows": rows, "counts": counts}


async def settings(ctx: Ctx) -> dict:
    defs = defaults(ctx.dep["strategy_name"])
    cfg = ctx.config
    b = await book(ctx)
    rows = []
    for k in list(defs.keys()) + [k for k in cfg if k not in defs]:
        if k == "instrument_tokens":
            continue
        meta = FIELDS.get(k, {})
        rows.append({"key": k, "label": meta.get("label") or humanize(k), "value": cfg.get(k, defs.get(k)),
                     "default": defs.get(k), "changed": k in cfg and cfg[k] != defs.get(k),
                     "help": meta.get("help") or None, "effect": None})
    by_rule: dict[str, int] = {}
    for a in b["actions"]:
        by_rule[a["trigger"]] = by_rule.get(a["trigger"], 0) + 1
    return {"rows": rows, "quote_refutes": 0,
            "rules": [{"rule": r, "label": label(r), "n": n} for r, n in sorted(by_rule.items(), key=lambda x: -x[1])]}


# ── conditions: which market conditions paid ────────────────────────────
#
# Every trade with the market it was opened into (the "context" each fill
# carries since the runner started stamping it: gap, range and move so far
# today, strike from spot, days to expiry; and for signal strategies the
# signal candle and ATR), how far it went against and for it on the way
# (MAE / MFE, from the 5-minute position marks), and its result; then the
# results added up by each condition.

_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_HOURS = ["9:15–10"] + [f"{h}–{h + 1}" for h in range(10, 15)] + ["15–15:30"]
_CANDLE_SPAN, _CANDLE_LEGS = timedelta(days=3), 300


def trades_of(b: dict) -> list[dict]:
    """Actions -> trades, oldest first. A trade starts at an entry made
    while flat, or at a switch's new legs; entries made while it still has
    an open leg (adjustments, rolls, hedges) join it."""
    pos = {p["id"]: p for p in b["positions"]}
    out: list[dict] = []
    for a in b["actions"]:
        opens = [f for f in a["fills"] if f["opening"]]
        if not opens:
            continue
        cur = out[-1] if out else None
        live = cur is not None and a["kind"] == "entry" and any(
            pos.get(i) is not None and (pos[i]["closed_at"] is None or pos[i]["closed_at"] > a["t"]) for i in cur["ids"])
        if live:
            cur["actions"].append(a)
            for f in opens:
                if f["position_id"] not in cur["ids"]:
                    cur["ids"].append(f["position_id"])
                cur["qty"][f["position_id"]] = cur["qty"].get(f["position_id"], 0.0) + float(f["qty"])
            continue
        out.append({"t": a["t"], "trigger": a["trigger"], "actions": [a], "first": opens[0],
                    "trigger_values": a["trigger_values"], "ids": list(dict.fromkeys(f["position_id"] for f in opens)),
                    "qty": {}})
        for f in opens:
            out[-1]["qty"][f["position_id"]] = out[-1]["qty"].get(f["position_id"], 0.0) + float(f["qty"])
    return out


def excursion(legs: list[dict], marks: dict, now: datetime) -> tuple[Optional[float], Optional[float], list]:
    """(MAE, MFE, path) of one trade: its P&L at every mark of any of its
    legs, each leg at its last mark (or its booked P&L once closed). MAE
    is the worst point (≤ 0), MFE the best (≥ 0); None without marks."""
    events: list[tuple] = []
    for lg in legs:
        for t, px in marks.get(lg["id"], []):
            if t >= lg["opened_at"] and (lg["closed_at"] is None or t <= lg["closed_at"]):
                events.append((t, lg["id"], px))
        if lg["closed_at"] is not None:
            events.append((lg["closed_at"], lg["id"], None))
    if not any(px is not None for _, _, px in events):
        return None, None, []
    events.sort(key=lambda e: e[0])
    now_pnl: dict = {}
    path = []
    by = {lg["id"]: lg for lg in legs}
    for n, (t, i, px) in enumerate(events):
        lg = by[i]
        if px is None:
            now_pnl[i] = lg["realized"]
        else:
            now_pnl[i] = (px - lg["entry"]) * lg["qty"] * lg["sign"]
        if n + 1 == len(events) or events[n + 1][0] != t:   # one point per moment, every leg at it updated
            path.append((t, round(sum(now_pnl.values()), 2)))
    vals = [v for _, v in path]
    return round(min(0.0, min(vals)), 2), round(max(0.0, max(vals)), 2), path


def _bucket(v, edges: list, labels: list) -> Optional[str]:
    if v is None:
        return None
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


def _hour(t: datetime) -> str:
    h = t.hour
    if h < 10:
        return "9:15–10"
    if h >= 15:
        return "15–15:30"
    return f"{h}–{h + 1}"


DIMENSIONS = [
    ("hour", "Time of entry", None),
    ("weekday", "Day of the week", None),
    ("dte", "Days to expiry", (["Expiry day", "1 day", "2–3 days", "4–7 days", "8+ days"], [0.5, 1.5, 3.5, 7.5])),
    ("gap", "Today’s gap", (["Gap down > 0.5%", "Down 0–0.5%", "Up 0–0.5%", "Gap up > 0.5%"], [-0.5, 0, 0.5])),
    ("range", "Range so far, % of the index", (["< 0.3%", "0.3–0.6%", "0.6–1%", "> 1%"], [0.3, 0.6, 1.0])),
    ("move", "Index since the open", (["Down > 0.5%", "Down 0–0.5%", "Up 0–0.5%", "Up > 0.5%"], [-0.5, 0, 0.5])),
    ("held", "Held for", (["< 30 min", "30 min–2 h", "2 h–1 day", "1–5 days", "> 5 days"], [30, 120, 1440, 7200])),
    ("legs", "Options sold or bought", None),
    ("money", "Strike against the index", (["In the money", "At the money (±0.25%)", "0.25–1% out of the money", "> 1% out of the money"], [-0.25, 0.25, 1.0])),
    ("atr", "ATR at the signal", "thirds"),
    ("candle", "Signal candle’s size", "thirds"),
]
# Split into thirds of this deployment's own trades (points mean different
# things on different indices and timeframes); needs this many trades.
_THIRDS_MIN = 6


def thirds(values: list) -> Optional[tuple[float, float]]:
    """The two cut points that split the values into thirds, or None."""
    v = sorted(x for x in values if x is not None)
    if len(v) < _THIRDS_MIN:
        return None
    return v[len(v) // 3 - (1 if len(v) % 3 == 0 else 0)], v[2 * len(v) // 3 - (1 if len(v) % 3 == 0 else 0)]


def third_labels(cuts: tuple[float, float]) -> list[str]:
    a, b = (_num(round(x, 2)) for x in cuts)
    return [f"Low · up to {a}", f"Middle · {a}–{b}", f"High · above {b}"]


def otm_pct(c: dict, opt: Optional[str]) -> Optional[float]:
    """How far the strike is out of the money, % of the index (negative:
    in the money): above the index for a call, below it for a put."""
    if c.get("strike_from_spot") is None or not c.get("spot") or opt not in ("CE", "PE"):
        return None
    return round(c["strike_from_spot"] / c["spot"] * 100 * (1 if opt == "CE" else -1), 3)


def _dims(row: dict) -> dict:
    c = row["context"]
    t = _ist(datetime.fromisoformat(row["t"]))
    out = {"hour": _hour(t), "weekday": _WD[t.weekday()], "legs": row["opt"]}
    partial = bool(c.get("partial"))   # the day only since a restart: its range and move aren't the day's
    for key, _, spec in DIMENSIONS:
        if not isinstance(spec, tuple):
            continue
        labels, edges = spec
        v = {"dte": c.get("dte"), "gap": c.get("gap_pct"), "move": None if partial else c.get("from_open_pct"),
             "held": row["held_min"], "money": row["otm_pct"],
             "range": round(c["range_pts"] / c["spot"] * 100, 3) if not partial and c.get("range_pts") is not None and c.get("spot") else None}[key]
        out[key] = _bucket(v, edges + [float("inf")], labels)
    return out


async def conditions(ctx: Ctx, limit: int = 300) -> dict:
    now = ctx.now or datetime.now(IST)
    b = await book(ctx)
    pos = {p["id"]: p for p in b["positions"]}
    trades = trades_of(b)[-limit:]
    ids = [i for tr in trades for i in tr["ids"]]
    marks: dict = {}
    if ids:
        first = min(pos[i]["opened_at"] for i in ids if i in pos)
        for r in await queries.list_marks_for_positions(ctx.pool, ids, first, now):
            marks.setdefault(r["position_id"], []).append((r["marked_at"], float(r["price"])))
        # a leg without marks (opened and closed between two snapshots, or
        # from before marks were recorded): its contract's stored 1-minute
        # closes, for the newest few hundred short-lived legs
        bare = [pos[i] for i in reversed(ids) if i in pos and i not in marks]
        for p in [p for p in bare if (p["closed_at"] or now) - p["opened_at"] <= _CANDLE_SPAN][:_CANDLE_LEGS]:
            rows = await queries.list_candles(ctx.pool, p["instrument_token"], p["opened_at"], p["closed_at"] or now)
            if rows:
                marks[p["id"]] = [(r["minute"] + timedelta(minutes=1), float(r["c"])) for r in rows]
    rows = []
    for tr in trades:
        legs = []
        for i in tr["ids"]:
            p = pos.get(i)
            if p is None:
                continue
            legs.append({"id": i, "symbol": p["symbol"], "opened_at": p["opened_at"], "closed_at": p["closed_at"],
                         "entry": float(p["avg_entry_price"]), "qty": tr["qty"].get(i) or float(p["qty"]),
                         "sign": -1 if p["side"] == "short" else 1, "realized": float(p["realized_pnl"] or 0)})
        if not legs:
            continue
        is_open = any(lg["closed_at"] is None for lg in legs)
        pnl = None if is_open else round(sum(lg["realized"] for lg in legs), 2)
        mae, mfe, _ = excursion(legs, marks, now)
        has_path = mae is not None
        if pnl is not None:   # the result itself is a point on the path
            mae, mfe = (min(mae, pnl, 0.0), max(mfe, pnl, 0.0)) if mae is not None else (min(pnl, 0.0), max(pnl, 0.0))
        end = None if is_open else max(lg["closed_at"] for lg in legs)
        m = tr["first"]["metadata"]
        c = dict(m.get("context") or {})
        tv = tr["trigger_values"] or {}
        if c.get("dte") is None and m.get("expiry"):
            try:
                c["dte"] = (datetime.fromisoformat(str(m["expiry"])[:10]).date() - _ist(tr["t"]).date()).days
            except ValueError:
                pass
        candle = tv.get("candle") if isinstance(tv.get("candle"), dict) else None
        opts = {_opt(lg["symbol"]) for lg in legs} - {None}
        row = {"t": _iso(tr["t"]), "end": _iso(end), "trigger": tr["trigger"], "label": label(tr["trigger"]),
               "symbols": [lg["symbol"] for lg in legs], "n_legs": len(legs), "adds": len(tr["actions"]) - 1,
               "open": is_open, "pnl": pnl, "mae": mae, "mfe": mfe, "path": has_path,
               "held_min": round((end - tr["t"]).total_seconds() / 60, 1) if end else None,
               "opt": "Calls and puts" if opts == {"CE", "PE"} else "Calls" if opts == {"CE"} else "Puts" if opts == {"PE"} else "Other",
               "context": c, "has_context": bool(m.get("context")),
               "candle_pts": round(candle["high"] - candle["low"], 2) if candle and candle.get("high") is not None and candle.get("low") is not None else None,
               "atr": _f(tv.get("atr")), "band_width": _f(tv.get("band_width")),
               "otm_pct": otm_pct(c, _opt(tr["first"].get("symbol") or ""))}
        row["dims"] = _dims(row)
        rows.append(row)
    closed = [r for r in rows if r["pnl"] is not None]
    third_order = {}
    for key, field in (("atr", "atr"), ("candle", "candle_pts")):
        cuts = thirds([r[field] for r in closed])
        if cuts is None:
            continue
        third_order[key] = labs = third_labels(cuts)
        for r in rows:
            r["dims"][key] = _bucket(r[field], [cuts[0] + 1e-9, cuts[1] + 1e-9, float("inf")], labs)
    groups = []
    for key, name, spec in DIMENSIONS:
        order = spec[0] if isinstance(spec, tuple) else third_order.get(key) or {"weekday": _WD, "hour": _HOURS}.get(key)
        agg: dict = {}
        for r in closed:
            k = r["dims"].get(key)
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
        keys = sorted(agg, key=lambda k: order.index(k) if order and k in order else k)
        groups.append({"key": key, "label": name, "buckets": [agg[k] for k in keys],
                       "covered": sum(g["n"] for g in agg.values())})
    with_path = [r for r in closed if r["path"]]
    winners = [r for r in with_path if r["pnl"] > 0]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    summary = {"trades": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
               "wins": sum(1 for r in closed if r["pnl"] > 0), "with_context": sum(1 for r in rows if r["has_context"]),
               "avg_mae": avg([r["mae"] for r in with_path]), "avg_mfe": avg([r["mfe"] for r in with_path]),
               "gave_back": avg([r["mfe"] - r["pnl"] for r in winners]),
               "losers_were_up": sum(1 for r in with_path if r["pnl"] < 0 and r["mfe"] > 0), "with_path": len(with_path)}
    rows.reverse()
    return {"rows": rows, "groups": groups, "summary": summary}


from .volatility import volatility  # noqa: E402  (it builds on this module)

VIEWS = {"engine": engine, "decisions": decisions, "settings": settings, "conditions": conditions, "volatility": volatility}
