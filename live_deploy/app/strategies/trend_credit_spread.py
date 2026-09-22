"""
live_deploy — trend_credit_spread: a weekly, SuperTrend-gated, DEFINED-RISK
credit spread. Live paper-trading only — this is a genuinely new idea
being proposed for evaluation, not a port of anything backtested
elsewhere.

──────────────────────────────────────────────────────────────────────
WHY THIS STRATEGY EXISTS
──────────────────────────────────────────────────────────────────────
Looking at every strategy already running in this book, there is a real
gap in the middle of a 2x2 that the rest of the family fills the other
three corners of:

                        BUYS PREMIUM              SELLS PREMIUM
  DIRECTIONAL      pivot_supertrend_options_       trend_credit_spread
  (a trend read                inverse                  (THIS FILE)
   picks the side)
  NON-DIRECTIONAL   (none — buying premium        strangle_monthly_v2,
  (no trend read,    with no directional edge      intraday_dtt_* family
   sells/buys                is a bad trade)        (naked strangles/
   regardless)                                       straddles)

Every strangle/straddle in this book (strangle_monthly_v2, the
intraday_dtt_* family) sells premium with NO directional read at all —
profitable from theta decay as long as the underlying stays roughly
where it started, but with UNDEFINED risk if it doesn't: a naked short
strangle has no ceiling on how much a strong trend can cost it, capped
only by whatever adjustment/hedge logic that strategy layers on top.
pivot_supertrend_options_inverse has the opposite shape: it BUYS a
single option on a SuperTrend flip (a real directional read), but
buying premium means paying theta decay every day the move doesn't
continue — a directionally-right call still loses money if it's not
right FAST enough.

This strategy is the missing fourth corner: SELL premium (theta decay
works FOR it, same edge every strangle in this book already relies on)
but ONLY in the direction the SAME proven SuperTrend engine
(pivot_supertrend.py's SuperTrendState/CandleAggregator — the identical
signal already running pivot_supertrend_options/_inverse, not a new
indicator) is currently pointing, AND with a bought protective leg
turning "undefined risk" into "known, capped risk from the moment the
trade opens" — the one structural gap the naked strangles in this book
don't close.

──────────────────────────────────────────────────────────────────────
1. THE TRADE — a fixed-width, offset-based weekly credit spread
──────────────────────────────────────────────────────────────────────
On a SuperTrend flip to "up" (bullish):
    SELL a PE `short_leg_otm_steps` strike-steps below ATM, and
    BUY   a PE `short_leg_otm_steps + spread_width_steps` steps below ATM
    — a BULL PUT credit spread: max profit if THIS_WEEK's underlying
    stays above the short strike through expiry; max loss is capped at
    (spread width in price) − (credit received), never more, from the
    moment both legs fill.

On a SuperTrend flip to "down" (bearish): the mirror image — a BEAR
CALL credit spread (short CE closer to ATM, long CE further OTM as
protection).

Both legs are ALWAYS resolved by strike-STEPS from ATM (OptionsResolver.
get_otm_leg — "steps" is chain jargon for "the Nth listed strike out",
not a rupee offset), not by a premium target the way strangle_monthly_v2
picks its own strikes. A fixed, deterministic width is what makes this a
genuine defined-risk SPREAD in the first place — a premium-target
selection (right choice for a naked strangle, where "how much premium"
IS the whole point) would let the spread's own WIDTH drift with however
today's IV happens to price a given premium, which is exactly the kind
of variability a defined-risk structure exists to remove.

Only ONE spread open at a time. A flip while a spread from the PREVIOUS
trend is still open closes it first (see Section 3), then the new
spread opens immediately after, same candle — same "an exit can be
immediately followed by a fresh entry" precedent every sibling
SuperTrend strategy in this package already establishes.

──────────────────────────────────────────────────────────────────────
2. ENTRY — fires ONCE PER FLIP, not continuously
──────────────────────────────────────────────────────────────────────
Unlike pivot_supertrend_options (which re-enters on every pivot-level
break for as long as the trend holds), this strategy enters EXACTLY
ONCE per SuperTrend flip event — the candle where `prev_trend` actually
CHANGES, never on some later candle where the trend simply "still
happens to be up". If a profit-target or stop-loss closes the spread
mid-trend (Section 4), the strategy stays FLAT for the remainder of
that trend leg — it does NOT re-open a fresh spread in the same
direction until the NEXT real flip.

This is a deliberate, conservative choice specific to this strategy
(not copied from a sibling): premium SELLING doesn't need momentum
CONFIRMATION the way premium buying does (pivot_supertrend_options_
inverse buys on the raw flip precisely because it has no cheaper way to
express "I think this trend continues"; a seller earns theta regardless
of exactly when in the trend it gets in) — so entering on every flip
directly, without waiting for a pivot-level break too, is not a
weaker signal, just a differently-timed one, calibrated for premium
selling's own risk profile: bounding this to ONE spread per trend leg
avoids repeatedly re-entering (and potentially re-stopping) in a choppy
market, the failure mode a "re-enter on every candle while flat" rule
would otherwise invite.

Also gated (same shape as every SuperTrend sibling in this package):
  - Flat (no spread currently open).
  - SuperTrend has actually warmed up (`prev_trend is not None`).
  - `market_open_time` has passed for THIS CANDLE's own bucket, not
    real wall-clock time (see pivot_supertrend.py's own docstring for
    why that distinction matters under immediate-execution timing).
  - The candle-close event itself isn't stale (a WebSocket-gap-delayed
    candle skips a fresh entry, same as every sibling — see
    is_stale_candle_close).
  - `max_trades_per_day` not yet reached (Step 98 precedent — a safety
    cap against a pathologically choppy day generating many flips, not
    a limit expected to bind in ordinary conditions given Section 2's
    own "once per flip" rule already bounds this heavily).

SAME-DAY-EXPIRY ENTRIES: `switch_to_next_week_on_expiry` defaults to
TRUE here — the OPPOSITE default from every sibling strategy in this
package (they default False). Deliberate: this spread is meant to be
held across MULTIPLE DAYS to let theta actually do the work (Section
4's profit target is 50% of a FULL week's credit by default, not one
session's worth) — selling a spread that expires the same afternoon
defeats the entire point of the trade, whereas a same-day-expiry entry
is at least a coherent (if aggressive) choice for the single-day option
positions every sibling strategy trades instead.

──────────────────────────────────────────────────────────────────────
3. TREND-FLIP EXIT — the market disagreeing is itself a reason to leave
──────────────────────────────────────────────────────────────────────
Checked at every candle close, same event that drives entry: if a
spread is open and SuperTrend flips AWAY from the direction that spread
was sold for (a bull put spread open when the trend flips to "down", or
a bear call spread open when it flips to "up"), close it immediately —
`reason="st_flip"`, same trigger name pivot_supertrend_options.py
already uses for its own identical concept, so the Reports/Detail
episode-grouping logic (queries.py's `_group_into_episodes`) classifies
it the same way: an ADJUST-family close, still eligible to bridge with
whatever opens right after it within the usual 5-minute tolerance, same
as any other roll.

──────────────────────────────────────────────────────────────────────
4. PROFIT TARGET / STOP LOSS — checked every TICK, not just candle close
──────────────────────────────────────────────────────────────────────
Unlike the trend-flip/entry logic above (inherently candle-based — the
signal itself only updates on a candle close), risk management on an
already-open spread is checked on EVERY tick of the UNDERLYING
(on_tick, not _on_candle_closed) — a stop shouldn't have to wait up to 5
minutes to fire just because the signal engine only updates that often.
This reads BOTH legs' current marks straight from
`runner.dispatcher.last_prices` (no REST call on the hot path) — the
exact same "read the dispatcher's already-live cache, defer if a leg's
price isn't in it yet" pattern strangle_monthly_v2's own `_maybe_manage`
already established for multi-leg P&L checks.

Per-unit credit received at entry:
    credit = short_leg.entry_price − long_leg.entry_price   (always > 0
             for a real credit spread — see the sanity guard in
             Section 1's own entry code; a non-positive value here
             means the chain quoted the long leg richer than the short,
             which never happens for a validly-constructed spread and
             skips the entry outright rather than opening a broken one)

Live spread P&L (cash, both legs combined — mirrors strangle_monthly_v2's
own per-leg unrealized formula, generalized to mixed short+long legs by
applying each leg's own correct profit sign):
    unrealized_short = (short_leg.entry_price − short_now) × qty
    unrealized_long  = (long_now − long_leg.entry_price)  × qty
    spread_pnl        = unrealized_short + unrealized_long

  PROFIT TARGET (`profit_target_pct`, default 0.5): close when
      spread_pnl >= profit_target_pct × credit_received_cash
      — i.e. close once 50% of the maximum possible profit (the full
      credit received) has been captured. A well-known, standard
      credit-spread management rule (take the easy, high-probability
      part of the decay curve; the last 50% of theta decay takes
      disproportionately longer and carries disproportionately more
      gamma risk for the reward left on the table).

  STOP LOSS (`stop_loss_multiple`, default 1.0): close when
      spread_pnl <= −stop_loss_multiple × credit_received_cash
      — i.e. close once the LOSS reaches 1x the credit originally
      received (the other half of the same standard rule: risk no more
      than what you could have made).

Whichever is hit first wins; both are checked every tick, profit target
first (an arbitrary but harmless tie-break — the two conditions can
never both be true of the same spread_pnl value at once).

──────────────────────────────────────────────────────────────────────
5. EXPIRY-DAY HARD BACKSTOP — a different mechanism from every sibling
──────────────────────────────────────────────────────────────────────
`expiry_day_force_exit_time` (default "15:00") is checked at every
candle close, but ONLY when `self.current_expiry` (the OPEN spread's
own resolved contract date) equals today — unlike pivot_supertrend_
options'/_inverse's `force_exit_time`, which fires EVERY SINGLE DAY
regardless of whether anything is actually expiring, forcing those
strategies effectively flat every afternoon.

That daily-close behavior is right for a single fast-decaying ATM
option (the sibling's whole trade is done well within a day); it would
be WRONG here — deliberately so, this is not an oversight — since this
spread is meant to be HELD ACROSS MULTIPLE DAYS specifically so theta
decay (Section 4's profit target) has time to work. Force-closing it
every afternoon regardless of expiry would silently turn a multi-day
theta play into an accidental same-day one. This backstop exists purely
to avoid the ONE genuinely dangerous day to be holding a short option
position through — its own expiry afternoon, where gamma risk spikes as
time value collapses to nothing — same "avoid the sharp-gamma window,
don't ban holding positions generally" reasoning pivot_supertrend_
options.py's own module docstring gives for why THAT strategy's
same-day-expiry entries are merely opt-in, not forbidden outright.
Checked with real wall-clock time (`now.time()`, not candle time), same
convention every force-exit check in this package already uses.

──────────────────────────────────────────────────────────────────────
6. POSITION SIZING & CONFIG
──────────────────────────────────────────────────────────────────────
  "instrument_tokens": [<single token>] — the UNDERLYING's token, used
      ONLY to drive the SuperTrend signal (identical role to every
      sibling). The options actually traded are resolved dynamically
      and are never this token.
  "symbol": underlying's display name — logging only.
  "options_underlying": REQUIRED — the options chain's own `name`
      (e.g. "NIFTY", NOT the spot tradingsymbol "NIFTY 50").
  "expiry_selector": "THIS_WEEK" (default) — any OptionsResolver
      selector.
  "switch_to_next_week_on_expiry": true (default — see Section 2 for
      why this differs from every sibling's own default of false).
  "atm_reference_mode": "auto" (default) — see OptionsResolver's own
      get_reference_price docstring; "spot" also accepted.
  "atr_smoothing": "wilder" (default) | "sma" | "ema".
  "short_leg_otm_steps": 6 (default) — how many strike-steps OTM from
      ATM the SHORT (premium-collecting) leg sits.
  "spread_width_steps": 4 (default) — ADDITIONAL strike-steps beyond
      the short leg to the LONG (protective) leg. Spread width in price
      terms is this × the chain's own live strike step (NIFTY has used
      both 50 and 100 at different points — see OptionsResolver.
      get_strike_step's own docstring — so this is always resolved live,
      never hardcoded).
  "profit_target_pct": 0.5 (default) — see Section 4.
  "stop_loss_multiple": 1.0 (default) — see Section 4.
  "expiry_day_force_exit_time": "15:00" (default, nullable to disable)
      — see Section 5. Distinct config key from the sibling strategies'
      "force_exit_time" on purpose (same name, different daily-vs-
      expiry-only meaning, would be a trap for anyone porting a sibling's
      config over by habit).
  "market_open_time": "09:15" (default, nullable) — identical meaning
      to every sibling.
  "max_trades_per_day": 3 (default, nullable/0 to disable) — identical
      meaning/reasoning to pivot_supertrend_options'/_inverse's own
      copy of this (Step 98) — see Section 2 for why this rarely binds
      here in practice.
  "lots_per_trade": 1 (default) — both legs of the spread always trade
      the SAME quantity (`lots_per_trade` × the chain's own live lot
      size) — that equal sizing is what makes it a spread with a
      genuinely capped max loss, rather than two independently-sized
      positions.
  "seed_candles" / "supertrend_seed": SuperTrend warmup — identical
      meaning to pivot_supertrend.py; see that module's docstring.

──────────────────────────────────────────────────────────────────────
7. RESUME-SAFETY
──────────────────────────────────────────────────────────────────────
The open spread itself needs no separate persisted blob — same
philosophy every strategy in this package already uses (the DB, via
`runner.open_positions`, is the source of truth for actual position
state; get_persistable_state only ever carries the SuperTrend/day-
tracking SIGNAL state, identical shape to pivot_supertrend_options_
inverse's own copy). Each leg's OWN opening fill stamps `leg_role`
("short"/"long"), `spread_side`, `option_type`, and `expiry` into its
`positions.metadata` (see `_enter_spread`) — on restart, `on_start`
reclassifies whichever positions are still open by that stamped
`leg_role`, and reconstructs `credit_received_cash` the DB-truth way
(short leg's own avg_entry_price − long leg's own avg_entry_price,
never trusting a persisted number that could have drifted).

ORPHANED HALF-SPREAD: the two legs are opened via two SEPARATE awaited
DB writes (`_enter_spread`) — a crash between them (the only way this
can happen; both writes are otherwise sequential and reliable) could in
principle leave exactly one leg open in the DB with no partner. Every
other piece of this strategy (the profit-target/stop-loss math, the
status display, the flip-exit) assumes a real two-leg spread — silently
managing a stray NAKED leg as "half of one" would be actively wrong,
not just incomplete. Detected explicitly on resume: logged loudly as an
error (this should never happen in ordinary operation) and the orphan
is force-closed on the very next tick, rather than left to be
mismanaged by logic that assumes a structure that no longer exists.
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

logger = logging.getLogger("live_deploy.strategies.trend_credit_spread")


@register_strategy(
    "trend_credit_spread",
    description="Weekly, SuperTrend-gated, defined-risk credit spread: sells a "
               "PE credit spread on a flip to an uptrend, a CE credit spread on "
               "a flip to a downtrend, closing at a profit target, a stop loss, "
               "the next opposing flip, or its own expiry-day backstop — "
               "whichever comes first. Same proven SuperTrend engine as "
               "pivot_supertrend_options/_inverse; the missing 'sell premium, "
               "but only WITH a trend read, defined risk' corner of this book. "
               "Live paper-trading only, no backtested version — a genuinely "
               "new idea being evaluated, not a port.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "expiry_selector": "THIS_WEEK",
        "switch_to_next_week_on_expiry": True,
        "atm_reference_mode": "auto",
        "atr_smoothing": "wilder",
        "short_leg_otm_steps": 6,
        "spread_width_steps": 4,
        "profit_target_pct": 0.5,
        "stop_loss_multiple": 1.0,
        "expiry_day_force_exit_time": "15:00",
        "market_open_time": "09:15",
        "max_trades_per_day": 3,
        "lots_per_trade": 1,
        "seed_candles": None,
        "supertrend_seed": None,
    },
)
class TrendCreditSpreadStrategy(StrategyBase):

    async def on_start(self, runner) -> None:
        cfg = runner.config
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "trend_credit_spread requires config.instrument_tokens to be a "
                f"ONE-ELEMENT list — the underlying's token used only for the "
                f"SuperTrend signal — got {tokens!r}"
            )
        self.instrument_token = tokens[0]
        self.symbol = cfg.get("symbol", str(self.instrument_token))

        self.options_underlying = cfg.get("options_underlying")
        if not self.options_underlying:
            raise ValueError(
                "trend_credit_spread requires config.options_underlying (the "
                "options chain's own `name`, e.g. \"NIFTY\" — NOT the spot "
                "tradingsymbol \"NIFTY 50\")"
            )
        self.expiry_selector = cfg.get("expiry_selector", "THIS_WEEK")
        self.switch_to_next_week_on_expiry = bool(cfg.get("switch_to_next_week_on_expiry", True))
        self.atr_method = cfg.get("atr_smoothing", "wilder")

        self.short_leg_otm_steps = int(cfg.get("short_leg_otm_steps", 6))
        if self.short_leg_otm_steps < 0:
            raise ValueError(f"short_leg_otm_steps must be >= 0, got {self.short_leg_otm_steps}")
        self.spread_width_steps = int(cfg.get("spread_width_steps", 4))
        if self.spread_width_steps < 1:
            raise ValueError(f"spread_width_steps must be >= 1, got {self.spread_width_steps}")

        self.profit_target_pct = float(cfg.get("profit_target_pct", 0.5))
        if not 0 < self.profit_target_pct <= 1:
            raise ValueError(f"profit_target_pct must be in (0, 1], got {self.profit_target_pct}")
        self.stop_loss_multiple = float(cfg.get("stop_loss_multiple", 1.0))
        if self.stop_loss_multiple <= 0:
            raise ValueError(f"stop_loss_multiple must be > 0, got {self.stop_loss_multiple}")

        self.expiry_day_force_exit_time = _parse_hhmm(cfg.get("expiry_day_force_exit_time", "15:00"))
        self.market_open_time = _parse_hhmm(cfg.get("market_open_time", "09:15"))
        raw_max_trades = cfg.get("max_trades_per_day", 3)
        self.max_trades_per_day: Optional[int] = int(raw_max_trades) if raw_max_trades else None

        self.lots_per_trade = int(cfg.get("lots_per_trade") or 1)
        if self.lots_per_trade < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {self.lots_per_trade}")

        self.aggregator = CandleAggregator(interval_minutes=5, label=runner.deployment_name)
        self.st = SuperTrendState(period=ST_PERIOD, multiplier=ST_MULTIPLIER, atr_method=self.atr_method)
        self.prev_trend: Optional[str] = None

        self.resolver = OptionsResolver(
            runner.dispatcher, exchange=options_exchange_for(self.options_underlying),
            atm_reference_mode=cfg.get("atm_reference_mode", "auto"),
        )

        # Position state — see module docstring's Section 7. Exactly two
        # fixed roles (never a variable-length list the way strangle_
        # monthly_v2's multi-adjustment legs need) — a spread is always
        # precisely one short + one long leg, nothing this strategy ever
        # adds a third leg to.
        self.short_leg: Optional[dict] = None
        self.long_leg: Optional[dict] = None
        self.spread_side: Optional[str] = None      # "bull_put" | "bear_call"
        self.spread_option_type: Optional[str] = None
        self.current_expiry: Optional[date] = None
        self.credit_received_cash: float = 0.0
        self._orphan_leg: Optional[dict] = None      # see module docstring's Section 7

        self.today: Optional[date] = None
        self.trades_today = 0

        # Resume-safety for the daily trade cap — same reasoning/shape as
        # every sibling's identical block: restored here, unconditionally,
        # before the primary Kite-seed path below (there's no external
        # source of truth for "how many entries already happened today"
        # the way SuperTrend itself gets re-derived fresh from Kite).
        persisted = await runner.load_state()
        if persisted:
            today_str = persisted.get("today")
            if today_str:
                try:
                    if date.fromisoformat(today_str) == datetime.now(_IST).date():
                        self.trades_today = persisted.get("trades_today", 0)
                except ValueError:
                    pass

        # Resume-safety: reclassify whichever positions are still open by
        # their own stamped leg_role — see module docstring's Section 7.
        open_legs_by_role: dict[str, dict] = {}
        for token, pos in runner.open_positions.items():
            meta = pos["metadata"] or {}
            role = meta.get("leg_role")
            if role not in ("short", "long"):
                continue
            open_legs_by_role[role] = {
                "token": token, "symbol": pos["symbol"],
                "exchange": meta.get("exchange", options_exchange_for(self.options_underlying)),
                "entry_price": float(pos["avg_entry_price"]), "qty": float(pos["qty"]),
                "strike": meta.get("strike"),
            }
            runner.dispatcher.add_instruments([{"instrument_token": token, "symbol": pos["symbol"]}])

        if "short" in open_legs_by_role and "long" in open_legs_by_role:
            self.short_leg = open_legs_by_role["short"]
            self.long_leg = open_legs_by_role["long"]
            any_meta = next(
                (pos["metadata"] or {}) for pos in runner.open_positions.values()
                if (pos["metadata"] or {}).get("leg_role") in ("short", "long")
            )
            self.spread_side = any_meta.get("spread_side")
            self.spread_option_type = any_meta.get("option_type")
            expiry_str = any_meta.get("expiry")
            self.current_expiry = date.fromisoformat(expiry_str) if expiry_str else None
            # DB-truth reconstruction, never a persisted number — see
            # module docstring's Section 7.
            self.credit_received_cash = (
                self.short_leg["entry_price"] - self.long_leg["entry_price"]
            ) * self.short_leg["qty"]
            logger.info(
                "%s: resumed with an open %s spread (short %s, long %s, "
                "credit ~₹%.2f)", runner.deployment_name, self.spread_side,
                self.short_leg["symbol"], self.long_leg["symbol"], self.credit_received_cash,
            )
        elif open_legs_by_role:
            stray_role, stray_leg = next(iter(open_legs_by_role.items()))
            self._orphan_leg = stray_leg
            logger.error(
                "%s: resumed with only the %s leg of a spread open (%s) — the "
                "other leg is missing (most likely a crash between the two "
                "entry fills). Treating this as an orphan and force-closing "
                "it on the very next tick rather than mismanaging it as half "
                "a spread.", runner.deployment_name, stray_role, stray_leg["symbol"],
            )

        # PRIMARY seeding path — identical shape to pivot_supertrend_
        # options_inverse.py's own copy (need_prev_day_ohlc=False: this
        # strategy has no pivot machinery at all, same as that sibling).
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

        if persisted and self._restore_from_state(runner, persisted):
            return

        apply_seed_to_state(
            runner.deployment_name, self.st, self.atr_method, cfg,
            current_prev_day_ohlc=None, log=logger,
        )
        self.prev_trend = self.st.trend

    def _restore_from_state(self, runner, state: dict) -> bool:
        """See pivot_supertrend.py's identical method for the full
        rationale. No pivots here at all (see module docstring) —
        today/trades_today exist purely for the daily trade cap."""
        try:
            if state.get("version") != 1:
                return False
            self.st = SuperTrendState.from_snapshot(state["supertrend"])
            self.prev_trend = state.get("prev_trend")
            today_str = state.get("today")
            self.today = date.fromisoformat(today_str) if today_str else None
            if self.today == datetime.now(_IST).date():
                self.trades_today = state.get("trades_today", 0)
        except (KeyError, TypeError, ValueError):
            logger.exception(
                "%s: persisted state was malformed — ignoring it and falling "
                "back to the config seed instead", runner.deployment_name,
            )
            return False
        logger.info(
            "%s: resumed from persisted live state (trend=%s) — ignoring any "
            "static seed config, since this is more current",
            runner.deployment_name, self.st.trend,
        )
        return True

    def get_persistable_state(self) -> Optional[dict]:
        """See StrategyBase's own docstring. Only the SuperTrend/day-
        tracking SIGNAL state — the open spread itself needs nothing
        here at all, it's already resume-safe via the DB (Section 7)."""
        if self.st.trend is None:
            return None
        return {
            "version": 1, "supertrend": self.st.snapshot(), "prev_trend": self.prev_trend,
            "today": self.today.isoformat() if self.today else None,
            "trades_today": self.trades_today,
        }

    def get_status_fields(self) -> Optional[list]:
        if self.short_leg is not None and self.long_leg is not None:
            return [
                {"label": "Spread", "value": "Bull Put" if self.spread_side == "bull_put" else "Bear Call"},
                {"label": "Short strike", "value": self.short_leg["strike"]},
                {"label": "Long strike", "value": self.long_leg["strike"]},
                {"label": "Credit received", "value": round(self.credit_received_cash, 2)},
                {"label": "Expiry", "value": self.current_expiry.isoformat() if self.current_expiry else "—"},
            ]
        # Flat — same transparency this session's own strangle_monthly_v2
        # get_status_fields addition was built for: reuse the identical
        # trend/value display every SuperTrend sibling already shows, so
        # "why isn't this trading" is answered by the trend itself, not
        # silence. No pivots (see module docstring).
        return supertrend_status_fields(self.st, None)

    @staticmethod
    def status_fields_from_state(state: dict) -> Optional[list]:
        return supertrend_status_fields_from_state(state)

    async def on_post_market_checkpoint(self, runner) -> None:
        """See pivot_supertrend.py's identical method. Does NOT touch
        short_leg/long_leg/current_expiry — an open spread is already
        resume-safe via the DB regardless (Section 7)."""
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

    # ── Tick consumption ────────────────────────────────────────────────

    async def on_tick(self, runner, tick: dict) -> None:
        ts = tick.get("exchange_timestamp")
        price = tick.get("last_price")
        if ts is None or price is None:
            return

        # Orphan cleanup (Section 7) — highest priority, resolved on the
        # very first tick that can reach it after a resume, before
        # anything else runs.
        if self._orphan_leg is not None:
            await self._close_orphan_leg(runner, ts)

        day = ts.date()
        if self.today is None:
            self.today = day
        elif day != self.today:
            self._roll_over_day(runner)
            self.today = day

        # Profit-target/stop-loss — every tick, not just candle close
        # (Section 4). Checked BEFORE candle aggregation below: more
        # time-sensitive risk management takes priority over the (much
        # less frequent) signal update.
        if self.short_leg is not None and self.long_leg is not None:
            await self._maybe_manage_open_spread(runner, ts)

        completed = self.aggregator.add_tick(ts, price)
        if completed is None:
            return
        await self._on_candle_closed(runner, completed, ts)

    def _roll_over_day(self, runner) -> None:
        # Only job here is resetting the daily trade cap — no pivots to
        # recompute (see module docstring), SuperTrend itself runs
        # continuously across day boundaries, never reset.
        logger.info("%s: new trading day -> trade count reset", runner.deployment_name)
        self.trades_today = 0

    async def _on_candle_closed(self, runner, candle: dict, now: datetime) -> None:
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

        t = now.time()   # real wall-clock time, for the expiry-day cutoff
        before_cutoff = self.expiry_day_force_exit_time is None or t < self.expiry_day_force_exit_time
        after_open = self.market_open_time is None or candle["date"].time() >= self.market_open_time

        # 1 — expiry-day hard backstop (Section 5). Checked first, same
        # "hard backstop above everything else" priority strangle_
        # monthly_v2's own contract-expiry check uses.
        if (self.short_leg is not None and self.current_expiry is not None
                and self.expiry_day_force_exit_time is not None
                and candle["date"].date() >= self.current_expiry and t >= self.expiry_day_force_exit_time):
            await self._exit_spread(runner, candle["date"], "force_exit", {
                "current_expiry": self.current_expiry.isoformat(),
                "expiry_day_force_exit_time": self.expiry_day_force_exit_time.isoformat(),
            })

        # 2 — advance SuperTrend, detect a flip. A flip AWAY from the
        # open spread's own direction closes it (Section 3); a flip
        # (any direction) while flat is a fresh entry signal (Section
        # 2) — both act IMMEDIATELY, same candle-close event, same
        # "an exit can be immediately followed by a fresh entry"
        # precedent every SuperTrend sibling in this package uses.
        prev_trend_before_update = self.prev_trend
        new_trend = self.st.update(candle)
        if new_trend is not None:
            flipped = prev_trend_before_update is not None and new_trend != prev_trend_before_update
            if flipped and self.short_leg is not None:
                against = (self.spread_side == "bull_put" and new_trend == "down") or \
                          (self.spread_side == "bear_call" and new_trend == "up")
                if against:
                    trigger_values = {
                        "prev_trend": prev_trend_before_update, "new_trend": new_trend,
                        "close": round(candle["close"], 2),
                        "final_upper": round(self.st.final_upper, 2) if self.st.final_upper is not None else None,
                        "final_lower": round(self.st.final_lower, 2) if self.st.final_lower is not None else None,
                    }
                    await self._exit_spread(runner, candle["date"], "st_flip", trigger_values)

            if (flipped and not stale and self.short_leg is None and before_cutoff and after_open):
                if self.max_trades_per_day is not None and self.trades_today >= self.max_trades_per_day:
                    logger.info(
                        "%s: SuperTrend flipped (%s -> %s) but max_trades_per_day "
                        "(%d) already reached today — staying flat",
                        runner.deployment_name, prev_trend_before_update, new_trend,
                        self.max_trades_per_day,
                    )
                else:
                    trigger_values = {
                        "prev_trend": prev_trend_before_update, "new_trend": new_trend,
                        "close": round(candle["close"], 2),
                        "final_upper": round(self.st.final_upper, 2) if self.st.final_upper is not None else None,
                        "final_lower": round(self.st.final_lower, 2) if self.st.final_lower is not None else None,
                    }
                    await self._enter_spread(runner, candle, new_trend, trigger_values)
            self.prev_trend = new_trend

    # ── Execution ────────────────────────────────────────────────────────

    async def _enter_spread(self, runner, candle: dict, new_trend: str, trigger_values: dict) -> None:
        option_type = "PE" if new_trend == "up" else "CE"   # bullish -> sell puts, bearish -> sell calls
        spread_side = "bull_put" if new_trend == "up" else "bear_call"
        try:
            # Resolve the expiry FIRST — switch_to_next_week_on_expiry
            # (Section 2) needs the chance to override it before strike/
            # leg resolution ever happens, same two-step shape every
            # sibling's own _enter uses.
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
                        "same-day-expiry spread as resolved.",
                        runner.deployment_name, self.expiry_selector, expiry,
                    )
            short_leg = await self.resolver.get_otm_leg(
                self.options_underlying, expiry, option_type, steps=self.short_leg_otm_steps,
            )
            long_leg = await self.resolver.get_otm_leg(
                self.options_underlying, expiry, option_type,
                steps=self.short_leg_otm_steps + self.spread_width_steps,
            )
            prices = await self.resolver.get_ltp_many([short_leg, long_leg])
            short_price = prices.get(short_leg.key)
            long_price = prices.get(long_leg.key)
        except NoKiteSession:
            logger.warning(
                "%s: SuperTrend flip -> sell %s credit spread signal, but no "
                "Kite session yet — skipping", runner.deployment_name, spread_side,
            )
            return
        except Exception:
            logger.exception(
                "%s: failed to resolve/price the %s credit spread's legs for "
                "entry — skipping", runner.deployment_name, spread_side,
            )
            return

        if short_price is None or long_price is None:
            logger.warning(
                "%s: no live quote for one or both legs of the %s credit "
                "spread — skipping this entry", runner.deployment_name, spread_side,
            )
            return
        if short_leg.strike == long_leg.strike:
            logger.warning(
                "%s: short and long legs resolved to the SAME strike (%.2f) — "
                "chain too narrow for short_leg_otm_steps=%d + "
                "spread_width_steps=%d — skipping this entry",
                runner.deployment_name, short_leg.strike,
                self.short_leg_otm_steps, self.spread_width_steps,
            )
            return
        credit_per_unit = short_price - long_price
        if credit_per_unit <= 0:
            logger.warning(
                "%s: resolved short leg (%.2f) is not pricier than the long "
                "leg (%.2f) — net debit, not a credit spread (illiquid/stale "
                "quotes?) — skipping this entry",
                runner.deployment_name, short_price, long_price,
            )
            return

        qty = self.lots_per_trade * short_leg.lot_size
        runner.dispatcher.add_instruments([
            {"instrument_token": short_leg.instrument_token, "symbol": short_leg.tradingsymbol},
            {"instrument_token": long_leg.instrument_token, "symbol": long_leg.tradingsymbol},
        ])

        common = dict(
            spread_side=spread_side, option_type=option_type,
            expiry=expiry.isoformat(), exchange=short_leg.exchange,
        )
        short_meta = build_trade_meta(
            trigger="st_flip_entry", action=f"sell_open_short_{option_type}",
            trigger_values=trigger_values,
            resulting_state={"leg_role": "short", "strike": short_leg.strike, "symbol": short_leg.tradingsymbol},
            target_basis={
                "selection_basis": "OTM_STEPS", "otm_steps": self.short_leg_otm_steps,
                "selected_strike": short_leg.strike, "fill_premium": short_price,
            },
            leg_role="short", strike=short_leg.strike, **common,
        )
        await runner.sell(   # SELL TO OPEN — the premium-collecting leg
            short_leg.tradingsymbol, short_leg.instrument_token, qty, short_price, candle["date"],
            reason="entry", metadata=short_meta,
        )

        long_meta = build_trade_meta(
            trigger="st_flip_entry", action=f"buy_open_long_{option_type}",
            trigger_values=trigger_values,
            resulting_state={"leg_role": "long", "strike": long_leg.strike, "symbol": long_leg.tradingsymbol},
            target_basis={
                "selection_basis": "OTM_STEPS", "otm_steps": self.short_leg_otm_steps + self.spread_width_steps,
                "selected_strike": long_leg.strike, "fill_premium": long_price,
            },
            leg_role="long", strike=long_leg.strike, **common,
        )
        await runner.buy(   # BUY TO OPEN — the protective leg
            long_leg.tradingsymbol, long_leg.instrument_token, qty, long_price, candle["date"],
            reason="entry", metadata=long_meta,
        )

        self.short_leg = {
            "token": short_leg.instrument_token, "symbol": short_leg.tradingsymbol,
            "exchange": short_leg.exchange, "entry_price": short_price, "qty": qty,
            "strike": short_leg.strike,
        }
        self.long_leg = {
            "token": long_leg.instrument_token, "symbol": long_leg.tradingsymbol,
            "exchange": long_leg.exchange, "entry_price": long_price, "qty": qty,
            "strike": long_leg.strike,
        }
        self.spread_side = spread_side
        self.spread_option_type = option_type
        self.current_expiry = expiry
        self.credit_received_cash = credit_per_unit * qty
        # Counted here, not at flip-detection time above — this is the
        # point a real fill actually happened; a flip that fired but
        # never got filled (the early returns above) doesn't use up one
        # of today's slots.
        self.trades_today += 1

        await runner.notify_execution(
            "entry",
            f"Sold {spread_side} spread: short {short_leg.tradingsymbol}@{short_price:.2f}, "
            f"long {long_leg.tradingsymbol}@{long_price:.2f} (credit {credit_per_unit:.2f}/unit, "
            f"~₹{self.credit_received_cash:.2f} total)",
            metadata={"short": short_meta, "long": long_meta},
        )
        logger.info(
            "%s: SuperTrend flipped -> sold %s (short %s@%.2f / long %s@%.2f, "
            "credit ~₹%.2f)", runner.deployment_name, spread_side,
            short_leg.tradingsymbol, short_price, long_leg.tradingsymbol, long_price,
            self.credit_received_cash,
        )

    async def _maybe_manage_open_spread(self, runner, ts) -> None:
        short_price = runner.dispatcher.last_prices.get(self.short_leg["token"])
        long_price = runner.dispatcher.last_prices.get(self.long_leg["token"])
        if short_price is None or long_price is None:
            return   # wait for a live tick on both legs

        qty = self.short_leg["qty"]
        unrealized_short = (self.short_leg["entry_price"] - short_price) * qty
        unrealized_long = (long_price - self.long_leg["entry_price"]) * qty
        spread_pnl = unrealized_short + unrealized_long

        profit_target = self.profit_target_pct * self.credit_received_cash
        if spread_pnl >= profit_target:
            await self._exit_spread(runner, ts, "profit_target", {
                "spread_pnl": round(spread_pnl, 2), "profit_target": round(profit_target, 2),
                "credit_received_cash": round(self.credit_received_cash, 2),
            })
            return

        stop_loss = -self.stop_loss_multiple * self.credit_received_cash
        if spread_pnl <= stop_loss:
            await self._exit_spread(runner, ts, "stop_loss", {
                "spread_pnl": round(spread_pnl, 2), "stop_loss": round(stop_loss, 2),
                "credit_received_cash": round(self.credit_received_cash, 2),
            })

    async def _exit_spread(self, runner, when: datetime, reason: str, trigger_values: dict) -> None:
        if self.short_leg is None or self.long_leg is None:
            return
        short_pos = runner.open_positions.get(self.short_leg["token"])
        long_pos = runner.open_positions.get(self.long_leg["token"])
        if short_pos is None or long_pos is None:
            # Already closed out from under us (e.g. a manual force_close
            # on stop) — nothing more to do, just drop local tracking.
            self._clear_spread()
            return

        short_key = f"{self.short_leg['exchange']}:{self.short_leg['symbol']}"
        long_key = f"{self.long_leg['exchange']}:{self.long_leg['symbol']}"
        try:
            prices = await self.resolver.get_ltp_many([short_key, long_key])
            short_price = prices.get(short_key)
            long_price = prices.get(long_key)
        except Exception:
            short_price = long_price = None

        if short_price is None:
            short_price = runner.dispatcher.last_prices.get(self.short_leg["token"])
            if short_price is None:
                short_price = self.short_leg["entry_price"]
                logger.warning(
                    "%s: no live/last price for %s on exit (%s) — buying back "
                    "at its own entry_price %.2f (zero P&L on this leg)",
                    runner.deployment_name, self.short_leg["symbol"], reason, short_price,
                )
        if long_price is None:
            long_price = runner.dispatcher.last_prices.get(self.long_leg["token"])
            if long_price is None:
                long_price = self.long_leg["entry_price"]
                logger.warning(
                    "%s: no live/last price for %s on exit (%s) — selling at "
                    "its own entry_price %.2f (zero P&L on this leg)",
                    runner.deployment_name, self.long_leg["symbol"], reason, long_price,
                )

        qty = self.short_leg["qty"]
        common = dict(
            spread_side=self.spread_side, option_type=self.spread_option_type,
            expiry=self.current_expiry.isoformat() if self.current_expiry else None,
        )
        short_meta = build_trade_meta(
            trigger=reason, action=f"buy_close_short_{self.spread_option_type}",
            trigger_values=trigger_values, resulting_state={"leg_role": "short", "closing": True}, **common,
        )
        long_meta = build_trade_meta(
            trigger=reason, action=f"sell_close_long_{self.spread_option_type}",
            trigger_values=trigger_values, resulting_state={"leg_role": "long", "closing": True}, **common,
        )
        closed_short_symbol, closed_long_symbol = self.short_leg["symbol"], self.long_leg["symbol"]

        await runner.buy(    # BUY TO CLOSE the short leg
            self.short_leg["symbol"], self.short_leg["token"], qty, short_price, when,
            reason=reason, metadata=short_meta,
        )
        await runner.sell(   # SELL TO CLOSE the long leg
            self.long_leg["symbol"], self.long_leg["token"], qty, long_price, when,
            reason=reason, metadata=long_meta,
        )
        runner.dispatcher.release_instruments([self.short_leg["token"], self.long_leg["token"]])

        spread_pnl = (self.credit_received_cash) - ((short_price - long_price) * qty)
        await runner.notify_execution(
            "exit",
            f"{reason}: closed {self.spread_side} spread ({closed_short_symbol}@{short_price:.2f} / "
            f"{closed_long_symbol}@{long_price:.2f}, P&L ~₹{spread_pnl:.2f})",
            metadata={"short": short_meta, "long": long_meta},
        )
        self._clear_spread()

    async def _close_orphan_leg(self, runner, ts) -> None:
        leg = self._orphan_leg
        pos = runner.open_positions.get(leg["token"])
        if pos is None:
            self._orphan_leg = None
            return
        price = runner.dispatcher.last_prices.get(leg["token"], leg["entry_price"])
        meta = build_trade_meta(
            trigger="orphan_leg_cleanup", action="close_orphan",
            trigger_values={"note": "other leg of this spread was missing on resume"},
            resulting_state={"closing": True},
        )
        # side, not leg_role, decides the close direction here — this is
        # the one place in this strategy that has to fall back to it,
        # since the whole point is that the normal leg_role-paired
        # bookkeeping never got the chance to apply.
        if pos["side"] == "short":
            await runner.buy(leg["symbol"], leg["token"], leg["qty"], price, ts,
                             reason="orphan_leg_cleanup", metadata=meta)
        else:
            await runner.sell(leg["symbol"], leg["token"], leg["qty"], price, ts,
                              reason="orphan_leg_cleanup", metadata=meta)
        runner.dispatcher.release_instruments([leg["token"]])
        logger.info(
            "%s: closed orphaned leg %s @ %.2f", runner.deployment_name, leg["symbol"], price,
        )
        self._orphan_leg = None

    def _clear_spread(self) -> None:
        self.short_leg = None
        self.long_leg = None
        self.spread_side = None
        self.spread_option_type = None
        self.current_expiry = None
        self.credit_received_cash = 0.0

    async def on_stop(self, runner) -> None:
        tokens = []
        if self.short_leg is not None:
            tokens.append(self.short_leg["token"])
        if self.long_leg is not None:
            tokens.append(self.long_leg["token"])
        if self._orphan_leg is not None:
            tokens.append(self._orphan_leg["token"])
        if tokens:
            runner.dispatcher.release_instruments(tokens)
        logger.info(
            "%s: strategy stopped (trend=%s, spread=%s)",
            runner.deployment_name, self.st.trend, self.spread_side or "none",
        )
