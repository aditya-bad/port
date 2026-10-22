"""Every strategy, through every fault that really happens in production
(tests/strategies/faults.py: bad and repeated ticks, the feed going quiet,
a price spike, Kite down or hanging, a fill failing half-way through a
trade, no price for a leg at exit, saved state corrupt or unreadable).

Whatever the fault, a deployment must:
  - never let an exception reach the tick loop (it would die),
  - keep the book consistent, and be flat after an intraday square-off,
  - not flood the error log (a strategy failing every tick is rested),
  - never close a leg at its entry price without saying so,
  - say what happened: each fault has the report it must leave behind,
    and a strategy that trades less on the fault day than without it
    must have reported why.
A failure here means a strategy that fails silently or dangerously: fix
the strategy (or the runner), not this test."""

import pytest

from app.deployments.strategy_base import StrategyBase
from app.strategies.registry import get_strategy_class, list_strategies
from tests.strategies.faults import FAULTS, run

STRATS = sorted(s["name"] for s in list_strategies())
MAX_ERRORS = 2

# What each fault must leave behind (any one of the kinds).
MUST_REPORT = {
    "bad_ticks": {"bad_ticks"},
    "dup_ticks": {"bad_ticks"},
    "fill_fails": {"fill_failed"},
    "fill_fails_twice": {"fill_failed"},
    "feed_gap": {"feed_gap"},
}
# Reports that explain trading less than usual.
EXPLAINS_LESS = {"entry_delayed", "entry_skipped", "missed_trade", "blocked", "adjustment_delayed",
                 "fill_failed", "resynced", "crash", "state_lost", "feed_gap", "bad_ticks"}


def saves_state(name: str) -> bool:
    return get_strategy_class(name).get_persistable_state is not StrategyBase.get_persistable_state


_baseline: dict[str, int] = {}


async def baseline_fills(pool, monkeypatch, strategy):
    if strategy not in _baseline:
        with monkeypatch.context() as mp:
            _baseline[strategy] = (await run(pool, mp, strategy, "none")).fills_fault_day
    return _baseline[strategy]


@pytest.mark.parametrize("fault", FAULTS)
@pytest.mark.parametrize("strategy", STRATS)
def test_strategy_survives_fault(strategy, fault, pool_run, monkeypatch):
    async def go(pool):
        base = await baseline_fills(pool, monkeypatch, strategy) if fault != "none" else None
        o = await run(pool, monkeypatch, strategy, fault)
        kinds = {r["kind"] for r in o.reports}
        why = f"{strategy} / {fault}: {o.row()}"

        assert not o.escaped, why
        assert o.book is None, why
        assert not o.open_after_close, why
        assert o.errors <= MAX_ERRORS, why
        if o.at_entry:
            assert "exit_at_entry" in kinds, f"closed at the entry price without a report — {why}"

        if fault == "none":
            assert not [r for r in o.reports if r["severity"] != "info"], f"a clean run reported problems — {why}"
            return
        if fault in MUST_REPORT:
            assert kinds & MUST_REPORT[fault], why
        if fault == "state_unreadable" and saves_state(strategy):
            assert not o.started_cold, f"started over state it couldn't read — {why}"
        if fault == "corrupt_state":
            if saves_state(strategy):
                assert "state_lost" in kinds, why
            # rebuilt from the book: no second entry, no lost cap
            assert o.fills_fault_day == base, f"traded {o.fills_fault_day} times vs {base} without the fault — {why}"
        if o.fills_fault_day < base:
            assert kinds & EXPLAINS_LESS, f"traded less ({o.fills_fault_day} vs {base}) without saying why — {why}"
        if "fill_failed" in kinds:
            # half a trade was booked: the strategy must have ended up agreeing with the book
            assert not {"legs_mismatch"} & {r["kind"] for r in o.reports if r["resolved_by"] is None}, why
    pool_run(go)
