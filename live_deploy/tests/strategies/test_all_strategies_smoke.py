"""Every registered strategy runs several simulated sessions through the
real runner, resolver and fill engine — with a restart mid-session and
(intraday) one missed square-off — and must:
  - never raise,
  - actually trade (a strategy that stops trading is a regression),
  - keep a consistent book (cash, quantities, realized P&L),
  - (intraday) be flat after every close, and have any leg carried past a
    missed square-off closed first thing the next session.
A new strategy is picked up automatically from the registry."""


import pytest

from tests.sim import Sim
from tests.strategies.helpers import DAYS, STRATS, assert_book_consistent, cfg, market, mode_of, trading_days



@pytest.mark.parametrize("strategy", STRATS)
def test_strategy_trades_and_survives_sessions_restarts_and_a_missed_close(strategy, pool_run, monkeypatch):
    async def go(pool):
        mode = mode_of(strategy)
        sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=25000)
        squares_off = mode == "intraday" and bool(sim.runner.config.get("force_exit_time"))
        step = 5 if strategy in DAYS else 1
        days = trading_days(sim.market.today, DAYS.get(strategy, 3))
        for i, d in enumerate(days):
            spot = market(i + 1)
            if i == 0:
                await sim.walk(f"{d} 09:15", f"{d} 12:30", every_min=step, spot_fn=spot)
                await sim.restart()
                await sim.walk(f"{d} 12:30", f"{d} 15:29", every_min=step, spot_fn=spot)
            elif i == 1 and squares_off:
                # the session's last ticks never arrive: a missed square-off
                await sim.walk(f"{d} 09:15", f"{d} 14:30", every_min=step, spot_fn=spot)
                continue
            else:
                if i == 2 and squares_off:
                    await sim.tick(f"{d} 09:15", spot=spot(sim.market.now))
                    held_overnight = [p for p in await sim.open_positions()
                                      if p["opened_at"].date() < d]
                    assert held_overnight == [], f"{strategy}: legs carried past a missed square-off weren't closed at the open"
                await sim.walk(f"{d} 09:15", f"{d} 15:29", every_min=step, spot_fn=spot)
            if squares_off:
                assert await sim.open_positions() == [], f"{strategy} still holds legs after its square-off on {d}"
        fills = await sim.fills()
        assert fills, f"{strategy} never traded in {len(days)} simulated sessions"
        for p in await sim.open_positions():
            assert float(p["qty"]) > 0
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)
