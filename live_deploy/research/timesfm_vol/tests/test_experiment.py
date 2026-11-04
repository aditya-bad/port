import numpy as np
import pandas as pd
import pytest

import baselines
import forecast_timesfm as tf
import run
import score
import vol


def test_a_days_variance_is_its_overnight_gap_and_its_5_minute_moves():
    d = pd.DataFrame({"open": [100.0, 102.0], "high": [101.0, 104.0], "low": [99.0, 101.0], "close": [100.0, 103.0]},
                     index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    ts = pd.date_range("2024-01-02 09:15", periods=60, freq="5min", tz="Asia/Kolkata")
    closes = 102.0 * np.exp(np.linspace(0, np.log(103 / 102), 60))
    bars = pd.DataFrame({"open": np.r_[102.0, closes[:-1]], "high": closes, "low": closes, "close": closes}, index=ts)
    v = vol.daily_variance(d, bars)
    moves = np.diff(np.log(np.r_[102.0, closes]))
    assert v.iloc[0] == pytest.approx(np.log(102 / 100) ** 2 + (moves ** 2).sum())


def test_a_day_with_too_few_5_minute_candles_uses_its_high_and_low():
    d = pd.DataFrame({"open": [100.0, 102.0], "high": [101.0, 104.0], "low": [99.0, 101.0], "close": [100.0, 103.0]},
                     index=pd.to_datetime(["2024-01-01", "2024-01-02"]))
    ts = pd.date_range("2024-01-02 09:15", periods=10, freq="5min", tz="Asia/Kolkata")
    bars = pd.DataFrame({"open": 102.0, "high": 102.5, "low": 101.5, "close": 102.2}, index=ts)
    v = vol.daily_variance(d, bars)
    assert v.iloc[0] == pytest.approx(np.log(102 / 100) ** 2 + float(vol.garman_klass(102, 104, 101, 103)))


def test_the_target_is_the_mean_variance_of_the_days_after_and_none_at_the_end():
    var = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    y = vol.targets(var, 2)
    assert y.iloc[0] == 2.5 and y.iloc[2] == 4.5 and y.iloc[3:].isna().all()


@pytest.mark.parametrize("name", ["last20", "ewma", "har", "garch"])
def test_no_baseline_looks_past_the_day_it_forecasts_from(name):
    rng = np.random.default_rng(1)
    var = np.abs(rng.normal(1e-4, 4e-5, 400))
    close = 100 * np.exp(np.r_[0, np.cumsum(rng.normal(0, 0.01, 400))])
    days = [300, 320]

    def run_on(v, c):
        if name == "garch":
            return baselines.garch(c, days, 5)
        return getattr(baselines, name)(v, days, 5)
    a = run_on(var, close)
    later_v, later_c = var.copy(), close.copy()
    later_v[321:] *= 50
    later_c[322:] *= 1.5
    b = run_on(later_v, later_c)
    assert np.allclose(a, b), "changing the days after the last forecast changed a forecast"


def test_har_recovers_a_persistent_series_better_than_its_last_value():
    rng = np.random.default_rng(3)
    v = np.empty(800)
    v[0] = 1e-4
    for t in range(1, 800):
        v[t] = 1e-6 + 0.95 * v[t - 1] + 3e-6 * rng.standard_normal() ** 2
    days = list(range(500, 780))
    y = vol.targets(pd.Series(v), 5).to_numpy()[days]
    assert score.qlike(y, baselines.har(v, days, 5)).mean() < score.qlike(y, v[days]).mean()


def test_qlike_is_zero_when_right_and_punishes_too_little_more():
    assert score.qlike(2.0, 2.0) == pytest.approx(0)
    assert score.qlike(2.0, 1.0) > score.qlike(2.0, 4.0), "missing a doubling costs more than overcalling one"


def test_diebold_mariano_says_which_is_lower_and_when_it_is_noise():
    rng = np.random.default_rng(0)
    a = rng.normal(1.0, 0.1, 500)
    stat, p = score.dm_test(a, a + 0.05, 5)
    assert stat < 0 and p < 0.01
    stat, p = score.dm_test(a, a + rng.normal(0, 0.1, 500), 5)
    assert p > 0.01


def test_coverage_counts_the_outcomes_inside_the_band():
    assert score.coverage([1, 2, 3, 4], [0, 0, 0, 5], [5, 5, 2, 6]) == 0.5


def test_both_timesfm_quantile_layouts_give_the_10_50_and_90_columns():
    q9 = np.tile(np.arange(9.0), (20, 1))             # 3.0: 0.1 .. 0.9
    q10 = np.tile(np.r_[99.0, np.arange(9.0)], (20, 1))   # 2.5: mean, then 0.1 .. 0.9
    assert (tf.pick3(q9)[0] == [0, 4, 8]).all() and (tf.pick3(q10)[0] == [0, 4, 8]).all()
    with pytest.raises(ValueError):
        tf.pick3(np.zeros((20, 7)))


def test_a_log_variance_forecast_becomes_a_mean_variance_lifted_by_its_spread():
    dates = pd.to_datetime(["2024-01-01"] * 2)
    flat = np.column_stack([np.full(20, -9.0)] * 3)                    # no spread: exp() as it is
    wide = np.column_stack([np.full(20, -10.0), np.full(20, -9.0), np.full(20, -8.0)])
    a = tf.to_rows("NIFTY 50", "tfm3", dates, [0], [flat])[0]
    b = tf.to_rows("NIFTY 50", "tfm3", dates, [0], [wide])[0]
    assert a["var"] == pytest.approx(np.exp(-9.0)) and b["var"] > a["var"]
    assert b["lo"] == pytest.approx(np.exp(-10.0)) and b["hi"] == pytest.approx(np.exp(-8.0))


def test_the_whole_run_scores_every_model_on_the_same_days_and_an_oracle_wins(export_file, tmp_path):
    daily, intraday = vol.load(export_file)
    base = run.baseline_forecasts(daily, intraday, 0.5)
    assert set(base["model"]) == {"last20", "ewma", "har", "garch", "vix"}
    # a stand-in for TimesFM that knows the outcome, with a band around it
    oracle = base[base["model"] == "har"][["symbol", "date", "h", "y"]].copy()
    oracle["model"], oracle["var"] = "tfm_oracle", oracle["y"]
    oracle["lo"], oracle["hi"] = oracle["y"] * 0.8, oracle["y"] * 1.2
    fc = pd.concat([base, run.attach_outcomes(oracle.drop(columns="y"), base)], ignore_index=True)
    sc = run.scores(fc)
    for (sym, h), g in sc.groupby(["symbol", "h"]):
        assert g.sort_values("qlike").iloc[0]["model"] == "tfm_oracle", (sym, h)
        assert g["days"].nunique() == 1, "every model on the same days"
    assert (sc[sc["model"] == "tfm_oracle"]["coverage"] == 1.0).all()
    text = run.report(sc)
    assert "## Verdict" in text and "tfm_oracle" in text
    assert "band off" in text, "a band that holds every outcome is too wide, not right"


def test_without_timesfm_the_report_says_what_is_still_to_run(export_file):
    daily, intraday = vol.load(export_file)
    text = run.report(run.scores(run.baseline_forecasts(daily, intraday, 0.5)))
    assert "No TimesFM forecasts yet" in text


class _FakeOut:
    def __init__(self, q):
        self.quantiles = q


def _fake_timesfm(seen):
    """A stand-in `timesfm` module with 3.0's and 2.5's interfaces: each
    forecast is the context's last value, with a band of +-1 (log)."""
    import types
    mod = types.ModuleType("timesfm")

    class TimesFM3Forecaster:
        @classmethod
        def from_pretrained(cls, name, device=None, **kw):
            seen.setdefault("loaded", []).append(name)
            return cls()

        def predict_batch(self, contexts, horizon, return_quantiles=False, past_only_covariates=None, **kw):
            seen.setdefault("batches", []).append(len(contexts))
            seen.setdefault("contexts", []).extend(contexts)
            seen.setdefault("covariates", []).append(past_only_covariates)
            for c in contexts:
                q = np.tile(c[-1] + np.linspace(-1, 1, 9), (horizon, 1))
                yield _FakeOut(q)

    class ForecastConfig:
        def __init__(self, **kw):
            self.kw = kw

    class TimesFM_2p5_200M_torch:
        @classmethod
        def from_pretrained(cls, name):
            seen.setdefault("loaded", []).append(name)
            return cls()

        def compile(self, config):
            pass

        def forecast(self, horizon, inputs):
            q = np.stack([np.tile(np.r_[0.0, c[-1] + np.linspace(-1, 1, 9)], (horizon, 1)) for c in inputs])
            return q[..., 0], q

    mod.TimesFM3Forecaster, mod.ForecastConfig, mod.TimesFM_2p5_200M_torch = TimesFM3Forecaster, ForecastConfig, TimesFM_2p5_200M_torch
    return mod


def test_the_timesfm_step_forecasts_each_day_from_its_own_past_and_feeds_the_scoring(export_file, tmp_path, monkeypatch):
    import sys
    seen = {}
    monkeypatch.setitem(sys.modules, "timesfm", _fake_timesfm(seen))
    out = str(tmp_path / "tfm.csv")
    assert tf.main([export_file, "--out", out, "--test-years", "0.5", "--batch", "16", "--weights3", "/w3"]) == 0
    assert seen["loaded"].count("/w3") == 2, "3.0, with and without VIX, from the folder given"
    assert max(seen["batches"]) <= 16
    daily, intraday = vol.load(export_file)
    logv = np.log(vol.daily_variance(daily["NIFTY 50"], intraday["NIFTY 50"]).to_numpy())
    days = vol.test_days(pd.Series(logv), 0.5, 20)
    first = seen["contexts"][: len(days)]
    assert [len(c) for c in first] == [i + 1 for i in days], "each day's context ends on that day"
    assert all(c[-1] == logv[i] for c, i in zip(first, days))
    assert any(c is not None for c in seen["covariates"]) and any(c is None for c in seen["covariates"])

    got = pd.read_csv(out)
    assert set(got["model"]) == {"tfm3", "tfm3_vix", "tfm25"} and set(got["h"]) == {5, 20}
    base = run.baseline_forecasts(daily, intraday, 0.5)
    sc = run.scores(pd.concat([base, run.attach_outcomes(got, base)], ignore_index=True))
    assert {"tfm3", "tfm3_vix", "tfm25"} <= set(sc["model"])
    assert sc[sc["model"].str.startswith("tfm")]["coverage"].notna().all()
