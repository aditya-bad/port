"""How forecasts are scored.

  qlike     y/f - ln(y/f) - 1 on variances: 0 when the forecast is right,
            and it punishes forecasting too little (a big move missed)
            more than too much. The usual loss for volatility forecasts.
  rmse      root mean squared error of the annualized volatility (in VIX
            points), the easiest to read
  dm        Diebold-Mariano: is one model's QLIKE really lower than
            another's, or noise? Newey-West variance, h-1 lags (the
            outcomes of overlapping horizons are correlated). A two-sided
            p-value; under 0.05, the difference is real.
  coverage  how often the outcome fell inside a model's 10-90% band
            (80% if the band is right)
"""

from __future__ import annotations

import math

import numpy as np


def qlike(y, f) -> np.ndarray:
    r = np.asarray(y) / np.asarray(f)
    return r - np.log(r) - 1


def rmse(a, b) -> float:
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


def newey_west_var(d: np.ndarray, lags: int) -> float:
    """Long-run variance of `d`'s mean (Bartlett weights)."""
    d = d - d.mean()
    n = len(d)
    v = float(d @ d) / n
    for k in range(1, lags + 1):
        w = 1 - k / (lags + 1)
        v += 2 * w * float(d[k:] @ d[:-k]) / n
    return v / n


def dm_test(loss_a, loss_b, h: int) -> tuple[float, float]:
    """(statistic, two-sided p) for mean(loss_a - loss_b) = 0; a negative
    statistic means a's losses are lower."""
    d = np.asarray(loss_a) - np.asarray(loss_b)
    v = newey_west_var(d, max(0, h - 1))
    if v <= 0:
        return 0.0, 1.0
    stat = float(d.mean() / math.sqrt(v))
    p = math.erfc(abs(stat) / math.sqrt(2))
    return stat, p


def coverage(y, lo, hi) -> float:
    y, lo, hi = map(np.asarray, (y, lo, hi))
    return float(np.mean((y >= lo) & (y <= hi)))
