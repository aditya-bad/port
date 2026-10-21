"""Geek's Volatility view, its arithmetic (app/geek/volatility.py)."""

from datetime import date, timedelta

from app.geek import volatility as V


def test_bands_and_change():
    assert [V.bucket(v, V.BANDS) for v in (11.9, 12, 17.99, 22, 30)] == ["Under 12", "12–15", "15–18", "Over 22", "Over 22"]
    assert [V.bucket(v, V.CHANGE) for v in (-1.5, 0, 1, 2)] == ["Fell over 1 point", "Within a point", "Rose over 1 point", "Rose over 1 point"]
    assert V.bucket(None, V.BANDS) is None


def test_rupees_per_vix_point_is_a_least_squares_slope():
    # a seller losing ₹2,000 per point VIX rises, plus a little noise
    xs = [-2, -1, 0, 1, 2, 0.5, -0.5, 1.5, -1.5, 3]
    ys = [-2000 * x + (50 if i % 2 else -50) for i, x in enumerate(xs)]
    r = V.ols(xs, ys)
    assert -2050 < r["slope"] < -1950 and r["r"] < -0.99 and r["n"] == 10
    assert V.ols([1, 1, 1], [1, 2, 3]) is None, "VIX never changed: no slope"
    assert V.ols([1, 2], [1, 2]) is None


def test_days_expected_vs_actual_move_and_pnl():
    mon = date(2026, 10, 12)
    vix = {mon: (14, 15, 13.5, 14.6), mon + timedelta(days=1): (14.6, 17, 14.5, 16.6)}
    idx = {mon - timedelta(days=3): (25000, 25100, 24900, 25000), mon: (25000, 25300, 24950, 25250), mon + timedelta(days=1): (25250, 25260, 24600, 24700)}
    eq = {mon - timedelta(days=3): 500000, mon: 501500, mon + timedelta(days=1): 497000}
    days = V.daily(vix, idx, eq)
    fri, d1, d2 = days
    assert fri["vix_close"] is None and fri["pnl"] is None
    assert d1["dvix"] is None and d1["implied_pct"] is None, "no VIX the day before: nothing priced"
    assert (d1["move_pct"], d1["pnl"]) == (1.0, 1500.0)
    # Tuesday: VIX 14.6 priced ±0.764%; the index fell 2.18% — 2.85× what was priced
    assert d2["dvix"] == 2.0 and d2["implied_pct"] == 0.764 and d2["move_pct"] == 2.178 and d2["ratio"] == 2.85
    assert d2["pnl"] == -4500.0


def test_weeks():
    mon = date(2026, 10, 12)
    days = [
        {"day": (mon - timedelta(days=3)).isoformat(), "vix_open": 13, "vix_close": 13.5, "vix_high": 14, "index_close": 25000, "pnl": 100},
        {"day": mon.isoformat(), "vix_open": 13.5, "vix_close": 14.6, "vix_high": 15, "index_close": 25250, "pnl": 1500},
        {"day": (mon + timedelta(days=1)).isoformat(), "vix_open": 14.6, "vix_close": 16.6, "vix_high": 17, "index_close": 24700, "pnl": -4500},
    ]
    trades = [{"pnl": -3000, "end": "2026-10-13T14:00:00+05:30"}, {"pnl": 900, "end": "2026-10-12T14:00:00+05:30"}, {"pnl": None, "end": None}]
    w0, w1 = V.weekly(days, trades)
    assert w1 == {"week": "2026-10-12", "vix_open": 13.5, "vix_close": 16.6, "vix_high": 17, "dvix": 3.1,
                  "index_move_pct": -1.2, "pnl": -3000.0, "trades": 2, "wins": 1}
    assert w0["index_move_pct"] is None, "no earlier week to measure from"


def test_sold_or_bought_by_net_premium():
    f = lambda a, px, o=True, q=75: {"action": a, "price": px, "qty": q, "opening": o}  # noqa: E731
    assert V.side_of([f("sell", 100), f("sell", 90), f("buy", 300, o=False)]) == "sold", "a closing buy doesn't count"
    assert V.side_of([f("buy", 120)]) == "bought"
    assert V.side_of([f("sell", 100), f("sell", 95), f("buy", 12), f("buy", 10)]) == "sold", "a hedged strangle: a seller"
    assert V.side_of([f("sell", 80), f("sell", 78), f("buy", 120), f("buy", 118)]) == "bought", "a calendar: pays more for next week"
    assert V.side_of([f("sell", 50), f("buy", 50)]) == "both"


def _r(vix, pnl):
    return {"vix_in": vix, "pnl": pnl}


def test_what_if_entry_filters():
    rows = [_r(11.2, -900), _r(12.5, -400), _r(13.1, 300), _r(15.4, 800), _r(16.2, 1200), _r(18.9, -200), _r(None, 5000), _r(14, None)]
    f = {x["level"]: x for x in V.filters(rows)}
    assert sorted(f) == [12, 13, 14, 15, 16, 17, 18], "every whole level inside the range traded"
    assert (f[13]["above_n"], f[13]["above_pnl"], f[13]["below_n"], f[13]["below_pnl"]) == (4, 2100.0, 2, -1300.0)
    assert f[13]["above_win"] == 75.0 and f[13]["below_win"] == 0.0
    assert V.filters(rows[:4]) == [], "too few trades to say"


def test_at_this_level():
    rows = [_r(14.2, 500), _r(15.6, -300), _r(13.9, 200), _r(15.4, -400), _r(18, 900)]
    h = V.at_this_level(rows, 14.5)   # within a point: 14.2, 13.9, 15.4 (15.6 is 1.1 away)
    assert (h["n"], h["pnl"], h["avg"], h["win_rate"]) == (3, 300.0, 100.0, 66.7) and (h["low"], h["high"]) == (13.5, 15.5)
    assert V.at_this_level(rows, 25)["n"] == 0 and V.at_this_level(rows, None) is None


def test_spike_days():
    d = lambda c, p: {"vix_close": c, "pnl": p}  # noqa: E731
    s = V.spikes([d(14, None), d(14.2, 300), d(16, -2500), d(15.5, 400), d(17.2, -1800), d(17, 200)])
    assert (s["spike_n"], s["spike_avg"], s["spike_worst"]) == (2, -2150.0, -2500.0)
    assert (s["rest_n"], s["rest_avg"]) == (3, 300.0)
    assert V.spikes([d(14, 1), d(14.1, 2)]) is None
