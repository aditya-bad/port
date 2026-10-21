"""
live_deploy — Geek mode for intraday_dtt_adjusted and intraday_dtt_advanced.

Both sell the ATM straddle at `entry_time` and, instead of a per-leg
stop, add legs to the side that has fallen (an adjustment) when it drops
to `adjustment_trigger_ratio` of the other side, unwind the cheapest one
when that side recovers, and flatten everything on the day's profit
target, on a break-even breach of the index, or at `force_exit_time`.
The adjusted one stops adding after `max_adjustments` a day; the
advanced one rolls instead (closes the cheapest leg on that side and
sells a new one) and can widen the break-even band.

Views on top of the shared ones (common.py):
  engine       every rule in the order the strategy checks it, the legs on
               each side against the trigger, and the index inside its
               break-even band
  sessions     every day: the straddle, the adjustments, unwinds and rolls,
               what ended it and what the extra legs earned or cost
  moves        every adjustment, unwind and roll: what it saw, the leg it
               sold or closed and how that leg did
  session      one day end to end (?d=YYYY-MM-DD, default the latest)
"""

from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Optional

from ..db import queries
from . import common
from .common import IST, Ctx, _f, _ist, _iso, label, saw

NAMES = {"entry_time_reached": "Entry", "entry": "Entry", "adjustment": "Adjustment", "reversal_unwind": "Reversal unwind",
         "roll_close": "Roll · close", "roll_open": "Roll · open", "force_exit": "Square-off",
         "breakeven_fallback": "Break-even", "profit_target_total": "Profit target", "exit_checks": "Exit checks"}
FLATTEN = ("profit_target_total", "breakeven_fallback", "force_exit")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SIDES = ("CE", "PE")


def _base(reason: Optional[str]) -> Optional[str]:
    return reason[:-6] if reason and reason.endswith("_sweep") else reason


def _hhmm(s, default: str) -> time:
    try:
        h, m = (s or default).split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        h, m = default.split(":")
        return time(int(h), int(m))


def _cfg(ctx: Ctx, key: str, *fallbacks):
    cfg = ctx.config
    for k in (key, *fallbacks):
        if k in cfg and cfg[k] is not None:
            return cfg[k]
    return common.defaults(ctx.dep["strategy_name"]).get(key)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _mins(m: float) -> str:
    m = max(0, int(round(m)))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def rolling(ctx: Ctx) -> bool:
    return ctx.dep["strategy_name"] == "intraday_dtt_advanced"


def settings_of(ctx: Ctx) -> dict:
    return {"entry_time": _hhmm(_cfg(ctx, "entry_time"), "10:00"), "force_exit": _hhmm(_cfg(ctx, "force_exit_time"), "15:00"),
            "profit_pct": float(_cfg(ctx, "combined_premium_profit_pct", "decay_pct") or 0.10),
            "trigger_ratio": float(_cfg(ctx, "adjustment_trigger_ratio") or 0.5),
            "size_pct": float(_cfg(ctx, "adjustment_size_pct") or 0.25),
            "max_adjustments": int(_cfg(ctx, "max_adjustments") if _cfg(ctx, "max_adjustments") is not None else 2),
            "be_mult": float(_cfg(ctx, "breakeven_multiplier") or 1.0), "rolling": rolling(ctx)}


# ── sessions: one row per trading day ───────────────────────────────────

async def sessions_of(ctx: Ctx) -> list[dict]:
    """Every day it traded, oldest first: each leg with its role, open
    and close, every move, what ended the day and its P&L."""
    if "sessions" in ctx._cache:
        return ctx._cache["sessions"]
    b = await common.book(ctx)
    cfg = settings_of(ctx)
    lots_by: dict = {}
    for l in b["lots"]:
        lots_by.setdefault(l["position_id"], []).append(l)
    days: dict = {}
    for p in sorted((p for p in b["positions"] if p["side"] == "short"), key=lambda x: x["opened_at"]):
        lots = sorted(lots_by.get(p["id"], []), key=lambda x: x["executed_at"])
        ent = next((l for l in lots if l["opening"]), None)
        ext = next((l for l in reversed(lots) if not l["opening"]), None)
        m = (ent or {}).get("metadata") or {}
        closed = p["status"] == "closed"
        t0 = _ist(p["opened_at"])
        leg = {"id": str(p["id"]), "symbol": p["symbol"], "token": p["instrument_token"],
               "side": common._opt(p["symbol"]) or m.get("leg"), "role": m.get("leg_role") or "original",
               "strike": _f(m.get("strike")), "entry": float(ent["price"]) if ent else float(p["avg_entry_price"]),
               "exit": float(ext["price"]) if ext and closed else None, "opened": _iso(t0),
               "closed": _iso(p["closed_at"]) if closed and p.get("closed_at") else None,
               "opened_by": (ent or {}).get("trigger"), "closed_by": _base((ext or {}).get("trigger")) if closed else None,
               "pnl": round(float(p["realized_pnl"]), 2) if closed else None, "qty": float(ent["qty"]) if ent else None,
               "open_values": m.get("trigger_values") or {}, "target_basis": m.get("target_basis") or {},
               "close_values": ((ext or {}).get("metadata") or {}).get("trigger_values") if closed else None,
               "entry_spot": _f(m.get("entry_spot")), "expiry": m.get("expiry")}
        days.setdefault(t0.date().isoformat(), []).append(leg)
    rows = []
    for d, legs in sorted(days.items()):
        orig = [l for l in legs if l["role"] == "original"]
        extra = [l for l in legs if l["role"] != "original"]
        is_open = any(l["closed"] is None for l in legs)
        first = min(legs, key=lambda l: l["opened"])
        spot = next((l["entry_spot"] for l in orig if l["entry_spot"] is not None), None)
        c_in = sum(l["entry"] for l in orig) if len(orig) == 2 else None
        band = cfg["be_mult"] * c_in if c_in is not None else None
        closes = [l for l in legs if l["closed"]]
        last = max(closes, key=lambda l: l["closed"]) if closes else None
        reason = last["closed_by"] if last and not is_open else None
        exit_t = last["closed"] if last and not is_open else None
        # legs open at once, at most
        ev = sorted([(l["opened"], 1) for l in legs] + [(l["closed"], -1) for l in closes])
        cur = peak = 0
        for _, k in ev:
            cur += k
            peak = max(peak, cur)
        expiry = next((l["expiry"] for l in orig if l["expiry"]), None)
        t0 = datetime.fromisoformat(first["opened"])
        rows.append({
            "day": d, "weekday": WEEKDAYS[t0.weekday()], "entry_t": first["opened"],
            "strike": next((l["strike"] for l in orig if l["strike"] is not None), None), "spot": spot,
            "expiry": expiry, "dte": (date.fromisoformat(expiry) - t0.date()).days if expiry else None,
            "combined_entry": round(c_in, 2) if c_in is not None else None,
            "breakeven": [round(spot - band, 2), round(spot + band, 2)] if spot is not None and band is not None else None,
            "adjusted_side": next((l["side"] for l in extra), None),
            "adjustments": sum(1 for l in extra if l["opened_by"] == "adjustment"),
            "rolls": sum(1 for l in extra if l["opened_by"] == "roll_open"),
            "unwinds": sum(1 for l in closes if l["closed_by"] == "reversal_unwind"),
            "max_legs": peak, "legs": sorted(legs, key=lambda l: l["opened"]),
            "reason": reason, "exit_t": exit_t, "open": is_open,
            "held_min": round((datetime.fromisoformat(exit_t) - t0).total_seconds() / 60, 1) if exit_t else None,
            "pnl": round(sum(l["pnl"] for l in legs if l["pnl"] is not None), 2) if not is_open else None,
            "pnl_original": round(sum(l["pnl"] for l in orig if l["pnl"] is not None), 2) if not is_open else None,
            "pnl_extra": round(sum(l["pnl"] for l in extra if l["pnl"] is not None), 2) if not is_open and extra else None,
            "realized": round(sum(l["pnl"] for l in legs if l["pnl"] is not None), 2),
            "target": round(cfg["profit_pct"] * c_in * (orig[0]["qty"] or 0), 2) if c_in is not None and orig and orig[0]["qty"] else None,
        })
    ctx._cache["sessions"] = rows
    return rows


def _group(rows: list, key) -> dict:
    out: dict = {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        g = out.setdefault(k, {"n": 0, "wins": 0, "pnl": 0.0})
        g["n"] += 1
        g["wins"] += 1 if (r["pnl"] or 0) > 0 else 0
        g["pnl"] = round(g["pnl"] + (r["pnl"] or 0), 2)
    for g in out.values():
        g["avg"] = round(g["pnl"] / g["n"], 2)
        g["win_rate"] = round(g["wins"] / g["n"] * 100, 1)
    return out


def _bucket(n: int) -> str:
    return "3+" if n >= 3 else str(n)


async def sessions(ctx: Ctx) -> dict:
    rows = await sessions_of(ctx)
    closed = [r for r in rows if not r["open"]]
    by_exit = _group(closed, lambda r: r["reason"])
    for k, g in by_exit.items():
        g["label"] = label(k, NAMES)
        ts = sorted(r["exit_t"][11:16] for r in closed if r["reason"] == k and r["exit_t"])
        g["median_exit"] = ts[len(ts) // 2] if ts else None
    moves = lambda r: r["adjustments"] + r["rolls"]  # noqa: E731
    by_moves = _group(closed, lambda r: _bucket(moves(r)))
    pnls = [r["pnl"] for r in closed]
    extra = [r["pnl_extra"] for r in closed if r["pnl_extra"] is not None]
    summary = {
        "sessions": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
        "pnl": round(sum(pnls), 2), "wins": sum(1 for p in pnls if p > 0),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else None,
        "avg": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "best": max(pnls) if pnls else None, "worst": min(pnls) if pnls else None,
        "days_adjusted": sum(1 for r in closed if moves(r)),
        "adjustments": sum(r["adjustments"] for r in rows), "rolls": sum(r["rolls"] for r in rows),
        "unwinds": sum(r["unwinds"] for r in rows),
        "extra_pnl": round(sum(extra), 2) if extra else None,
        "original_pnl": round(sum(r["pnl_original"] for r in closed), 2) if closed else None,
        "median_held": round(median([r["held_min"] for r in closed if r["held_min"] is not None]), 1) if closed else None,
        "breakeven_hits": sum(1 for r in closed if r["reason"] == "breakeven_fallback"),
        "rolling": rolling(ctx),
    }
    return {"sessions": list(reversed(rows)), "summary": summary, "by_exit": by_exit,
            "by_moves": [{"moves": k, **by_moves[k]} for k in ("0", "1", "2", "3+") if k in by_moves],
            "by_side": _group([r for r in closed if r["adjusted_side"]], lambda r: r["adjusted_side"]),
            "by_weekday": [{"day": d, **g} for d, g in sorted(_group(closed, lambda r: r["weekday"]).items(), key=lambda x: WEEKDAYS.index(x[0]))],
            "by_dte": [{"dte": d, **g} for d, g in sorted(_group(closed, lambda r: r["dte"]).items())]}


async def moves(ctx: Ctx) -> dict:
    """Every adjustment, unwind and roll leg, newest first: what the
    strategy saw, the leg it sold or closed, and how that leg did."""
    rows = []
    for s in await sessions_of(ctx):
        for l in s["legs"]:
            if l["opened_by"] in ("adjustment", "roll_open"):
                tv = l["open_values"]
                bigger, smaller = _f(tv.get("bigger_now")), _f(tv.get("smaller_total"))
                tb = l["target_basis"]
                rows.append({"t": l["opened"], "day": s["day"], "kind": "roll" if l["opened_by"] == "roll_open" else "adjustment",
                             "side": l["side"], "symbol": l["symbol"], "strike": l["strike"], "role": l["role"],
                             "premium": l["entry"], "target_premium": _f(tb.get("target_premium")),
                             "bigger_now": bigger, "smaller_total": smaller,
                             "ratio": round(smaller / bigger, 3) if bigger and smaller is not None else None,
                             "exit": l["exit"], "closed": l["closed"], "closed_by": l["closed_by"], "pnl": l["pnl"],
                             "held_min": round((datetime.fromisoformat(l["closed"]) - datetime.fromisoformat(l["opened"])).total_seconds() / 60, 1) if l["closed"] else None,
                             "session_pnl": s["pnl"]})
            if l["closed_by"] in ("reversal_unwind", "roll_close"):
                cv = l["close_values"] or {}
                rows.append({"t": l["closed"], "day": s["day"], "kind": "unwind" if l["closed_by"] == "reversal_unwind" else "roll_close",
                             "side": l["side"], "symbol": l["symbol"], "strike": l["strike"], "role": l["role"],
                             "premium": l["exit"], "target_premium": None,
                             "bigger_now": _f(cv.get("bigger_now")), "smaller_total": _f(cv.get("smaller_total")),
                             "ratio": round(cv["smaller_total"] / cv["bigger_now"], 3) if cv.get("bigger_now") and cv.get("smaller_total") is not None else None,
                             "exit": l["exit"], "closed": l["closed"], "closed_by": l["closed_by"], "pnl": l["pnl"],
                             "held_min": round((datetime.fromisoformat(l["closed"]) - datetime.fromisoformat(l["opened"])).total_seconds() / 60, 1),
                             "session_pnl": s["pnl"]})
    rows.sort(key=lambda r: r["t"])
    sold = [r for r in rows if r["kind"] in ("adjustment", "roll") and r["pnl"] is not None]
    run, cum = 0.0, []
    for r in sold:
        run += r["pnl"]
        cum.append({"t": r["t"], "pnl": r["pnl"], "total": round(run, 2), "kind": r["kind"], "symbol": r["symbol"]})
    by_kind: dict = {}
    for r in rows:
        g = by_kind.setdefault(r["kind"], {"n": 0, "pnl": 0.0, "scored": 0, "wins": 0})
        g["n"] += 1
        if r["kind"] in ("adjustment", "roll") and r["pnl"] is not None:
            g["scored"] += 1
            g["pnl"] = round(g["pnl"] + r["pnl"], 2)
            g["wins"] += 1 if r["pnl"] > 0 else 0
    hours: dict = {}
    for r in rows:
        h = int(r["t"][11:13])
        hours.setdefault(h, {"hour": h, "adjustment": 0, "roll": 0, "unwind": 0, "roll_close": 0})[r["kind"]] += 1
    return {"rows": list(reversed(rows)), "cumulative": cum, "by_kind": by_kind,
            "by_hour": [hours[h] for h in sorted(hours)], "rolling": rolling(ctx)}


async def session(ctx: Ctx) -> dict:
    """One day end to end: ?d=YYYY-MM-DD, default the latest."""
    rows = await sessions_of(ctx)
    if not rows:
        return {"session": None, "n": 0}
    want = ctx.params.get("d")
    i = next((j for j, r in enumerate(rows) if r["day"] == want), None) if want else len(rows) - 1
    if i is None:
        return {"session": None, "n": len(rows), "missing": want}
    s = rows[i]
    now = ctx.now or datetime.now(IST)
    t0 = datetime.fromisoformat(s["entry_t"])
    t1 = datetime.fromisoformat(s["exit_t"]) if s["exit_t"] else now
    start, end = t0 - timedelta(minutes=30), min(t1 + timedelta(minutes=30), now + timedelta(minutes=1))
    tok = (ctx.config.get("instrument_tokens") or [None])[0]
    names = {l["token"]: l for l in s["legs"]}
    toks = [t for t in [tok, *names] if t is not None]
    series: dict = {"index": [], "legs": {l["id"]: [] for l in s["legs"]}}
    for c in await queries.list_candle_closes(ctx.pool, toks, start, end, every_s=60):
        if c["instrument_token"] == tok:
            series["index"].append([_iso(c["minute"]), round(float(c["c"]), 2)])
        else:
            l = names[c["instrument_token"]]
            t = _iso(c["minute"])
            if l["opened"][:16] <= t[:16] and (l["closed"] is None or t[:16] <= l["closed"][:16]):
                series["legs"][l["id"]].append([t, round(float(c["c"]), 2)])
    # each side's total and the day's P&L, minute by minute, while every open leg has a price
    at = {lid: dict(map(tuple, pts)) for lid, pts in series["legs"].items()}
    minutes = sorted({t for pts in series["legs"].values() for t, _ in pts})
    sides, pnl = [], []
    for t in minutes:
        live = [l for l in s["legs"] if l["opened"][:16] <= t[:16] and (l["closed"] is None or t[:16] < l["closed"][:16])]
        if not live or any(t not in at[l["id"]] for l in live):
            continue
        tot = {k: round(sum(at[l["id"]][t] for l in live if l["side"] == k), 2) for k in SIDES}
        booked = sum(l["pnl"] for l in s["legs"] if l["closed"] and l["closed"][:16] <= t[:16] and l["pnl"] is not None)
        unreal = sum((l["entry"] - at[l["id"]][t]) * (l["qty"] or 0) for l in live)
        sides.append([t, tot["CE"], tot["PE"], len(live)])
        pnl.append([t, round(booked + unreal, 2)])
    decs = [{"t": _iso(d["decided_at"]), "rule": d["rule"], "label": label(d["rule"], NAMES), "outcome": d["outcome"],
             "summary": d["summary"], "why": saw(d["data"])}
            for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], since=start, limit=500)
            if _ist(d["decided_at"]) <= end]
    return {"session": s, "no": i + 1, "n": len(rows),
            "prev": rows[i - 1]["day"] if i > 0 else None, "next": rows[i + 1]["day"] if i + 1 < len(rows) else None,
            "series": {"index": series["index"], "legs": series["legs"], "sides": sides, "pnl": pnl},
            "settings": {k: (v.strftime("%H:%M") if isinstance(v, time) else v) for k, v in settings_of(ctx).items()},
            "decisions": sorted(decs, key=lambda d: d["t"])}


# ── the engine ──────────────────────────────────────────────────────────

async def readings(ctx: Ctx, legs: list) -> dict:
    """The day as the strategy holds it: off the running strategy, or
    today's session in the book while it isn't running."""
    cfg = settings_of(ctx)
    s = ctx.strategy
    disp = getattr(ctx.runner, "dispatcher", None)
    last = (getattr(disp, "last_prices", {}) or {}) if disp is not None else {}
    tok = (ctx.config.get("instrument_tokens") or [None])[0]
    r = {**cfg, "spot": _f(last.get(tok)) if tok is not None else None, "legs": {k: [] for k in SIDES},
         "adjusted_side": None, "adjustments_used": None, "entered_today": None, "combined_entry": None,
         "breakeven": None, "realized_today": None, "target": None}
    if s is not None and hasattr(s, "legs") and isinstance(getattr(s, "legs"), dict):
        for k in SIDES:
            for l in s.legs.get(k) or []:
                r["legs"][k].append({"symbol": l["symbol"], "role": l.get("role"), "strike": l.get("strike"),
                                     "entry": l["entry_price"], "now": _f(last.get(l["token"]))})
        r["adjusted_side"] = getattr(s, "adjusted_side", None)
        r["adjustments_used"] = getattr(s, "adjustments_used", None)
        r["entered_today"] = bool(getattr(s, "entered_today", False))
        r["combined_entry"] = _f(getattr(s, "combined_entry_premium", None))
        lo, hi = getattr(s, "breakeven_lower", None), getattr(s, "breakeven_upper", None)
        r["breakeven"] = [round(lo, 2), round(hi, 2)] if lo is not None and hi is not None else None
        r["realized_today"] = round(float(getattr(s, "realized_pnl_today", 0) or 0), 2)
        q = getattr(s, "entry_qty", None)
        r["target"] = round(cfg["profit_pct"] * r["combined_entry"] * q, 2) if r["combined_entry"] and q else None
    else:
        for l in legs:
            if l["side"] == "short" and l["opt"] in SIDES:
                r["legs"][l["opt"]].append({"symbol": l["symbol"], "role": l["role"], "strike": l["strike"],
                                            "entry": l["entry"], "now": l["ltp"]})
        today = (ctx.now or datetime.now(IST)).date().isoformat()
        sess = next((x for x in reversed(await sessions_of(ctx)) if x["day"] == today), None)
        if sess:
            r["combined_entry"], r["breakeven"], r["target"] = sess["combined_entry"], sess["breakeven"], sess["target"]
            r["realized_today"] = sess["realized"]
            r["adjusted_side"] = sess["adjusted_side"]
            r["adjustments_used"] = sess["adjustments"] + sess["rolls"]
            r["entered_today"] = True
    return r


def day_state(r: dict) -> dict:
    """Side totals and the comparison the adjustment rule makes."""
    legs = r["legs"]
    tot = {k: (round(sum(l["now"] for l in legs[k]), 2) if legs[k] and all(l["now"] is not None for l in legs[k]) else None) for k in SIDES}
    adj = r["adjusted_side"]
    if adj in SIDES and legs[adj] and legs[{"CE": "PE", "PE": "CE"}[adj]]:
        smaller_side, bigger_side = adj, {"CE": "PE", "PE": "CE"}[adj]
    elif tot["CE"] is not None and tot["PE"] is not None:
        smaller_side, bigger_side = ("PE", "CE") if tot["CE"] >= tot["PE"] else ("CE", "PE")
    else:
        smaller_side = bigger_side = None
    ratio = None
    if smaller_side and tot[smaller_side] is not None and tot[bigger_side]:
        # the bigger side is the anchor's single leg once a side has been adjusted
        bigger = legs[bigger_side][0]["now"] if adj else tot[bigger_side]
        ratio = round(tot[smaller_side] / bigger, 3) if bigger else None
    return {"totals": tot, "smaller": smaller_side, "bigger": bigger_side, "ratio": ratio,
            "legs_open": {k: len(legs[k]) for k in SIDES}}


def rules(r: dict, st: dict, now: datetime, day_pnl: Optional[float]) -> list[dict]:
    """In the strategy's order on a tick: entry, square-off, break-even,
    profit target, the adjustment (or roll) trigger, the reversal unwind,
    and the adjustment budget."""
    t, et, fe = now.time(), r["entry_time"], r["force_exit"]
    holding = any(r["legs"][k] for k in SIDES)
    day = lambda x: datetime.combine(now.date(), x, IST)  # noqa: E731
    open_ = day(time(9, 15))
    out = []
    entered = holding or r["entered_today"]
    to_entry = (day(et) - now).total_seconds() / 60
    out.append({"key": "entry", "label": f"Entry · {et:%H:%M}", "desc": "Sells the ATM CE and PE at one strike, once a day",
                "fill": 1.0 if entered or t >= et else _clamp((now - open_).total_seconds() / max(1, (day(et) - open_).total_seconds())),
                "reading": "sold today" if entered else f"in {_mins(to_entry)}" if t < et else f"{et:%H:%M} passed",
                "left": "09:15", "right": f"{et:%H:%M}",
                "state": "done" if entered else "armed" if 0 < to_entry <= 10 else "ok" if t < et else "idle",
                "chip": "Done today" if entered else "Soon" if 0 < to_entry <= 10 else "Waiting" if t < et else "Not today"})
    left = (day(fe) - now).total_seconds() / 60
    out.append({"key": "force_exit", "label": f"Square-off · {fe:%H:%M}", "desc": f"Everything open is bought back at {fe:%H:%M}",
                "fill": _clamp((now - open_).total_seconds() / max(1, (day(fe) - open_).total_seconds())),
                "reading": f"in {_mins(left)}" if t < fe else f"{fe:%H:%M} passed", "left": "09:15", "right": f"{fe:%H:%M}",
                "state": "done" if t >= fe else "armed" if holding and left <= 15 else "ok" if holding else "idle",
                "chip": "Done today" if t >= fe else "Soon" if holding and left <= 15 else "Holding" if holding else "Nothing open"})
    be, spot = r["breakeven"], r["spot"]
    if be and spot is not None and holding:
        half = (be[1] - be[0]) / 2 or 1
        mid = (be[0] + be[1]) / 2
        room = min(spot - be[0], be[1] - spot)
        fill = _clamp(abs(spot - mid) / half)
        reading = f"index {spot:,.0f} · band {be[0]:,.0f}–{be[1]:,.0f} · {room:,.0f} pts to the {'upper' if spot >= mid else 'lower'} edge"
    else:
        fill, reading = 0.0, (f"band {be[0]:,.0f}–{be[1]:,.0f}" if be else "set at entry")
    out.append({"key": "breakeven", "label": f"Break-even band{' ×' + format(r['be_mult'], 'g') if r['be_mult'] != 1 else ''}",
                "desc": "The index leaving entry spot ± the combined entry premium flattens everything",
                "fill": fill, "reading": reading, "left": "middle", "right": "edge", "tone": "neg",
                "state": "idle" if not holding else "armed" if fill >= 0.85 else "ok",
                "chip": "Nothing open" if not holding else "Near the edge" if fill >= 0.85 else "Inside"})
    tgt = r["target"]
    if tgt and day_pnl is not None and holding:
        fill = _clamp(day_pnl / tgt)
        reading = f"day {day_pnl:,.0f} of {tgt:,.0f}"
    else:
        fill, reading = 0.0, (f"target {tgt:,.0f}" if tgt else "set at entry")
    out.append({"key": "profit_target", "label": f"Profit target · {r['profit_pct'] * 100:.0f}%",
                "desc": "Booked plus open P&L reaching this share of the original straddle's premium flattens everything",
                "fill": fill, "reading": reading, "left": "0", "right": "target", "tone": "pos",
                "state": "idle" if not holding else "armed" if fill >= 0.85 else "ok",
                "chip": "Nothing open" if not holding else "Close to target" if fill >= 0.85 else "Holding"})
    ratio, trig = st["ratio"], r["trigger_ratio"]
    if ratio is not None and holding:
        fill = _clamp((1 - ratio) / (1 - trig)) if trig < 1 else 0.0
        reading = f"{st['smaller']} {st['totals'][st['smaller']]:,.1f} is {ratio * 100:.0f}% of {st['bigger']} · fires at {trig * 100:.0f}%"
    else:
        fill, reading = 0.0, "both sides need prices"
    cap_used = r["adjustments_used"] or 0
    at_cap = (st["legs_open"].get(r["adjusted_side"] or "", 0) - 1 >= r["max_adjustments"]) if r["rolling"] and r["adjusted_side"] \
        else (not r["rolling"] and cap_used >= r["max_adjustments"])
    what = ("rolls the cheapest leg" if at_cap else "adds a leg") if r["rolling"] else ("blocked: cap used" if at_cap else "adds a leg")
    out.append({"key": "adjustment", "label": f"{'Adjust or roll' if r['rolling'] else 'Adjustment'} · {trig * 100:.0f}%",
                "desc": f"The smaller side falling to {trig * 100:.0f}% of the bigger {what} (premium ≈ {r['size_pct'] * 100:.0f}% of the bigger side)",
                "fill": fill, "reading": reading, "left": "even", "right": "fires", "tone": "neg",
                "state": "idle" if not holding or ratio is None else "off" if at_cap and not r["rolling"] else "armed" if fill >= 0.85 else "ok",
                "chip": "Nothing open" if not holding else "Cap used" if at_cap and not r["rolling"] else "About to act" if fill >= 0.85 else "Watching"})
    adj = r["adjusted_side"]
    extra_open = max(0, st["legs_open"].get(adj or "", 0) - 1) if adj else 0
    if extra_open and ratio is not None:
        out.append({"key": "reversal", "label": "Reversal unwind", "desc": "The adjusted side recovering to the anchor's premium closes its cheapest leg",
                    "fill": _clamp(ratio), "reading": f"{adj} side is {ratio * 100:.0f}% of the anchor · unwinds at 100%",
                    "left": "0", "right": "100%", "state": "armed" if ratio >= 0.9 else "ok",
                    "chip": "About to act" if ratio >= 0.9 else f"{extra_open} extra leg{'s' if extra_open != 1 else ''}"})
    else:
        out.append({"key": "reversal", "label": "Reversal unwind", "desc": "Only once a side has extra legs: its recovery closes the cheapest one",
                    "fill": 0.0, "reading": "no extra legs open", "left": "0", "right": "100%", "state": "idle", "chip": "No extra legs"})
    if r["rolling"]:
        out.append({"key": "cap", "label": f"Legs on one side · max {1 + r['max_adjustments']}",
                    "desc": "At the cap a trigger rolls (closes the cheapest, sells a new one) instead of adding",
                    "fill": _clamp(extra_open / r["max_adjustments"]) if r["max_adjustments"] else 1.0,
                    "reading": f"{extra_open} of {r['max_adjustments']} extra legs open · {cap_used} sold today",
                    "left": "0", "right": str(r["max_adjustments"]), "state": "done" if at_cap else "ok",
                    "chip": "At the cap: rolls" if at_cap else f"{r['max_adjustments'] - extra_open} to the cap"})
    else:
        out.append({"key": "cap", "label": f"Adjustments today · max {r['max_adjustments']}",
                    "desc": "A lifetime budget for the day: once used, no more legs are added",
                    "fill": _clamp(cap_used / r["max_adjustments"]) if r["max_adjustments"] else 1.0,
                    "reading": f"{cap_used} of {r['max_adjustments']} used", "left": "0", "right": str(r["max_adjustments"]),
                    "state": "done" if at_cap else "ok", "chip": "Used up" if at_cap else f"{r['max_adjustments'] - cap_used} left"})
    return out


def next_move(r: dict, st: dict, now: datetime, running: bool, base: dict, day_pnl: Optional[float]) -> dict:
    if not running:
        return base
    t, et, fe = now.time(), r["entry_time"], r["force_exit"]
    holding = any(r["legs"][k] for k in SIDES)
    if holding:
        parts = []
        if r["target"] and day_pnl is not None:
            parts.append(f"books everything at a day P&L of {r['target']:,.0f} (now {day_pnl:,.0f})")
        if st["ratio"] is not None:
            parts.append(f"{'adjusts or rolls' if r['rolling'] else 'adjusts'} {st['smaller']} if it falls to {r['trigger_ratio'] * 100:.0f}% of {st['bigger']} (now {st['ratio'] * 100:.0f}%)")
        if r["breakeven"]:
            parts.append(f"flattens if the index leaves {r['breakeven'][0]:,.0f}–{r['breakeven'][1]:,.0f}")
        n = sum(len(r["legs"][k]) for k in SIDES)
        return {"text": f"Holding {n} leg{'s' if n != 1 else ''}: " + "; ".join(parts) + f"; else squares off at {fe:%H:%M}", "at": None}
    if r["entered_today"] or t >= fe:
        return {"text": f"Done for today. Next straddle tomorrow at {et:%H:%M}", "at": None}
    if t < et:
        return {"text": f"Sells the ATM straddle at {et:%H:%M}, in {_mins((datetime.combine(now.date(), et, IST) - now).total_seconds() / 60)}", "at": et.strftime("%H:%M")}
    return {"text": f"Sells the ATM straddle on the next tick ({et:%H:%M} has passed)", "at": None}


async def engine(ctx: Ctx) -> dict:
    now = ctx.now or datetime.now(IST)
    base = await common.engine(ctx)
    r = await readings(ctx, base["legs"])
    st = day_state(r)
    open_pnl = [l["pnl"] for l in base["legs"] if l["side"] == "short"]
    day_pnl = round((r["realized_today"] or 0) + sum(open_pnl), 2) if open_pnl and all(p is not None for p in open_pnl) else None
    holding = any(r["legs"][k] for k in SIDES)
    detail = base["next"].get("detail")
    base["rules"] = rules(r, st, now, day_pnl) if base["running"] or holding else []
    base["next"] = next_move(r, st, now, base["running"], base["next"], day_pnl)
    if detail:
        base["next"]["detail"] = detail
    rows = await sessions_of(ctx)
    base["adjusted"] = {
        "legs": r["legs"], "totals": st["totals"], "ratio": st["ratio"], "smaller": st["smaller"], "bigger": st["bigger"],
        "adjusted_side": r["adjusted_side"], "breakeven": r["breakeven"], "spot": r["spot"], "target": r["target"],
        "day_pnl": day_pnl, "realized_today": r["realized_today"], "combined_entry": r["combined_entry"],
        "trigger_ratio": r["trigger_ratio"], "max_adjustments": r["max_adjustments"], "adjustments_used": r["adjustments_used"],
        "rolling": r["rolling"], "entry_time": r["entry_time"].strftime("%H:%M"), "force_exit": r["force_exit"].strftime("%H:%M"),
        "profit_pct": r["profit_pct"], "last": rows[-1] if rows else None,
    }
    return base


async def decisions(ctx: Ctx) -> dict:
    return await common.decisions(ctx, names=NAMES)


VIEWS = {**common.VIEWS, "engine": engine, "decisions": decisions, "sessions": sessions, "moves": moves, "session": session}
