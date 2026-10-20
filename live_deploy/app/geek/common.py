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


def label(rule: Optional[str]) -> str:
    return humanize(rule or "trade")


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
    if "book" in ctx._cache:
        return ctx._cache["book"]
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
    ctx._cache["book"] = out
    return out


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
        nxt["detail"] = f"Last check: {last['label']}, {last['summary']}"
    return {"running": running, "now": now.isoformat(), "legs": open_legs, "hedges": [], "rules": [],
            "state": _state(ctx.strategy), "cooldowns": [], "strikes": None, "spot": _spot(ctx),
            "fields": fields, "fields_source": source, "last_decision": last, "next": nxt}


def _spot(ctx: Ctx) -> Optional[float]:
    disp = getattr(ctx.runner, "dispatcher", None)
    toks = ctx.config.get("instrument_tokens") or []
    return (getattr(disp, "last_prices", {}) or {}).get(toks[0]) if disp is not None and toks else None


async def decisions(ctx: Ctx, limit: int = 300) -> dict:
    b = await book(ctx)
    rows = []
    for a in b["actions"]:
        what, why = action_text(a)
        rows.append({"t": _iso(a["t"]), "rule": a["trigger"], "label": label(a["trigger"]), "outcome": "acted",
                     "kind": a["kind"], "summary": what, "why": why, "data": a["trigger_values"], "cycle_id": None})
    for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], limit=limit):
        rows.append({"t": _iso(d["decided_at"]), "rule": d["rule"], "label": label(d["rule"]),
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


VIEWS = {"engine": engine, "decisions": decisions, "settings": settings}
