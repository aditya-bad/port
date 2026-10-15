"""
live_deploy — Cycles: one chart per trading cycle, replaying how the
underlying moved against the cycle's P&L.

A CYCLE is one positional EPISODE (every overlapping leg, roll and
adjustment — the same grouping as Stats "per position") or, for an
intraday deployment, one IST trading day. For a cycle, the chart payload
assembles:

  - underlying candles: Kite historical data for the deployment's
    underlying (config.instrument_tokens[0] — every strategy here keeps
    its underlying first);
  - each position's option price path: Kite historical data while the
    contract is still listed; Kite has none for expired options, so the
    fallback is the app's own per-leg price marks (position_marks: every
    minute for intraday, every 5 minutes for positional, recorded from
    this version onward); with neither, that leg is "unknown" between
    its fills;
  - the cycle's gross P&L at every point (closed legs at their realized
    P&L, open legs marked to that point's option price), plus, for
    positional deployments, the snapshot-based cycle P&L as a fallback
    line whenever the per-position total is incomplete;
  - every fill, grouped into entry / adjustment / exit events;
  - each leg's strike over the time it was held (drawn on the
    underlying's axis, to show how close spot came);
  - summary stats.

Charges/slippage are NOT applied to the curve (an open leg's charges
aren't known yet); the cycle's final net figures come alongside it.
"""

import logging
import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

from ..db import queries
from ..options import NoKiteSession, OptionsResolver

logger = logging.getLogger("live_deploy.cycles")

router = APIRouter(prefix="/deployments", tags=["cycles"])

_IST = ZoneInfo("Asia/Kolkata")
_SESSION_OPEN, _SESSION_CLOSE = time(9, 15), time(15, 30)
# Fills this close together are one event (a roll's close+open, both legs
# of an entry).
_EVENT_GAP = timedelta(seconds=90)
_INTERVAL_SECONDS = {"minute": 60, "5minute": 300, "15minute": 900, "60minute": 3600}
_MONTHLY_STRIKE_RE = re.compile(r"[A-Z]{3}(\d+(?:\.\d+)?)(CE|PE)$")

# (token, start, end, interval) -> (fetched_at, candles). Windows that
# ended well in the past never change, so they're kept; live windows are
# refetched after a minute.
_HIST_CACHE: dict[tuple, tuple[datetime, list]] = {}
_HIST_CACHE_MAX = 500
_LIVE_TTL = timedelta(seconds=60)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _num(v) -> float:
    return float(v) if v is not None else 0.0


def _option_type(symbol: str) -> Optional[str]:
    return symbol[-2:] if symbol[-2:] in ("CE", "PE") else None


def _pick_interval(span: timedelta) -> str:
    if span <= timedelta(days=2):
        return "minute"
    if span <= timedelta(days=8):
        return "5minute"
    if span <= timedelta(days=45):
        return "15minute"
    return "60minute"


async def _historical(resolver: OptionsResolver, token: int, start: datetime, end: datetime, interval: str) -> list[dict]:
    """Candles as [{t, o, h, l, c}] (t = candle start, epoch ms). Raises
    NoKiteSession / API errors to the caller."""
    key = (token, start.isoformat(), end.isoformat(), interval)
    now = datetime.now(timezone.utc)
    hit = _HIST_CACHE.get(key)
    if hit is not None and (end < now - timedelta(minutes=10) or now - hit[0] < _LIVE_TTL):
        return hit[1]
    raw = await resolver.get_historical_candles(token, start.astimezone(_IST), end.astimezone(_IST), interval)
    candles = []
    for c in raw:
        ts = c["date"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_IST)
        candles.append({"t": _ms(ts), "o": c["open"], "h": c["high"], "l": c["low"], "c": c["close"]})
    if len(_HIST_CACHE) >= _HIST_CACHE_MAX:
        _HIST_CACHE.pop(next(iter(_HIST_CACHE)))
    _HIST_CACHE[key] = (now, candles)
    return candles


async def _load_cycles(pool, dep) -> list[dict]:
    """Every cycle, newest first, each with its own position rows."""
    rows = await queries.list_positions_with_episode(pool, dep["id"], status=None)
    groups: dict[int, list] = {}
    for r in rows:
        if dep["mode"] == "positional":
            anchor = r["episode_opened_at"]
        else:
            anchor = datetime.combine(r["opened_at"].astimezone(_IST).date(), time(0), _IST)
        groups.setdefault(_ms(anchor), []).append(r)

    cycles = []
    for key, prs in groups.items():
        prs.sort(key=lambda p: p["opened_at"])
        is_open = any(p["status"] == "open" for p in prs)
        closed = [p for p in prs if p["status"] == "closed"]
        cycles.append({
            "key": str(key),
            "start": min(p["opened_at"] for p in prs),
            "end": None if is_open else max(p["closed_at"] for p in prs),
            "status": "open" if is_open else "closed",
            "positions": prs,
            "realized_pnl": sum(_num(p["realized_pnl"]) for p in closed),
            "realized_pnl_net": sum(_num(p["realized_pnl"]) - _num(p["estimated_charges"]) for p in closed),
            "realized_pnl_net_slip": sum(
                _num(p["realized_pnl"]) - _num(p["estimated_charges"]) - _num(p["estimated_slippage"]) for p in closed
            ),
        })
    cycles.sort(key=lambda c: c["start"], reverse=True)
    return cycles


def _group_events(lots: list, cycle: dict) -> list[dict]:
    """Fills -> entry / adjustment / exit events."""
    if not lots:
        return []
    first_t, last_t = lots[0]["executed_at"], lots[-1]["executed_at"]
    events: list[dict] = []
    for lot in lots:
        opening = (lot["side"] == "long") == (lot["action"] == "buy")
        meta = lot["metadata"] or {}
        fill = {
            "symbol": lot["symbol"], "action": lot["action"], "opening": opening,
            "qty": _num(lot["qty"]), "price": _num(lot["price"]),
            "reason": lot["reason"], "strike": meta.get("strike"),
            "leg_role": meta.get("leg_role"), "position_id": str(lot["position_id"]),
        }
        if events and lot["executed_at"] - events[-1]["_last"] <= _EVENT_GAP:
            events[-1]["fills"].append(fill)
            events[-1]["_last"] = lot["executed_at"]
        else:
            events.append({"t": _ms(lot["executed_at"]), "_first": lot["executed_at"],
                           "_last": lot["executed_at"], "fills": [fill]})
    for ev in events:
        if ev["_first"] - first_t <= _EVENT_GAP:
            ev["kind"] = "entry"
        elif cycle["status"] == "closed" and last_t - ev["_last"] <= _EVENT_GAP and not any(f["opening"] for f in ev["fills"]):
            ev["kind"] = "exit"
        else:
            ev["kind"] = "adjustment"
        reasons = sorted({f["reason"] for f in ev["fills"] if f["reason"]})
        ev["reason"] = ", ".join(reasons)
        del ev["_first"], ev["_last"]
    return events


def _strike_of(position: dict, lots: list) -> Optional[float]:
    for lot in lots:
        if lot["position_id"] == position["id"]:
            strike = (lot["metadata"] or {}).get("strike")
            if strike is not None:
                return float(strike)
    m = _MONTHLY_STRIKE_RE.search(position["symbol"])
    return float(m.group(1)) if m else None


def _price_at(series: list[tuple[int, float]], t: int) -> Optional[float]:
    """Last price at or before t (series sorted by time), None if none yet."""
    lo, hi, found = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= t:
            found = series[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return found


@router.get("/{deployment_id}/cycles")
async def list_cycles(deployment_id: UUID, request: Request):
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    cycles = await _load_cycles(pool, dep)
    all_ids = [p["id"] for c in cycles for p in c["positions"]]
    lots = await queries.list_lots_for_positions(pool, all_ids)
    lots_by_pos: dict = {}
    for lot in lots:
        lots_by_pos.setdefault(lot["position_id"], []).append(lot)
    out = []
    for c in cycles:
        cycle_lots = sorted(
            (lot for p in c["positions"] for lot in lots_by_pos.get(p["id"], [])),
            key=lambda lot: lot["executed_at"],
        )
        events = _group_events(cycle_lots, c)
        out.append({
            "key": c["key"], "start": c["start"], "end": c["end"], "status": c["status"],
            "positions": len(c["positions"]), "fills": len(cycle_lots),
            "adjustments": sum(1 for e in events if e["kind"] == "adjustment"),
            "realized_pnl": round(c["realized_pnl"], 2),
            "realized_pnl_net": round(c["realized_pnl_net"], 2),
            "realized_pnl_net_slip": round(c["realized_pnl_net_slip"], 2),
        })
    return out


@router.get("/{deployment_id}/cycles/{key}/chart")
async def cycle_chart(deployment_id: UUID, key: str, request: Request):
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    cycles = await _load_cycles(pool, dep)
    cycle = next((c for c in cycles if c["key"] == key), None)
    if cycle is None:
        raise HTTPException(404, "No such cycle")

    now = datetime.now(timezone.utc)
    start, end = cycle["start"], cycle["end"] or now
    if dep["mode"] == "positional":
        win_start, win_end = start - timedelta(minutes=30), min(now, end + timedelta(minutes=30))
    else:
        day = start.astimezone(_IST).date()
        win_start = datetime.combine(day, _SESSION_OPEN, _IST)
        win_end = min(now, max(datetime.combine(day, _SESSION_CLOSE, _IST), end + timedelta(minutes=5)))
        win_start = min(win_start, start - timedelta(minutes=5))
    interval = _pick_interval(win_end - win_start)
    step_ms = _INTERVAL_SECONDS[interval] * 1000

    positions = cycle["positions"]
    lots = await queries.list_lots_for_positions(pool, [p["id"] for p in positions])
    events = _group_events(lots, cycle)
    snapshots = await queries.list_snapshots_between(pool, deployment_id, win_start, win_end)

    resolver = OptionsResolver(request.app.state.dispatcher)
    notes: list[str] = []

    # Underlying
    underlying: list[dict] = []
    underlying_error = None
    kite_logged_in = True
    tokens = (dep["config"] or {}).get("instrument_tokens") or []
    if not tokens:
        underlying_error = "No underlying instrument configured for this deployment."
    else:
        try:
            underlying = await _historical(resolver, int(tokens[0]), win_start, win_end, interval)
            if not underlying:
                underlying_error = "Kite returned no underlying history for this window."
        except NoKiteSession:
            kite_logged_in = False
            underlying_error = "Kite isn't logged in, so the underlying's history can't be fetched. Log in and reload."
        except Exception as e:
            logger.exception("Cycles: underlying history failed for %s", dep["deployment_name"])
            underlying_error = f"Couldn't fetch the underlying's history ({e})."

    # Per-position option price paths
    marks_by_pos: dict[str, list[tuple[int, float]]] = {}
    for m in await queries.list_marks_for_positions(pool, [p["id"] for p in positions], win_start, win_end):
        marks_by_pos.setdefault(str(m["position_id"]), []).append((_ms(m["marked_at"]), m["price"]))

    kite_by_token: dict[int, list[tuple[int, float]]] = {}
    for token in sorted({int(p["instrument_token"]) for p in positions}):
        if not kite_logged_in:
            break
        legs = [p for p in positions if int(p["instrument_token"]) == token]
        t0 = max(win_start, min(p["opened_at"] for p in legs) - timedelta(minutes=1))
        t1 = min(win_end, max((p["closed_at"] or now) for p in legs) + timedelta(minutes=1))
        try:
            candles = await _historical(resolver, token, t0, t1, interval)
            if candles:
                kite_by_token[token] = [(c["t"], c["c"]) for c in candles]
        except NoKiteSession:
            kite_logged_in = False
        except Exception:
            logger.info("Cycles: no Kite history for option token %s (likely expired)", token)

    pos_out, series_of = [], []
    for p in positions:
        pid = str(p["id"])
        token = int(p["instrument_token"])
        if token in kite_by_token:
            source, series = "kite", kite_by_token[token]
        elif pid in marks_by_pos:
            source, series = "marks", marks_by_pos[pid]
        else:
            source, series = "none", []
        qty = _num(p.get("total_qty") or p["qty"])
        pos_out.append({
            "id": pid, "symbol": p["symbol"], "side": p["side"], "option_type": _option_type(p["symbol"]),
            "qty": qty, "entry_price": _num(p["avg_entry_price"]),
            "exit_price": _num(p["exit_price"]) if p.get("exit_price") is not None else None,
            "opened_at": _ms(p["opened_at"]),
            "closed_at": _ms(p["closed_at"]) if p["closed_at"] else None,
            "status": p["status"], "realized_pnl": _num(p["realized_pnl"]),
            "strike": _strike_of(p, lots), "price_source": source,
        })
        series_of.append(series)

    # Timeline: underlying candles (each evaluated at its close), else
    # every snapshot/fill time in the window.
    if underlying:
        points = [(c["t"], c["t"] + step_ms) for c in underlying]
    else:
        times = sorted({_ms(s["snapshot_at"]) for s in snapshots} | {e["t"] for e in events}
                       | {_ms(win_start), _ms(win_end)})
        points = [(t, t) for t in times]

    per_pos = [[] for _ in pos_out]
    totals: list[Optional[float]] = []
    for _, eval_t in points:
        total, complete, any_in = 0.0, True, False
        for i, (po, series) in enumerate(zip(pos_out, series_of)):
            if po["opened_at"] > eval_t:
                per_pos[i].append(None)
                continue
            any_in = True
            if po["closed_at"] is not None and po["closed_at"] <= eval_t:
                v = po["realized_pnl"]
            else:
                price = _price_at(series, eval_t) if series else None
                if price is None and po["price_source"] != "none":
                    price = po["entry_price"]   # before the first candle/mark: just entered
                if price is None:
                    per_pos[i].append(None)
                    complete = False
                    continue
                diff = price - po["entry_price"]
                v = diff * po["qty"] if po["side"] == "long" else -diff * po["qty"]
            per_pos[i].append(round(v, 2))
            total += v
        totals.append(round(total, 2) if (complete and any_in) else (0.0 if not any_in else None))

    # Positional fallback: snapshot total_value minus everything realized
    # before this cycle began (episodes never overlap).
    snapshot_pnl = []
    if dep["mode"] == "positional" and snapshots:
        realized_before = sum(
            _num(r["realized_pnl"]) for c in cycles if c is not cycle for r in c["positions"]
            if r["closed_at"] is not None and r["closed_at"] <= cycle["start"]
        )
        base = _num(dep["initial_capital"]) + realized_before
        snapshot_pnl = [
            {"t": _ms(s["snapshot_at"]), "pnl": round(s["total_value"] - base, 2)}
            for s in snapshots if cycle["start"] <= s["snapshot_at"] <= end
        ]

    # Notes on data coverage
    unknown = [po["symbol"] for po in pos_out if po["price_source"] == "none"]
    if unknown:
        notes.append(
            f"No price history for {len(unknown)} of {len(pos_out)} legs (expired before this "
            f"version started recording its own) — their P&L is only known at their fills"
            + ("; the cycle's P&L line uses the 5-minute account snapshots instead." if snapshot_pnl else ".")
        )
    if any(po["price_source"] == "marks" for po in pos_out):
        notes.append("Some legs use the app's own recorded price marks (1-min intraday, 5-min positional) — the contract has expired on Kite.")

    # Stats
    in_cycle = [c for c in underlying if _ms(start) - step_ms < c["t"] <= _ms(end)]
    stats: dict = {
        "duration_minutes": round((end - start).total_seconds() / 60),
        "positions": len(pos_out), "fills": len(lots),
        "adjustments": sum(1 for e in events if e["kind"] == "adjustment"),
        "realized_pnl": round(cycle["realized_pnl"], 2),
        "realized_pnl_net": round(cycle["realized_pnl_net"], 2),
        "realized_pnl_net_slip": round(cycle["realized_pnl_net_slip"], 2),
    }
    if in_cycle:
        u_open, u_close = in_cycle[0]["o"], in_cycle[-1]["c"]
        hi = max(in_cycle, key=lambda c: c["h"])
        lo = min(in_cycle, key=lambda c: c["l"])
        stats["underlying"] = {
            "open": u_open, "close": u_close, "change": round(u_close - u_open, 2),
            "change_pct": round((u_close - u_open) / u_open * 100, 2) if u_open else None,
            "high": hi["h"], "high_t": hi["t"], "low": lo["l"], "low_t": lo["t"],
        }
    curve = [(points[i][0], v) for i, v in enumerate(totals) if v is not None]
    if any(v is None for v in totals) and snapshot_pnl:
        curve = [(s["t"], s["pnl"]) for s in snapshot_pnl]
    if curve:
        best = max(curve, key=lambda x: x[1])
        worst = min(curve, key=lambda x: x[1])
        stats["max_pnl"], stats["max_pnl_t"] = best[1], best[0]
        stats["min_pnl"], stats["min_pnl_t"] = worst[1], worst[0]

    return {
        "cycle": {"key": cycle["key"], "start": _ms(start), "end": _ms(cycle["end"]) if cycle["end"] else None,
                  "status": cycle["status"], "mode": dep["mode"]},
        "window": {"start": _ms(win_start), "end": _ms(win_end), "interval": interval},
        "underlying": underlying, "underlying_error": underlying_error,
        "underlying_symbol": (dep["config"] or {}).get("instrument") or (dep["config"] or {}).get("options_underlying"),
        "positions": pos_out,
        # Each point is evaluated at t + eval_offset_ms (the candle's close).
        "series": {"t": [p[0] for p in points], "pnl": totals, "pos": per_pos,
                   "eval_offset_ms": step_ms if underlying else 0},
        "snapshot_pnl": snapshot_pnl,
        "events": events,
        "stats": stats,
        "notes": notes,
    }
