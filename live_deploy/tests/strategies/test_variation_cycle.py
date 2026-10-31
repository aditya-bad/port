"""A variation that continues its primary's cycle (app/variations.copy_book)
on the strangle's simulated market: it holds the same legs, knows the
cycle's booked P&L and its convergence, and leaves earlier cycles out;
then it manages the cycle under its own settings."""

from datetime import timedelta

from app import variations as V
from app.db import queries
from app.deployments.runner import DeploymentRunner
from app.strategies.registry import get_strategy_class
from tests.strategies.helpers import assert_book_consistent, cfg
from tests.strategies.test_strangle import S, closes, converge, converged_state, shorts, start


async def variation_of(sim, label="v", **over):
    """A paused variation of the sim's deployment holding a copy of its cycle,
    and a runner started on it as a restart would."""
    p = dict(await queries.get_deployment(sim.pool, sim.dep_id))
    row = await queries.create_deployment(sim.pool, f"{p['deployment_name']} · {label}", S, "positional",
                                          float(p["initial_capital"]), {**p["config"], **over})
    await sim.pool.execute("UPDATE deployments SET variation_of = $2, variation_label = $3, status = 'active' WHERE id = $1",
                           row["id"], p["id"], label)
    copied = await V.copy_book(sim.pool, p, row["id"], today=sim.market.today)
    strat = get_strategy_class(S)()
    runner = DeploymentRunner(await queries.get_deployment(sim.pool, row["id"]), sim.pool, None, sim.dispatcher, strat)
    runner._is_stale_tick = lambda t: False
    runner.clock_now = sim.now
    await runner._reload_positions()
    await runner.start_strategy()
    return row["id"], runner, copied


def legs_of(strategy):
    return {side: sorted((l["symbol"], l["qty"], l["role"]) for l in strategy.legs[side]) for side in ("CE", "PE")}


def test_it_continues_a_converged_cycle_with_its_trailing_stop(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch, convergence_mode="trailing_stop")
        await converge(sim)                     # rolls on the way: closed legs in the cycle
        p = sim.runner.strategy
        vid, runner, copied = await variation_of(sim)
        v = runner.strategy
        assert copied["legs_closed"] > 0 and copied["state"], copied
        assert legs_of(v) == legs_of(p) and v.cycle_id == p.cycle_id
        assert converged_state(v) == converged_state(p), "the convergence the book can't say came with the state"
        assert abs(v.cycle_realized_pnl - p.cycle_realized_pnl) < 0.01, "the rolls' booked P&L counts toward its checkpoint"
        await assert_book_consistent(pool, vid)
        await assert_book_consistent(pool, sim.dep_id)
        # and it survives a crash of its own as itself
        again = get_strategy_class(S)()
        r2 = DeploymentRunner(await queries.get_deployment(pool, vid), pool, None, sim.dispatcher, again)
        r2.clock_now = sim.now
        await r2._reload_positions()
        await r2.start_strategy()
        assert legs_of(again) == legs_of(v) and converged_state(again) == converged_state(v)
        assert abs(again.cycle_realized_pnl - v.cycle_realized_pnl) < 0.01
        assert {r["deployment_id"] for r in await pool.fetch(
            "SELECT DISTINCT deployment_id FROM positions WHERE deployment_id IN ($1, $2)", sim.dep_id, vid)} == {sim.dep_id, vid}
    pool_run(go)


def test_earlier_cycles_stay_with_the_primary(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        sim.market.vol = 0.13          # the checkpoint closes cycle 1 and cycle 2 opens
        await sim.tick("11:00", spot=25000)
        assert "checkpoint_target" in {f["reason"] for f in closes(await sim.fills())}
        vid, runner, copied = await variation_of(sim)
        cycles = {int(r["metadata"]["cycle_id"]) for r in await pool.fetch("SELECT metadata FROM positions WHERE deployment_id = $1", vid)}
        assert cycles == {2} and copied["legs_held"] == 2 and copied["legs_closed"] == 0
        assert runner.strategy.cycle_id == 2 and runner.strategy.entered_ever
        assert abs(runner.strategy.cycle_realized_pnl) < 0.01, "cycle 1's profit isn't this cycle's"
        await assert_book_consistent(pool, vid)
    pool_run(go)


def test_then_it_manages_the_cycle_under_its_own_settings(pool_run, monkeypatch):
    """The same profit: the primary's 0.5% checkpoint isn't reached, the
    variation's 0.05% is, and it flattens and re-enters on its own."""
    async def go(pool):
        sim = await start(pool, monkeypatch, checkpoint_profit_pct_of_capital=0.005)
        vid, runner, _ = await variation_of(sim, "early", checkpoint_profit_pct_of_capital=0.0005)
        sim.market.vol = 0.159         # a small fall in premium
        t = sim.market.now + timedelta(minutes=30)
        sim.market.now = t
        sim.dispatcher.publish()
        tick = {"instrument_token": 256265, "last_price": 25000, "exchange_timestamp": t}
        await sim.runner.process_tick(tick)
        await runner.process_tick(tick)
        mine = [dict(r) for r in await pool.fetch("SELECT reason FROM position_lots WHERE deployment_id = $1", vid)]
        theirs = [f["reason"] for f in await sim.fills()]
        assert "checkpoint_target" in {r["reason"] for r in mine} and "checkpoint_target" not in theirs
        assert len(await shorts(sim)) == 2, "the primary still holds its cycle"
        await assert_book_consistent(pool, vid)
    pool_run(go)


def test_nothing_held_is_nothing_to_continue(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        for p in await sim.open_positions():
            await pool.execute("UPDATE positions SET status = 'closed', closed_at = now() WHERE id = $1", p["id"])
        p = dict(await queries.get_deployment(pool, sim.dep_id))
        try:
            await V.cycle_rows(pool, p)
        except V.VariationError as e:
            assert "no cycle to continue" in str(e)
        else:
            raise AssertionError("refused")
    pool_run(go)
