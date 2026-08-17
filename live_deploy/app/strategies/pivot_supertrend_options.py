"""
live_deploy — Pivot Points + SuperTrend(7,3), but SELLING OPTIONS instead
of trading the underlying. Live paper-trading only — there is no
backtested version of this variant in tg_int_st_pp; the signal engine
(pivots, SuperTrend, candle aggregation) is the identical, already-proven
one from `pivot_supertrend.py`, only WHAT gets traded on each signal is
different.

RULES — same signal timing as pivot_supertrend, different execution:
  Long signal  (5-min close above R1/R2/R3, ST green) -> SELL THIS_WEEK
      ATM PE (bullish: profit if NIFTY stays above the strike; a put
      seller is a defined-premium bull).
  Short signal (5-min close below S1/S2/S3, ST red)   -> SELL THIS_WEEK
      ATM CE (bearish: profit if NIFTY stays below the strike).
  Exit — BUY BACK (close) whichever leg is open, on a SuperTrend flip or
      force-exit time, same triggers as pivot_supertrend. Both entry and
      exit still execute at the NEXT candle's open (signal timing is
      unchanged), but the PRICE used is the option's own live LTP at
      that moment (fetched via a REST call), not the underlying's price
      — the underlying's candle open is only used to decide WHEN to act.
  Only 1 open option position at a time, matching the original's "one
  lot in, one lot out" — a fresh entry can fire immediately after an
  exit closes the previous leg.

  NO entry before `market_open_time` (config, default 09:15) — see
  pivot_supertrend.py's own module docstring for the full reasoning
  (no `entry_time` schedule here at all, so without this floor, a
  pivot/trend combination already "ready" from a prior day could queue
  a real entry off a pre-market indicative-price tick). Only gates
  fresh signal DETECTION; exits and an already-queued pending entry are
  unaffected.

WHY SELL, NOT BUY: this always SELLS a leg to open and BUYS to close it,
regardless of signal direction — we're always writing premium, just
choosing which side (PE on a bullish signal, CE on a bearish one) based
on the same directional read the original strategy used to go long/short
the index. This maps onto the existing paper-trading ledger with zero
schema/query changes: `record_fill` already treats "sell first, buy
later" as a short position with realized_pnl = qty*(sell_price -
buy_price) — exactly premium collected minus premium paid to close,
which is precisely option-selling P&L. No margin model exists here (same
simplification the whole live_deploy paper-trading engine already makes
for the underlying) — selling a leg always succeeds cash-wise (premium
is pure credit), buying back to close is still cash-checked for real,
same as any other buy.

WHY THIS IS ITS OWN FILE rather than a flag on pivot_supertrend.py: the
execution side (leg resolution, dynamic option-token subscription,
sell-to-open/buy-to-close instead of buy-to-open/sell-to-close) is
different enough that folding it into one class via a config flag would
make both harder to read. The signal engine itself (pivot formulas,
SuperTrendState, CandleAggregator, seeding, day-rollover) is NOT
duplicated — it's imported straight from pivot_supertrend.py, so both
strategies share the exact same tested math; only the (much simpler,
non-numeric) candle-close orchestration and the trade-execution methods
are separate.

CONFIG (on top of the seeding options — prev_day_ohlc / seed_candles /
supertrend_seed — which work identically to pivot_supertrend, see that
module's docstring):
  "instrument_tokens": [<single token>] — the UNDERLYING's token (e.g.
      NIFTY 50's 256265), used ONLY to generate the pivot/SuperTrend
      signal from its own tick stream. The options actually traded are
      resolved dynamically and are NOT this token.
  "symbol": underlying's display name, e.g. "NIFTY 50" — logging only.
  "options_underlying": REQUIRED, the `name` field on the options chain
      itself, e.g. "NIFTY" (note: NOT "NIFTY 50" — that's the spot
      index's own tradingsymbol, options are listed under the shorter
      "NIFTY"). See app/options/resolver.py's INDEX_SPOT_SYMBOL mapping.
  "expiry_selector": "THIS_WEEK" (default) — any selector OptionsResolver
      accepts (THIS_WEEK/NEXT_WEEK/THIS_MONTH/NEXT_MONTH/int/date).
  "lots_per_trade": 1 (default) — options only trade in whole lots;
      each entry sells exactly this many lots of whatever the current
      lot size is for options_underlying.
  "pivot_type" / "atr_smoothing" / "force_exit_time" / "market_open_time":
      identical meaning and defaults to pivot_supertrend — see that
      module's CONFIG section for market_open_time specifically.

A dynamic instrument subscription is added to the dispatcher for
whichever option leg is currently open (so its live LTP feeds
dispatcher.last_prices for mark-to-market / force_close-on-stop) and
released the moment that leg closes — or on pause/stop, re-subscribed
automatically on resume if a position is still open in the DB. This is
managed by the strategy itself (not DeploymentManager, which only knows
about the static config["instrument_tokens"] used for the signal) since
which option token is "this deployment's instrument" changes every
trade.
"""

import logging
from datetime import date
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver
from .pivot_supertrend import (
    R_KEYS,
    S_KEYS,
    ST_MULTIPLIER,
    ST_PERIOD,
    CandleAggregator,
    SuperTrendState,
    _parse_hhmm,
    apply_seed_to_state,
    compute_pivots,
)
from .registry import register_strategy
from .trade_meta import build_trade_meta

logger = logging.getLogger("live_deploy.strategies.pivot_supertrend_options")


@register_strategy(
    "pivot_supertrend_options",
    description="Same pivot points + SuperTrend(7,3) signal as pivot_supertrend, "
               "but SELLS options instead of trading the underlying: a long "
               "signal sells THIS_WEEK ATM PE, a short signal sells THIS_WEEK "
               "ATM CE, buying back to close on a SuperTrend flip or "
               "force-exit. Live paper-trading only, no backtested version.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "expiry_selector": "THIS_WEEK",
        "lots_per_trade": 1,
        "pivot_type": "classic",
        "atr_smoothing": "wilder",
        "force_exit_time": "15:00",
        "market_open_time": "09:15",
        "prev_day_ohlc": None,
        "seed_candles": None,
        "supertrend_seed": None,
    },
)
class PivotSupertrendOptionsStrategy(StrategyBase):

    async def on_start(self, runner) -> None:
        cfg = runner.config
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "pivot_supertrend_options requires config.instrument_tokens to "
                f"be a ONE-ELEMENT list — the underlying's token used only for "
                f"signal generation — got {tokens!r}"
            )
        self.instrument_token = tokens[0]
        self.symbol = cfg.get("symbol", str(self.instrument_token))

        self.options_underlying = cfg.get("options_underlying")
        if not self.options_underlying:
            raise ValueError(
                "pivot_supertrend_options requires config.options_underlying "
                "(the options chain's own `name`, e.g. \"NIFTY\" — NOT the "
                "spot tradingsymbol \"NIFTY 50\")"
            )
        self.expiry_selector = cfg.get("expiry_selector", "THIS_WEEK")
        self.lots_per_trade = int(cfg.get("lots_per_trade") or 1)
        if self.lots_per_trade < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {self.lots_per_trade}")

        self.pivot_type = cfg.get("pivot_type", "classic")
        self.atr_method = cfg.get("atr_smoothing", "wilder")
        self.force_exit_time = _parse_hhmm(cfg.get("force_exit_time", "15:00"))
        self.market_open_time = _parse_hhmm(cfg.get("market_open_time", "09:15"))

        self.aggregator = CandleAggregator(interval_minutes=5)
        self.st = SuperTrendState(period=ST_PERIOD, multiplier=ST_MULTIPLIER,
                                  atr_method=self.atr_method)

        self.today = None
        self.today_high: Optional[float] = None
        self.today_low: Optional[float] = None
        self.today_last_close: Optional[float] = None

        self.prev_day_ohlc: Optional[dict] = cfg.get("prev_day_ohlc")
        self.pivots: Optional[dict] = None

        # Both hold trigger_values captured at DETECTION time, not bare
        # flags — see pivot_supertrend.py's identical comment; same
        # "decide on close, act on next open" timing applies here.
        self.pending_exit: Optional[dict] = None
        self.pending_entry: Optional[dict] = None
        self.prev_trend: Optional[str] = None

        self.resolver = OptionsResolver(runner.dispatcher)

        # Which option leg (if any) is currently open. Unlike
        # pivot_supertrend, the traded instrument_token isn't fixed — a
        # fresh ATM leg is resolved on every entry — so this is tracked
        # explicitly rather than reusing self.instrument_token (which is
        # the UNDERLYING's token and never itself gets a position here).
        self.active_leg_token: Optional[int] = None
        self.active_leg_symbol: Optional[str] = None
        self.active_leg_exchange: Optional[str] = None

        # Resume-safety: if this deployment restarted (or was
        # paused/resumed) with an option position still open in the DB,
        # reattach to it and re-subscribe for live marks — this strategy
        # only ever holds at most one position, so the first (only) open
        # position IS the active leg.
        for token, pos in runner.open_positions.items():
            self.active_leg_token = token
            self.active_leg_symbol = pos["symbol"]
            self.active_leg_exchange = (pos["metadata"] or {}).get("exchange", "NFO")
            runner.dispatcher.add_instruments(
                [{"instrument_token": token, "symbol": pos["symbol"]}]
            )
            logger.info(
                "%s: resumed with an already-open option position: %s (qty=%s)",
                runner.deployment_name, pos["symbol"], pos["qty"],
            )
            break

        # Prefer whatever this deployment last persisted (see
        # get_persistable_state below) over the static config seed —
        # same reasoning as pivot_supertrend.py's identical block. Only
        # a first-ever start falls through to the config seed.
        persisted = await runner.load_state()
        if persisted and self._restore_from_state(runner, persisted):
            return

        seeded_trend, derived = apply_seed_to_state(
            runner.deployment_name, self.st, self.atr_method, cfg, self.prev_day_ohlc,
            log=logger,
        )
        if derived:
            self.prev_day_ohlc = derived

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
        self.prev_trend = self.st.trend

    def _restore_from_state(self, runner, state: dict) -> bool:
        """See pivot_supertrend.py's identical method for the full
        rationale. Returns False (nothing mutated) on anything
        malformed, so the caller falls through to the config-seed path."""
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
            # See pivot_supertrend.py's identical restore block for the
            # full rationale — without this, a restart landing between a
            # flip/break's DETECTION and its EXECUTION silently dropped
            # it; for pending_exit specifically that can leave a
            # position open indefinitely (self.prev_trend already
            # reflects the post-flip trend, so the same flip never gets
            # re-detected either), not just delayed the way a lost
            # pending_entry harmlessly would be.
            self.pending_exit = state.get("pending_exit")
            self.pending_entry = state.get("pending_entry")
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
        Note this ONLY persists the SuperTrend/pivot signal-generation
        state -- the currently-open option leg (if any) doesn't need to
        be in here at all, since that's already resume-safe via the DB
        (see the "Resume-safety" reattach block above, which reads it
        straight from runner.open_positions on every on_start)."""
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
            "pending_exit": self.pending_exit,
            "pending_entry": self.pending_entry,
        }

    # ── Tick consumption — identical control flow to pivot_supertrend ──

    async def on_tick(self, runner, tick: dict) -> None:
        ts = tick.get("exchange_timestamp")
        price = tick.get("last_price")
        if ts is None or price is None:
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
        # Lower bound — see market_open_time in pivot_supertrend.py's
        # own CONFIG/RULES for the full reasoning. Only combined into
        # step 5 (fresh entry DETECTION) below.
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
            if self.active_leg_token is not None:
                await self._exit(runner, candle, "force_exit", {
                    "candle_time": t.isoformat(), "force_exit_time": self.force_exit_time.isoformat(),
                })

        # 4 — advance SuperTrend, detect a flip — trigger_values captured
        # at detection time (see pivot_supertrend.py's identical comment).
        prev_trend_before_update = self.prev_trend
        new_trend = self.st.update(candle)
        if new_trend is not None:
            if prev_trend_before_update is not None and new_trend != prev_trend_before_update:
                if self.active_leg_token is not None:
                    self.pending_exit = {
                        "trigger_values": {
                            "prev_trend": prev_trend_before_update, "new_trend": new_trend,
                            "close": round(candle["close"], 2),
                            "final_upper": round(self.st.final_upper, 2) if self.st.final_upper is not None else None,
                            "final_lower": round(self.st.final_lower, 2) if self.st.final_lower is not None else None,
                        },
                    }
            self.prev_trend = new_trend

        # 5 — detect a fresh entry signal (flat, pivots known, ST ready, within the entry window)
        if self.active_leg_token is None and self.pivots is not None \
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

    # ── Execution — sell an option leg to open, buy it back to close ───

    async def _enter(self, runner, candle: dict, side: str, trigger_values: dict) -> None:
        option_type = "PE" if side == "long" else "CE"   # bullish -> sell puts, bearish -> sell calls
        try:
            leg = await self.resolver.get_atm_leg(
                self.options_underlying, self.expiry_selector, option_type,
            )
            price = await self.resolver.get_ltp(leg)
        except NoKiteSession:
            logger.warning(
                "%s: entry signal (%s -> sell %s) but no Kite session yet — skipping",
                runner.deployment_name, side, option_type,
            )
            return
        except Exception:
            logger.exception(
                "%s: failed to resolve/price the %s ATM leg for entry — skipping",
                runner.deployment_name, option_type,
            )
            return

        qty = self.lots_per_trade * leg.lot_size
        runner.dispatcher.add_instruments(
            [{"instrument_token": leg.instrument_token, "symbol": leg.tradingsymbol}]
        )

        meta = build_trade_meta(
            trigger="pivot_break_long" if side == "long" else "pivot_break_short",
            action=f"sell_open_{option_type}",
            trigger_values=trigger_values,
            resulting_state={"leg": option_type, "strike": leg.strike, "symbol": leg.tradingsymbol},
            target_basis={
                "selection_basis": "ATM", "selected_strike": leg.strike, "fill_premium": price,
            },
            signal_side=side, option_type=option_type, strike=leg.strike,
            expiry=leg.expiry.isoformat(), exchange=leg.exchange,
            pivots={k: round(v, 2) for k, v in self.pivots.items()},
        )
        await runner.sell(   # SELL TO OPEN — writing this leg, regardless of signal direction
            leg.tradingsymbol, leg.instrument_token, qty, price, candle["date"],
            reason="entry", metadata=meta,
        )
        self.active_leg_token = leg.instrument_token
        self.active_leg_symbol = leg.tradingsymbol
        self.active_leg_exchange = leg.exchange

    async def _exit(self, runner, candle: dict, reason: str, trigger_values: dict) -> None:
        if self.active_leg_token is None:
            return
        pos = runner.open_positions.get(self.active_leg_token)
        if pos is None:
            # Already closed out from under us (e.g. a manual force_close
            # on stop) — nothing to buy back, just drop local tracking.
            self._clear_active_leg()
            return

        key = f"{self.active_leg_exchange}:{self.active_leg_symbol}"
        try:
            price = await self.resolver.get_ltp(key)
        except Exception:
            price = runner.dispatcher.last_prices.get(self.active_leg_token)
            if price is None:
                price = float(pos["avg_entry_price"])
                logger.warning(
                    "%s: no live/last price for %s on exit (%s) — buying back "
                    "at avg_entry_price %.2f (zero P&L on this close)",
                    runner.deployment_name, self.active_leg_symbol, reason, price,
                )
            else:
                logger.warning(
                    "%s: LTP fetch failed for %s on exit (%s) — using dispatcher's "
                    "last known tick price %.2f instead",
                    runner.deployment_name, self.active_leg_symbol, reason, price,
                )

        qty = float(pos["qty"])
        option_type = "CE" if self.active_leg_symbol.endswith("CE") else "PE"
        meta = build_trade_meta(
            trigger=reason,
            action=f"buy_close_{option_type}",
            trigger_values=trigger_values,
            resulting_state={"position": "flat"},
        )
        await runner.buy(   # BUY TO CLOSE
            self.active_leg_symbol, self.active_leg_token, qty, price, candle["date"],
            reason=reason, metadata=meta,
        )
        runner.dispatcher.release_instruments([self.active_leg_token])
        self._clear_active_leg()

    def _clear_active_leg(self) -> None:
        self.active_leg_token = None
        self.active_leg_symbol = None
        self.active_leg_exchange = None

    async def on_stop(self, runner) -> None:
        # Release the dynamic option subscription (if any) whether this
        # is a pause or a full stop — on_start() re-subscribes it on
        # resume if the position is still open, so pausing loses nothing
        # but a brief gap in live marks while genuinely idle.
        if self.active_leg_token is not None:
            runner.dispatcher.release_instruments([self.active_leg_token])
        logger.info(
            "%s: strategy stopped (trend=%s, pivots=%s, open_leg=%s)",
            runner.deployment_name, self.st.trend,
            "set" if self.pivots else "none", self.active_leg_symbol,
        )
