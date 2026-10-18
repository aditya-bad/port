"""Every registered strategy runs several simulated sessions through the
real runner, resolver and fill engine — with a restart mid-session and
(intraday) one missed square-off — and must:
  - never raise,
  - actually trade (a strategy that stops trading is a regression),
  - keep a consistent book (cash, quantities, realized P&L),
  - (intraday) be flat after every close, and have any leg carried past a
    missed square-off closed first thing the next session.
A new strategy is picked up automatically from the registry."""

import math
from datetime import timedelta

import pytest

from app.strategies.registry import list_strategies
from tests.sim import Sim
from tests.strategies.helpers import assert_book_consistent, cfg

STRATS = sorted(s["name"] for s in list_strategies())
POSITIONAL = {"strangle_monthly_v2", "calendar_btst", "trend_credit_spread", "weekly_ema_st_spread"}
# Trading days to simulate: the hourly-signal strategy needs a week of
# candles before its indicators exist; pivots need the previous day.
DAYS = {"weekly_ema_st_spread": 10}


def market(day_i):
    """An oscillating, drifting market: plenty of signal flips."""
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


@pytest.mark.parametrize("strategy", STRATS)
def test_strategy_trades_and_survives_sessions_restarts_and_a_missed_close(strategy, pool_run, monkeypatch):
    async def go(pool):
        mode = "positional" if strategy in POSITIONAL else "intraday"
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
