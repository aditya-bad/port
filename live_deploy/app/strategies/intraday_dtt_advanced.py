"""
live_deploy — intraday_dtt_advanced: intraday_dtt_adjusted, but with
ROLLING adjustments (no lifetime cap on how many times a leg can be
replaced) and a configurable break-even band width.

Live paper-trading only — no backtested version exists for this one.

This is a SUBCLASS of `IntradayDTTAdjustedStrategy`, not a fork of that
file — see that module's docstring for exactly which two seams exist
there for this reuse (`self.breakeven_multiplier` and
`_handle_adjustment_trigger`). Entry, the profit-target check, the
break-even check (parameterized by multiplier), the reversal-unwind
trigger/mechanics, 3pm force-exit, the expiry-day exclusion, and the
"one entry attempt per day" rule are ALL inherited unchanged — every
one of those was already built and verified in intraday_dtt_adjusted,
and duplicating any of them here would just be a second place for the
two to drift apart the next time one gets a fix.

DIFFERENCE 1 — adjustments ROLL instead of permanently stopping:

  intraday_dtt_adjusted's `max_adjustments` is a LIFETIME counter: once
  spent, no more legs are EVER added that day, even as the imbalance
  keeps growing. Here, `max_adjustments` instead caps how many
  adjustment legs may be CONCURRENTLY open on the adjusted side (so the
  side never exceeds `1 + max_adjustments` legs total) — but there is no
  lifetime ceiling on how many times a full cycle can happen. Once the
  adjusted side is AT that concurrent cap and the adjustment trigger
  fires again (same trigger condition, same ratio, completely
  unchanged):

    1. Close the single CHEAPEST currently-open leg on that side — same
       selection and tie-break as ordinary reversal-unwind (lowest
       current premium wins; ties go to the earliest-opened leg). The
       ORIGINAL leg is eligible here too, exactly as it already is for
       reversal-unwind in the base class — nothing here special-cases
       "leave the original alone". The "1 original + N adjustments"
       framing describes the leg-COUNT ceiling for the side, not a
       permanent role reservation for any specific leg.
    2. Immediately sell ONE new leg on that side, target premium =
       `adjustment_size_pct * bigger_now` using the CURRENT bigger-side
       premium AT THE MOMENT OF THIS ROLL (never stale from an earlier
       trigger) — same `get_leg_by_premium(..., exclude_strikes=...)`
       call intraday_dtt_adjusted already uses for ordinary adjustments,
       excluding every strike still open on that side after the close.

  Net leg count on the adjusted side is unchanged by a roll (still
  `1 + max_adjustments`). If the imbalance keeps growing, this can roll
  again, and again, with no cap on the NUMBER of rolls in a day — only
  on how many legs are open at once.

  IMPLEMENTATION: this only overrides `_handle_adjustment_trigger`
  (see intraday_dtt_adjusted's docstring). The base class's own
  `_adjust`/`_unwind_one` are reused DIRECTLY, unmodified, for the
  "open"/"close" halves of a roll — `_unwind_one` already implements
  exactly the "close the single cheapest leg on `self.adjusted_side`,
  original included, ties toward earliest-opened" logic a roll's first
  step needs, and `_adjust` already implements exactly the "sell one new
  leg at the closest-premium strike, excluding strikes already held"
  logic the second step needs. A roll is genuinely just those two calls
  back to back, with distinct `reason` strings ("roll_close"/
  "roll_open") passed through so the trade history can tell a roll's
  two fills apart from an ordinary reversal-unwind or ordinary
  adjustment.

  `max_adjustments` HERE MEANS SOMETHING DIFFERENT than in
  intraday_dtt_adjusted (concurrent cap here, lifetime cap there) —
  same config key name, deliberately, since it plays the same structural
  role ("how many extra legs can exist on one side"), but the semantics
  genuinely differ between the two strategies. This is called out
  explicitly rather than left to be discovered by reading two files side
  by side.

  RESUME-SAFETY IMPLICATION: unlike intraday_dtt_adjusted's
  `adjustments_used` (a lifetime total that has to be reconstructed from
  BOTH open and closed-today history, since a since-unwound leg still
  counts toward it), the cap check here only ever needs "how many
  non-original legs are open on the adjusted side RIGHT NOW" —
  `len(self.legs[side]) - 1`. That's already exactly what the inherited
  `_resume_from_db()`'s leg-reattachment loop rebuilds from
  `runner.open_positions` alone, with no extra reconstruction step
  needed and no dependency on closed-today history for THIS specific
  purpose (closed-today history is still consulted for
  `realized_pnl_today`/`combined_entry_premium`/`entry_spot`, which
  every strategy in this family needs regardless — see
  intraday_dtt_adjusted's own "RESUME-SAFETY" section). This strategy
  therefore doesn't override `_resume_from_db` at all.

DIFFERENCE 2 — break-even band width is configurable:

  intraday_dtt_adjusted's break-even is always exactly
  `entry_spot ± combined_entry_premium` (an implicit 1.0x multiplier).
  Here, config `breakeven_multiplier` (default 1.0 — matches
  intraday_dtt_adjusted's behavior exactly when left at default) scales
  that band:

      breakeven_lower = entry_spot - breakeven_multiplier * combined_entry_premium
      breakeven_upper = entry_spot + breakeven_multiplier * combined_entry_premium

  e.g. at 1.1x with combined_entry_premium=1200: band=1320, giving
  [entry_spot-1320, entry_spot+1320] — wider than the 1.0x default, so
  this check fires later / less often than it would in
  intraday_dtt_adjusted with the same entry. Everything else about
  break-even is unchanged: computed once at entry from the ORIGINAL
  2-leg premium only (never recalculated as adjustment/roll legs add
  more premium), checked against the live underlying price, full
  flatten on breach, same priority position (below force-exit, above
  the profit target — see intraday_dtt_adjusted's "PRIORITY ORDER";
  identical here, unaffected by anything in this file).

  IMPLEMENTATION: no override needed at all — `self.breakeven_multiplier`
  is read from config in intraday_dtt_adjusted's own `on_start`
  (inherited, unchanged) and multiplies the band at both computation
  sites there; this strategy's `default_config` just documents/exposes
  the key.

STILL APPLIES, ALL INHERITED UNCHANGED FROM intraday_dtt_adjusted:
  - Entry: 10:00 ATM CE+PE, `allow_expiry_day_entry`/`catch_up_late_entry`,
    exactly one entry attempt per day, no same-day re-entry.
  - Profit target (`decay_pct`, default 10%): running total profit =
    realized P&L from every leg closed earlier today (via reversal-
    unwind OR a roll's close-half — both are ordinary closed positions
    with a `realized_pnl`, counted identically) + unrealized P&L of
    every currently-open leg, compared against
    `decay_pct * combined_entry_premium` (the ORIGINAL 2-leg premium,
    fixed for the day — this was already the confirmed reading in
    intraday_dtt_adjusted, not a new decision here).
  - 3:00 PM hard exit — closes everything regardless of state.
  - Priority order every tick: force_exit > break-even > profit_target >
    adjustment-or-roll > reversal-unwind. Identical to
    intraday_dtt_adjusted's, with "adjustment" now meaning "add, or roll
    if at the concurrent cap" instead of "add, or reject if at the
    lifetime cap" — the ONLY thing this file changes about that line.

CONFIG — identical to intraday_dtt_adjusted's own config surface (see
that module's docstring) PLUS:
  "max_adjustments": 2 (default) — now a CONCURRENT-leg cap (never more
      than `1 + max_adjustments` legs on the adjusted side at once), not
      a lifetime total.
  "breakeven_multiplier": 1.0 (default) — see "DIFFERENCE 2" above.

No margin model — same simplification as every other strategy in this
family.
"""

import logging

from .intraday_dtt_adjusted import IntradayDTTAdjustedStrategy
from .registry import register_strategy

logger = logging.getLogger("live_deploy.strategies.intraday_dtt_advanced")


@register_strategy(
    "intraday_dtt_advanced",
    description="intraday_dtt_adjusted with rolling adjustments instead of a "
               "lifetime cap: once the adjusted side hits max_adjustments "
               "concurrent legs, a further trigger closes the cheapest leg "
               "and immediately opens a new one sized off the current "
               "bigger-side premium, with no limit on how many times that "
               "can roll over the day. Also adds a configurable "
               "breakeven_multiplier (default 1.0, matching "
               "intraday_dtt_adjusted exactly). Live paper-trading only.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "expiry_selector": "THIS_WEEK",
        "entry_time": "10:00",
        "force_exit_time": "15:00",
        "decay_pct": 0.10,
        "adjustment_trigger_ratio": 0.5,
        "adjustment_size_pct": 0.25,
        "max_adjustments": 2,
        "adjustment_strike_window": 40,
        "breakeven_multiplier": 1.0,
        "lots_per_trade": 1,
        "catch_up_late_entry": True,
        "allow_expiry_day_entry": False,
    },
)
class IntradayDTTAdvancedStrategy(IntradayDTTAdjustedStrategy):

    async def _handle_adjustment_trigger(
        self, runner, ts, side: str, bigger_now: float, prices: dict[int, float],
    ) -> None:
        # "How many non-original legs are open on `side` right now" —
        # always re-derivable from current state (see this module's
        # docstring's "RESUME-SAFETY IMPLICATION"), never a lifetime
        # total. At most 1 leg exists on `side` before the first
        # adjustment ever fires, so this is always < max_adjustments on
        # that very first call (can't roll before anything to roll).
        concurrent_adjustment_legs = len(self.legs[side]) - 1
        if concurrent_adjustment_legs < self.max_adjustments:
            await self._adjust(runner, ts, side, bigger_now)
            return

        # At the concurrent cap: roll. Close the single cheapest leg on
        # `side` (original leg competes on equal footing — see this
        # module's docstring's "DIFFERENCE 1"), then immediately reopen
        # one, sized off `bigger_now` as passed in — which is always
        # THIS tick's current value, never stale from an earlier trigger,
        # since `_maybe_manage` (inherited, unmodified) computes it fresh
        # every tick before calling this method.
        legs_before = len(self.legs[side])
        await self._unwind_one(runner, ts, prices, reason="roll_close")
        await self._adjust(runner, ts, side, bigger_now, reason="roll_open")
        logger.info(
            "%s: rolled %s side at the concurrent cap (%d legs before, "
            "%d after)", runner.deployment_name, side, legs_before, len(self.legs[side]),
        )
