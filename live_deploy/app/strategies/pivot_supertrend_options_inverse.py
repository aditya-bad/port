"""
live_deploy — the INVERSE of pivot_supertrend_options: buys options on a
SuperTrend flip and holds for a fixed number of candles, instead of
selling on a pivot breakout and holding until the next flip.

Live paper-trading only — no backtested version. Reuses the same shared
signal primitives as pivot_supertrend/pivot_supertrend_options
(SuperTrendState, CandleAggregator) — but NOT the pivot machinery
(compute_pivots, prev_day_ohlc, pivot_type): this strategy never looks
at R1-R3/S1-S3 at all.

RULES — deliberately the mirror image of pivot_supertrend_options:
  Where the original ENTERS is exactly where this one does NOT — a 5-min
  close above/below a pivot level plays no role here whatsoever.
  Where the original EXITS (a SuperTrend flip) is exactly where THIS one
  ENTERS:
    ST flips to red   -> BUY THIS_WEEK ATM PE  (betting the new downtrend
                          continues — a put buyer profits as price falls)
    ST flips to green -> BUY THIS_WEEK ATM CE  (betting the new uptrend
                          continues)
  Exit is purely TIME-based, not SuperTrend-based (the flip already WAS
  the entry trigger, so there's nothing left for a second flip to exit
  on): hold for exactly `hold_candles` complete 5-min candles after
  entry, then exit. A `force_exit_time` safety net (default 15:00, same
  as the rest of this strategy family) still applies in case a late-day
  flip's hold period would otherwise run past market close.
  NEVER skips an entry, including on the resolved contract's OWN expiry
  day (config: `switch_to_next_week_on_expiry`, default False — same
  option/meaning as pivot_supertrend_options'/intraday_dtt_simple's
  identical flag): false buys the same-day-expiry contract as resolved
  (the old behavior); true re-resolves NEXT_WEEK instead, just for that
  one entry. See CONFIG below and `_enter`.

  NO fresh entry before `market_open_time` (config, default 09:15) —
  see pivot_supertrend.py's own module docstring for the full reasoning
  AND for why this is checked against the SIGNAL CANDLE's own bucket
  start, never real wall-clock time (no `entry_time` schedule here
  either, so a SuperTrend flip detected off pre-market indicative-price
  ticks could otherwise queue a real entry the moment regular trading
  begins). Only gates fresh entry DETECTION (a flip while flat); an
  exit is never blocked by this.

BUYING, NOT SELLING: this always BUYS a leg to open and SELLS it to
close — standard long-option mechanics, the exact opposite fill
direction from pivot_supertrend_options (which always sells to open).
This maps directly onto the SAME mechanics pivot_supertrend.py already
uses for the underlying (buy to go long, sell to exit) — record_fill
already treats "buy first, sell later" as a long position with
realized_pnl = qty*(sell_price - buy_price), so no schema/query changes
are needed here either.

RE-ARMS EVERY TIME, NOT ONCE A DAY: unlike intraday_dtt_simple, this is
not a "one trade per day" strategy — a SuperTrend flip can happen
several times in a session, and every flip while flat is a fresh entry
signal. Only one open position at a time (a flip that occurs while
already holding one is simply missed, same "only 1 lot in, 1 lot out"
rule the rest of this family uses).

HOLD-CANDLES TIMING (config: `hold_candles`, default 1) — entry executes
IMMEDIATELY off the SAME candle-close event that confirms the flip
(Step 94: no more deferred "decide now, act next candle" timing — see
pivot_supertrend_options.py's own module docstring for why that
deferral used to mean a genuinely late, not just late-but-correctly-
backdated, option price). From entry, `hold_candles` counts full
candle-close events — including the entry candle's own close — so
`hold_candles: 1` exits at the very end of the NEXT candle-close event
after entry (held through exactly 1 candle); `hold_candles: 2` exits one
candle further out, and so on. Same as entry, the exit itself now fires
the instant the threshold is reached, in whichever call's candle-close
crosses it — not deferred an extra candle the way it briefly was.

RESUME-SAFETY FOR THE HOLD COUNTER: `candles_held` isn't itself stored
anywhere durable — it's reconstructed on resume from the entry candle's
own timestamp (`entry_candle_date`, stashed in the opening fill's
metadata) compared against the next candle actually observed live: 1 +
however many full candle-widths have elapsed since entry (the +1 is the
entry candle's own close, which always counts as "1 held" the moment
it's processed, same as it would without any restart at all). If that
reconstructed count already meets or exceeds `hold_candles`, the
position exits IMMEDIATELY, in that same call. This is a deliberate,
one-directional asymmetry: a resume can make this strategy exit up to
one candle EARLIER than an uninterrupted run would have at the exact
threshold candle, but it will never exit LATER — holding an options
position any longer than intended after a resume is the worse failure
mode, so the reconciliation errs toward closing sooner, not toward
exact call-count parity with a hypothetical non-resumed run. Older
position rows with no `entry_candle_date` in their metadata (shouldn't
happen going forward, but defensively handled) just resume counting
from 0, i.e. as if re-entering fresh — safer to hold a little long than
to guess wrong.

CONFIG:
  "instrument_tokens": [<single token>] — the UNDERLYING's token, used
      ONLY to drive the candle/SuperTrend signal. The options actually
      traded are resolved dynamically and are never this token.
  "symbol": underlying's display name — logging only.
  "options_underlying": REQUIRED, the options chain's own `name`, e.g.
      "NIFTY" — NOT the spot tradingsymbol "NIFTY 50".
  "expiry_selector": "THIS_WEEK" (default) — any selector OptionsResolver
      accepts.
  "switch_to_next_week_on_expiry": false (default) — same option, same
      meaning, as pivot_supertrend_options'/intraday_dtt_simple's
      identical flag: when the resolved `expiry_selector` contract
      expires TODAY, false buys it anyway (same-day gamma, opted into);
      true re-resolves "NEXT_WEEK" instead, just for that one entry —
      `expiry_selector` itself is never mutated. Checked in `_enter` on
      every fresh entry, against the ACTUAL resolved expiry date.
  "atr_smoothing": wilder (default) | sma | ema.
  "hold_candles": 1 (default) — see "HOLD-CANDLES TIMING" above. Must be
      >= 1.
  "force_exit_time": "15:00" (default) — nullable to disable, same as
      pivot_supertrend (NOT required-non-null the way it is for
      intraday_dtt_simple, since hold_candles is this strategy's own
      primary exit mechanism, not force_exit_time).
  "market_open_time": "09:15" (default) — nullable to disable (NOT
      recommended), identical meaning to pivot_supertrend.
  "max_trades_per_day": 3 (default) — a hard cap on fresh ENTRIES per
      IST calendar day (Step 98), identical meaning/reasoning to
      pivot_supertrend_options' own copy of this — see that module's
      docstring for the full rationale (only entries count, never
      exits/force-exit; resets at the next IST day boundary; persisted
      across a restart so it can't be reset mid-day for extra trades).
      null/0 disables it.
  "lots_per_trade": 1 (default) — lots bought per entry.
  "seed_candles" / "supertrend_seed": SuperTrend warmup, identical
      meaning to pivot_supertrend (see that module's docstring) — NOTE
      "prev_day_ohlc" is NOT a config key here, since this strategy
      never computes pivots at all.

No margin model — buying options costs real cash up front (checked for
real by record_fill, same as any other buy), selling to close credits
it back, same as pivot_supertrend's own underlying-buying logic already
does.
"""

import logging
from datetime import date, datetime
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver, options_exchange_for
from .pivot_supertrend import (
    ST_MULTIPLIER,
    ST_PERIOD,
    CandleAggregator,
    SuperTrendState,
    _IST,
    _parse_hhmm,
    apply_seed_to_state,
    fetch_seed_from_kite,
    is_stale_candle_close,
    supertrend_from_seed_candles,
    supertrend_status_fields,
    supertrend_status_fields_from_state,
)
from .registry import register_strategy
from .trade_meta import build_trade_meta

logger = logging.getLogger("live_deploy.strategies.pivot_supertrend_options_inverse")


@register_strategy(
    "pivot_supertrend_options_inverse",
    description="Inverse of pivot_supertrend_options: no pivot levels at all — "
               "BUYS THIS_WEEK ATM PE on a SuperTrend flip to red, BUYS ATM CE "
               "on a flip to green (exactly where the original strategy used "
               "to exit), holds for hold_candles candles, then sells to close. "
               "Live paper-trading only, no backtested version.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "expiry_selector": "THIS_WEEK",
        "switch_to_next_week_on_expiry": False,
        "atm_reference_mode": "auto",
        "atr_smoothing": "wilder",
        "hold_candles": 1,
        "force_exit_time": "15:00",
        "market_open_time": "09:15",
        "max_trades_per_day": 3,
        "lots_per_trade": 1,
        "seed_candles": None,
        "supertrend_seed": None,
    },
)
class PivotSupertrendOptionsInverseStrategy(StrategyBase):

    async def on_start(self, runner) -> None:
        cfg = runner.config
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "pivot_supertrend_options_inverse requires config.instrument_tokens "
                f"to be a ONE-ELEMENT list — the underlying's token used only for "
                f"the SuperTrend signal — got {tokens!r}"
            )
        self.instrument_token = tokens[0]
        self.symbol = cfg.get("symbol", str(self.instrument_token))

        self.options_underlying = cfg.get("options_underlying")
        if not self.options_underlying:
            raise ValueError(
                "pivot_supertrend_options_inverse requires config.options_underlying "
                "(the options chain's own `name`, e.g. \"NIFTY\" — NOT the spot "
                "tradingsymbol \"NIFTY 50\")"
            )
        self.expiry_selector = cfg.get("expiry_selector", "THIS_WEEK")
        self.switch_to_next_week_on_expiry = bool(cfg.get("switch_to_next_week_on_expiry", False))
        self.atr_method = cfg.get("atr_smoothing", "wilder")

        self.hold_candles = int(cfg.get("hold_candles") or 1)
        if self.hold_candles < 1:
            raise ValueError(f"hold_candles must be >= 1, got {self.hold_candles}")

        raw_force_exit = cfg.get("force_exit_time", "15:00")
        self.force_exit_time = _parse_hhmm(raw_force_exit)   # None disables it, same as pivot_supertrend
        self.market_open_time = _parse_hhmm(cfg.get("market_open_time", "09:15"))
        # None (from an explicit null OR a 0) disables the cap entirely --
        # see this class's own module docstring for the full reasoning.
        raw_max_trades = cfg.get("max_trades_per_day", 3)
        self.max_trades_per_day: Optional[int] = int(raw_max_trades) if raw_max_trades else None

        self.lots_per_trade = int(cfg.get("lots_per_trade") or 1)
        if self.lots_per_trade < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {self.lots_per_trade}")

        self.aggregator = CandleAggregator(interval_minutes=5, label=runner.deployment_name)
        self.st = SuperTrendState(period=ST_PERIOD, multiplier=ST_MULTIPLIER,
                                  atr_method=self.atr_method)

        self.prev_trend: Optional[str] = None

        # exchange=... : the options CHAIN's exchange (NFO for
        # NIFTY/BANKNIFTY/..., BFO for SENSEX/BANKEX) — see
        # options_exchange_for's own docstring in app/options/resolver.py
        # for the real bug this fixes (every SENSEX entry attempt here
        # was silently failing with "No option expiries found for
        # 'SENSEX' on NFO", identical to pivot_supertrend_options.py's
        # own copy of this same bug).
        self.resolver = OptionsResolver(
            runner.dispatcher, exchange=options_exchange_for(self.options_underlying),
            atm_reference_mode=cfg.get("atm_reference_mode", "auto"),
        )

        self.active_leg_token: Optional[int] = None
        self.active_leg_symbol: Optional[str] = None
        self.active_leg_exchange: Optional[str] = None
        self.candles_held = 0
        self._reattach_entry_date: Optional[datetime] = None

        # Day-tracking (Step 98) exists ONLY to reset the daily trade cap
        # below — this strategy still has no pivots/prev_day_ohlc to
        # recompute at a day boundary (see module docstring), so unlike
        # pivot_supertrend_options.py there's no _roll_over_day beyond
        # that one job.
        self.today: Optional[date] = None
        self.trades_today = 0

        # Resume-safety for the daily trade cap: restored HERE,
        # unconditionally, before the primary Kite-seed path below runs
        # — see pivot_supertrend_options.py's identical block for the
        # full reasoning (there's no external source of truth to
        # re-derive "how many entries already happened today" from the
        # way SuperTrend itself gets re-derived fresh from Kite on every
        # restart, so this can't wait for that path to fail first).
        # Only applied if the persisted day actually IS today.
        persisted = await runner.load_state()
        if persisted:
            today_str = persisted.get("today")
            if today_str:
                try:
                    if date.fromisoformat(today_str) == datetime.now(_IST).date():
                        self.trades_today = persisted.get("trades_today", 0)
                except ValueError:
                    pass

        # Resume-safety: reattach to an already-open leg from the DB.
        for token, pos in runner.open_positions.items():
            self.active_leg_token = token
            self.active_leg_symbol = pos["symbol"]
            metadata = pos["metadata"] or {}
            self.active_leg_exchange = metadata.get("exchange", "NFO")
            raw_entry_date = metadata.get("entry_candle_date")
            if raw_entry_date:
                try:
                    self._reattach_entry_date = datetime.fromisoformat(raw_entry_date)
                except ValueError:
                    logger.warning(
                        "%s: could not parse entry_candle_date %r on resume — "
                        "hold counter restarts from 0 (may hold a bit longer "
                        "than intended, never shorter)",
                        runner.deployment_name, raw_entry_date,
                    )
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
        # a cold deploy). need_prev_day_ohlc=False — this strategy never
        # uses pivots at all, so skips that round trip entirely.
        try:
            seed = await fetch_seed_from_kite(
                runner.dispatcher, self.instrument_token, need_prev_day_ohlc=False,
            )
            self.st = supertrend_from_seed_candles(seed["seed_candles"], self.atr_method)
            self.prev_trend = self.st.trend
            logger.info(
                "%s: auto-seeded live from Kite (%d candle(s)) -> trend=%s",
                runner.deployment_name, len(seed["seed_candles"]), self.st.trend,
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
        apply_seed_to_state(
            runner.deployment_name, self.st, self.atr_method, cfg,
            current_prev_day_ohlc=None,   # this strategy never uses pivots/prev_day_ohlc
            log=logger,
        )
        self.prev_trend = self.st.trend

    def _restore_from_state(self, runner, state: dict) -> bool:
        """See pivot_supertrend.py's identical method for the full
        rationale. No pivots tracking here (this strategy never uses
        them at all) -- `today`/`trades_today` (Step 98) exist purely
        for the daily trade cap, not for anything pivot-related, same as
        on_start's own early restore of them. Returns False (nothing
        mutated) on anything malformed, so the caller falls through to
        the config-seed path."""
        try:
            if state.get("version") != 1:
                return False
            self.st = SuperTrendState.from_snapshot(state["supertrend"])
            self.prev_trend = state.get("prev_trend")
            today_str = state.get("today")
            self.today = date.fromisoformat(today_str) if today_str else None
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
            "%s: resumed from persisted live state (trend=%s) — ignoring "
            "any static seed config, since this is more current",
            runner.deployment_name, self.st.trend,
        )
        return True

    def get_persistable_state(self) -> Optional[dict]:
        """See StrategyBase's own docstring for when this gets called.
        The currently-held option leg (if any) doesn't need to be in
        here — it's already resume-safe via the DB (see the "Resume-
        safety" reattach block above)."""
        if self.st.trend is None:
            return None
        return {
            "version": 1, "supertrend": self.st.snapshot(), "prev_trend": self.prev_trend,
            "today": self.today.isoformat() if self.today else None,
            "trades_today": self.trades_today,
        }

    def get_status_fields(self) -> Optional[list]:
        # No pivots on this variant at all (see module docstring) --
        # supertrend_status_fields(pivots=None) just skips those entries.
        return supertrend_status_fields(self.st, None)

    @staticmethod
    def status_fields_from_state(state: dict) -> Optional[list]:
        return supertrend_status_fields_from_state(state)

    async def on_post_market_checkpoint(self, runner) -> None:
        """See pivot_supertrend.py's identical method for the full
        reasoning — re-fetches a clean candle window from Kite's REST
        API and recomputes SuperTrend fresh, applied to LIVE in-memory
        state so a deployment that stays running straight through market
        close self-heals without a restart. No pivots/prev_day_ohlc here
        at all (this strategy never uses them), so need_prev_day_ohlc is
        False — same as on_start's own call."""
        try:
            seed = await fetch_seed_from_kite(
                runner.dispatcher, self.instrument_token, need_prev_day_ohlc=False,
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
        logger.info(
            "%s: post-market checkpoint — resynced SuperTrend from live Kite "
            "data (trend %s -> %s)", runner.deployment_name, old_trend, fresh_st.trend,
        )

    # ── Tick consumption — day-rollover ONLY resets the daily trade cap
    # (Step 98) now; there's still no pivots to recompute and SuperTrend
    # itself still runs continuously across day boundaries (never
    # reset), same as pivot_supertrend/pivot_supertrend_options. ───────

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

    def _roll_over_day(self, runner) -> None:
        # Only job here (unlike pivot_supertrend_options.py's own
        # _roll_over_day) is resetting the daily trade cap for the new
        # day — see module docstring, this strategy never computes
        # pivots at all.
        logger.info("%s: new trading day -> trade count reset", runner.deployment_name)
        self.trades_today = 0

    async def _on_candle_closed(self, runner, candle: dict, now: datetime) -> None:
        # GAP GUARD — see pivot_supertrend.py's is_stale_candle_close for
        # the real incident this fixes: without it, a fresh flip signal
        # computed off a candle that only reached this strategy hours
        # late (a WebSocket reconnect gap) gets acted on at a completely
        # disconnected price. `now` is `ts`, the REAL tick timestamp that
        # triggered this candle-close (from on_tick above) — never
        # candle["date"] itself. Does NOT affect step 0 below (post-
        # resume hold-counter reconciliation) — that's a different
        # mechanism comparing two candle-based timestamps against each
        # other, not against real time.
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
                          # force-exit cutoff below, NOT for after_open
                          # (see the comment on it below).
        before_cutoff = self.force_exit_time is None or t < self.force_exit_time
        # Checked against THIS CANDLE'S OWN bucket start, never real
        # wall-clock `now` (Step 94 fix) — see pivot_supertrend.py's own
        # module docstring for exactly why a `now.time()` check here was
        # a real bug under immediate-execution timing (below).
        after_open = self.market_open_time is None or candle["date"].time() >= self.market_open_time

        # 0 — one-time reconciliation of the hold counter after a resume
        # with an already-open position. Uses THIS candle's own
        # timestamp vs. the stored entry candle's timestamp, floor-
        # divided by the candle width, so it doesn't matter how long the
        # deployment was actually paused for. If that reconciliation
        # finds the hold period is already overdue, exit IMMEDIATELY,
        # right here, rather than waiting for step 2 below to catch up.
        just_reconciled = False
        if self._reattach_entry_date is not None:
            elapsed_seconds = (candle["date"] - self._reattach_entry_date).total_seconds()
            candle_seconds = self.aggregator.interval_minutes * 60
            # +1: the entry candle itself always counts as "1 held candle"
            # the moment it's processed (see step 2 below, which runs
            # unconditionally on the entry call too) — elapsed_seconds
            # alone only counts FULL candle-widths that have passed
            # SINCE entry, missing that first one.
            self.candles_held = 1 + max(0, int(elapsed_seconds // candle_seconds))
            logger.info(
                "%s: reconstructed candles_held=%d after resume (entry candle "
                "was %s, this candle is %s)", runner.deployment_name,
                self.candles_held, self._reattach_entry_date, candle["date"],
            )
            self._reattach_entry_date = None
            just_reconciled = True
            if self.candles_held >= self.hold_candles:
                await self._exit(runner, candle, "hold_expired", {
                    "candles_held": self.candles_held, "hold_candles": self.hold_candles,
                    "reconciled_after_resume": True,
                })

        # 1 — force-exit at/after cutoff if still open (real `now`, not
        # candle time)
        if self.force_exit_time is not None and t >= self.force_exit_time:
            if self.active_leg_token is not None:
                await self._exit(runner, candle, "force_exit", {
                    "candle_time": t.isoformat(), "force_exit_time": self.force_exit_time.isoformat(),
                })

        # 2 — still holding a position -> one more full candle has
        # elapsed since entry, count it toward hold_candles, and exit
        # IMMEDIATELY (Step 94: not deferred to the next candle) once
        # hold_candles is reached. Skipped on the same call a resumed
        # position's counter was just reconstructed above, to avoid
        # counting that candle twice.
        if self.active_leg_token is not None and not just_reconciled:
            self.candles_held += 1
            if self.candles_held >= self.hold_candles:
                await self._exit(runner, candle, "hold_expired", {
                    "candles_held": self.candles_held, "hold_candles": self.hold_candles,
                    "reconciled_after_resume": False,
                })

        # 3 — advance SuperTrend, detect a flip -> BUY the inverse leg
        # IMMEDIATELY (Step 94: same candle-close event that confirmed
        # the flip, not deferred to the next one). A flip landing right
        # after a hold-expiry exit in step 1/2 above, same call, is
        # allowed through deliberately -- same "a fresh entry can fire
        # immediately after an exit" precedent pivot_supertrend_options.py
        # already established. Never gated by `stale` on the EXIT side
        # (steps 0-2 above); only this fresh-entry detection is.
        # trigger_values captured right here, at the moment the flip is
        # confirmed -- no longer "gone by the time a deferred call
        # executes it", since there is no deferred call anymore.
        prev_trend_before_update = self.prev_trend
        new_trend = self.st.update(candle)
        if new_trend is not None:
            if (not stale and prev_trend_before_update is not None and new_trend != prev_trend_before_update
                    and self.active_leg_token is None and before_cutoff and after_open):
                # Daily trade cap (Step 98) — checked here, separate from
                # the compound condition above, purely so a flip that
                # genuinely qualifies but gets blocked by the cap is
                # distinguishable (and logged) from every OTHER candle
                # where nothing flipped at all.
                if self.max_trades_per_day is not None and self.trades_today >= self.max_trades_per_day:
                    logger.info(
                        "%s: SuperTrend flipped (%s -> %s) but max_trades_per_day "
                        "(%d) already reached today — staying flat",
                        runner.deployment_name, prev_trend_before_update, new_trend,
                        self.max_trades_per_day,
                    )
                else:
                    option_type = "PE" if new_trend == "down" else "CE"
                    trigger_values = {
                        "prev_trend": prev_trend_before_update, "new_trend": new_trend,
                        "close": round(candle["close"], 2),
                        "final_upper": round(self.st.final_upper, 2) if self.st.final_upper is not None else None,
                        "final_lower": round(self.st.final_lower, 2) if self.st.final_lower is not None else None,
                    }
                    await self._enter(runner, candle, option_type, trigger_values)
            self.prev_trend = new_trend

    # ── Execution — buy an option leg to open, sell it to close ────────

    async def _enter(self, runner, candle: dict, option_type: str, trigger_values: dict) -> None:
        try:
            # Resolve the expiry FIRST, on its own, rather than handing
            # expiry_selector straight to get_atm_leg -- switch_to_next_
            # week_on_expiry (see module docstring) needs the chance to
            # override it with "NEXT_WEEK" before strike/leg resolution
            # ever happens, same shape as pivot_supertrend_options'
            # identical check in its own _enter.
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
                        "switch_to_next_week_on_expiry=false, buying the "
                        "same-day-expiry leg as resolved.",
                        runner.deployment_name, self.expiry_selector, expiry,
                    )
            leg = await self.resolver.get_atm_leg(self.options_underlying, expiry, option_type)
            price = await self.resolver.get_ltp(leg)
        except NoKiteSession:
            logger.warning(
                "%s: SuperTrend flip -> buy %s signal, but no Kite session "
                "yet — skipping", runner.deployment_name, option_type,
            )
            return
        except Exception:
            logger.exception(
                "%s: failed to resolve/price the %s ATM leg for entry — "
                "skipping", runner.deployment_name, option_type,
            )
            return

        qty = self.lots_per_trade * leg.lot_size
        runner.dispatcher.add_instruments(
            [{"instrument_token": leg.instrument_token, "symbol": leg.tradingsymbol}]
        )

        meta = build_trade_meta(
            trigger="st_flip_entry_ce" if option_type == "CE" else "st_flip_entry_pe",
            action=f"buy_open_{option_type}",
            trigger_values=trigger_values,
            resulting_state={"leg": option_type, "strike": leg.strike, "symbol": leg.tradingsymbol},
            target_basis={
                "selection_basis": "ATM", "selected_strike": leg.strike, "fill_premium": price,
            },
            option_type=option_type, strike=leg.strike,
            expiry=leg.expiry.isoformat(), exchange=leg.exchange,
            entry_candle_date=candle["date"].isoformat(),
        )
        await runner.buy(   # BUY TO OPEN — long premium, opposite of pivot_supertrend_options
            leg.tradingsymbol, leg.instrument_token, qty, price, candle["date"],
            reason="entry", metadata=meta,
        )
        self.active_leg_token = leg.instrument_token
        self.active_leg_symbol = leg.tradingsymbol
        self.active_leg_exchange = leg.exchange
        self.candles_held = 0
        # Counted here, not at flip-detection time above — this is the
        # point a real fill actually happened; a flip that fired but
        # never got filled (the two early-returns above) doesn't use up
        # one of today's slots.
        self.trades_today += 1
        await runner.notify_execution(
            "entry", f"Bought {qty} {leg.tradingsymbol} @ {price}", metadata=meta,
        )

        logger.info(
            "%s: SuperTrend flipped -> bought %s %s@%.2f, holding for %d candle(s)",
            runner.deployment_name, option_type, leg.tradingsymbol, price, self.hold_candles,
        )

    async def _exit(self, runner, candle: dict, reason: str, trigger_values: dict) -> None:
        if self.active_leg_token is None:
            return
        pos = runner.open_positions.get(self.active_leg_token)
        if pos is None:
            # Already closed out from under us (e.g. a manual force_close
            # on stop) — nothing to sell, just drop local tracking.
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
                    "%s: no live/last price for %s on exit (%s) — selling at "
                    "avg_entry_price %.2f (zero P&L on this close)",
                    runner.deployment_name, self.active_leg_symbol, reason, price,
                )
            else:
                logger.warning(
                    "%s: LTP fetch failed for %s on exit (%s) — using "
                    "dispatcher's last known tick price %.2f instead",
                    runner.deployment_name, self.active_leg_symbol, reason, price,
                )

        qty = float(pos["qty"])
        option_type = "CE" if self.active_leg_symbol.endswith("CE") else "PE"
        meta = build_trade_meta(
            trigger=reason,
            action=f"sell_close_{option_type}",
            trigger_values=trigger_values,
            resulting_state={"position": "flat"},
        )
        closed_symbol = self.active_leg_symbol
        await runner.sell(   # SELL TO CLOSE
            self.active_leg_symbol, self.active_leg_token, qty, price, candle["date"],
            reason=reason, metadata=meta,
        )
        runner.dispatcher.release_instruments([self.active_leg_token])
        self._clear_active_leg()
        await runner.notify_execution(
            "exit", f"{reason}: sold {qty} {closed_symbol} @ {price}", metadata=meta,
        )

    def _clear_active_leg(self) -> None:
        self.active_leg_token = None
        self.active_leg_symbol = None
        self.active_leg_exchange = None
        self.candles_held = 0

    async def on_stop(self, runner) -> None:
        # Release the dynamic option subscription (if any) whether this
        # is a pause or a full stop — on_start re-subscribes it on
        # resume if the position is still open (and reconstructs the
        # hold counter from entry_candle_date at that point).
        if self.active_leg_token is not None:
            runner.dispatcher.release_instruments([self.active_leg_token])
        logger.info(
            "%s: strategy stopped (trend=%s, open_leg=%s)",
            runner.deployment_name, self.st.trend, self.active_leg_symbol,
        )
