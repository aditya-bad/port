"""
live_deploy — Geek mode for intraday_dtt_simple (the DTT straddle).

Once a day at `entry_time` it sells the ATM CE and PE at one strike; it
buys both back when either leg's premium is up `per_leg_stop_loss_pct`
from its own entry (checked first), when the combined premium has
decayed `combined_premium_profit_pct`, or at `force_exit_time`.

Views on top of the shared ones (common.py):
  engine     each rule's distance to acting: the entry time, the
             square-off, each leg's stop and the combined target; the
             straddle's legs against their lines; the next move in words
  straddles  every straddle (one a day): what it sold, how it ended, and
             the same by exit, by weekday, by days to expiry and by the
             hour it ended
  straddle   one straddle end to end (?s=<id>, default the newest): each
             leg's premium and the combined premium against the target
             and stops, and the index through the day
"""

from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Optional

from ..db import queries
from . import common
from .common import IST, Ctx, _f, _ist, _iso, label, saw

NAMES = {"entry_time_reached": "Entry", "entry": "Entry", "leg_spike_stop": "Leg stop",
         "profit_target_decay": "Profit target", "force_exit": "Square-off", "exit_checks": "Exit checks"}
EXITS = ("profit_target_decay", "leg_spike_stop", "force_exit")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_SAME = timedelta(seconds=90)


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


# ── live readings ───────────────────────────────────────────────────────

async def readings(ctx: Ctx, legs: list) -> dict:
    """The straddle as the strategy holds it: off the running strategy,
    or the open positions while it isn't running."""
    s = ctx.strategy
    disp = getattr(ctx.runner, "dispatcher", None)
    last = (getattr(disp, "last_prices", {}) or {}) if disp is not None else {}
    r = {"entry_time": _hhmm(_cfg(ctx, "entry_time"), "10:00"),
         "force_exit": _hhmm(_cfg(ctx, "force_exit_time"), "15:00"),
         "profit_pct": float(_cfg(ctx, "combined_premium_profit_pct", "decay_pct") or 0.10),
         "stop_pct": float(_cfg(ctx, "per_leg_stop_loss_pct", "spike_pct") or 0.40),
         "entered_today": None, "ce": None, "pe": None}
    if s is not None and hasattr(s, "ce_token"):
        r["entered_today"] = bool(getattr(s, "entered_today", False))
        for side in ("ce", "pe"):
            tok = getattr(s, f"{side}_token", None)
            if tok is not None:
                r[side] = {"symbol": getattr(s, f"{side}_symbol", None), "entry": _f(getattr(s, f"{side}_entry_price", None)),
                           "now": _f(last.get(tok))}
    else:
        for l in legs:
            side = (l.get("opt") or "").lower()
            if side in ("ce", "pe") and l["side"] == "short":
                r[side] = {"symbol": l["symbol"], "entry": l["entry"], "now": l["ltp"]}
    tok = (ctx.config.get("instrument_tokens") or [None])[0]
    r["spot"] = _f(last.get(tok)) if tok is not None else None
    return r


def straddle_state(r: dict) -> Optional[dict]:
    """Both legs against their lines: each stop, the combined target."""
    ce, pe = r["ce"], r["pe"]
    if not ce and not pe:
        return None
    out = {"legs": {}, "combined": None}
    for k, l in (("CE", ce), ("PE", pe)):
        if l:
            stop = round(l["entry"] * (1 + r["stop_pct"]), 2) if l["entry"] is not None else None
            out["legs"][k] = {**l, "stop": stop,
                              "to_stop": round(stop - l["now"], 2) if stop is not None and l["now"] is not None else None}
    if ce and pe and ce["entry"] is not None and pe["entry"] is not None:
        entry = ce["entry"] + pe["entry"]
        now = ce["now"] + pe["now"] if ce["now"] is not None and pe["now"] is not None else None
        target = round(entry * (1 - r["profit_pct"]), 2)
        out["combined"] = {"entry": round(entry, 2), "now": round(now, 2) if now is not None else None, "target": target,
                           "to_target": round(now - target, 2) if now is not None else None,
                           "decay_pct": round((entry - now) / entry * 100, 1) if now is not None and entry else None}
    return out


def rules(r: dict, now: datetime, running: bool) -> list[dict]:
    """In the order the strategy checks them on a tick: the entry, then
    the square-off, each leg's stop, the combined target."""
    t, et, fe = now.time(), r["entry_time"], r["force_exit"]
    holding = bool(r["ce"] or r["pe"])
    st = straddle_state(r)
    day = lambda x: datetime.combine(now.date(), x, IST)  # noqa: E731
    open_ = day(time(9, 15))
    out = []

    entered = holding or r["entered_today"]
    to_entry = (day(et) - now).total_seconds() / 60
    out.append({"key": "entry", "label": f"Entry · {et:%H:%M}",
                "desc": f"Sells the ATM CE and PE at one strike at {et:%H:%M}, once a day",
                "fill": 1.0 if entered or t >= et else _clamp((now - open_).total_seconds() / max(1, (day(et) - open_).total_seconds())),
                "reading": "sold today" if entered else f"in {_mins(to_entry)}" if t < et else f"{et:%H:%M} passed",
                "left": "09:15", "right": f"{et:%H:%M}",
                "state": "done" if entered else "armed" if 0 < to_entry <= 10 else "ok" if t < et else "idle",
                "chip": "Done today" if entered else "Soon" if 0 < to_entry <= 10 else "Waiting" if t < et else "Not today"})

    left = (day(fe) - now).total_seconds() / 60
    out.append({"key": "force_exit", "label": f"Square-off · {fe:%H:%M}",
                "desc": f"Whatever is open is bought back at {fe:%H:%M}",
                "fill": _clamp((now - open_).total_seconds() / max(1, (day(fe) - open_).total_seconds())),
                "reading": f"in {_mins(left)}" if t < fe else f"{fe:%H:%M} passed", "left": "09:15", "right": f"{fe:%H:%M}",
                "state": "done" if t >= fe else "armed" if holding and left <= 15 else "ok" if holding else "idle",
                "chip": "Done today" if t >= fe else "Soon" if holding and left <= 15 else "Holding" if holding else "Nothing open"})

    for k in ("CE", "PE"):
        l = (st or {}).get("legs", {}).get(k) if st else None
        if l and l["now"] is not None and l["entry"]:
            fill = _clamp((l["now"] - l["entry"]) / (l["entry"] * r["stop_pct"]))
            reading = f"{l['now']:,.2f} · stop {l['stop']:,.2f} · {l['to_stop']:,.2f} to go"
        elif l:
            fill, reading = 0.0, f"stop {l['stop']:,.2f} · no live price"
        else:
            fill, reading = 0.0, "not open"
        out.append({"key": f"stop_{k.lower()}", "label": f"{k} stop · +{r['stop_pct'] * 100:.0f}%",
                    "desc": f"{k} premium up {r['stop_pct'] * 100:.0f}% from its own entry buys both legs back",
                    "fill": fill, "reading": reading, "left": "entry", "right": "stop", "tone": "neg",
                    "state": "idle" if not l else "armed" if fill >= 0.85 else "ok",
                    "chip": "Not open" if not l else "Close to its stop" if fill >= 0.85 else "Holding"})

    c = (st or {}).get("combined") if st else None
    if c and c["now"] is not None:
        fill = _clamp((c["entry"] - c["now"]) / (c["entry"] * r["profit_pct"])) if c["entry"] else 0.0
        reading = f"combined {c['now']:,.2f} · target {c['target']:,.2f} · {c['to_target']:,.2f} to go" if c["to_target"] > 0 \
            else f"combined {c['now']:,.2f} is at the target {c['target']:,.2f}"
    elif c:
        fill, reading = 0.0, f"target {c['target']:,.2f} · no live prices"
    else:
        fill, reading = 0.0, "no straddle open"
    out.append({"key": "profit_target", "label": f"Profit target · −{r['profit_pct'] * 100:.0f}%",
                "desc": f"The combined premium down {r['profit_pct'] * 100:.0f}% from the combined entry books both legs",
                "fill": fill, "reading": reading, "left": "entry", "right": "target", "tone": "pos",
                "state": "idle" if not c else "armed" if fill >= 0.85 else "ok",
                "chip": "Not open" if not c else "Close to target" if fill >= 0.85 else "Holding"})
    return out


def next_move(r: dict, now: datetime, running: bool, base: dict, last_exit: Optional[dict]) -> dict:
    if not running:
        return base
    t, et, fe = now.time(), r["entry_time"], r["force_exit"]
    st = straddle_state(r)
    if st:
        c = st["combined"]
        parts = []
        if c and c["now"] is not None:
            parts.append(f"books at combined {c['target']:,.1f} (now {c['now']:,.1f})")
        stops = [f"{k} ≥ {l['stop']:,.1f}" for k, l in st["legs"].items() if l["stop"] is not None]
        if stops:
            parts.append("stops if " + " or ".join(stops))
        strike = next(iter(st["legs"].values()))["symbol"]
        return {"text": f"Holding the straddle ({strike[:-2]}…): " + ", ".join(parts) + f", else squares off at {fe:%H:%M}", "at": None}
    if r["entered_today"] or t >= fe:
        how = f" by {NAMES.get(last_exit['reason'], label(last_exit['reason']))} at {last_exit['t'][11:16]}" \
            if last_exit and last_exit["t"][:10] == now.date().isoformat() else ""
        return {"text": f"Done for today{': exited' + how if how else ''}. Next straddle tomorrow at {et:%H:%M}", "at": None}
    if t < et:
        return {"text": f"Sells the ATM straddle at {et:%H:%M}, in {_mins((datetime.combine(now.date(), et, IST) - now).total_seconds() / 60)}", "at": et.strftime("%H:%M")}
    return {"text": f"Sells the ATM straddle on the next tick ({et:%H:%M} has passed)", "at": None}


# ── straddles: one row per entry ────────────────────────────────────────

async def straddles_of(ctx: Ctx) -> list[dict]:
    """Every straddle, oldest first: the CE and PE sold together."""
    if "straddles" in ctx._cache:
        return ctx._cache["straddles"]
    b = await common.book(ctx)
    lots_by: dict = {}
    for l in b["lots"]:
        lots_by.setdefault(l["position_id"], []).append(l)
    groups: list[list] = []
    for p in sorted((p for p in b["positions"] if p["side"] == "short"), key=lambda x: x["opened_at"]):
        if groups and p["opened_at"] - groups[-1][0]["opened_at"] <= _SAME:
            groups[-1].append(p)
        else:
            groups.append([p])
    rows = []
    for g in groups:
        legs = {}
        for p in g:
            lots = sorted(lots_by.get(p["id"], []), key=lambda x: x["executed_at"])
            ent = next((l for l in lots if l["opening"]), None)
            ext = next((l for l in reversed(lots) if not l["opening"]), None)
            closed = p["status"] == "closed"
            legs[common._opt(p["symbol"]) or p["symbol"]] = {
                "id": str(p["id"]), "symbol": p["symbol"], "token": p["instrument_token"],
                "entry": float(ent["price"]) if ent else float(p["avg_entry_price"]),
                "exit": float(ext["price"]) if ext and closed else None,
                "exit_t": _iso(p["closed_at"]) if closed and p.get("closed_at") else None,
                "reason": (ext or {}).get("trigger") if closed else None,
                "exit_values": ((ext or {}).get("metadata") or {}).get("trigger_values") if closed else None,
                "pnl": round(float(p["realized_pnl"]), 2) if closed else None,
                "meta": (ent or {}).get("metadata") or {}, "qty": float(ent["qty"]) if ent else None, "open": not closed}
        ce, pe = legs.get("CE"), legs.get("PE")
        first = ce or pe or next(iter(legs.values()))
        m = first["meta"]
        t0 = _ist(g[0]["opened_at"])
        is_open = any(l["open"] for l in legs.values())
        exit_t = max((l["exit_t"] for l in legs.values() if l["exit_t"]), default=None) if not is_open else None
        reasons = [l["reason"] for l in legs.values() if l["reason"]]
        reason = reasons[0] if reasons and all(x == reasons[0] for x in reasons) else (reasons[0] if reasons else None)
        xv = next((l["exit_values"] for l in legs.values() if l["exit_values"]), None) or {}
        stop_leg = None
        if reason == "leg_spike_stop":
            ce_hit = xv.get("ce_now") is not None and xv.get("ce_threshold") is not None and xv["ce_now"] >= xv["ce_threshold"]
            pe_hit = xv.get("pe_now") is not None and xv.get("pe_threshold") is not None and xv["pe_now"] >= xv["pe_threshold"]
            stop_leg = "both" if ce_hit and pe_hit else "CE" if ce_hit else "PE" if pe_hit else None
        c_in = (ce["entry"] + pe["entry"]) if ce and pe else None
        c_out = (ce["exit"] + pe["exit"]) if ce and pe and ce["exit"] is not None and pe["exit"] is not None else None
        expiry = m.get("expiry")
        dte = (date.fromisoformat(expiry) - t0.date()).days if expiry else None
        pnl = round(sum(l["pnl"] for l in legs.values() if l["pnl"] is not None), 2) if not is_open else None
        tv = m.get("trigger_values") or {}
        rows.append({
            "id": first["id"], "day": t0.date().isoformat(), "weekday": WEEKDAYS[t0.weekday()], "entry_t": _iso(t0),
            "strike": _f(m.get("strike")), "expiry": expiry, "dte": dte, "spot": _f(m.get("entry_spot")),
            "ce": {k: v for k, v in ce.items() if k != "meta"} if ce else None,
            "pe": {k: v for k, v in pe.items() if k != "meta"} if pe else None,
            "combined_entry": round(c_in, 2) if c_in is not None else None,
            "combined_exit": round(c_out, 2) if c_out is not None else None,
            "decay_pct": round((c_in - c_out) / c_in * 100, 1) if c_in and c_out is not None else None,
            "exit_t": exit_t, "reason": reason, "stop_leg": stop_leg, "exit_values": xv or None,
            "held_min": round((datetime.fromisoformat(exit_t) - t0).total_seconds() / 60, 1) if exit_t else None,
            "pnl": pnl, "open": is_open, "late_start": bool(tv.get("late_start_today")),
            "switched_to_next_week": bool(tv.get("switched_to_next_week")),
            "price_source": xv.get("price_source"), "qty": first["qty"],
        })
    ctx._cache["straddles"] = rows
    return rows


def _group(rows: list, key) -> dict:
    out: dict = {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        g = out.setdefault(k, {"n": 0, "wins": 0, "pnl": 0.0, "held": []})
        g["n"] += 1
        g["wins"] += 1 if (r["pnl"] or 0) > 0 else 0
        g["pnl"] = round(g["pnl"] + (r["pnl"] or 0), 2)
        if r["held_min"] is not None:
            g["held"].append(r["held_min"])
    for g in out.values():
        g["avg"] = round(g["pnl"] / g["n"], 2)
        g["win_rate"] = round(g["wins"] / g["n"] * 100, 1)
        g["median_held"] = round(median(g["held"]), 1) if g["held"] else None
        del g["held"]
    return out


async def straddles(ctx: Ctx) -> dict:
    rows = await straddles_of(ctx)
    closed = [r for r in rows if not r["open"]]
    by_exit = _group(closed, lambda r: r["reason"])
    for k, g in by_exit.items():
        g["label"] = label(k, NAMES)
        ts = sorted(r["exit_t"][11:16] for r in closed if r["reason"] == k and r["exit_t"])
        g["median_exit"] = ts[len(ts) // 2] if ts else None
    stops = [r for r in closed if r["reason"] == "leg_spike_stop"]
    pnls = [r["pnl"] for r in closed]
    decays = [r["decay_pct"] for r in closed if r["decay_pct"] is not None]
    summary = {
        "straddles": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
        "pnl": round(sum(pnls), 2), "wins": sum(1 for p in pnls if p > 0),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else None,
        "avg": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "best": max(pnls) if pnls else None, "worst": min(pnls) if pnls else None,
        "median_decay": round(median(decays), 1) if decays else None,
        "median_held": round(median([r["held_min"] for r in closed if r["held_min"] is not None]), 1) if closed else None,
        "late_starts": sum(1 for r in rows if r["late_start"]),
    }
    hours = _group(closed, lambda r: int(r["exit_t"][11:13]) if r["exit_t"] else None)
    return {"straddles": list(reversed(rows)), "summary": summary, "by_exit": by_exit,
            "by_weekday": [{"day": d, **g} for d, g in sorted(_group(closed, lambda r: r["weekday"]).items(), key=lambda x: WEEKDAYS.index(x[0]))],
            "by_dte": [{"dte": d, **g} for d, g in sorted(_group(closed, lambda r: r["dte"]).items())],
            "by_exit_hour": [{"hour": h, **g} for h, g in sorted(hours.items())],
            "stop_leg": {k: sum(1 for r in stops if r["stop_leg"] == k) for k in ("CE", "PE", "both")}}


async def straddle(ctx: Ctx) -> dict:
    """One straddle end to end: ?s=<id>, default the newest."""
    rows = await straddles_of(ctx)
    if not rows:
        return {"straddle": None, "n": 0}
    want = ctx.params.get("s")
    i = next((j for j, r in enumerate(rows) if r["id"] == want or any((r[k] or {}).get("id") == want for k in ("ce", "pe"))), None) \
        if want else len(rows) - 1
    if i is None:
        return {"straddle": None, "n": len(rows), "missing": want}
    r = rows[i]
    now = ctx.now or datetime.now(IST)
    t0 = datetime.fromisoformat(r["entry_t"])
    t1 = datetime.fromisoformat(r["exit_t"]) if r["exit_t"] else now
    start, end = t0 - timedelta(minutes=30), min(t1 + timedelta(minutes=30), now + timedelta(minutes=1))
    tok = (ctx.config.get("instrument_tokens") or [None])[0]
    legs = {k: r[k.lower()] for k in ("CE", "PE") if r[k.lower()]}
    toks = [t for t in [tok] + [l["token"] for l in legs.values()] if t is not None]
    series: dict = {"index": [], "CE": [], "PE": []}
    names = {tok: "index", **{l["token"]: k for k, l in legs.items()}}
    for c in await queries.list_candle_closes(ctx.pool, toks, start, end, every_s=60):
        series[names[c["instrument_token"]]].append([_iso(c["minute"]), round(float(c["c"]), 2)])
    ce_at, pe_at = dict(map(tuple, series["CE"])), dict(map(tuple, series["PE"]))
    series["combined"] = [[t, round(ce_at[t] + pe_at[t], 2)] for t in sorted(set(ce_at) & set(pe_at)) if t >= _iso(t0)]
    cfg_profit = float(_cfg(ctx, "combined_premium_profit_pct", "decay_pct") or 0.10)
    cfg_stop = float(_cfg(ctx, "per_leg_stop_loss_pct", "spike_pct") or 0.40)
    lines = {"target": round(r["combined_entry"] * (1 - cfg_profit), 2) if r["combined_entry"] else None,
             "stops": {k: round(l["entry"] * (1 + cfg_stop), 2) for k, l in legs.items()}}
    decs = [{"t": _iso(d["decided_at"]), "rule": d["rule"], "label": label(d["rule"], NAMES), "outcome": d["outcome"],
             "summary": d["summary"], "why": saw(d["data"])}
            for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], since=start, limit=500)
            if _ist(d["decided_at"]) <= end]
    return {"straddle": r, "no": i + 1, "n": len(rows),
            "prev": rows[i - 1]["id"] if i > 0 else None, "next": rows[i + 1]["id"] if i + 1 < len(rows) else None,
            "series": series, "lines": lines, "decisions": sorted(decs, key=lambda d: d["t"])}


async def engine(ctx: Ctx) -> dict:
    now = ctx.now or datetime.now(IST)
    base = await common.engine(ctx)
    r = await readings(ctx, base["legs"])
    rows = await straddles_of(ctx)
    done = [x for x in rows if not x["open"]]
    last_exit = {"reason": done[-1]["reason"], "t": done[-1]["exit_t"]} if done and done[-1]["exit_t"] else None
    detail = base["next"].get("detail")
    # paused or stopped: the clock rules mean nothing, the open legs' lines still do
    base["rules"] = rules(r, now, base["running"]) if base["running"] or r["ce"] or r["pe"] else []
    base["next"] = next_move(r, now, base["running"], base["next"], last_exit)
    if detail:
        base["next"]["detail"] = detail
    st = straddle_state(r)
    base["straddle"] = {"state": st, "entry_time": r["entry_time"].strftime("%H:%M"), "force_exit": r["force_exit"].strftime("%H:%M"),
                        "profit_pct": r["profit_pct"], "stop_pct": r["stop_pct"], "entered_today": r["entered_today"],
                        "spot": r["spot"], "last": rows[-1] if rows else None}
    return base


async def decisions(ctx: Ctx) -> dict:
    return await common.decisions(ctx, names=NAMES)


VIEWS = {**common.VIEWS, "engine": engine, "decisions": decisions, "straddles": straddles, "straddle": straddle}
