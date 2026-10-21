"""Replay's arithmetic (app/geek/replay.py): the step size for a window,
market minutes, the P&L split adding up, strikes, and what each action
was within its episode."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.geek import replay as R
from app.geek.optionmath import implied_vol, years_to

IST = ZoneInfo("Asia/Kolkata")


def test_steps_stay_under_about_1500():
    assert R.pick_interval(375) == "minute"
    assert R.pick_interval(5 * 375) == "5minute"
    assert R.pick_interval(25 * 375) == "15minute"
    assert R.pick_interval(80 * 375) == "60minute"
    for days in (1, 4, 20, 60):
        m = days * 375
        assert m / R.INTERVAL_MIN[R.pick_interval(m)] <= 1500


def test_market_minutes_skip_nights_and_weekends():
    fri = datetime(2026, 10, 16, 15, 0, tzinfo=IST)
    assert R.market_minutes(fri, fri + timedelta(days=3)) == 30 + 345    # Fri 15:00-15:30, Mon 09:15-15:00
    assert R.market_minutes(fri, fri) == 0


def test_the_split_adds_up_to_the_legs_pnl():
    t0 = datetime(2026, 10, 12, 10, 0, tzinfo=IST)
    exp = date(2026, 11, 24)
    iv = implied_vol("PE", 150.0, 25000, 24000, years_to(exp, t0))
    leg = {"iv": iv, "k": 24000, "kind": "PE", "expiry": exp, "t0": t0, "s0": 25000, "p0": 150.0, "qty": 75, "sold": True}
    t, m, v = R.split_leg(leg, t0 + timedelta(days=3), 24700, 190.0)
    assert t + m + v == pytest.approx((150.0 - 190.0) * 75, abs=0.01)
    assert t > 0 and m < 0          # a seller gains from time, loses as the index falls toward the put
    assert R.split_leg({**leg, "iv": None}, t0, 25000, 150) is None


def test_strike_from_the_fill_first():
    assert R.strike_of({"strike": 23250}, {"strike": 2423250}) == 23250
    assert R.strike_of({"target_basis": {"selected_strike": "24000"}}, {}) == 24000
    assert R.strike_of({}, {"strike": 25000.0}) == 25000
    assert R.strike_of(None, {}) is None


def test_what_each_action_was_within_its_episode():
    t = datetime(2026, 10, 12, 10, tzinfo=IST)
    legs = [{"opened_at": t, "closed_at": t + timedelta(hours=5)}, {"opened_at": t, "closed_at": None}]
    a = lambda kind, trig="x", dt=0: {"kind": kind, "trigger": trig, "t_last": t + timedelta(hours=dt)}  # noqa: E731
    first, adj, roll, hedge, part, ex = a("entry"), a("switch"), a("switch", "eod_rollout"), a("entry", "hedge_add"), a("exit", dt=1), a("exit", dt=6)
    acts = [first, adj, roll, hedge, part, ex]
    assert [R.kind_of(x, 0, acts, legs) for x in acts] == ["new", "adjustment", "roll", "hedge", "leg_closed", "leg_closed"]
    assert R.kind_of(ex, 0, acts, [legs[0]]) == "exit"
