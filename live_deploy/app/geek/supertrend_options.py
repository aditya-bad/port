"""
live_deploy — Geek mode for pivot_supertrend_options.

The strategy sells one ATM option at a time: a 5-minute close beyond a
pivot level in the SuperTrend's direction sells a PE (trend up, above
R1/R2/R3) or a CE (trend down, below S1/S2/S3); a SuperTrend flip or the
square-off time buys it back; at most `max_trades_per_day` entries a day.

Views on top of the shared ones (common.py):
  engine   the SuperTrend line and how far the close is from flipping it,
           the pivot ladder with the index on it, each rule's distance to
           acting, and the next move in words
  signals  every trade with what it saw (the level it broke, how far
           past it, how far from the SuperTrend line), how it ended, and
           the same by level, by side, by hour, by exit and by day
  trade    one trade end to end (?p=<position id>, default the newest):
           the index and the option's premium through it, against the
           pivot levels it was taken on
"""

from datetime import datetime, time, timedelta
from statistics import median
from typing import Optional

from ..db import queries
from . import common
from .common import IST, Ctx, _f, _ist, _iso, label, saw

LADDER = ("S3", "S2", "S1", "P", "R1", "R2", "R3")
R_KEYS, S_KEYS = ("R1", "R2", "R3"), ("S1", "S2", "S3")
EXIT_LABELS = {"st_flip": "SuperTrend flip", "force_exit": "Square-off"}
NAMES = {**EXIT_LABELS, "pivot_break_long": "Pivot break · sold a PE", "pivot_break_short": "Pivot break · sold a CE",
         "pivot_break": "Pivot break", "max_trades": "Daily cap", "stale_candle": "Late candle", "entry": "Entry"}


def _hhmm(s: Optional[str], default: str) -> time:
    try:
        h, m = (s or default).split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        h, m = default.split(":")
        return time(int(h), int(m))


def _cfg(ctx: Ctx, key: str):
    cfg = ctx.config
    return cfg[key] if key in cfg else common.defaults(ctx.dep["strategy_name"]).get(key)


# ── live readings ───────────────────────────────────────────────────────

async def readings(ctx: Ctx) -> dict:
    """SuperTrend, pivots, today's count and the open leg: off the running
    strategy, or the last state it saved."""
    s = ctx.strategy
    src, st, pivots, today, trades_today, last_close = "unavailable", None, None, None, None, None
    open_sym = None
    if s is not None and getattr(s, "st", None) is not None:
        src = "live"
        st = {"trend": s.st.trend, "upper": s.st.final_upper, "lower": s.st.final_lower, "atr": s.st.atr,
              "multiplier": s.st.multiplier}
        pivots = dict(s.pivots) if getattr(s, "pivots", None) else None
        today = s.today.isoformat() if getattr(s, "today", None) else None
        trades_today = getattr(s, "trades_today", None)
        last_close = getattr(s, "today_last_close", None)
        open_sym = getattr(s, "active_leg_symbol", None)
    else:
        state = await queries.load_deployment_state(ctx.pool, ctx.dep["id"])
        snap = (state or {}).get("supertrend") if isinstance(state, dict) else None
        if isinstance(snap, dict):
            src = "persisted"
            st = {"trend": snap.get("trend"), "upper": snap.get("final_upper"), "lower": snap.get("final_lower"),
                  "atr": snap.get("atr"), "multiplier": snap.get("multiplier")}
            pivots = state.get("pivots")
            today = state.get("today")
            trades_today = state.get("trades_today")
            last_close = state.get("today_last_close")
    disp = getattr(ctx.runner, "dispatcher", None)
    tok = (ctx.config.get("instrument_tokens") or [None])[0]
    spot = (getattr(disp, "last_prices", {}) or {}).get(tok) if disp is not None and tok is not None else None
    now = ctx.now or datetime.now(IST)
    if today and today != now.date().isoformat():
        trades_today = 0     # a new day since it last saved
    raw_max = _cfg(ctx, "max_trades_per_day")
    return {"source": src, "st": st, "pivots": pivots, "trades_today": trades_today,
            "max_trades": int(raw_max) if raw_max else None,
            "close": spot if spot is not None else last_close, "close_is_live": spot is not None,
            "open_symbol": open_sym,
            "force_exit": _hhmm(_cfg(ctx, "force_exit_time"), "15:00"),
            "market_open": _hhmm(_cfg(ctx, "market_open_time"), "09:15")}


def line_of(st: Optional[dict]) -> Optional[float]:
    """The SuperTrend line: the lower band while trending up (a close
    below it flips down), the upper band while trending down."""
    if not st or st.get("trend") not in ("up", "down"):
        return None
    v = st["lower"] if st["trend"] == "up" else st["upper"]
    return float(v) if v is not None else None


def trigger_level(trend: Optional[str], pivots: Optional[dict]) -> Optional[tuple[str, float]]:
    """The level a close has to break for an entry: R1 while the trend is
    up (any close above R1 breaks it first), S1 while it's down."""
    if not pivots or trend not in ("up", "down"):
        return None
    k = "R1" if trend == "up" else "S1"
    return (k, float(pivots[k])) if pivots.get(k) is not None else None


def bracket(close: Optional[float], pivots: Optional[dict]) -> Optional[str]:
    """Where the close sits on the ladder: 'R1–R2', 'above R3', ..."""
    if close is None or not pivots:
        return None
    lv = [(k, float(pivots[k])) for k in LADDER if pivots.get(k) is not None]
    if not lv:
        return None
    if close < lv[0][1]:
        return f"below {lv[0][0]}"
    for (a, va), (b, vb) in zip(lv, lv[1:]):
        if va <= close < vb:
            return f"{a}–{b}"
    return f"above {lv[-1][0]}"


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _pts(x: float) -> str:
    return f"{abs(x):,.0f} pts"


def _mins(m: float) -> str:
    m = max(0, int(round(m)))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


def rules(r: dict, now: datetime, holding: bool) -> list[dict]:
    """Every rule in the order the strategy checks it on a candle close."""
    st, piv, close = r["st"], r["pivots"], r["close"]
    line = line_of(st)
    trend = st.get("trend") if st else None
    out = []

    # 1 SuperTrend flip: closes the open leg
    width = (st.get("atr") or 0) * (st.get("multiplier") or 3) if st else 0
    if line is not None and close is not None:
        gap = (close - line) if trend == "up" else (line - close)
        fill = _clamp(1 - gap / width) if width else 0.0
        reading = f"close {close:,.2f} · line {line:,.2f} · {_pts(gap)} {'above' if trend == 'up' else 'below'}" if gap >= 0 \
            else f"close {close:,.2f} is through the line {line:,.2f}: flips on this candle's close"
    else:
        fill, reading = 0.0, "SuperTrend not ready yet"
    out.append({"key": "st_flip", "label": "SuperTrend flip",
                "desc": "A 5-minute close through the SuperTrend line buys the open leg back",
                "fill": fill, "reading": reading, "left": "far", "right": "flips",
                "state": "idle" if not holding else "armed" if fill >= 0.85 else "ok",
                "chip": "Nothing open" if not holding else "Close to a flip" if fill >= 0.85 else "Holding"})

    # 2 square-off
    t, fe, mo = now.time(), r["force_exit"], r["market_open"]
    day = lambda x: datetime.combine(now.date(), x, IST)  # noqa: E731
    span = (day(fe) - day(mo)).total_seconds() or 1
    left_min = (day(fe) - now).total_seconds() / 60
    fill = _clamp((now - day(mo)).total_seconds() / span)
    out.append({"key": "force_exit", "label": f"Square-off · {fe:%H:%M}",
                "desc": f"Whatever is open is bought back at {fe:%H:%M}; no new entries after it",
                "fill": fill, "reading": f"in {_mins(left_min)}" if t < fe else f"{fe:%H:%M} passed",
                "left": f"{mo:%H:%M}", "right": f"{fe:%H:%M}",
                "state": "done" if t >= fe else ("armed" if holding and left_min <= 15 else "ok" if holding else "idle"),
                "chip": "Done today" if t >= fe else "Soon" if holding and left_min <= 15 else "Waiting"})

    # 3 pivot-break entry
    lv = trigger_level(trend, piv)
    cap_hit = r["max_trades"] is not None and (r["trades_today"] or 0) >= r["max_trades"]
    if lv and close is not None:
        k, v = lv
        gap = (v - close) if trend == "up" else (close - v)       # > 0: still to go
        p = float(piv["P"]) if piv.get("P") is not None else None
        scale = abs(v - p) if p is not None and v != p else abs(v) * 0.005
        fill = _clamp(1 - gap / scale) if gap > 0 else 1.0
        sell = "PE" if trend == "up" else "CE"
        reading = (f"{k} {v:,.2f} · {_pts(gap)} to go · sells a {sell}" if gap > 0
                   else f"close is past {k} {v:,.2f}: sells a {sell} on the next candle close")
    else:
        fill, reading = 0.0, "No pivot levels yet" if not piv else "SuperTrend not ready yet"
    state = ("idle" if holding else "done" if t >= fe else "off" if cap_hit
             else "armed" if lv and fill >= 1 else "ok" if lv else "idle")
    out.append({"key": "pivot_break", "label": "Pivot break entry",
                "desc": "Flat, a 5-minute close beyond R1 with the trend up sells an ATM PE; below S1 with it down, an ATM CE",
                "fill": fill, "reading": reading, "left": "P", "right": lv[0] if lv else "level", "state": state,
                "chip": {"idle": "Holding" if holding else "Not ready", "done": "Done today", "off": "Cap reached",
                         "armed": "Enters next close", "ok": "Watching"}[state]})

    # 4 daily cap
    n, mx = r["trades_today"] or 0, r["max_trades"]
    out.append({"key": "max_trades", "label": "Entries today",
                "desc": "No new entry once today's cap is used; exits are never blocked",
                "fill": _clamp(n / mx) if mx else 0.0,
                "reading": f"{n} of {mx} used" if mx else f"{n} today · no cap",
                "left": "0", "right": str(mx) if mx else "∞",
                "state": "off" if not mx else "done" if n >= mx else "ok",
                "chip": "No cap" if not mx else "Cap reached" if n >= mx else f"{mx - n} left"})
    return out


def next_move(r: dict, now: datetime, holding: Optional[dict], running: bool, base: dict) -> dict:
    if not running:
        return base
    st, piv, close = r["st"], r["pivots"], r["close"]
    line, trend = line_of(st), (st or {}).get("trend")
    fe = r["force_exit"]
    if holding:
        txt = f"Holding {holding['symbol']}: bought back on a SuperTrend flip"
        if line is not None and close is not None:
            txt += f" (line {line:,.0f}, {_pts(close - line)} away)"
        return {"text": f"{txt} or at {fe:%H:%M}", "at": None}
    if now.time() >= fe:
        return {"text": f"Done for today: no new entries after {fe:%H:%M}", "at": None}
    if r["max_trades"] is not None and (r["trades_today"] or 0) >= r["max_trades"]:
        return {"text": f"Done for today: all {r['max_trades']} entries used", "at": None}
    lv = trigger_level(trend, piv)
    if not lv:
        return {"text": "Waiting for pivot levels and a SuperTrend reading before any entry", "at": None}
    k, v = lv
    sell = "PE" if trend == "up" else "CE"
    if close is None:
        return {"text": f"Watching for a 5-minute close {'above' if trend == 'up' else 'below'} {k} {v:,.0f} to sell an ATM {sell}", "at": None}
    gap = (v - close) if trend == "up" else (close - v)
    if gap <= 0:
        return {"text": f"Close is past {k} {v:,.0f}: sells an ATM {sell} on the next 5-minute close", "at": None}
    return {"text": f"Sells an ATM {sell} on a 5-minute close {'above' if trend == 'up' else 'below'} {k} {v:,.0f}, {_pts(gap)} away", "at": None}


async def engine(ctx: Ctx) -> dict:
    now = ctx.now or datetime.now(IST)
    base = await common.engine(ctx)
    r = await readings(ctx)
    holding = next((l for l in base["legs"] if l["side"] == "short"), None)
    st, piv, close = r["st"], r["pivots"], r["close"]
    line = line_of(st)
    base["rules"] = rules(r, now, holding is not None) if base["running"] or r["source"] != "unavailable" else []
    base["next"] = next_move(r, now, holding, base["running"], base["next"]) | (
        {"detail": base["next"].get("detail")} if base["next"].get("detail") else {})
    base["supertrend"] = {
        "source": r["source"], "trend": (st or {}).get("trend"), "line": line,
        "upper": _f((st or {}).get("upper")), "lower": _f((st or {}).get("lower")), "atr": _f((st or {}).get("atr")),
        "close": close, "close_is_live": r["close_is_live"],
        "gap": None if line is None or close is None else round(close - line, 2),
        "ladder": [{"key": k, "value": round(float(piv[k]), 2)} for k in LADDER if piv and piv.get(k) is not None],
        "bracket": bracket(close, piv), "trigger": (lambda lv: {"key": lv[0], "value": round(lv[1], 2)} if lv else None)(
            trigger_level((st or {}).get("trend"), piv)),
        "trades_today": r["trades_today"], "max_trades": r["max_trades"],
        "force_exit": r["force_exit"].strftime("%H:%M"), "open_leg": holding,
    }
    return base


# ── trades: one row per option sold ─────────────────────────────────────

async def trade_rows(ctx: Ctx) -> list[dict]:
    """Every leg the strategy sold, oldest first, with what its entry
    saw and how it ended."""
    if "trades" in ctx._cache:
        return ctx._cache["trades"]
    b = await common.book(ctx)
    lots_by: dict = {}
    for l in b["lots"]:
        lots_by.setdefault(l["position_id"], []).append(l)
    rows = []
    for p in sorted(b["positions"], key=lambda x: x["opened_at"]):
        lots = sorted(lots_by.get(p["id"], []), key=lambda x: x["executed_at"])
        ent = next((l for l in lots if l["opening"]), None)
        ext = next((l for l in reversed(lots) if not l["opening"]), None)
        em = (ent or {}).get("metadata") or {}
        tv = em.get("trigger_values") or {}
        xm = (ext or {}).get("metadata") or {}
        xtv = xm.get("trigger_values") or {}
        side = em.get("signal_side") or ("long" if p["symbol"].endswith("PE") else "short")
        close, lvl, stv = _f(tv.get("close")), _f(tv.get("broken_level")), _f(tv.get("supertrend_value"))
        sign = 1 if side == "long" else -1
        entry_px = float(ent["price"]) if ent else float(p["avg_entry_price"])
        exit_px = float(ext["price"]) if ext and p["status"] == "closed" else None
        t0 = _ist(p["opened_at"])
        t1 = _ist(p["closed_at"]) if p["status"] == "closed" and p.get("closed_at") else None
        rows.append({
            "id": str(p["id"]), "symbol": p["symbol"], "opt": common._opt(p["symbol"]), "side": side,
            "strike": _f(em.get("strike")), "expiry": em.get("expiry"), "qty": float(ent["qty"]) if ent else None,
            "entry_t": _iso(t0), "entry_px": entry_px, "trigger": (ent or {}).get("trigger"),
            "level": tv.get("broken_level_key"), "level_px": lvl, "close": close, "st_value": stv,
            "pivot_point": _f(tv.get("pivot_point")),
            "past_level": round(sign * (close - lvl), 2) if close is not None and lvl is not None else None,
            "from_st": round(sign * (close - stv), 2) if close is not None and stv is not None else None,
            "pivots": em.get("pivots"), "switched_to_next_week": bool(tv.get("switched_to_next_week")),
            "exit_t": _iso(t1), "exit_px": exit_px, "exit_reason": (ext or {}).get("trigger") if t1 else None,
            "exit_values": {k: v for k, v in xtv.items() if k != "price_source"} if t1 else None,
            "price_source": xtv.get("price_source") or xm.get("price_source") if t1 else None,
            "pnl": round(float(p["realized_pnl"]), 2) if t1 else None,
            "hold_min": round((t1 - t0).total_seconds() / 60, 1) if t1 else None,
            "kept_pct": round((entry_px - exit_px) / entry_px * 100, 1) if t1 and exit_px is not None and entry_px else None,
            "open": p["status"] != "closed", "token": p["instrument_token"],
        })
    ctx._cache["trades"] = rows
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
        g["avg"] = round(g["pnl"] / g["n"], 2) if g["n"] else None
        g["win_rate"] = round(g["wins"] / g["n"] * 100, 1) if g["n"] else None
    return out


HOLD_BUCKETS = [(0, 15, "< 15m"), (15, 30, "15–30m"), (30, 60, "30m–1h"), (60, 120, "1–2h"), (120, None, "2h +")]


async def signals(ctx: Ctx) -> dict:
    rows = await trade_rows(ctx)
    closed = [r for r in rows if not r["open"]]
    decs = [dict(d) for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], limit=5000)]
    by_level = _group(closed, lambda r: r["level"])
    by_side = _group(closed, lambda r: r["opt"])
    by_hour = _group(closed, lambda r: datetime.fromisoformat(r["entry_t"]).hour)
    by_exit = _group(closed, lambda r: r["exit_reason"])
    for k, g in by_exit.items():
        hs = [r["hold_min"] for r in closed if r["exit_reason"] == k and r["hold_min"] is not None]
        ks = [r["kept_pct"] for r in closed if r["exit_reason"] == k and r["kept_pct"] is not None]
        g["median_hold"] = round(median(hs), 1) if hs else None
        g["median_kept"] = round(median(ks), 1) if ks else None
        g["label"] = label(k, NAMES)
    hold = []
    for lo, hi, lab in HOLD_BUCKETS:
        sel = [r for r in closed if r["hold_min"] is not None and r["hold_min"] >= lo and (hi is None or r["hold_min"] < hi)]
        hold.append({"label": lab, "n": len(sel), "pnl": round(sum(r["pnl"] for r in sel), 2),
                     "wins": sum(1 for r in sel if r["pnl"] > 0)})
    days: dict = {}
    for r in rows:
        d = days.setdefault(r["entry_t"][:10], {"day": r["entry_t"][:10], "trades": 0, "pnl": 0.0, "wins": 0, "cap_hits": 0,
                                                 "flips_flat": 0, "blocked": 0, "open": 0})
        d["trades"] += 1
        d["open"] += 1 if r["open"] else 0
        d["pnl"] = round(d["pnl"] + (r["pnl"] or 0), 2)
        d["wins"] += 1 if (r["pnl"] or 0) > 0 else 0
    for x in decs:
        k = _ist(x["decided_at"]).date().isoformat()
        d = days.setdefault(k, {"day": k, "trades": 0, "pnl": 0.0, "wins": 0, "cap_hits": 0, "flips_flat": 0,
                                "blocked": 0, "open": 0})
        if x["rule"] == "max_trades":
            d["cap_hits"] += 1
        elif x["rule"] == "st_flip" and x["outcome"] == "no_action":
            d["flips_flat"] += 1
        elif x["outcome"] == "blocked":
            d["blocked"] += 1
    pnls = [r["pnl"] for r in closed]
    num = lambda xs: [x for x in xs if x is not None]  # noqa: E731
    past, from_st = num(r["past_level"] for r in closed), num(r["from_st"] for r in closed)
    summary = {
        "trades": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
        "pnl": round(sum(pnls), 2), "wins": sum(1 for p in pnls if p > 0),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else None,
        "avg": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "best": max(pnls) if pnls else None, "worst": min(pnls) if pnls else None,
        "median_past_level": round(median(past), 1) if past else None,
        "median_from_st": round(median(from_st), 1) if from_st else None,
        "median_hold": round(median(num(r["hold_min"] for r in closed)), 1) if closed else None,
        "cap_days": sum(1 for d in days.values() if d["cap_hits"]),
        "days": len(days), "days_traded": sum(1 for d in days.values() if d["trades"]),
    }
    return {"trades": list(reversed(rows)), "summary": summary, "by_level": by_level, "by_side": by_side,
            "by_hour": [{"hour": h, **g} for h, g in sorted(by_hour.items())],
            "by_exit": by_exit, "hold": hold,
            "days": sorted(days.values(), key=lambda d: d["day"], reverse=True)}


async def trade(ctx: Ctx) -> dict:
    """One trade end to end: ?p=<position id>, default the newest."""
    rows = await trade_rows(ctx)
    if not rows:
        return {"trade": None, "n": 0}
    want = ctx.params.get("p")
    i = next((j for j, r in enumerate(rows) if r["id"] == want), None) if want else len(rows) - 1
    if i is None:
        return {"trade": None, "n": len(rows), "missing": want}
    r = rows[i]
    t0 = datetime.fromisoformat(r["entry_t"])
    t1 = datetime.fromisoformat(r["exit_t"]) if r["exit_t"] else (ctx.now or datetime.now(IST))
    start, end = t0 - timedelta(minutes=45), min(t1 + timedelta(minutes=30), (ctx.now or datetime.now(IST)) + timedelta(minutes=1))
    span = (end - start).total_seconds()
    every = 60 if span <= 4 * 3600 else 300 if span <= 3 * 86400 else 900
    tok = (ctx.config.get("instrument_tokens") or [None])[0]
    toks = [t for t in (tok, r["token"]) if t is not None]
    path = {"index": [], "option": []}
    for c in await queries.list_candle_closes(ctx.pool, toks, start, end, every_s=every):
        k = "index" if c["instrument_token"] == tok else "option"
        path[k].append([_iso(c["minute"]), round(float(c["c"]), 2)])
    decs = [{"t": _iso(d["decided_at"]), "rule": d["rule"], "label": label(d["rule"], NAMES), "outcome": d["outcome"],
             "summary": d["summary"], "why": saw(d["data"])}
            for d in await queries.list_decisions(ctx.pool, ctx.dep["id"], since=start, limit=500)
            if _ist(d["decided_at"]) <= end]
    return {"trade": r, "no": i + 1, "n": len(rows),
            "prev": rows[i - 1]["id"] if i > 0 else None, "next": rows[i + 1]["id"] if i + 1 < len(rows) else None,
            "path": path, "decisions": sorted(decs, key=lambda d: d["t"])}


async def decisions(ctx: Ctx) -> dict:
    return await common.decisions(ctx, names=NAMES)


VIEWS = {**common.VIEWS, "engine": engine, "decisions": decisions, "signals": signals, "trade": trade}
