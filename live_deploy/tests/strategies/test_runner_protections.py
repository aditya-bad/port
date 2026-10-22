"""The runner's protections, each on its own, with the database: a
strategy that can't start or keeps failing, bad prices, a quiet feed,
legs the strategy and the book disagree on, a close with no price, an
order that makes no sense, cash below zero, state that can't be saved,
a check that stays blocked. Driven with a probe strategy whose failures
each test chooses (tests/strategies/test_faults.py runs the real ones)."""

import asyncio
import math
from datetime import timedelta

import pytest

from app.deployments.strategy_base import StrategyBase
from tests.sim import INDEX, Sim, ist
from tests.strategies.helpers import cfg

IDX = INDEX["NIFTY"][0]
LEG = 990001


class Probe(StrategyBase):
    start_fails = 0
    tick_fails = False
    tick_sleeps = 0.0
    adopt = True
    state: object = None

    async def on_start(self, runner):
        if Probe.start_fails:
            Probe.start_fails -= 1
            raise ConnectionError("Kite is down")
        self.held = {t: p["symbol"] for t, p in runner.open_positions.items()} if Probe.adopt else {}
        self.seen = []

    async def on_tick(self, runner, t):
        self.seen.append(t)
        if Probe.tick_sleeps:
            await asyncio.sleep(Probe.tick_sleeps)
        if Probe.tick_fails:
            raise RuntimeError("boom")

    async def on_stop(self, runner):
        pass

    def tracked_tokens(self):
        return dict(self.held)

    def get_persistable_state(self):
        return Probe.state


@pytest.fixture(autouse=True)
def reset_probe():
    Probe.start_fails, Probe.tick_fails, Probe.tick_sleeps, Probe.adopt, Probe.state = 0, False, 0.0, True, None


async def probe(pool, monkeypatch, capital=500000):
    sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"), capital=capital)
    sim.market.now = ist("09:20")
    r = sim.runner
    r.issues.WRITE_EVERY = timedelta(0)     # every report written: wall-clock throttling isn't under test here
    sim.strategy = r.strategy = Probe()
    r.started = False
    return sim


async def issues(sim, open_only=False):
    return [dict(x) for x in await sim.pool.fetch(
        "SELECT key, kind, severity, title, detail, day, count, expires_at, resolved_by FROM strategy_issues "
        "WHERE deployment_id = $1" + (" AND resolved_at IS NULL" if open_only else "") + " ORDER BY id", sim.dep_id)]


def kinds(rows):
    return [r["kind"] for r in rows]


# ── a strategy that can't start ───────────────────────────────────────

def test_a_strategy_that_cant_start_raises_and_is_never_fed_ticks(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        Probe.start_fails = 1
        with pytest.raises(ConnectionError):      # the manager keeps it down and says why
            await r.start_strategy()
        assert not r.started
        await sim.tick("09:21")
        assert not hasattr(r.strategy, "seen"), "a half-started strategy never sees a tick"
        await r.start_strategy()                  # the watchdog's retry
        await sim.tick("09:22")
        assert r.started and len(r.strategy.seen) == 1
    pool_run(go)


# ── a strategy that keeps failing ─────────────────────────────────────

def test_a_strategy_that_keeps_failing_is_rested_reported_and_cleared_when_it_recovers(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        await r.start_strategy()
        Probe.tick_fails = True
        for m in range(5):
            await sim.tick(f"09:2{m}")
        assert len(r.strategy.seen) == 5
        await sim.tick("09:25")                    # tripped at 09:24: rested until 09:25
        assert len(r.strategy.seen) == 6
        await sim.tick("09:26")                    # tripped again: rested 2 minutes
        assert len(r.strategy.seen) == 6, "not called while rested"
        [crash] = await issues(sim, open_only=True)
        assert (crash["kind"], crash["severity"]) == ("crash", "critical")
        assert "RuntimeError: boom" in crash["detail"] and "test_runner_protections.py" not in crash["detail"]
        assert len(await sim.events("strategy_error")) == 1, "the same error is logged once, not on every tick"

        Probe.tick_fails = False
        await sim.tick("09:27")
        assert len(r.strategy.seen) == 7
        assert (await issues(sim))[0]["resolved_by"] == "cleared"
    pool_run(go)


def test_an_on_tick_that_hangs_is_cut_off(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        await r.start_strategy()
        r.TICK_TIMEOUT = 0.05
        Probe.tick_sleeps = 1.0
        await sim.tick("09:20")
        await sim.tick("09:21")
        [e1] = await sim.events("strategy_error")
        assert "took longer than" in e1["message"]
        assert kinds(await issues(sim)) == ["crash"]
    pool_run(go)


# ── prices ────────────────────────────────────────────────────────────

def test_bad_prices_never_reach_the_strategy_and_are_reported(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        await r.start_strategy()
        await sim.tick("09:20")
        at = ist("09:21")
        for bad in (None, 0, -1.0, float("nan"), float("inf"), "abc"):
            await r.process_tick({"instrument_token": IDX, "last_price": bad, "exchange_timestamp": at})
        await r.process_tick({"instrument_token": IDX, "last_price": 25000.0})
        assert len(r.strategy.seen) == 1
        [i] = await issues(sim)
        assert i["kind"] == "bad_ticks" and i["expires_at"] is not None
        assert "6 with no usable price" in i["detail"] and "1 with no time" in i["detail"]
    pool_run(go)


def test_a_quiet_feed_in_the_session_is_put_on_the_days_record(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        await sim.runner.start_strategy()
        await sim.tick("09:10")
        await sim.tick("09:30")                    # before the open: not a gap
        await sim.tick("09:34")                    # 4 minutes: not a gap
        await sim.tick("10:00")
        [i] = await issues(sim)
        assert (i["kind"], i["severity"], i["title"]) == ("feed_gap", "info", "No prices from 09:34 to 10:00")
        assert i["day"] == sim.market.today and "26 minutes" in i["detail"]
    pool_run(go)


# ── the strategy and the book ─────────────────────────────────────────

async def open_leg(sim, price=100.0):
    await sim.runner.sell("NIFTYTESTCE", LEG, 75, price, sim.market.now, reason="test")


def test_a_leg_the_strategy_lost_track_of_is_reported_after_two_checks(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        r.resync_enabled = False
        Probe.adopt = False
        await r.start_strategy()
        await open_leg(sim)
        await r.check_legs("periodic")
        assert await issues(sim) == [], "one look isn't enough: a trade may be half-way through"
        await r.check_legs("periodic")
        [i] = await issues(sim)
        assert (i["kind"], i["severity"]) == ("legs_mismatch", "critical")
        assert "NIFTYTESTCE" in i["detail"] and "not managed by the strategy" in i["detail"]
        r.strategy.held = {LEG: "NIFTYTESTCE"}
        await r.check_legs("periodic")
        assert (await issues(sim))[0]["resolved_by"] == "cleared"
    pool_run(go)


def test_after_a_failure_a_strategy_out_of_step_is_restarted_from_the_book(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        Probe.adopt = False
        await r.start_strategy()
        await open_leg(sim)
        Probe.adopt = True                         # a fresh start adopts what's in the book
        Probe.tick_fails = True
        old = r.strategy
        await sim.tick("09:21")
        assert r.strategy is not old and r.strategy.tracked_tokens() == {LEG: "NIFTYTESTCE"}
        rows = await issues(sim)
        assert "resynced" in kinds(rows) and "legs_mismatch" not in kinds(rows)
        assert "lost track of NIFTYTESTCE" in next(x for x in rows if x["kind"] == "resynced")["detail"]
    pool_run(go)


# ── closing without a price ───────────────────────────────────────────

def test_the_exit_price_ladder_ends_at_the_entry_price_only_with_a_report(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        r.dispatcher.last_prices[LEG] = 120.0
        assert await r.exit_price(LEG, "NIFTYTESTCE", 100.0) == (120.0, "live")
        r.dispatcher.last_prices.pop(LEG)
        await pool.execute("INSERT INTO candles_1m (instrument_token, minute, open, high, low, close, volume) "
                           "VALUES ($1, now() - interval '10 minutes', 1, 1, 1, 111.5, 0)", LEG)
        assert await r.exit_price(LEG, "NIFTYTESTCE", 100.0) == (111.5, "candle")
        assert await issues(sim) == []
        await pool.execute("DELETE FROM candles_1m WHERE instrument_token = $1", LEG)
        assert await r.exit_price(LEG, "NIFTYTESTCE", 100.0) == (100.0, "entry")
        [i] = await issues(sim)
        assert (i["kind"], i["severity"], i["key"]) == ("exit_at_entry", "critical", "exit_at_entry:NIFTYTESTCE")
        assert i["day"] == sim.market.today
    pool_run(go)


# ── orders and cash ───────────────────────────────────────────────────

@pytest.mark.parametrize("price,qty", [(float("nan"), 75), (0.0, 75), (-3.0, 75), (100.0, 0), (100.0, float("inf"))])
def test_an_order_that_makes_no_sense_is_refused_and_reported(pool_run, monkeypatch, price, qty):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        with pytest.raises(ValueError):
            await sim.runner.sell("NIFTYTESTCE", LEG, qty, price, sim.market.now, reason="test")
        assert await sim.fills() == []
        [i] = await issues(sim)
        assert (i["kind"], i["severity"]) == ("bad_order", "critical")
    pool_run(go)


def test_buying_back_a_short_is_never_refused_and_cash_below_zero_is_reported(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch, capital=10000)
        r = sim.runner
        await open_leg(sim, price=100.0)            # +7,500 cash
        await r.buy("NIFTYTESTCE", LEG, 75, 400.0, sim.market.now, reason="stop")   # -30,000
        assert await sim.open_positions() == []
        assert r.cash < 0
        [i] = await issues(sim)
        assert (i["kind"], i["severity"]) == ("cash_negative", "critical")
    pool_run(go)


# ── saved state ───────────────────────────────────────────────────────

def test_state_with_nan_in_it_is_saved_with_nulls(pool_run, monkeypatch):
    async def go(pool):
        from app.db import queries
        sim = await probe(pool, monkeypatch)
        await sim.runner.start_strategy()
        Probe.state = {"version": 1, "ema": float("nan"), "bands": [1.5, float("inf")]}
        await sim.runner.checkpoint_state()
        assert await queries.load_deployment_state(pool, sim.dep_id) == {"version": 1, "ema": None, "bands": [1.5, None]}
        assert await issues(sim) == []
    pool_run(go)


def test_state_that_wont_save_is_reported_after_three_tries_and_cleared(pool_run, monkeypatch):
    async def go(pool):
        from app.db import queries
        sim = await probe(pool, monkeypatch)
        await sim.runner.start_strategy()
        orig = queries.save_deployment_state

        async def down(*a, **kw):
            raise ConnectionError("db down")
        monkeypatch.setattr(queries, "save_deployment_state", down)
        for n in range(3):
            Probe.state = {"version": 1, "n": n}
            await sim.runner.checkpoint_state()
        assert kinds(await issues(sim, open_only=True)) == ["state_unsaved"]
        monkeypatch.setattr(queries, "save_deployment_state", orig)
        await sim.runner.checkpoint_state()
        assert await issues(sim, open_only=True) == []
    pool_run(go)


# ── decisions ─────────────────────────────────────────────────────────

def test_a_missed_trade_is_reported_at_once_and_a_block_after_ten_minutes(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        await r.note_decision(ist("10:00"), "entry", "blocked", "No price for the leg", missed=True)
        [m] = await issues(sim)
        assert (m["kind"], m["day"]) == ("missed_trade", sim.market.today)

        await r.note_decision(ist("10:00"), "roll", "blocked", "Kite down", fault=True)
        await r.note_decision(ist("10:09"), "roll", "blocked", "Kite down", fault=True)
        assert len(await issues(sim)) == 1
        await r.note_decision(ist("10:10"), "roll", "blocked", "Kite still down", fault=True)
        b = (await issues(sim))[-1]
        assert (b["kind"], b["title"], b["detail"]) == ("blocked", "“roll” has been blocked since 10:00", "Kite still down")
        await r.note_decision(ist("10:11"), "roll", "acted", "rolled")
        assert (await issues(sim))[-1]["resolved_by"] == "cleared"

        await r.note_decision(ist("10:00"), "cap", "blocked", "2 adjustments used")
        await r.note_decision(ist("10:30"), "cap", "blocked", "2 adjustments used")
        assert len(await issues(sim)) == 2, "a block by design is never raised"
    pool_run(go)


# ── the book ──────────────────────────────────────────────────────────

def test_the_book_answers_what_was_opened_today_and_when_the_last_exit_was(pool_run, monkeypatch):
    async def go(pool):
        sim = await probe(pool, monkeypatch)
        r = sim.runner
        day = sim.market.today
        assert await r.positions_opened_on(day) == 0 and not await r.opened_on(day)
        assert await r.last_exit_at() is None
        await open_leg(sim)
        await r.buy("NIFTYTESTPE", LEG + 1, 75, 50.0, sim.market.now, reason="hedge")
        assert await r.positions_opened_on(day) == 2
        assert await r.positions_opened_on(day, "short") == 1
        assert await r.opened_on(day)
        assert await r.positions_opened_on(day + timedelta(days=1)) == 0
        await r.buy("NIFTYTESTCE", LEG, 75, 90.0, sim.market.now, reason="exit")
        assert await r.last_exit_at() is not None
        assert math.isfinite(r.cash)
    pool_run(go)
