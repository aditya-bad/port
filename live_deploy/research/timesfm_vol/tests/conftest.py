import gzip
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def synthetic_export(path, days=500, seed=7, intraday=True):
    """An export like custom_scripts/export_vol_history.py's: three indices
    whose daily variance clusters (a GARCH-like process), 75 five-minute
    candles a day, and an India VIX that tracks the variance with noise."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=days)
    rows = []
    base_v = np.empty(days)
    v = 1e-4
    for t in range(days):
        v = 2e-6 + 0.90 * v + 0.08 * v * rng.chisquare(1)
        base_v[t] = v
    for k, sym in enumerate(("NIFTY 50", "NIFTY BANK", "SENSEX")):
        price = 20000.0 * (1 + k)
        for t, d in enumerate(dates):
            var = base_v[t] * (1 + 0.3 * k)
            gap = rng.normal(0, np.sqrt(var * 0.2))
            o = price * np.exp(gap)
            steps = rng.normal(0, np.sqrt(var * 0.8 / 75), 75)
            walk = o * np.exp(np.cumsum(steps))
            hi, lo = max(o, walk.max()), min(o, walk.min())
            rows.append([sym, "day", d.strftime("%Y-%m-%dT00:00:00+05:30"), o, hi, lo, walk[-1]])
            if intraday:
                prev = o
                for j, c in enumerate(walk):
                    ts = (d + pd.Timedelta(hours=9, minutes=15 + 5 * j)).strftime("%Y-%m-%dT%H:%M:%S+05:30")
                    rows.append([sym, "5minute", ts, prev, max(prev, c), min(prev, c), c])
                    prev = c
            price = walk[-1]
    vix = 100 * np.sqrt(252 * base_v) * np.exp(rng.normal(0.1, 0.1, days))
    for t, d in enumerate(dates):
        rows.append(["INDIA VIX", "day", d.strftime("%Y-%m-%dT00:00:00+05:30"), vix[t], vix[t], vix[t], vix[t]])
    df = pd.DataFrame(rows, columns=["symbol", "interval", "ts", "open", "high", "low", "close"])
    with gzip.open(path, "wt") as f:
        df.to_csv(f, index=False)
    return path


@pytest.fixture(scope="session")
def export_file(tmp_path_factory):
    return str(synthetic_export(str(tmp_path_factory.mktemp("data") / "vol_history.csv.gz")))
