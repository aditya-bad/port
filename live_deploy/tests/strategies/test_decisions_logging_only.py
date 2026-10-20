"""Every strategy notes its decisions for Geek mode (StrategyBase.decide):
the checks that didn't trade and why. That must be logging only. Each
strategy runs the same simulated sessions twice, once noting decisions
and once with decide() switched off, and must place exactly the same
orders; and a decision log that fails to save never reaches the
strategy."""

from datetime import timedelta

import pytest

from app.deployments.strategy_base import StrategyBase
from app.strategies.registry import list_strategies
from tests.sim import Sim
from tests.strategies.helpers import cfg
from tests.strategies.test_all_strategies_smoke import DAYS, POSITIONAL, market, trading_days

STRATS = sorted(s["name"] for s in list_strategies())
OUTCOMES = {"no_action", "blocked"}


async def _run(pool, monkeypatch, strategy, name):
    mode = "positional" if strategy in POSITIONAL else "intraday"
    sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=25000, name=name)
    step = 5 if strategy in DAYS else 1
    for i, d in enumerate(trading_days(sim.market.today, DAYS.get(strategy, 2))):
        await sim.walk(f"{d} 09:15", f"{d} 15:29", every_min=step, spot_fn=market(i + 1))
    fills = [(f["symbol"], f["action"], float(f["qty"]), round(float(f["price"]), 4), f["executed_at"], f["reason"])
             for f in await pool.fetch(
                 "SELECT p.symbol, l.action, l.qty, l.price, l.executed_at, l.reason FROM position_lots l "
                 "JOIN positions p ON p.id = l.position_id WHERE l.deployment_id = $1 ORDER BY l.executed_at, p.symbol, l.action",
                 sim.dep_id)]
    decisions = [dict(r) for r in await pool.fetch(
        "SELECT rule, outcome, summary, data FROM strategy_decisions WHERE deployment_id = $1 ORDER BY decided_at", sim.dep_id)]
    return fills, decisions


@pytest.mark.parametrize("strategy", STRATS)
def test_noting_decisions_never_changes_what_a_strategy_trades(strategy, pool_run, monkeypatch):
    async def go(pool):
        with_log, decisions = await _run(pool, monkeypatch, strategy, f"{strategy} with decisions")
        real = StrategyBase.decide

        async def off(self, *a, **k):
            return None
        monkeypatch.setattr(StrategyBase, "decide", off)
        without, none = await _run(pool, monkeypatch, strategy, f"{strategy} without")
        monkeypatch.setattr(StrategyBase, "decide", real)
        assert with_log, f"{strategy} never traded"
        assert with_log == without, f"{strategy}: noting decisions changed its orders"
        # what was noted is well formed
        for d in decisions:
            assert d["outcome"] in OUTCOMES, d
            assert d["rule"] and d["summary"] and len(d["summary"]) < 400, d
            assert isinstance(d["data"], dict)
        # the simulation has no feed gaps and every leg ticks: nothing about
        # either belongs in the log (a session's last candle closes on the
        # next morning's first tick; a new leg's first tick takes a moment)
        assert not [d for d in decisions if d["rule"] == "stale_candle"], "an overnight candle isn't a feed gap"
        assert not [d for d in decisions if "No live price" in d["summary"]], "a leg's first seconds aren't a missing price"
        if strategy != "strangle_monthly_v2":   # the strangle keeps its own helper
            assert not none, "switched off: nothing noted"
    pool_run(go)


def test_a_decision_that_fails_to_save_never_reaches_the_strategy(pool_run):
    async def go(pool):
        class Runner:
            calls = 0

            async def record_decision(self, *a):
                Runner.calls += 1
                raise RuntimeError("database down")

        class S(StrategyBase):
            async def on_start(self, runner): ...
            async def on_tick(self, runner, tick): ...
            async def on_stop(self, runner): ...

        from datetime import datetime
        s, r, t = S(), Runner(), datetime(2026, 10, 20, 10, 0)
        await s.decide(r, t, "entry", "blocked", "x")          # raises inside: swallowed
        await s.decide(None, t, "entry", "blocked", "x")       # no runner hook: ignored
        await s.decide(r, None, "entry", "blocked", "x")       # no time: ignored
        assert Runner.calls == 1
    pool_run(go)


def test_once_notes_a_repeating_check_once_a_day(pool_run):
    async def go(pool):
        seen = []

        class Runner:
            async def record_decision(self, ts, rule, outcome, summary, data):
                seen.append((ts, rule, outcome))

        class S(StrategyBase):
            async def on_start(self, runner): ...
            async def on_tick(self, runner, tick): ...
            async def on_stop(self, runner): ...

        from datetime import datetime
        s, r = S(), Runner()
        t = datetime(2026, 10, 20, 10, 0)
        for m in range(30):
            await s.decide(r, t + timedelta(minutes=m), "exit_checks", "blocked", "no price", once="no_price")
        await s.decide(r, t, "exit_checks", "blocked", "other", once="one_leg")      # a different key
        await s.decide(r, t + timedelta(days=1), "exit_checks", "blocked", "no price", once="no_price")   # next day
        await s.decide(r, t, "entry", "blocked", "every time")
        await s.decide(r, t, "entry", "blocked", "every time")
        assert [x[1:] for x in seen] == [("exit_checks", "blocked")] * 3 + [("entry", "blocked")] * 2
        assert seen[2][0].date() == (t + timedelta(days=1)).date()
    pool_run(go)
