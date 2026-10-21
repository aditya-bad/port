"""Black-Scholes for Geek mode (app/geek/optionmath.py)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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
