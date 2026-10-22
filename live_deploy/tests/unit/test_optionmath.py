"""Black-Scholes for Geek mode (app/geek/optionmath.py)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.geek import optionmath as M

IST = ZoneInfo("Asia/Kolkata")


def test_prices_against_known_values_and_parity():
    # S=K=100, T=1, r=5%, σ=20%: the textbook 10.4506 call, 5.5735 put
    assert round(M.price("CE", 100, 100, 1, 0.2, 0.05), 4) == 10.4506
    assert round(M.price("PE", 100, 100, 1, 0.2, 0.05), 4) == 5.5735
    import math
    for s in (90, 100, 115):
        c, p = M.price("CE", s, 100, 0.3, 0.25), M.price("PE", s, 100, 0.3, 0.25)
        assert abs((c - p) - (s - 100 * math.exp(-M.RATE * 0.3))) < 1e-9, "put-call parity"
    assert M.price("CE", 120, 100, 0, 0.2) == 20 and M.price("PE", 120, 100, 0, 0.2) == 0, "at expiry: what it's in the money"


def test_implied_volatility_round_trips():
    for kind in ("CE", "PE"):
        for vol in (0.08, 0.15, 0.32, 0.9):
            p = M.price(kind, 25000, 25100, 7 / 365, vol)
            assert abs(M.implied_vol(kind, p, 25000, 25100, 7 / 365) - vol) < 1e-4
    assert M.implied_vol("CE", 1.0, 25500, 25000, 7 / 365) is None, "below what it's worth in the money"
    assert M.implied_vol("CE", 0, 25000, 25000, 0.1) is None


def test_years_to_expiry_ends_at_1530_ist():
    at = datetime(2026, 10, 27, 15, 30, tzinfo=IST)
    assert abs(M.years_to(date(2026, 10, 28), at) - 1 / 365) < 1e-9
    assert M.years_to(date(2026, 10, 28), at + timedelta(days=2)) == M.MIN_T


def test_attribution_adds_up_and_points_the_right_way():
    exp = date(2026, 10, 28)
    t0, t1 = datetime(2026, 10, 21, 10, 0, tzinfo=IST), datetime(2026, 10, 21, 15, 0, tzinfo=IST)
    s0, k, iv0 = 25000.0, 25000.0, 0.14
    p0 = M.price("CE", s0, k, M.years_to(exp, t0), iv0)
    # a quiet afternoon: the index unchanged, volatility unchanged — a seller earns only time decay
    p1 = M.price("CE", s0, k, M.years_to(exp, t1), iv0)
    a = M.attribute("CE", k, exp, t0, s0, p0, t1, s0, p1, 75, sold=True)
    assert a["time"] > 0 and abs(a["move"]) < 0.01 and abs(a["vol"]) < 0.05
    assert abs(a["time"] + a["move"] + a["vol"] - a["total"]) < 0.02 and round(a["iv0"]) == 14
    # the index rallies 1% into a sold call: the move costs, and a VIX jump costs more
    p1 = M.price("CE", 25250, k, M.years_to(exp, t1), 0.17)
    a = M.attribute("CE", k, exp, t0, s0, p0, t1, 25250, p1, 75, sold=True)
    assert a["move"] < 0 and a["vol"] < 0 and a["time"] > 0
    assert abs(a["time"] + a["move"] + a["vol"] - a["total"]) < 0.02
    # the same trade bought: every part flips
    b = M.attribute("CE", k, exp, t0, s0, p0, t1, 25250, p1, 75, sold=False)
    assert all(abs(a[x] + b[x]) < 0.02 for x in ("time", "move", "vol", "total"))
    assert M.attribute("CE", k, exp, t0, s0, 0.0, t1, s0, 1.0, 75, sold=True) is None


def test_vega_matches_a_one_point_bump():
    for kind in ("CE", "PE"):
        s, k, t, v = 25000.0, 25200.0, 9 / 365, 0.14
        bump = M.price(kind, s, k, t, v + 0.01) - M.price(kind, s, k, t, v)
        assert abs(M.vega(s, k, t, v) / 100 - bump) < 0.05, "vega ÷ 100 ≈ the price change for +1 point"
    assert M.vega(25000, 25000, 0, 0.14) == 0, "expired: no vega"


def test_black76_prices_off_the_future_and_inverts():
    from app.geek.optionmath import fwd_price, fwd_implied_vol, fwd_greeks, price
    t, f, k = 30 / 365, 25100.0, 25000.0
    c, p = fwd_price("CE", f, k, t, 0.15), fwd_price("PE", f, k, t, 0.15)
    import math
    assert c - p == pytest.approx(math.exp(-0.065 * t) * (f - k), abs=1e-6)     # put-call parity on the forward
    assert fwd_implied_vol("CE", c, f, k, t) == pytest.approx(0.15, abs=1e-4)
    # the same option from the spot whose forward is f
    s = f * math.exp(-0.065 * t)
    assert price("CE", s, k, t, 0.15) == pytest.approx(c, abs=1e-6)
    dc, gc = fwd_greeks("CE", f, k, t, 0.15)
    dp, gp = fwd_greeks("PE", f, k, t, 0.15)
    assert 0 < dc < 1 and -1 < dp < 0 and dc - dp == pytest.approx(math.exp(-0.065 * t), abs=1e-9)
    assert gc == pytest.approx(gp) and gc > 0
    # a numeric check of delta
    h = 0.5
    assert dc == pytest.approx((fwd_price("CE", f + h, k, t, 0.15) - fwd_price("CE", f - h, k, t, 0.15)) / (2 * h), abs=1e-4)
    assert fwd_greeks("CE", 25100, 25000, 0, 0.15) == (1.0, 0.0)


def test_the_quick_solver_reprices_every_option_to_its_premium():
    """quick_implied_vol (the replay solves every leg at every step):
    the volatility it finds prices the option back to its premium, spot or
    future, across strikes, expiries and volatilities. Deep in the money
    the volatility itself is barely defined, so the price is what's held."""
    import random
    from app.geek.optionmath import fwd_price, price, quick_implied_vol
    rnd = random.Random(1)
    n = 0
    for _ in range(1500):
        s, k = 25000.0, float(rnd.choice(range(22000, 28000, 50)))
        t, vol, kind = rnd.uniform(1 / 365 / 24, 40 / 365), rnd.uniform(.06, .6), rnd.choice(["CE", "PE"])
        for fwd, f in ((False, price), (True, fwd_price)):
            p = f(kind, s, k, t, vol)
            if p < .05:
                continue
            v = quick_implied_vol(kind, p, s, k, t, fwd)
            assert v is not None
            assert abs(f(kind, s, k, t, v) - p) < .01, (kind, k, t, vol)
            n += 1
    assert n > 2000
    assert quick_implied_vol("CE", 0, 25000, 25200, .02) is None
    assert quick_implied_vol("CE", 10, 25000, 24000, .02) is None, "below what it's worth in the money"
