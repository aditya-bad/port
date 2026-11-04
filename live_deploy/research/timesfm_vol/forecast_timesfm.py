"""TimesFM's forecasts, on their own: run this where the model's weights
can be had (Hugging Face, or a folder you downloaded them to), and give
its CSV to run.py.

    pip install -r requirements-timesfm.txt
    python forecast_timesfm.py vol_history.csv.gz --out forecasts_timesfm.csv
    python forecast_timesfm.py vol_history.csv.gz --models tfm3 --weights3 /path/to/timesfm-3.0-pytorch

What it's given: each index's daily log variance (vol.daily_variance),
everything up to the forecast day, nothing after. What it gives back,
for every test day and horizon (5 and 20 trading days): the forecast
mean daily variance, and its 10% and 90% band.

  tfm3       TimesFM 3.0 (google/timesfm-3.0-pytorch: its licence is
             non-commercial, non-production)
  tfm3_vix   the same, with India VIX's log variance as a past covariate
  tfm25      TimesFM 2.5 (google/timesfm-2.5-200m-pytorch: Apache-2.0)

From log variance back to variance: the median path's exp() is a median,
lower than the mean of a skewed variance, so each day's is lifted by
exp(s^2 / 2), s taken from the day's own 10-90% band (a normal's is
2.563 s wide). The band is each quantile path's own exp().
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import vol

HORIZONS = (5, 20)
Z90 = 1.2815515655446004   # the 90% point of a standard normal
MAX_CONTEXT = 2048


def to_rows(symbol: str, model: str, dates, days: list[int], q: list[np.ndarray]) -> list[dict]:
    """Forecast rows from each test day's quantile paths. `q[k]`: an
    array (horizon, 3) of the 10%, 50% and 90% log variance, day by day."""
    rows = []
    for i, path in zip(days, q):
        lo, mid, hi = path[:, 0], path[:, 1], path[:, 2]
        s = np.clip((hi - lo) / (2 * Z90), 0, None)
        mean = np.exp(mid + s ** 2 / 2)
        for h in HORIZONS:
            rows.append({"symbol": symbol, "date": dates[i].date().isoformat(), "model": model, "h": h,
                         "var": float(mean[:h].mean()), "lo": float(np.exp(lo[:h]).mean()),
                         "hi": float(np.exp(hi[:h]).mean())})
    return rows


def pick3(quantiles: np.ndarray) -> np.ndarray:
    """(horizon, 3): the 10%, 50% and 90% columns of a model's quantile
    output: 3.0 gives the nine levels 0.1 .. 0.9; 2.5 gives the mean and
    then those nine."""
    q = np.asarray(quantiles)
    if q.shape[-1] == 10:
        q = q[..., 1:]
    if q.shape[-1] != 9:
        raise ValueError(f"expected 9 quantile levels (0.1 .. 0.9), got {q.shape[-1]}")
    return q[..., [0, 4, 8]]


def run_tfm3(series: dict, days: dict, weights: str, with_vix: bool, vix_log: dict | None, batch: int) -> dict:
    import timesfm
    m = timesfm.TimesFM3Forecaster.from_pretrained(weights, device=_device())
    out = {}
    for sym, logv in series.items():
        ctx = [logv[max(0, i + 1 - MAX_CONTEXT): i + 1] for i in days[sym]]
        cov = [vix_log[sym][max(0, i + 1 - MAX_CONTEXT): i + 1] for i in days[sym]] if with_vix else None
        got = []
        for a in range(0, len(ctx), batch):
            res = m.predict_batch(contexts=ctx[a:a + batch], horizon=max(HORIZONS), return_quantiles=True,
                                  past_only_covariates=cov[a:a + batch] if cov else None)
            got += [pick3(r.quantiles) for r in res]
            print(f"  {sym}: {min(a + batch, len(ctx))}/{len(ctx)}", file=sys.stderr, flush=True)
        out[sym] = got
    return out


def run_tfm25(series: dict, days: dict, weights: str, batch: int) -> dict:
    import timesfm
    m = timesfm.TimesFM_2p5_200M_torch.from_pretrained(weights)
    m.compile(timesfm.ForecastConfig(max_context=MAX_CONTEXT, max_horizon=max(HORIZONS) + 12,
                                     normalize_inputs=True, use_continuous_quantile_head=True,
                                     fix_quantile_crossing=True, infer_is_positive=False,
                                     per_core_batch_size=batch))
    out = {}
    for sym, logv in series.items():
        ctx = [logv[: i + 1] for i in days[sym]]
        _point, q = m.forecast(horizon=max(HORIZONS), inputs=ctx)
        out[sym] = [pick3(q[k]) for k in range(len(ctx))]
        print(f"  {sym}: {len(ctx)} forecasts", file=sys.stderr, flush=True)
    return out


def _device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", help="the export: vol_history.csv.gz")
    ap.add_argument("--out", default="forecasts_timesfm.csv")
    ap.add_argument("--models", default="tfm3,tfm3_vix,tfm25", help="comma-separated: tfm3, tfm3_vix, tfm25")
    ap.add_argument("--test-years", type=float, default=2.0)
    ap.add_argument("--weights3", default="google/timesfm-3.0-pytorch", help="Hugging Face id or a local folder")
    ap.add_argument("--weights25", default="google/timesfm-2.5-200m-pytorch", help="Hugging Face id or a local folder")
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args(argv)

    daily, intraday = vol.load(args.data)
    series, days, dates, vix_log = {}, {}, {}, {}
    vixd = daily.get(vol.VIX)
    for sym in vol.INDICES:
        if sym not in daily:
            continue
        var = vol.daily_variance(daily[sym], intraday.get(sym))
        series[sym] = np.log(var.to_numpy())
        days[sym] = vol.test_days(var, args.test_years, max(HORIZONS))
        dates[sym] = var.index
        if vixd is not None:
            vix_log[sym] = np.log(vol.var_of_vix(vixd["close"].reindex(var.index).ffill().bfill().to_numpy()))

    rows = []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"{model}…", file=sys.stderr, flush=True)
        if model in ("tfm3", "tfm3_vix"):
            if model == "tfm3_vix" and not vix_log:
                print("  no INDIA VIX in the data: skipped", file=sys.stderr)
                continue
            got = run_tfm3(series, days, args.weights3, model == "tfm3_vix", vix_log, args.batch)
        elif model == "tfm25":
            got = run_tfm25(series, days, args.weights25, args.batch)
        else:
            raise SystemExit(f"unknown model {model!r}")
        for sym, q in got.items():
            rows += to_rows(sym, model, dates[sym], days[sym], q)
    pd.DataFrame(rows).to_csv(args.out, index=False)
    print(f"{len(rows)} forecasts → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
