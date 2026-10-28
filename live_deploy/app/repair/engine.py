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
    STEP_SECONDS, kite_lookup, minute_of, naive_ist, natural_order, replaying, session_minutes,
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
        # (minute, action, symbol, qty) -> the app's own fill prices for
        # that trade in that minute (a day check), in the order it made them
        self.app_prices: dict[tuple, list[float]] = {}
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
        # once, at the minute it first came up: a problem that lasts is
        # reported on every tick (four a minute, 1,500 lines a day)
        what = f"{title}{f': {detail}' if detail else ''}"
        if not any(p[6:] == what for p in self.problems):
            self.problems.append(f"{self.clock.minute:%H:%M} {what}")

    async def _report(self, kind, title, detail=None, **kw):
        await self.report(kind, title, detail, **kw)

    async def _fill(self, action, symbol, instrument_token, qty, price, executed_at, reason, metadata):
        at = self.clock.now_ist() if executed_at is None else \
            executed_at.replace(tzinfo=IST) if executed_at.tzinfo is None else executed_at
        meta = dict(metadata or {})
        phase = "app" if self.clock.moment else self.clock.phase
        meta["repair"] = {"run": self.run, "source": "replay-1m", **self.window.as_dict(),
                          "minute": f"{self.clock.minute:%H:%M}", "phase": phase}
        # the same trade the app made in this minute: at the price it really got
        key = (f"{self.clock.minute:%H:%M}", action, symbol, round(float(qty), 6))
        real = self.app_prices.get(key, [])
        n = sum(1 for f in self.log if (f["minute"], f["action"], f["symbol"], round(f["qty"], 6)) == key)
        app_price = n < len(real)
        if app_price:
            price = real[n]
        res = await super()._fill(action, symbol, instrument_token, qty, price, at, reason, meta)
        self.log.append({
            "minute": f"{self.clock.minute:%H:%M}", "phase": phase, "at": at.astimezone(IST).isoformat(),
            "action": action, "symbol": symbol, "token": int(instrument_token), "qty": float(qty),
            "price": round(float(price), 2), "reason": reason or "",
            "trigger": (metadata or {}).get("trigger"), "realized": res.get("realized_pnl"),
            "meta": {k: v for k, v in (metadata or {}).items() if k != "repair"},
            "position_id": str(res["position_id"]), "app_price": app_price,
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

    async def keep(self) -> None:
        await self.sp.commit()

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
    gone: list = field(default_factory=list)      # expiries it needed that expired before the repair ran: can't be trusted either
    index_name: str = "Index"
    start_equity: float = 0.0
    charges: float = 0.0
    fingerprint: str = ""
    end_state: Optional[dict] = None   # the strategy's saved state at the window's end
    followed: list = field(default_factory=list)   # strikes the app picked that a moment of the minute explains
    edge_only: list = field(default_factory=list)  # minutes a trade only the very edge of the candle made was left out, as the app did
    app_candles: list = field(default_factory=list)  # the strategy's candles where the app's own ticks made them other than Kite's minutes
    app_turns: list = field(default_factory=list)    # where the replay's SuperTrend took the app's logged one

    @property
    def complete(self) -> bool:
        return self.ambiguity is None


# A trade the app didn't make, made only in the last tenth of the way from
# a minute's open/close to its high or low, is the app's call (settle).
EDGE = 0.9
APP_WITHIN = timedelta(minutes=3)   # as the day check matches trades (daycheck.MATCH_WITHIN)


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


def _today() -> date:
    return datetime.now(IST).date()


# The strangle re-checks a trigger its ticks set off against Kite's quotes
# (the bid/ask mid) before acting; when they don't confirm it, it waits
# and logs this, at most once every 5 minutes (strangle_monthly_v2
# _confirm_trigger, _REFUTE_NOTE_EVERY). The replay has no bid/ask from
# the day: what it quotes is the last price, so it can never refuse.
QUOTE_REFUSED = "Not acted on: live quotes don't confirm the tick prices"
REFUSAL_HOLDS = timedelta(minutes=5, seconds=30)   # until the next note was due, and a little


async def _app_refusals(conn, dep_id, day: date, app_fills: list) -> dict[str, list[dict]]:
    """{rule: [{from, to, tick, quote}]}: when the app's own log says
    Kite's quotes refused that rule. Each note holds until the next was
    due (a refusal that lasts is noted every 5 minutes), runs on into the
    next note, and ends early where the app acted on the rule after all."""
    start = datetime.combine(day, time(0), tzinfo=IST)
    rows = await conn.fetch(
        "SELECT decided_at, rule, data FROM strategy_decisions WHERE deployment_id = $1 AND outcome = 'blocked' "
        "AND summary = $2 AND decided_at >= $3 AND decided_at < $4 ORDER BY decided_at",
        dep_id, QUOTE_REFUSED, start, start + timedelta(days=1))
    acted = {}
    for f in sorted(app_fills, key=lambda f: f["at"]):
        acted.setdefault(f.get("reason") or "", []).append(naive_ist(f["at"]))
    out: dict[str, list[dict]] = {}
    for r in rows:
        at = naive_ist(r["decided_at"])
        data = json.loads(r["data"]) if isinstance(r["data"], str) else (r["data"] or {})
        to = min([at + REFUSAL_HOLDS] + [t for t in acted.get(r["rule"], []) if t > at])
        spans = out.setdefault(r["rule"], [])
        if spans and at <= spans[-1]["to"]:
            spans[-1]["to"] = max(spans[-1]["to"], to)
            spans[-1]["notes"] += 1
        else:
            spans.append({"from": at, "to": to, "tick": data.get("tick") or {}, "quote": data.get("quote") or {}, "notes": 1})
    return out


# A short leg with no trade yet today is priced from its bid/ask mid
# (strangle_monthly_v2 _quote_stale), each new price logged with this.
# The replay's quotes are made up around the last trade (yesterday's,
# for such a leg), so it takes the app's instead.
QUOTED = "Priced from a live quote: not traded yet today"


async def _app_quotes(conn, dep_id, day: date) -> dict[int, list[tuple[datetime, float]]]:
    """{token: [(at, price)]}: each live-quote price the app logged for a
    leg that hadn't traded, in order."""
    start = datetime.combine(day, time(0), tzinfo=IST)
    rows = await conn.fetch(
        "SELECT decided_at, data FROM strategy_decisions WHERE deployment_id = $1 AND outcome = 'no_action' "
        "AND summary = $2 AND decided_at >= $3 AND decided_at < $4 ORDER BY decided_at",
        dep_id, QUOTED, start, start + timedelta(days=1))
    out: dict[int, list[tuple[datetime, float]]] = {}
    for r in rows:
        data = json.loads(r["data"]) if isinstance(r["data"], str) else (r["data"] or {})
        for tok, px in (data.get("tokens") or {}).items():
            out.setdefault(int(tok), []).append((naive_ist(r["decided_at"]), float(px)))
    return out


def _honour_quotes(strategy, quotes: dict) -> None:
    """A leg the app priced from its live quote is priced the same in the
    replay: the last price the app logged for it at or before the tick.
    None (hold) before the app had one, as it held then."""
    if getattr(type(strategy), "_quote_stale", None) is None:
        return

    async def _quote_stale(runner, ts, stale):
        me = runner.strategy
        t = naive_ist(ts)
        legs = [leg for side_legs in me.legs.values() for leg in side_legs if leg["symbol"] in stale]
        out = {}
        for leg in legs:
            px = next((p for at, p in reversed(quotes.get(int(leg["token"]), ())) if at <= t), None)
            if px is None:
                return None
            out[leg["token"]] = px
            me.__dict__.setdefault("_stale_quotes", {})[leg["token"]] = (px, ts)
        return out

    strategy._quote_stale = _quote_stale


OHLC = ("open", "high", "low", "close")


async def _app_candles(conn, dep_id, day: date, app_fills: list, strategy, tokens) -> dict[datetime, dict]:
    """{a candle's start: what the app's strategy saw of it}, for a
    strategy that builds candles from its own ticks (a CandleAggregator).
    Kite's minutes can close a candle a few points from where the app's
    last tick did, and a SuperTrend flip or a level break can hinge on
    that. Each candle it completed, as it completed it (strategy_candles,
    since migration 0040). And for each candle it acted on, or flipped on
    and declined, what it logged then (a fill's trigger_values, a
    decision's data): the candle (or its close) and its SuperTrend after
    it ("st": trend, bands, ATR), so a SuperTrend that drifted on candles
    nobody logged is the app's again from there. Only from the tick that
    closed the candle (a fill stamped at its candle's start, or a candle
    closed by a late tick, says nothing sure), and only for a strategy fed
    one token, its index."""
    agg = getattr(strategy, "aggregator", None)
    if agg is None or not hasattr(agg, "_floor") or len(tokens) != 1:
        return {}
    start = datetime.combine(day, time(0), tzinfo=IST)
    out: dict[datetime, dict] = {}
    for r in await conn.fetch("SELECT start, open, high, low, close FROM strategy_candles WHERE deployment_id = $1 "
                              "AND start >= $2 AND start < $3", dep_id, start, start + timedelta(days=1)):
        out[naive_ist(r["start"])] = {k: float(r[k]) for k in OHLC}
    seen = [(f["at"], f.get("signal") or {}) for f in app_fills if f.get("timed") is not False and f.get("signal")]
    for r in await conn.fetch("SELECT decided_at, data FROM strategy_decisions WHERE deployment_id = $1 "
                              "AND decided_at >= $2 AND decided_at < $3 AND data ? 'close'",
                              dep_id, start, start + timedelta(days=1)):
        seen.append((r["decided_at"], json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]))
    for at, v in seen:
        t = naive_ist(at)
        bucket = agg._floor(t)
        if t - bucket >= timedelta(minutes=1):
            continue
        candle = {k: float(x) for k, x in (v.get("candle") or {}).items() if k in OHLC and x is not None}
        if not candle and v.get("close") is not None:
            candle = {"close": float(v["close"])}
        if not candle:
            continue
        rec = out.setdefault(bucket - timedelta(minutes=agg.interval_minutes), {})
        for k, x in candle.items():
            rec.setdefault(k, x)
        st = {k: float(v[k]) for k in ("final_upper", "final_lower", "atr") if v.get(k) is not None}
        if v.get("new_trend") in ("up", "down"):
            rec.setdefault("st", {**st, "trend": v["new_trend"]})
    return out


def _use_app_candles(strategy, candles: dict, used: dict, turned: dict) -> None:
    """The strategy's aggregator hands it the app's own candle wherever
    there is one (_app_candles), in place of the one the replay's ticks
    made (with only the close known, the rest stands, widened to take
    it); and where the app logged its SuperTrend after a candle, the
    strategy's SuperTrend takes those values on that candle. `used`:
    {start: (the replay's candle, the app's)} where they differ;
    `turned`: {start: (the replay's SuperTrend, the app's)} where those do."""
    agg = getattr(strategy, "aggregator", None)
    if agg is None or not candles:
        return
    base = type(agg)

    def add_tick(self, ts, price):
        done = base.add_tick(self, ts, price)
        if done is None:
            return None
        start = naive_ist(done["date"])
        rec = candles.get(start)
        if rec is None:
            return done
        app = {**done, **{k: rec[k] for k in OHLC if k in rec}}
        app["high"], app["low"] = max(app["high"], app["close"]), min(app["low"], app["close"])
        if any(abs(float(app[k]) - float(done[k])) >= 0.005 for k in OHLC):
            used[start] = (dict(done), dict(app))
        if rec.get("st"):
            app["app_st"] = {**rec["st"], "start": start}
        return app

    agg.__class__ = type(f"AppCandles{base.__name__}", (base,), {"add_tick": add_tick})
    st = getattr(strategy, "st", None)
    if st is None or not hasattr(st, "final_upper"):
        return
    st_base = type(st)

    def update(self, candle):
        trend = st_base.update(self, candle)
        app = candle.get("app_st") if isinstance(candle, dict) else None
        if not app:
            return trend
        mine = {"trend": trend, "final_upper": self.final_upper, "final_lower": self.final_lower, "atr": self.atr}
        for k in ("final_upper", "final_lower", "atr"):
            if k in app:
                setattr(self, k, app[k])
        self.trend, self.prev_close = app["trend"], candle["close"]
        if mine["trend"] != app["trend"] or any(
                k in app and (mine[k] is None or abs(mine[k] - app[k]) >= 0.01) for k in ("final_upper", "final_lower")):
            turned[app["start"]] = (mine, {k: app[k] for k in ("trend", "final_upper", "final_lower", "atr") if k in app})
        return self.trend

    st.__class__ = type(f"AppSuperTrend{st_base.__name__}", (st_base,), {"update": update})


def _honour_refusals(strategy, refusals: dict, held: list) -> None:
    """Where the app's log says Kite's quotes refused a rule, the replay's
    strategy holds off it too: the quotes it would need aren't stored. The
    stand-in calls the strategy the runner holds then (a minute replayed
    again runs on a copy of it)."""
    confirm = getattr(type(strategy), "_confirm_trigger", None)
    if confirm is None or not refusals:
        return

    async def _confirm_trigger(runner, ts, name, prices, still_fires):
        t = naive_ist(ts)
        span = next((s for s in refusals.get(name, ()) if s["from"] <= t < s["to"]), None)
        if span is not None:
            if not any(h is span for h in held):
                held.append(span)
            return None
        return await confirm(runner.strategy, runner, ts, name, prices, still_fires)

    strategy._confirm_trigger = _confirm_trigger


def _real_prices(app_fills: list) -> tuple[dict, dict, dict, dict]:
    """The app's own fills as ({(token, minute): price}, {(HH:MM, action,
    symbol, qty): [prices]}, {minute: [strikes it picked by premium]},
    {minute: {second: {token: price}}}: the prices it really saw, at the
    moment it traded). A fill stamped at its candle's start (`timed`
    False, daycheck.actual_day) is left out: the minute it really happened
    in isn't known."""
    from ..candles import parse_contract
    quoted, filled, picks, moments = {}, {}, {}, {}
    for f in sorted(app_fills, key=lambda f: f["at"]):
        if f.get("timed") is False:
            continue
        m = minute_of(f["at"])
        quoted.setdefault((int(f["token"]), m), float(f["price"]))
        moments.setdefault(m, {}).setdefault(naive_ist(f["at"]).second, {}).setdefault(int(f["token"]), float(f["price"]))
        filled.setdefault((f"{m:%H:%M}", f["action"], f["symbol"], round(float(f["qty"]), 6)), []).append(float(f["price"]))
        basis, c = f.get("basis") or {}, parse_contract(f["symbol"]) or {}
        if basis.get("target_premium") is not None and c.get("instrument_type") in ("CE", "PE"):
            picks.setdefault(m, []).append({"symbol": f["symbol"], "underlying": c["underlying"],
                                            "type": c["instrument_type"], "target": float(basis["target_premium"])})
    return quoted, filled, picks, moments


async def replay(conn, dep, window: Window, book: MinuteBook, real_kite, choices: dict,
                 auto: bool = False, run: str = "dry-run", app_fills: Optional[list] = None) -> Outcome:
    """Replay `dep` over `window` on `conn` (already inside a transaction
    the caller commits or rolls back). `choices`: {"HH:MM": order} for
    minutes already decided. `auto`: settle ambiguous minutes by the usual
    reading of the candle instead of stopping (a quick scan). `app_fills`:
    what the app itself did that day (a day check, daycheck.actual_day): a
    contract it filled in a minute is quoted at its fill price in that
    minute, and the same trade (action, contract, qty) made in the same
    minute fills at that price, so the strategy carries the real price
    (a stop set off its entry) rather than the candle's. And where the
    strategy picks a strike by premium in a minute the app picked a
    different one, the app's is taken when some moment of the minute
    explains it (ReplayKite.follow_app). And each moment the app traded
    is played too, between the candle's ticks, with the prices the app
    really saw then: a 1-minute candle can't show two legs falling
    together for a moment, and a profit target the app took there would
    otherwise be missed."""
    out = Outcome(deployment_id=dep["id"], window=window, choices=dict(choices))
    if book.lookup is None and real_kite is not None:
        book.lookup = kite_lookup(real_kite)   # a leg Kite has nothing for by token is asked for by name
    clock = ReplayClock(window.day)
    kite = ReplayKite(real_kite, book, clock)
    kite.app_quotes, app_prices, kite.app_picks, app_moments = _real_prices(app_fills or [])
    # what was listed on the day, as far as the app knows: every contract a
    # deployment held is stored with its exact expiry, and outlives Kite's
    # list. Only expiries that have passed since: one still to come is in
    # Kite's own list, and a stored one that isn't there is no expiry at all
    for r in await conn.fetch("SELECT DISTINCT underlying, expiry FROM contracts "
                              "WHERE expiry >= $1 AND expiry < $2 AND instrument_type IN ('CE', 'PE')",
                              window.day, _today()):
        if r["underlying"]:
            kite.known_expiries.setdefault(r["underlying"], set()).add(r["expiry"])
    disp = ReplayDispatcher(book, clock)
    strategy = get_strategy_class(dep["strategy_name"])()
    held_off: list[dict] = []
    _honour_refusals(strategy, await _app_refusals(conn, dep["id"], window.day, app_fills or []), held_off)
    _honour_quotes(strategy, await _app_quotes(conn, dep["id"], window.day))
    runner = ReplayRunner(dep, TxPool(conn), disp, strategy, clock, run, window)
    runner.app_prices = app_prices
    minutes = window.minutes
    if not minutes:
        # nothing left to replay (a repair from after the close): the book
        # as it stands, so the plan doesn't show legs still held as flat
        await runner._reload_positions()
        out.start_positions = out.end_positions = _legs(runner, disp)
        out.end_state = await conn.fetchval("SELECT state FROM deployment_state WHERE deployment_id = $1", dep["id"])
        out.notes.append("Nothing left to replay after the window's start: the legs stand as they were then.")
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
        # the strategy's candles as the app built them from its own ticks
        app_candles: dict = {}
        app_turns: dict = {}
        _use_app_candles(runner.strategy, await _app_candles(conn, dep["id"], window.day, app_fills or [],
                                                             runner.strategy, runner.tokens), app_candles, app_turns)

        app_trades = [(minute_of(f["at"]), minute_of(f.get("until") or f["at"]), f["action"], f["symbol"],
                       round(float(f["qty"]), 6)) for f in app_fills or []]

        def app_made(f: dict, m: datetime) -> bool:
            q = round(float(f["qty"]), 6)
            return any(a == f["action"] and s_ == f["symbol"] and q_ == q and lo - APP_WITHIN <= m <= hi + APP_WITHIN
                       for lo, hi, a, s_, q_ in app_trades)

        async def feed() -> None:
            for tok in sorted(runner.tokens):
                px = disp.last_prices.get(tok)
                if px is not None:
                    await runner.process_tick({"instrument_token": tok, "last_price": px,
                                               "exchange_timestamp": clock.now})

        async def play(m: datetime, order) -> list[dict]:
            seq, n = (ORDERS[order] if isinstance(order, str) else order), len(runner.log)
            moments = sorted((app_moments.get(m) or {}).items())
            for step in range(4):
                clock.set(m, seq, step)
                disp.publish()
                await feed()
                # the app's own moments before the next tick: what it really saw then
                upto = STEP_SECONDS[step + 1] if step < 3 else 60
                while moments and moments[0][0] < upto:
                    sec, prices = moments.pop(0)
                    clock.moment = m + timedelta(seconds=sec)
                    try:
                        for tok, px in prices.items():
                            disp.last_prices[tok], disp.last_price_times[tok] = px, clock.now
                        await feed()
                    finally:
                        clock.moment = None
            return runner.log[n:]

        async def settle(m: datetime, order: str) -> tuple[list[dict], bool]:
            """Play `m` in `order`. Against the app's own day (a day check):
            a trade the app didn't make, made at an extreme of the candle,
            that the same minute with every extreme pulled in to EDGE of
            the way from its open/close doesn't make, is left out. The candle puts every
            contract at its own high or low at one moment; live, each
            reached it at its own time, and a trigger only that last tenth
            sets off is one the app could rightly not have seen. One made
            further in is a real difference. (True when it was left out.)"""
            if not app_trades:
                return await play(m, order), False
            snap = await _Snapshot.take(conn, runner, disp)
            got = await play(m, order)
            lone = [f for f in got if f["phase"] in ("up", "down") and not app_made(f, m)]
            if not lone:
                await snap.keep()
                return got, False
            await snap.restore()
            snap = await _Snapshot.take(conn, runner, disp)
            book.edge = EDGE
            try:
                inner = await play(m, order)
            finally:
                book.edge = 1.0
            made = lambda fs: sorted((f["action"], f["symbol"], f["qty"]) for f in fs if app_made(f, m))   # noqa: E731
            if all(app_made(f, m) for f in inner) and made(inner) == made(got):
                await snap.keep()
                out.edge_only.append({"minute": f"{m:%H:%M}", "left_out": [
                    {k: f[k] for k in ("phase", "action", "symbol", "qty", "price", "reason")} for f in lone]})
                return inner, True
            await snap.restore()
            return await play(m, order), False

        def view(fills: list) -> dict:
            return {"fills": [dict(f) for f in fills], "pnl": round(_equity(runner, disp) - out.start_equity, 2),
                    "open": _legs(runner, disp)}

        for m in minutes:
            key = f"{m:%H:%M}"
            idx = book.candle(idx_tok, m) if idx_tok is not None else None
            if key in choices:
                await settle(m, choices[key])
            else:
                nat = natural_order(idx)
                snap = await _Snapshot.take(conn, runner, disp)
                alt = await play(m, other(nat))
                alt_view = view(alt)
                held = set(runner.open_positions) | {f["token"] for f in alt}
                await snap.restore()
                got, settled = await settle(m, nat)
                if not settled and not same_trades(alt, got):
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
    made = {(f["minute"], f["symbol"]) for f in out.fills}
    out.followed = [v for k, v in sorted(kite.followed.items()) if k in made]
    for f in out.followed:
        out.notes.append(f"{f['minute']}: the app picked {f['app']} where the candle's usual reading picked {f['replay']}. "
                         f"With the app's own target premium ({f['target']:,.2f}; the candle's {f['replay_target']:,.2f}) "
                         f"and every price {f['moment'] * 100:.0f}% of the way through its candle's range, the strategy "
                         "picks the app's strike too: a near-tie inside the minute, so the replay follows the app.")
    for c in out.edge_only:
        out.notes.append(f"{c['minute']}: the candle's usual reading made "
                         + ", ".join(f"{f['action']} {f['symbol']} ({f['reason']}, with the index at its {'high' if f['phase'] == 'up' else 'low'})"
                                     for f in c["left_out"])
                         + f", which the app didn't. That takes every contract to its high or low at one moment; live, "
                         f"each reached its own at its own time. With every price going {EDGE:.0%} of the way there the strategy "
                         "makes no such trade: only the very edge of the minute sets it off, so the replay keeps to the app.")
    interval = getattr(getattr(strategy, "aggregator", None), "interval_minutes", None)
    for start, (mine, app) in sorted(app_candles.items()):
        out.app_candles.append({"start": f"{start:%H:%M}", "minutes": interval,
                                "replay": {k: round(float(mine[k]), 2) for k in ("open", "high", "low", "close")},
                                "app": {k: round(float(app[k]), 2) for k in ("open", "high", "low", "close")}})
    for c in out.app_candles:
        end = datetime.strptime(c["start"], "%H:%M") + timedelta(minutes=c["minutes"] or 0)
        diff = [f"{k} {c['app'][k]:,.2f} (Kite's minutes: {c['replay'][k]:,.2f})" for k in ("open", "high", "low", "close")
                if abs(c["app"][k] - c["replay"][k]) >= 0.005]
        out.notes.append(f"The {c['start']}–{end:%H:%M} candle, as the app built it from its own ticks: " + ", ".join(diff)
                         + ". The strategy decided on the app's, so the replay gives it the app's.")
    for start, (mine, app) in sorted(app_turns.items()):
        end = start + timedelta(minutes=interval or 0)
        def bands(x):
            return ", ".join(f"{w} {x[k]:,.2f}" for k, w in (("final_upper", "upper band"), ("final_lower", "lower band"), ("atr", "ATR"))
                             if x.get(k) is not None)
        out.app_turns.append({"at": f"{end:%H:%M}", "replay": mine, "app": app})
        out.notes.append(f"At {end:%H:%M} the app's SuperTrend was {app['trend']} ({bands(app)}), as it logged it then; on "
                         f"Kite's minutes the replay's was {mine['trend']} ({bands(mine)}). Its earlier candles were built "
                         "from the app's own ticks and weren't kept, so the replay takes the app's SuperTrend from there.")
    for s in held_off:
        def px_view(px: dict) -> str:
            return ", ".join(f"{k} ₹{float(v):,.2f}" for k, v in px.items())
        out.notes.append(f"{s['from']:%H:%M}–{s['to']:%H:%M}: the app's log says Kite's quotes didn't confirm what its ticks "
                         f"set off ({s['notes']} note{'' if s['notes'] == 1 else 's'}; first: on ticks {px_view(s['tick'])}, "
                         f"at the bid/ask mid {px_view(s['quote'])}), so it held off. The replay has no bid/ask from the day "
                         "and holds off there too.")
    same = [f for f in out.fills if f["app_price"]]
    if same:
        out.notes.append(f"{len(same)} trade(s) the app also made in the same minute are at the app's own fill price, "
                         "what Kite really quoted then, not the candle's: "
                         + ", ".join(f"{f['minute']} {f['action']} {f['symbol']} ₹{f['price']:,.2f}" for f in same[:6])
                         + (" …" if len(same) > 6 else "") + ".")
    for und, exp in sorted(kite.gone):
        out.gone.append(f"{und} {exp:%d %b %Y}")
        out.notes.append(f"{und}'s {exp:%d %b} contracts expired before this repair ran: Kite no longer lists them "
                         f"or serves their prices, so the strategy couldn't pick its strikes there the way it did on "
                         f"{window.day:%d %b}. Without this check it would have traded the next expiry instead. A repair "
                         "can be trusted while the day's contracts are still listed (the day check runs after the 17:00 export the same day).")
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
        "gone": list(o.gone),
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
            if out.gone:
                raise PlanChanged(f"{', '.join(out.gone)} expired before this repair: the replay couldn't trade them")
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
