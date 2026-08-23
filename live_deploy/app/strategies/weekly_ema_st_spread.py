"""
live_deploy — weekly_ema_st_spread: directional weekly credit spread off
EMA(20) + SuperTrend confluence on 1-HOUR NIFTY candles.

Genuinely different strategy from pivot_supertrend_options.py — different
indicators (EMA, not pivots), different timeframe (1H, not 5min), weekly
multi-day holding (not intraday, no daily force-exit at all), a REAL
bought hedge leg (not a naked ATM sell). This file is new; it does NOT
modify pivot_supertrend.py, pivot_supertrend_options.py, or
pivot_supertrend_options_inverse.py — it only imports the genuinely
timeframe-agnostic pieces (`SuperTrendState`, `CandleAggregator`,
`_parse_hhmm`, `_IST`, `is_stale_candle_close`) those files already
proved out, the same way pivot_supertrend_options.py itself does.

═══════════════════════════════════════════════════════════════════════
WHAT'S STATED vs. WHAT'S INFERRED — read this before touching any
PROVISIONAL constant below
═══════════════════════════════════════════════════════════════════════
Two source videos gave this strategy. Most of it is well-evidenced (16
weeks of real trades, explicit rules, explicit numbers) and is marked
STATED. Two pieces are explicitly NOT formulas in the source material —
described as experience/feel, with only one worked example each — and
are marked PROVISIONAL, with the single data point they're drawn from
named in place. These are ordinary config values with sane defaults, not
reverse-engineered "better" formulas — tune them from real forward-test
results later; a real formula found in more source material later is a
config change, not a rebuild.

  STATED, high confidence:
    - EMA(20) + SuperTrend on 1H NIFTY candles, weekly expiry, credit
      spread with a real hedge (never naked), multi-day holding.
    - Entry confluence rules (bearish/bullish), target_sell_premium=100.
    - Exit is SuperTrend-flip-only; EMA position alone never exits.
    - Fresh-touch-of-EMA gate before any re-entry after a close.
    - Fixed lots_per_trade, stepped up by exactly 1 lot once accumulated
      profit covers that lot's own margin — not a % or milestone.
    - No fixed stop-loss/target in rupees — losses can run large on any
      one trade, accepted by design, never patched with an invented stop.

  PROVISIONAL (flagged again at each config field below):
    - st_period=10 / st_multiplier=3 — inferred from "normal, nothing
      changed" (i.e. unmodified default), never stated as numbers.
      Deliberately NOT pivot_supertrend's tuned 7/3 — that was tuned for
      a different (intraday) signal and reusing it here would be a real
      behavioral change, not a neutral default.
    - hedge_width_points=200 — drawn from exactly one worked example
      (sold 24,700 CE / bought 24,900 CE); source material is explicit
      there is no fixed formula ("no fixed distance... not for margin
      benefit"). A straight points offset, deliberately NOT
      adaptive/volatility-scaled — that would be inventing a formula the
      source explicitly says doesn't exist.
    - entry_signal_cutoff_time="14:15" — inferred from one example of
      ignoring a signal appearing ~3:00-3:15pm on a 1H chart; not an
      exact stated cutoff.
    - early_close_capture_pct=0.80 / early_close_max_days_to_expiry=1 —
      drawn from one example (~80-85% captured, ~1 day left), not a
      precisely quoted rule.

═══════════════════════════════════════════════════════════════════════
DEVIATIONS FROM THE LITERAL CONFIG SCHEMA GIVEN, AND WHY
═══════════════════════════════════════════════════════════════════════
Two keys exist beyond the literal schema handed down for this build —
both are structural necessities, not scope creep, and both default to
values that make an unmodified deploy behave exactly as described:

  "instrument_tokens": [256265] (NIFTY 50 spot) — REQUIRED by the
      runner's own tick-routing (DeploymentRunner.tokens is computed
      ONCE from config before on_start ever runs, and every strategy in
      this codebase needs it for the same reason: no instrument_tokens
      means literally zero ticks ever reach on_tick, a permanently-silent
      deployment). "instrument" in the given schema names the OPTIONS
      CHAIN ("NIFTY"), a different thing from the SPOT token this
      strategy's candles/EMA/SuperTrend are built from — same
      underlying/options split every pivot_supertrend* file already has.
  "market_open_time": "09:15" (nullable) — the exact pre-market
      call-auction/futures-pre-open gate pivot_supertrend.py's own module
      docstring documents at length (NSE disseminates live indicative
      ticks through the same feed 09:00-09:15) — a genuine correctness
      issue for ANY candle-close signal in this codebase, not something
      specific to the pivot family. Same default, same nullable-to-
      disable convention, applied identically to both fresh entry
      detection AND the SuperTrend-flip exit check (a flip detected off
      pre-market data is exactly as untrustworthy as an entry signal off
      it) — checked against the CANDLE'S OWN bucket start, never real
      wall-clock time, for the identical reason documented in
      pivot_supertrend.py's own RULES section.

Also fixed as Python constants, NOT config keys, because the source
material frames them as this strategy's IDENTITY, not tunable knobs:
  EXPIRY_SELECTOR = "THIS_WEEK" — "weekly expiry" is a defining trait
      here, not a per-deployment choice the way pivot_supertrend_options'
      own expiry_selector is.
  (No force_exit_time at all — unlike the intraday family, this strategy
  is genuinely positional; its only exits are the two rules below.)

═══════════════════════════════════════════════════════════════════════
ENTRY
═══════════════════════════════════════════════════════════════════════
On each 1H candle CLOSE (immediate execution — see EXECUTION TIMING
below), while flat:
  Bearish: SuperTrend trend == "down" AND close < EMA(20) -> sell a
      CALL spread (sold ATM-ish CE near target_sell_premium + a CE hedge
      hedge_width_points ABOVE the sold strike).
  Bullish: SuperTrend trend == "up" AND close > EMA(20) -> sell a PUT
      spread (sold PE + a PE hedge hedge_width_points BELOW the sold
      strike).

Sold leg strike selection reuses OptionsResolver.get_leg_by_premium AS
IS (ATM-centered, ± a strike window) rather than building a separate
"start near the SuperTrend line, drift toward ATM" search — the source
material's own instruction is explicit: "reuse the same target-premium
strike search already built... don't rebuild it." In practice a target
premium of 100 on NIFTY weeklies resolves to something reasonably close
to ATM anyway, so this is not a meaningful behavioral gap from the
video's own described process, just a simpler, already-proven code path
instead of a bespoke one.

LAST-CANDLE-OF-DAY DEFERRAL: a signal detected on a candle starting
at/after entry_signal_cutoff_time is NOT acted on today. This needs no
extra state at all: entry (and the flip-exit, see EXIT below) is simply
gated by `candle_time < entry_signal_cutoff_time`; tomorrow's own first
candle re-evaluates the CURRENT (by-then-updated) EMA/SuperTrend state
fresh, which is exactly "re-check at/after the next trading day's open,
enter only if the condition still holds" — a whipsaw that reverses
overnight-equivalent is correctly NOT acted on the next day either.

FRESH-TOUCH RE-ENTRY GATE: `touched_ema_since_exit` starts True (nothing
to gate before any trade has ever happened), is cleared to False the
moment a position closes, and is set back True the instant a later
candle's range contains the EMA value OR closes on the opposite side of
it from the previous candle (a touch or a cross, either satisfies it).
No fresh entry is considered while this is False, regardless of how
strongly the confluence condition otherwise holds.

POST-EXIT GAP RULE — translated, not hardcoded to a weekday: the source
rule ("closes Thursday -> skip Friday and Monday -> resume Tuesday") is
anchored to NIFTY's OLD Thursday expiry, since moved to Tuesday — never
hardcoded here. Translated as: a close that lands ON the current
position's own actual expiry date (`self.sold_expiry`, the real resolved
date THIS position was opened against — never a guessed weekday) arms a
trading-day counter; entries stay blocked while
`trading_days_elapsed_since_close <= post_exit_gap_trading_days`, i.e.
resuming on the (N+1)th trading day after the close (2 skipped, 3rd one
resumes — matches "skip Friday and Monday [2], resume Tuesday [3rd]"
exactly, weekday-agnostic).

═══════════════════════════════════════════════════════════════════════
EXIT
═══════════════════════════════════════════════════════════════════════
Primary — SuperTrend flip ONLY: EMA position alone never exits a
position ("if SuperTrend is not green... the trade keeps running",
regardless of where price sits vs. the EMA). Implemented as `current
trend != the trend recorded at this position's own entry`
(`self.entry_trend`) rather than a same-candle "did it just flip" edge
check — equivalent when acting immediately (the first candle where they
diverge IS the flip, from this position's own point of view), and it's
what makes the SAME last-candle-of-day deferral trivially correct for
exits too: a divergence detected at/after cutoff is deferred, and
tomorrow's first candle re-compares the (possibly since-reverted)
current trend against the unchanged `entry_trend` — "exit only if the
flip still holds" falls out for free, no separate staged-signal state.

Secondary — early profit-take near expiry (PROVISIONAL, see above): if
`early_close_capture_pct` of the spread's own net credit is already
captured (captured = net credit at entry − net cost to close now, as a
fraction of the entry net credit — the standard "how much of this credit
spread's max possible profit is already banked" reading, chosen because
it's the one that means the same thing across every strike/premium combo
this strategy can enter, not a fixed rupee number that wouldn't) AND
`early_close_max_days_to_expiry` or fewer days remain (calendar days
against `self.today`, the same IST-tick-derived "today" every other
day-tracking field in this codebase already uses — never
`date.today()`), close early. NOT gated by entry_signal_cutoff_time —
deferring a capture-lock exit to "tomorrow" when only ~1 day is left
could mean deferring it past expiry entirely, defeating the rule's own
purpose.

No other exit condition exists. No fixed rupee target, no fixed
stop-loss distance — deliberate: the source material is explicit that
losses on this strategy can run large relative to wins on any single
trade, accepted by design (see POSITION SIZING below), never patched
with an invented stop.

═══════════════════════════════════════════════════════════════════════
POSITION SIZING — why capital defaults to 1,00,000, not 5,00,000
═══════════════════════════════════════════════════════════════════════
The video's own numbers were sized for 5 lots, not 1. He states quantity
as 300; NIFTY's lot size before the January 2026 revision was smaller
than today's 65 — at a prior lot size of 60, 300 / 60 = 5 lots exactly.
His stated Rs 5,00,000 capital was the base for THAT 5-lot position,
meaning the real ratio he actually ran is Rs 1,00,000 of capital PER
LOT, not Rs 5L for a single lot. This build starts at 1 lot, so capital
scales down by the same factor: `capital` defaults to Rs 1,00,000, not
Rs 5,00,000 — leaving capital at 5L while dropping to 1 lot would
silently break every ratio the video actually demonstrated (margin
utilization, the step-up threshold), since both depend on capital and
lot count moving TOGETHER.

Step-up (explicit interpretation, flagged per this project's own
convention for resolving a "needs a concrete formula" ambiguity):
`cum_pnl_since_stepup` accumulates realized P&L across every closed
spread; the moment it EXCEEDS `capital_per_lot` (default: `capital` /
the INITIAL lots_per_trade at deploy time — a fixed per-lot capital
unit, never recomputed as lots grow), `lots_per_trade` increases by
exactly 1 for the NEXT entry only (never retroactive), and the excess
above the threshold carries forward (not reset to 0) so a single trade
that clears the bar twice over correctly steps up twice, and no profit
is silently lost at the boundary. No enforced margin-utilization target
— the ~35-40% figure from the video is reported in trade metadata, not
gated on.

═══════════════════════════════════════════════════════════════════════
EXECUTION TIMING / RESUME-SAFETY (mirroring the pivot_supertrend*
family's own hard-won architecture, per explicit instruction to reuse
it rather than reinvent a parallel, simpler version)
═══════════════════════════════════════════════════════════════════════
  - Immediate execution, same candle that confirms a signal — no staged/
    pending-signal model exists here at all (the ONE deliberate delay is
    the narrow, stated last-candle-of-day rule above, not a general
    "decide now, act later" architecture).
  - SuperTrend seeds via the REUSED `SuperTrendState` class, exactly as
    pivot_supertrend_options.py does — imported, never reimplemented.
  - EMA(20) is genuinely new to this codebase (no existing strategy uses
    one), so there's no existing state-persistence pattern to import for
    it — but per explicit instruction, it gets the IDENTICAL discipline
    built fresh: `EMAState` below mirrors `SuperTrendState`'s own shape
    (`update()`/`ready`/`snapshot()`/`from_snapshot()`) call for call.
  - BOTH indicators get a fresh REST seed from Kite on EVERY on_start
    (cold deploy, resume, or a mid-day restart — never trusting whatever
    might already be in memory or the DB to still be gap-free), replayed
    together over the SAME 1H candle sequence so their warmup states
    stay mutually consistent, with the identical fallback chain the
    pivot family uses: live Kite seed -> persisted state -> cold start.
  - A periodic self-healing re-seed at `on_post_market_checkpoint`
    (same daily standing-checkpoint hook every pivot_supertrend* file
    already uses) re-fetches clean 1H candles and recomputes BOTH
    indicators fresh, so a deployment running continuously through
    market close self-heals without a restart.
  - Persisted state is versioned; restart-safety additionally covers
    this strategy's own daily/step-up counters
    (`touched_ema_since_exit`, the post-exit-gap counters,
    `lots_per_trade`, `cum_pnl_since_stepup`) the SAME way
    pivot_supertrend_options.py persists `trades_today` — there is no
    external source of truth to re-derive these from the way SuperTrend/
    EMA's own numeric state can be re-derived from a fresh Kite fetch, so
    they're restored UNCONDITIONALLY near the top of on_start, before
    either seeding path runs.
  - The two spread legs (sold + hedge) themselves are never persisted
    here at all — already resume-safe via the DB (`runner.open_positions`
    on every on_start), same principle as every other strategy in this
    family. Since a call spread's two legs are BOTH "CE" (and a put
    spread's both "PE"), they can't be told apart by symbol suffix the
    way intraday_dtt_simple's CE/PE straddle legs can — `positions.side`
    ("short"/"long") is what distinguishes the sold leg from the hedge
    leg on resume.
  - Stale-signal guard: `is_stale_candle_close` (imported, unmodified)
    gates fresh ENTRY detection exactly like the pivot family; the
    SuperTrend-flip and early-close exits are never staleness-gated
    (absorbing a late candle's real OHLC into the recursive indicators
    is still correct even when it arrived late — only a NEW decision off
    stale data is the actual risk).

CONFIG:
  "instrument": "NIFTY" (default) — the options chain's own `name`.
      NIFTY is the only instrument this strategy has been demonstrated
      against; nothing here assumes it works for anything else.
  "instrument_tokens": [256265] (default) — see DEVIATIONS above.
  "candle_interval_minutes": 60 (default) — the signal timeframe.
  "ema_period": 20 (default) — STATED.
  "st_period": 10 / "st_multiplier": 3 (defaults) — PROVISIONAL, see
      above.
  "target_sell_premium": 100 (default) — STATED.
  "hedge_width_points": 200 (default) — PROVISIONAL, see above.
  "entry_signal_cutoff_time": "14:15" (default) — PROVISIONAL, see
      above. Applied to both fresh entries and the SuperTrend-flip exit.
  "market_open_time": "09:15" (default, nullable) — see DEVIATIONS.
  "post_exit_gap_trading_days": 2 (default) — STATED (translated from
      the Thursday-anchored example, see above).
  "early_close_capture_pct": 0.80 / "early_close_max_days_to_expiry": 1
      (defaults) — PROVISIONAL, see above.
  "capital": 100000 (default) — see POSITION SIZING above for why this
      isn't 500000.
  "lots_per_trade": 1 (default) — mutable at runtime via the step-up
      rule; does NOT auto-scale with account growth on its own.
  "capital_per_lot": null (default) — resolves to `capital /
      lots_per_trade` (the INITIAL value) at on_start if not set
      explicitly.
"""

import asyncio
import logging
from collections import deque
from datetime import date, datetime, timedelta, time as dtime
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver, get_kite_connect, options_exchange_for
from .pivot_supertrend import _IST, _parse_hhmm, CandleAggregator, SuperTrendState, is_stale_candle_close
from .registry import register_strategy
from .trade_meta import build_trade_meta

logger = logging.getLogger("live_deploy.strategies.weekly_ema_st_spread")

EXPIRY_SELECTOR = "THIS_WEEK"   # identity, not a config knob -- see module docstring

# Calendar days of 1H candles to replay through "now" on every seed —
# ~40 trading days' worth (~280 hourly candles at ~7/day), comfortably
# past both EMA(20)'s exponential warmup and SuperTrend(10)'s ATR
# convergence, matching what a real chart showing "the last couple of
# months" would already show — safety margin for convergence quality,
# not a bare minimum.
HOURLY_AUTOSEED_LOOKBACK_DAYS = 60


# ═════════════════════════════════════════════════════════════════════
# EMA — incremental/streaming state, deliberately mirroring
# SuperTrendState's own shape (update/ready/snapshot/from_snapshot) so
# it gets the identical seed-fresh/persist/self-heal discipline, per
# explicit instruction, even though there's no existing EMA state to
# import (no other strategy in this codebase uses one).
# ═════════════════════════════════════════════════════════════════════

class EMAState:
    def __init__(self, period: int = 20):
        self.period = period
        self.value: Optional[float] = None
        # Warmup: the first `period` closes are simple-averaged -- the
        # standard, textbook way to seed an EMA recursion cold, same
        # "seed = simple average of the first N values" convention
        # SuperTrendState's own ATR warmup already uses.
        self._buffer: deque = deque(maxlen=period)

    @property
    def ready(self) -> bool:
        return self.value is not None

    def update(self, candle: dict) -> Optional[float]:
        close = candle["close"]
        if self.value is None:
            self._buffer.append(close)
            if len(self._buffer) < self.period:
                return None
            self.value = sum(self._buffer) / self.period
            return self.value
        k = 2 / (self.period + 1)
        self.value = close * k + self.value * (1 - k)
        return self.value

    def snapshot(self) -> dict:
        return {"period": self.period, "value": self.value, "buffer": list(self._buffer)}

    @classmethod
    def from_snapshot(cls, snap: dict) -> "EMAState":
        ema = cls(period=snap["period"])
        ema.value = snap["value"]
        ema._buffer = deque(snap["buffer"], maxlen=ema.period)
        return ema


def _status_fields(st: SuperTrendState, ema: EMAState, lots_per_trade: Optional[int] = None) -> Optional[list]:
    """Same shape/active-band convention as pivot_supertrend.py's own
    supertrend_status_fields, plus the EMA reading and current lot size
    -- not imported from there since this also needs to fold in EMA,
    which that function knows nothing about."""
    if st.trend is None and ema.value is None:
        return None
    fields = []
    if st.trend is not None:
        st_value = st.final_lower if st.trend == "up" else st.final_upper
        fields.append({"label": "SuperTrend Trend", "value": st.trend})
        fields.append({"label": "SuperTrend Value", "value": round(st_value, 2) if st_value is not None else None})
    if ema.value is not None:
        fields.append({"label": f"EMA({ema.period})", "value": round(ema.value, 2)})
    if lots_per_trade is not None:
        fields.append({"label": "Current Lot Size", "value": lots_per_trade})
    return fields


async def _fetch_hourly_seed_from_kite(dispatcher, instrument_token: int, lookback_days: int) -> list[dict]:
    """Gap-free 1H candles straight from Kite's own historical_data —
    the same "authoritative REST source, immune to WS gaps" principle
    fetch_seed_from_kite (pivot_supertrend.py) already established for
    the 5-min pivot family, at a different granularity and without that
    function's own prev_day_ohlc side-fetch (this strategy has no
    pivots at all). Kept local to this file rather than generalizing
    the shared function, since pivot_supertrend.py is deliberately left
    untouched by this build. Raises NoKiteSession, same as
    fetch_seed_from_kite, for the caller to handle identically (fall
    through to persisted state / cold start)."""
    kite = get_kite_connect(dispatcher)
    now = datetime.now(_IST).replace(tzinfo=None)
    start = datetime.combine(now.date() - timedelta(days=lookback_days), dtime(0, 0))
    raw = await asyncio.to_thread(kite.historical_data, instrument_token, start, now, "60minute")
    candles = []
    for c in raw:
        d = c["date"]
        if d.tzinfo is not None:
            d = d.astimezone(_IST).replace(tzinfo=None)
        candles.append({
            "date": d, "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
        })
    return candles


@register_strategy(
    "weekly_ema_st_spread",
    description="EMA(20) + SuperTrend on 1H NIFTY candles — sells a weekly "
               "credit spread (target-premium short + a real points-away "
               "hedge, never naked) on trend/EMA confluence, exits on a "
               "SuperTrend flip or an early profit-take near expiry. "
               "Multi-day holding, no daily force-exit. Live paper-trading only.",
    default_config={
        "instrument": "NIFTY",
        "instrument_tokens": [256265],
        "candle_interval_minutes": 60,
        "ema_period": 20,
        "st_period": 10,
        "st_multiplier": 3,
        "target_sell_premium": 100,
        "hedge_width_points": 200,
        "entry_signal_cutoff_time": "14:15",
        "market_open_time": "09:15",
        "post_exit_gap_trading_days": 2,
        "early_close_capture_pct": 0.80,
        "early_close_max_days_to_expiry": 1,
        "capital": 100000,
        "lots_per_trade": 1,
        "capital_per_lot": None,
    },
)
class WeeklyEmaStSpreadStrategy(StrategyBase):

    async def on_start(self, runner) -> None:
        cfg = runner.config
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "weekly_ema_st_spread requires config.instrument_tokens to "
                f"be a ONE-ELEMENT list — the underlying spot's token, used "
                f"only for candle/signal generation — got {tokens!r}"
            )
        self.instrument_token = tokens[0]
        self.underlying = str(cfg.get("instrument") or "NIFTY").strip().upper()

        self.candle_interval_minutes = int(cfg.get("candle_interval_minutes", 60))
        self.ema_period = int(cfg.get("ema_period", 20))
        self.st_period = int(cfg.get("st_period", 10))
        self.st_multiplier = float(cfg.get("st_multiplier", 3))
        if self.ema_period < 2 or self.st_period < 2:
            raise ValueError("ema_period and st_period must both be >= 2")

        self.target_sell_premium = float(cfg.get("target_sell_premium", 100))
        self.hedge_width_points = float(cfg.get("hedge_width_points", 200))
        if self.hedge_width_points <= 0:
            raise ValueError(f"hedge_width_points must be > 0, got {self.hedge_width_points}")

        self.entry_signal_cutoff_time = _parse_hhmm(cfg.get("entry_signal_cutoff_time", "14:15")) or dtime(14, 15)
        self.market_open_time = _parse_hhmm(cfg.get("market_open_time", "09:15"))

        self.post_exit_gap_trading_days = int(cfg.get("post_exit_gap_trading_days", 2))
        self.early_close_capture_pct = float(cfg.get("early_close_capture_pct", 0.80))
        self.early_close_max_days_to_expiry = int(cfg.get("early_close_max_days_to_expiry", 1))

        self.capital = float(cfg.get("capital", 100000))
        initial_lots = int(cfg.get("lots_per_trade") or 1)
        if initial_lots < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {initial_lots}")
        self.lots_per_trade = initial_lots
        raw_cpl = cfg.get("capital_per_lot")
        self.capital_per_lot = float(raw_cpl) if raw_cpl else (self.capital / initial_lots)
        if self.capital_per_lot <= 0:
            raise ValueError(f"capital_per_lot must be > 0, got {self.capital_per_lot}")

        self.resolver = OptionsResolver(runner.dispatcher, exchange=options_exchange_for(self.underlying))
        self.aggregator = CandleAggregator(interval_minutes=self.candle_interval_minutes, label=runner.deployment_name)
        self.st = SuperTrendState(period=self.st_period, multiplier=self.st_multiplier, atr_method="wilder")
        self.ema = EMAState(period=self.ema_period)
        self._prev_close: Optional[float] = None

        self.today: Optional[date] = None
        self._late_signal_logged_today = False

        # Re-entry / cooldown state -- see module docstring's ENTRY
        # section. touched_ema_since_exit starts True: nothing to gate
        # before this deployment has ever held a position.
        self.touched_ema_since_exit = True
        self._gap_exit_pending = False
        self._gap_trading_days_elapsed = 0
        self.cum_pnl_since_stepup = 0.0

        self.entry_trend: Optional[str] = None
        self.position_side: Optional[str] = None

        self.sold_token: Optional[int] = None
        self.sold_symbol: Optional[str] = None
        self.sold_exchange: Optional[str] = None
        self.sold_entry_price: Optional[float] = None
        self.sold_strike: Optional[float] = None
        self.sold_option_type: Optional[str] = None
        self.sold_expiry: Optional[date] = None

        self.hedge_token: Optional[int] = None
        self.hedge_symbol: Optional[str] = None
        self.hedge_exchange: Optional[str] = None
        self.hedge_entry_price: Optional[float] = None
        self.hedge_strike: Optional[float] = None

        # Daily/step-up counters have NO external source of truth to
        # re-derive from the way SuperTrend/EMA's own numeric state can
        # be re-derived from a fresh Kite fetch below -- so, same as
        # pivot_supertrend_options.py's own trades_today, they're
        # restored UNCONDITIONALLY here, before either seeding path
        # runs, from whatever this deployment last persisted.
        persisted = await runner.load_state()
        if persisted and persisted.get("version") == 1:
            self.touched_ema_since_exit = bool(persisted.get("touched_ema_since_exit", True))
            self._gap_exit_pending = bool(persisted.get("gap_exit_pending", False))
            self._gap_trading_days_elapsed = int(persisted.get("gap_trading_days_elapsed", 0))
            self.lots_per_trade = int(persisted.get("lots_per_trade", self.lots_per_trade))
            self.cum_pnl_since_stepup = float(persisted.get("cum_pnl_since_stepup", 0.0))
            persisted_today = persisted.get("today")
            if persisted_today:
                try:
                    self.today = date.fromisoformat(persisted_today)
                except ValueError:
                    self.today = None

        # Resume-safety: reattach any already-open sold/hedge leg(s).
        # `positions.side` ("short"/"long") is what tells them apart --
        # NOT the CE/PE suffix, since both legs of a call spread are CE
        # and both legs of a put spread are PE (unlike intraday_dtt_
        # simple's CE+PE straddle, where the suffix alone disambiguates).
        found_short = found_long = None
        for token, pos in runner.open_positions.items():
            if pos["side"] == "short":
                found_short = (token, pos)
            elif pos["side"] == "long":
                found_long = (token, pos)

        if found_short:
            token, pos = found_short
            meta = pos["metadata"] or {}
            self.sold_token, self.sold_symbol = token, pos["symbol"]
            self.sold_exchange = meta.get("exchange", "NFO")
            self.sold_entry_price = float(pos["avg_entry_price"])
            self.sold_option_type = "CE" if pos["symbol"].endswith("CE") else "PE"
            self.sold_strike = meta.get("strike")
            expiry_str = meta.get("expiry")
            self.sold_expiry = date.fromisoformat(expiry_str) if expiry_str else None
            runner.dispatcher.add_instruments([{"instrument_token": token, "symbol": pos["symbol"]}])
        if found_long:
            token, pos = found_long
            meta = pos["metadata"] or {}
            self.hedge_token, self.hedge_symbol = token, pos["symbol"]
            self.hedge_exchange = meta.get("exchange", "NFO")
            self.hedge_entry_price = float(pos["avg_entry_price"])
            self.hedge_strike = meta.get("strike")
            runner.dispatcher.add_instruments([{"instrument_token": token, "symbol": pos["symbol"]}])

        if found_short or found_long:
            self.entry_trend = persisted.get("entry_trend") if persisted else None
            if found_short and found_long:
                logger.info(
                    "%s: resumed with both spread legs open: short %s / hedge %s",
                    runner.deployment_name, self.sold_symbol, self.hedge_symbol,
                )
            else:
                logger.warning(
                    "%s: resumed with only ONE spread leg open (%s) -- asymmetric "
                    "state. The early-close capture check needs BOTH legs' live "
                    "prices and is skipped until this is reconciled; the "
                    "SuperTrend-flip exit is unaffected and still applies.",
                    runner.deployment_name, self.sold_symbol or self.hedge_symbol,
                )

        # PRIMARY seeding path: fresh, gap-free 1H candles straight from
        # Kite's REST API, replayed through BOTH indicators together
        # right now -- see fetch_seed_from_kite's own docstring
        # (pivot_supertrend.py) for the identical reasoning, applied
        # here on every on_start, not just a cold deploy.
        try:
            seed_candles = await _fetch_hourly_seed_from_kite(
                runner.dispatcher, self.instrument_token, HOURLY_AUTOSEED_LOOKBACK_DAYS,
            )
            fresh_st = SuperTrendState(period=self.st_period, multiplier=self.st_multiplier, atr_method="wilder")
            fresh_ema = EMAState(period=self.ema_period)
            prev_close = None
            touched_during_replay = False
            for c in seed_candles:
                fresh_st.update(c)
                v = fresh_ema.update(c)
                if v is not None:
                    crossed = prev_close is not None and (prev_close - v) * (c["close"] - v) < 0
                    if crossed or (c["low"] <= v <= c["high"]):
                        touched_during_replay = True
                prev_close = c["close"]
            self.st = fresh_st
            self.ema = fresh_ema
            self._prev_close = prev_close

            # A cold replay can't distinguish "touched since the actual
            # last exit" from "touched at some point in this whole
            # lookback window" -- only trusted when there's no persisted
            # flag to defer to instead (a genuinely fresh deploy, where
            # it's moot anyway since no position has ever existed to
            # gate re-entry against) or a pre-this-feature open position
            # being resumed for the first time (best-effort, better than
            # nothing).
            if persisted is None:
                self.touched_ema_since_exit = touched_during_replay or not (self.sold_token or self.hedge_token)

            logger.info(
                "%s: auto-seeded EMA(%d)/SuperTrend(%d,%s) live from Kite "
                "(%d candle(s)) -> trend=%s ema=%s",
                runner.deployment_name, self.ema_period, self.st_period, self.st_multiplier,
                len(seed_candles), fresh_st.trend, round(fresh_ema.value, 2) if fresh_ema.value is not None else None,
            )
            if (self.sold_token or self.hedge_token) and self.entry_trend is None:
                # An open position but no persisted entry_trend (e.g. an
                # upgrade from before this field existed) -- best-effort:
                # assume the CURRENT trend is also the entry trend, so at
                # least the very next real flip is caught correctly, even
                # though a flip that already happened before this restart
                # is missed once.
                self.entry_trend = fresh_st.trend
                logger.warning(
                    "%s: resumed with an open position but no persisted "
                    "entry_trend -- assuming the current trend (%s) as a "
                    "best-effort fallback", runner.deployment_name, fresh_st.trend,
                )
            return
        except NoKiteSession:
            logger.warning(
                "%s: no Kite session yet — cannot auto-seed live; falling back "
                "to persisted indicator state / cold start", runner.deployment_name,
            )
        except Exception:
            logger.exception(
                "%s: live auto-seed from Kite failed — falling back to "
                "persisted indicator state / cold start", runner.deployment_name,
            )

        # FALLBACK: whatever this deployment last persisted for the
        # indicators themselves.
        if persisted and self._restore_indicator_state(runner, persisted):
            return

        logger.warning(
            "%s: no live Kite seed, no persisted indicator state — EMA/"
            "SuperTrend cold-starting from live ticks only (no entries "
            "until both warm up).", runner.deployment_name,
        )

    def _restore_indicator_state(self, runner, state: dict) -> bool:
        try:
            self.st = SuperTrendState.from_snapshot(state["supertrend"])
            self.ema = EMAState.from_snapshot(state["ema"])
            self._prev_close = state.get("prev_close")
        except (KeyError, TypeError, ValueError):
            logger.exception(
                "%s: persisted indicator state was malformed — ignoring it, "
                "cold-starting EMA/SuperTrend instead", runner.deployment_name,
            )
            return False
        logger.info(
            "%s: resumed EMA/SuperTrend from persisted state (trend=%s, ema=%s)",
            runner.deployment_name, self.st.trend,
            round(self.ema.value, 2) if self.ema.value is not None else None,
        )
        return True

    def get_persistable_state(self) -> Optional[dict]:
        if self.st.trend is None and self.ema.value is None:
            return None
        return {
            "version": 1,
            "supertrend": self.st.snapshot(),
            "ema": self.ema.snapshot(),
            "prev_close": self._prev_close,
            "entry_trend": self.entry_trend,
            "today": self.today.isoformat() if self.today else None,
            "touched_ema_since_exit": self.touched_ema_since_exit,
            "gap_exit_pending": self._gap_exit_pending,
            "gap_trading_days_elapsed": self._gap_trading_days_elapsed,
            "lots_per_trade": self.lots_per_trade,
            "cum_pnl_since_stepup": self.cum_pnl_since_stepup,
        }

    def get_status_fields(self) -> Optional[list]:
        return _status_fields(self.st, self.ema, self.lots_per_trade)

    @staticmethod
    def status_fields_from_state(state: dict) -> Optional[list]:
        if not state:
            return None
        try:
            st = SuperTrendState.from_snapshot(state["supertrend"])
            ema = EMAState.from_snapshot(state["ema"])
        except (KeyError, TypeError, ValueError):
            return None
        return _status_fields(st, ema, state.get("lots_per_trade"))

    async def on_post_market_checkpoint(self, runner) -> None:
        """Re-fetches clean 1H candles and recomputes BOTH indicators
        fresh, same reasoning as pivot_supertrend_options.py's identical
        method. Does NOT touch entry_trend/touched_ema_since_exit/gap
        state/lots_per_trade or the open legs themselves -- none of that
        is affected by a recursive-indicator resync."""
        try:
            seed_candles = await _fetch_hourly_seed_from_kite(
                runner.dispatcher, self.instrument_token, HOURLY_AUTOSEED_LOOKBACK_DAYS,
            )
        except NoKiteSession:
            logger.warning("%s: post-market checkpoint skipped — no Kite session",
                           runner.deployment_name)
            return
        except Exception:
            logger.exception(
                "%s: post-market checkpoint's Kite fetch failed — keeping "
                "existing live state", runner.deployment_name,
            )
            return

        fresh_st = SuperTrendState(period=self.st_period, multiplier=self.st_multiplier, atr_method="wilder")
        fresh_ema = EMAState(period=self.ema_period)
        prev_close = None
        for c in seed_candles:
            fresh_st.update(c)
            fresh_ema.update(c)
            prev_close = c["close"]
        if fresh_st.trend is None or fresh_ema.value is None:
            logger.warning(
                "%s: post-market checkpoint's fresh replay never warmed up "
                "(unexpected — leaving existing live state untouched)",
                runner.deployment_name,
            )
            return
        old_trend = self.st.trend
        self.st = fresh_st
        self.ema = fresh_ema
        self._prev_close = prev_close
        logger.info(
            "%s: post-market checkpoint — resynced EMA/SuperTrend from live "
            "Kite data (trend %s -> %s)", runner.deployment_name, old_trend, fresh_st.trend,
        )

    # ── Tick consumption ────────────────────────────────────────────────

    async def on_tick(self, runner, tick: dict) -> None:
        ts = tick.get("exchange_timestamp")
        price = tick.get("last_price")
        if ts is None or price is None:
            return

        day = ts.date()
        if self.today is None:
            self.today = day
        elif day != self.today:
            self._roll_over_day()
            self.today = day

        completed = self.aggregator.add_tick(ts, price)
        if completed is None:
            return

        await self._on_candle_closed(runner, completed, ts)

    def _roll_over_day(self) -> None:
        self._late_signal_logged_today = False
        if self._gap_exit_pending:
            self._gap_trading_days_elapsed += 1
            if self._gap_trading_days_elapsed > self.post_exit_gap_trading_days:
                self._gap_exit_pending = False

    async def _on_candle_closed(self, runner, candle: dict, now: datetime) -> None:
        stale = is_stale_candle_close(candle["date"], self.aggregator.interval_minutes, now)
        if stale:
            logger.warning(
                "%s: candle closed at %s but only reached this strategy at "
                "%s (%.0f min late) — likely a WebSocket reconnect gap. "
                "Absorbing this candle's real OHLC into EMA/SuperTrend, but "
                "skipping any fresh entry decision off data this stale (an "
                "exit is never blocked by this).",
                runner.deployment_name, candle["date"], now,
                (now - candle["date"]).total_seconds() / 60,
            )

        prev_close = self._prev_close
        new_ema = self.ema.update(candle)
        new_trend = self.st.update(candle)
        if new_ema is not None and not self.touched_ema_since_exit:
            crossed = prev_close is not None and (prev_close - new_ema) * (candle["close"] - new_ema) < 0
            touched = candle["low"] <= new_ema <= candle["high"]
            if crossed or touched:
                self.touched_ema_since_exit = True
                logger.info("%s: price touched/crossed EMA(%d) again — re-entry gate cleared",
                           runner.deployment_name, self.ema_period)
        self._prev_close = candle["close"]

        t = candle["date"].time()
        # Checked against this candle's own BUCKET END, not its bucket
        # START, unlike pivot_supertrend.py's identical-in-spirit gate on
        # 5-min candles. That distinction matters here specifically
        # because CandleAggregator floors every bucket to a fixed
        # boundary: on a 5-min grid the day's first post-open candle's
        # bucket-start naturally IS 09:15 (a tick at 09:15 floors to
        # itself), so comparing the start against market_open_time="09:15"
        # works by construction. On a 60-min grid, EVERY day's first
        # candle floors to 09:00 regardless of when trading actually
        # started inside it — comparing that start against "09:15" would
        # make after_open false for the entire first hour, every single
        # day, forever. Comparing the bucket's END instead asks the
        # actually-intended question ("has this candle's own window
        # reached market open") correctly regardless of granularity.
        candle_end = candle["date"] + timedelta(minutes=self.candle_interval_minutes)
        after_open = self.market_open_time is None or candle_end.time() >= self.market_open_time
        before_cutoff = t < self.entry_signal_cutoff_time
        st_value = (self.st.final_lower if new_trend == "up" else self.st.final_upper) if new_trend else None

        has_position = self.sold_token is not None or self.hedge_token is not None
        if has_position:
            if before_cutoff and self.entry_trend is not None and new_trend is not None and new_trend != self.entry_trend:
                trigger_values = {
                    "close": round(candle["close"], 2),
                    "ema": round(new_ema, 2) if new_ema is not None else None,
                    "entry_trend": self.entry_trend, "new_trend": new_trend,
                    "supertrend_value": round(st_value, 2) if st_value is not None else None,
                }
                await self._exit(runner, candle, "st_flip", trigger_values)
                return
            early = self._check_early_close(runner)
            if early is not None:
                await self._exit(runner, candle, "early_close_capture", early)
            return

        if stale or new_trend is None or new_ema is None or not after_open:
            return
        if self._gap_exit_pending:
            return
        if not self.touched_ema_since_exit:
            return

        close = candle["close"]
        bearish = new_trend == "down" and close < new_ema
        bullish = new_trend == "up" and close > new_ema
        if not (bearish or bullish):
            return
        side = "bearish" if bearish else "bullish"

        trigger_values = {
            "close": round(close, 2), "ema": round(new_ema, 2),
            "supertrend_trend": new_trend,
            "supertrend_value": round(st_value, 2) if st_value is not None else None,
        }

        if not before_cutoff:
            if not self._late_signal_logged_today:
                logger.info(
                    "%s: %s signal detected at/after entry_signal_cutoff_time "
                    "(%s) — deferring to tomorrow's open, entering only if the "
                    "condition still holds then", runner.deployment_name, side,
                    self.entry_signal_cutoff_time,
                )
                self._late_signal_logged_today = True
            return

        await self._enter(runner, candle, side, trigger_values)

    # ── Entry ────────────────────────────────────────────────────────────

    async def _resolve_hedge_leg(self, sold_leg, option_type: str, expiry: date):
        """Hedge leg is a PURE points-offset from the sold leg's own
        strike, snapped to the nearest listed strike — see module
        docstring's PROVISIONAL note on hedge_width_points for why this
        is deliberately NOT adaptive/volatility-scaled. Same points-away
        -from-the-short convention, and same snap-to-nearest-listed-
        strike mechanics, strangle_monthly_v2.py's own hedge placement
        already uses (`_resolve_and_open_hedge`'s point_distance branch)
        — direction: a CE hedge sits ABOVE the sold CE strike, a PE
        hedge sits BELOW the sold PE strike."""
        sign = 1 if option_type == "CE" else -1
        target_strike = sold_leg.strike + sign * self.hedge_width_points
        strikes = await self.resolver.list_strikes(self.underlying, expiry, option_type)
        nearest = min(strikes, key=lambda s: abs(s - target_strike))
        hedge_leg = await self.resolver.get_leg(self.underlying, expiry, nearest, option_type)
        price = await self.resolver.get_ltp(hedge_leg)
        return hedge_leg, price

    async def _enter(self, runner, candle: dict, side: str, trigger_values: dict) -> None:
        option_type = "CE" if side == "bearish" else "PE"
        try:
            expiry = await self.resolver.resolve_expiry(self.underlying, EXPIRY_SELECTOR)
            sold_leg = await self.resolver.get_leg_by_premium(self.underlying, expiry, option_type, self.target_sell_premium)
            hedge_leg, hedge_price = await self._resolve_hedge_leg(sold_leg, option_type, expiry)
        except NoKiteSession:
            logger.warning(
                "%s: entry signal (%s -> sell %s spread) but no Kite session "
                "yet — skipping", runner.deployment_name, side, option_type,
            )
            return
        except Exception:
            logger.exception(
                "%s: failed to resolve/price the %s spread for entry — skipping",
                runner.deployment_name, option_type,
            )
            return

        sold_price = sold_leg.last_price
        qty = self.lots_per_trade * sold_leg.lot_size
        runner.dispatcher.add_instruments([
            {"instrument_token": sold_leg.instrument_token, "symbol": sold_leg.tradingsymbol},
            {"instrument_token": hedge_leg.instrument_token, "symbol": hedge_leg.tradingsymbol},
        ])

        # Hedge opened FIRST, sold leg SECOND — deliberately the reverse
        # of the "usual" short-then-hedge order (see strangle_monthly_v2's
        # own `reversed_hedge_order` for the same idea, used there on its
        # roll/replace path). Opening the short before its hedge exists
        # means a failed HEDGE fill (its buy CAN fail on InsufficientCash
        # — a short's own sell-to-open never can in this paper-trading
        # model, see queries.record_fill) would leave the position
        # genuinely naked for however long that takes to notice, directly
        # against this strategy's own stated identity ("a real hedge leg
        # — never naked"). Hedge-first means a failed hedge fill means
        # NOTHING gets entered at all.
        await runner.buy(
            hedge_leg.tradingsymbol, hedge_leg.instrument_token, qty, hedge_price, candle["date"],
            reason="entry",
            metadata=build_trade_meta(
                trigger=f"ema_st_{side}", action=f"buy_open_{option_type}_hedge",
                trigger_values=trigger_values,
                resulting_state={"leg": "hedge", "option_type": option_type,
                                "strike": hedge_leg.strike, "entry_price": round(hedge_price, 2)},
                target_basis={"selection_basis": "points_from_sold", "hedge_width_points": self.hedge_width_points,
                            "sold_strike": sold_leg.strike, "selected_strike": hedge_leg.strike,
                            "fill_premium": round(hedge_price, 2)},
                leg="hedge", exchange=hedge_leg.exchange, strike=hedge_leg.strike,
                expiry=expiry.isoformat(), option_type=option_type,
            ),
        )
        await runner.sell(
            sold_leg.tradingsymbol, sold_leg.instrument_token, qty, sold_price, candle["date"],
            reason="entry",
            metadata=build_trade_meta(
                trigger=f"ema_st_{side}", action=f"sell_open_{option_type}",
                trigger_values=trigger_values,
                resulting_state={"leg": "sold", "option_type": option_type,
                                "strike": sold_leg.strike, "entry_price": round(sold_price, 2)},
                target_basis={"selection_basis": "target_premium", "target_premium": self.target_sell_premium,
                            "selected_strike": sold_leg.strike, "fill_premium": round(sold_price, 2)},
                leg="sold", exchange=sold_leg.exchange, strike=sold_leg.strike,
                expiry=expiry.isoformat(), option_type=option_type,
            ),
        )

        self.hedge_token, self.hedge_symbol, self.hedge_exchange = \
            hedge_leg.instrument_token, hedge_leg.tradingsymbol, hedge_leg.exchange
        self.hedge_entry_price, self.hedge_strike = hedge_price, hedge_leg.strike
        self.sold_token, self.sold_symbol, self.sold_exchange = \
            sold_leg.instrument_token, sold_leg.tradingsymbol, sold_leg.exchange
        self.sold_entry_price, self.sold_strike, self.sold_option_type = sold_price, sold_leg.strike, option_type
        self.sold_expiry = expiry
        self.entry_trend = self.st.trend
        self.position_side = side

        net_credit = sold_price - hedge_price
        common_meta = {"strike": sold_leg.strike, "hedge_strike": hedge_leg.strike,
                       "expiry": expiry.isoformat(), "option_type": option_type}
        await runner.notify_execution(
            "entry",
            f"Sold {option_type} spread ({side}) — short {sold_leg.tradingsymbol}@{sold_price:.2f}, "
            f"hedge {hedge_leg.tradingsymbol}@{hedge_price:.2f} (net credit {net_credit:.2f}, "
            f"{self.lots_per_trade} lot(s))",
            metadata=common_meta,
        )
        logger.info(
            "%s: entered %s %s spread — short %s@%.2f / hedge %s@%.2f (net credit %.2f)",
            runner.deployment_name, side, option_type, sold_leg.tradingsymbol, sold_price,
            hedge_leg.tradingsymbol, hedge_price, net_credit,
        )

    # ── Exit ─────────────────────────────────────────────────────────────

    def _check_early_close(self, runner) -> Optional[dict]:
        """See module docstring's Secondary exit section for the exact
        definition chosen: captured = net credit at entry minus net cost
        to close now, as a fraction of the entry net credit. Returns the
        trigger_values dict (captured_pct/days_to_expiry and the raw
        numbers behind them, per explicit instruction, so a forward-
        tested early close can be checked against the config threshold
        after the fact) if the exit condition is met, else None."""
        if self.sold_token is None or self.hedge_token is None or self.sold_expiry is None:
            return None   # asymmetric resume, or no expiry on record -- can't safely evaluate
        if self.today is None:
            return None
        sold_now = runner.dispatcher.last_prices.get(self.sold_token)
        hedge_now = runner.dispatcher.last_prices.get(self.hedge_token)
        if sold_now is None or hedge_now is None:
            return None
        net_credit_entry = self.sold_entry_price - self.hedge_entry_price
        if net_credit_entry <= 0:
            return None   # degenerate/edge case -- no meaningful "% captured" of a non-positive credit
        net_cost_now = sold_now - hedge_now
        captured_pct = (net_credit_entry - net_cost_now) / net_credit_entry
        days_to_expiry = (self.sold_expiry - self.today).days
        if captured_pct >= self.early_close_capture_pct and days_to_expiry <= self.early_close_max_days_to_expiry:
            return {
                "captured_pct": round(captured_pct, 4), "days_to_expiry": days_to_expiry,
                "net_credit_entry": round(net_credit_entry, 2), "net_cost_now": round(net_cost_now, 2),
                "early_close_capture_pct": self.early_close_capture_pct,
                "early_close_max_days_to_expiry": self.early_close_max_days_to_expiry,
            }
        return None

    async def _exit_price(self, runner, token: int, symbol: str, exchange: str, entry_price: float, reason: str) -> float:
        try:
            return await self.resolver.get_ltp(f"{exchange}:{symbol}")
        except Exception:
            price = runner.dispatcher.last_prices.get(token)
            if price is None:
                logger.warning(
                    "%s: no live/last price for %s on exit (%s) — using entry "
                    "price %.2f (zero P&L on this leg)",
                    runner.deployment_name, symbol, reason, entry_price,
                )
                return float(entry_price)
            logger.warning(
                "%s: LTP fetch failed for %s on exit (%s) — using dispatcher's "
                "last known tick price %.2f instead",
                runner.deployment_name, symbol, reason, price,
            )
            return price

    async def _exit(self, runner, candle: dict, reason: str, trigger_values: dict) -> None:
        realized = 0.0
        exit_date = candle["date"].date()

        if self.sold_token is not None:
            price = await self._exit_price(runner, self.sold_token, self.sold_symbol,
                                           self.sold_exchange, self.sold_entry_price, reason)
            pos = runner.open_positions.get(self.sold_token)
            if pos is not None:
                result = await runner.buy(
                    self.sold_symbol, self.sold_token, float(pos["qty"]), price, candle["date"],
                    reason=reason,
                    metadata=build_trade_meta(
                        trigger=reason, action=f"buy_close_{self.sold_option_type}",
                        trigger_values=trigger_values, resulting_state={"position": "flat"},
                    ),
                )
                if result.get("realized_pnl") is not None:
                    realized += result["realized_pnl"]
            runner.dispatcher.release_instruments([self.sold_token])
            self.sold_token = self.sold_symbol = self.sold_exchange = None
            self.sold_entry_price = self.sold_strike = self.sold_option_type = None

        if self.hedge_token is not None:
            price = await self._exit_price(runner, self.hedge_token, self.hedge_symbol,
                                           self.hedge_exchange, self.hedge_entry_price, reason)
            pos = runner.open_positions.get(self.hedge_token)
            if pos is not None:
                result = await runner.sell(
                    self.hedge_symbol, self.hedge_token, float(pos["qty"]), price, candle["date"],
                    reason=reason,
                    metadata=build_trade_meta(
                        trigger=reason, action="sell_close_hedge",
                        trigger_values=trigger_values, resulting_state={"position": "flat"},
                    ),
                )
                if result.get("realized_pnl") is not None:
                    realized += result["realized_pnl"]
            runner.dispatcher.release_instruments([self.hedge_token])
            self.hedge_token = self.hedge_symbol = self.hedge_exchange = None
            self.hedge_entry_price = self.hedge_strike = None

        # Post-exit gap rule -- see module docstring's ENTRY section. Only
        # armed when the close landed ON this position's own actual
        # expiry date, resolved dynamically at ENTRY time (self.sold_expiry),
        # never a guessed/hardcoded weekday.
        if self.sold_expiry is not None and exit_date == self.sold_expiry:
            self._gap_exit_pending = True
            self._gap_trading_days_elapsed = 0
            logger.info(
                "%s: closed on its own expiry day (%s) — new entries paused "
                "for %d trading day(s)", runner.deployment_name, exit_date,
                self.post_exit_gap_trading_days,
            )

        self.touched_ema_since_exit = False
        self.entry_trend = None
        self.sold_expiry = None
        self.position_side = None

        # Step-up sizing -- see module docstring's POSITION SIZING
        # section for the exact interpretation. Excess above the
        # threshold carries forward (not reset to 0), and a `while`
        # (not `if`) lets one outsized trade step up more than once.
        self.cum_pnl_since_stepup += realized
        stepped_up = 0
        while self.cum_pnl_since_stepup > self.capital_per_lot:
            self.lots_per_trade += 1
            self.cum_pnl_since_stepup -= self.capital_per_lot
            stepped_up += 1
        if stepped_up:
            logger.info(
                "%s: cumulative P&L since the last step-up crossed "
                "capital_per_lot (%.2f) %d time(s) — lots_per_trade now %d "
                "(applies to the NEXT entry only)", runner.deployment_name,
                self.capital_per_lot, stepped_up, self.lots_per_trade,
            )

        await runner.notify_execution(
            "exit", f"{reason}: closed spread (realized {realized:+.2f})",
            metadata={**trigger_values, "realized_pnl": round(realized, 2)},
        )
        logger.info("%s: exited spread (%s), realized=%.2f", runner.deployment_name, reason, realized)

    async def on_stop(self, runner) -> None:
        tokens = [t for t in (self.sold_token, self.hedge_token) if t is not None]
        if tokens:
            runner.dispatcher.release_instruments(tokens)
        logger.info(
            "%s: strategy stopped (trend=%s, ema=%s, sold=%s, hedge=%s)",
            runner.deployment_name, self.st.trend,
            round(self.ema.value, 2) if self.ema.value is not None else None,
            self.sold_symbol, self.hedge_symbol,
        )
