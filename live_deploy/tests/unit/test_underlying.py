"""The positions page's view of the index (app/v2/underlying.py): each
leg against the spot and its future, its delta, and what a move of the
future does to the book."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.geek.optionmath import fwd_price, years_to
from app.v2 import underlying as U

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 21, 11, 0, tzinfo=IST)
EXP = date(2026, 11, 24)
FUT = {"tradingsymbol": "NIFTY26NOVFUT", "price": 25150.0}


def leg(kind, strike, side="short", qty=75, iv=0.14):
    t = years_to(EXP, NOW)
    px = round(fwd_price(kind, FUT["price"], strike, t, iv), 2)
    return {"symbol": f"NIFTY26NOV{strike}{kind}", "side": side, "qty": qty, "avg_entry_price": px + 20, "price": px,
            "expiry": EXP, "strike": strike}


def test_a_leg_against_the_spot_and_its_future():
    v = U.leg_view(leg("CE", 25600), FUT, 25080.0, NOW)
    assert v["from_spot"] == 520 and v["from_fut"] == 450
    assert v["iv"] == pytest.approx(14.0, abs=0.05)
    assert -75 < v["delta"] < 0 and v["gamma"] < 0          # a short call: loses as the future rises
    p = U.leg_view(leg("PE", 24600), FUT, 25080.0, NOW)
    assert 0 < p["delta"] < 75                               # a short put gains as it rises
    # no price, no future, or not an option: distances only where known
    assert U.leg_view({**leg("CE", 25600), "price": None}, FUT, 25080.0, NOW)["delta"] is None
    assert U.leg_view(leg("CE", 25600), None, 25080.0, NOW)["from_fut"] is None


def test_scenarios_revalue_each_leg_at_the_moved_future():
    legs = [U.leg_view(leg("CE", 25600), FUT, 25080.0, NOW), U.leg_view(leg("PE", 24600), FUT, 25080.0, NOW)]
    s = {x["move"]: x["pnl"] for x in U.scenarios(legs, {"NIFTY26NOVFUT": FUT})}
    assert list(s) == U.MOVES
    # a short strangle loses on a big move either way, and small moves sit near the delta
    assert s[300] < 0 and s[-300] < 0
    delta, gamma = sum(l["delta"] for l in legs), sum(l["gamma"] for l in legs)
    for x in (-50, 50):   # small moves: delta and gamma (Taylor) agree with the revaluation
        assert s[x] == pytest.approx(delta * x + gamma * x * x / 2, abs=8)
    # revalued, not extrapolated: the loss grows faster than the move (short gamma)
    assert s[300] < 3 * s[100]
