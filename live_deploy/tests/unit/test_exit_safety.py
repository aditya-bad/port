"""Exits must leave nothing behind: a close that fails mid-flatten must not
orphan a leg, untracked positions are swept, and an intraday deployment
is squared off and never carries a leg into the next session."""

import asyncio
from datetime import datetime, time, timezone
from types import SimpleNamespace

from app.deployments.runner import DeploymentRunner
from app.strategies.intraday_dtt_adjusted import IntradayDTTAdjustedStrategy
from app.strategies.strangle_monthly_v2 import StrangleMonthlyV2Strategy

TS = datetime(2026, 10, 16, 15, 0, 5)   # naive local (IST), like Kite ticks


def pos(token, symbol, side="short", qty=75, opened=datetime(2026, 10, 16, 4, 30, tzinfo=timezone.utc)):
    return {"instrument_token": token, "symbol": symbol, "side": side, "qty": qty,
            "avg_entry_price": 100.0, "opened_at": opened, "metadata": {}}


class FakeRunner:
    """Just enough of DeploymentRunner: fills pop open_positions; tokens in
    `fail` raise once, like a DB error or a refused order."""
    deployment_name = "T"
    _SWEEP_GRACE = DeploymentRunner._SWEEP_GRACE
    _SWEEP_RETRY = DeploymentRunner._SWEEP_RETRY
    _IST = DeploymentRunner._IST
    close_open_positions = DeploymentRunner.close_open_positions
    _exit_price = DeploymentRunner._exit_price
    exit_price = DeploymentRunner.exit_price
    clock_now = staticmethod(lambda: datetime.now(timezone.utc))
    _intraday_guard = DeploymentRunner._intraday_guard

    def __init__(self, positions, fail=(), mode="intraday"):
        self.mode = mode
        self.open_positions = {p["instrument_token"]: p for p in positions}
        self.dispatcher = SimpleNamespace(
            last_prices={p["instrument_token"]: 90.0 for p in positions},
            release_instruments=lambda tokens: None, add_instruments=lambda items: None,
        )
        self.fail = set(fail)
        self.fills, self.events, self.reports = [], [], []
        self._square_off_at = time(15, 0)
        self._last_sweep_try = None
        self.resolved = []

        async def resolve(key):
            self.resolved.append(key)
            self.reports = [x for x in self.reports if x["key"] != key]
        self.issues = SimpleNamespace(is_open=lambda key: any(x["key"] == key for x in self.reports), resolve=resolve)

    async def _report(self, kind, title, detail=None, **kw):
        if not self.issues.is_open(kw.get("key", kind)):
            self.reports.append({"kind": kind, "key": kw.get("key", kind), "title": title})

    async def _fill(self, action, symbol, token, qty, price, ts=None, reason=None, metadata=None):
        if token in self.fail:
            self.fail.discard(token)
            raise RuntimeError("order refused")
        self.open_positions.pop(token)
        self.fills.append((action, symbol, reason))
        return {"realized_pnl": 10.0}

    async def buy(self, *a, **k):
        return await self._fill("buy", *a, **k)

    async def sell(self, *a, **k):
        return await self._fill("sell", *a, **k)

    async def _record_event(self, event_type, message=None, metadata=None):
        self.events.append(event_type)

    async def record_strategy_error(self, message, metadata=None):
        self.events.append("strategy_error")

    async def notify_execution(self, *a, **k):
        pass


def leg(token, symbol, role="original"):
    return {"token": token, "symbol": symbol, "exchange": "NFO", "entry_price": 100.0, "strike": 25000.0, "role": role}


def dtt(legs):
    s = object.__new__(IntradayDTTAdjustedStrategy)
    s.legs = legs
    s.realized_pnl_today = 0.0
    return s


def test_dtt_flatten_keeps_a_failed_leg_and_closes_the_rest():
    r = FakeRunner([pos(1, "NIFTY25000CE"), pos(2, "NIFTY25000PE"), pos(3, "NIFTY25200PE")], fail={2})
    s = dtt({"CE": [leg(1, "NIFTY25000CE")], "PE": [leg(2, "NIFTY25000PE"), leg(3, "NIFTY25200PE", "adjustment_1")]})
    asyncio.run(s._flatten_all(r, TS, "force_exit", {}))
    # 1 and 3 closed by the strategy; 2 failed, then the sweep closed it
    assert [f[1] for f in r.fills] == ["NIFTY25000CE", "NIFTY25200PE", "NIFTY25000PE"]
    assert r.open_positions == {}
    # the failed leg stays tracked until a tick confirms it's gone
    assert [l["token"] for l in s.legs["PE"]] == [2]


def test_dtt_flatten_retries_a_leg_that_keeps_failing_next_tick():
    r = FakeRunner([pos(1, "A CE"), pos(2, "B PE")])
    s = dtt({"CE": [leg(1, "A CE")], "PE": [leg(2, "B PE")]})
    orig = r._fill

    async def fail_always(action, symbol, token, *a, **k):
        if token == 2 and not getattr(r, "healthy", False):
            raise RuntimeError("down")
        return await orig(action, symbol, token, *a, **k)
    r._fill = fail_always
    asyncio.run(s._flatten_all(r, TS, "force_exit", {}))
    assert 2 in r.open_positions and [l["token"] for l in s.legs["PE"]] == [2]
    assert "strategy_error" in r.events
    assert [x["kind"] for x in r.reports] == ["square_off_failed"], "a leg left open is raised as an issue"
    r.healthy = True
    asyncio.run(s._flatten_all(r, TS, "force_exit", {}))
    assert r.open_positions == {} and s.legs == {"CE": [], "PE": []}
    assert r.reports == [] and r.resolved, "and cleared once it closes"


def test_dtt_flatten_sweeps_positions_it_was_not_tracking():
    r = FakeRunner([pos(1, "A CE"), pos(9, "ORPHAN PE")])
    s = dtt({"CE": [leg(1, "A CE")], "PE": []})
    asyncio.run(s._flatten_all(r, TS, "force_exit", {}))
    assert r.open_positions == {}
    assert ("buy", "ORPHAN PE", "force_exit_sweep") in r.fills


def test_strangle_flatten_keeps_failed_leg_and_sweeps_orphans():
    r = FakeRunner([pos(1, "N CE"), pos(2, "N PE"), pos(8, "HEDGE CE", side="long"), pos(9, "OLD PE")], fail={2}, mode="positional")
    s = object.__new__(StrangleMonthlyV2Strategy)
    s.legs = {"CE": [leg(1, "N CE")], "PE": [leg(2, "N PE")]}
    s.enable_hedging = False
    s.cycle_realized_pnl = 0.0
    s.cycle_id = 3
    s._snapshot_state = lambda: {}
    s.contract_expiry = None
    s.converged, s.convergence_premium, s.exchange = False, None, "NFO"
    asyncio.run(s._flatten_all(r, TS, "contract_expiry_backstop", {}))
    closed = [f[1] for f in r.fills]
    assert "N CE" in closed and "OLD PE" in closed and "HEDGE CE" in closed
    # untracked short bought back before the untracked hedge is sold
    assert closed.index("OLD PE") < closed.index("HEDGE CE")
    assert [l["token"] for l in s.legs["PE"]] == [2] and 2 in r.open_positions


def test_guard_closes_legs_carried_from_an_earlier_day_before_the_strategy_runs():
    yesterday = datetime(2026, 10, 15, 5, 0, tzinfo=timezone.utc)
    r = FakeRunner([pos(1, "OLD CE", opened=yesterday), pos(2, "NEW PE")])
    asyncio.run(r._intraday_guard({"exchange_timestamp": datetime(2026, 10, 16, 9, 15, 2)}))
    assert [f[1:] for f in r.fills] == [("OLD CE", "carried_over_close")]
    assert list(r.open_positions) == [2]


def test_guard_squares_off_after_the_grace_period_only():
    r = FakeRunner([pos(1, "A CE"), pos(2, "B PE")])
    asyncio.run(r._intraday_guard({"exchange_timestamp": datetime(2026, 10, 16, 15, 0, 30)}))
    assert r.fills == []   # the strategy gets the first minute
    asyncio.run(r._intraday_guard({"exchange_timestamp": datetime(2026, 10, 16, 15, 1, 5)}))
    assert r.open_positions == {} and {f[2] for f in r.fills} == {"square_off_sweep"}


def test_guard_leaves_positional_deployments_alone():
    yesterday = datetime(2026, 10, 15, 5, 0, tzinfo=timezone.utc)
    r = FakeRunner([pos(1, "A CE", opened=yesterday)], mode="positional")
    asyncio.run(r._intraday_guard({"exchange_timestamp": datetime(2026, 10, 16, 15, 20)}))
    assert r.fills == [] and 1 in r.open_positions
