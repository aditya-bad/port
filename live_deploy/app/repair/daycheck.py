"""
live_deploy — the end-of-day check: did each deployment end the day where
its strategy would have, and did it trade as its strategy would have?

Run after the 15:45 export, in its own process (custom_scripts/
day_check.py: the replay points the app's Kite and clock at the past, so
it can't share the running app's process). For each deployment:

  1. THE RULES of a day's end, from the book as it stood at the close:
       - an intraday deployment with a force-exit time holds nothing after it
       - nothing is held past its contract's expiry
     A rule broken is flagged whatever else the check finds.
  2. THE REPLAY: the deployment's own strategy, from the state it had at
     that morning's open (deployment_state_days) and its book rewound to
     the open, through the whole session on Kite's 1-minute candles
     (app/repair/engine.py, everything rolled back). Its trades are matched
     to the app's — same action, contract and quantity within a few
     minutes; prices differ, a 1-minute candle isn't the tick it filled at
     — and so are the end states.
       - the same: ok
       - different, and no minute in the day could be read two ways: mismatch
       - different, and some minute could (the usual reading was used):
         ambiguous — for you to settle on the Repair page
     No start-of-day state (the first day after this shipped, or a day the
     app was down at the open), or a leg with no price at all: the replay
     can't be trusted, so only the rules are checked (cant_check).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

from ..candles import parse_contract
from . import engine
from .market import IST, MinuteBook

OPEN, CLOSE = time(9, 15), time(15, 30)
MATCH_WITHIN = timedelta(minutes=3)


def _at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, IST)


def _hhmm(s: Optional[str]) -> Optional[time]:
    if not s:
        return None
    try:
        h, m = map(int, str(s).split(":")[:2])
        return time(h, m)
    except ValueError:
        return None


# ── The start of a day ──────────────────────────────────────────────────

async def capture_states(pool, day: date, at: str) -> int:
    """Copy every deployment's current strategy state (not stopped ones)
    as `at` ('open' or 'close') of `day`; a day already captured is kept."""
    res = await pool.execute(
        "INSERT INTO deployment_state_days (deployment_id, day, at, state) "
        "SELECT d.id, $1, $2, s.state FROM deployments d LEFT JOIN deployment_state s ON s.deployment_id = d.id "
        "WHERE d.status <> 'stopped' ON CONFLICT DO NOTHING", day, at)
    return int(res.split()[-1])


async def start_state(conn, dep_id, day: date) -> tuple[bool, Optional[dict], str]:
    """(known, state, from what): the state captured at that day's open,
    else the one after the last close before it."""
    row = await conn.fetchrow(
        "SELECT state FROM deployment_state_days WHERE deployment_id = $1 AND day = $2 AND at = 'open'", dep_id, day)
    if row is not None:
        return True, row["state"], f"captured at the open of {day:%d %b}"
    row = await conn.fetchrow(
        "SELECT day, state FROM deployment_state_days WHERE deployment_id = $1 AND day < $2 AND at = 'close' "
        "ORDER BY day DESC LIMIT 1", dep_id, day)
    if row is not None:
        return True, row["state"], f"captured after the close of {row['day']:%d %b}"
    return False, None, ""


async def rewind(conn, dep_id, start_at: datetime, state: Optional[dict]) -> None:
    """Inside a transaction: the deployment as it stood at `start_at` —
    every fill from then on removed, positions and cash rebuilt from the
    fills before it (a close always closes the whole position), and the
    strategy's state set to `state`."""
    await conn.execute("DELETE FROM position_lots WHERE deployment_id = $1 AND executed_at >= $2", dep_id, start_at)
    await conn.execute("DELETE FROM positions WHERE deployment_id = $1 AND opened_at >= $2", dep_id, start_at)
    for p in await conn.fetch("SELECT * FROM positions WHERE deployment_id = $1", dep_id):
        lots = await conn.fetch("SELECT * FROM position_lots WHERE position_id = $1 ORDER BY executed_at", p["id"])
        opening = "sell" if p["side"] == "short" else "buy"
        opens = [l for l in lots if l["action"] == opening]
        if not opens:
            await conn.execute("DELETE FROM positions WHERE id = $1", p["id"])
            continue
        if any(l["action"] != opening for l in lots):
            continue   # closed before the day: as it was
        qty = sum(float(l["qty"]) for l in opens)
        avg = sum(float(l["qty"]) * float(l["price"]) for l in opens) / qty
        await conn.execute(
            "UPDATE positions SET status = 'open', qty = $2, avg_entry_price = $3, closed_at = NULL, "
            "realized_pnl = 0, estimated_charges = 0 WHERE id = $1", p["id"], qty, avg)
    flow = await conn.fetchval(
        "SELECT COALESCE(SUM(CASE WHEN action = 'sell' THEN qty * price ELSE -qty * price END), 0) "
        "FROM position_lots WHERE deployment_id = $1", dep_id)
    await conn.execute("UPDATE deployments SET current_cash = initial_capital + $2 WHERE id = $1", dep_id, flow)
    if state is None:
        await conn.execute("DELETE FROM deployment_state WHERE deployment_id = $1", dep_id)
    else:
        await conn.execute(
            "INSERT INTO deployment_state (deployment_id, state, updated_at) VALUES ($1, $2, now()) "
            "ON CONFLICT (deployment_id) DO UPDATE SET state = $2, updated_at = now()", dep_id, state)


# ── What the app actually did ───────────────────────────────────────────

async def actual_day(conn, dep_id, day: date) -> tuple[list[dict], list[dict]]:
    """(the day's fills, the legs held at the day's end)."""
    start, end = _at(day, time(0)), _at(day + timedelta(days=1), time(0))
    fills = [{"at": r["executed_at"].astimezone(IST), "minute": f"{r['executed_at'].astimezone(IST):%H:%M}",
              "action": r["action"], "symbol": r["symbol"], "token": int(r["instrument_token"]), "qty": float(r["qty"]),
              "price": round(float(r["price"]), 2), "reason": r["reason"] or ""}
             for r in await conn.fetch(
                 "SELECT l.*, p.symbol, p.instrument_token FROM position_lots l JOIN positions p ON p.id = l.position_id "
                 "WHERE l.deployment_id = $1 AND l.executed_at >= $2 AND l.executed_at < $3 ORDER BY l.executed_at",
                 dep_id, start, end)]
    held = []
    for p in await conn.fetch(
            "SELECT * FROM positions WHERE deployment_id = $1 AND opened_at < $2 AND (closed_at IS NULL OR closed_at >= $2) "
            "ORDER BY symbol", dep_id, end):
        opening = "sell" if p["side"] == "short" else "buy"
        q = await conn.fetchval(
            "SELECT COALESCE(SUM(qty), 0) FROM position_lots WHERE position_id = $1 AND action = $2 AND executed_at < $3",
            p["id"], opening, end)
        held.append({"symbol": p["symbol"], "token": int(p["instrument_token"]), "side": p["side"], "qty": float(q),
                     "opened_at": p["opened_at"].astimezone(IST).isoformat()})
    return fills, held


# ── The rules of a day's end ────────────────────────────────────────────

def expiry_of(symbol: str) -> Optional[date]:
    c = parse_contract(symbol or "")
    return c["expiry"] if c else None


def rules(dep: dict, held: list[dict], fills: list[dict], day: date, expiries: Optional[dict] = None) -> list[dict]:
    """The rules of the day's end this deployment broke."""
    out = []
    cfg = dep.get("config") or {}
    cut = _hhmm(cfg.get("force_exit_time"))
    if dep.get("mode") == "intraday" and cut is not None and held:
        out.append({"rule": "flat_after_cutoff", "severity": "critical",
                    "title": f"Still holding {len(held)} leg(s) after its {cut:%H:%M} cut-off",
                    "detail": f"An intraday deployment that exits by {cut:%H:%M} ended the day holding "
                              f"{', '.join(h['symbol'] for h in held)}. Left as they are, they'd be closed at the next "
                              "open, at the next day's prices.",
                    "legs": [h["symbol"] for h in held]})
    if dep.get("mode") == "intraday" and cut is not None:
        late = [f for f in fills if f["at"].time() > (datetime.combine(day, cut) + timedelta(minutes=5)).time()]
        if late:
            out.append({"rule": "closed_late", "severity": "warning",
                        "title": f"{len(late)} fill(s) after its {cut:%H:%M} cut-off",
                        "detail": "; ".join(f"{f['minute']} {f['action']} {f['symbol']} ({f['reason']})" for f in late),
                        "legs": sorted({f["symbol"] for f in late})})
    expired = []
    for h in held:
        e = (expiries or {}).get(h["token"]) or expiry_of(h["symbol"])
        if e is not None and e <= day:
            expired.append(f"{h['symbol']} (expired {e:%d %b})")
    if expired:
        out.append({"rule": "held_past_expiry", "severity": "critical",
                    "title": f"Holding {len(expired)} leg(s) past expiry",
                    "detail": "Still open after its contract's last day: " + ", ".join(expired) + ".",
                    "legs": [x.split(" (")[0] for x in expired]})
    return out


# ── The replay against the app ──────────────────────────────────────────

def compare(actual: list[dict], replayed: list[dict], held: list[dict], replay_held: list[dict],
            day: date) -> dict:
    """Match the replay's fills to the app's: same action, contract and
    quantity within MATCH_WITHIN. Returns {matched, missed (the replay did,
    the app didn't), extra (the app did, the replay didn't), end: {app,
    replay, same}}."""
    left = list(actual)
    matched, missed = [], []
    for r in sorted(replayed, key=lambda f: f["minute"]):
        rt = _at(day, _hhmm(r["minute"]))
        best = None
        for a in left:
            if a["action"] == r["action"] and a["symbol"] == r["symbol"] and abs(a["qty"] - r["qty"]) < 1e-9:
                dt = abs(a["at"] - rt)
                if dt <= MATCH_WITHIN and (best is None or dt < best[0]):
                    best = (dt, a)
        if best is None:
            missed.append(_fill(r))
        else:
            left.remove(best[1])
            matched.append({"replay": _fill(r), "app": _fill(best[1]), "minutes_apart": int(best[0].total_seconds() // 60),
                            "price_diff": round(best[1]["price"] - r["price"], 2)})
    key = lambda l: (l["symbol"], l["side"], round(float(l["qty"]), 4))
    app_end = sorted(key(h) for h in held)
    rep_end = sorted(key(h) for h in replay_held)
    return {"matched": matched, "missed": missed, "extra": [_fill(a) for a in left],
            "end": {"app": [{"symbol": s, "side": d, "qty": q} for s, d, q in app_end],
                    "replay": [{"symbol": s, "side": d, "qty": q} for s, d, q in rep_end],
                    "same": app_end == rep_end}}


def _fill(f: dict) -> dict:
    return {k: f.get(k) for k in ("minute", "action", "symbol", "qty", "price", "reason")}


def verdict(rule_list: list[dict], diff: Optional[dict], outcome, known: bool, why_not: str = "") -> tuple[str, str]:
    """(status, headline)."""
    crit = [r for r in rule_list if r["severity"] == "critical"]
    differs = diff is not None and (diff["missed"] or diff["extra"] or not diff["end"]["same"])
    trades = ""
    if differs:
        n_m, n_e = len(diff["missed"]), len(diff["extra"])
        bits = [f"{n_m} trade(s) the strategy would have made that the app didn't" if n_m else "",
                f"{n_e} the app made that it wouldn't have" if n_e else "",
                "a different end state" if not diff["end"]["same"] and not (n_m or n_e) else ""]
        trades = "; ".join(b for b in bits if b)
    if crit:
        return "rule", crit[0]["title"] + (f" — and the replay found {trades}" if trades else "")
    if not known:
        return "cant_check", why_not + ("" if not rule_list else f" {rule_list[0]['title']}.")
    if not differs:
        if rule_list:
            return "rule", rule_list[0]["title"]
        return "ok", "Traded as its strategy would have, and ended the day as expected"
    if outcome is not None and outcome.auto:
        mins = ", ".join(a["minute"] for a in outcome.auto[:4])
        return "ambiguous", f"The replay found {trades} — but {len(outcome.auto)} minute(s) could be read two ways ({mins}): choose on the Repair page"
    return "mismatch", f"The replay found {trades}"


async def check_deployment(pool, dep: dict, day: date, book: MinuteBook, real_kite) -> dict:
    """The check of one deployment's day. Nothing is written but the result."""
    dep_id = dep["id"]
    async with pool.acquire() as conn:
        fills, held = await actual_day(conn, dep_id, day)
        exp = {int(r["instrument_token"]): r["expiry"] for r in await conn.fetch(
            "SELECT instrument_token, expiry FROM contracts WHERE instrument_token = ANY($1::bigint[])",
            [h["token"] for h in held])} if held else {}
        rule_list = rules(dict(dep), held, fills, day, exp)
        known, state, from_what = await start_state(conn, dep_id, day)
        if not known:
            status, headline = verdict(rule_list, None, None, False,
                                       "No start-of-day state for this day, so the replay wasn't run; the end-of-day rules were.")
            return {"status": status, "headline": headline, "rules": rule_list, "diff": {}, "replay": {"fills_app": len(fills)}}
        tr = conn.transaction()
        await tr.start()
        try:
            await rewind(conn, dep_id, _at(day, OPEN), state)
            fresh = dict(await conn.fetchrow("SELECT * FROM deployments WHERE id = $1", dep_id))
            out = await engine.replay(conn, fresh, engine.Window(day, OPEN, CLOSE), book, real_kite, {}, auto=True,
                                      run=f"daycheck-{uuid.uuid4().hex[:8]}")
        finally:
            await tr.rollback()
    replay = {"start": from_what, "fills": [_fill(f) for f in out.fills], "auto": out.auto, "notes": out.notes,
              "problems": out.problems, "blind": out.blind, "estimated": out.estimated, "fills_app": len(fills)}
    if out.blind:
        status, headline = verdict(rule_list, None, None, False,
                                   f"No prices at all for {', '.join(out.blind)}, so the replay can't be trusted; the end-of-day rules were checked.")
        return {"status": status, "headline": headline, "rules": rule_list, "diff": {}, "replay": replay}
    diff = compare(fills, out.fills, held, out.end_positions, day)
    status, headline = verdict(rule_list, diff, out, True)
    return {"status": status, "headline": headline, "rules": rule_list, "diff": diff, "replay": replay}


async def deployments_of(pool, day: date) -> list[dict]:
    """Every deployment not stopped, and any that traded that day."""
    rows = await pool.fetch(
        "SELECT * FROM deployments d WHERE d.status <> 'stopped' OR EXISTS (SELECT 1 FROM position_lots l "
        "WHERE l.deployment_id = d.id AND l.executed_at >= $1 AND l.executed_at < $2) ORDER BY deployment_name",
        _at(day, time(0)), _at(day + timedelta(days=1), time(0)))
    return [dict(r) for r in rows]


async def run_day(pool, day: date, book: MinuteBook, real_kite, run_id: Optional[int] = None,
                  only: Optional[list] = None, say: Callable = lambda s: None) -> list[dict]:
    """Check every deployment's day and store each result (day_checks)."""
    deps = [d for d in await deployments_of(pool, day) if not only or str(d["id"]) in {str(x) for x in only}]
    results = []
    for i, d in enumerate(deps):
        say(f"[{i + 1}/{len(deps)}] {d['deployment_name']}")
        if run_id is not None:
            await pool.execute("UPDATE day_check_runs SET progress = $2 WHERE id = $1", run_id,
                               {"total": len(deps), "done": i, "current": d["deployment_name"]})
        try:
            r = await check_deployment(pool, d, day, book, real_kite)
        except Exception as e:
            r = {"status": "error", "headline": f"The check failed: {type(e).__name__}: {e}", "rules": [], "diff": {}, "replay": {}}
        await pool.execute(
            "INSERT INTO day_checks (run_id, day, deployment_id, status, headline, rules, diff, replay, checked_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now()) ON CONFLICT (day, deployment_id) DO UPDATE SET "
            "run_id = EXCLUDED.run_id, status = EXCLUDED.status, headline = EXCLUDED.headline, rules = EXCLUDED.rules, "
            "diff = EXCLUDED.diff, replay = EXCLUDED.replay, checked_at = now()",
            run_id, day, d["id"], r["status"], r["headline"], r["rules"], _json(r["diff"]), _json(r["replay"]))
        results.append({**r, "deployment_id": str(d["id"]), "name": d["deployment_name"]})
        say(f"    {r['status']}: {r['headline']}")
    if run_id is not None:
        await pool.execute("UPDATE day_check_runs SET progress = $2 WHERE id = $1", run_id,
                           {"total": len(deps), "done": len(deps), "current": None})
    return results


def _json(v):
    """Datetimes and dates inside a result, as text (it's stored as JSON)."""
    if isinstance(v, dict):
        return {k: _json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v
