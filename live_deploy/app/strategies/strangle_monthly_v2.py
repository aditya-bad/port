"""
live_deploy — strangle_monthly_v2: a monthly, checkpoint-cycling short
strangle with continuous + EOD rebalancing, optional convergence
handling, and optional protective-leg hedging.

Live paper-trading only — no backtested version exists for this one.
The most complex strategy in this family: variable-length legs per side
(same shape as `intraday_dtt_adjusted`), a position locked to a specific
CONTRACT (not just a signal state) for its entire life, checkpoint-
triggered re-entry that can itself land in a different contract than the
position it replaced, two independent adjustment mechanisms (a
continuous one and a fixed-time daily one) that must generalize
correctly whether a side currently has one or two legs, and three
selectable behaviors for what happens once the two strikes converge to
a straddle — one of which reuses `intraday_dtt_adjusted`'s own methods
directly rather than reimplementing that logic.

Section numbers below match the spec this was built from exactly, so a
reviewer can check any specific rule against the corresponding piece of
code without having to re-derive the mapping.

──────────────────────────────────────────────────────────────────────
1. INSTRUMENT & CONTRACT SELECTION
──────────────────────────────────────────────────────────────────────
`instrument`: "NIFTY" | "BANKNIFTY" | "SENSEX" | "BANKEX" (NSE/NFO or
BSE/BFO respectively — see Section 9).

Rotation rule, applied ONLY at the moment of a fresh entry (initial or a
checkpoint-triggered re-entry — see Section 4), NEVER re-evaluated on an
already-open position:
    day 1-15 of the calendar month  -> THIS_MONTH's contract
    day 16-end of month             -> NEXT_MONTH's contract
Once locked in at entry, `self.contract_expiry` (an actual `date`, not a
selector string) is used for EVERY resolver call this position ever
makes — every roll, every EOD adjustment, every hedge placement — for
its entire life, even if the calendar later crosses the 16th while the
position is still open. This is the single most important correctness
property of this strategy and is why every leg-resolution call below
passes `self.contract_expiry` explicitly rather than re-deriving a
selector from `ts.date()` mid-position.

HARD BACKSTOP (config `force_close_at_contract_expiry`, default True —
visible in config precisely so it can be turned off deliberately, never
silently assumed): this strategy has no daily time-based exit, so a
position that never hits its checkpoint and never converges could in
principle run until its own contract's expiry. Checked FIRST, above
everything else, every tick: if today >= self.contract_expiry and the
position is still open, force-close it regardless of any other state.

──────────────────────────────────────────────────────────────────────
2. ENTRY
──────────────────────────────────────────────────────────────────────
`entry_time` (default 10:00) gates every fresh entry — initial AND every
checkpoint-triggered re-entry — with ONE exception: `enter_immediately_on_deploy`
(default False) lets ONLY the very first entry this strategy instance
ever makes ignore the gate; every entry after that (including the very
first checkpoint re-entry) reverts to the normal entry_time rule, even
if it happens to fall before entry_time on some later day (e.g. a
checkpoint firing right at market open on day 2).

──────────────────────────────────────────────────────────────────────
3. POSITION SIZING
──────────────────────────────────────────────────────────────────────
    per-leg strike-selection premium target =
        (capital * strike_selection_capital_pct) / lot_size / 2

"capital" here is `runner.initial_capital` — the FIXED reference value
for the deployment's ENTIRE lifetime. `runner.cash` (the compounding
balance) is read by NEITHER this formula NOR the quantity calculation
below — REVISED from an earlier version of this file that scaled
`qty_multiplier` off `runner.cash`, which let quantity silently drift
upward as checkpoints compounded the account. Quantity is now simply:
    qty = lots_per_trade * lot_size
— fixed for as long as `lots_per_trade` itself isn't changed in config.
Every entry, roll, EOD-accumulation leg, and hedge across the entire
deployment resolves the SAME reference-sized strike (via
`get_leg_by_premium`, reused directly — see Step 3's own worked
examples: NIFTY capital=120000 -> target=36, BANKNIFTY -> target=72) at
the SAME quantity, with zero drift from account growth. Scaling to more
size is a deliberate config change (`lots_per_trade`), never automatic
behavior — this is the literal, no-longer-ambiguous reading of "never
inflate a single pair's premium target above the reference unit."

Both legs naked by default. Optional hedging: see "HEDGING" below —
kept in its own clearly-separated section since (per the spec) this
mechanism has never been combined with the rest of this strategy's
logic before and needs its own isolated verification.

──────────────────────────────────────────────────────────────────────
4. CHECKPOINT PROFIT TARGET
──────────────────────────────────────────────────────────────────────
`monthly_target_pct` (default 2%) is INFORMATIONAL ONLY — logged
alongside every checkpoint fill so cumulative progress against it is
visible, but never itself a hard stop ("a target to aim past... don't
artificially stop"). The actual live trigger is
`checkpoint_profit_pct_of_capital` (default 0.5% of CAPITAL — not
premium, unlike the DTT family's combined_premium_profit_pct; named
explicitly to say so, since a config-editing user comparing this
against a premium-based sibling could otherwise assume the same basis),
checked EVERY tick, same total-profit shape already proven in
`intraday_dtt_adjusted`:

    total_cycle_profit = self.cycle_realized_pnl
                        + unrealized P&L of every leg currently open
    target = checkpoint_profit_pct_of_capital * runner.initial_capital

REVISED from an earlier version of this file, which used the
compounding `runner.cash` here — the bar to clear each cycle is now
FIXED for the deployment's entire lifetime, the same reference value
Section 3's sizing already uses, not something that rises as the
account grows.

On hit: full flatten (both sides, all legs, same mechanism as
force-exit elsewhere in this family), THEN a fresh CE+PE pair opens
immediately (same tick, not deferred to the next — this isn't a
candle-driven signal-then-confirm situation, it's "target hit, redeploy
now"), independently RE-APPLYING Section 1's rotation rule against
TODAY's date — never carrying the just-closed position's contract
forward. `self.cycle_id` increments and `self.cycle_realized_pnl`
resets to 0.0 on every fresh entry (initial or checkpoint-triggered).

──────────────────────────────────────────────────────────────────────
5. CONTINUOUS ADJUSTMENT (all day, every day — pre AND post convergence)
──────────────────────────────────────────────────────────────────────
    trigger: smaller_side_current <= adjustment_trigger_ratio * bigger_side_current
    (default ratio 0.5; validated strictly between 0 and 1, same
    reasoning as intraday_dtt_adjusted's own validation)

The moving leg is always the DECAYED/WINNING side (smaller premium =
the side that's working, per this whole strategy family's convention);
the losing side's strike never moves.

IMPORTANT STRUCTURAL POINT, easy to get wrong by analogy with
intraday_dtt_advanced: this section ALWAYS REPLACES, never GROWS leg
count. Whether the triggering side currently has one leg or two
(the two-leg case only arises from a PRIOR Section 6 EOD accumulation —
this section itself never grows a side past its current count), the
action is the same: close the single CHEAPEST currently-open leg on
that side (tie-break: earliest-opened — same rule as reversal-unwind
elsewhere in this codebase), then open exactly ONE replacement, sized
so the resulting SIDE TOTAL (remaining leg(s)' current premium + new
leg's premium) lands at the midpoint of [adjustment_band_min,
adjustment_band_max] (default 80-95%) of the bigger side's current
premium. This is DIFFERENT from `intraday_dtt_advanced`'s roll (which
only replaces once AT a concurrent cap, and otherwise plainly ADDS) —
here, replacement happens EVERY time this trigger fires, regardless of
current leg count, and net leg count on the triggering side is
unchanged by this section alone. Only Section 6 ever grows a side's leg
count.

──────────────────────────────────────────────────────────────────────
6. DAILY EOD CHECK (eod_check_time, default 15:13 — fixed, every day,
   both pre- and post-convergence, per this section's own heading)
──────────────────────────────────────────────────────────────────────
15:13 specifically because it's just before NSE/BSE's Closing Auction
Session transition (constituent stocks stop continuous trading at 15:15
and enter a call auction) — the reference price for this check needs to
come from continuous trading, not auction-based price discovery. Do not
move this later without re-deriving why it was placed here.

    compare sum(all CE legs) vs sum(all PE legs)
    if smaller_side_sum < eod_gap_floor (default 80%) * bigger_side_sum:
        protected = whichever leg on that side has been open LONGEST
            (by an explicit open-order `seq` stamp, not list position --
            see "PROTECTED LEG" note below)
        if smaller_side has ONLY the protected leg (no extras yet):
            GROW — sell one additional leg on that side (protected leg
            untouched), sized so the side's NEW total lands at the
            midpoint of [eod_gap_floor, adjustment_band_max] (reusing
            Section 5's band config -- the spec's own "80-95% band"
            language for this section matches those defaults exactly,
            so this reuses the SAME two config keys rather than
            inventing EOD-specific ones not listed in the config
            schema). This is that side's first accumulation event.
        else (side already has 1 or 2 extras beyond the protected leg):
            REPLACE the cheapest of the EXTRAS ONLY (the protected leg
            is never a candidate here -- the spec's own phrasing,
            "square off whichever of the TWO [adjustment legs]", never
            includes the side's longest-held leg; tie-break: earliest-
            opened among the extras, same rule as everywhere else —
            FLAGGED default, not confirmed against source material, per
            the spec's own note) with one new leg, same band sizing.
            Net leg count on the side is unchanged either way, so this
            never stacks past the 1+max_adjustments cap once there's
            ever been a single extra.

PROTECTED LEG, Section 6 vs Section 5 -- a real, deliberate asymmetry:
Section 5's roll explicitly makes the ORIGINAL leg eligible ("the
original leg is eligible too... it competes on equal footing"); Section
6's accumulation/replacement NEVER touches whichever leg has been open
longest on that side, regardless of its role label. "Longest open" is
tracked via an explicit monotonic `seq` counter stamped on every leg at
open time, not list position -- Section 5's own roll can remove index 0
and append its replacement at the end, which would silently shift
"protection" onto a younger leg if list index were used instead.

This grow-then-replace-at-cap shape is structurally the SAME pattern
`intraday_dtt_advanced` uses for its own rolling cap — but it is NOT
implemented by calling into that strategy's code. Section 7 explicitly
reserves cross-strategy reuse for `convergence_mode=active_management`
specifically; this section is this strategy's own mechanism with its
own (different) trigger and sizing formula, so it gets its own
implementation, structured the same way on purpose for consistency.

──────────────────────────────────────────────────────────────────────
7. CONVERGENCE (strangle -> straddle)
──────────────────────────────────────────────────────────────────────
Detected when repeated Section 5/6 actions bring both sides down to
exactly one leg each, at the SAME strike. Checked once, right after any
Section 5/6 leg change; STICKY once detected — `self.converged` stays
True for the rest of this cycle even if a LATER Section 5/6 action
(both of which keep running post-convergence, see below) happens to
move a strike again. FLAGGED DESIGN DECISION: the spec doesn't address
whether convergence should be "sticky" or continuously re-evaluated;
sticky was chosen because oscillating between convergence-governed and
strangle-governed behavior on every subsequent roll seemed like the
less stable, more surprising choice, and `fixed_stop`'s own definition
("never recalculated") already implies convergence is meant to be a
one-time, not continuously re-derived, event.

  `convergence_mode: "fixed_stop"` (default): stop = convergence_stop_pct
      (default 10%) above the combined premium SNAPSHOTTED at the exact
      convergence moment; never recalculated afterward.
  `convergence_mode: "trailing_stop"`: same mechanism, but the stop is
      recalculated every tick as convergence_stop_pct above the CURRENT
      combined premium, letting it trail down as the position decays
      favorably.
  `convergence_mode: "active_management"`: does NOT watch a stop number
      at all. Instead, POST-convergence, Section 5's own trigger/roll is
      REPLACED (not run in parallel) by directly calling
      `intraday_dtt_adjusted`'s own `_handle_adjustment_trigger` /
      `_adjust` / `_unwind_one` methods, unmodified, against THIS
      strategy's own `self.legs`/`self.resolver`/etc. — see
      "ACTIVE-MANAGEMENT DELEGATION" below for exactly how that works
      without subclassing or reimplementing. Section 6 (EOD) is
      UNCHANGED and keeps using this strategy's own implementation even
      in this mode — `intraday_dtt_adjusted` has no EOD concept to
      delegate to.

In ALL three modes, Section 6 (EOD) keeps running unmodified post-
convergence (per that section's own explicit heading), and Section 4
(checkpoint) keeps running underneath everything, unchanged, at the
same top-of-priority position it had before convergence.

ACTIVE-MANAGEMENT DELEGATION — how "reuse the actual functions" works
without subclassing: `IntradayDTTAdjustedStrategy._adjust`,
`._unwind_one`, and `._handle_adjustment_trigger` are ordinary instance
methods that only touch attributes by NAME (`self.legs`, `self.resolver`,
`self.adjusted_side`, `self.adjustments_used`, `self.max_adjustments`,
`self.adjustment_size_pct`, `self.adjustment_strike_window`,
`self.options_underlying`, `self.expiry_selector`, `self.lots_per_trade`)
— nothing in them requires `self` to actually BE an
`IntradayDTTAdjustedStrategy` instance, only that it HAS those
attributes with compatible shapes. This strategy keeps its OWN
`self.legs` in the exact same `{"CE": [...], "PE": [...]}` /
`{token, symbol, exchange, entry_price, strike, role}` shape (see
Section 10), and sets `self.options_underlying = self.instrument`,
`self.expiry_selector = self.contract_expiry` (an actual `date` — which
`resolve_expiry` already accepts directly, honoring the contract lock
for free, with zero special-casing) before ever engaging
active_management.

The borrowed methods are bound onto `self` as ordinary instance
attributes in `on_start` (only when `convergence_mode ==
"active_management"`):
    self._adjust = IntradayDTTAdjustedStrategy._adjust.__get__(self)
    self._unwind_one = IntradayDTTAdjustedStrategy._unwind_one.__get__(self)
    self._handle_adjustment_trigger = \
        IntradayDTTAdjustedStrategy._handle_adjustment_trigger.__get__(self)
`.__get__(self)` is ordinary Python bound-method creation — this is NOT
just a convenience over the plain unbound-call form
`IntradayDTTAdjustedStrategy._adjust(self, ...)`, it's REQUIRED:
`_handle_adjustment_trigger`'s own body calls `self._adjust(...)`
internally (a `self.`-prefixed call, not an unbound one), and Python
resolves that against `self`'s ACTUAL class — `StrangleMonthlyV2Strategy`,
which doesn't define `_adjust` and doesn't inherit from
`IntradayDTTAdjustedStrategy` — so a plain unbound outer call alone
raises `AttributeError` the moment that inner call runs. Binding all
three names as instance attributes makes every `self.<name>(...)` call
inside those methods' own bodies resolve correctly too, since instance
attributes shadow class-level lookups. `_active_management_tick` then
calls `self._handle_adjustment_trigger(...)` / `self._unwind_one(...)`
like ordinary methods — no subclassing, no reimplementation, no
duplicated logic, literally the same functions, running against THIS
strategy's own state.

──────────────────────────────────────────────────────────────────────
8. EXIT / ACTION PRIORITY, every tick
──────────────────────────────────────────────────────────────────────
    1. Contract's own expiry (hard backstop, Section 1)
    2. Checkpoint profit target (Section 4 -- flatten + immediate re-entry)
    3. Post-convergence stop, if converged (Section 7)
    4. EOD 80% check, at eod_check_time only (Section 6)
    5. Continuous 50% adjustment trigger (Section 5) -- or, post-
       convergence under active_management, the delegated equivalent
Each of 1-4 stops that tick's evaluation immediately on firing.

──────────────────────────────────────────────────────────────────────
9. SENSEX / BANKEX (BSE) SUPPORT
──────────────────────────────────────────────────────────────────────
Spot price resolution needs NO changes — `OptionsResolver.get_spot_price`
already routes SENSEX/BANKEX through `INDEX_SPOT_SYMBOL` to BSE
correctly (verified by reading that code, not assumed), PROVIDED the
dispatcher is actually subscribed to the relevant spot token (see
tokens.json note below).

Options-chain resolution needs `exchange="BFO"` (BSE's F&O segment code
— confirmed absent from the codebase everywhere else by grep before
writing this). Since every `OptionsResolver` method already threads an
`exchange: Optional[str]` override through generically (built that way
already, not NSE-specific), and the constructor itself already accepts
an `exchange` DEFAULT (`OptionsResolver(dispatcher, exchange="NFO")`),
this strategy just constructs its resolver with
`OptionsResolver(runner.dispatcher, exchange="BFO")` for SENSEX/BANKEX
— every call this strategy makes then defaults to BFO automatically,
with no per-call override needed anywhere else in this file.

Lot size and strike step are NEVER hardcoded for these instruments —
`get_lot_size`/`get_strike_step` already derive both dynamically from
the live instrument master (confirmed by reading that code), which is
reused as-is; nothing SENSEX/BANKEX-specific was added to either.

THINGS THIS SESSION COULD NOT VERIFY (flagged explicitly, not silently
assumed correct — confirm before relying on SENSEX/BANKEX in
production):
  - Whether `kite.instruments("BFO")` actually returns SENSEX/BANKEX
    options rows with the expected shape (strike, expiry, tradingsymbol,
    lot_size, tick_size) — this session has no real Kite Connect
    session to test against; only the synthetic fake used throughout
    this codebase's test suite.
  - Whether the Kite Connect account/API key this deploys under
    actually has BSE F&O market data permissions — an account-level
    entitlement no amount of correct code can substitute for.
  - The exact SENSEX spot `instrument_token` added to tokens.json below
    (265) is from general knowledge of Zerodha's published instrument
    list, NOT independently re-verified against a live dump in this
    session — confirm it before relying on the live-tick-cache path;
    if wrong, `get_spot_price` still works correctly via its REST
    fallback, just without the cache optimization.

──────────────────────────────────────────────────────────────────────
10. POSITION-STATE
──────────────────────────────────────────────────────────────────────
`self.legs = {"CE": [...], "PE": [...]}`, same shape as
`intraday_dtt_adjusted` — `{token, symbol, exchange, entry_price,
strike, role}`, `role` = `"original"` or `f"adjustment_{n}"` (shared,
ever-incrementing sequence across BOTH Section 5 and Section 6 actions
— no permanent role reservation, matching `intraday_dtt_adjusted`'s own
established precedent: whichever leg is currently cheapest is eligible
to be replaced regardless of role, including the original).

Beyond that pattern, this strategy additionally tracks:
  - `self.contract_expiry` — the locked contract (Section 1).
  - `self.cycle_id` — increments on every fresh entry; stored in every
    leg's own opening-fill metadata, used on resume to know which
    closed-position records belong to the CURRENT (still-open) cycle
    versus an earlier, already-completed one.
  - `self.cycle_realized_pnl` — running realized P&L for the CURRENT
    checkpoint cycle (NOT `intraday_dtt_adjusted`'s single-day figure —
    a cycle here can span many trading days before its next checkpoint).
  - `self.converged`, `self.convergence_premium` — see Section 7.

RESUME-SAFETY reconstructs all of the above with the same rigor already
proven in `intraday_dtt_adjusted`: every open leg reattaches from
`runner.open_positions` (role/side/strike/cycle_id read back from that
leg's own stored metadata); `runner.list_closed_positions()` is
consulted to reconstruct `cycle_realized_pnl` from every CLOSED position
whose metadata `cycle_id` matches the currently-open legs' cycle_id
(NOT "closed today" the way `intraday_dtt_adjusted` does it — a cycle
here spans days, so day-boundary filtering would be wrong); the
adjustment role-sequence continues from the highest `adjustment_N` seen
across open AND closed-this-cycle legs (same reasoning as
`intraday_dtt_adjusted`'s lifetime counter); `contract_expiry` is read
directly from any open leg's own metadata (stored at entry, needs no
re-derivation); and `converged`/`convergence_premium` are reconstructed
from a dedicated metadata field stamped onto the fill that most
recently caused (or confirmed) convergence, read back from whichever of
open-or-closed-this-cycle records is newest.

──────────────────────────────────────────────────────────────────────
11-12. CONFIG / TRADE-REASON LOGGING
──────────────────────────────────────────────────────────────────────
See `default_config` below for the full schema (every key from the
spec's own schema, plus a small number of necessarily-added keys the
spec's schema didn't enumerate but its own prose requires — each
flagged in its own comment: `hedge_pct_bank`, `hedge_flat_premium`
(Section 3's "~10%" / "₹3-5" ranges need a concrete default),
`adjustment_strike_window` (the same search-window knob every other
strategy in this family already exposes for `get_leg_by_premium`).

Every fill this strategy places carries a `metadata` dict with, at
minimum: `trigger` (which of Section 8's five paths caused it),
`action` (`"open"`/`"close"`), `leg` (CE/PE), `strike`, `trigger_values`
(the actual numbers that made the condition true, sufficient to
independently re-verify it without cross-referencing other log lines),
`target_basis` (for opens: target premium aimed for, strike actually
selected, resulting fill premium), and `resulting_state` (a snapshot of
both sides' leg counts/strikes/roles immediately after this fill) — see
`_trade_meta()`. Same one-fill-per-action logging mechanism already
established for `intraday_dtt_adjusted`/`intraday_dtt_advanced`
(`runner.sell`/`runner.buy`'s own `metadata` parameter), just with a
richer, consistently-shaped payload.

──────────────────────────────────────────────────────────────────────
HEDGING (config `enable_hedging`, default False) — kept in its own
section deliberately; this mechanism has never been combined with the
rest of this strategy's logic before (the source material describing it
covered a different, simpler strategy) — test it in isolation before
trusting its interaction with checkpoints/convergence/EOD.
──────────────────────────────────────────────────────────────────────
Protective (long) legs, one per currently-open short leg, tracked in
`self.hedges: dict[int, dict]` keyed by the SHORT leg's own token (a
generalization from the source material's single-leg-per-side framing,
needed because this strategy can have up to 3 short legs on one side —
documented here as a deliberate extension, not an oversight).
Sizing: BANKNIFTY/BANKEX -> `hedge_pct_bank` (default 10%) of the short
leg's own premium; NIFTY/SENSEX -> flat `hedge_flat_premium` (default
₹4, the midpoint of the spec's given 3-5 range — 10% would be
unrealistically thin at NIFTY/SENSEX's typical premium levels, mirroring
the weekly strangle's own established reasoning, not a new invention).
Rolls maintain a fixed POINT distance from the short strike (not
premium) — recomputed once, at the moment a hedge is first placed for a
given short leg, then held fixed across that short leg's own life
(rolling only when the SHORT itself rolls, carrying the same point
offset to the new short's strike).
Roll order (REVERSED from entry's own CE-then-PE order, deliberately):
close old short -> close old protective -> open new protective -> open
new short — short exposure should never exist without its hedge already
in place, even for the few hundred milliseconds between two order
calls. Implemented via `_replace_leg()`, which resolves (read-only,
places no order) the new short's strike FIRST so the new protective's
point-offset placement can be computed before any order is placed, then
executes in the specified sequence.
KNOWN LIMITATION: `convergence_mode="active_management"`'s delegated
calls into `intraday_dtt_adjusted`'s own methods have ZERO hedging
awareness (those methods are reused verbatim, unmodified, specifically
per Section 7's instruction not to reimplement them) — hedging combined
with active_management is therefore NOT integrated in this version;
protective legs already open when active_management engages are left
in place untouched by the delegated logic, which will not roll or
close them. Flagged rather than silently broken.

KNOWN LIMITATION (narrowed by the Step 14 trade-reason-logging retrofit —
`intraday_dtt_adjusted._adjust`/`_unwind_one`/`_flatten_all` now build
their OWN `trigger`/`trigger_values`/`target_basis`/`resulting_state`
internally via the shared `build_trade_meta()` helper, duck-typed against
whatever `self` actually is, so those four fields ARE now present, with
real numbers, on fills placed BY the delegated `_adjust`/`_unwind_one`
call — this was NOT true before Step 14): what's STILL absent from those
specific fills is this strategy's OWN extra context —
`cycle_id`/`contract_expiry`/`converged`/`convergence_premium`/`seq` —
since the delegated methods have no way to know about this strategy's
own bookkeeping (`leg_role`/`leg`/`strike`/`expiry`/`exchange` ARE
present, `intraday_dtt_adjusted`'s own naming for the same concepts).
Every OTHER trigger path in this strategy — including Section 6/EOD legs
opened post-convergence under active_management — uses the full,
richer `_trade_meta()` schema (Section 12) as normal. Flagged rather
than silently inconsistent; see `_active_management_tick`'s own
docstring for the two other bridging fixes this delegation needs
(`realized_pnl_today` sync, missing `"seq"` backfill).
"""

import logging
from datetime import date
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver
from .intraday_dtt_adjusted import IntradayDTTAdjustedStrategy
from .pivot_supertrend import _parse_hhmm
from .registry import register_strategy

logger = logging.getLogger("live_deploy.strategies.strangle_monthly_v2")

OTHER_SIDE = {"CE": "PE", "PE": "CE"}
SUPPORTED_INSTRUMENTS = {
    "NIFTY": "NFO", "BANKNIFTY": "NFO", "SENSEX": "BFO", "BANKEX": "BFO",
}
BANK_INSTRUMENTS = {"BANKNIFTY", "BANKEX"}


@register_strategy(
    "strangle_monthly_v2",
    description="Checkpoint-cycling monthly short strangle on NIFTY/BANKNIFTY "
               "(NSE) or SENSEX/BANKEX (BSE): sells a CE+PE pair sized off "
               "capital, flattens and immediately re-enters (possibly in a "
               "different contract) every time a capital-based checkpoint "
               "fires, continuously rolls the decayed side, adds/replaces a "
               "second leg on a lagging side at a fixed daily check, and "
               "handles strangle-to-straddle convergence via a configurable "
               "stop or by delegating directly into intraday_dtt_adjusted's "
               "own adjustment machinery. Optional protective-leg hedging. "
               "Live paper-trading only.",
    default_config={
        # See on_start's own validation for exactly why this can't be
        # left empty/omitted: with no candle logic of its own, this is
        # ONLY here to give the deployment a live tick to drive its
        # clock forward at all -- DeploymentRunner filters every
        # incoming tick down to a deployment's own instrument_tokens
        # before ever calling the strategy, so an empty list means it
        # never gets called, ever, with zero error. NIFTY's own token
        # by default -- override to match whichever `instrument` this
        # deployment is actually configured for (BANKNIFTY/SENSEX/
        # BANKEX each need their own spot token, not this one).
        "instrument_tokens": [256265],
        "instrument": "NIFTY",
        "strike_selection_capital_pct": 0.03,
        "monthly_target_pct": 0.02,
        "checkpoint_profit_pct_of_capital": 0.005,
        "entry_time": "10:00",
        "enter_immediately_on_deploy": False,
        "enable_hedging": False,
        "hedge_pct_bank": 0.10,
        "hedge_flat_premium": 4.0,
        "adjustment_trigger_ratio": 0.5,
        "adjustment_band_min": 0.80,
        "adjustment_band_max": 0.95,
        "adjustment_strike_window": 40,
        "eod_check_time": "15:13",
        "eod_gap_floor": 0.80,
        "convergence_mode": "fixed_stop",
        "convergence_stop_pct": 0.10,
        "max_adjustments": 2,
        "force_close_at_contract_expiry": True,
        "lots_per_trade": 1,
    },
)
class StrangleMonthlyV2Strategy(StrategyBase):
    # A single cycle spans many calendar days (checkpoint-to-checkpoint,
    # potentially the better part of a month) -- "day" would be
    # meaningless here (see StrategyBase.ADJUSTMENT_GROUP_BY's own
    # docstring), so this groups by positions.metadata->>'cycle_id'
    # instead (see _trade_meta's own `leg_role` param below).
    ADJUSTMENT_GROUP_BY = "cycle_id"

    # ── Setup ────────────────────────────────────────────────────────────

    async def on_start(self, runner) -> None:
        cfg = runner.config
        # BUG FIX (confirmed against a real deployment, not theoretical):
        # this strategy has no candle/signal logic of its own -- it's
        # purely time-driven (entry_time, checkpoint, EOD, continuous
        # ratio) -- so it never referenced config.instrument_tokens
        # anywhere, and never validated it either. DeploymentRunner
        # (see its own module docstring) filters every incoming tick
        # down to THIS deployment's own instrument_tokens before ever
        # calling the strategy at all -- an empty/missing list means
        # ZERO ticks ever pass that filter, so on_tick, and therefore
        # every check inside it (entry, adjustment, EOD, everything),
        # never fires. The deployment sits "active" with 0 positions
        # forever, completely silently -- no error, no skipped-entry
        # log line, nothing -- because nothing ever calls in to
        # generate one. Four real deployments were found stuck exactly
        # this way (Nifty/BankNifty/Sensex/Bankex Strangle, all with
        # instrument_tokens=[]) before this check existed. Require
        # exactly one token, same shape every other strategy in this
        # codebase already enforces (see pivot_supertrend_options.py's
        # own identical check) -- it doesn't need to be a SPECIFIC
        # token (unlike a candle-driven strategy, this one only needs
        # ANY live tick to drive its own clock forward), just not zero.
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "strangle_monthly_v2 requires config.instrument_tokens to "
                f"be a ONE-ELEMENT list — without it, this deployment "
                f"receives NO ticks at all (see DeploymentRunner's own "
                f"tick-filtering) and silently never trades — got {tokens!r}"
            )
        self.instrument = str(cfg.get("instrument", "NIFTY")).strip().upper()
        if self.instrument not in SUPPORTED_INSTRUMENTS:
            raise ValueError(
                f"strangle_monthly_v2 requires config.instrument to be one of "
                f"{sorted(SUPPORTED_INSTRUMENTS)}, got {self.instrument!r}"
            )
        self.exchange = SUPPORTED_INSTRUMENTS[self.instrument]
        self.resolver = OptionsResolver(runner.dispatcher, exchange=self.exchange)
        # Attributes named EXACTLY as intraday_dtt_adjusted's own methods
        # expect, so those methods can be called unbound against this
        # instance for convergence_mode=active_management — see module
        # docstring's "ACTIVE-MANAGEMENT DELEGATION".
        self.options_underlying = self.instrument

        self.strike_selection_capital_pct = float(cfg.get("strike_selection_capital_pct", 0.03))
        self.monthly_target_pct = float(cfg.get("monthly_target_pct", 0.02))   # informational only
        # checkpoint_pct is the pre-rename name -- read as a fallback so
        # a deployment created before this rename keeps working unchanged.
        self.checkpoint_profit_pct_of_capital = float(
            cfg.get("checkpoint_profit_pct_of_capital", cfg.get("checkpoint_pct", 0.005))
        )
        self.entry_time = _parse_hhmm(cfg.get("entry_time", "10:00"))
        if self.entry_time is None:
            raise ValueError("strangle_monthly_v2 requires a non-null entry_time")
        self.enter_immediately_on_deploy = bool(cfg.get("enter_immediately_on_deploy", False))

        self.enable_hedging = bool(cfg.get("enable_hedging", False))
        self.hedge_pct_bank = float(cfg.get("hedge_pct_bank", 0.10))
        self.hedge_flat_premium = float(cfg.get("hedge_flat_premium", 4.0))

        self.adjustment_trigger_ratio = float(cfg.get("adjustment_trigger_ratio", 0.5))
        if not 0 < self.adjustment_trigger_ratio < 1:
            raise ValueError(
                f"adjustment_trigger_ratio must be strictly between 0 and 1, "
                f"got {self.adjustment_trigger_ratio}"
            )
        self.adjustment_band_min = float(cfg.get("adjustment_band_min", 0.80))
        self.adjustment_band_max = float(cfg.get("adjustment_band_max", 0.95))
        if not 0 < self.adjustment_band_min < self.adjustment_band_max < 1:
            raise ValueError(
                f"adjustment_band_min/max must satisfy 0 < min < max < 1, "
                f"got min={self.adjustment_band_min}, max={self.adjustment_band_max}"
            )
        self.adjustment_strike_window = int(cfg.get("adjustment_strike_window", 40))

        self.eod_check_time = _parse_hhmm(cfg.get("eod_check_time", "15:13"))
        if self.eod_check_time is None:
            raise ValueError("strangle_monthly_v2 requires a non-null eod_check_time")
        self.eod_gap_floor = float(cfg.get("eod_gap_floor", 0.80))
        if not 0 < self.eod_gap_floor < 1:
            raise ValueError(f"eod_gap_floor must be strictly between 0 and 1, got {self.eod_gap_floor}")

        self.convergence_mode = cfg.get("convergence_mode", "fixed_stop")
        if self.convergence_mode not in ("fixed_stop", "trailing_stop", "active_management"):
            raise ValueError(
                f"convergence_mode must be 'fixed_stop', 'trailing_stop', or "
                f"'active_management', got {self.convergence_mode!r}"
            )
        self.convergence_stop_pct = float(cfg.get("convergence_stop_pct", 0.10))

        if self.convergence_mode == "active_management":
            # `IntradayDTTAdjustedStrategy._handle_adjustment_trigger`
            # calls `self._adjust(...)` and `self._unwind_one` similarly
            # calls other `self.`-prefixed helpers internally — plain
            # unbound calls like `IntradayDTTAdjustedStrategy._adjust(
            # self, ...)` only work for the OUTERMOST call in a chain;
            # once that method's OWN body does `self._adjust(...)`,
            # Python resolves `_adjust` against this instance's actual
            # class (`StrangleMonthlyV2Strategy`), which doesn't define
            # it and doesn't inherit from `IntradayDTTAdjustedStrategy`
            # — a plain AttributeError. Binding the borrowed functions as
            # INSTANCE attributes here (via `.__get__(self)`, ordinary
            # bound-method creation) makes every `self.<name>(...)` call
            # inside those methods' own bodies resolve correctly too,
            # since instance attributes shadow class-level lookups. Only
            # done when this mode is actually selected — every other
            # mode leaves this strategy's own method set untouched.
            self._adjust = IntradayDTTAdjustedStrategy._adjust.__get__(self)
            self._unwind_one = IntradayDTTAdjustedStrategy._unwind_one.__get__(self)
            self._handle_adjustment_trigger = IntradayDTTAdjustedStrategy._handle_adjustment_trigger.__get__(self)

        self.max_adjustments = int(cfg.get("max_adjustments", 2))
        if self.max_adjustments < 1:
            raise ValueError(f"max_adjustments must be >= 1, got {self.max_adjustments}")
        self.force_close_at_contract_expiry = bool(cfg.get("force_close_at_contract_expiry", True))
        self.lots_per_trade = int(cfg.get("lots_per_trade") or 1)
        if self.lots_per_trade < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {self.lots_per_trade}")

        # Position state -- see module docstring's "POSITION-STATE".
        self.legs: dict[str, list[dict]] = {"CE": [], "PE": []}
        self.adjusted_side: Optional[str] = None    # only meaningful mid-active_management delegation
        self.adjustments_used = 0                    # shared role-label sequence, Sections 5 AND 6
        self.contract_expiry: Optional[date] = None
        self.cycle_id = 0
        self.cycle_realized_pnl = 0.0
        self.converged = False
        self.convergence_premium: Optional[float] = None

        self.hedges: dict[int, dict] = {}   # short leg token -> {token, symbol, exchange, entry_price, strike, point_distance}

        self.today: Optional[date] = None
        self._eod_fired_today = False
        self.entered_ever = False
        self._last_flatten_trigger: Optional[str] = None
        # Monotonic open-order counter, stamped onto every leg at open
        # time (both entry legs and later adjustment/EOD legs) — used by
        # Section 6 to find "whichever leg on this side has been open
        # LONGEST" reliably. NOT the same as list position: Section 5's
        # roll can remove index 0 and append its replacement at the end,
        # which would silently shift "protection" onto a younger leg if
        # list index were used instead of an explicit stamp.
        self._leg_seq = 0

        await self._resume_from_db(runner)

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
            self.today = day
            self._eod_fired_today = False
            # NOTE: legs/cycle state deliberately NOT reset here -- unlike
            # intraday_dtt_adjusted, a position (and a checkpoint cycle)
            # here can span many trading days; there is no daily boundary
            # reset in this strategy at all.

        await self._maybe_enter(runner, ts)
        if self.legs["CE"] or self.legs["PE"]:
            await self._maybe_manage(runner, ts, price)

    # ── Entry ────────────────────────────────────────────────────────────

    async def _maybe_enter(self, runner, ts) -> None:
        if self.legs["CE"] or self.legs["PE"]:
            return
        t = ts.time()
        is_very_first_entry = not self.entered_ever
        if not (is_very_first_entry and self.enter_immediately_on_deploy):
            if t < self.entry_time:
                return
        if is_very_first_entry:
            trigger = "initial_entry"
        else:
            # Not the very first entry, and NOT reached via the checkpoint
            # path's own direct _enter() call (that one labels itself
            # explicitly and never falls through to here) -- so whatever
            # flatten most recently emptied self.legs is why we're
            # re-entering now.
            trigger = f"reentry_after_{self._last_flatten_trigger or 'unknown'}"
        await self._enter(runner, ts, trigger=trigger)

    async def _enter(self, runner, ts, trigger: str) -> None:
        try:
            selector = "THIS_MONTH" if ts.date().day <= 15 else "NEXT_MONTH"
            expiry = await self.resolver.resolve_expiry(self.instrument, selector, reference_date=ts.date())
            lot_size = await self.resolver.get_lot_size(self.instrument)
            target_premium = (runner.initial_capital * self.strike_selection_capital_pct) / lot_size / 2
            ce_leg = await self.resolver.get_leg_by_premium(
                self.instrument, expiry, "CE", target_premium, strike_window=self.adjustment_strike_window,
            )
            pe_leg = await self.resolver.get_leg_by_premium(
                self.instrument, expiry, "PE", target_premium, strike_window=self.adjustment_strike_window,
            )
        except NoKiteSession:
            logger.warning(
                "%s: entry_time reached but no Kite session yet — skipping "
                "this entry attempt", runner.deployment_name,
            )
            return
        except Exception:
            logger.exception(
                "%s: failed to resolve/price the strangle for entry — "
                "skipping this entry attempt", runner.deployment_name,
            )
            return

        # Fixed for as long as `lots_per_trade` itself isn't changed in
        # config — never scales automatically off `runner.cash` (see
        # module docstring's Section 3, "REVISED"). Scaling to more size
        # is a deliberate config change, not something that drifts on
        # its own as checkpoints compound the account.
        qty = self.lots_per_trade * lot_size

        self.entered_ever = True
        self.cycle_id += 1
        self.contract_expiry = expiry
        self.expiry_selector = expiry   # for active_management delegation, see module docstring
        self.cycle_realized_pnl = 0.0
        self.converged = False
        self.convergence_premium = None
        self.adjusted_side = None
        self.adjustments_used = 0

        trigger_values = {
            "target_premium": target_premium,
            # `capital_ref` is what actually DRIVES sizing (fixed for
            # the deployment's lifetime); `capital_now` is a genuinely
            # useful record of what `runner.cash` happened to be at this
            # moment, kept for visibility only — it is NOT an input to
            # `qty` (see module docstring's Section 3, "REVISED").
            "capital_ref": runner.initial_capital, "capital_now": runner.cash,
            "day_of_month": ts.date().day, "rotation_selector": selector,
        }
        for side, leg in (("CE", ce_leg), ("PE", pe_leg)):
            price = leg.last_price
            runner.dispatcher.add_instruments([{"instrument_token": leg.instrument_token, "symbol": leg.tradingsymbol}])
            self._leg_seq += 1
            leg_dict = {
                "token": leg.instrument_token, "symbol": leg.tradingsymbol, "exchange": leg.exchange,
                "entry_price": price, "strike": leg.strike, "role": "original", "seq": self._leg_seq,
                "qty": qty,
            }
            self.legs[side].append(leg_dict)
            await runner.sell(
                leg.tradingsymbol, leg.instrument_token, qty, price, ts,
                reason=trigger,
                metadata=self._trade_meta(
                    trigger=trigger, action="open", side=side, strike=leg.strike,
                    fill_price=price, cycle_id=self.cycle_id,
                    contract_expiry=expiry.isoformat(), trigger_values=trigger_values,
                    target_basis={"target_premium": target_premium, "selected_strike": leg.strike, "fill_premium": price},
                    seq=leg_dict["seq"], leg_role="original",
                ),
            )
            if self.enable_hedging:
                await self._open_hedge_for_short_leg(runner, ts, side, leg_dict, trigger)

        # ONE notification for the whole strangle (both legs + any hedges
        # opened above), not one per fill.
        await runner.notify_execution(
            "entry",
            f"{trigger}: sold strangle — CE {ce_leg.tradingsymbol}@{ce_leg.last_price:.2f}, "
            f"PE {pe_leg.tradingsymbol}@{pe_leg.last_price:.2f} (cycle {self.cycle_id})",
            metadata={"cycle_id": self.cycle_id, "contract_expiry": expiry.isoformat()},
        )

        logger.info(
            "%s: entered strangle (%s) — CE %s@%.2f, PE %s@%.2f, contract=%s, "
            "qty=%d, cycle_id=%d", runner.deployment_name, trigger,
            ce_leg.tradingsymbol, ce_leg.last_price, pe_leg.tradingsymbol, pe_leg.last_price,
            expiry, qty, self.cycle_id,
        )

    # ── Priority dispatcher — Section 8 ─────────────────────────────────

    async def _maybe_manage(self, runner, ts, spot_price: float) -> None:
        t = ts.time()

        # 1 — contract expiry backstop
        if self.force_close_at_contract_expiry and self.contract_expiry is not None \
                and ts.date() >= self.contract_expiry:
            await self._flatten_all(
                runner, ts, "contract_expiry_backstop",
                {"contract_expiry": self.contract_expiry.isoformat(), "today": ts.date().isoformat()},
            )
            return

        prices: dict[int, float] = {}
        for side_legs in self.legs.values():
            for leg in side_legs:
                p = runner.dispatcher.last_prices.get(leg["token"])
                if p is None:
                    return   # wait for a live tick on every open leg
                prices[leg["token"]] = p

        # 2 — checkpoint profit target
        # NOTE: unrealized here is REAL CASH (entry-vs-current premium
        # difference * the leg's own qty), not a raw premium-point sum --
        # unlike intraday_dtt_adjusted's own analogous check (which stays
        # self-consistent by comparing a points-sum against a points-
        # based target derived from combined_entry_premium), this
        # strategy's target (`checkpoint_profit_pct_of_capital *
        # runner.initial_capital`)
        # is an explicit RUPEE figure, so the two sides of the comparison
        # would otherwise be in mismatched units whenever qty != 1 (i.e.
        # always, since qty = lots_per_trade * lot_size).
        #
        # `checkpoint_target` is fixed off `runner.initial_capital`, NOT
        # the compounding `runner.cash` — REVISED from an earlier version
        # of this file; see module docstring's Section 4, "REVISED".
        unrealized = sum(
            (leg["entry_price"] - prices[leg["token"]]) * leg["qty"]
            for side_legs in self.legs.values() for leg in side_legs
        )
        total_cycle_profit = self.cycle_realized_pnl + unrealized
        checkpoint_target = self.checkpoint_profit_pct_of_capital * runner.initial_capital
        if total_cycle_profit >= checkpoint_target:
            await self._flatten_all(
                runner, ts, "checkpoint_target",
                {
                    "cycle_realized_pnl": self.cycle_realized_pnl, "unrealized": unrealized,
                    "total_cycle_profit": total_cycle_profit, "checkpoint_target": checkpoint_target,
                    # Informational only, same reasoning as `capital_now`
                    # in the entry trigger_values -- NOT an input to
                    # `checkpoint_target` itself, which is fixed off
                    # `runner.initial_capital` above.
                    "capital_now": runner.cash,
                    "monthly_target_value": self.monthly_target_pct * runner.initial_capital,
                },
            )
            # "Immediately" per the spec means "same tick if already past
            # entry_time, no additional delay beyond that" — checkpoint
            # re-entries still respect entry_time like every other fresh
            # entry (Section 2). If we're before it, leave self.legs
            # empty and let the NEXT tick's _maybe_enter pick this up
            # once entry_time is reached (labelled
            # "reentry_after_checkpoint_target" via _last_flatten_trigger).
            if ts.time() >= self.entry_time:
                await self._enter(runner, ts, trigger="checkpoint_target")
            return

        # 3 — post-convergence stop, if converged
        if self.converged and self.convergence_mode in ("fixed_stop", "trailing_stop"):
            combined_now = sum(prices[l["token"]] for side_legs in self.legs.values() for l in side_legs)
            if self.convergence_mode == "fixed_stop":
                # Snapshot at the exact convergence moment, never
                # recalculated afterward -- literally that section's
                # own definition.
                stop_level = self.convergence_premium * (1 + self.convergence_stop_pct)
            else:
                # trailing_stop: the stop trails DOWN as combined premium
                # decays favorably, but never moves back up if premium
                # later rises again -- `_trailing_floor` tracks the
                # lowest combined premium seen since convergence, and the
                # stop is always convergence_stop_pct above THAT, not
                # above whatever the premium happens to be this tick.
                self._trailing_floor = min(getattr(self, "_trailing_floor", combined_now), combined_now)
                stop_level = self._trailing_floor * (1 + self.convergence_stop_pct)
            if combined_now >= stop_level:
                await self._flatten_all(
                    runner, ts, "convergence_stop",
                    {
                        "convergence_premium": self.convergence_premium, "combined_now": combined_now,
                        "stop_level": stop_level, "convergence_mode": self.convergence_mode,
                    },
                )
                return

        # Post-convergence FREEZE, fixed_stop/trailing_stop ONLY: once
        # converged under one of these two modes, the position is meant
        # to sit at EXACTLY the 2 legs it converged with until the stop
        # fires or checkpoint/contract-expiry closes it out — neither of
        # which is scoped to convergence state, so both are correctly
        # left alone by this guard. Letting Section 5 keep rolling would
        # silently turn the straddle back into a strangle, defeating the
        # whole point of a converged state; letting Section 6 keep
        # growing a side injects that new leg's own premium into
        # `combined_now`, corrupting the stop calculation with an
        # artifact that has nothing to do with real market movement
        # (e.g. converged at 600/stop 660, drifts to a harmless 630 —
        # then an EOD-added leg worth ~82.5 pushes combined_now to
        # 712.5, tripping the stop on zero real loss). active_management
        # is DELIBERATELY EXCLUDED from this freeze — it already governs
        # its own post-convergence leg changes via delegation (Section 5
        # replaced by `_active_management_tick`), and Section 6 (EOD) is
        # meant to keep running unmodified under it per that section's
        # own "both pre- and post-convergence" heading.
        frozen_post_convergence = self.converged and self.convergence_mode in ("fixed_stop", "trailing_stop")

        # 4 — EOD 80% check (frozen post-convergence under fixed_stop/
        # trailing_stop — see the freeze note above; otherwise runs both
        # pre- and post-convergence, unmodified, exactly as before)
        if not frozen_post_convergence and not self._eod_fired_today and t >= self.eod_check_time:
            self._eod_fired_today = True
            await self._eod_check(runner, ts, prices)
            return

        # 5 — continuous 50% adjustment trigger (or delegated equivalent
        # post-convergence/active_management) — frozen (does nothing at
        # all) post-convergence under fixed_stop/trailing_stop, per the
        # same freeze note above.
        if frozen_post_convergence:
            return
        if self.converged and self.convergence_mode == "active_management":
            await self._active_management_tick(runner, ts, prices)
        else:
            await self._continuous_adjustment_check(runner, ts, prices)

    # ── Section 4 / general: full flatten ───────────────────────────────

    async def _flatten_all(self, runner, ts, trigger: str, trigger_values: dict) -> None:
        for side in ("CE", "PE"):
            for leg in list(self.legs[side]):
                price = runner.dispatcher.last_prices.get(leg["token"])
                if price is None:
                    price = leg["entry_price"]
                    logger.warning(
                        "%s: no live price for %s on flatten (%s) — closing "
                        "at entry_price %.2f (zero P&L on this leg)",
                        runner.deployment_name, leg["symbol"], trigger, price,
                    )
                await self._close_leg(runner, ts, side, leg, trigger, trigger_values, _skip_list_remove=True)
            self.legs[side] = []
        # Remembered so `_maybe_enter`'s NEXT auto-re-entry (contract-
        # expiry-backstop and convergence-stop both go flat with no
        # direct follow-up _enter() call, unlike the checkpoint path,
        # which calls _enter() immediately itself and doesn't need this)
        # logs an accurate "why did we re-enter" trigger instead of
        # defaulting to "checkpoint_target" for every non-first entry.
        self._last_flatten_trigger = trigger
        # ONE notification for the whole flatten (every short + hedge leg
        # closed above), not one per leg -- `_close_leg` itself skips its
        # own per-leg notify when called from here (`_skip_list_remove`).
        await runner.notify_execution(
            "exit", f"{trigger}: flattened strangle (cycle {self.cycle_id})", metadata=trigger_values,
        )
        logger.info(
            "%s: flattened everything (%s) — cycle_realized_pnl=%.2f",
            runner.deployment_name, trigger, self.cycle_realized_pnl,
        )

    # ── Shared leg open/close primitives ────────────────────────────────

    async def _close_leg(
        self, runner, ts, side: str, leg: dict, trigger: str, trigger_values: dict,
        _skip_list_remove: bool = False,
    ) -> None:
        price = runner.dispatcher.last_prices.get(leg["token"])
        if price is None:
            price = leg["entry_price"]
        pos = runner.open_positions.get(leg["token"])
        if pos is not None:
            if not _skip_list_remove:
                self.legs[side].remove(leg)
            result = await runner.buy(
                leg["symbol"], leg["token"], float(pos["qty"]), price, ts,
                reason=trigger,
                metadata=self._trade_meta(
                    trigger=trigger, action="close", side=side, strike=leg["strike"],
                    fill_price=price, cycle_id=self.cycle_id, trigger_values=trigger_values,
                ),
            )
            if result.get("realized_pnl") is not None:
                self.cycle_realized_pnl += result["realized_pnl"]
            if not _skip_list_remove:
                # A standalone single-leg close (Section 5's roll,
                # Section 6's at-cap replace) -- its own distinct
                # execution. When called from `_flatten_all`'s loop
                # instead (`_skip_list_remove=True`), that caller sends
                # ONE notification for the whole flatten already -- skip
                # here to avoid one push per leg.
                await runner.notify_execution(
                    "exit", f"{trigger}: closed {leg['symbol']} ({side})",
                    metadata={"side": side, "cycle_id": self.cycle_id},
                )
        elif not _skip_list_remove:
            self.legs[side].remove(leg)
        runner.dispatcher.release_instruments([leg["token"]])
        if self.enable_hedging:
            await self._close_hedge_for(runner, ts, leg["token"], trigger)

    async def _open_leg(
        self, runner, ts, side: str, target_premium: float, trigger: str,
        trigger_values: dict, exclude_strikes=None,
        reversed_hedge_order: bool = False, hedge_point_distance: Optional[float] = None,
        prices: Optional[dict] = None,
    ) -> dict:
        """
        `reversed_hedge_order` / `hedge_point_distance`: set by callers
        that just closed the leg being replaced (Section 5's roll,
        Section 6's at-cap replace) — per the module docstring's
        "HEDGING" roll-order requirement, a REPLACE must place the new
        protective leg BEFORE the new short (reversed from how entry/
        grow do it, short-then-hedge, since there nothing existing is
        being closed first so there's no exposure gap to protect
        against). `hedge_point_distance`, if given, is the REPLACED
        leg's own hedge distance, carried forward onto the new leg
        instead of resolving a fresh premium-target hedge — "roll in
        lockstep... to maintain a fixed point-distance."
        """
        exclude = {l["strike"] for l in self.legs[side]}
        if exclude_strikes:
            exclude |= set(exclude_strikes)
        # Resolve (read-only, places no order) BEFORE any trade at all —
        # this is what lets a reversed-order hedge be sized off the new
        # short's strike before the short itself is ever sold.
        leg = await self.resolver.get_leg_by_premium(
            self.instrument, self.contract_expiry, side, max(target_premium, 0.5),
            strike_window=self.adjustment_strike_window, exclude_strikes=exclude,
        )
        price = leg.last_price
        self.adjustments_used += 1
        role = f"adjustment_{self.adjustments_used}"
        self._leg_seq += 1
        # Fixed, same as every other leg this deployment ever opens —
        # never scales off `runner.cash` (see module docstring's
        # Section 3, "REVISED").
        qty = self.lots_per_trade * leg.lot_size
        leg_dict = {
            "token": leg.instrument_token, "symbol": leg.tradingsymbol, "exchange": leg.exchange,
            "entry_price": price, "strike": leg.strike, "role": role, "seq": self._leg_seq,
            "qty": qty,
        }

        async def _sell_short():
            runner.dispatcher.add_instruments([{"instrument_token": leg.instrument_token, "symbol": leg.tradingsymbol}])
            self.legs[side].append(leg_dict)
            await runner.sell(
                leg.tradingsymbol, leg.instrument_token, qty, price, ts,
                reason=trigger,
                metadata=self._trade_meta(
                    trigger=trigger, action="open", side=side, strike=leg.strike,
                    fill_price=price, cycle_id=self.cycle_id, trigger_values=trigger_values,
                    target_basis={"target_premium": target_premium, "selected_strike": leg.strike, "fill_premium": price},
                    seq=leg_dict["seq"], leg_role=role,
                ),
            )

        if not self.enable_hedging:
            await _sell_short()
        elif reversed_hedge_order:
            # Hedge BEFORE short — the whole point of the reversed order.
            await self._resolve_and_open_hedge(runner, ts, side, leg, price, trigger, hedge_point_distance)
            await _sell_short()
        else:
            # Normal order (entry, or a Section-6 GROW with nothing being
            # closed alongside it) — short first, hedge second.
            await _sell_short()
            await self._resolve_and_open_hedge(runner, ts, side, leg, price, trigger, hedge_point_distance)

        # `_open_leg` is only ever called outside of `_enter` (which has
        # its own inline open loop + its own single notify) — a roll's
        # open-half (Section 5) or a grow/replace (Section 6), each its
        # own distinct execution.
        await runner.notify_execution(
            "entry", f"{trigger}: sold {leg.tradingsymbol} ({side})",
            metadata={"side": side, "cycle_id": self.cycle_id},
        )

        self._check_convergence(trigger, prices or {})
        return leg_dict

    # ── Section 5: continuous 50% trigger — always REPLACES in place ───

    async def _continuous_adjustment_check(self, runner, ts, prices: dict[int, float]) -> None:
        if not (self.legs["CE"] and self.legs["PE"]):
            return
        ce_sum = sum(prices[l["token"]] for l in self.legs["CE"])
        pe_sum = sum(prices[l["token"]] for l in self.legs["PE"])
        if ce_sum <= 0 or pe_sum <= 0:
            return
        if ce_sum >= pe_sum:
            bigger_side, smaller_side, bigger_sum, smaller_sum = "CE", "PE", ce_sum, pe_sum
        else:
            bigger_side, smaller_side, bigger_sum, smaller_sum = "PE", "CE", pe_sum, ce_sum

        if smaller_sum <= self.adjustment_trigger_ratio * bigger_sum:
            trigger_values = {
                "bigger_side": bigger_side, "smaller_side": smaller_side,
                "bigger_sum": bigger_sum, "smaller_sum": smaller_sum,
                "ratio": self.adjustment_trigger_ratio,
            }
            await self._roll_side(runner, ts, smaller_side, bigger_sum, prices, "continuous_50pct_trigger", trigger_values)

    async def _roll_side(
        self, runner, ts, side: str, bigger_sum: float, prices: dict[int, float],
        trigger: str, trigger_values: dict,
    ) -> None:
        legs_on_side = self.legs[side]
        # Tiebreak by `seq` (open order), not list position -- a prior
        # roll's remove()+append() can leave list order out of sync with
        # actual open order (see Section 6's own note on this).
        cheapest = min(legs_on_side, key=lambda l: (prices[l["token"]], l["seq"]))
        remaining_sum = sum(prices[l["token"]] for l in legs_on_side if l is not cheapest)
        band_mid = (self.adjustment_band_min + self.adjustment_band_max) / 2
        band_target_total = band_mid * bigger_sum
        new_leg_target = band_target_total - remaining_sum

        # Capture the closed leg's own hedge distance BEFORE closing it
        # (which pops it from self.hedges) -- this is a REPLACE, so the
        # reversed hedge-before-short order applies (see module
        # docstring's "HEDGING").
        old_hedge = self.hedges.get(cheapest["token"])
        point_distance = old_hedge["point_distance"] if old_hedge else None
        await self._close_leg(runner, ts, side, cheapest, trigger, trigger_values)
        await self._open_leg(
            runner, ts, side, new_leg_target, trigger, trigger_values,
            reversed_hedge_order=True, hedge_point_distance=point_distance, prices=prices,
        )

    # ── Section 6: EOD 80% check — grows under cap, replaces at cap ────

    async def _eod_check(self, runner, ts, prices: dict[int, float]) -> None:
        if not (self.legs["CE"] and self.legs["PE"]):
            return
        ce_sum = sum(prices[l["token"]] for l in self.legs["CE"])
        pe_sum = sum(prices[l["token"]] for l in self.legs["PE"])
        if ce_sum <= 0 or pe_sum <= 0:
            return
        if ce_sum >= pe_sum:
            bigger_side, smaller_side, bigger_sum, smaller_sum = "CE", "PE", ce_sum, pe_sum
        else:
            bigger_side, smaller_side, bigger_sum, smaller_sum = "PE", "CE", pe_sum, ce_sum

        if smaller_sum >= self.eod_gap_floor * bigger_sum:
            return   # within floor, nothing to do

        trigger_values = {
            "bigger_side": bigger_side, "smaller_side": smaller_side,
            "bigger_sum": bigger_sum, "smaller_sum": smaller_sum,
            "eod_gap_floor": self.eod_gap_floor, "check_time": ts.time().isoformat(),
        }
        band_mid = (self.eod_gap_floor + self.adjustment_band_max) / 2
        band_target_total = band_mid * bigger_sum
        side_legs = self.legs[smaller_side]
        # The PROTECTED leg is whichever on this side has been open
        # LONGEST (lowest `seq`) -- Section 6, unlike Section 5, never
        # touches it ("square off whichever of the TWO [adjustment
        # legs]..." -- the spec's own phrasing never includes the side's
        # longest-held leg as a candidate). Deliberately keyed by `seq`,
        # not list position -- Section 5's own roll can remove index 0
        # and append its replacement at the end, which would silently
        # shift "protection" onto a younger leg if list index were used.
        protected = min(side_legs, key=lambda l: l["seq"])
        extras = [l for l in side_legs if l is not protected]

        if not extras:
            # GROW: first accumulation event on this side -- 1 leg -> 2,
            # protected leg untouched.
            new_leg_target = band_target_total - prices[protected["token"]]
            await self._open_leg(runner, ts, smaller_side, new_leg_target, "eod_gap_check", trigger_values, prices=prices)
        else:
            # REPLACE: side already has 1 or 2 extras (2 = at the cap) --
            # tiebreak among the EXTRAS ONLY (never the protected leg),
            # lowest current premium wins, ties toward earliest-opened.
            # Net leg count on this side is unchanged either way, so the
            # cap ("never stacks a third leg") is respected automatically
            # once there's ever been a single extra.
            cheapest_extra = min(extras, key=lambda l: (prices[l["token"]], l["seq"]))
            remaining_sum = sum(prices[l["token"]] for l in side_legs if l is not cheapest_extra)
            new_leg_target = band_target_total - remaining_sum
            # Same reversed hedge-before-short ordering as Section 5's
            # roll -- this is ALSO a replace (close then open).
            old_hedge = self.hedges.get(cheapest_extra["token"])
            point_distance = old_hedge["point_distance"] if old_hedge else None
            await self._close_leg(runner, ts, smaller_side, cheapest_extra, "eod_gap_check", trigger_values)
            await self._open_leg(
                runner, ts, smaller_side, new_leg_target, "eod_gap_check", trigger_values,
                reversed_hedge_order=True, hedge_point_distance=point_distance, prices=prices,
            )

    # ── Section 7: convergence detection + active-management delegation ─

    def _check_convergence(self, trigger: str, prices: dict) -> None:
        if self.converged:
            return
        if len(self.legs["CE"]) == 1 and len(self.legs["PE"]) == 1 \
                and self.legs["CE"][0]["strike"] == self.legs["PE"][0]["strike"]:
            self.converged = True
            # Snapshot combined premium at THIS instant. The JUST-opened
            # leg's entry_price IS its current price (it has none other
            # yet); the OTHER (untouched) side's leg may have been opened
            # much earlier, so ITS live price (from `prices`, gathered at
            # the top of _maybe_manage before this call chain started)
            # must be used instead of its own possibly-stale entry_price.
            ce_leg, pe_leg = self.legs["CE"][0], self.legs["PE"][0]
            self.convergence_premium = (
                prices.get(ce_leg["token"], ce_leg["entry_price"])
                + prices.get(pe_leg["token"], pe_leg["entry_price"])
            )
            self._trailing_floor = self.convergence_premium
            logger.info(
                "convergence detected (strike=%.2f, combined_premium=%.2f, "
                "mode=%s, caused by %s)", self.legs["CE"][0]["strike"],
                self.convergence_premium, self.convergence_mode, trigger,
            )

    async def _active_management_tick(self, runner, ts, prices: dict[int, float]) -> None:
        """
        See module docstring's "ACTIVE-MANAGEMENT DELEGATION": calls
        intraday_dtt_adjusted's own methods, unbound, against this
        instance. `self.adjustment_size_pct` doesn't otherwise exist on
        this strategy -- intraday_dtt_adjusted's own default (25%) is
        used here since active_management means "hand this fully over to
        that strategy's own rules", including its own sizing fraction,
        not this strategy's band-based sizing.

        Two bridging fixes are required here because the delegated methods
        are used completely unmodified (per Section 7's own instruction
        not to reimplement them), and they were written against
        `intraday_dtt_adjusted`'s own attribute names, which don't all
        have a 1:1 counterpart on this strategy:

          - `_unwind_one`/`_flatten_all` accumulate into
            `self.realized_pnl_today` (a name this strategy doesn't
            otherwise use — its own equivalent is
            `self.cycle_realized_pnl`, scoped to the whole checkpoint
            cycle rather than a single day). Sync it in before delegating
            and back out after, so the delegated call's bookkeeping lands
            in the field this strategy's checkpoint/resume logic actually
            reads, instead of silently accumulating into an attribute
            nothing else here ever looks at.
          - `_adjust` appends a leg dict WITHOUT a `"seq"` key (that field
            is specific to this strategy's own Section 5/6 "which leg is
            protected" bookkeeping — `intraday_dtt_adjusted` has no such
            concept). Section 6 (EOD) keeps running unmodified post-
            convergence even under active_management (per that section's
            own heading), and its `min(..., key=lambda l: l["seq"])` would
            raise `KeyError` on such a leg — so any leg missing `"seq"`
            after a delegated call is stamped with one here, exactly as
            if it had been opened through this strategy's own `_open_leg`.
        """
        if not hasattr(self, "adjustment_size_pct"):
            self.adjustment_size_pct = 0.25   # intraday_dtt_adjusted's own default
        self.realized_pnl_today = self.cycle_realized_pnl
        try:
            if self.adjusted_side is None:
                if self.legs["CE"] and self.legs["PE"]:
                    ce_now = prices[self.legs["CE"][0]["token"]]
                    pe_now = prices[self.legs["PE"][0]["token"]]
                    if ce_now >= pe_now:
                        bigger_side, smaller_side, bigger_now, smaller_total = "CE", "PE", ce_now, pe_now
                    else:
                        bigger_side, smaller_side, bigger_now, smaller_total = "PE", "CE", pe_now, ce_now
                    if smaller_total <= self.adjustment_trigger_ratio * bigger_now:
                        # Call via `self.` (bound in on_start, see there)
                        # rather than the unbound `IntradayDTTAdjusted
                        # Strategy._handle_adjustment_trigger(self, ...)`
                        # form -- that method's OWN body calls
                        # `self._adjust(...)` internally, which only
                        # resolves correctly if `_adjust` was bound onto
                        # this instance too, not just this outer call.
                        await self._handle_adjustment_trigger(
                            runner, ts, smaller_side, bigger_now, prices,
                        )
            else:
                anchor_side = OTHER_SIDE[self.adjusted_side]
                if self.legs[anchor_side]:
                    bigger_now = prices[self.legs[anchor_side][0]["token"]]
                    smaller_total = sum(prices[l["token"]] for l in self.legs[self.adjusted_side])
                    if smaller_total <= self.adjustment_trigger_ratio * bigger_now:
                        await self._handle_adjustment_trigger(
                            runner, ts, self.adjusted_side, bigger_now, prices,
                        )
                        return
                    if len(self.legs[self.adjusted_side]) > 1 and smaller_total >= bigger_now:
                        await self._unwind_one(runner, ts, prices)
        finally:
            self.cycle_realized_pnl = self.realized_pnl_today
            for side_legs in self.legs.values():
                for leg_dict in side_legs:
                    if "seq" not in leg_dict:
                        self._leg_seq += 1
                        leg_dict["seq"] = self._leg_seq
                    if "qty" not in leg_dict:
                        # `_adjust` (delegated, unmodified) doesn't stamp
                        # a "qty" field either -- same reasoning as the
                        # "seq" backfill above, read the REAL qty back
                        # from the position `_adjust` itself already
                        # created via runner.sell(...), so the checkpoint
                        # unrealized-P&L calc (Section 4) never KeyErrors
                        # or silently uses a wrong quantity for this leg.
                        pos = runner.open_positions.get(leg_dict["token"])
                        leg_dict["qty"] = float(pos["qty"]) if pos is not None else 0.0

    # ── Hedging (isolated section — see module docstring) ───────────────

    def _hedge_target_premium(self, short_price: float) -> float:
        if self.instrument in BANK_INSTRUMENTS:
            return short_price * self.hedge_pct_bank
        return self.hedge_flat_premium

    async def _open_hedge_for_short_leg(self, runner, ts, side: str, short_leg: dict, trigger: str) -> None:
        """Entry/grow path only — short already exists (already sold, its
        token is real). See `_resolve_and_open_hedge` for the reversed-
        order (roll/replace) path, called BEFORE the short is sold."""
        class _Stub:   # duck-typed just enough for _resolve_and_open_hedge's needs
            pass
        stub = _Stub()
        stub.instrument_token = short_leg["token"]
        stub.strike = short_leg["strike"]
        await self._resolve_and_open_hedge(runner, ts, side, stub, short_leg["entry_price"], trigger, None)

    async def _resolve_and_open_hedge(
        self, runner, ts, side: str, short_leg_obj, short_price: float, trigger: str,
        point_distance: Optional[float] = None,
    ) -> None:
        """
        `short_leg_obj` needs only `.instrument_token`/`.strike` — an
        `OptionLeg` (roll/replace path, not yet sold) or the small stub
        `_open_hedge_for_short_leg` builds (entry/grow path, already
        sold). `point_distance`, if given, carries a REPLACED leg's own
        hedge distance forward instead of resolving a fresh premium
        target — "roll in lockstep... fixed point-distance."
        """
        try:
            if point_distance is not None:
                # Preserve the exact points-away-from-short offset —
                # OTM direction is the same as the short's own side (a
                # CE hedge sits ABOVE the short CE strike, a PE hedge
                # sits BELOW the short PE strike).
                sign = 1 if side == "CE" else -1
                target_strike = short_leg_obj.strike + sign * point_distance
                strikes = await self.resolver.list_strikes(self.instrument, self.contract_expiry, side)
                nearest = min(strikes, key=lambda s: abs(s - target_strike))
                hedge = await self.resolver.get_leg(self.instrument, self.contract_expiry, nearest, side)
                price = await self.resolver.get_ltp(hedge)
                target_for_log = None
            else:
                target_for_log = self._hedge_target_premium(short_price)
                hedge = await self.resolver.get_leg_by_premium(
                    self.instrument, self.contract_expiry, side, target_for_log,
                    strike_window=self.adjustment_strike_window, exclude_strikes={short_leg_obj.strike},
                )
                price = hedge.last_price
        except Exception:
            logger.exception(
                "%s: failed to open/roll a protective leg (side=%s) — short "
                "leg left UNHEDGED, will not retry automatically",
                runner.deployment_name, side,
            )
            return
        # Fixed, same as every other leg this deployment ever opens --
        # never scales off `runner.cash` (see module docstring's
        # Section 3, "REVISED").
        qty = self.lots_per_trade * hedge.lot_size
        runner.dispatcher.add_instruments([{"instrument_token": hedge.instrument_token, "symbol": hedge.tradingsymbol}])
        await runner.buy(
            hedge.tradingsymbol, hedge.instrument_token, qty, price, ts,
            reason=f"{trigger}_hedge",
            metadata=self._trade_meta(
                trigger=f"{trigger}_hedge", action="open", side=side, strike=hedge.strike,
                fill_price=price, cycle_id=self.cycle_id,
                target_basis={"target_premium": target_for_log, "selected_strike": hedge.strike, "fill_premium": price},
            ),
        )
        self.hedges[short_leg_obj.instrument_token] = {
            "token": hedge.instrument_token, "symbol": hedge.tradingsymbol, "exchange": hedge.exchange,
            "entry_price": price, "strike": hedge.strike,
            "point_distance": point_distance if point_distance is not None else abs(short_leg_obj.strike - hedge.strike),
        }

    async def _close_hedge_for(self, runner, ts, short_token: int, trigger: str) -> None:
        hedge = self.hedges.pop(short_token, None)
        if hedge is None:
            return
        price = runner.dispatcher.last_prices.get(hedge["token"]) or hedge["entry_price"]
        pos = runner.open_positions.get(hedge["token"])
        if pos is not None:
            await runner.sell(
                hedge["symbol"], hedge["token"], float(pos["qty"]), price, ts,
                reason=f"{trigger}_hedge",
                metadata=self._trade_meta(
                    trigger=f"{trigger}_hedge", action="close", side=None, strike=hedge["strike"],
                    fill_price=price, cycle_id=self.cycle_id,
                ),
            )
        runner.dispatcher.release_instruments([hedge["token"]])

    # ── Resume-safety ────────────────────────────────────────────────────

    async def _resume_from_db(self, runner) -> None:
        open_legs = [
            (token, pos) for token, pos in runner.open_positions.items()
            if pos["symbol"].endswith("CE") or pos["symbol"].endswith("PE")
        ]
        if not open_legs:
            # No open legs doesn't mean "never entered" -- it's also the
            # exact shape of the window between a flatten (checkpoint
            # target / contract-expiry backstop / convergence stop) and
            # the next _enter() actually landing (e.g. still waiting for
            # entry_time, or a transient resolver failure retrying every
            # tick — see _enter()'s except blocks). A restart caught in
            # that window used to silently reset entered_ever to False
            # and cycle_id/_leg_seq to 0 with nothing on the "no open
            # legs, so just return" path to catch it — wrongly making
            # the next entry look like the deployment's very-first-ever
            # entry again: re-triggering enter_immediately_on_deploy's
            # skip-the-entry_time-gate exception (which is meant to fire
            # ONCE, ever, per module docstring's Section 2) on a restart
            # that has nothing to do with a fresh deployment, and risking
            # a stale, long-closed cycle_id being reused by the next
            # _enter() — which would make _resume_from_db's own
            # cycle_realized_pnl reconstruction (below) sum in P&L from
            # that unrelated old cycle on a LATER restart, corrupting the
            # checkpoint-target comparison. Recover what we can from
            # closed-position history instead of assuming "flat now" ==
            # "never entered".
            closed = await runner.list_closed_positions()
            ever_closed_legs = [
                p for p in closed if p["symbol"].endswith("CE") or p["symbol"].endswith("PE")
            ]
            if not ever_closed_legs:
                return   # genuinely never entered — all the on_start defaults are correct as-is
            self.entered_ever = True
            for pos in ever_closed_legs:
                # `positions.metadata` is written once at OPEN and never
                # overwritten by the later close (see queries.record_fill)
                # — but cycle_id/seq are stamped identically at open time
                # and don't change, so reading them off this OPEN metadata
                # is exactly as reliable as reading them off the close.
                meta = pos["metadata"] or {}
                if meta.get("cycle_id") is not None:
                    self.cycle_id = max(self.cycle_id, int(meta["cycle_id"]))
                if meta.get("seq") is not None:
                    self._leg_seq = max(self._leg_seq, int(meta["seq"]))

            # _last_flatten_trigger, unlike cycle_id/seq above, genuinely
            # needs the CLOSE fill's OWN metadata (what trigger caused
            # THAT flatten) — which only ever lands in position_lots, not
            # positions. list_recent_lots() reads that table directly.
            recent_lots = await runner.list_recent_lots(limit=50)
            most_recent_close = next(
                (l for l in recent_lots
                 if l["action"] == "buy" and (l["metadata"] or {}).get("action") == "close"
                 and (l["metadata"] or {}).get("leg") in ("CE", "PE")),
                None,
            )
            if most_recent_close is not None:
                self._last_flatten_trigger = (most_recent_close["metadata"] or {}).get("trigger")
            logger.info(
                "%s: resumed flat (no open legs), but history shows a prior "
                "entry — entered_ever=True, cycle_id=%d, last_flatten_trigger=%s "
                "(preventing this restart from looking like a fresh first entry)",
                runner.deployment_name, self.cycle_id, self._last_flatten_trigger,
            )
            return

        for token, pos in open_legs:
            meta = pos["metadata"] or {}
            role = meta.get("leg_role", meta.get("role", "original"))
            side = meta.get("leg") or ("CE" if pos["symbol"].endswith("CE") else "PE")
            # `seq` reconstructs the open-order stamp Section 6 needs to
            # find "the side's longest-held leg" reliably (see that
            # section's own note) -- 0 is a safe fallback for any
            # pre-existing row from before this field was added, since it
            # sorts as "oldest", matching how a never-touched original
            # leg would naturally behave anyway.
            seq = int(meta.get("seq", 0))
            leg = {
                "token": token, "symbol": pos["symbol"], "exchange": meta.get("exchange", "NFO"),
                "entry_price": float(pos["avg_entry_price"]), "strike": float(meta.get("strike", 0.0)),
                "role": role, "seq": seq,
                # Read straight from the position row itself (ground
                # truth, always present) rather than metadata -- needed
                # by the checkpoint's unrealized-P&L calc (Section 4).
                "qty": float(pos["qty"]),
            }
            self.legs[side].append(leg)
            self._leg_seq = max(self._leg_seq, seq)
            runner.dispatcher.add_instruments([{"instrument_token": token, "symbol": pos["symbol"]}])
            if role != "original":
                self.adjusted_side = side
            if meta.get("cycle_id") is not None:
                self.cycle_id = max(self.cycle_id, int(meta["cycle_id"]))
            if meta.get("contract_expiry"):
                self.contract_expiry = date.fromisoformat(meta["contract_expiry"])
                self.expiry_selector = self.contract_expiry

        self.entered_ever = True
        any_leg_pos = next(iter(open_legs))[1]
        today = any_leg_pos["opened_at"].date()
        self.today = today

        closed = await runner.list_closed_positions()
        closed_this_cycle = [
            dict(p) for p in closed
            if (p["symbol"].endswith("CE") or p["symbol"].endswith("PE"))
            and (p["metadata"] or {}).get("cycle_id") == self.cycle_id
        ]
        for pos in closed_this_cycle:
            self.cycle_realized_pnl += float(pos["realized_pnl"] or 0.0)

        for role_source in (
            [l["role"] for side_legs in self.legs.values() for l in side_legs]
            + [(p["metadata"] or {}).get("leg_role", "original") for p in closed_this_cycle]
        ):
            if isinstance(role_source, str) and role_source.startswith("adjustment_"):
                self.adjustments_used = max(self.adjustments_used, int(role_source.split("_")[1]))

        # Convergence reconstruction: scan open-or-closed-this-cycle
        # records (either can carry the stamp — see _trade_meta) for a
        # convergence marker; closed ones checked oldest-first so the
        # LAST write (chronologically) wins if more than one is stamped.
        for token, pos in open_legs:
            meta = pos["metadata"] or {}
            if meta.get("converged"):
                self.converged = True
                self.convergence_premium = float(meta["convergence_premium"])
        for pos in sorted(closed_this_cycle, key=lambda p: p["closed_at"] or p["opened_at"]):
            meta = pos["metadata"] or {}
            if meta.get("converged"):
                self.converged = True
                self.convergence_premium = float(meta["convergence_premium"])
        if self.converged:
            self._trailing_floor = self.convergence_premium

        logger.info(
            "%s: resumed with %d CE / %d PE leg(s), contract=%s, cycle_id=%d, "
            "cycle_realized_pnl=%.2f, adjustments_used=%d, converged=%s",
            runner.deployment_name, len(self.legs["CE"]), len(self.legs["PE"]),
            self.contract_expiry, self.cycle_id, self.cycle_realized_pnl,
            self.adjustments_used, self.converged,
        )

    # ── Trade-reason metadata (Section 12) ──────────────────────────────

    def _trade_meta(
        self, trigger: str, action: str, side: Optional[str], strike: float, fill_price: float,
        cycle_id: Optional[int] = None, contract_expiry: Optional[str] = None,
        trigger_values: Optional[dict] = None, target_basis: Optional[dict] = None,
        seq: Optional[int] = None, leg_role: Optional[str] = None,
    ) -> dict:
        meta = {
            "trigger": trigger, "action": action, "leg": side, "strike": strike,
            "fill_price": fill_price, "cycle_id": cycle_id if cycle_id is not None else self.cycle_id,
            "trigger_values": trigger_values or {}, "target_basis": target_basis or {},
            "resulting_state": self._snapshot_state(),
        }
        if seq is not None:
            meta["seq"] = seq
        # Top-level (unlike `resulting_state`'s own per-leg "role", which
        # is a full-book snapshot, not specifically THIS fill's leg) --
        # "original" for a leg opened by _enter(), "adjustment_<n>" for
        # one opened later by _open_leg() (roll/grow/replace). Powers
        # GET /deployments/{id}/adjustment-histogram (Step 87), same
        # leg_role convention intraday_dtt_adjusted already established.
        # Only ever set going forward from Step 87 -- a leg opened before
        # this defaults to no leg_role at all when queried (COALESCE'd
        # to "original" there, same fallback intraday_dtt_adjusted uses
        # for its own pre-existing data).
        if leg_role is not None:
            meta["leg_role"] = leg_role
        if contract_expiry:
            meta["contract_expiry"] = contract_expiry
        elif self.contract_expiry:
            meta["contract_expiry"] = self.contract_expiry.isoformat()
        if self.converged:
            meta["converged"] = True
            meta["convergence_premium"] = self.convergence_premium
        if side is not None:
            meta["exchange"] = self.exchange
        return meta

    def _snapshot_state(self) -> dict:
        return {
            side: [{"strike": l["strike"], "role": l["role"], "token": l["token"]} for l in self.legs[side]]
            for side in ("CE", "PE")
        }

    async def on_stop(self, runner) -> None:
        tokens = [l["token"] for side_legs in self.legs.values() for l in side_legs]
        tokens += [h["token"] for h in self.hedges.values()]
        if tokens:
            runner.dispatcher.release_instruments(tokens)
        logger.info(
            "%s: strategy stopped (CE legs=%d, PE legs=%d, contract=%s, "
            "cycle_id=%d, cycle_realized_pnl=%.2f, converged=%s)",
            runner.deployment_name, len(self.legs["CE"]), len(self.legs["PE"]),
            self.contract_expiry, self.cycle_id, self.cycle_realized_pnl, self.converged,
        )
