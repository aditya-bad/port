"""
live_deploy — the repair replay: a deployment's own strategy, run again
through the real runner and fill engine over a stretch of the session the
app missed, on the 1-minute candles of app/repair/market.py.

HOW A REPLAY RUNS
  1. Start where the app stopped: the deployment's open positions, cash
     and saved strategy state as they are in the database, prices as at
     the close of the minute before the window.
  2. The strategy starts (its own on_start, exactly as after a restart),
     then every minute of the window is played as four ticks.
  3. Each minute is played BOTH ways (index up first, down first) from a
     snapshot (a database savepoint plus a copy of the strategy, the
     runner and the price cache). If both ways trade the same (same
     actions on the same contracts for the same reasons, at the same
     moment of the candle), the usual reading of the candle is kept. If they differ, that minute is
     AMBIGUOUS: the replay stops and reports both paths; the caller picks
     one and replays again with that choice fixed, on to the next.
  4. Everything runs inside one database transaction. A plan (dry run)
     rolls it back; apply runs the same replay with the same choices and
     commits, only if it comes out exactly as the plan did.

Fills are the real fill engine's (positions, lots, cash, charges), tagged
metadata.repair = {run, source: "replay-1m", window, minute, phase}. An
applied repair is recorded in stall_repairs with what's needed to undo it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from ..db import queries
from ..deployments.runner import DeploymentRunner
from ..strategies.registry import get_strategy_class
from .market import (
    IST, ORDERS, VIX_TOKEN, MinuteBook, ReplayClock, ReplayDispatcher, ReplayKite,
    kite_lookup, naive_ist, natural_order, replaying, session_minutes,
)


class PlanChanged(Exception):
    """The deployment, or the replay, no longer matches the plan shown."""


class AlreadyRepaired(Exception):
    pass


@dataclass
class Window:
    day: date
    start: time
    end: time = time(15, 30)

    @property
    def minutes(self) -> list[datetime]:
        return session_minutes(self.day, self.start, self.end)

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.day, self.start, IST)

    @property
    def end_at(self) -> datetime:
        return datetime.combine(self.day, self.end, IST)

    def as_dict(self) -> dict:
        return {"day": self.day.isoformat(), "from": self.start.strftime("%H:%M"), "to": self.end.strftime("%H:%M")}


def other(order: str) -> str:
    return "up_first" if order == "down_first" else "down_first"


# ── The database through one connection ─────────────────────────────────


class _Acquired:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class TxPool:
    """A pool stand-in over ONE connection, so everything the runner and
    the fill engine write lands in the replay's transaction (their own
    transactions become savepoints inside it)."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquired(self._conn)

    async def release(self, conn):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ── The runner, tagging and noting every fill ───────────────────────────


class ReplayRunner(DeploymentRunner):
    def __init__(self, deployment, pool, dispatcher, strategy, clock: ReplayClock, run: str, window: Window):
        super().__init__(deployment, pool, None, dispatcher, strategy)
        self.clock = clock
        self.run = run
        self.window = window
        self.log: list[dict] = []
        self.problems: list[str] = []
        # a replay revisits minutes (both ways through one) and runs on
        # candles: no tick filter, no breaker resting the strategy, and
        # what it reports stays with the replay, not the live deployment
        self.filter_ticks = False
        self.use_breaker = False
        self.resync_enabled = False
        self.issues.enabled = False

    def _is_stale_tick(self, tick: dict) -> bool:
        return False   # replayed ticks are in the past by design

    async def report(self, kind, title, detail=None, **kw):
        self.problems.append(f"{self.clock.minute:%H:%M} {title}{f': {detail}' if detail else ''}")

    async def _report(self, kind, title, detail=None, **kw):
        await self.report(kind, title, detail, **kw)

    async def _fill(self, action, symbol, instrument_token, qty, price, executed_at, reason, metadata):
        at = self.clock.now_ist() if executed_at is None else \
            executed_at.replace(tzinfo=IST) if executed_at.tzinfo is None else executed_at
        meta = dict(metadata or {})
        meta["repair"] = {"run": self.run, "source": "replay-1m", **self.window.as_dict(),
                          "minute": f"{self.clock.minute:%H:%M}", "phase": self.clock.phase}
        res = await super()._fill(action, symbol, instrument_token, qty, price, at, reason, meta)
        self.log.append({
            "minute": f"{self.clock.minute:%H:%M}", "phase": self.clock.phase, "at": at.astimezone(IST).isoformat(),
            "action": action, "symbol": symbol, "token": int(instrument_token), "qty": float(qty),
            "price": round(float(price), 2), "reason": reason or "",
            "trigger": (metadata or {}).get("trigger"), "realized": res.get("realized_pnl"),
            "meta": {k: v for k, v in (metadata or {}).items() if k != "repair"},
            "position_id": str(res["position_id"]),
        })
        return res

    async def _record_event(self, event_type, message=None, metadata=None):
        if event_type == "strategy_error":
            self.problems.append(f"{self.clock.minute:%H:%M} {message}")
        await super()._record_event(event_type, message, metadata)


_RUNNER_FIELDS = ("open_positions", "cash", "_session", "_last_saved_state", "_last_sweep_try")


class _Snapshot:
    """Everything one minute can change: rolled back to replay it the
    other way."""

    @classmethod
    async def take(cls, conn, runner: ReplayRunner, dispatcher: ReplayDispatcher) -> "_Snapshot":
        s = cls()
        s.sp = conn.transaction()
        await s.sp.start()
        memo = {id(runner): runner, id(dispatcher): dispatcher, id(runner.pool): runner.pool}
        s.strategy = copy.deepcopy(runner.strategy, memo)
        s.fields = copy.deepcopy({k: getattr(runner, k) for k in _RUNNER_FIELDS})
        s.prices = dispatcher.save()
        s.log, s.problems = len(runner.log), len(runner.problems)
        s.runner, s.dispatcher = runner, dispatcher
        return s

    async def restore(self) -> None:
        await self.sp.rollback()
        r = self.runner
        r.strategy = self.strategy
        for k, v in self.fields.items():
            setattr(r, k, v)
        self.dispatcher.load(self.prices)
        del r.log[self.log:]
        del r.problems[self.problems:]


# ── One replay ──────────────────────────────────────────────────────────


@dataclass
class Ambiguity:
    minute: str
    natural: str
    paths: dict          # order -> {"fills": [...], "pnl": window P&L at the minute's close, "open": [...]}
    candles: list        # [{"label", "open", "high", "low", "close"}] — the index first


@dataclass
class Outcome:
    deployment_id: Any
    window: Window
    choices: dict
    fills: list = field(default_factory=list)
    auto: list = field(default_factory=list)        # ambiguous minutes settled by the usual reading (scan)
    ambiguity: Optional[Ambiguity] = None
    curve: list = field(default_factory=list)       # per minute: t, pnl (repaired), left (as the stall left it), idx, vix
    start_positions: list = field(default_factory=list)
    left_positions: list = field(default_factory=list)   # the stalled legs, marked at the window's last close
    end_positions: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    estimated: list = field(default_factory=list)  # legs priced by model: Kite had no trade in them all day
    blind: list = field(default_factory=list)     # legs it held with no price at all: the plan can't be trusted
    index_name: str = "Index"
    start_equity: float = 0.0
    charges: float = 0.0
    fingerprint: str = ""
    end_state: Optional[dict] = None   # the strategy's saved state at the window's end

    @property
    def complete(self) -> bool:
        return self.ambiguity is None


def fill_key(f: dict) -> tuple:
    return (f["action"], f["symbol"], f["qty"], f["reason"])


def same_trades(a: list, b: list) -> bool:
    """Both ways of playing a minute did the same thing: the same trades,
    each at the same moment of the candle (so at the same price). The same
    trades at opposite extremes (a stop taken at the high one way, at the
    low the other) are NOT the same: the P&L differs."""
    key = lambda f: (*fill_key(f), f["phase"])  # noqa: E731
    return sorted(map(key, a)) == sorted(map(key, b))


def exact(fills: list) -> list:
    return [(f["minute"], f["phase"], f["action"], f["symbol"], f["qty"], f["price"], f["reason"]) for f in fills]


def _value(pos: dict, mark: Optional[float]) -> float:
    px = float(mark) if mark is not None else float(pos["avg_entry_price"])
    q = float(pos["qty"])
    return q * px if pos["side"] == "long" else -q * px


def _equity(runner: ReplayRunner, dispatcher: ReplayDispatcher) -> float:
    return runner.cash + sum(_value(p, dispatcher.last_prices.get(t)) for t, p in runner.open_positions.items())


def _legs(runner: ReplayRunner, dispatcher: ReplayDispatcher) -> list[dict]:
    out = []
    for tok, p in sorted(runner.open_positions.items(), key=lambda kv: kv[1]["symbol"]):
        mark = dispatcher.last_prices.get(tok)
        entry = float(p["avg_entry_price"])
        q = float(p["qty"])
        out.append({"symbol": p["symbol"], "token": tok, "side": p["side"], "qty": q, "entry": round(entry, 2),
                    "mark": None if mark is None else round(float(mark), 2),
                    "pnl": None if mark is None else round((mark - entry) * q * (1 if p["side"] == "long" else -1), 2)})
    return out


def _prime_session(runner: ReplayRunner, book: MinuteBook, before: datetime) -> None:
    """The day so far for the market context stamped on fills."""
    for tok in runner.tokens:
        rows = [c for m, c in book.load(tok).items() if m <= before]
        if rows:
            runner._session[tok] = {"day": before.date(), "open": rows[0]["open"],
                                    "high": max(c["high"] for c in rows), "low": min(c["low"] for c in rows),
                                    "last": rows[-1]["close"], "prev_close": None, "partial": False}


async def replay(conn, dep, window: Window, book: MinuteBook, real_kite, choices: dict,
                 auto: bool = False, run: str = "dry-run") -> Outcome:
    """Replay `dep` over `window` on `conn` (already inside a transaction
    the caller commits or rolls back). `choices`: {"HH:MM": order} for
    minutes already decided. `auto`: settle ambiguous minutes by the usual
    reading of the candle instead of stopping (a quick scan)."""
    out = Outcome(deployment_id=dep["id"], window=window, choices=dict(choices))
    if book.lookup is None and real_kite is not None:
        book.lookup = kite_lookup(real_kite)   # a leg Kite has nothing for by token is asked for by name
    clock = ReplayClock(window.day)
    kite = ReplayKite(real_kite, book, clock)
    disp = ReplayDispatcher(book, clock)
    strategy = get_strategy_class(dep["strategy_name"])()
    runner = ReplayRunner(dep, TxPool(conn), disp, strategy, clock, run, window)
    minutes = window.minutes
    if not minutes:
        out.notes.append("The window has no minutes.")
        return out
    idx_tok = next(iter(sorted(runner.tokens)), None)
    from .market import INDEX_LABELS
    out.index_name = INDEX_LABELS.get(idx_tok, "Index")

    with replaying(kite, clock):
        before = minutes[0] - timedelta(minutes=1)
        clock.set(before, ORDERS["down_first"], 3)
        await runner._reload_positions()
        for tok, p in runner.open_positions.items():
            book.note(tok, p["symbol"])
            disp.subscribed.add(tok)
        # what its legs last filled at: where a never-traded leg's model starts
        for r in await conn.fetch(
                "SELECT p.instrument_token, l.price, l.executed_at FROM position_lots l "
                "JOIN positions p ON p.id = l.position_id WHERE p.deployment_id = $1 AND p.status = 'open'",
                dep["id"]):
            book.anchor(int(r["instrument_token"]), float(r["price"]), r["executed_at"],
                        f"the fill at {naive_ist(r['executed_at']):%d %b %H:%M}")
        for tok in runner.tokens:
            disp.subscribed.add(tok)
        disp.subscribed.add(VIX_TOKEN)
        disp.publish()
        _prime_session(runner, book, before)
        start_positions = {t: dict(p) for t, p in runner.open_positions.items()}
        start_cash = runner.cash
        out.start_positions = _legs(runner, disp)
        out.start_equity = _equity(runner, disp)

        await runner.strategy.on_start(runner)   # a replay that can't start stops here, with the error
        runner.started = True
        await runner.checkpoint_state()

        async def play(m: datetime, order: str) -> list[dict]:
            seq, n = ORDERS[order], len(runner.log)
            for step in range(4):
                clock.set(m, seq, step)
                disp.publish()
                for tok in sorted(runner.tokens):
                    px = disp.last_prices.get(tok)
                    if px is not None:
                        await runner.process_tick({"instrument_token": tok, "last_price": px,
                                                   "exchange_timestamp": clock.now})
            return runner.log[n:]

        def view(fills: list) -> dict:
            return {"fills": [dict(f) for f in fills], "pnl": round(_equity(runner, disp) - out.start_equity, 2),
                    "open": _legs(runner, disp)}

        for m in minutes:
            key = f"{m:%H:%M}"
            idx = book.candle(idx_tok, m) if idx_tok is not None else None
            if key in choices:
                await play(m, choices[key])
            else:
                nat = natural_order(idx)
                snap = await _Snapshot.take(conn, runner, disp)
                alt = await play(m, other(nat))
                alt_view = view(alt)
                held = set(runner.open_positions) | {f["token"] for f in alt}
                await snap.restore()
                got = await play(m, nat)
                if not same_trades(alt, got):
                    held |= set(runner.open_positions) | {f["token"] for f in got}
                    if not auto:
                        out.ambiguity = Ambiguity(
                            minute=key, natural=nat, paths={nat: view(got), other(nat): alt_view},
                            candles=_candles(book, m, [idx_tok, VIX_TOKEN] + sorted(held - {idx_tok, VIX_TOKEN})),
                        )
                        out.fills, out.problems = list(runner.log), list(runner.problems)
                        return out
                    out.auto.append({"minute": key, "picked": nat, "picked_fills": [fill_key(f) for f in got],
                                     "other_fills": [fill_key(f) for f in alt]})
            left = start_cash + sum(_value(p, book.at(t, m, "close")) for t, p in start_positions.items())
            out.curve.append({"t": key, "pnl": round(_equity(runner, disp) - out.start_equity, 2),
                              "left": round(left - out.start_equity, 2),
                              "idx": book.at(idx_tok, m, "close") if idx_tok is not None else None,
                              "vix": book.at(VIX_TOKEN, m, "close")})

        await runner.checkpoint_state()
        out.end_state = await conn.fetchval("SELECT state FROM deployment_state WHERE deployment_id = $1", dep["id"])
        out.fills, out.problems = list(runner.log), list(runner.problems)
        out.end_positions = _legs(runner, disp)
        last = minutes[-1]
        for leg in out.start_positions:
            mark = book.at(leg["token"], last, "close")
            q, sign = leg["qty"], 1 if leg["side"] == "long" else -1
            out.left_positions.append({**leg, "mark": None if mark is None else round(mark, 2),
                                       "pnl": None if mark is None else round((mark - leg["entry"]) * q * sign, 2)})
        closed = list({f["position_id"] for f in out.fills if f["realized"] is not None})
        if closed:
            out.charges = round(float(await conn.fetchval(
                "SELECT COALESCE(SUM(estimated_charges), 0) FROM positions WHERE id = ANY($1::uuid[])", closed) or 0), 2)
    # every leg it held, before or during the window: one with no price at
    # all left the strategy blind, so nothing it did (or didn't) can be trusted
    held = {int(t) for t in start_positions} | {f["token"] for f in out.fills}
    for tok, origin in sorted(book.origin.items()):
        if tok == VIX_TOKEN:
            continue
        label = book.symbols.get(tok, str(tok))
        if origin == "none" and tok in held:
            why = book.errors.get(tok)
            asked = book.tried.get(tok)
            out.blind.append(label)
            out.notes.append(f"No prices at all for {label}: " + (f"Kite said “{why}”" if why else
                             "Kite returned no 1-minute candles for it, and the app stored none")
                             + (f" (asked for {asked})" if asked else "")
                             + "; no earlier price to model it from either"
                             + (f" (the days before: Kite said “{book.history_errors[tok]}”)" if tok in book.history_errors else "")
                             + ". The strategy couldn't see this leg. Run the script with --probe to see what Kite answers.")
        elif origin == "model":
            md = book.models[tok]
            if tok in held or tok in disp.subscribed:
                out.estimated.append(label)
            out.notes.append(f"{label}: Kite has no trade in it all day, so it's priced by model — Black-76 off "
                             f"{md['index']} at {md['iv'] * 100:.1f}% implied volatility, from "
                             f"{md['anchor']} (₹{md['anchor_price']:,.2f} with {md['index']} at {md['level']:,.2f}).")
        elif origin == "kite" and book.before.get(tok) and book.keys.get(tok) and (tok in held or tok in disp.subscribed):
            px, at = book.before[tok]
            out.notes.append(f"{label}: no trade until {book.keys[tok][0]:%H:%M}; before that, its last trade on Kite, "
                             f"₹{px:,.2f} on {at:%d %b %H:%M}.")
        elif origin == "quote":
            px, at = book.before[tok]
            if tok in held or tok in disp.subscribed:
                last = "Kite shows no trade in it ever" if tok in book.never_traded else f"its last trade was {at:%d %b %H:%M}"
                out.notes.append(f"{label}: no trade on Kite all day ({last}), so it stood at ₹{px:,.2f}, Kite's last price. "
                                 "The strangle makes no decision while a leg hasn't traded that day (its stale-price "
                                 "guard), so live it would have done nothing with it either.")
        elif origin == "stale" and tok in book.ltp_used:
            px, _ = book.before[tok]
            if tok in held or tok in disp.subscribed:
                out.estimated.append(label)
            out.notes.append(f"{label}: no trade in it on Kite in the 60 days before or since, so it's at Kite's "
                             f"ltp() for {book.ltp_used[tok]}, ₹{px:,.2f}, the price the app saw that day.")
        elif origin == "stale":
            px, at = book.before[tok]
            if tok in held or tok in disp.subscribed:
                out.estimated.append(label)
            out.notes.append(f"{label}: Kite has no trade in it all day and nothing to model it on; it stood at its "
                             f"last trade, ₹{px:,.2f} on {at:%d %b %H:%M}.")
        elif origin == "none" and tok in disp.subscribed:
            out.notes.append(f"No 1-minute candles for {label}" + (f" (Kite said “{book.errors[tok]}”)" if tok in book.errors else "") + ".")
        elif origin == "stored" and tok in disp.subscribed:
            out.notes.append(f"{label}: candles from the app's own store (Kite had none).")
        elif tok in held and not any(m >= minutes[0] for m in book.keys.get(tok, [])):
            out.notes.append(f"{label}: no trade in the window; its price stood still at its last close before it.")
    return out


def _candles(book: MinuteBook, m: datetime, tokens: list) -> list[dict]:
    rows = []
    for tok in tokens:
        if tok is None:
            continue
        c = book.candle(tok, m)
        if c is not None:
            rows.append({"label": book.symbols.get(tok, str(tok)), "token": tok,
                         **{k: c[k] for k in ("open", "high", "low", "close")}})
    return rows


# ── State of a deployment ───────────────────────────────────────────────


def window_problem(window: Window, now: datetime) -> Optional[str]:
    """Why a window can't be repaired yet: Kite has no candles for minutes
    that haven't happened (the script defaults to today)."""
    if window.day.weekday() >= 5:
        return f"{window.day:%a %d %b %Y} is a weekend: no market. Pass --date YYYY-MM-DD for the day of the stall."
    if window.start_at > now:
        return (f"{window.day:%a %d %b} {window.as_dict()['from']} hasn't happened yet (it's {now.astimezone(IST):%a %d %b %H:%M}), "
                "so Kite has no candles for it. Pass --date YYYY-MM-DD for the day of the stall.")
    if window.end_at > now:
        return (f"The window runs to {window.as_dict()['to']} but it's only {now.astimezone(IST):%H:%M}: "
                f"pass --to {now.astimezone(IST) - timedelta(minutes=1):%H:%M} or wait for the close.")
    return None


async def fingerprint(db, dep_id) -> str:
    """What the replay starts from — open positions, cash, fills — so a
    plan can't be applied over a deployment that changed since."""
    dep = await db.fetchrow("SELECT current_cash FROM deployments WHERE id = $1", dep_id)
    pos = await db.fetch(
        "SELECT id::text, qty::text, avg_entry_price::text FROM positions WHERE deployment_id = $1 AND status = 'open' ORDER BY id",
        dep_id)
    lots = await db.fetchrow(
        "SELECT count(*) AS n, max(executed_at) AS last FROM position_lots WHERE deployment_id = $1", dep_id)
    blob = json.dumps([str(dep["current_cash"]), [tuple(r) for r in pos], lots["n"], str(lots["last"])])
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def live_repair(db, dep_id, day: date):
    return await db.fetchrow(
        "SELECT * FROM stall_repairs WHERE deployment_id = $1 AND day = $2 AND undone_at IS NULL", dep_id, day)


async def candidates(pool, window: Window) -> list[dict]:
    """Every deployment that wasn't stopped, with what it held and booked."""
    deps = await pool.fetch(
        "SELECT * FROM deployments WHERE status IN ('active', 'paused') ORDER BY deployment_name")
    day_start = datetime.combine(window.day, time(0), IST)
    out = []
    for d in deps:
        legs = await pool.fetch(
            "SELECT symbol, side, qty, avg_entry_price, opened_at FROM positions WHERE deployment_id = $1 AND status = 'open' ORDER BY symbol",
            d["id"])
        booked = await pool.fetchval(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE deployment_id = $1 AND status = 'closed' "
            "AND closed_at >= $2 AND closed_at < $3", d["id"], day_start, window.start_at)
        after = await pool.fetchval(
            "SELECT count(*) FROM position_lots WHERE deployment_id = $1 AND executed_at >= $2 "
            "AND NOT (metadata ? 'repair')", d["id"], window.start_at)
        rep = await live_repair(pool, d["id"], window.day)
        out.append({"id": d["id"], "name": d["deployment_name"], "strategy": d["strategy_name"], "mode": d["mode"],
                    "status": d["status"], "capital": float(d["initial_capital"]),
                    "legs": [dict(r) for r in legs], "booked_before": float(booked or 0),
                    "fills_after_start": int(after or 0), "repaired": dict(rep) if rep else None})
    return out


async def plan(pool, dep_id, window: Window, book: MinuteBook, real_kite, choices: dict, auto: bool = False) -> Outcome:
    """A dry run: replay and roll everything back."""
    dep = await queries.get_deployment(pool, dep_id)
    async with pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            fp = await fingerprint(conn, dep_id)
            out = await replay(conn, dep, window, book, real_kite, choices, auto=auto)
        finally:
            await tr.rollback()
    out.fingerprint = fp
    return out


# ── Apply and undo ──────────────────────────────────────────────────────


async def _capture(conn, dep_id) -> dict:
    cash = await conn.fetchval("SELECT current_cash::text FROM deployments WHERE id = $1", dep_id)
    state = await conn.fetchrow("SELECT state FROM deployment_state WHERE deployment_id = $1", dep_id)
    rows = await conn.fetch(
        "SELECT row_to_json(p)::text AS j FROM positions p WHERE deployment_id = $1 AND status = 'open'", dep_id)
    return {
        "cash": cash, "had_state": state is not None, "state": state["state"] if state else None,
        "positions": [json.loads(r["j"]) for r in rows],
        "position_ids": {r["id"] for r in await conn.fetch("SELECT id FROM positions WHERE deployment_id = $1", dep_id)},
        "lot_ids": {r["id"] for r in await conn.fetch("SELECT id FROM position_lots WHERE deployment_id = $1", dep_id)},
        "max_decision": await conn.fetchval(
            "SELECT COALESCE(MAX(id), 0) FROM strategy_decisions WHERE deployment_id = $1", dep_id),
    }


async def _added(conn, dep_id, before: dict) -> dict:
    pos = [r["id"] for r in await conn.fetch("SELECT id FROM positions WHERE deployment_id = $1", dep_id)
           if r["id"] not in before["position_ids"]]
    lots = [r["id"] for r in await conn.fetch("SELECT id FROM position_lots WHERE deployment_id = $1", dep_id)
            if r["id"] not in before["lot_ids"]]
    events = [r["id"] for r in await conn.fetch(
        "SELECT id FROM deployment_events WHERE deployment_id = $1 AND created_at = now()", dep_id)]
    decisions = [r["id"] for r in await conn.fetch(
        "SELECT id FROM strategy_decisions WHERE deployment_id = $1 AND id > $2", dep_id, before["max_decision"])]
    return {"positions": [str(x) for x in pos], "lots": [str(x) for x in lots],
            "events": [str(x) for x in events], "decisions": decisions}


def summarize(o: Outcome, capital: float = 0.0) -> dict:
    """The plan's numbers: P&L over the window as repaired and as the
    stall left it, what was booked, the best and worst moment, the index
    and VIX through it, and the trades by reason."""
    end = o.curve[-1] if o.curve else {"pnl": 0.0, "left": 0.0}
    booked = round(sum(f["realized"] or 0 for f in o.fills), 2)
    worst = min(o.curve, key=lambda c: c["pnl"]) if o.curve else None
    best = max(o.curve, key=lambda c: c["pnl"]) if o.curve else None
    idx = [c["idx"] for c in o.curve if c["idx"] is not None]
    vix = [c["vix"] for c in o.curve if c["vix"] is not None]
    reasons: dict[str, int] = {}
    for f in o.fills:
        reasons[f["reason"]] = reasons.get(f["reason"], 0) + 1
    return {
        "pnl": end["pnl"], "left": end["left"], "change": round(end["pnl"] - end["left"], 2),
        "booked": booked, "charges": o.charges, "net": round(end["pnl"] - o.charges, 2),
        "pct": round(end["pnl"] / capital * 100, 2) if capital else None,
        "worst": {"t": worst["t"], "pnl": worst["pnl"]} if worst else None,
        "best": {"t": best["t"], "pnl": best["pnl"]} if best else None,
        "index": {"first": idx[0], "last": idx[-1], "high": max(idx), "low": min(idx)} if idx else None,
        "vix": {"first": vix[0], "last": vix[-1]} if vix else None,
        "fills": len(o.fills), "reasons": reasons, "open_after": len(o.end_positions),
        "spikes": sum(1 for f in o.fills if f["phase"] in ("up", "down")),
        "blind": list(o.blind),
        "estimated": list(o.estimated),
    }


async def _keep_candles(conn, book: MinuteBook, fills: list) -> None:
    """Store the day's 1-minute candles of every contract the repair
    traded, as the app does for what it held (so its charts show them and
    they outlive the contract's expiry). Market data: kept on undo."""
    from ..candles import parse_contract
    pool = TxPool(conn)
    seen = {}
    for f in fills:
        seen.setdefault(f["token"], f["symbol"])
    rows, contracts = [], []
    for tok, sym in seen.items():
        if book.origin.get(tok) != "kite":
            continue
        rows += [(tok, m.replace(tzinfo=IST), c["open"], c["high"], c["low"], c["close"], c["volume"])
                 for m, c in sorted(book.load(tok).items())]
        p = parse_contract(sym)
        if p:
            contracts.append({"instrument_token": tok, "tradingsymbol": sym, **p})
    await queries.upsert_candles(pool, rows, "kite")
    await queries.upsert_contracts(pool, contracts)


async def apply(pool, dep_id, window: Window, book: MinuteBook, real_kite, expected: Outcome,
                author: Optional[str] = None) -> dict:
    """Replay again with the plan's choices and commit, only if it comes
    out exactly as the plan did. Records the repair (with its undo), a
    journal note and an Activity event."""
    dep = await queries.get_deployment(pool, dep_id)
    run = uuid.uuid4().hex[:12]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT 1 FROM deployments WHERE id = $1 FOR UPDATE", dep_id)
            if await live_repair(conn, dep_id, window.day):
                raise AlreadyRepaired(f"{dep['deployment_name']} was already repaired for {window.day}")
            if await fingerprint(conn, dep_id) != expected.fingerprint:
                raise PlanChanged("the deployment changed since this plan was made (new fills, cash or positions)")
            before = await _capture(conn, dep_id)
            out = await replay(conn, dep, window, book, real_kite, expected.choices, run=run)
            if not out.complete:
                raise PlanChanged(f"the replay found an undecided minute ({out.ambiguity.minute})")
            if out.blind:
                raise PlanChanged(f"no prices for {', '.join(out.blind)}: the replay couldn't see them")
            if exact(out.fills) != exact(expected.fills):
                raise PlanChanged("the replay came out differently from the plan (market data changed?)")
            added = await _added(conn, dep_id, before)
            await _keep_candles(conn, book, out.fills)
            s = summarize(out, float(dep["initial_capital"]))
            w = window.as_dict()
            paths = ", ".join(f"{k} {v.replace('_', ' ')}" for k, v in sorted(expected.choices.items()))
            body = (f"Kite tick stall {w['from']}–{w['to']}: repaired by replaying the strategy on 1-minute candles. "
                    f"{s['fills']} fill(s); window P&L {s['pnl']:+,.0f} (as left by the stall {s['left']:+,.0f}). "
                    + (f"Paths chosen: {paths}." if paths else "No ambiguous minutes."))
            journal = await queries.create_journal_entry(
                TxPool(conn), window.day, body, ["Kite down", "repaired"], deployment_id=dep_id, author=author)
            event = await conn.fetchval(
                "INSERT INTO deployment_events (deployment_id, event_type, message, metadata) "
                "VALUES ($1, 'stall_repair', $2, $3) RETURNING id",
                dep_id, body, {"run": run, **w, "choices": expected.choices, "fills": len(out.fills)})
            undo = {"cash": before["cash"], "had_state": before["had_state"], "state": before["state"],
                    "positions": before["positions"], "added": added,
                    "journal": str(journal), "event": str(event)}
            rid = await conn.fetchval(
                "INSERT INTO stall_repairs (deployment_id, day, window_from, window_to, choices, summary, undo) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                dep_id, window.day, window.start_at, window.end_at, expected.choices,
                {**s, "run": run, "fills_detail": out.fills}, undo)
    return {"id": rid, "run": run, "summary": s, "fills": out.fills}


async def undo(pool, repair_id) -> dict:
    """Take an applied repair back out: its fills, positions, events,
    decisions and journal note removed, the positions it changed, the
    cash and the strategy state restored. Refused once the deployment
    has traded after the window (that would rewrite later history)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow("SELECT * FROM stall_repairs WHERE id = $1 FOR UPDATE", repair_id)
            if r is None or r["undone_at"] is not None:
                raise PlanChanged("no such repair, or it was already undone")
            u, dep_id = r["undo"], r["deployment_id"]
            a = u["added"]
            later = await conn.fetchval(
                "SELECT count(*) FROM position_lots WHERE deployment_id = $1 AND executed_at >= $2 "
                "AND NOT (id = ANY($3::uuid[]))", dep_id, r["window_to"], a["lots"])
            if later:
                raise PlanChanged(f"the deployment traded {later} time(s) after the repaired window; undo would rewrite that")
            await conn.execute("DELETE FROM position_lots WHERE id = ANY($1::uuid[])", a["lots"])
            await conn.execute("DELETE FROM deployment_events WHERE id = ANY($1::uuid[])", a["events"] + [u["event"]])
            await conn.execute("DELETE FROM strategy_decisions WHERE id = ANY($1::bigint[])", a["decisions"])
            await conn.execute("DELETE FROM journal_entries WHERE id = $1::uuid", u["journal"])
            await conn.execute("DELETE FROM positions WHERE id = ANY($1::uuid[])", a["positions"])
            cols = [c["column_name"] for c in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'positions' AND column_name NOT IN ('id', 'deployment_id') ORDER BY ordinal_position")]
            names = ", ".join(f'"{c}"' for c in cols)
            for row in u["positions"]:
                await conn.execute(
                    f"UPDATE positions SET ({names}) = (SELECT {names} FROM json_populate_record(NULL::positions, $1::json)) "
                    "WHERE id = $2::uuid", json.dumps(row), row["id"])
            # a day repair (app/repair/dayrepair.py) also took out the app's own
            # fills from where it diverged: put them, and their positions, back
            removed = u.get("removed") or {}
            for row in removed.get("positions", []):
                if await conn.fetchval("SELECT 1 FROM positions WHERE id = $1::uuid", row["id"]):
                    await conn.execute(
                        f"UPDATE positions SET ({names}) = (SELECT {names} FROM json_populate_record(NULL::positions, $1::json)) "
                        "WHERE id = $2::uuid", json.dumps(row), row["id"])
                else:
                    await conn.execute("INSERT INTO positions SELECT * FROM json_populate_record(NULL::positions, $1::json)",
                                       json.dumps(row))
            for row in removed.get("lots", []):
                await conn.execute("INSERT INTO position_lots SELECT * FROM json_populate_record(NULL::position_lots, $1::json) "
                                   "ON CONFLICT (id) DO NOTHING", json.dumps(row))
            await conn.execute("UPDATE deployments SET current_cash = $2::numeric, updated_at = now() WHERE id = $1",
                               dep_id, u["cash"])
            if u["had_state"]:
                await queries.save_deployment_state(TxPool(conn), dep_id, u["state"])
            else:
                await conn.execute("DELETE FROM deployment_state WHERE deployment_id = $1", dep_id)
            await conn.execute("UPDATE stall_repairs SET undone_at = now() WHERE id = $1", repair_id)
            await conn.execute(
                "INSERT INTO deployment_events (deployment_id, event_type, message, metadata) VALUES ($1, 'stall_repair_undone', $2, $3)",
                dep_id, f"Stall repair of {r['day']} undone", {"repair": str(repair_id)})
    return {"deployment_id": dep_id, "lots_removed": len(a["lots"]), "positions_removed": len(a["positions"])}
