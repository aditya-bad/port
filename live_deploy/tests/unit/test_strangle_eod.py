"""strangle_monthly_v2's EOD check: roll the untested side in as usual,
but if that would converge into a straddle, roll the TESTED side out."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.routers import cycles
from app.strategies.strangle_monthly_v2 import StrangleMonthlyV2Strategy

TS = datetime(2026, 10, 16, 15, 13)


def strategy(pe_strike_for_target, enabled=True):
    """CE2 sold at 25300, now 155; PE1 (original) at 24800, now 120.
    The resolver answers the EOD's PE target with `pe_strike_for_target`."""
    s = object.__new__(StrangleMonthlyV2Strategy)
    s.legs = {"CE": [{"token": 2, "symbol": "CE2", "strike": 25300.0, "seq": 3, "entry_price": 140}],
              "PE": [{"token": 1, "symbol": "PE1", "strike": 24800.0, "seq": 2, "entry_price": 100}]}
    s.eod_gap_floor, s.adjustment_band_max, s.adjustment_strike_window = 0.80, 0.95, 40
    s.eod_rollout_instead_of_straddle = enabled
    s.instrument, s.contract_expiry, s.hedges = "NIFTY", None, {}
    s.adjustment_band_min, s.round_strike_multiple, s.round_strike_tolerance_pct = 0.80, 0.0, 0.15
    s.acted, s._roll_blocked_noted = [], {}

    # a replacement is resolved before anything is closed (_replace_leg)
    async def get_leg_by_premium(inst, exp, side, target, **k):
        return SimpleNamespace(strike=pe_strike_for_target, last_price=100.0)

    async def get_leg(inst, exp, strike, side):
        return SimpleNamespace(strike=strike, last_price=100.0)

    async def get_ltp(leg):
        return 100.0
    s.resolver = SimpleNamespace(get_leg_by_premium=get_leg_by_premium, get_leg=get_leg, get_ltp=get_ltp,
                                 get_live_price=get_ltp)

    async def confirm(runner, ts, name, prices, fires):
        return prices

    async def close(runner, ts, side, leg, trigger, tv, **k):
        s.acted.append(("close", leg["symbol"], trigger))
        s.legs[side].remove(leg)

    async def open_(runner, ts, side, target, trigger, tv, **k):
        s.acted.append(("open", side, round(target, 1), trigger))
    s._confirm_trigger, s._close_leg, s._open_leg = confirm, close, open_
    return s


def run(s):
    asyncio.run(s._eod_check(SimpleNamespace(deployment_name="T"), TS, {2: 155.0, 1: 120.0}))
    return s.acted


def test_normal_eod_roll_moves_the_untested_side_in():
    assert run(strategy(25100.0)) == [
        ("close", "PE1", "eod_gap_check"), ("open", "PE", 135.6, "eod_gap_check")]


def test_eod_rolls_the_tested_side_out_instead_of_making_a_straddle():
    # the PE target strike (25300) would meet CE2's strike -> roll CE2 out
    # instead, sized so PE 120 is 87.5% of the new CE: 120 / 0.875 = 137.1
    assert run(strategy(25300.0)) == [
        ("close", "CE2", "eod_rollout"), ("open", "CE", 137.1, "eod_rollout")]


def test_switch_off_keeps_the_old_converging_roll():
    assert run(strategy(25300.0, enabled=False)) == [
        ("close", "PE1", "eod_gap_check"), ("open", "PE", 135.6, "eod_gap_check")]


def test_rollout_fills_are_their_own_event_type():
    t0 = datetime(2026, 10, 16, 4, 30, tzinfo=timezone.utc)
    lot = lambda mins, action, reason, side="short": {
        "executed_at": t0.replace(minute=0) .replace(hour=4 + mins // 60, minute=mins % 60), "side": side,
        "action": action, "metadata": {}, "symbol": "X", "qty": 75, "price": 100, "reason": reason, "position_id": "p"}
    lots = [lot(30, "sell", "initial_entry"), lot(150, "buy", "eod_rollout"), lot(150, "sell", "eod_rollout"),
            lot(300, "buy", "continuous_50pct_trigger"), lot(300, "sell", "continuous_50pct_trigger"), lot(400, "buy", "checkpoint_target")]
    kinds = [e["kind"] for e in cycles._group_events(lots, {"status": "closed"})]
    assert kinds == ["entry", "rollout", "adjustment", "exit"]


def test_a_roll_whose_only_strike_is_its_own_does_nothing_and_says_so():
    """A straddle not known as one (CE and PE both 24800): the strike-cross
    cap can only put the put back where it is. Closing and re-selling one
    contract changes nothing but the book's prices (DTT Nifty Strangle,
    28 Oct, 22850CE): it's left as it is, noted once a day."""
    s = strategy(25000.0)
    s.legs["CE"][0]["strike"] = 24800.0
    s.adjustment_band_min, s._decision_days, noted = 0.80, set(), []

    async def record(ts, rule, outcome, summary, data):
        noted.append((rule, outcome, summary))
    runner = SimpleNamespace(deployment_name="T", record_decision=record)
    asyncio.run(s._roll_side(runner, TS, "PE", 155.0, {2: 155.0, 1: 60.0}, "continuous_50pct_trigger", {}))
    assert s.acted == [], "nothing closed, nothing sold"
    assert noted and noted[0][:2] == ("continuous_50pct_trigger", "no_action") and "PE1's own" in noted[0][2]
