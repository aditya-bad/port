"""
live_deploy — Pivot Points + SuperTrend(7,3) SHARED SIGNAL ENGINE.

Ports the exact math validated in tg_int_st_pp/strategy_pivot_supertrend.py
(pivot formulas, SuperTrend recursion) to live streaming ticks: pivot
computation, incremental SuperTrend, 5-min candle bucketing, live REST
seeding from Kite, and the gap/staleness guards every pivot_supertrend*
strategy needs. Re-implemented here rather than imported from
tg_int_st_pp — live_deploy stays standalone from the rest of the repo,
same as every other folder in it.

NOT A DEPLOYABLE STRATEGY ITSELF (Step 94) — this file used to also
register `"pivot_supertrend"`, a strategy trading the underlying index/
futures instrument directly. That variant is gone: every real deployment
in practice traded options off this same signal instead (selling premium
on a pivot breakout via `pivot_supertrend_options.py`, or buying it on a
SuperTrend flip via `pivot_supertrend_options_inverse.py`), and keeping
the direct-underlying version around meant maintaining a third, unused
copy of the same "decide on close, act on next candle" execution timing
— removed at the same time as Step 94's actual fix (see those two
modules' own docstrings for what changed and why). What's left here is
purely the shared library both of them import from: `CandleAggregator`,
`SuperTrendState`, `compute_pivots`, `fetch_seed_from_kite`,
`apply_seed_to_state`, `supertrend_from_seed_candles`,
`supertrend_status_fields(_from_state)`, `is_stale_candle_close`, and the
small parsing helpers (`_parse_hhmm` etc.) — nothing below registers a
strategy, so importing this module alone has no side effects beyond
defining these.

SIGNAL RULES (shared by both strategies that consume this engine):
  Long signal  — 5-min close above any of R1/R2/R3 AND SuperTrend green.
  Short signal — 5-min close below any of S1/S2/S3 AND SuperTrend red.
  A SuperTrend flip (trend reverses) is the OTHER half of the shared
  signal — pivot_supertrend_options exits an open leg on it,
  pivot_supertrend_options_inverse ENTERS on it (see that module's own
  docstring — it's deliberately the mirror image of the pivot-breakout
  rule above).

  NO fresh signal before `market_open_time` (config, default 09:15) —
  neither consuming strategy has an `entry_time` schedule at all: both
  watch continuously and react the instant a signal breaks, any time of
  day, which used to implicitly mean "any time the market is actually
  open" back when pre-market data simply never reached this pipeline.
  NSE now disseminates LIVE pre-open indicative-price ticks through the
  same feed (equity index dissemination during the 09:00-09:15 call
  auction, plus a genuine F&O futures pre-open session since Dec 2025)
  — without this floor, a pivot/trend combination already "ready" from
  a prior day (the normal state of any established deployment) could
  queue a real entry off a pre-market signal, executing the moment
  regular trading begins, priced off auction-based price discovery
  rather than real continuous trading. Only gates fresh signal
  DETECTION — an exit is never blocked by this.

  IMPORTANT (Step 94 fix): this gate must be checked against the
  SIGNAL CANDLE'S OWN bucket start (`candle["date"].time()`), never
  against the real wall-clock time the code happens to be running at.
  The two consuming strategies both process each candle's close the
  moment it closes (no more deferred "decide now, act next candle"
  gap — see their own docstrings), which means the call evaluating a
  candle spanning e.g. 09:10-09:15 always runs at real time ~09:15 —
  a naive `now.time() >= market_open_time` check is then trivially true
  for the FIRST candle of the day, every single day, letting a signal
  computed off pre-open/call-auction data leak straight through as
  "detected after market open" purely because of when the check
  happened to run, not what the data itself represents.

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

SHARED CONFIG KEYS (both consuming strategies accept these with the same
meaning — see each one's own CONFIG section for the rest):
  "pivot_type": classic (default) | fibonacci | camarilla | woodie
  "atr_smoothing": wilder (default) | sma | ema
  "force_exit_time": "15:00" (default) | null (disable — let SuperTrend
      flips be the ONLY exit trigger, i.e. positions can ride overnight
      — this is what a "positional" deployment would typically set)
  "market_open_time": "09:15" (default) | null (disable — allow signals
      off pre-market ticks too; NOT recommended, see the RULES section's
      "NO fresh signal before market_open_time" paragraph above for why)
      — the earliest time a fresh signal is allowed to be DETECTED.
      Never affects exits.
"""

import asyncio
import logging
from collections import deque
from datetime import date, datetime, timedelta, time as dtime
from typing import Optional
from zoneinfo import ZoneInfo

from ..options import NoKiteSession, get_kite_connect

logger = logging.getLogger("live_deploy.strategies.pivot_supertrend")

_IST = ZoneInfo("Asia/Kolkata")

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
# STATUS FIELDS (Step 87) — StrategyBase.get_status_fields()/
# status_fields_from_state() for the Detail page's Stats tab. Shared by
# all three pivot_supertrend* strategies (pivot_supertrend,
# pivot_supertrend_options, pivot_supertrend_options_inverse) — each
# tracks the identical `self.st` (SuperTrendState) / `self.pivots`
# (dict from compute_pivots) shape and persists it identically (see
# each file's own get_persistable_state), so one formatter covers all
# three rather than three near-duplicate copies.
# ═════════════════════════════════════════════════════════════════════

# Display order: the trend-following S1/S2/S3 levels (what a bullish
# pivot-break strategy actually watches for support) come before R1-R3,
# with the pivot point itself first as the reference everything else is
# relative to -- reads top-to-bottom as "center, then the ladder either
# side of it," not the raw insertion order compute_pivots happens to
# use internally (which is P/R.../S... alternating).
_PIVOT_DISPLAY_ORDER = ("P", "S1", "S2", "S3", "R1", "R2", "R3")


def supertrend_status_fields(st: SuperTrendState, pivots: Optional[dict]) -> Optional[list]:
    """Turns a live (or reconstructed-from-snapshot) SuperTrendState +
    pivots dict into the [{"label", "value"}, ...] shape
    get_status_fields()/status_fields_from_state() return. None if
    SuperTrend hasn't warmed up yet (st.trend is None) -- nothing
    meaningful to show, same "not ready" case on_tick's own callers
    already check via st.ready."""
    if st.trend is None:
        return None
    # The SuperTrend "line" itself is whichever band is currently
    # ACTIVE for the live trend -- final_lower while trending up (price
    # support, a break below it flips to down), final_upper while
    # trending down (resistance, a break above flips to up). Exactly
    # the value a chart's own SuperTrend indicator plots.
    st_value = st.final_lower if st.trend == "up" else st.final_upper
    fields = [
        {"label": "SuperTrend Trend", "value": st.trend},
        {"label": "SuperTrend Value", "value": round(st_value, 2) if st_value is not None else None},
    ]
    if pivots:
        fields.extend(
            {"label": f"Pivot {k}", "value": round(pivots[k], 2)}
            for k in _PIVOT_DISPLAY_ORDER if k in pivots
        )
    return fields


def supertrend_status_fields_from_state(state: Optional[dict]) -> Optional[list]:
    """The status_fields_from_state (paused/stopped) counterpart to
    supertrend_status_fields above -- reconstructs just enough of a
    SuperTrendState from a persisted deployment_state blob (the exact
    shape get_persistable_state() returns in all three
    pivot_supertrend* files: {"supertrend": st.snapshot(), "pivots":
    ..., ...}) to compute the same fields, without needing a live
    strategy instance at all. Tolerates a missing/malformed/
    incompatible blob by returning None rather than raising -- the
    caller (GET /deployments/{id}/strategy-status) has no other
    fallback if this throws."""
    if not state:
        return None
    try:
        st = SuperTrendState.from_snapshot(state["supertrend"])
    except (KeyError, TypeError, ValueError):
        return None
    return supertrend_status_fields(st, state.get("pivots"))


# ═════════════════════════════════════════════════════════════════════
# CANDLE AGGREGATION — buckets live ticks into 5-min OHLC candles
# ═════════════════════════════════════════════════════════════════════

class CandleAggregator:
    def __init__(self, interval_minutes: int = 5, label: Optional[str] = None):
        self.interval_minutes = interval_minutes
        # Logging-only — typically the owning deployment's name, so a
        # gap warning (below) is attributable when several deployments'
        # aggregators are running concurrently. None just omits the
        # prefix; doesn't affect bucketing/candle behavior at all.
        self.label = label
        self._bucket_start: Optional[datetime] = None
        self._candle: Optional[dict] = None

    def _floor(self, ts: datetime) -> datetime:
        minute = (ts.minute // self.interval_minutes) * self.interval_minutes
        return ts.replace(minute=minute, second=0, microsecond=0)

    def add_tick(self, ts: datetime, price: float) -> Optional[dict]:
        """
        Feed one tick. Returns the just-COMPLETED candle if this tick
        started a new bucket, else None (candle still forming).

        GAP DETECTION: if this tick's bucket is more than one interval
        past the bucket currently forming, one or more whole candles
        never happened for this aggregator — no tick landed in them at
        all, most likely because the upstream WebSocket dropped and
        reconnected (see LiveDataDispatcher.reconnect_count) with no
        backfill of whatever ticks were missed during the outage. This
        matters far more here than it would for a stateless indicator:
        SuperTrendState is RECURSIVE (each candle's bands depend on the
        previous candle's), so a silently skipped candle doesn't just
        leave a small gap in the chart — it permanently shifts every
        subsequent ATR/band value away from what a continuous data feed
        (e.g. what a real charting platform, or a fresh Kite REST
        historical_data fetch, would show) would have produced. Logged
        as a WARNING here purely for visibility/diagnosis — no separate
        correction step is needed: on_start's own live auto-seed (see
        fetch_seed_from_kite below) re-fetches gap-free candles straight
        from Kite's REST API on every restart, and on_post_market_
        checkpoint does the same once a day for whatever's still
        running, so a gap this logs is already self-healing on its own
        schedule; this warning just tells you it happened and roughly
        when. Only checked WITHIN the same calendar day — the very first tick
        of a new day is expected to land a long "gap" past whenever the
        previous day's last candle closed (overnight/weekend market
        closure, not a real outage), so that transition is deliberately
        not flagged.
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

        if bucket.date() == self._bucket_start.date():
            missed = int((bucket - self._bucket_start).total_seconds() // 60 // self.interval_minutes) - 1
            if missed > 0:
                logger.warning(
                    "%sCandleAggregator: %d candle(s) apparently missing between "
                    "%s and %s (no tick landed in that window) — likely a "
                    "WebSocket reconnect gap. Self-healing: the next on_start "
                    "(or today's post-market checkpoint) re-seeds SuperTrend "
                    "fresh from Kite's REST API, so no manual action is needed.",
                    f"{self.label}: " if self.label else "",
                    missed, self._bucket_start.time(), bucket.time(),
                )

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
# LIVE AUTO-SEEDING — self-fetched from Kite's REST API, not asked for
# via config. Real incident this exists for: the WebSocket tick stream
# can silently drop whole candles across a reconnect (CandleAggregator
# has no way to notice a gap in ITS OWN input — see its own docstring),
# and SuperTrend is RECURSIVE, so one skipped candle permanently drifts
# every value after it away from what a continuous feed (a real chart,
# or this same REST endpoint) would show. Rather than ask the deployer
# to hand-feed seed_candles/prev_day_ohlc once at registration time (the
# older apply_seed_to_state path above, kept only as a fallback), every
# strategy in this family now fetches its OWN seed straight from Kite —
# on EVERY on_start (cold deploy, resume from pause, or a mid-day
# restart after a crash/redeploy — all the same call, always re-fetched,
# never trusting whatever might already be in memory or the DB to still
# be gap-free) AND once a day at the post-market checkpoint (see
# StrategyBase.on_post_market_checkpoint), which additionally rolls
# prev_day_ohlc/pivots forward to TOMORROW using today's own now-final
# daily candle.
# ═════════════════════════════════════════════════════════════════════

AUTOSEED_LOOKBACK_DAYS = 7   # calendar days of 5-min candles to replay through "now" —
                             # comfortably several real trading days even across a long
                             # weekend; ATR(7)'s Wilder smoothing converges to the true
                             # trajectory well within that, so this is safety margin, not
                             # precision — there's no real downside to fetching more.


async def fetch_seed_from_kite(
    dispatcher, instrument_token: int, lookback_days: int = AUTOSEED_LOOKBACK_DAYS,
    need_prev_day_ohlc: bool = True, include_today_ohlc: bool = False,
) -> dict:
    """
    Live REST fetch (via the SAME Kite session the dispatcher's
    WebSocket is already authenticated with — get_kite_connect, no
    separate login) producing everything a strategy needs to seed
    itself cleanly, straight from Kite's own historical_data endpoint —
    the authoritative, gap-free source a real chart is built from, NOT
    this app's own WebSocket tick stream / CandleAggregator, which is
    exactly the thing that can silently develop gaps.

    Returns {"seed_candles": [{"date","high","low","close"}, ...],
             "prev_day_ohlc": {"high","low","close"} | None}.

    seed_candles: gap-free 5-min candles for the last `lookback_days`
    calendar days through right now — feed these through a fresh
    SuperTrendState.update() call each to get the mathematically
    correct current trend/bands, immune to whatever WS gaps happened in
    between.

    prev_day_ohlc (only fetched if need_prev_day_ohlc — the inverse
    strategy never uses pivots at all, so skips this round trip
    entirely): WHICH day depends on include_today_ohlc, since pivots'
    source day is a genuinely different question at different call
    sites —
      - include_today_ohlc=False (on_start, ANY time of day including a
        mid-session restart): today's own pivots must come from the
        most recently COMPLETED trading day strictly BEFORE today — the
        normal "yesterday" meaning, correct whether this fires at 9:16am
        or 1pm, since today itself is never a candidate no matter how
        far into the session it already is.
      - include_today_ohlc=True (the post-market checkpoint, which only
        ever runs after the day's close): today's session is NOW
        complete, so this call is rolling pivots forward to TOMORROW —
        today's own daily candle is exactly the right source.
    None if no completed day matching that rule exists yet in the
    fetched window (e.g. a brand new instrument, or lookback_days too
    short) — caller falls back to whatever it already has, same as a
    cold start with no seed ever did.

    Raises NoKiteSession (propagated from get_kite_connect) if no Kite
    session exists yet at all — caller decides how to degrade (this
    module's own on_start/on_post_market_checkpoint callers all catch
    this and fall through to persisted state / cold start, never let it
    crash the runner).
    """
    kite = get_kite_connect(dispatcher)   # raises NoKiteSession
    now = datetime.now(_IST).replace(tzinfo=None)
    today = now.date()

    candle_start = datetime.combine(today - timedelta(days=lookback_days), dtime(0, 0))
    raw_candles = await asyncio.to_thread(kite.historical_data, instrument_token, candle_start, now, "5minute")
    seed_candles = []
    for c in raw_candles:
        d = c["date"]
        if d.tzinfo is not None:
            d = d.astimezone(_IST).replace(tzinfo=None)
        seed_candles.append({
            "date": d, "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"]),
        })

    prev_day_ohlc = None
    if need_prev_day_ohlc:
        # A few extra days on the daily-candle side so a long weekend or
        # holiday cluster right before "today" still resolves to a real
        # completed day, not an empty result.
        daily_start = datetime.combine(today - timedelta(days=lookback_days + 10), dtime(0, 0))
        daily_raw = await asyncio.to_thread(kite.historical_data, instrument_token, daily_start, now, "day")
        candidates = []
        for row in daily_raw:
            row_date = row["date"].date() if hasattr(row["date"], "date") else row["date"]
            if (row_date == today) if include_today_ohlc else (row_date < today):
                candidates.append(row)
        if candidates:
            last = candidates[-1]
            prev_day_ohlc = {
                "high": round(float(last["high"]), 2), "low": round(float(last["low"]), 2),
                "close": round(float(last["close"]), 2),
            }

    return {"seed_candles": seed_candles, "prev_day_ohlc": prev_day_ohlc}


def supertrend_from_seed_candles(seed_candles: list[dict], atr_method: str = "wilder") -> "SuperTrendState":
    """A brand new SuperTrendState, fully replayed through `seed_candles`
    in order — the mathematically correct current state, immune to
    whatever gaps this deployment's own tick-driven CandleAggregator may
    have hit, since these candles came straight from fetch_seed_from_kite
    (Kite's REST API), not the WS stream."""
    st = SuperTrendState(period=ST_PERIOD, multiplier=ST_MULTIPLIER, atr_method=atr_method)
    for c in seed_candles:
        st.update(c)
    return st


# How late (past a candle's own theoretical close time) a candle-close
# event can arrive before it's treated as GAP-AFFECTED rather than
# ordinary processing lag. In normal operation, `_on_candle_closed`
# fires within a few seconds to a minute of a candle's real close (the
# very next tick after the boundary triggers it) — two full candle-
# widths of grace comfortably covers that, with no risk of mistaking
# real jitter for a gap, while being nowhere near long enough to miss a
# genuine multi-candle WebSocket outage (see CandleAggregator.add_tick's
# own gap-detection comment — this is the SAME failure mode, checked at
# the point where it actually matters: before a trade gets executed off
# a stale timestamp and a now-disconnected live price).
STALE_CANDLE_GRACE_INTERVALS = 2


def is_stale_candle_close(candle_date: datetime, interval_minutes: int, now: datetime) -> bool:
    """True if `now` (the REAL tick timestamp that triggered this
    candle-close — see each strategy's own on_tick, which passes the
    live tick's exchange_timestamp through, never the candle's own
    bucket-start) is suspiciously far past when `candle_date` should
    have closed.

    Real incident this exists for: a WebSocket gap from ~9:20 to
    ~13:30 meant the candle that had been forming when the gap started
    (date=9:20) wasn't handed to _on_candle_closed until the first tick
    after the gap arrived at ~13:30 — REAL time. Every pivot_supertrend*
    strategy blindly used candle["date"] (9:20) as the executed trade's
    timestamp, so a signal detected before the gap got executed hours
    later, at a live price with no relation to the original setup,
    LOGGED as if it happened at 9:20 — which is exactly backwards from
    what actually happened (a real fill at ~13:30's price, mislabeled).
    """
    theoretical_close = candle_date + timedelta(minutes=interval_minutes)
    return (now - theoretical_close).total_seconds() > STALE_CANDLE_GRACE_INTERVALS * interval_minutes * 60
