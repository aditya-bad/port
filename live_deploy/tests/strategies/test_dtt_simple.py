"""intraday_dtt_simple through the real runner, resolver and fill engine
on a simulated market: entry, spike stop, decay target, square-off,
late start, restart, next day, carried legs."""

from datetime import timedelta

from tests.sim import Sim
from tests.strategies.helpers import assert_book_consistent, cfg

S = "intraday_dtt_simple"


async def sim_for(pool, monkeypatch, **over):
    return await Sim.create(pool, monkeypatch, S, cfg(S, **over))


def reasons(fills, action="buy"):
    return {f["reason"] for f in fills if f["action"] == action}


def test_quiet_day_enters_at_entry_time_and_squares_off_at_force_exit(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch)
        await sim.walk("09:15", "09:55", every_min=5)
        assert await sim.open_positions() == []
        await sim.tick("10:00")
        legs = await sim.open_positions()
        assert sorted(p["symbol"][-2:] for p in legs) == ["CE", "PE"]
        assert all(p["side"] == "short" and float(p["qty"]) == 75 for p in legs)
        assert len({float(p["avg_entry_price"]) > 0 for p in legs}) == 1
        await sim.walk("10:05", "14:55", every_min=10)
        assert len(await sim.open_positions()) == 2
        await sim.tick("15:00")
        assert await sim.open_positions() == []
        assert reasons(await sim.fills()) == {"force_exit"}
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_leg_spike_stop_exits_both_and_does_not_re_enter(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch)
        await sim.tick("10:00", spot=25000)
        await sim.tick("10:30", spot=25500)    # CE far more than +40%
        assert await sim.open_positions() == []
        assert reasons(await sim.fills()) == {"leg_spike_stop"}
        await sim.walk("10:35", "14:00", every_min=30, spot_fn=lambda t: 25000)
        assert await sim.open_positions() == []
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_premium_decay_hits_profit_target(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch)
        await sim.tick("10:00")
        sim.market.vol = 0.12                   # premiums fall ~25%
        await sim.tick("11:00")
        assert await sim.open_positions() == []
        assert reasons(await sim.fills()) == {"profit_target_decay"}
        closed = await sim.all_positions()
        assert sum(float(p["realized_pnl"]) for p in closed) > 0
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_late_start_catches_up_when_allowed(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch)
        await sim.tick("11:40")
        assert len(await sim.open_positions()) == 2
    pool_run(go)


def test_late_start_skips_the_day_when_catch_up_is_off(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch, catch_up_late_entry=False)
        await sim.walk("11:40", "14:40", every_min=30)
        assert await sim.open_positions() == []
    pool_run(go)


def test_first_tick_after_force_exit_time_never_enters(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch)
        await sim.tick("15:05")
        assert await sim.open_positions() == []
    pool_run(go)


def test_restart_mid_day_resumes_legs_without_re_entering_and_still_exits(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch)
        await sim.tick("10:00")
        await sim.restart()
        await sim.walk("10:10", "14:50", every_min=20)
        assert len(await sim.open_positions()) == 2
        assert len([f for f in await sim.fills() if f["action"] == "sell"]) == 2   # no second entry
        await sim.tick("15:00")
        assert await sim.open_positions() == []
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_trades_again_the_next_day(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch)
        await sim.tick("10:00")
        await sim.tick("15:00")
        d2 = sim.market.today + timedelta(days=1)
        await sim.tick(f"{d2} 09:30")
        assert await sim.open_positions() == []
        await sim.tick(f"{d2} 10:00")
        assert len(await sim.open_positions()) == 2
        await sim.tick(f"{d2} 15:00")
        assert await sim.open_positions() == []
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_legs_missed_at_square_off_are_closed_next_morning_and_the_day_trades_normally(pool_run, monkeypatch):
    async def go(pool):
        sim = await sim_for(pool, monkeypatch)
        await sim.tick("10:00")
        await sim.tick("14:50")                  # no tick after 15:00: the exit is missed
        d2 = sim.market.today + timedelta(days=1)
        await sim.tick(f"{d2} 09:16")
        assert await sim.open_positions() == []
        assert reasons(await sim.fills()) == {"carried_over_close"}
        await sim.tick(f"{d2} 10:00")
        legs = await sim.open_positions()
        assert len(legs) == 2, "the strategy must trade the new day after its carried legs were closed"
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)
