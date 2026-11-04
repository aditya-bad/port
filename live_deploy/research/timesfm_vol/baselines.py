"""The baselines TimesFM has to beat. Each gives, at day position i, a
forecast of the mean daily variance over the next h days, from the days
up to and including i only.

  last20  the last 20 days' mean ("the next weeks look like the last few")
  ewma    RiskMetrics' exponentially weighted variance, lambda 0.94
  garch   GARCH(1,1) on close-to-close returns, refitted every 5 days
  har     HAR: next days on yesterday, the last week and the last month,
          by least squares over every day whose outcome was known at i
  vix     the variance India VIX implies (the market's own forecast; for
          NIFTY BANK and SENSEX it stands in, as India VIX is NIFTY's)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from vol import var_of_vix

EWMA_LAMBDA = 0.94
GARCH_REFIT = 5


def last20(var: np.ndarray, days: list[int], h: int) -> np.ndarray:
    c = np.concatenate([[0.0], np.cumsum(var)])
    return np.array([(c[i + 1] - c[max(0, i - 19)]) / (i + 1 - max(0, i - 19)) for i in days])


def ewma(var: np.ndarray, days: list[int], h: int, lam: float = EWMA_LAMBDA) -> np.ndarray:
    s = np.empty(len(var))
    s[0] = var[: min(20, len(var))].mean()
    for t in range(1, len(var)):
        s[t] = lam * s[t - 1] + (1 - lam) * var[t]
    return s[days]


def har_features(var: np.ndarray) -> np.ndarray:
    """[1, day, week (5), month (22)] at each day; NaN for the first 21."""
    s = pd.Series(var)
    return np.column_stack([np.ones(len(var)), var, s.rolling(5).mean().to_numpy(), s.rolling(22).mean().to_numpy()])


def har(var: np.ndarray, days: list[int], h: int) -> np.ndarray:
    X = har_features(var)
    c = np.concatenate([[0.0], np.cumsum(var)])
    y = np.full(len(var), np.nan)
    for j in range(len(var) - h):
        y[j] = (c[j + 1 + h] - c[j + 1]) / h
    out = []
    for i in days:
        # a row j is usable at i only once its outcome (days j+1 .. j+h) is in
        rows = np.arange(21, i - h + 1)
        if len(rows) < 30:
            out.append(np.nan)
            continue
        beta, *_ = np.linalg.lstsq(X[rows], y[rows], rcond=None)
        out.append(max(float(X[i] @ beta), 1e-10))
    return np.array(out)


def garch(close: np.ndarray, days: list[int], h: int, refit: int = GARCH_REFIT) -> np.ndarray:
    """`close`: the closes the variance series came from, aligned to it
    with one extra close at the start (position i of the variance is the
    move from close[i] to close[i+1], as a log return)."""
    from arch import arch_model
    r = np.diff(np.log(close)) * 100   # percent: arch fits better on that scale
    out, params = [], None
    for k, i in enumerate(days):
        am = arch_model(r[: i + 1], mean="Zero", vol="GARCH", p=1, q=1, dist="normal", rescale=False)
        if params is None or k % refit == 0:
            params = am.fit(disp="off", show_warning=False).params
        f = am.fix(params).forecast(horizon=h, reindex=False)
        out.append(float(f.variance.values[-1].mean()) / 1e4)
    return np.array(out)


def vix(vix_close: np.ndarray, days: list[int], h: int) -> np.ndarray:
    return var_of_vix(vix_close[days])


def all_baselines(var: pd.Series, close: pd.Series, vix_close: pd.Series | None, days: list[int], h: int) -> dict[str, np.ndarray]:
    """Every baseline's forecasts at `days` (positions in `var`). `close`:
    the daily closes over the same days as `var`, plus the one before;
    `vix_close`: India VIX's close on each day of `var` (or None)."""
    v = var.to_numpy()
    out = {"last20": last20(v, days, h), "ewma": ewma(v, days, h), "har": har(v, days, h),
           "garch": garch(close.to_numpy(), days, h)}
    if vix_close is not None:
        out["vix"] = vix(vix_close.to_numpy(), days, h)
    return out
