"""Tools › Compare with underlying (app/v2/track.py): the figures, worked by hand."""

import math

import pytest

from app.v2 import track


def c(t, close, v=0):
    return {"t": t, "c": close, "v": v}


def test_one_scale_the_gap_and_the_figures():
    base = [c(1, 100), c(2, 110), c(3, 105), c(4, 99)]            # 3: a candle the ETF doesn't have
    etf = [c(1, 50, 1000), c(2, 55.5, 2000), c(4, 49.5, 0)]
    out = track.compare(base, [(7, etf)], "day")
    assert out["t"] == [1, 2, 4] and out["dropped"] == 1
    assert out["base"]["pct"] == [0, 10, -1]
    [o] = out["others"]
    assert o["pct"] == [0, 11, -1] and o["gap"] == [0, 1, 0]
    s = o["stats"]
    assert s["ret"] == -1 and s["base_ret"] == -1 and s["diff"] == 0
    # candle returns: base +10%, −10%; ETF +11%, −10.8108%: gaps of +1 and −0.8108 points
    d = [1.0, (49.5 / 55.5 - 1 - (-0.1)) * 100]
    te = abs(d[0] - d[1]) / math.sqrt(2)
    assert s["te"] == pytest.approx(te, abs=1e-4) and s["te_year"] == pytest.approx(te * math.sqrt(252), abs=1e-3)
    assert s["max_gap"] == 1 and s["max_gap_at"] == 2
    assert s["corr"] == pytest.approx(1.0) and s["beta"] == pytest.approx((0.11 - (49.5 / 55.5 - 1)) / 0.2, abs=1e-4)
    assert s["traded"] == pytest.approx((50 * 1000 + 55.5 * 2000) / 2), "candles with volume only"


def test_an_exact_copy_tracks_perfectly_and_intraday_has_no_yearly_figure():
    base = [c(i, 100 + i * 3 - (i % 2) * 5) for i in range(10)]
    out = track.compare(base, [(1, [c(x["t"], x["c"] / 100) for x in base])], "5minute")
    s = out["others"][0]["stats"]
    assert s["diff"] == 0 and s["te"] == 0 and s["max_gap"] == 0 and s["te_year"] is None
    assert s["corr"] == pytest.approx(1) and s["beta"] == pytest.approx(1) and s["traded"] is None


def test_nothing_in_common_says_so():
    out = track.compare([c(1, 100), c(2, 101)], [(1, [c(5, 1), c(6, 2)])])
    assert out["others"] == [] and "Not enough candles" in out["error"]
    assert "has none" in track.compare([], [(1, [c(5, 1)])])["error"]
