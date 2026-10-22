"""
live_deploy — repairing a day the end-of-day check flagged (app/repair/
daycheck.py), from the minute it went wrong.

  FROM WHERE     the first minute the app and its strategy, replayed,
                 parted ways: the earliest trade one made and the other
                 didn't, or the earliest minute that could be read two
                 ways. Everything the app did before it stays as it was —
                 real fills at real prices.
  THE STATE      the strategy's own state at that minute, from replaying
                 the morning from the state it had at the open (the only
                 state kept); the book is the app's own up to that minute.
  PLAN           from there to the close on Kite's 1-minute candles, with
                 the paths you chose where a minute reads two ways (the
                 replay stops at the next one you haven't), everything
                 rolled back. With what you need to judge it: the index,
                 VIX and every leg minute by minute, each trade with what
                 triggered it, each check that held back, the app's fills
                 it replaces, and the end state both ways.
  APPLY          the same replay again, committed only if it comes out
                 exactly as the plan you reviewed; the app's fills from
                 that minute on are replaced by the strategy's, recorded
                 in stall_repairs with everything needed to undo it
                 (engine.undo puts the app's own fills back).

Only the latest day a deployment traded can be repaired: a repair of an
earlier one would rewrite what came after it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta
from typing import Optional

from ..db import queries
from . import daycheck as D
from . import engine
from .market import INDEX_LABELS, IST, VIX_TOKEN, MinuteBook

OPEN, CLOSE = D.OPEN, D.CLOSE


def divergence(check: dict) -> Optional[str]:
    """HH:MM where the app and the replay parted, from a day_checks row."""
    diff, rep = check.get("diff") or {}, check.get("replay") or {}
    mins = [f["minute"] for f in (diff.get("missed") or []) + (diff.get("extra") or [])]
    mins += [a["minute"] for a in rep.get("auto") or []]
    return min(mins) if mins else None


def _t(hhmm: str) -> time:
    h, m = map(int, hhmm.split(":"))
    return time(h, m)


def fills_key(fills: list) -> str:
    return hashlib.sha1(json.dumps(engine.exact(fills), sort_keys=True, default=str).encode()).hexdigest()[:16]


async def state_at(pool, dep_id, day: date, at: str, book: MinuteBook, kite) -> tuple[Optional[dict], str]:
    """The strategy's state at `at` (HH:MM): the morning replayed from the
    state kept at the open, rolled back."""
    async with pool.acquire() as conn:
        known, state, from_what = await D.start_state(conn, dep_id, day)
        if not known:
            raise engine.PlanChanged("no start-of-day state is kept for this day, so the morning can't be replayed")
        if _t(at) <= OPEN:
            return state, from_what
        tr = conn.transaction()
        await tr.start()
        try:
            await D.rewind(conn, dep_id, D._at(day, OPEN), state)
            fresh = dict(await conn.fetchrow("SELECT * FROM deployments WHERE id = $1", dep_id))
            out = await engine.replay(conn, fresh, engine.Window(day, OPEN, _t(at)), book, kite, {}, auto=True,
                                      run=f"state-{uuid.uuid4().hex[:8]}")
        finally:
            await tr.rollback()
    return out.end_state, f"{from_what}, the morning replayed to {at}"


async def blockers(conn, dep_id, day: date) -> list[str]:
    """Why a repair of `day` can't be applied, if it can't."""
    out = []
    later = await conn.fetchval(
        "SELECT count(*) FROM position_lots WHERE deployment_id = $1 AND executed_at >= $2",
        dep_id, D._at(day + timedelta(days=1), time(0)))
    if later:
        out.append(f"It has traded {later} time(s) since that day: repairing it would rewrite what came after.")
    if await engine.live_repair(conn, dep_id, day):
        out.append("A repair of this day is already applied — undo it first.")
    status = await conn.fetchval("SELECT status FROM deployments WHERE id = $1", dep_id)
    if status == "stopped":
        out.append("It's stopped.")
    return out


def series(book: MinuteBook, day: date, idx_tok: Optional[int], legs: dict) -> dict:
    """Minute by minute, 09:15–15:29: the index's candles, VIX, and each
    leg's close (the last trade carried over a minute with none)."""
    mins = [datetime.combine(day, OPEN) + timedelta(minutes=i) for i in range(375)]
    out = {"t": [f"{m:%H:%M}" for m in mins], "index": None, "vix": [], "legs": {}}
    if idx_tok is not None:
        rows = book.load(idx_tok)
        out["index"] = {"label": INDEX_LABELS.get(idx_tok, str(idx_tok)),
                        "ohlc": [[c["open"], c["high"], c["low"], c["close"]] if (c := rows.get(m)) else None for m in mins]}
    out["vix"] = [book.at(VIX_TOKEN, m, "close") for m in mins]
    for tok, sym in legs.items():
        book.note(tok, sym)
        out["legs"][sym] = [None if (p := book.at(tok, m, "close")) is None else round(p, 2) for m in mins]
    return out


async def _decisions(conn, dep_id, after_id: int) -> list[dict]:
    return [{"at": f"{r['decided_at'].astimezone(IST):%H:%M:%S}", "rule": r["rule"], "outcome": r["outcome"],
             "summary": r["summary"], "data": r["data"] or {}}
            for r in await conn.fetch(
                "SELECT * FROM strategy_decisions WHERE deployment_id = $1 AND id > $2 ORDER BY decided_at, id", dep_id, after_id)]


async def _app_fills(conn, dep_id, day: date) -> list[dict]:
    rows = await conn.fetch(
        "SELECT l.*, p.symbol, p.instrument_token FROM position_lots l JOIN positions p ON p.id = l.position_id "
        "WHERE l.deployment_id = $1 AND l.executed_at >= $2 AND l.executed_at < $3 ORDER BY l.executed_at",
        dep_id, D._at(day, time(0)), D._at(day + timedelta(days=1), time(0)))
    return [{"at": f"{r['executed_at'].astimezone(IST):%H:%M:%S}", "minute": f"{r['executed_at'].astimezone(IST):%H:%M}",
             "action": r["action"], "symbol": r["symbol"], "token": int(r["instrument_token"]), "qty": float(r["qty"]),
             "price": round(float(r["price"]), 2), "reason": r["reason"] or "",
             "repaired": "repair" in (r["metadata"] or {}),
             "meta": {k: v for k, v in (r["metadata"] or {}).items() if k not in ("repair",)}} for r in rows]


async def _touched(conn, dep_id, start_at: datetime) -> dict:
    """Every row a rewind to `start_at` deletes or changes, as it is."""
    pos = await conn.fetch(
        "SELECT row_to_json(p)::text AS j FROM positions p WHERE deployment_id = $1 AND "
        "(status = 'open' OR opened_at >= $2 OR closed_at >= $2)", dep_id, start_at)
    lots = await conn.fetch(
        "SELECT row_to_json(l)::text AS j FROM position_lots l WHERE deployment_id = $1 AND executed_at >= $2", dep_id, start_at)
    return {"positions": [json.loads(r["j"]) for r in pos], "lots": [json.loads(r["j"]) for r in lots]}


async def run(pool, dep_id, day: date, book: MinuteBook, kite, choices: dict, *, commit: bool = False,
              expected: Optional[str] = None, author: Optional[str] = None) -> dict:
    """Plan (commit=False) or apply (commit=True, `expected` = the plan's
    fills_key) the repair of `dep_id`'s `day` with `choices`."""
    check = await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1 AND day = $2", dep_id, day)
    if check is None:
        raise engine.PlanChanged("this day hasn't been checked")
    start = divergence(dict(check))
    if start is None:
        raise engine.PlanChanged("the check found nothing to repair")
    state, from_what = await state_at(pool, dep_id, day, start, book, kite)
    window = engine.Window(day, _t(start), CLOSE)
    dep = dict(await queries.get_deployment(pool, dep_id))
    run_id = uuid.uuid4().hex[:12]
    async with pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT 1 FROM deployments WHERE id = $1 FOR UPDATE", dep_id)
            why_not = await blockers(conn, dep_id, day)
            if commit and why_not:
                raise engine.PlanChanged(why_not[0])
            app = await _app_fills(conn, dep_id, day)
            _, app_end = await D.actual_day(conn, dep_id, day)
            before = await engine._capture(conn, dep_id)
            touched = await _touched(conn, dep_id, window.start_at)
            await D.rewind(conn, dep_id, window.start_at, state)
            fresh = dict(await conn.fetchrow("SELECT * FROM deployments WHERE id = $1", dep_id))
            out = await engine.replay(conn, fresh, window, book, kite, choices, run=run_id)
            decisions = await _decisions(conn, dep_id, before["max_decision"])
            key = fills_key(out.fills)
            if not commit:
                result = None
            else:
                if not out.complete:
                    raise engine.PlanChanged(f"the replay stopped at an undecided minute ({out.ambiguity.minute})")
                if out.blind:
                    raise engine.PlanChanged(f"no prices for {', '.join(out.blind)}")
                if key != expected:
                    raise engine.PlanChanged("the replay came out differently from the plan you reviewed — plan it again")
                added = await engine._added(conn, dep_id, before)
                await engine._keep_candles(conn, book, out.fills)
                s = engine.summarize(out, float(dep["initial_capital"]))
                paths = ", ".join(f"{k} {v.replace('_', ' ')}" for k, v in sorted(choices.items()))
                body = (f"Day check of {day:%d %b}: repaired from {start} by replaying the strategy on 1-minute candles "
                        f"({s['fills']} fill(s) in place of the app's {len([f for f in app if f['minute'] >= start])}). "
                        + (f"Paths chosen: {paths}." if paths else "No minute read two ways."))
                journal = await queries.create_journal_entry(
                    engine.TxPool(conn), day, body, ["repaired"], deployment_id=dep_id, author=author)
                event = await conn.fetchval(
                    "INSERT INTO deployment_events (deployment_id, event_type, message, metadata) "
                    "VALUES ($1, 'stall_repair', $2, $3) RETURNING id",
                    dep_id, body, {"run": run_id, **window.as_dict(), "choices": choices, "fills": len(out.fills), "kind": "day"})
                undo = {"cash": before["cash"], "had_state": before["had_state"], "state": before["state"],
                        "positions": [], "added": added, "removed": touched,
                        "journal": str(journal), "event": str(event)}
                rid = await conn.fetchval(
                    "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, choices, summary, undo) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                    dep_id, day, window.start_at, window.end_at, choices,
                    {**s, "run": run_id, "kind": "day", "fills_detail": D._json(out.fills),
                     "replaced": D._json([f for f in app if f["minute"] >= start])}, undo)
                result = {"id": str(rid), "summary": s}
        except BaseException:
            await tr.rollback()
            raise
        else:
            if commit:
                await tr.commit()
            else:
                await tr.rollback()
    if result is not None:
        return {"applied": result, "fills_key": key}
    legs = {f["token"]: f["symbol"] for f in app}
    legs.update({f["token"]: f["symbol"] for f in out.fills})
    legs.update({p["token"]: p["symbol"] for p in out.start_positions})
    idx = next(iter(sorted(int(t) for t in (dep.get("config") or {}).get("instrument_tokens") or [])), None)
    amb = None
    if out.ambiguity is not None:
        a = out.ambiguity
        amb = {"minute": a.minute, "natural": a.natural, "candles": a.candles,
               "paths": {k: {"fills": D._json(v["fills"]), "pnl": v["pnl"], "open": v["open"]} for k, v in a.paths.items()}}
    return D._json({
        "from": start, "start": from_what, "choices": choices, "complete": out.complete, "ambiguity": amb,
        "fills": out.fills, "fills_key": key, "app_fills": app,
        "replaced": [f for f in app if f["minute"] >= start],
        "decisions": decisions, "auto": out.auto, "notes": out.notes, "problems": out.problems,
        "blind": out.blind, "estimated": out.estimated,
        "end": {"app": app_end, "after": out.end_positions},
        "summary": engine.summarize(out, float(dep["initial_capital"])), "curve": out.curve,
        "can_apply": not why_not and out.complete and not out.blind, "why_not": why_not,
        "series": series(book, day, idx, legs),
    })
