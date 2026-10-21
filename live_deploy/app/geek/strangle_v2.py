"""
Geek mode for strangle_monthly_v2 (see app/strategies/strangle_monthly_v2.py
for the rules themselves).

Views, each `async (ctx) -> dict`:
  engine     live: every rule in the order the strategy checks it, how close
             each is to firing, the next move, the legs and the saved state
  decisions  every check: trades (from fills' metadata) and checks that
             didn't trade (strategy_decisions)
  cycles     one row per checkpoint cycle (cycle_id): how it ended, rolls,
             worst dip / best peak, P&L and its path; monthly progress
  cycle      one cycle end to end (?c=<cycle_id>): its entry, every leg,
             every action and check in order, its rolls scored, its P&L
             path and side balance, and the money
  adjustments  every roll against keeping the leg it closed
  entries    why each contract and strike was picked
  premium    premium sold and kept, P&L by leg role, the current cycle's
             side totals over time
  settings   every setting, its default and what it has done

Everything is read-only. The live readings mirror _maybe_manage's own
arithmetic; if that changes, change engine() with it (tests pin both).
"""

from __future__ import annotations

import statistics
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from ..db import queries
from . import common

IST = ZoneInfo("Asia/Kolkata")

ROLL_KIND = {"continuous_50pct_trigger": "continuous", "eod_gap_check": "eod", "eod_rollout": "rollout"}
EXIT_REASONS = {"checkpoint_target", "contract_expiry_backstop", "convergence_stop"}
RULE_LABELS = {
    "contract_expiry_backstop": "Expiry backstop",
    "checkpoint_target": "Checkpoint",
    "convergence_stop": "Convergence stop",
    "eod_gap_check": "EOD check",
    "eod_rollout": "EOD roll-out",
    "continuous_50pct_trigger": "Continuous adjustment",
    "stale_price_guard": "Stale-price guard",
    "initial_entry": "Entry",
    "entry": "Entry",
}
# Lots this close together belong to one action (both legs of an entry,
# a roll's close and open).
_ACTION_GAP = timedelta(seconds=90)


@dataclass
class Ctx:
    pool: Any
    dep: dict
    runner: Any = None          # DeploymentRunner when running, else None
    now: Optional[datetime] = None
    candles: Any = None         # CandleService, to fill a rolled-away leg's prices
    params: dict = field(default_factory=dict)   # the request's query (?c=<cycle_id>)
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


# ── small helpers ───────────────────────────────────────────────────────

def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _opt(symbol: str) -> Optional[str]:
    return symbol[-2:] if symbol and symbol[-2:] in ("CE", "PE") else None


def _ist(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return _ist(dt).isoformat() if dt is not None else None


def _base(reason: Optional[str]) -> str:
    r = reason or ""
    for suffix in ("_hedge", "_sweep"):
        if r.endswith(suffix):
            r = r[: -len(suffix)]
    return r


def _is_entry_reason(r: str) -> bool:
    return r in ("initial_entry", "checkpoint_target") or r.startswith("reentry_after_")


def _hhmm(t: Optional[time]) -> Optional[str]:
    return t.strftime("%H:%M") if t else None


def _rupees(v: float) -> str:
    sign = "−" if v < 0 else ""
    return f"{sign}₹{abs(v):,.0f}"


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _defaults() -> dict:
    from ..strategies.registry import list_strategies
    meta = next((m for m in list_strategies() if m["name"] == "strangle_monthly_v2"), {})
    return dict(meta.get("default_config") or {})


# ── the book: positions + fills, grouped into actions ───────────────────

async def _book(ctx: Ctx) -> dict:
    if "book" in ctx._cache:
        return ctx._cache["book"]
    dep_id = ctx.dep["id"]
    positions = [dict(r) for r in await queries.list_positions(ctx.pool, dep_id)]
    lots = [dict(r) for r in await queries.list_deployment_lots(ctx.pool, dep_id)]
    by_id = {p["id"]: p for p in positions}
    for p in positions:
        p["metadata"] = p.get("metadata") or {}
        p["cycle_id"] = p["metadata"].get("cycle_id")
        p["hedge"] = p["side"] == "long"
        p["opt"] = _opt(p["symbol"])
        p["strike"] = _f(p["metadata"].get("strike"))
        p["role"] = p["metadata"].get("leg_role") or ("hedge" if p["hedge"] else "original")
    for lot in lots:
        lot["metadata"] = lot.get("metadata") or {}
        pos = by_id.get(lot["position_id"]) or {}
        m = lot["metadata"]
        lot["cycle_id"] = m.get("cycle_id", pos.get("cycle_id"))
        lot["base"] = _base(lot["reason"])
        if m.get("action") in ("open", "close"):
            lot["opening"] = m["action"] == "open"
        else:
            lot["opening"] = (lot["position_side"] == "short") == (lot["action"] == "sell")
        lot["hedge"] = lot["position_side"] == "long"
        lot["strike"] = _f(m.get("strike")) if m.get("strike") is not None else pos.get("strike")
        lot["opt"] = _opt(lot["symbol"])
        lot["role"] = m.get("leg_role") or pos.get("role")
    out = {"positions": positions, "lots": lots, "by_id": by_id, "actions": _actions(lots, by_id)}
    ctx._cache["book"] = out
    return out


def _kind(lot: dict) -> str:
    b = lot["base"]
    if b in ROLL_KIND:
        return "roll"
    if lot["opening"] and _is_entry_reason(b):
        return "entry"
    if not lot["opening"] and b in EXIT_REASONS:
        return "exit"
    return "other"


def _actions(lots: list, by_id: dict) -> list[dict]:
    """Fills -> actions: an entry (both legs + hedges), a roll (close +
    open), a flatten, or anything else (a manual close, a sweep)."""
    actions: list[dict] = []
    for lot in lots:
        kind = _kind(lot)
        prev = actions[-1] if actions else None
        if (prev is not None and prev["kind"] == kind and prev["reason"] == lot["base"]
                and lot["executed_at"] - prev["_last"] <= _ACTION_GAP):
            act = prev
        else:
            act = {"t": lot["executed_at"], "kind": kind, "reason": lot["base"], "cycle_id": lot["cycle_id"],
                   "fills": [], "trigger_values": {}, "target_basis": {}, "contract_expiry": None}
            actions.append(act)
        act["_last"] = lot["executed_at"]
        m = lot["metadata"]
        if m.get("trigger_values") and not act["trigger_values"]:
            act["trigger_values"] = m["trigger_values"]
        if m.get("target_basis") and not lot["hedge"] and lot["opening"]:
            act["target_basis"].setdefault(lot["opt"], m["target_basis"])
        if m.get("contract_expiry"):
            act["contract_expiry"] = m["contract_expiry"]
        if act["cycle_id"] is None:
            act["cycle_id"] = lot["cycle_id"]
        pos = by_id.get(lot["position_id"]) or {}
        closes_position = (not lot["opening"] and pos.get("status") == "closed"
                           and pos.get("closed_at") is not None
                           and abs((pos["closed_at"] - lot["executed_at"]).total_seconds()) < 1)
        act["fills"].append({
            "symbol": lot["symbol"], "token": int(lot["instrument_token"]), "opt": lot["opt"],
            "strike": lot["strike"], "open": lot["opening"], "hedge": lot["hedge"],
            "price": float(lot["price"]), "book_price": _f(lot.get("book_price")), "qty": float(lot["qty"]),
            "role": lot["role"], "position_id": str(lot["position_id"]),
            "pnl": _f(pos.get("realized_pnl")) if closes_position else None,
            "charges": _f(pos.get("estimated_charges")) if closes_position else None,
            "slippage": _f(pos.get("estimated_slippage")) if closes_position else None,
        })
    for a in actions:
        del a["_last"]
    return actions


def _shorts(fills: list, opening: bool) -> list:
    return [f for f in fills if not f["hedge"] and f["open"] == opening]


def _leg_txt(f: dict) -> str:
    strike = f"{f['strike']:g}" if f["strike"] is not None else f["symbol"]
    return f"{f['opt'] or ''} {strike} @{f['price']:.1f}".strip()


def _direction(opt: str, old: Optional[float], new: Optional[float]) -> str:
    if old is None or new is None or old == new:
        return ""
    closer = new > old if opt == "PE" else new < old
    return "in" if closer else "out"


def _action_text(a: dict) -> tuple[str, str]:
    """(what it did, why) for one action."""
    tv, kind = a["trigger_values"] or {}, a["kind"]
    if kind == "entry":
        opened = _shorts(a["fills"], True)
        what = "Sold strangle: " + " · ".join(_leg_txt(f) for f in sorted(opened, key=lambda f: f["opt"] or ""))
        why = []
        if tv.get("day_of_month") is not None:
            why.append(f"day {tv['day_of_month']} → {tv.get('rotation_selector', '?')}")
        if a.get("contract_expiry"):
            why.append(f"expiry {a['contract_expiry']}")
        if tv.get("days_to_expiry") is not None:
            why.append(f"{tv['days_to_expiry']} days out")
        if tv.get("strike_selection_capital_pct") is not None:
            pct = float(tv["strike_selection_capital_pct"]) * 100
            why.append(f"{'long-days ' if tv.get('long_days_override') else ''}{pct:g}% of capital")
        tgt = next((b.get("target_premium") for b in a["target_basis"].values() if b.get("target_premium")), None)
        if tgt:
            why.append(f"target {float(tgt):.1f} / leg")
        return what, " · ".join(why)
    if kind == "roll":
        closed, opened = _shorts(a["fills"], False), _shorts(a["fills"], True)
        side = (opened or closed or [{}])[0].get("opt") or ""
        old = closed[0]["strike"] if closed else None
        new = opened[0]["strike"] if opened else None
        d = _direction(side, old, new)
        what = f"Rolled {side} {d}: " if d else f"Rolled {side}: "
        what += " · ".join(
            [f"closed {_leg_txt(f)}" + (f" ({_rupees(f['pnl'])})" if f["pnl"] is not None else "") for f in closed]
            + [f"sold {_leg_txt(f)}" for f in opened])
        why = ""
        if tv.get("smaller_sum") and tv.get("bigger_sum"):
            r = float(tv["smaller_sum"]) / float(tv["bigger_sum"])
            limit = tv.get("eod_gap_floor") if a["reason"] != "continuous_50pct_trigger" else tv.get("ratio")
            op = "<" if a["reason"] != "continuous_50pct_trigger" else "≤"
            why = f"{tv.get('smaller_side')}/{tv.get('bigger_side')} {r:.2f}"
            if limit is not None:
                why += f" {op} {float(limit):.2f}"
        if a["reason"] == "eod_rollout" and tv.get("why"):
            why += f" · {tv['why']}"
        return what, why
    if kind == "exit":
        what = f"Flattened cycle {a['cycle_id']}" if a["cycle_id"] is not None else "Flattened"
        if a["reason"] == "checkpoint_target" and tv.get("total_cycle_profit") is not None:
            why = f"cycle P&L {_rupees(float(tv['total_cycle_profit']))} ≥ {_rupees(float(tv['checkpoint_target']))}"
        elif a["reason"] == "contract_expiry_backstop":
            why = f"contract expiry {tv.get('contract_expiry', '')}".strip()
        elif a["reason"] == "convergence_stop" and tv.get("stop_level") is not None:
            why = f"premium {float(tv['combined_now']):.1f} ≥ stop {float(tv['stop_level']):.1f}"
        else:
            why = ""
        return what, why
    legs = ", ".join(_leg_txt(f) for f in a["fills"])
    return f"{'Opened' if all(f['open'] for f in a['fills']) else 'Closed'} {legs}", a["reason"].replace("_", " ")


# ── engine (live) ───────────────────────────────────────────────────────

def _side_sums(legs: dict, prices: dict) -> Optional[tuple]:
    if not (legs.get("CE") and legs.get("PE")):
        return None
    ce = sum(prices[l["token"]] for l in legs["CE"])
    pe = sum(prices[l["token"]] for l in legs["PE"])
    if ce <= 0 or pe <= 0:
        return None
    return ("CE", "PE", ce, pe) if ce >= pe else ("PE", "CE", pe, ce)


def _age(now: datetime, traded_at) -> Optional[float]:
    if traded_at is None:
        return None
    return max(0.0, (now - _ist(traded_at)).total_seconds())



def _checkpoint_progress(profit: float, target: float) -> str:
    """How far the cycle is toward the checkpoint, in words: a percentage
    while in profit, the loss and the distance while in loss."""
    if target <= 0:
        return "no checkpoint set"
    if profit < 0:
        return f"cycle is {_rupees(profit)}, {_rupees(target - profit)} short of the checkpoint"
    return f"checkpoint {round(profit / target * 100)}% there"

def engine_from_strategy(s, runner, now: datetime) -> dict:
    """Live readings off a running StrangleMonthlyV2Strategy. Pure apart
    from reading the strategy and the dispatcher's last prices."""
    disp = runner.dispatcher
    capital = float(runner.initial_capital)
    legs = {side: list(s.legs.get(side) or []) for side in ("CE", "PE")}
    all_legs = legs["CE"] + legs["PE"]
    prices = {l["token"]: disp.last_prices.get(l["token"]) for l in all_legs}
    have_prices = all(v is not None for v in prices.values())
    times = getattr(disp, "last_price_times", {}) or {}
    tokens = runner.config.get("instrument_tokens") or []
    spot = disp.last_prices.get(tokens[0]) if tokens else None
    t = now.time()

    leg_rows = []
    for side in ("CE", "PE"):
        for l in legs[side]:
            px = prices[l["token"]]
            leg_rows.append({
                "symbol": l["symbol"], "opt": side, "strike": l["strike"], "role": l.get("role"),
                "qty": l["qty"], "entry": l["entry_price"], "ltp": px,
                "pnl": None if px is None else round((l["entry_price"] - px) * l["qty"], 2),
                "tick_age_s": _age(now, times.get(l["token"])),
            })
    hedges = [{"symbol": h["symbol"], "strike": h["strike"], "entry": h["entry_price"],
               "ltp": disp.last_prices.get(h["token"])} for h in (getattr(s, "hedges", {}) or {}).values()]

    state = {
        "cycle_id": s.cycle_id, "contract_expiry": s.contract_expiry.isoformat() if s.contract_expiry else None,
        "cycle_realized_pnl": round(s.cycle_realized_pnl, 2), "converged": s.converged,
        "convergence_premium": s.convergence_premium, "adjustments_used": s.adjustments_used,
        "eod_checked_today": s._eod_fired_today, "entered_ever": s.entered_ever,
        "last_flatten": s._last_flatten_trigger, "hedges": len(hedges),
    }
    cooldowns = [{"rule": k, "until": _iso(v)} for k, v in (s._confirm_cooldown_until or {}).items()
                 if v is not None and _ist(v) > now]
    out = {"running": True, "now": now.isoformat(), "spot": spot, "legs": leg_rows, "hedges": hedges,
           "state": state, "cooldowns": cooldowns, "rules": [], "next": None, "strikes": None}

    if not all_legs:
        entry = _hhmm(s.entry_time)
        if t < s.entry_time:
            out["next"] = {"text": f"Flat. Waiting for entry time ({entry} IST)", "at": entry}
        else:
            out["next"] = {"text": "Flat. Entering on the next tick", "at": None}
        return out
    if not have_prices:
        out["next"] = {"text": "Waiting for a live price on every leg", "at": None}
        return out

    sums = _side_sums(legs, prices)
    profit = s.cycle_realized_pnl + sum((l["entry_price"] - prices[l["token"]]) * l["qty"] for l in all_legs)
    target = s.checkpoint_profit_pct_of_capital * capital
    gated = s.checkpoint_exit_not_before is not None and t < s.checkpoint_exit_not_before
    frozen = s.converged and s.convergence_mode in ("fixed_stop", "trailing_stop")
    rules = []

    # 1 expiry backstop
    exp = s.contract_expiry
    if exp is not None:
        opened = min((_ist(p["opened_at"]).date() for p in (runner.open_positions or {}).values()
                      if p.get("opened_at") is not None), default=now.date())
        total = max(1, (exp - opened).days)
        left = (exp - now.date()).days
        rules.append({
            "key": "contract_expiry_backstop", "label": "Expiry backstop", "desc": "closes everything on expiry day",
            "fill": _clamp((total - left) / total), "mark": 1.0,
            "left": f"entered {opened:%d %b}", "right": f"{exp:%d %b} expiry",
            "reading": "closes today" if left <= 0 else f"{left} day{'s' if left != 1 else ''} left",
            "state": ("armed" if left <= 0 else "idle") if s.force_close_at_contract_expiry else "off",
        })
    # 2 checkpoint
    reached = profit >= target
    rules.append({
        "key": "checkpoint_target", "label": "Checkpoint",
        "desc": f"cycle P&L ≥ {s.checkpoint_profit_pct_of_capital * 100:g}% of capital → flatten, re-enter",
        "fill": _clamp(profit / target) if target > 0 else 0, "mark": 1.0, "tone": "pos",
        "left": "₹0", "mid": f"{_rupees(profit)} of {_rupees(target)}", "right": _rupees(target),
        "reading": ("reached · exits wait until " + _hhmm(s.checkpoint_exit_not_before)) if reached and gated
        else "reached" if reached else f"{_rupees(target - profit)} to go",
        # A cycle in loss isn't on its way to the checkpoint: "behind", no
        # percentage (a negative "-357% there" in green read as good news).
        "state": "armed" if reached else "behind" if profit < 0 else "ok",
        "pct": round(profit / target * 100) if target > 0 and profit >= 0 else None,
        "value": round(profit, 2), "target": target,
    })
    # 3 convergence stop
    if not s.converged:
        apart = None
        if len(legs["CE"]) == 1 and len(legs["PE"]) == 1:
            apart = legs["CE"][0]["strike"] - legs["PE"][0]["strike"]
        rules.append({
            "key": "convergence_stop", "label": "Convergence stop",
            "desc": f"{s.convergence_mode.replace('_', ' ')} {s.convergence_stop_pct * 100:g}% · only once strikes converge",
            "fill": 0, "mark": 1.0, "left": f"strikes {apart:g} pts apart" if apart is not None else "",
            "right": "converged = 0 pts", "reading": "not converged", "state": "off",
        })
    elif s.convergence_mode in ("fixed_stop", "trailing_stop"):
        combined = sum(prices[l["token"]] for l in all_legs)
        base = s.convergence_premium if s.convergence_mode == "fixed_stop" else \
            min(getattr(s, "_trailing_floor", combined), combined)
        stop = base * (1 + s.convergence_stop_pct)
        rules.append({
            "key": "convergence_stop", "label": "Convergence stop",
            "desc": f"{s.convergence_mode.replace('_', ' ')}: premium ≥ {stop:.1f} closes the cycle",
            "fill": _clamp(combined / stop) if stop else 0, "mark": 1.0,
            "left": f"premium {combined:.1f}", "right": f"stop {stop:.1f}",
            "reading": f"{stop - combined:.1f} pts below stop", "state": "armed" if combined >= stop else "ok",
        })
    else:
        rules.append({"key": "convergence_stop", "label": "Convergence", "desc": "converged: intraday DTT rules manage it",
                      "fill": 0, "mark": 1.0, "reading": "delegated", "state": "ok"})
    # 4 EOD check
    eod = _hhmm(s.eod_check_time)
    if sums is not None:
        big, small, bsum, ssum = sums
        r = ssum / bsum
        short_by = s.eod_gap_floor * bsum - ssum
        if frozen:
            st, reading = "off", "frozen after convergence"
        elif s._eod_fired_today:
            st, reading = "done", "checked today"
        elif r < s.eod_gap_floor:
            st, reading = "armed", f"{short_by:.1f} pts short"
        else:
            st, reading = "idle", "inside band"
        rules.append({
            "key": "eod_gap_check", "label": f"EOD check · {eod}",
            "desc": f"smaller side < {s.eod_gap_floor * 100:g}% of bigger → roll it toward the band",
            "fill": _clamp(r), "mark": s.eod_gap_floor, "band": [s.eod_gap_floor, s.adjustment_band_max],
            "left": "0%", "mid": f"{small}/{big} {r * 100:.1f}% · band {s.eod_gap_floor * 100:g}–{s.adjustment_band_max * 100:g}%",
            "right": "100%", "reading": reading, "state": st, "ratio": round(r, 4),
        })
        # 5 continuous
        can_lose = ssum - s.adjustment_trigger_ratio * bsum
        rules.append({
            "key": "continuous_50pct_trigger", "label": "Continuous adjustment",
            "desc": f"smaller side ≤ {s.adjustment_trigger_ratio * 100:g}% of bigger → roll now",
            "fill": _clamp(r), "mark": s.adjustment_trigger_ratio, "left": "0%",
            "mid": f"trigger {s.adjustment_trigger_ratio * 100:g}%", "right": f"{small}/{big} {r * 100:.1f}%",
            "reading": "off after convergence" if frozen else
            ("firing" if can_lose <= 0 else f"{small} can lose {can_lose:.1f} more"),
            "state": "off" if frozen else ("armed" if can_lose <= 0 else "idle"), "ratio": round(r, 4),
        })
    out["rules"] = rules

    # strikes against spot
    ce_k = [l["strike"] for l in legs["CE"]]
    pe_k = [l["strike"] for l in legs["PE"]]
    credit = sum(l["entry_price"] for l in all_legs) - sum(h["entry"] for h in hedges)
    if ce_k and pe_k:
        out["strikes"] = {"ce": min(ce_k), "pe": max(pe_k), "credit": round(credit, 2),
                          "upper_be": round(min(ce_k) + credit, 2), "lower_be": round(max(pe_k) - credit, 2),
                          "spot": spot}

    # next move, in the strategy's own priority order
    nxt = None
    market_open = now.weekday() < 5 and time(9, 15) <= t < time(15, 30)
    if exp is not None and now.date() >= exp and s.force_close_at_contract_expiry:
        nxt = {"text": "Contract expires today: closing everything on the next tick", "at": None}
    elif reached and not gated:
        nxt = {"text": "Checkpoint reached: flattening and re-entering on the next tick", "at": None}
    elif reached:
        nxt = {"text": f"Checkpoint reached: exits wait until {_hhmm(s.checkpoint_exit_not_before)}",
               "at": _hhmm(s.checkpoint_exit_not_before)}
    elif sums is not None and not frozen:
        big, small, bsum, ssum = sums
        r = ssum / bsum
        cool = any(c["rule"] in ("continuous_50pct_trigger", "eod_gap_check") for c in cooldowns)
        if r <= s.adjustment_trigger_ratio:
            nxt = {"text": (f"Rolling the {small} side now" if market_open else
                            f"Market closed. At the open the {small} side rolls if it's still this low")
                           + f": {small}/{big} {r:.2f} ≤ {s.adjustment_trigger_ratio:.2f}"
                   + (" (waiting for quotes to confirm)" if cool else ""), "at": None}
        elif r < s.eod_gap_floor and not s._eod_fired_today:
            way = "up" if small == "PE" else "down"
            due = market_open and t >= s.eod_check_time
            if not market_open:
                lead = f"Market closed. At {eod} next session the EOD check rolls the {small} side {way} if it's still short"
            elif due:
                lead = f"The EOD check is due: it rolls the {small} side {way} on the next tick"
            else:
                lead = f"At {eod} the EOD check will roll the {small} side {way}"
            nxt = {"text": lead + f": {small}/{big} is {r * 100:.0f}%, the floor is {s.eod_gap_floor * 100:g}%",
                   "at": None if due else eod}
            cheapest = min(prices[l["token"]] for l in legs[small])
            mid = (s.eod_gap_floor + s.adjustment_band_max) / 2
            nxt["detail"] = (f"If prices hold: close the {small} leg at {cheapest:.1f} and sell a {small} near "
                             f"{mid * bsum - (ssum - cheapest):.1f} so {small} reaches {mid * 100:.1f}% of {big}"
                             + (f", or roll {big} out instead if that would make a straddle"
                                if s.eod_rollout_instead_of_straddle else ""))
    if nxt is None:
        if sums is not None and not frozen:
            big, small, bsum, ssum = sums
            nxt = {"text": f"Nothing due. Closest: {small} can lose {ssum - s.adjustment_trigger_ratio * bsum:.1f} "
                           f"more before a roll; {_checkpoint_progress(profit, target)}",
                   "at": None}
        else:
            nxt = {"text": f"Nothing due. {_checkpoint_progress(profit, target).capitalize()}", "at": None}
    out["next"] = nxt
    return out


async def engine(ctx: Ctx) -> dict:
    s = ctx.strategy
    now = ctx.now or datetime.now(IST)
    if s is not None and hasattr(s, "legs"):
        return engine_from_strategy(s, ctx.runner, now)
    rows = await queries.list_open_positions(ctx.pool, ctx.dep["id"])
    legs = [{"symbol": r["symbol"], "opt": _opt(r["symbol"]), "strike": _f((r["metadata"] or {}).get("strike")),
             "role": (r["metadata"] or {}).get("leg_role"), "qty": float(r["qty"]),
             "entry": float(r["avg_entry_price"]), "ltp": None, "pnl": None, "tick_age_s": None}
            for r in rows if r["side"] == "short"]
    status = ctx.dep.get("status")
    why = {"paused": "Paused", "stopped": "Stopped"}.get(status, "Not running")
    return {"running": False, "now": now.isoformat(), "legs": legs, "rules": [], "hedges": [], "state": None,
            "cooldowns": [], "strikes": None, "spot": None,
            "next": {"text": f"{why}: rules aren't being checked, so there are no live readings", "at": None}}


# ── decisions ───────────────────────────────────────────────────────────

async def decisions(ctx: Ctx, limit: int = 300) -> dict:
    book = await _book(ctx)
    rows = []
    for a in book["actions"]:
        what, why = _action_text(a)
        rule = a["reason"] if a["kind"] != "entry" else "entry"
        rows.append({"t": _iso(a["t"]), "rule": rule, "label": RULE_LABELS.get(rule, rule.replace("_", " ").capitalize()),
                     "outcome": "acted", "kind": a["kind"], "summary": what, "why": why, "cycle_id": a["cycle_id"]})
    cycles = _cycle_windows(book)
    for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], limit=limit):
        t = d["decided_at"]
        rows.append({"t": _iso(t), "rule": d["rule"],
                     "label": RULE_LABELS.get(d["rule"], d["rule"].replace("_", " ").capitalize()),
                     "outcome": d["outcome"], "kind": "check", "summary": d["summary"], "why": "",
                     "data": d["data"], "cycle_id": _cycle_at(cycles, t)})
    rows.sort(key=lambda r: r["t"], reverse=True)
    rows = rows[:limit]
    counts = {k: sum(1 for r in rows if r["outcome"] == k) for k in ("acted", "no_action", "blocked")}
    return {"rows": rows, "counts": counts}


def _cycle_windows(book: dict) -> list[tuple]:
    w: dict = {}
    for p in book["positions"]:
        c = p["cycle_id"]
        if c is None:
            continue
        a, b = w.get(c, (p["opened_at"], p["closed_at"]))
        end = None if (b is None or p["closed_at"] is None) else max(b, p["closed_at"])
        w[c] = (min(a, p["opened_at"]), end)
    return sorted((c, a, b) for c, (a, b) in w.items())


def _cycle_at(windows: list, t: datetime) -> Optional[int]:
    for c, a, b in windows:
        if a <= t and (b is None or t <= b):
            return c
    return None


# ── cycles ──────────────────────────────────────────────────────────────

def _thin(path: list, n: int = 120) -> list:
    """At most n points, evenly picked, always keeping the last."""
    if len(path) <= n:
        return path
    step = (len(path) - 1) / (n - 1)
    return [path[round(i * step)] for i in range(n)]


def _side_series(prs: list, mark_rows: list) -> list[dict]:
    """CE and PE totals of the short legs open at each mark, and the
    smaller ÷ bigger balance. One snapshot's marks share a minute, if not
    always the exact instant."""
    marks: dict = {}
    for r in mark_rows:
        marks.setdefault(r["marked_at"].replace(second=0, microsecond=0), {})[r["position_id"]] = r["price"]
    by_id = {p["id"]: p for p in prs}
    out = []
    for t in sorted(marks):
        ce = pe = 0.0
        for pid, px in marks[t].items():
            p = by_id.get(pid)
            if p is None or p["hedge"]:
                continue
            if p["opened_at"] <= t and (p["closed_at"] is None or p["closed_at"] > t):
                if p["opt"] == "CE":
                    ce += float(px)
                elif p["opt"] == "PE":
                    pe += float(px)
        if ce > 0 and pe > 0:
            out.append({"t": _iso(t), "ce": round(ce, 2), "pe": round(pe, 2),
                        "ratio": round(min(ce, pe) / max(ce, pe), 4)})
    return out

_MARKS_LATE = timedelta(hours=1)


def _leg_prices(positions: list, marks: dict, candles: dict) -> tuple[dict, bool]:
    """Each leg's price history, oldest first: its 5-minute marks, and
    stored candle closes for the part of its life before marks began
    (legs older than the marks, or a gap before the first one). Returns
    (prices by position id, whether any candle was used)."""
    out, used = {}, False
    for p in positions:
        m = marks.get(p["id"], [])
        first = m[0][0] if m else None
        if first is not None and first - p["opened_at"] <= _MARKS_LATE:
            out[p["id"]] = m
            continue
        c = [(t, px) for t, px in candles.get(int(p["instrument_token"]), [])
             if t >= p["opened_at"] and (first is None or t < first)
             and (p["closed_at"] is None or t < p["closed_at"])]
        used = used or bool(c)
        out[p["id"]] = c + m
    return out, used


def _pnl_path(positions: list, marks: dict, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """Cycle P&L at every price time: closed legs at their realized P&L,
    open legs at their last price then. A time where an open leg has no
    price yet is left out."""
    series = {p["id"]: marks.get(p["id"], []) for p in positions}
    keys = {pid: [t for t, _ in s] for pid, s in series.items()}
    times = sorted({t for s in series.values() for t, _ in s if start <= t <= end})
    path = []
    for t in times:
        total, known = 0.0, True
        for p in positions:
            if p["opened_at"] > t:
                continue
            if p["closed_at"] is not None and p["closed_at"] <= t:
                total += float(p["realized_pnl"])
                continue
            i = bisect_right(keys[p["id"]], t)
            if not i:
                known = False
                break
            px = series[p["id"]][i - 1][1]
            entry, qty = float(p["avg_entry_price"]), float(p["qty"] or p.get("total_qty") or 0)
            total += (entry - px) * qty if p["side"] == "short" else (px - entry) * qty
        if known:
            path.append((t, total))
    return path


def _market_minutes(a: datetime, b: datetime) -> float:
    """Weekday market minutes (09:15–15:30 IST) between a and b."""
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


def _gaps(times: list, missing_min: float = 90) -> list[int]:
    """Indices i where the market was open for over 90 minutes between
    point i-1 and point i with no price: Kite offline, a holiday. Nights
    and weekends aren't gaps."""
    return [i for i in range(1, len(times)) if _market_minutes(times[i - 1], times[i]) > missing_min]


def _booked_path(positions: list, start: datetime, end: Optional[datetime]) -> list[tuple[datetime, float]]:
    """With no prices at all: 0 at the start, then what had been booked
    each time a leg closed. Open legs count as 0, so it's a lower-detail
    outline, not the cycle's real ups and downs."""
    path, total = [(start, 0.0)], 0.0
    for p in sorted((p for p in positions if p["closed_at"] is not None and (end is None or p["closed_at"] < end)),
                    key=lambda p: p["closed_at"]):
        total += float(p["realized_pnl"])
        path.append((p["closed_at"], total))
    return path


async def cycles(ctx: Ctx) -> dict:
    book = await _book(ctx)
    now = ctx.now or datetime.now(IST)
    target = float(ctx.config.get("checkpoint_profit_pct_of_capital", 0.005)) * ctx.capital
    groups: dict = {}
    for p in book["positions"]:
        if p["cycle_id"] is not None:
            groups.setdefault(int(p["cycle_id"]), []).append(p)
    ids = [p["id"] for p in book["positions"]]
    marks: dict = {}
    if ids:
        first = min(p["opened_at"] for p in book["positions"])
        for r in await queries.list_marks_for_positions(ctx.pool, ids, first, now):
            marks.setdefault(r["position_id"], []).append((r["marked_at"], r["price"]))
    # legs from before the marks began: stored candle closes, 15 minutes apart
    late = [p for p in book["positions"]
            if not marks.get(p["id"]) or marks[p["id"]][0][0] - p["opened_at"] > _MARKS_LATE]
    candles: dict = {}
    if late:
        for r in await queries.list_candle_closes(
                ctx.pool, sorted({int(p["instrument_token"]) for p in late}),
                min(p["opened_at"] for p in late), now):
            # stamped at the bucket's start, so every leg's closes line up
            # (an illiquid leg's last trade in a bucket can be minutes early)
            t = r["minute"]
            candles.setdefault(int(r["instrument_token"]), []).append((t - timedelta(minutes=t.minute % 15), r["c"]))
    decs =[dict(d) for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], limit=5000)]
    rows = []
    for cid, prs in sorted(groups.items()):
        start = min(p["opened_at"] for p in prs)
        is_open = any(p["status"] == "open" for p in prs)
        end = None if is_open else max(p["closed_at"] for p in prs)
        acts = [a for a in book["actions"] if a["cycle_id"] == cid]
        exits = [a for a in acts if a["kind"] == "exit"]
        ended_by = "open" if is_open else (exits[-1]["reason"] if exits else
                                           next((a["reason"] for a in reversed(acts) if not
                                                 any(f["open"] for f in a["fills"])), "other"))
        rolls = [a for a in acts if a["kind"] == "roll"]
        closed = [p for p in prs if p["status"] == "closed"]
        gross = sum(float(p["realized_pnl"]) for p in closed)
        charges = sum(float(p["estimated_charges"]) for p in closed)
        slip = sum(float(p["estimated_slippage"]) for p in closed)
        prices, used_candles = _leg_prices(prs, marks, candles)
        path = [p for p in _pnl_path(prs, prices, start, end or now) if p[0] > start]
        kind = "candles" if used_candles else "marks"
        if not path:
            path, kind = _booked_path(prs, start, end), "booked"
        elif path[0][0] > start:   # it opened at 0
            path = [(start, 0.0)] + path
        if end is not None:   # a closed cycle ends at its result, not at its last price
            path = [p for p in path if p[0] < end] + [(end, sum(float(p["realized_pnl"]) for p in prs))]
        ctx._cache.setdefault("cycle_parts", {})[cid] = {"prs": prs, "acts": acts, "path": path, "prices": prices,
                                                         "path_kind": kind}
        expiry = next((a["contract_expiry"] for a in acts if a["contract_expiry"]), None)
        eod_checks = [d for d in decs if d["rule"] == "eod_gap_check" and d["decided_at"] >= start
                      and (end is None or d["decided_at"] <= end)]
        open_pnl = None
        if is_open and ctx.strategy is not None and getattr(ctx.strategy, "cycle_id", None) == cid:
            eng = engine_from_strategy(ctx.strategy, ctx.runner, now)
            ck = next((r for r in eng["rules"] if r["key"] == "checkpoint_target"), None)
            if ck is not None:
                open_pnl = ck["value"]
        elif is_open and path:
            open_pnl = round(path[-1][1], 2)
        thin = _thin(path, 80)
        rows.append({
            "cycle_id": cid, "start": _iso(start), "end": _iso(end), "status": "open" if is_open else "closed",
            "contract_expiry": expiry, "days": round(((end or now) - start).total_seconds() / 86400, 1),
            "ended_by": ended_by, "rolls": len(rolls),
            "rolls_by_kind": {k: sum(1 for a in rolls if ROLL_KIND[a["reason"]] == k)
                              for k in ("continuous", "eod", "rollout")},
            "eod_checks_no_action": sum(1 for d in eod_checks if d["outcome"] == "no_action"),
            # never above / below zero: a cycle that was never down has no dip
            # (booked-only outlines don't see the dips and peaks in between)
            "mae": round(min(0.0, *(v for _, v in path)), 2) if path and kind != "booked" else None,
            "mfe": round(max(0.0, *(v for _, v in path)), 2) if path and kind != "booked" else None,
            "realized_pnl": round(gross, 2), "charges": round(charges, 2), "slippage": round(slip, 2),
            "pnl": round(gross, 2) if not is_open else open_pnl,
            "pnl_net": round(gross - charges - slip, 2) if not is_open else None,
            "legs": len([p for p in prs if not p["hedge"]]),
            # cycle P&L over its life, for the journeys chart: [hours since start, ₹]
            "path": [[round((t - start).total_seconds() / 3600, 2), round(v, 2)] for t, v in thin],
            "path_gaps": [] if kind == "booked" else _gaps([t for t, _ in thin]),
            # where it came from: 5-minute marks, stored candles (older legs), or only what was booked
            "path_kind": kind,
        })
    closed_rows = [r for r in rows if r["status"] == "closed"]
    ck_days = [r["days"] for r in closed_rows if r["ended_by"] == "checkpoint_target"]
    ended = {}
    for r in closed_rows:
        ended[r["ended_by"]] = ended.get(r["ended_by"], 0) + 1
    maes = [r["mae"] for r in closed_rows if r["mae"] is not None]
    mfes = [r["mfe"] for r in closed_rows if r["mfe"] is not None]
    months: dict = {}
    for p in book["positions"]:
        if p["status"] == "closed":
            k = _ist(p["closed_at"]).strftime("%Y-%m")
            months[k] = months.get(k, 0.0) + float(p["realized_pnl"]) - float(p["estimated_charges"]) \
                - float(p["estimated_slippage"])
    monthly_target = float(ctx.config.get("monthly_target_pct", 0.02)) * ctx.capital
    return {
        "rows": list(reversed(rows)), "checkpoint_target": target,
        "summary": {
            "cycles": len(rows), "closed": len(closed_rows), "open": len(rows) - len(closed_rows),
            "ended_by": ended,
            "median_days_to_checkpoint": round(statistics.median(ck_days), 1) if ck_days else None,
            "fastest_checkpoint_days": min(ck_days) if ck_days else None,
            "slowest_checkpoint_days": max(ck_days) if ck_days else None,
            "avg_mae": round(sum(maes) / len(maes), 2) if maes else None,
            "avg_mfe": round(sum(mfes) / len(mfes), 2) if mfes else None,
            "worst_mae": min(maes) if maes else None,
        },
        "monthly": {"target": monthly_target,
                    "months": [{"month": k, "pnl_net": round(v, 2)} for k, v in sorted(months.items())]},
    }


# ── adjustments ─────────────────────────────────────────────────────────

async def _price_at(ctx: Ctx, token: int, t: datetime, symbol: Optional[str] = None) -> Optional[float]:
    """Last 1-minute close at or before t from the candle store. A leg
    closed by a roll stops being recorded, so its later minutes are
    filled from Kite once (ctx.candles, rate-limited) and kept."""
    t = _ist(t)
    rows = await queries.list_candles(ctx.pool, token, t - timedelta(days=5), t + timedelta(minutes=1))
    day_start = datetime.combine(t.date(), time(9, 15), IST)
    have_today = rows and rows[-1]["minute"] >= day_start
    if not have_today and ctx.candles is not None and t > day_start:
        try:
            await ctx.candles.ensure(token, day_start, t + timedelta(minutes=1), symbol=symbol)
        except Exception:
            pass    # no Kite session, contract expired, ...: score what we can
        rows = await queries.list_candles(ctx.pool, token, t - timedelta(days=5), t + timedelta(minutes=1))
    return float(rows[-1]["c"]) if rows else None


async def adjustments(ctx: Ctx) -> dict:
    book = await _book(ctx)
    now = ctx.now or datetime.now(IST)
    by_id = book["by_id"]
    live = {}
    if ctx.strategy is not None and ctx.runner is not None:
        live = dict(ctx.runner.dispatcher.last_prices)   # the open new legs only
    rows = []
    for a in book["actions"]:
        if a["kind"] != "roll":
            continue
        closed, opened = _shorts(a["fills"], False), _shorts(a["fills"], True)
        tv = a["trigger_values"] or {}
        side = (opened or closed or [{}])[0].get("opt")
        row = {"t": _iso(a["t"]), "rule": ROLL_KIND[a["reason"]], "reason": a["reason"], "cycle_id": a["cycle_id"],
               "side": side,
               "direction": _direction(side or "", closed[0]["strike"] if closed else None,
                                       opened[0]["strike"] if opened else None) or None,
               "closed": [{k: f[k] for k in ("symbol", "opt", "strike", "price", "pnl")} for f in closed],
               "opened": [{k: f[k] for k in ("symbol", "opt", "strike", "price")} for f in opened],
               "ratio_before": None, "ratio_after": None, "costs": None, "if_held": None,
               "roll_pnl": None, "roll_paid": None, "note": None}
        if tv.get("smaller_sum") and tv.get("bigger_sum"):
            ss, bs = float(tv["smaller_sum"]), float(tv["bigger_sum"])
            row["ratio_before"] = round(ss / bs, 4)
            if closed and opened:
                delta = sum(f["price"] for f in opened) - sum(f["price"] for f in closed)
                if a["reason"] == "eod_rollout":       # the bigger (tested) side was replaced
                    row["ratio_after"] = round(ss / (bs + delta), 4) if bs + delta > 0 else None
                else:
                    row["ratio_after"] = round((ss + delta) / bs, 4)
        if len(closed) == 1 and len(opened) == 1:
            c, o = closed[0], opened[0]
            new_pos = by_id.get(UUID(o["position_id"]))
            horizon = new_pos["closed_at"] if new_pos and new_pos["closed_at"] is not None else now
            # The new leg: realized if closed, else marked to the live price
            # (or the last stored candle).
            if new_pos and new_pos["status"] == "closed":
                new_pnl = float(new_pos["realized_pnl"])
                costs = float(new_pos["estimated_charges"]) + float(new_pos["estimated_slippage"])
            else:
                px = live.get(o["token"])
                if px is None:
                    px = await _price_at(ctx, o["token"], now)
                new_pnl = None if px is None else (o["price"] - px) * o["qty"]
                costs = None
            # Never the dispatcher's last price for the closed leg: it
            # stops updating once the leg is released.
            held_px = await _price_at(ctx, c["token"], horizon, symbol=c["symbol"])
            if held_px is not None and c["pnl"] is not None:
                old_pos = by_id.get(UUID(c["position_id"]))
                entry = float(old_pos["avg_entry_price"]) if old_pos else None
                if entry is not None:
                    row["if_held"] = round((entry - held_px) * c["qty"], 2)
            row["costs"] = None if costs is None else round(costs, 2)
            if new_pnl is not None and c["pnl"] is not None:
                row["roll_pnl"] = round(c["pnl"] + new_pnl, 2)
                if row["if_held"] is not None:
                    row["roll_paid"] = round(row["roll_pnl"] - row["if_held"] - (costs or 0), 2)
            if row["if_held"] is None:
                row["note"] = "No stored price history for the closed leg at that time"
            row["horizon"] = _iso(horizon)
            row["new_leg_open"] = not (new_pos and new_pos["status"] == "closed")
        rows.append(row)
    by_rule = {}
    for k in ("continuous", "eod", "rollout"):
        rs = [r for r in rows if r["rule"] == k]
        scored = [r for r in rs if r["roll_paid"] is not None]
        by_rule[k] = {"rolls": len(rs), "scored": len(scored), "helped": sum(1 for r in scored if r["roll_paid"] > 0),
                      "net": round(sum(r["roll_paid"] for r in scored), 2)}
    scored = [r for r in rows if r["roll_paid"] is not None]
    costs = [r["costs"] for r in rows if r["costs"] is not None]
    return {
        "rows": list(reversed(rows)), "by_rule": by_rule,
        "summary": {"rolls": len(rows), "scored": len(scored), "helped": sum(1 for r in scored if r["roll_paid"] > 0),
                    "net": round(sum(r["roll_paid"] for r in scored), 2) if scored else None,
                    "avg_cost": round(sum(costs) / len(costs), 2) if costs else None},
        "method": "Each roll against keeping the leg it closed until the new leg was closed (or now), "
                  "priced from stored 1-minute candles. Roll paid = closed leg's booked P&L + new leg's P&L "
                  "− if held − the new leg's charges and slippage.",
    }


# ── entries ─────────────────────────────────────────────────────────────

async def entries(ctx: Ctx) -> dict:
    book = await _book(ctx)
    rows = []
    for a in book["actions"]:
        if a["kind"] != "entry":
            continue
        tv = a["trigger_values"] or {}
        legs = []
        for f in sorted(_shorts(a["fills"], True), key=lambda f: f["opt"] or ""):
            tb = a["target_basis"].get(f["opt"]) or {}
            legs.append({"opt": f["opt"], "symbol": f["symbol"], "strike": f["strike"], "price": f["price"],
                         "book_price": f["book_price"], "target": _f(tb.get("target_premium")),
                         "slip_pts": round(abs(f["price"] - f["book_price"]), 2) if f["book_price"] else None})
        targets = [l["target"] for l in legs if l["target"]]
        rows.append({
            "t": _iso(a["t"]), "trigger": a["reason"], "cycle_id": a["cycle_id"], "contract_expiry": a["contract_expiry"],
            "day_of_month": tv.get("day_of_month"), "rotation": tv.get("rotation_selector"),
            "days_to_expiry": tv.get("days_to_expiry"), "long_days": bool(tv.get("long_days_override")),
            "capital_pct": _f(tv.get("strike_selection_capital_pct")),
            "target": round(targets[0], 2) if targets else None, "legs": legs,
            "hedges": [{"symbol": f["symbol"], "price": f["price"]} for f in a["fills"] if f["hedge"]],
            "why": _action_text(a)[1],
        })
    return {"rows": list(reversed(rows))}


# ── premium & decay ─────────────────────────────────────────────────────

async def premium(ctx: Ctx) -> dict:
    book = await _book(ctx)
    now = ctx.now or datetime.now(IST)
    shorts = [p for p in book["positions"] if not p["hedge"]]
    sold = sum(f["price"] * f["qty"] for a in book["actions"] for f in a["fills"] if not f["hedge"] and f["open"])
    closed = [p for p in book["positions"] if p["status"] == "closed"]
    roles: dict = {}
    for p in closed:
        k = "hedge" if p["hedge"] else ("original" if (p["role"] or "original") == "original" else "adjustment")
        roles[k] = roles.get(k, 0.0) + float(p["realized_pnl"])
    by_close: dict = {}
    for a in book["actions"]:
        for f in a["fills"]:
            if not f["open"] and f["pnl"] is not None and not f["hedge"]:
                k = "roll" if a["kind"] == "roll" else ("cycle end" if a["kind"] == "exit" else "other")
                by_close[k] = by_close.get(k, 0.0) + f["pnl"]
    charges = sum(float(p["estimated_charges"]) for p in closed)
    slip = sum(float(p["estimated_slippage"]) for p in closed)
    gross = sum(float(p["realized_pnl"]) for p in closed)
    # The open cycle's side totals over time, from the 5-minute marks.
    series = []
    open_cycle = next((p["cycle_id"] for p in shorts if p["status"] == "open"), None)
    if open_cycle is not None:
        prs = [p for p in shorts if p["cycle_id"] == open_cycle]
        start = min(p["opened_at"] for p in prs)
        rows = await queries.list_marks_for_positions(ctx.pool, [p["id"] for p in prs], start, now)
        series = _side_series(prs, rows)
    return {
        "premium_sold": round(sold, 2), "kept": round(gross - charges - slip, 2), "gross": round(gross, 2),
        "charges": round(charges, 2), "slippage": round(slip, 2),
        "by_role": {k: round(v, 2) for k, v in roles.items()},
        "by_close": {k: round(v, 2) for k, v in by_close.items()},
        "open_cycle": open_cycle, "series": series,
    }


# ── settings at work ────────────────────────────────────────────────────

_SETTING_HELP = {
    "entry_time": "Time of day fresh entries are allowed from",
    "strike_selection_capital_pct": "Per-leg premium target, % of capital",
    "long_days_target_percentage": "Per-leg target when the contract is > 29 days out (other month)",
    "checkpoint_profit_pct_of_capital": "Cycle profit that flattens and re-enters",
    "checkpoint_exit_not_before": "Checkpoint exits wait until this time",
    "monthly_target_pct": "Monthly target (informational only)",
    "adjustment_trigger_ratio": "Roll now when the smaller side ≤ this × bigger",
    "adjustment_band_min": "Rolls aim between band min and max",
    "adjustment_band_max": "Rolls aim between band min and max",
    "eod_check_time": "Daily check time",
    "eod_gap_floor": "Daily check rolls when smaller < this × bigger",
    "eod_rollout_instead_of_straddle": "At the daily check, roll the tested side out rather than make a straddle",
    "convergence_mode": "What happens once strikes converge",
    "convergence_stop_pct": "Stop distance after convergence",
    "enable_hedging": "Buy a protective wing per short",
    "force_close_at_contract_expiry": "Close everything on expiry day",
    "lots_per_trade": "Lots per leg",
}


async def settings(ctx: Ctx) -> dict:
    book = await _book(ctx)
    defaults = _defaults()
    cfg = ctx.config
    acts = book["actions"]
    decs = [dict(d) for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], limit=5000)]
    entries_ = [a for a in acts if a["kind"] == "entry"]
    rolls = [a for a in acts if a["kind"] == "roll"]
    exits = [a for a in acts if a["kind"] == "exit"]
    long_days = sum(1 for a in entries_ if (a["trigger_values"] or {}).get("long_days_override"))
    n = lambda xs, reason: sum(1 for a in xs if a["reason"] == reason)  # noqa: E731
    eod_noop = sum(1 for d in decs if d["rule"] == "eod_gap_check" and d["outcome"] == "no_action")
    refuted = sum(1 for d in decs if d["outcome"] == "blocked" and d["rule"] != "stale_price_guard")
    landed = [r for r in (await adjustments(ctx))["rows"] if r["ratio_after"] is not None and r["reason"] != "eod_rollout"]
    effects = {
        "entry_time": f"{len(entries_)} entr{'y' if len(entries_) == 1 else 'ies'} placed",
        "strike_selection_capital_pct": f"used on {len(entries_) - long_days} entr{'y' if len(entries_) - long_days == 1 else 'ies'}",
        "long_days_target_percentage": f"used on {long_days} entr{'y' if long_days == 1 else 'ies'}",
        "checkpoint_profit_pct_of_capital":
            f"{_rupees(float(cfg.get('checkpoint_profit_pct_of_capital', defaults.get('checkpoint_profit_pct_of_capital', 0))) * ctx.capital)} · hit {n(exits, 'checkpoint_target')} time(s)",
        "adjustment_trigger_ratio": f"fired {n(rolls, 'continuous_50pct_trigger')} time(s)",
        "adjustment_band_min": (f"rolls landed {min(r['ratio_after'] for r in landed) * 100:.0f}–"
                                f"{max(r['ratio_after'] for r in landed) * 100:.0f}%") if landed else "no rolls yet",
        "eod_gap_floor": f"rolled {n(rolls, 'eod_gap_check')} time(s) · nothing to do {eod_noop} time(s)",
        "eod_rollout_instead_of_straddle": f"{n(rolls, 'eod_rollout')} roll-out(s)",
        "convergence_stop_pct": f"stopped {n(exits, 'convergence_stop')} cycle(s)",
        "force_close_at_contract_expiry": f"closed {n(exits, 'contract_expiry_backstop')} cycle(s) on expiry day",
        "enable_hedging": f"{sum(1 for p in book['positions'] if p['hedge'])} wing(s) bought",
    }
    rows = []
    for k in list(defaults.keys()) + [k for k in cfg if k not in defaults]:
        if k == "instrument_tokens":
            continue
        val = cfg.get(k, defaults.get(k))
        rows.append({"key": k, "value": val, "default": defaults.get(k), "changed": k in cfg and cfg[k] != defaults.get(k),
                     "help": _SETTING_HELP.get(k), "effect": effects.get(k)})
    return {"rows": rows, "quote_refutes": refuted}


# ── one cycle, end to end ───────────────────────────────────────────────

async def cycle(ctx: Ctx) -> dict:
    """Everything about one cycle (?c=<cycle_id>, default the newest): its
    row from cycles(), how it was entered, every leg, every action and
    check in order, its rolls scored, its P&L path with the moments that
    moved it, the side balance and CE/PE premium, and the money."""
    all_cycles = await cycles(ctx)
    rows = {r["cycle_id"]: r for r in all_cycles["rows"]}
    if not rows:
        return {"found": False, "cycle_id": None, "ids": []}
    ids = sorted(rows)
    try:
        cid = int(ctx.params.get("c")) if ctx.params.get("c") not in (None, "") else ids[-1]
    except ValueError:
        cid = None
    if cid not in rows:
        return {"found": False, "cycle_id": cid, "ids": ids}
    row = rows[cid]
    parts = ctx._cache["cycle_parts"][cid]
    prs, acts, path = parts["prs"], parts["acts"], parts["path"]
    now = ctx.now or datetime.now(IST)
    start = min(p["opened_at"] for p in prs)
    end = None if row["status"] == "open" else max(p["closed_at"] for p in prs)

    # every action, and every check that didn't trade, in order
    timeline = []
    for a in acts:
        what, why = _action_text(a)
        timeline.append({"t": _iso(a["t"]), "kind": a["kind"], "rule": a["reason"],
                         "label": RULE_LABELS.get(a["reason"], a["reason"].replace("_", " ").capitalize())
                         if a["kind"] != "entry" else "Entry",
                         "outcome": "acted", "summary": what, "why": why})
    for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], since=start, limit=5000):
        if end is not None and d["decided_at"] > end:
            continue
        timeline.append({"t": _iso(d["decided_at"]), "kind": "check", "rule": d["rule"],
                         "label": RULE_LABELS.get(d["rule"], d["rule"].replace("_", " ").capitalize()),
                         "outcome": d["outcome"], "summary": d["summary"], "why": ""})
    timeline.sort(key=lambda x: x["t"])

    # every leg, in the order it was opened
    close_reason = {}
    for a in acts:
        for f in a["fills"]:
            if not f["open"]:
                close_reason[f["position_id"]] = a["reason"]
    legs = []
    for p in sorted(prs, key=lambda p: (p["opened_at"], p["opt"] or "")):
        legs.append({
            "symbol": p["symbol"], "opt": p["opt"], "strike": p["strike"], "side": p["side"], "role": p["role"],
            "hedge": p["hedge"], "qty": float(p["total_qty"] or p["qty"] or 0),
            "opened_at": _iso(p["opened_at"]), "closed_at": _iso(p["closed_at"]), "status": p["status"],
            "entry": float(p["avg_entry_price"]), "exit": _f(p.get("exit_price")),
            "pnl": round(float(p["realized_pnl"]), 2) if p["status"] == "closed" else None,
            "charges": round(float(p["estimated_charges"]), 2) if p["status"] == "closed" else None,
            "slippage": round(float(p["estimated_slippage"]), 2) if p["status"] == "closed" else None,
            "closed_by": close_reason.get(str(p["id"])),
        })

    rolls = [r for r in (await adjustments(ctx))["rows"] if r["cycle_id"] == cid]
    entry = next((e for e in (await entries(ctx))["rows"] if e["cycle_id"] == cid), None)
    # the same prices as the P&L path: marks, or candles for older legs
    side = _side_series(prs, [{"position_id": pid, "marked_at": t, "price": px}
                              for pid, s in parts["prices"].items() for t, px in s
                              if start <= t <= (end or now)])
    thin = _thin(path, 400)
    sold = sum(f["price"] * f["qty"] for a in acts for f in a["fills"] if not f["hedge"] and f["open"])
    roles: dict = {}
    for p in prs:
        if p["status"] == "closed":
            k = "hedge" if p["hedge"] else ("original" if (p["role"] or "original") == "original" else "adjustment")
            roles[k] = round(roles.get(k, 0.0) + float(p["realized_pnl"]), 2)
    i = ids.index(cid)
    target = all_cycles["checkpoint_target"]
    cfg = {**_defaults(), **ctx.config}
    return {
        "found": True, "cycle_id": cid, "ids": ids,
        # every cycle's row and journey, to set this one against the others
        "cycles": all_cycles["rows"],
        "prev": ids[i - 1] if i > 0 else None, "next": ids[i + 1] if i < len(ids) - 1 else None,
        "row": {k: v for k, v in row.items() if k != "path"},
        "checkpoint_target": target, "capital": ctx.capital,
        "entry": entry, "legs": legs, "timeline": timeline, "rolls": list(reversed(rolls)),
        "path": [{"t": _iso(t), "pnl": round(v, 2)} for t, v in thin], "path_kind": parts["path_kind"],
        "path_gaps": [] if parts["path_kind"] == "booked" else _gaps([t for t, _ in thin]),
        "side": _thin(side, 400),
        "money": {
            "premium_sold": round(sold, 2), "gross": row["realized_pnl"], "charges": row["charges"],
            "slippage": row["slippage"], "net": round(row["realized_pnl"] - row["charges"] - row["slippage"], 2),
            "open": row["pnl"] if row["status"] == "open" else None, "by_role": roles,
        },
        "rules": {"trigger": float(cfg["adjustment_trigger_ratio"]), "floor": float(cfg["eod_gap_floor"]),
                  "band": [float(cfg["adjustment_band_min"]), float(cfg["adjustment_band_max"])]},
    }


VIEWS = {
    "engine": engine, "decisions": decisions, "cycles": cycles, "cycle": cycle, "adjustments": adjustments,
    "entries": entries, "premium": premium, "settings": settings, "conditions": common.conditions, "volatility": common.volatility,
}
