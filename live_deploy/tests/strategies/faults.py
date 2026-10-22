"""The fault matrix: every registered strategy, run through the same
simulated sessions as the smoke test with one fault injected on the last
session — the things that really go wrong in production: bad ticks, a
dead feed, a price spike, Kite down, a fill that fails half-way through a
multi-leg trade, no price for a leg at exit time, corrupted or unreadable
saved state, duplicated and out-of-order ticks, a hung Kite call.

`run(pool, monkeypatch, strategy, fault)` returns what happened:
  escaped   exceptions that reached the caller (the live loop would die)
  book      a book inconsistency, or None
  open_after_close   legs still open after an intraday square-off
  reports   what the deployment reported (open issues it raised)
  errors    strategy_error events recorded
  at_entry  closes booked at the entry price for want of a price
Used by test_faults.py."""

from __future__ import annotations

import math
from datetime import timedelta

from app.db import queries
from tests.sim import INDEX, Sim, ist
from tests.strategies.helpers import assert_book_consistent, cfg

POSITIONAL = {"strangle_monthly_v2", "calendar_btst", "trend_credit_spread", "weekly_ema_st_spread"}
DAYS = {"weekly_ema_st_spread": 10}
TRENDING = {"strangle_monthly_v2"}

FAULTS = [
    "none", "bad_ticks", "feed_gap", "spike", "kite_down", "fill_fails", "fill_fails_twice",
    "no_leg_price", "corrupt_state", "state_unreadable", "dup_ticks", "kite_hangs",
]


def market(day_i):
    def spot(t):
        m = (t.hour * 60 + t.minute) - 555
        return 25000 + 250 * math.sin((m + day_i * 120) / 60.0) + 40 * day_i * (1 if day_i % 2 else -1)
    return spot


def trading_days(start, n):
    d, out = start, []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class Out:
    def __init__(self):
        self.escaped: list[str] = []
        self.book = None
        self.open_after_close: list[str] = []
        self.reports: list[dict] = []
        self.errors = 0
        self.at_entry: list[str] = []
        self.fills_fault_day = 0
        self.started_cold = False

    def row(self):
        return {"escaped": self.escaped[:3], "book": self.book, "open_after_close": self.open_after_close,
                "reports": [r["kind"] for r in self.reports], "errors": self.errors, "at_entry": self.at_entry,
                "fills": self.fills_fault_day}


async def run(pool, monkeypatch, strategy: str, fault: str) -> Out:
    out = Out()
    mode = "positional" if strategy in POSITIONAL else "intraday"
    sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=25000, name=f"{strategy}/{fault}")
    step = 5 if strategy in DAYS else 1
    days = trading_days(sim.market.today, DAYS.get(strategy, 3))
    last = days[-1]
    idx_tok = INDEX[sim.underlying][0]
    in_window = lambda a, b: ist(a, last) <= sim.market.now < ist(b, last)   # noqa: E731

    # ── the faults ──────────────────────────────────────────────────────
    if fault in ("kite_down", "kite_hangs"):
        k = sim.kite
        for name in ("instruments", "ltp", "quote", "historical_data"):
            orig = getattr(k, name)

            def broken(*a, _orig=orig, **kw):
                if in_window("09:15", "11:00"):
                    if fault == "kite_hangs":
                        import time as _t
                        _t.sleep(0.02)   # the client's own timeout fires; here: a slow, then failing call
                    raise ConnectionError("Kite is down (simulated)")
                return _orig(*a, **kw)
            setattr(k, name, broken)
    if fault in ("fill_fails", "fill_fails_twice"):
        orig_fill = queries.record_fill
        seen = {"n": 0}

        async def flaky(*a, **kw):
            if sim.market.now.date() == last:
                seen["n"] += 1
                # the second fill of the day: half-way through a two-leg entry
                if seen["n"] == 2 or (fault == "fill_fails_twice" and seen["n"] in (2, 3)):
                    raise asyncpg_error()
            return await orig_fill(*a, **kw)
        monkeypatch.setattr(queries, "record_fill", flaky)
    if fault == "no_leg_price":
        orig_publish = sim.dispatcher.publish

        def publish():
            orig_publish()
            if sim.market.now.date() == last and sim.market.now.time() >= ist("14:30", last).time():
                for tok in list(sim.dispatcher.last_prices):
                    if tok != idx_tok and tok not in (v[0] for v in INDEX.values()):
                        sim.dispatcher.last_prices.pop(tok, None)
        sim.dispatcher.publish = publish
        orig_quote = sim.kite.quote

        def quote(keys):
            if sim.market.now.date() == last and sim.market.now.time() >= ist("14:30", last).time():
                return {}
            return orig_quote(keys)
        sim.kite.quote = quote
        sim.kite.ltp = lambda keys: {} if sim.market.now.date() == last and sim.market.now.time() >= ist("14:30", last).time() else type(sim.kite).ltp(sim.kite, keys)

    async def guarded(coro, what):
        try:
            await coro
        except Exception as e:   # what the live tick loop would have died of
            out.escaped.append(f"{what}: {type(e).__name__}: {e}")

    # ── the sessions ────────────────────────────────────────────────────
    for i, d in enumerate(days):
        spot = market(i + 1)
        if d == last and strategy in TRENDING:
            # a strategy that holds for weeks only acts on a real move: the
            # fault day runs up, so it has something to do while things fail
            base = spot
            spot = lambda t, base=base: base(t) + 2.2 * ((t.hour * 60 + t.minute) - 555)   # noqa: E731
        fault_day = d == last
        t, end = ist("09:15", d), ist("15:29", d)
        while t <= end:
            hhmm = t.strftime("%H:%M")
            if fault_day and fault == "feed_gap" and "11:00" <= hhmm < "13:30":
                t += timedelta(minutes=step)
                continue
            s = spot(t)
            if fault_day and fault == "spike" and hhmm == "12:00":
                s = s * 3
            await guarded(sim.tick(t, spot=s), f"tick {d} {hhmm}")
            if fault_day and fault == "bad_ticks" and t.minute % 7 == 0:
                for bad in (None, 0, -5.0, float("nan"), float("inf"), "abc"):
                    await guarded(sim.runner.process_tick({"instrument_token": idx_tok, "last_price": bad, "exchange_timestamp": t}),
                                  f"bad tick {bad!r} {hhmm}")
                await guarded(sim.runner.process_tick({"instrument_token": idx_tok, "last_price": s}), f"tick without time {hhmm}")
            if fault_day and fault == "dup_ticks":
                await guarded(sim.tick(t, spot=s), f"duplicate {hhmm}")
                if t.minute % 10 == 0:
                    await guarded(sim.tick(t - timedelta(minutes=5), spot=spot(t - timedelta(minutes=5))), f"out of order {hhmm}")
            if fault_day and fault == "corrupt_state" and hhmm == "12:00":
                await queries.save_deployment_state(pool, sim.dep_id, {"garbage": True, "legs": "not-a-list", "entered_on": 42,
                                                                       "cycle_id": {"x": 1}, "st": [1, 2]})
                await guarded(sim.restart(), "restart with corrupt state")
            if fault_day and fault == "state_unreadable" and hhmm == "12:00":
                orig_load = queries.load_deployment_state

                async def fail(*a, **kw):
                    raise asyncpg_error()
                monkeypatch.setattr(queries, "load_deployment_state", fail)
                # the start must fail (the manager keeps it down and says
                # why) rather than start cold over state it couldn't read
                try:
                    await sim.restart()
                    out.started_cold = True
                except Exception:
                    pass
                monkeypatch.setattr(queries, "load_deployment_state", orig_load)
                await guarded(sim.restart(), "the watchdog's restart once state is readable")
            t += timedelta(minutes=step)
        sim.market.now = ist("16:30", d)
        await guarded(sim.end_of_day(), f"end of day {d}")

    # ── what came of it ─────────────────────────────────────────────────
    try:
        await assert_book_consistent(pool, sim.dep_id)
    except AssertionError as e:
        out.book = str(e)
    if mode == "intraday" and sim.runner.config.get("force_exit_time"):
        out.open_after_close = [p["symbol"] for p in await sim.open_positions()]
    out.errors = len(await sim.events("strategy_error"))
    out.at_entry = [r["symbol"] for r in await pool.fetch(
        "SELECT p.symbol FROM position_lots l JOIN positions p ON p.id = l.position_id "
        "WHERE l.deployment_id = $1 AND (l.metadata->>'price_source' IN ('entry', 'entry_price') OR l.metadata->'trigger_values'->>'price_source' IN ('entry', 'entry_price'))", sim.dep_id)]
    out.fills_fault_day = await pool.fetchval(
        "SELECT count(*) FROM position_lots WHERE deployment_id = $1 AND (executed_at AT TIME ZONE 'Asia/Kolkata')::date = $2",
        sim.dep_id, last)
    if await pool.fetchval("SELECT to_regclass('strategy_issues') IS NOT NULL"):
        out.reports = [dict(r) for r in await pool.fetch(
            "SELECT key, kind, severity, title, detail, resolved_by FROM strategy_issues WHERE deployment_id = $1 ORDER BY id", sim.dep_id)]
    out.sim = sim
    return out


def asyncpg_error():
    import asyncpg
    return asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation (simulated)")

