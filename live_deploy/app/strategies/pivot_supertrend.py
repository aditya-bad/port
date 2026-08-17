"""
live_deploy — Pivot Points + SuperTrend(7,3) live paper-trading strategy.

Ports the exact rules validated in
tg_int_st_pp/strategy_pivot_supertrend.py (pivot formulas, SuperTrend
recursion, next-candle-open execution timing, only-1-position rule) to
live streaming ticks. Re-implemented here rather than imported —
live_deploy stays standalone from the rest of the repo, same as every
other folder in it.

RULES (identical to the backtested version):
  Long entry  — 5-min close above any of R1/R2/R3 AND SuperTrend green.
  Short entry — 5-min close below any of S1/S2/S3 AND SuperTrend red.
  Both entries and exits execute at the NEXT candle's open, never the
  signal candle's own close (can't trade on a price that hasn't printed).
  Exit on SuperTrend flip, OR force-exit at force_exit_time (default
  15:00) if still set — whichever comes first. Only 1 open position at
  a time; a fresh entry can fire immediately after an exit.

  NO entry before `market_open_time` (config, default 09:15) — unlike
  intraday_dtt_simple/calendar_btst/strangle_monthly_v2, this strategy
  has no `entry_time` schedule at all: it watches continuously and
  reacts the instant a signal breaks, any time of day, which used to
  implicitly mean "any time the market is actually open" back when
  pre-market data simply never reached this pipeline. NSE now
  disseminates LIVE pre-open indicative-price ticks through the same
  feed (equity index dissemination during the 09:00-09:15 call auction,
  plus a genuine F&O futures pre-open session since Dec 2025) — without
  this floor, a pivot/trend combination already "ready" from a prior
  day (the normal state of any established deployment) could queue a
  real entry off a pre-market signal, executing the moment regular
  trading begins, priced off auction-based price discovery rather than
  real continuous trading. Only gates fresh signal DETECTION (step 5
  below) — an exit, or a pending entry already queued from a REGULAR-
  session candle, is never blocked by this.

WHAT'S GENUINELY NEW vs. the backtest (live streaming, not a batch file):
  - Ticks arrive one at a time, not as a pre-loaded candle array — see
    CandleAggregator, which buckets ticks into 5-min OHLC candles as
    they happen and returns a candle exactly once, right when it closes.
  - SuperTrend must therefore be computed INCREMENTALLY (one candle at
    a time, carrying forward just the previous state) rather than in
    one batch pass over an array — see SuperTrendState. The math is
    identical to the batch version; this is proven by a test that
    replays the same synthetic candle sequence through both and asserts
    bit-for-bit identical trend/ATR/band output at every step.
  - No pre-loaded history exists at deploy time, so pivots (which need
    the PREVIOUS day's H/L/C) and SuperTrend's warmup (which needs
    `period` candles of ATR history) have nothing to work from on cold
    start — hence the seeding options in config, see below. Pivots are
    then recomputed automatically at every day boundary from whatever
    the strategy has observed live since deployment.

SEEDING (all optional — everything self-warms from live ticks if
omitted, exactly matching the backtest's "day 1 excluded, no trading
until warmup completes" behavior, just expressed live as "no entries
until ready"):

  "prev_day_ohlc": {"high":..., "low":..., "close":...}
      Previous trading day's H/L/C, for correct pivots from minute one.
      Easy to obtain from any chart. If omitted, no valid pivots exist
      until this deployment has itself observed one full trading day.

  "seed_candles": [{"date": "YYYY-MM-DD HH:MM:SS", "open":..., "high":...,
                     "low":..., "close":...}, ...]
      RECOMMENDED, most accurate: recent 5-min OHLC candles (more is
      better — at least 7 for ATR to seed at all, 20-30+ for the
      trend/bands to have "settled" the way they would on a real chart).
      Run through the EXACT SAME algorithm as everything else here — no
      approximation. Also used to derive prev_day_ohlc automatically if
      it wasn't given separately and the candles span the previous day.

  "supertrend_seed": {"trend": "up"|"down", "value": <the ST line value
                       shown on your chart>, "atr": <current ATR(7)>,
                       "as_of_candle": {"date":..., "open":..., "high":...,
                                        "low":..., "close":...}}
      Lighter-weight fallback for when you only have what your chart
      currently shows (the ST line + its color) rather than exported
      candle history. `atr` is required — most charting platforms let
      you add a plain ATR(7) indicator alongside SuperTrend to read it
      off. Only the ACTIVE band (whichever the chart's ST line
      currently represents) is known this way; the other one is
      approximated from `as_of_candle`'s own high/low ± multiplier*ATR
      — a reasonable approximation that a real chart's own ratchet logic
      converges away from within a handful of candles, not an exact
      match forever. Use seed_candles instead if that matters to you.
      NOT valid with atr_smoothing="sma" (SMA needs a rolling window of
      raw TR values, not a single current ATR number) — that
      combination is rejected with a clear warning and falls back to
      cold-start rather than silently producing wrong numbers.

  Neither given -> cold start: no entries until ATR warms up (period
  candles, ~35 min at 5-min bars) AND a full trading day has been
  observed for pivots.

OTHER CONFIG:
  "instrument_tokens": [<single token>] — required, a ONE-ELEMENT list.
      Plural/list to match the same key every other deployment's config
      uses (DeploymentRunner filters the shared tick stream by
      config["instrument_tokens"], and DeploymentManager's dynamic
      dispatcher subscription reads the same key) — this strategy only
      ever trades one instrument, but still reads element [0] from that
      list rather than inventing a separate singular key.
  "symbol": optional, for display/logging only
  "pivot_type": classic (default) | fibonacci | camarilla | woodie
  "atr_smoothing": wilder (default) | sma | ema
  "force_exit_time": "15:00" (default) | null (disable — let SuperTrend
      flips be the ONLY exit trigger, i.e. positions can ride overnight
      — this is what a "positional" deployment would typically set)
  "market_open_time": "09:15" (default) | null (disable — allow entries
      off pre-market ticks too; NOT recommended, see the RULES section's
      "NO entry before market_open_time" paragraph above for why) — the
      earliest time a fresh entry signal is allowed to be DETECTED.
      Doesn't affect exits, or an entry already queued from a candle
      that closed during regular hours.
  "capital_per_trade": null (default — use ALL of the deployment's
      current cash on each entry, sized as floor(cash / price)) | a
      fixed rupee amount to cap each entry's size instead

Position sizing is a genuinely NEW dimension not present in the
backtest at all — the original tg_int_st_pp version reported raw index
points per trade with no capital model, since the NIFTY 50 index isn't
itself a tradeable instrument. This paper-trading engine tracks real
cash, so entries are sized in whole "units" of the index's price as if
it were directly tradeable, using available capital — a deliberate
simplification consistent with how the strategy was originally
backtested (point-based, index-referenced), not a claim that you can
literally buy the index. No averaging, no multi-lot sizing — this
strategy's backtest never did that; it's always exactly one lot in, one
lot out.
"""

import logging
from collections import deque
from datetime import date, datetime, time as dtime
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from .registry import register_strategy
from .trade_meta import build_trade_meta

logger = logging.getLogger("live_deploy.strategies.pivot_supertrend")

ST_PERIOD = 7
ST_MULTIPLIER = 3
R_KEYS = ("R1", "R2", "R3")
S_KEYS = ("S1", "S2", "S3")


# ═════════════════════════════════════════════════════════════════════
# PIVOT POINTS — ported verbatim from tg_int_st_pp (same formulas)
# ═════════════════════════════════════════════════════════════════════

def _pivots_classic(h: float, l: float, c: float) -> dict:
    p = (h + l + c) / 3
    return {
        "P": p,
        "R1": 2 * p - l,        "S1": 2 * p - h,
        "R2": p + (h - l),      "S2": p - (h - l),
        "R3": h + 2 * (p - l),  "S3": l - 2 * (h - p),
    }


def _pivots_fibonacci(h: float, l: float, c: float) -> dict:
    p = (h + l + c) / 3
    rng = h - l
    return {
        "P": p,
        "R1": p + 0.382 * rng, "S1": p - 0.382 * rng,
        "R2": p + 0.618 * rng, "S2": p - 0.618 * rng,
        "R3": p + 1.000 * rng, "S3": p - 1.000 * rng,
    }


def _pivots_camarilla(h: float, l: float, c: float) -> dict:
    p = (h + l + c) / 3
    rng = h - l
    return {
        "P": p,
        "R1": c + rng * 1.1 / 12, "S1": c - rng * 1.1 / 12,
        "R2": c + rng * 1.1 / 6,  "S2": c - rng * 1.1 / 6,
        "R3": c + rng * 1.1 / 4,  "S3": c - rng * 1.1 / 4,
    }


def _pivots_woodie(h: float, l: float, c: float) -> dict:
    p = (h + l + 2 * c) / 4
    return {
        "P": p,
        "R1": 2 * p - l,        "S1": 2 * p - h,
        "R2": p + (h - l),      "S2": p - (h - l),
        "R3": h + 2 * (p - l),  "S3": l - 2 * (h - p),
    }


PIVOT_FORMULAS = {
    "classic": _pivots_classic, "fibonacci": _pivots_fibonacci,
    "camarilla": _pivots_camarilla, "woodie": _pivots_woodie,
}


def compute_pivots(h: float, l: float, c: float, pivot_type: str = "classic") -> dict:
    fn = PIVOT_FORMULAS.get(pivot_type)
    if fn is None:
        raise ValueError(f"Unknown pivot_type: {pivot_type!r}. Choose from {list(PIVOT_FORMULAS)}")
    return fn(h, l, c)


# ═════════════════════════════════════════════════════════════════════
# SUPERTREND — incremental/streaming state (same math as the batch
# version, one candle at a time instead of one array pass)
# ═════════════════════════════════════════════════════════════════════

class SuperTrendState:
    def __init__(self, period: int = ST_PERIOD, multiplier: float = ST_MULTIPLIER,
                atr_method: str = "wilder"):
        self.period = period
        self.multiplier = multiplier
        self.atr_method = atr_method

        self.atr: Optional[float] = None
        self.final_upper: Optional[float] = None
        self.final_lower: Optional[float] = None
        self.trend: Optional[str] = None
        self.prev_close: Optional[float] = None
        self._tr_buffer: deque = deque(maxlen=period)

    @property
    def ready(self) -> bool:
        return self.trend is not None

    def seed_from_state(self, trend: str, final_upper: float, final_lower: float,
                        atr: float, prev_close: float) -> None:
        """Direct numeric seed — from an explicit supertrend_seed config entry."""
        self.trend = trend
        self.final_upper = final_upper
        self.final_lower = final_lower
        self.atr = atr
        self.prev_close = prev_close

    def update(self, candle: dict) -> Optional[str]:
        """
        Advance the state by one CLOSED candle. Returns the new trend,
        or None if still warming up ATR (not ready to trade yet).
        """
        high, low, close = candle["high"], candle["low"], candle["close"]

        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))

        if self.atr_method == "sma":
            self._tr_buffer.append(tr)
            if len(self._tr_buffer) < self.period:
                self.prev_close = close
                return None
            self.atr = sum(self._tr_buffer) / self.period
        elif self.atr is None:
            # wilder/ema warmup — seed = simple average of first `period` TRs,
            # same as the batch version's seeding convention.
            self._tr_buffer.append(tr)
            if len(self._tr_buffer) < self.period:
                self.prev_close = close
                return None
            self.atr = sum(self._tr_buffer) / self.period
        elif self.atr_method == "wilder":
            self.atr = (self.atr * (self.period - 1) + tr) / self.period
        else:  # ema
            k = 2 / (self.period + 1)
            self.atr = tr * k + self.atr * (1 - k)

        hl2 = (high + low) / 2
        basic_upper = hl2 + self.multiplier * self.atr
        basic_lower = hl2 - self.multiplier * self.atr

        if self.final_upper is None:
            # First-ever band computation (no seed, cold warmup just finished).
            self.final_upper = basic_upper
            self.final_lower = basic_lower
            self.trend = "down" if close <= basic_upper else "up"
        else:
            prev_close = self.prev_close
            self.final_upper = (
                basic_upper if (basic_upper < self.final_upper or prev_close > self.final_upper)
                else self.final_upper
            )
            self.final_lower = (
                basic_lower if (basic_lower > self.final_lower or prev_close < self.final_lower)
                else self.final_lower
            )
            if self.trend == "up":
                self.trend = "down" if close < self.final_lower else "up"
            else:
                self.trend = "up" if close > self.final_upper else "down"

        self.prev_close = close
        return self.trend

    def snapshot(self) -> dict:
        """Full internal state, JSON-serializable, sufficient for
        from_snapshot() to resume EXACTLY where this left off on the
        very next update() call — including the raw TR buffer, needed
        for atr_method='sma' (which reads a full rolling window on
        every update, not just during warmup) to keep producing a trend
        immediately rather than re-entering warmup after a restore.
        Used by the deployment-state persistence hook (see StrategyBase.
        get_persistable_state) — NOT used by the lighter supertrend_seed
        config path, which only ever approximates from a single chart
        reading and was never meant to reconstruct this exactly."""
        return {
            "period": self.period, "multiplier": self.multiplier, "atr_method": self.atr_method,
            "atr": self.atr, "final_upper": self.final_upper, "final_lower": self.final_lower,
            "trend": self.trend, "prev_close": self.prev_close,
            "tr_buffer": list(self._tr_buffer),
        }

    @classmethod
    def from_snapshot(cls, snap: dict) -> "SuperTrendState":
        st = cls(period=snap["period"], multiplier=snap["multiplier"], atr_method=snap["atr_method"])
        st.atr = snap["atr"]
        st.final_upper = snap["final_upper"]
        st.final_lower = snap["final_lower"]
        st.trend = snap["trend"]
        st.prev_close = snap["prev_close"]
        st._tr_buffer = deque(snap["tr_buffer"], maxlen=st.period)
        return st


def approx_missing_band(candle: dict, atr: float, multiplier: float, need: str) -> float:
    """
    Approximate the currently-INACTIVE SuperTrend band from a single
    candle's H/L — used only by the supertrend_seed (direct numeric)
    path, where the chart only exposes the active line, not both bands.
    `need` is "upper" or "lower".
    """
    hl2 = (candle["high"] + candle["low"]) / 2
    return hl2 + multiplier * atr if need == "upper" else hl2 - multiplier * atr


# ═════════════════════════════════════════════════════════════════════
# CANDLE AGGREGATION — buckets live ticks into 5-min OHLC candles
# ═════════════════════════════════════════════════════════════════════

class CandleAggregator:
    def __init__(self, interval_minutes: int = 5):
        self.interval_minutes = interval_minutes
        self._bucket_start: Optional[datetime] = None
        self._candle: Optional[dict] = None

    def _floor(self, ts: datetime) -> datetime:
        minute = (ts.minute // self.interval_minutes) * self.interval_minutes
        return ts.replace(minute=minute, second=0, microsecond=0)

    def add_tick(self, ts: datetime, price: float) -> Optional[dict]:
        """
        Feed one tick. Returns the just-COMPLETED candle if this tick
        started a new bucket, else None (candle still forming).
        """
        bucket = self._floor(ts)

        if self._bucket_start is None:
            self._bucket_start = bucket
            self._candle = {"date": bucket, "open": price, "high": price,
                            "low": price, "close": price}
            return None

        if bucket == self._bucket_start:
            c = self._candle
            if price > c["high"]:
                c["high"] = price
            if price < c["low"]:
                c["low"] = price
            c["close"] = price
            return None

        completed = self._candle
        self._bucket_start = bucket
        self._candle = {"date": bucket, "open": price, "high": price,
                        "low": price, "close": price}
        return completed


# ═════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════

def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _parse_hhmm(value: Optional[str]) -> Optional[dtime]:
    if not value:
        return None
    hh, mm = (int(x) for x in value.split(":"))
    return dtime(hh, mm)


def _derive_prev_day_ohlc(candles: list[dict]) -> Optional[dict]:
    """If seed_candles span (at least) a full previous calendar day, use
    that day's aggregate as prev_day_ohlc. Picks the LATEST complete day
    present, i.e. the day immediately before the last candle's day if
    that day has candles of its own, else the last day fully covered."""
    by_day: dict[date, list[dict]] = {}
    for c in candles:
        d = _parse_dt(c["date"]).date() if isinstance(c["date"], str) else c["date"].date()
        by_day.setdefault(d, []).append(c)
    days = sorted(by_day.keys())
    if len(days) < 2:
        return None
    prev_day = days[-2]
    day_candles = by_day[prev_day]
    return {
        "high": max(c["high"] for c in day_candles),
        "low": min(c["low"] for c in day_candles),
        "close": day_candles[-1]["close"],
    }


def apply_seed_to_state(
    deployment_name: str, st: "SuperTrendState", atr_method: str, cfg: dict,
    current_prev_day_ohlc: Optional[dict], log: Optional[logging.Logger] = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Shared seeding logic — extracted so pivot_supertrend_options.py can
    seed its own SuperTrendState identically without duplicating this
    (non-numeric, but easy-to-drift) orchestration. Behavior is exactly
    what PivotSupertrendStrategy._apply_seed used to do inline; that
    method is now a thin wrapper around this function.

    Mutates `st` in place (same as before). Returns
    (prev_trend, derived_prev_day_ohlc):
      - prev_trend is st.trend after seeding — None if nothing was seeded
        (cold start; caller keeps whatever prev_trend it already had,
        which is None at this point on every call site).
      - derived_prev_day_ohlc is only non-None when seed_candles spanned
        a full previous day AND current_prev_day_ohlc was falsy — the
        caller decides whether/how to store it (never overwrites an
        explicit prev_day_ohlc the caller already has).

    `log` defaults to this module's own logger; pass a caller-specific
    one (e.g. pivot_supertrend_options passing its own) so log lines
    read as coming from whichever strategy actually seeded, not always
    "pivot_supertrend".
    """
    log = log or logger
    seed_candles = cfg.get("seed_candles")
    seed_state = cfg.get("supertrend_seed")
    derived_prev_day_ohlc = None

    if seed_candles:
        for raw in seed_candles:
            c = dict(raw)
            c["date"] = _parse_dt(raw["date"])
            st.update(c)
        log.info(
            "%s: SuperTrend seeded from %d candle(s) -> trend=%s atr=%s",
            deployment_name, len(seed_candles), st.trend,
            round(st.atr, 2) if st.atr else None,
        )
        if not current_prev_day_ohlc:
            derived_prev_day_ohlc = _derive_prev_day_ohlc(seed_candles)
            if derived_prev_day_ohlc:
                log.info("%s: prev_day_ohlc derived from seed_candles -> %s",
                        deployment_name, derived_prev_day_ohlc)
        return st.trend, derived_prev_day_ohlc

    elif seed_state:
        if atr_method == "sma":
            log.warning(
                "%s: supertrend_seed given but atr_smoothing='sma' needs a "
                "rolling TR window, not a single ATR value — ignoring the "
                "seed, cold-starting SuperTrend instead. Use seed_candles "
                "for an SMA-compatible seed.", deployment_name,
            )
            return None, None
        as_of = dict(seed_state["as_of_candle"])
        as_of["date"] = _parse_dt(as_of["date"])
        atr = seed_state["atr"]
        trend = seed_state["trend"]
        value = seed_state["value"]
        if trend == "up":
            final_lower = value
            final_upper = approx_missing_band(as_of, atr, ST_MULTIPLIER, "upper")
        else:
            final_upper = value
            final_lower = approx_missing_band(as_of, atr, ST_MULTIPLIER, "lower")
        st.seed_from_state(
            trend=trend, final_upper=final_upper, final_lower=final_lower,
            atr=atr, prev_close=as_of["close"],
        )
        log.info(
            "%s: SuperTrend seeded from explicit state -> trend=%s "
            "(inactive band approximated)", deployment_name, trend,
        )
        return trend, None

    return None, None


# ═════════════════════════════════════════════════════════════════════
# STRATEGY
# ═════════════════════════════════════════════════════════════════════

@register_strategy(
    "pivot_supertrend",
    description="Pivot points (R1-R3/S1-S3) + SuperTrend(7,3) intraday — "
               "long above resistance with ST green, short below support "
               "with ST red, exit on ST flip or force-exit time.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "pivot_type": "classic",
        "atr_smoothing": "wilder",
        "force_exit_time": "15:00",
        "market_open_time": "09:15",
        "capital_per_trade": None,
        "prev_day_ohlc": None,
        "seed_candles": None,
        "supertrend_seed": None,
    },
)
class PivotSupertrendStrategy(StrategyBase):

    async def on_start(self, runner) -> None:
        cfg = runner.config
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "pivot_supertrend requires config.instrument_tokens to be a "
                f"ONE-ELEMENT list (the single instrument this deployment "
                f"trades) — got {tokens!r}"
            )
        self.instrument_token = tokens[0]
        self.symbol = cfg.get("symbol", str(self.instrument_token))
        self.pivot_type = cfg.get("pivot_type", "classic")
        self.atr_method = cfg.get("atr_smoothing", "wilder")
        self.force_exit_time = _parse_hhmm(cfg.get("force_exit_time", "15:00"))
        self.market_open_time = _parse_hhmm(cfg.get("market_open_time", "09:15"))
        self.capital_per_trade = cfg.get("capital_per_trade")

        self.aggregator = CandleAggregator(interval_minutes=5)
        self.st = SuperTrendState(period=ST_PERIOD, multiplier=ST_MULTIPLIER,
                                  atr_method=self.atr_method)

        self.today: Optional[date] = None
        self.today_high: Optional[float] = None
        self.today_low: Optional[float] = None
        self.today_last_close: Optional[float] = None

        self.prev_day_ohlc: Optional[dict] = cfg.get("prev_day_ohlc")
        self.pivots: Optional[dict] = None

        # Both hold trigger_values captured at DETECTION time (step 4/5
        # below), not bare flags — this strategy's "decide on close, act
        # on next open" timing means the triggering candle's own data
        # (close price, trend before/after, which pivot level broke) is
        # gone by the time the deferred trade actually executes, so it
        # has to be stashed here to make it into the fill's metadata.
        self.pending_exit: Optional[dict] = None
        self.pending_entry: Optional[dict] = None
        self.prev_trend: Optional[str] = None

        # Prefer whatever this deployment last persisted (see
        # get_persistable_state below) over the static config seed —
        # it's genuinely more current, having been captured live at the
        # last graceful stop rather than typed in once at initial
        # deploy. Only a first-ever start (or a strategy that's never
        # made it past cold-start) falls through to the config seed.
        persisted = await runner.load_state()
        if persisted and self._restore_from_state(runner, persisted):
            pass
        else:
            self._apply_seed(runner, cfg)
            if self.prev_day_ohlc:
                self.pivots = compute_pivots(
                    self.prev_day_ohlc["high"], self.prev_day_ohlc["low"],
                    self.prev_day_ohlc["close"], self.pivot_type,
                )
                logger.info("%s: pivots seeded from prev_day_ohlc -> %s",
                           runner.deployment_name,
                           {k: round(v, 2) for k, v in self.pivots.items()})
            else:
                logger.warning(
                    "%s: no prev_day_ohlc/seed_candles given — pivots unavailable "
                    "until a full trading day has been observed live (no entries "
                    "until then).", runner.deployment_name,
                )

    def _restore_from_state(self, runner, state: dict) -> bool:
        """Restore from a persisted deployment_state blob (see
        get_persistable_state below). Returns False (and leaves nothing
        mutated) on anything malformed/incompatible, so the caller falls
        through to the normal config-seed path instead of crashing on
        e.g. a future/incompatible state version."""
        try:
            if state.get("version") != 1:
                return False
            self.st = SuperTrendState.from_snapshot(state["supertrend"])
            self.prev_trend = state.get("prev_trend")
            self.prev_day_ohlc = state.get("prev_day_ohlc")
            self.pivots = state.get("pivots")
            today_str = state.get("today")
            self.today = date.fromisoformat(today_str) if today_str else None
            self.today_high = state.get("today_high")
            self.today_low = state.get("today_low")
            self.today_last_close = state.get("today_last_close")
        except (KeyError, TypeError, ValueError):
            logger.exception(
                "%s: persisted state was malformed — ignoring it and "
                "falling back to the config seed instead", runner.deployment_name,
            )
            return False
        logger.info(
            "%s: resumed from persisted live state (trend=%s, pivots=%s) — "
            "ignoring any static seed config, since this is more current",
            runner.deployment_name, self.st.trend, bool(self.pivots),
        )
        return True

    def get_persistable_state(self) -> Optional[dict]:
        """See StrategyBase's own docstring for when this gets called.
        Persists nothing (returns None) until SuperTrend has actually
        warmed up (self.st.trend is not None) — a deployment that's
        never gotten that far has nothing more useful to hand back than
        cold-start already gives it."""
        if self.st.trend is None:
            return None
        return {
            "version": 1,
            "supertrend": self.st.snapshot(),
            "prev_trend": self.prev_trend,
            "prev_day_ohlc": self.prev_day_ohlc,
            "pivots": self.pivots,
            "today": self.today.isoformat() if self.today else None,
            "today_high": self.today_high,
            "today_low": self.today_low,
            "today_last_close": self.today_last_close,
        }

    def _apply_seed(self, runner, cfg: dict) -> None:
        prev_trend, derived = apply_seed_to_state(
            runner.deployment_name, self.st, self.atr_method, cfg, self.prev_day_ohlc,
        )
        if prev_trend is not None:
            self.prev_trend = prev_trend
        if derived:
            self.prev_day_ohlc = derived

    async def on_tick(self, runner, tick: dict) -> None:
        ts = tick.get("exchange_timestamp")
        price = tick.get("last_price")
        if ts is None or price is None:
            # exchange_timestamp only exists in Kite's "full" tick mode —
            # this strategy needs it for candle bucketing.
            return

        day = ts.date()
        if self.today is None:
            self.today = day
        elif day != self.today:
            self._roll_over_day(runner)
            self.today = day

        completed = self.aggregator.add_tick(ts, price)
        if completed is None:
            return

        await self._on_candle_closed(runner, completed)

        if self.today_high is None or completed["high"] > self.today_high:
            self.today_high = completed["high"]
        if self.today_low is None or completed["low"] < self.today_low:
            self.today_low = completed["low"]
        self.today_last_close = completed["close"]

    def _roll_over_day(self, runner) -> None:
        if self.today_high is not None:
            self.prev_day_ohlc = {
                "high": self.today_high, "low": self.today_low,
                "close": self.today_last_close,
            }
            self.pivots = compute_pivots(
                self.prev_day_ohlc["high"], self.prev_day_ohlc["low"],
                self.prev_day_ohlc["close"], self.pivot_type,
            )
            logger.info(
                "%s: new trading day -> pivots recomputed from yesterday: %s",
                runner.deployment_name,
                {k: round(v, 2) for k, v in self.pivots.items()},
            )
        self.today_high = self.today_low = self.today_last_close = None

    async def _on_candle_closed(self, runner, candle: dict) -> None:
        t = candle["date"].time()
        before_cutoff = self.force_exit_time is None or t < self.force_exit_time
        # Lower bound — see market_open_time in CONFIG/the module
        # docstring's RULES section. Only ever combined into step 5
        # (fresh entry DETECTION) below; exits and an already-queued
        # pending entry are never gated by this.
        after_open = self.market_open_time is None or t >= self.market_open_time

        # 1 — execute a pending ST-flip exit at THIS candle's open
        if self.pending_exit is not None:
            await self._exit(runner, candle, "st_flip", self.pending_exit["trigger_values"])
            self.pending_exit = None

        # 2 — execute a pending entry at THIS candle's open
        if self.pending_entry is not None and before_cutoff:
            await self._enter(runner, candle, self.pending_entry["side"], self.pending_entry["trigger_values"])
        self.pending_entry = None

        # 3 — force-exit at/after cutoff if still open
        if self.force_exit_time is not None and t >= self.force_exit_time:
            if self.instrument_token in runner.open_positions:
                await self._exit(runner, candle, "force_exit", {
                    "candle_time": t.isoformat(), "force_exit_time": self.force_exit_time.isoformat(),
                })

        # 4 — advance SuperTrend, detect a flip. trigger_values captured
        # HERE (detection time) since this is the only point that has
        # both the pre-flip and post-flip trend plus the candle that
        # caused it — by the time step 1 executes the exit next call,
        # this candle is gone.
        prev_trend_before_update = self.prev_trend
        new_trend = self.st.update(candle)
        if new_trend is not None:
            if prev_trend_before_update is not None and new_trend != prev_trend_before_update:
                if self.instrument_token in runner.open_positions:
                    self.pending_exit = {
                        "trigger_values": {
                            "prev_trend": prev_trend_before_update, "new_trend": new_trend,
                            "close": round(candle["close"], 2),
                            "final_upper": round(self.st.final_upper, 2) if self.st.final_upper is not None else None,
                            "final_lower": round(self.st.final_lower, 2) if self.st.final_lower is not None else None,
                        },
                    }
            self.prev_trend = new_trend

        # 5 — detect a fresh entry signal (flat, pivots known, ST ready,
        # within the entry window). Same detection-time capture as step
        # 4: records WHICH specific pivot level broke, not just that
        # "some" R/S did.
        if self.instrument_token not in runner.open_positions and self.pivots is not None \
                and self.prev_trend is not None and before_cutoff and after_open:
            close = candle["close"]
            if self.prev_trend == "up":
                for k in R_KEYS:
                    level = self.pivots[k]
                    if close > level:
                        self.pending_entry = {
                            "side": "long",
                            "trigger_values": {
                                "close": round(close, 2), "trend": self.prev_trend,
                                "broken_level_key": k, "broken_level": round(level, 2),
                                "r_levels": {rk: round(self.pivots[rk], 2) for rk in R_KEYS},
                            },
                        }
                        break
            elif self.prev_trend == "down":
                for k in S_KEYS:
                    level = self.pivots[k]
                    if close < level:
                        self.pending_entry = {
                            "side": "short",
                            "trigger_values": {
                                "close": round(close, 2), "trend": self.prev_trend,
                                "broken_level_key": k, "broken_level": round(level, 2),
                                "s_levels": {sk: round(self.pivots[sk], 2) for sk in S_KEYS},
                            },
                        }
                        break

    async def _enter(self, runner, candle: dict, side: str, trigger_values: dict) -> None:
        price = candle["open"]
        budget = self.capital_per_trade if self.capital_per_trade is not None else runner.cash
        qty = int(budget // price) if price > 0 else 0
        if qty < 1:
            logger.warning(
                "%s: entry signal (%s) but budget %.2f can't afford even 1 "
                "unit @ %.2f — skipping", runner.deployment_name, side, budget, price,
            )
            return
        action = runner.buy if side == "long" else runner.sell
        meta = build_trade_meta(
            trigger="pivot_break_long" if side == "long" else "pivot_break_short",
            action="open_long" if side == "long" else "open_short",
            trigger_values=trigger_values,
            resulting_state={"side": side, "qty": qty, "entry_price": round(price, 2)},
            pivots={k: round(v, 2) for k, v in self.pivots.items()},
        )
        await action(self.symbol, self.instrument_token, qty, price, candle["date"],
                     reason="entry", metadata=meta)

    async def _exit(self, runner, candle: dict, reason: str, trigger_values: dict) -> None:
        pos = runner.open_positions.get(self.instrument_token)
        if pos is None:
            return
        price = candle["open"]
        qty = float(pos["qty"])
        action = runner.sell if pos["side"] == "long" else runner.buy
        meta = build_trade_meta(
            trigger=reason,
            action="close_long" if pos["side"] == "long" else "close_short",
            trigger_values=trigger_values,
            resulting_state={"position": "flat"},
        )
        await action(self.symbol, self.instrument_token, qty, price, candle["date"],
                     reason=reason, metadata=meta)

    async def on_stop(self, runner) -> None:
        logger.info("%s: strategy stopped (trend=%s, pivots=%s)",
                   runner.deployment_name, self.st.trend,
                   "set" if self.pivots else "none")
