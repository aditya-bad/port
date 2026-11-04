"""The data and what's being forecast.

Each trading day's variance (the square of its move, in log terms):
    overnight   ln(open / previous close)^2, the gap a strangle held
                overnight takes
  + intraday    the sum of squared 5-minute log returns from the open
                (realized variance), or, on a day with too few 5-minute
                candles, the Garman-Klass estimate from the day's open,
                high, low and close
What's forecast at day t, for a horizon of h trading days: the mean daily
variance over days t+1 .. t+h. As a volatility, annualized like VIX:
sqrt(252 * that mean).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

YEAR = 252
INDICES = ("NIFTY 50", "NIFTY BANK", "SENSEX")
VIX = "INDIA VIX"
MIN_BARS = 50   # a full session has 75 five-minute candles


def load(path: str) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """The export (custom_scripts/export_vol_history.py) as
    ({symbol: daily candles}, {symbol: 5-minute candles}), each indexed by
    its time, oldest first, open/high/low/close as floats."""
    df = pd.read_csv(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Kolkata")
    daily, intraday = {}, {}
    for (sym, interval), g in df.groupby(["symbol", "interval"]):
        g = g.sort_values("ts").drop_duplicates("ts").set_index("ts")[["open", "high", "low", "close"]].astype(float)
        if interval == "day":
            g.index = g.index.normalize().tz_localize(None)
            daily[sym] = g
        else:
            intraday[sym] = g
    return daily, intraday


def garman_klass(o, h, l, c):
    """Garman-Klass variance of one day from its open, high, low, close."""
    return 0.5 * np.log(h / l) ** 2 - (2 * math.log(2) - 1) * np.log(c / o) ** 2


def daily_variance(daily: pd.DataFrame, intraday: pd.DataFrame | None = None) -> pd.Series:
    """Each day's variance: overnight gap squared plus the day's own
    (5-minute realized where there are enough candles, else
    Garman-Klass). The first day has no overnight gap and is dropped."""
    d = daily.copy()
    overnight = np.log(d["open"] / d["close"].shift(1)) ** 2
    day = garman_klass(d["open"], d["high"], d["low"], d["close"]).clip(lower=0)
    if intraday is not None and len(intraday):
        bars = intraday.copy()
        bars["day"] = bars.index.normalize().tz_localize(None)
        # each bar's move from the one before it in the same day; the
        # day's first bar from its own open
        prev = bars.groupby("day")["close"].shift(1)
        start = prev.isna()
        prev[start] = bars.loc[start, "open"]
        r2 = np.log(bars["close"] / prev) ** 2
        rv = r2.groupby(bars["day"]).sum()
        n = r2.groupby(bars["day"]).size()
        rv = rv[n >= MIN_BARS]
        day = day.where(~day.index.isin(rv.index), rv.reindex(day.index))
    out = (overnight + day).iloc[1:]
    return out[out > 0].rename("var")


def vol_of(mean_var):
    """Annualized volatility (as VIX quotes it, in %) of a mean daily variance."""
    return np.sqrt(YEAR * np.asarray(mean_var)) * 100


def var_of_vix(vix_close):
    """The daily variance a VIX level implies (VIX: annualized %, 30 days)."""
    return (np.asarray(vix_close) / 100) ** 2 / YEAR


def targets(var: pd.Series, h: int) -> pd.Series:
    """At each day t: the mean daily variance over t+1 .. t+h (NaN where
    the days after t run out)."""
    v = var.to_numpy()
    out = np.full(len(v), np.nan)
    c = np.concatenate([[0.0], np.cumsum(v)])
    for i in range(len(v) - h):
        out[i] = (c[i + 1 + h] - c[i + 1]) / h
    return pd.Series(out, index=var.index, name=f"y{h}")


def test_days(var: pd.Series, years: float, h_max: int, warmup: int = YEAR) -> list[int]:
    """Positions of the days forecast from: the last `years` of the data
    whose h_max days after are known, each with at least `warmup` days
    of history behind it."""
    last = len(var) - h_max - 1
    first = max(warmup, last - int(round(years * YEAR)) + 1)
    return list(range(first, last + 1))
