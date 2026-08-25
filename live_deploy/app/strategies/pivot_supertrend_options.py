"""
live_deploy — Pivot Points + SuperTrend(7,3), but SELLING OPTIONS instead
of trading the underlying. Live paper-trading only — there is no
backtested version of this variant in tg_int_st_pp; the signal engine
(pivots, SuperTrend, candle aggregation) is the identical, already-proven
one from `pivot_supertrend.py`, only WHAT gets traded on each signal is
different.

RULES — same signal source as pivot_supertrend.py's shared engine,
different execution:
  Long signal  (5-min close above R1/R2/R3, ST green) -> SELL THIS_WEEK
      ATM PE (bullish: profit if NIFTY stays above the strike; a put
      seller is a defined-premium bull).
  Short signal (5-min close below S1/S2/S3, ST red)   -> SELL THIS_WEEK
      ATM CE (bearish: profit if NIFTY stays below the strike).
  Exit — BUY BACK (close) whichever leg is open, on a SuperTrend flip or
      force-exit time.
  Only 1 open option position at a time, matching the original's "one
  lot in, one lot out" — a fresh entry can fire immediately after an
  exit closes the previous leg.
  NEVER skips an entry, including on the resolved contract's OWN expiry
  day (config: `switch_to_next_week_on_expiry`, default False, same
  option/meaning as intraday_dtt_simple's identical flag) — selling an
  option that expires that same afternoon is a fast-decay, sharp-gamma
  scenario, so this decides which contract gets sold, not whether to
  trade at all: false sells the same-day-expiry contract as resolved
  (the old behavior); true re-resolves NEXT_WEEK instead, just for that
  one entry (`expiry_selector` itself stays untouched for every other
  day). See CONFIG below and `_enter`.

  EXECUTION TIMING (Step 94 fix): both entry and exit fire IMMEDIATELY
  off the same candle-close event that confirms the signal — no more
  "detect now, execute next candle" deferral. That deferral used to be
  the plain pivot_supertrend.py convention, ported here unchanged even
  though it doesn't mean the same thing for an OPTION: the underlying
  strategy prices a deferred fill off `candle["open"]`, a value already
  captured back when the deferred candle STARTED (i.e. still the
  correct, un-stale price) — but this strategy prices every fill with a
  live `get_ltp()` REST call made at the moment `_enter`/`_exit` actually
  RUNS. Under the old "wait one more candle" timing, that moment was a
  FULL candle (5 min) after the signal genuinely confirmed — a real
  option-premium price at the wrong instant, not merely a late-but-
  correct one, silently contradicting this exact file's own former
  claim that entries/exits "execute at the next candle's open" (they
  didn't — they executed at the CANDLE AFTER the next one's open, since
  that's when `_on_candle_closed` next runs). Firing the LTP fetch
  immediately, in the same call that detects the break/flip, closes
  that gap: the option price now genuinely reflects the moment the
  underlying's own signal confirmed, not five minutes later.

  NO fresh signal before `market_open_time` (config, default 09:15) —
  see pivot_supertrend.py's own module docstring for the full reasoning
  AND for why this must check the SIGNAL CANDLE's own bucket start, not
  the real wall-clock time this code happens to run at (a second, related
  bug the immediate-execution fix above also required fixing: a naive
  "is it currently past market open" check is trivially true for the
  very first candle of every single day now, since that candle's own
  close is exactly when this code runs).

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
  "switch_to_next_week_on_expiry": false (default) — same option, same
      meaning, as intraday_dtt_simple/intraday_dtt_adjusted's identical
      flag: when the resolved `expiry_selector` contract expires TODAY,
      false sells it anyway (same-day gamma, opted into); true
      re-resolves "NEXT_WEEK" instead, just for that one entry —
      `expiry_selector` itself is never mutated, so every other day
      still resolves however it's configured to. Checked on every fresh
      entry in `_enter` (this strategy re-resolves an ATM leg per
      entry, unlike a fixed-instrument strategy), against the ACTUAL
      resolved expiry date, not a hardcoded weekday.
  "lots_per_trade": 1 (default) — options only trade in whole lots;
      each entry sells exactly this many lots of whatever the current
      lot size is for options_underlying.
  "pivot_type" / "atr_smoothing" / "force_exit_time" / "market_open_time":
      identical meaning and defaults to pivot_supertrend — see that
      module's CONFIG section for market_open_time specifically.
  "max_trades_per_day": 3 (default) — a hard cap on fresh ENTRIES per
      IST calendar day (Step 98); null/0 disables it (unlimited, the old
      behavior). Counts only actual fills — a signal that fires but
      can't be filled (no Kite session, a resolver error) doesn't use
      up a slot. Exits and force-exit are NEVER blocked by this — the
      cap only ever stops a NEW position from being opened, never
      closing an existing one. Resets to 0 at the next IST day
      boundary, same day-rollover check on_tick already does for
      pivots. Persisted (today + trades_today) across a restart mid-day
      so a redeploy can't reset the count and grant extra trades for
      the rest of the day it wouldn't otherwise have had.

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
from datetime import date, datetime
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver, options_exchange_for
from .pivot_supertrend import (
    R_KEYS,
    S_KEYS,
    ST_MULTIPLIER,
    ST_PERIOD,
    CandleAggregator,
    SuperTrendState,
    _IST,
    _parse_hhmm,
    apply_seed_to_state,
    compute_pivots,
    fetch_seed_from_kite,
    is_stale_candle_close,
    supertrend_from_seed_candles,
    supertrend_status_fields,
    supertrend_status_fields_from_state,
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
        "switch_to_next_week_on_expiry": False,
        "atm_reference_mode": "auto",
        "lots_per_trade": 1,
        "pivot_type": "classic",
        "atr_smoothing": "wilder",
        "force_exit_time": "15:00",
        "market_open_time": "09:15",
        "max_trades_per_day": 3,
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
        self.switch_to_next_week_on_expiry = bool(cfg.get("switch_to_next_week_on_expiry", False))
        self.lots_per_trade = int(cfg.get("lots_per_trade") or 1)
        if self.lots_per_trade < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {self.lots_per_trade}")

        self.pivot_type = cfg.get("pivot_type", "classic")
        self.atr_method = cfg.get("atr_smoothing", "wilder")
        self.force_exit_time = _parse_hhmm(cfg.get("force_exit_time", "15:00"))
        self.market_open_time = _parse_hhmm(cfg.get("market_open_time", "09:15"))
        # None (from an explicit null OR a 0) disables the cap entirely --
        # see this class's own module docstring for the full reasoning.
        raw_max_trades = cfg.get("max_trades_per_day", 3)
        self.max_trades_per_day: Optional[int] = int(raw_max_trades) if raw_max_trades else None

        self.aggregator = CandleAggregator(interval_minutes=5, label=runner.deployment_name)
        self.st = SuperTrendState(period=ST_PERIOD, multiplier=ST_MULTIPLIER,
                                  atr_method=self.atr_method)

        self.today = None
        self.today_high: Optional[float] = None
        self.today_low: Optional[float] = None
        self.today_last_close: Optional[float] = None
        self.trades_today = 0   # resets in _roll_over_day; see max_trades_per_day above

        # Fetched once, here, unconditionally -- reused below by FALLBACK
        # 1 so this isn't a second DB read. Resume-safety for the daily
        # trade cap needs it EVEN when the primary Kite-seed path below
        # succeeds (the common case): unlike SuperTrend/pivots, which
        # that path always re-derives fresh FROM KITE ITSELF (making a
        # persisted today/today_high/today_low genuinely redundant on a
        # same-day restart), there's no external source of truth for
        # "how many entries already happened today" to re-derive from —
        # the only record of it is whatever this deployment last
        # persisted, so it can't wait for the primary path to fail
        # before checking. Only applied if the persisted day actually
        # IS today — a restart on a NEW day should start that day's
        # count at 0, same as the normal _roll_over_day path would.
        persisted = await runner.load_state()
        if persisted:
            today_str = persisted.get("today")
            if today_str:
                try:
                    if date.fromisoformat(today_str) == datetime.now(_IST).date():
                        self.trades_today = persisted.get("trades_today", 0)
                except ValueError:
                    pass

        self.prev_day_ohlc: Optional[dict] = cfg.get("prev_day_ohlc")
        self.pivots: Optional[dict] = None

        self.prev_trend: Optional[str] = None

        # exchange=... : the options CHAIN's exchange, not the
        # underlying's spot exchange — NFO for NIFTY/BANKNIFTY/..., BFO
        # for SENSEX/BANKEX. Passing this explicitly (rather than
        # relying on OptionsResolver's own "NFO" default) is the fix for
        # a real bug: every SENSEX entry attempt was silently failing
        # with "No option expiries found for 'SENSEX' on NFO" — see
        # options_exchange_for's own docstring in app/options/resolver.py.
        self.resolver = OptionsResolver(
            runner.dispatcher, exchange=options_exchange_for(self.options_underlying),
            atm_reference_mode=cfg.get("atm_reference_mode", "auto"),
        )

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

        # PRIMARY seeding path: fetch fresh, gap-free candles straight
        # from Kite's REST API and replay them RIGHT NOW — see
        # pivot_supertrend.py's identical block / fetch_seed_from_kite's
        # own docstring for the full reasoning (every on_start, not just
        # a cold deploy; include_today_ohlc=False since today's own
        # pivots always come from the day strictly BEFORE today).
        try:
            seed = await fetch_seed_from_kite(runner.dispatcher, self.instrument_token)
            self.st = supertrend_from_seed_candles(seed["seed_candles"], self.atr_method)
            self.prev_trend = self.st.trend
            if seed["prev_day_ohlc"]:
                self.prev_day_ohlc = seed["prev_day_ohlc"]
                self.pivots = compute_pivots(
                    self.prev_day_ohlc["high"], self.prev_day_ohlc["low"],
                    self.prev_day_ohlc["close"], self.pivot_type,
                )
            logger.info(
                "%s: auto-seeded live from Kite (%d candle(s)) -> trend=%s, pivots=%s",
                runner.deployment_name, len(seed["seed_candles"]), self.st.trend, bool(self.pivots),
            )
            return
        except NoKiteSession:
            logger.warning(
                "%s: no Kite session yet — cannot auto-seed live; falling back "
                "to persisted state / config seed", runner.deployment_name,
            )
        except Exception:
            logger.exception(
                "%s: live auto-seed from Kite failed — falling back to "
                "persisted state / config seed", runner.deployment_name,
            )

        # FALLBACK 1: whatever this deployment last persisted (already
        # fetched above, for the trade-cap resume check).
        if persisted and self._restore_from_state(runner, persisted):
            return

        # FALLBACK 2: legacy config-provided seed — no longer required,
        # still honored if given.
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
                "%s: no live Kite seed, no persisted state, no config seed — "
                "pivots unavailable until a full trading day has been observed "
                "live (no entries until then).", runner.deployment_name,
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
            # Same value on_start's own early check already applied (see
            # its comment) -- restored again here too so this method
            # stays a complete, correct restore on its own, regardless
            # of which call site reaches it.
            if self.today == datetime.now(_IST).date():
                self.trades_today = state.get("trades_today", 0)
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
            "trades_today": self.trades_today,
        }

    def get_status_fields(self) -> Optional[list]:
        return supertrend_status_fields(self.st, self.pivots)

    @staticmethod
    def status_fields_from_state(state: dict) -> Optional[list]:
        return supertrend_status_fields_from_state(state)

    async def on_post_market_checkpoint(self, runner) -> None:
        """See pivot_supertrend.py's identical method for the full
        reasoning — re-fetches a clean candle window + today's own
        now-final daily OHLC from Kite's REST API, recomputes SuperTrend
        fresh, and rolls prev_day_ohlc/pivots forward to TOMORROW, all
        applied to LIVE in-memory state so a deployment that stays
        running straight through market close self-heals without a
        restart. Does NOT touch active_leg_token/active_leg_symbol/
        active_leg_exchange — an open option position is unaffected by
        this (it's already resume-safe via the DB regardless)."""
        try:
            seed = await fetch_seed_from_kite(
                runner.dispatcher, self.instrument_token, include_today_ohlc=True,
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

        fresh_st = supertrend_from_seed_candles(seed["seed_candles"], self.atr_method)
        if fresh_st.trend is None:
            logger.warning(
                "%s: post-market checkpoint's fresh replay never warmed up "
                "(unexpected — leaving existing live state untouched)",
                runner.deployment_name,
            )
            return
        old_trend = self.st.trend
        self.st = fresh_st
        self.prev_trend = fresh_st.trend

        if seed["prev_day_ohlc"]:
            self.prev_day_ohlc = seed["prev_day_ohlc"]
            self.pivots = compute_pivots(
                self.prev_day_ohlc["high"], self.prev_day_ohlc["low"],
                self.prev_day_ohlc["close"], self.pivot_type,
            )
        logger.info(
            "%s: post-market checkpoint — resynced SuperTrend from live Kite "
            "data (trend %s -> %s) and rolled pivots forward for tomorrow",
            runner.deployment_name, old_trend, fresh_st.trend,
        )

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

        await self._on_candle_closed(runner, completed, ts)

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
        # Daily trade cap (Step 98) resets with every new day, same as
        # pivots do above -- a fresh count for a fresh day, regardless of
        # how many entries yesterday used up.
        self.trades_today = 0

    async def _on_candle_closed(self, runner, candle: dict, now: datetime) -> None:
        # GAP GUARD — see pivot_supertrend.py's is_stale_candle_close for
        # the real incident this fixes: without it, a fresh signal
        # computed off a candle that only reached this strategy hours
        # late (a WebSocket reconnect gap) gets acted on at a completely
        # disconnected price. `now` is `ts`, the REAL tick timestamp that
        # triggered this candle-close (from on_tick above) — never
        # candle["date"] itself.
        stale = is_stale_candle_close(candle["date"], self.aggregator.interval_minutes, now)
        if stale:
            logger.warning(
                "%s: candle closed at %s but only reached this strategy at %s "
                "(%.0f min late) — likely a WebSocket reconnect gap. Absorbing "
                "this candle's real OHLC into SuperTrend, but skipping any "
                "fresh entry decision off data this stale (an exit, if one's "
                "otherwise due, is never blocked by this).",
                runner.deployment_name, candle["date"], now,
                (now - candle["date"]).total_seconds() / 60,
            )

        t = now.time()   # real wall-clock time -- deliberately used for the
                          # force-exit cutoff below (must reflect reality
                          # regardless of how late this call arrived), NOT
                          # for after_open (see the comment on it below).
        before_cutoff = self.force_exit_time is None or t < self.force_exit_time
        # Checked against THIS CANDLE'S OWN bucket start, never real
        # wall-clock `now` (Step 94 fix) — see pivot_supertrend.py's own
        # module docstring for exactly why a `now.time()` check here was
        # a real bug: with immediate execution (below), the call
        # evaluating the very first candle of every day always runs at
        # real time ~market_open_time, trivially passing a wall-clock
        # check regardless of whether the candle's own data was actually
        # from the regular session.
        after_open = self.market_open_time is None or candle["date"].time() >= self.market_open_time

        # 1 — advance SuperTrend, detect a flip, and act on it
        # IMMEDIATELY — same candle-close event that confirmed it, not
        # deferred to the next one (Step 94 fix — see module docstring's
        # EXECUTION TIMING section for why deferring here used to mean a
        # real 5-minutes-late option price, not just a 5-minutes-late
        # bookkeeping timestamp). Never gated by `stale` — an exit is
        # always allowed through regardless of how this candle arrived,
        # same as force-exit below; only FRESH entries (step 3) are
        # stale-gated.
        prev_trend_before_update = self.prev_trend
        new_trend = self.st.update(candle)
        if new_trend is not None:
            if (prev_trend_before_update is not None and new_trend != prev_trend_before_update
                    and self.active_leg_token is not None):
                trigger_values = {
                    "prev_trend": prev_trend_before_update, "new_trend": new_trend,
                    "close": round(candle["close"], 2),
                    "final_upper": round(self.st.final_upper, 2) if self.st.final_upper is not None else None,
                    "final_lower": round(self.st.final_lower, 2) if self.st.final_lower is not None else None,
                }
                await self._exit(runner, candle, "st_flip", trigger_values)
            self.prev_trend = new_trend

        # 2 — force-exit at/after cutoff if still open (real `now`, not
        # candle time)
        if self.force_exit_time is not None and t >= self.force_exit_time:
            if self.active_leg_token is not None:
                await self._exit(runner, candle, "force_exit", {
                    "candle_time": t.isoformat(), "force_exit_time": self.force_exit_time.isoformat(),
                })

        # 3 — a fresh entry signal, detected AND acted on immediately
        # (flat, pivots known, ST ready, within the entry window, this
        # candle's own data genuinely fresh and from the regular
        # session). Firing right after an exit in step 1/2 above, same
        # call, is intentional — see module docstring's RULES ("a fresh
        # entry can fire immediately after an exit closes the previous
        # leg").
        if not stale and self.active_leg_token is None and self.pivots is not None \
                and self.prev_trend is not None and before_cutoff and after_open:
            # Daily trade cap (Step 98) — checked here, not folded into
            # the outer `if` above, so a signal that genuinely fires but
            # gets blocked by the cap is distinguishable (and logged,
            # below) from the ordinary "nothing broke" case, which isn't
            # worth a log line every candle.
            at_daily_cap = self.max_trades_per_day is not None and self.trades_today >= self.max_trades_per_day
            close = candle["close"]
            # The SuperTrend "value" itself -- whichever band is
            # currently ACTIVE for prev_trend, same active-band
            # convention supertrend_status_fields already uses for the
            # Detail page's own Stats tab (final_lower while trending up
            # -- support, a break below flips down; final_upper while
            # trending down -- resistance, a break above flips up).
            # Captured on every position taken, alongside the underlying
            # price (`close`, already recorded) and the pivot point
            # itself, so an entry can be independently re-checked later
            # against what the indicators actually read at that instant.
            st_value = self.st.final_lower if self.prev_trend == "up" else self.st.final_upper
            pivot_point = self.pivots.get("P")
            if self.prev_trend == "up":
                for k in R_KEYS:
                    level = self.pivots[k]
                    if close > level:
                        if at_daily_cap:
                            logger.info(
                                "%s: pivot break (long, %s @ %.2f) but max_trades_per_day "
                                "(%d) already reached today — staying flat",
                                runner.deployment_name, k, close, self.max_trades_per_day,
                            )
                            break
                        trigger_values = {
                            "close": round(close, 2), "trend": self.prev_trend,
                            "supertrend_value": round(st_value, 2) if st_value is not None else None,
                            "pivot_point": round(pivot_point, 2) if pivot_point is not None else None,
                            "broken_level_key": k, "broken_level": round(level, 2),
                            "r_levels": {rk: round(self.pivots[rk], 2) for rk in R_KEYS},
                        }
                        await self._enter(runner, candle, "long", trigger_values)
                        break
            elif self.prev_trend == "down":
                for k in S_KEYS:
                    level = self.pivots[k]
                    if close < level:
                        if at_daily_cap:
                            logger.info(
                                "%s: pivot break (short, %s @ %.2f) but max_trades_per_day "
                                "(%d) already reached today — staying flat",
                                runner.deployment_name, k, close, self.max_trades_per_day,
                            )
                            break
                        trigger_values = {
                            "close": round(close, 2), "trend": self.prev_trend,
                            "supertrend_value": round(st_value, 2) if st_value is not None else None,
                            "pivot_point": round(pivot_point, 2) if pivot_point is not None else None,
                            "broken_level_key": k, "broken_level": round(level, 2),
                            "s_levels": {sk: round(self.pivots[sk], 2) for sk in S_KEYS},
                        }
                        await self._enter(runner, candle, "short", trigger_values)
                        break

    # ── Execution — sell an option leg to open, buy it back to close ───

    async def _enter(self, runner, candle: dict, side: str, trigger_values: dict) -> None:
        option_type = "PE" if side == "long" else "CE"   # bullish -> sell puts, bearish -> sell calls
        try:
            # Resolve the expiry FIRST, on its own, rather than handing
            # expiry_selector straight to get_atm_leg -- switch_to_next_
            # week_on_expiry (see module docstring) needs the chance to
            # override it with "NEXT_WEEK" before strike/leg resolution
            # ever happens, same two-step shape as intraday_dtt_simple's
            # resolve_atm_straddle_legs. Every entry re-resolves fresh
            # (unlike a fixed-instrument strategy), so this check runs
            # here, per entry, rather than once at on_start.
            expiry = await self.resolver.resolve_expiry(self.options_underlying, self.expiry_selector)
            if expiry == candle["date"].date():
                if self.switch_to_next_week_on_expiry:
                    logger.info(
                        "%s: resolved %s contract expires today (%s) — "
                        "switch_to_next_week_on_expiry=true, re-resolving "
                        "NEXT_WEEK for this entry instead.",
                        runner.deployment_name, self.expiry_selector, expiry,
                    )
                    expiry = await self.resolver.resolve_expiry(self.options_underlying, "NEXT_WEEK")
                    trigger_values = {**trigger_values, "switched_to_next_week": True}
                else:
                    logger.info(
                        "%s: resolved %s contract expires today (%s) — "
                        "switch_to_next_week_on_expiry=false, selling the "
                        "same-day-expiry leg as resolved.",
                        runner.deployment_name, self.expiry_selector, expiry,
                    )
            leg = await self.resolver.get_atm_leg(self.options_underlying, expiry, option_type)
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
        # Counted here, not at signal-detection time above — this is the
        # point a real fill actually happened; a signal that fired but
        # never got filled (the two early-returns above) doesn't use up
        # one of today's slots.
        self.trades_today += 1
        await runner.notify_execution(
            "entry", f"Sold {qty} {leg.tradingsymbol} @ {price}", metadata=meta,
        )

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
        closed_symbol = self.active_leg_symbol
        await runner.buy(   # BUY TO CLOSE
            self.active_leg_symbol, self.active_leg_token, qty, price, candle["date"],
            reason=reason, metadata=meta,
        )
        runner.dispatcher.release_instruments([self.active_leg_token])
        self._clear_active_leg()
        await runner.notify_execution(
            "exit", f"{reason}: bought back {qty} {closed_symbol} @ {price}", metadata=meta,
        )

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
