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
    day 1-10 of the calendar month  -> THIS_MONTH's contract
    day 11-end of month             -> NEXT_MONTH's contract
OVERRIDE on top of the day<=10 branch only: if THIS_MONTH's own expiry
date is already <=14 days out by the time of entry, roll straight to
NEXT_MONTH instead, even though the day-of-month alone would normally
still say THIS_MONTH. day<=10 is a fixed calendar proxy for "far enough
from expiry to be worth a fresh entry" — usually correct, but a monthly
expiry that lands unusually early in a given month (e.g. shifted earlier
by an exchange holiday) can make that proxy wrong on its own; this
checks the ACTUAL expiry date as a safety net rather than trusting the
day-of-month alone. Never applies in the day>10 branch — that one is
already NEXT_MONTH regardless, already at least ~2 weeks out.
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

LONG-DATED ENTRY OVERRIDE (`long_days_target_percentage`, default 5%):
`strike_selection_capital_pct` (default 3%) is tuned for a normal-length
entry, but a FRESH entry (initial or checkpoint re-entry, never a
roll/EOD/hedge — see below) whose resolved contract is more than 29 days
out prices strikes far enough OTM, this early, that live liquidity is
practically zero. At fresh-entry time only, if
`(expiry - entry_date).days > 29`, `long_days_target_percentage`
replaces `strike_selection_capital_pct` in the Step 3 formula above for
THAT entry's target_premium — a bigger target premium means a closer,
more liquid strike. EXCEPTION: if the entry date and the resolved
expiry fall in the SAME calendar month (year+month match) — a >29-day
gap is still possible here for a contract whose expiry lands very late
in the month — the normal `strike_selection_capital_pct` is used anyway,
even though the day-count alone would otherwise trigger the override.
This choice is made ONCE per fresh entry and is NOT re-derived for that
position's own rolls/EOD-accumulation/hedges — those resolve their
target premium from the CURRENT live band math (Sections 5/6), never
from either capital-pct config, so there is nothing for this override to
apply to there. Deployments created before this option existed are
backfilled to the same default (0.05 — see
custom_scripts/backfill_long_days_target_percentage.py) rather than
silently falling back to code-level default only in the DB's blind
spot; this deliberately DOES change their next fresh entry's strike
selection when the >29-day condition is met, which is the point
(fixing the too-far-OTM/illiquid entries this was added for) — it
never touches an already-open position's existing legs.

LIQUIDITY TIE-BREAK (`strike_liquidity_tolerance_pct`, default 5%, 0 =
off): EVERY leg this strategy opens by premium — fresh entry, Section 5
roll, Section 6 EOD replace, premium-targeted hedge, and
active_management's delegated `_adjust` (all go through this
deployment's own resolver, which carries the tolerance as its default)
— first computes its exact target premium exactly as before (capital
%, band midpoint, EOD midpoint, hedge target). Every strike whose LTP is
within ±tolerance of that target is then treated as an equally good
price match, and the one with the highest open interest that also
clears the liquidity bar (get_leg_by_premium's min_oi/max_spread_pct)
wins. Built for a real case: far-OTM NIFTY monthly 21150 being sold
over the far more liquid 21200, a few rupees apart in premium. If only
one strike is within tolerance, or none pass the bar, it falls back to
the plain closest-by-price strike — so the sold premium never drifts
more than the tolerance from target (at 5%, a band-midpoint roll lands
within roughly ±4 points of the 87.5% midpoint, always inside the
configured band). The strike-cross cap (after Section 6) still applies
last, and the point-distance hedge roll (fixed strike offset, not a
premium target) is unaffected. Fresh entries additionally keep the
older log-only `check_liquidity=True` warning on whatever was chosen.

STALE-PRICE GUARD: `dispatcher.last_prices` holds the last traded price
per contract, and a contract that hasn't traded yet this session still
carries the PREVIOUS session's close — e.g. at 09:15 the PE ticks while
the CE hasn't traded for a couple of minutes. Comparing a live PE
against yesterday's CE produced false 50%-trigger rolls (and fake
checkpoint profits), with the close filled at that stale price. Two
layers now prevent that, for every decision in Sections 2-5
(checkpoint, convergence stop, EOD check, continuous/active-management
adjustment); the Section 1 contract-expiry backstop is deliberately
exempt, it must close regardless:
  1. Freshness gate — no decision at all on a tick where any open short
     leg's cached price was last traded before today's 09:15 open
     (`dispatcher.last_price_times`). Management simply waits until
     every leg has traded today.
  2. Confirmation — when a trigger fires on tick prices, every leg is
     re-priced from a live quote() (bid/ask mid-price, falling back to
     last price when one side of the book is empty) and the trigger is
     re-checked on those prices; it only acts if it still holds, and the
     roll/EOD math then uses the confirmed prices. A trigger the quotes
     refute is not re-checked for 15 seconds (keeps quote() calls well
     inside Kite's rate limit). If quote() itself fails, the tick prices
     — already known fresh from layer 1 — are used, so a Kite REST
     hiccup can never stop the strategy from managing its position.
  3. A leg that doesn't trade — a thin contract (monthly BANKEX options
     can go a whole session without a trade) used to hold all
     management all day. From 09:20, a short leg with no trade yet today
     is priced from Kite's live quote instead: the bid/ask midpoint, and
     only when both sides of its book are quoted (a one-sided book holds,
     as before, and says so). Quoted at most every 15 seconds, the price
     kept for 90 if a quote fails; management, the trigger confirmation
     above and the leg's close all use it. Each new quoted price is logged
     (QUOTED) so the day check's replay, which has no order book, prices
     the leg as the app did.

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

`checkpoint_exit_not_before` (default None/disabled — existing
deployments are completely unaffected unless this is explicitly set):
an optional "HH:MM" floor below which a checkpoint-target hit is
deliberately NOT acted on. Added because the market's first couple of
ticks after open (9:15) can be noisy/gappy enough to spuriously satisfy
`checkpoint_target` on a tick that doesn't reflect a real, tradeable
price — if set (e.g. "09:17"), a target reached before this time is
simply left alone THIS tick (no flatten, no re-entry) and re-checked
fresh on every later tick exactly as if nothing had happened; every
other section (adjustments, rolls, EOD, convergence stop) keeps running
normally in the meantime. This is purely a "don't act on it yet" gate —
it never changes the target itself, and once the clock passes this
time, a still-live target is acted on immediately as usual.

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
count. Whether the triggering side currently has one leg or two (a
second leg can only ever exist via `convergence_mode=active_management`'s
own delegated growth post-convergence — see Section 7; NEITHER this
section nor Section 6 below ever grows a side's own leg count any
more), the action is the same: close the single CHEAPEST currently-open
leg on that side (tie-break: earliest-opened — same rule as
reversal-unwind elsewhere in this codebase), then open exactly ONE
replacement, sized so the resulting SIDE TOTAL (remaining leg(s)'
current premium + new leg's premium) lands at the midpoint of
[adjustment_band_min, adjustment_band_max] (default 80-95%) of the
bigger side's current premium. This is DIFFERENT from
`intraday_dtt_advanced`'s roll (which only replaces once AT a
concurrent cap, and otherwise plainly ADDS) — here, replacement happens
EVERY time this trigger fires, regardless of current leg count, and net
leg count on the triggering side is unchanged by this section alone.

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
        ALWAYS REPLACE — same shape as Section 5's own roll, just with
        eod_gap_floor as the band's lower bound instead of
        adjustment_trigger_ratio/adjustment_band_min: close the single
        CHEAPEST currently-open leg on that side (tie-break:
        earliest-opened, by an explicit open-order `seq` stamp, not
        list position — same rule as Section 5), then open exactly ONE
        replacement, sized so the side's NEW total lands at the
        midpoint of [eod_gap_floor, adjustment_band_max] (reusing
        Section 5's own adjustment_band_max — the spec's own "80-95%
        band" language for this section matches those defaults exactly,
        so this reuses that config key rather than inventing an
        EOD-specific one). Net leg count on the side is unchanged.

CONFIRMED FIX, NOT THE ORIGINAL READING (real bug, found against a live
deployment, not theoretical): this used to treat a side's FIRST breach
of eod_gap_floor as a GROW (sell one additional leg, leave whichever
leg had been open longest — the "protected" leg — untouched and
permanently immune to this check from then on) and only REPLACE
starting on a LATER breach, once an "extra" leg already existed
alongside the protected one. That directly defeated this section's own
stated purpose: closing the CE/PE premium gap so a big overnight
gap-up/gap-down can't produce a large, unhedged net loss between the
two sides. A side that had never rolled before could sit at a wide gap
indefinitely, since its one leg was exactly the "protected" one this
check would never touch. There is no more protected leg, on either
side, ever — every leg on the triggering side is an equally eligible
candidate to be the one closed, every time this fires, including the
side's very first (and only) leg. This also means neither Section 5 nor
Section 6 can grow a side's leg count any more (see Section 5's own
"IMPORTANT STRUCTURAL POINT" above) — the only way a side ever exceeds
1 leg now is `convergence_mode=active_management`'s own delegated
growth post-convergence (Section 7).

EOD ROLL-OUT (`eod_rollout_instead_of_straddle`, default True; this
section only — Section 5's intraday rolls are unchanged): if the
replacement above would reach or cross the TESTED (bigger) side's
strike — which the strike-cross cap below would turn into a straddle
right before the overnight gap — the untested side is left alone and
the TESTED side's leg nearest the market (highest premium) is rolled
OUT instead: closed, and re-sold further away at a premium sized so the
untested side's current total is the band midpoint (87.5% by default)
of the tested side's new total. The position goes overnight as a
balanced strangle, not a straddle; the roll is a net debit. Fills carry
reason "eod_rollout" and show as their own event type ("EOD roll-out")
in the cycle chart, the Linck session sheet, lanes and timelines.

──────────────────────────────────────────────────────────────────────
STRIKE-CROSS CAP (CONFIRMED requirement, applies to BOTH Section 5 and
Section 6, every roll, for as long as this is still a strangle)
──────────────────────────────────────────────────────────────────────
Neither section's own strike search (`OptionsResolver.get_leg_by_premium`,
inside `_open_leg`) has any awareness of the OTHER side's strike at all
— left alone, a smaller side that keeps needing more premium (a PE
climbing toward spot as its own strike rises, a CE descending toward
spot as its own strike falls) could roll to a strike AT OR PAST the
opposite side's own current strike, inverting the strangle into a
nonsensical crossed position (e.g. a PE strike sitting ABOVE its own
CE's strike). `_open_leg` now caps this: after resolving the naturally
closest-premium strike for a replacement leg, if that strike would
reach or cross the opposite side's own current strike (checked against
whichever single leg is open on that side — always exactly one,
pre-convergence, per the structural point above), the replacement opens
at the opposite side's EXACT strike instead of the premium-driven one.
This is what makes convergence (below) something Section 5/6 can
actually DRIVE TOWARD deterministically, not just something that might
happen to occur if a roll's premium target coincidentally lands on the
same strike the other side already holds.

──────────────────────────────────────────────────────────────────────
7. CONVERGENCE (strangle -> straddle)
──────────────────────────────────────────────────────────────────────
Detected the moment both sides hold exactly one leg each at the SAME
strike — either by coincidence (a Section 5/6 roll's own premium target
happens to land there) or FORCED (the strike-cross cap immediately
above snaps a roll to the opposite side's strike rather than letting it
overshoot past it). Checked once, right after any Section 5/6 leg
change; STICKY once detected — `self.converged` stays True for the
rest of this cycle even if a LATER Section 5/6 action (both of which
keep running post-convergence, see below) happens to move a strike
again. FLAGGED DESIGN DECISION: the spec doesn't address whether
convergence should be "sticky" or continuously re-evaluated; sticky was
chosen because oscillating between convergence-governed and
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

KNOWN LIMITATION — FLAGGED FOR LATER, NOT YET RESOLVED (raised by the
strategy owner, deliberately not fixed in the same pass as the
strike-cross cap above, since it needs its own separate confirmation
before touching real trading behavior): the strategy owner has stated
that once a position converges into a straddle, the STRANGLE's own
adjustment config — `eod_gap_floor`, `adjustment_band_min`,
`adjustment_band_max` — should no longer apply at all; only whichever
`convergence_mode` is configured should govern from that point on.
That is NOT what this file currently does under `active_management`
specifically: per the paragraph immediately above (and Section 6's own
heading), Section 6 (EOD) keeps firing post-convergence in that mode,
using `_eod_check` completely unmodified — i.e. still evaluating
`eod_gap_floor`/`adjustment_band_min/max` against a position that's
supposed to be governed by straddle rules instead. (Under `fixed_stop`/
`trailing_stop` this doesn't arise the same way — `_maybe_manage`'s own
`frozen_post_convergence` guard stops Section 6 from running in those
two modes, which is ALSO in tension with this very paragraph's claim
that Section 6 runs "in ALL three modes" — that claim is only actually
true for `active_management`; worth reconciling the text itself
against `_maybe_manage`'s real gating at the same time this is fixed.)
Needs its own explicit design decision (does Section 6 stop entirely
post-convergence under active_management too? does active_management's
own delegated EOD-equivalent, if any, replace it?) before changing —
not guessed at here.

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
re-derivation). `converged`, `convergence_premium` and the trailing
stop's low (`_trailing_floor`) are saved with the deployment's state
(get_persistable_state, checkpointed after every tick) and read back
for the same cycle. Where none was saved (a crash between the fill and
the checkpoint, or a position from before this was saved), one CE and
one PE at the same strike IS a straddle: converged, with the combined
premium at the fill that made it one (its fill price plus the other
side's price then, from its trigger values; for an entry, both entry
prices). A fill stamped `converged` (later fills of a converged cycle)
is read too. Before 28 Oct 2026 none of this was saved, and the fill
that made the straddle carried no stamp (it is written before
convergence is detected): any rebuild (a restart, the runner's re-sync
from the book, an automatic restart) brought a converged straddle back
as a strangle, and the 50% roll then closed and re-sold the same strike.

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
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .. import clock
from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver
from .intraday_dtt_adjusted import IntradayDTTAdjustedStrategy
from .pivot_supertrend import _parse_hhmm
from .registry import register_strategy

logger = logging.getLogger("live_deploy.strategies.strangle_monthly_v2")

_IST = ZoneInfo("Asia/Kolkata")

OTHER_SIDE = {"CE": "PE", "PE": "CE"}

# STALE-PRICE GUARD (module docstring): a leg last traded before this
# session's open is carrying the previous session's price.
_SESSION_OPEN = time(9, 15)
# The market's close. Kite keeps sending a few ticks after it, at the
# closing prices; nothing is traded on them (23 Oct: a 15:13 check held
# back until a 15:36 tick rolled a leg at prices no order could get).
_SESSION_CLOSE = time(15, 30)
# How long a trigger refuted by live quotes is treated as not fired.
_CONFIRM_COOLDOWN = timedelta(seconds=15)
# A short leg with no trade yet today, priced from its live quote (layer 3).
_QUOTE_STALE_FROM = time(9, 20)
_QUOTE_STALE_EVERY = timedelta(seconds=15)
_QUOTE_STALE_KEEP = timedelta(seconds=90)
QUOTED = "Priced from a live quote: not traded yet today"
# A trigger that keeps being refuted is noted in the decision log at most this often.
_REFUTE_NOTE_EVERY = timedelta(minutes=5)


def _naive_ist(dt: datetime) -> datetime:
    return dt.astimezone(_IST).replace(tzinfo=None) if dt.tzinfo is not None else dt


def _quote_mid(q: dict) -> Optional[float]:
    """Bid/ask mid from a kite.quote() entry, only when both sides are
    quoted: no last-price fallback (for a leg with no trade today, its
    last price is the previous session's)."""
    depth = q.get("depth") or {}
    buy, sell = depth.get("buy") or [], depth.get("sell") or []
    bid = buy[0].get("price") if buy else None
    ask = sell[0].get("price") if sell else None
    return (bid + ask) / 2 if bid and ask else None


def _quote_price(q: dict) -> Optional[float]:
    """Bid/ask mid from a kite.quote() entry, else its last_price."""
    depth = q.get("depth") or {}
    buy, sell = depth.get("buy") or [], depth.get("sell") or []
    bid = buy[0].get("price") if buy else None
    ask = sell[0].get("price") if sell else None
    if bid and ask:
        return (bid + ask) / 2
    return q.get("last_price")
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
        "long_days_target_percentage": 0.05,
        "monthly_target_pct": 0.02,
        "checkpoint_profit_pct_of_capital": 0.005,
        "checkpoint_exit_not_before": None,
        "entry_time": "10:00",
        "enter_immediately_on_deploy": False,
        "enable_hedging": False,
        "hedge_pct_bank": 0.10,
        "hedge_flat_premium": 4.0,
        "adjustment_trigger_ratio": 0.5,
        "adjustment_band_min": 0.80,
        "adjustment_band_max": 0.95,
        "adjustment_strike_window": 40,
        "strike_liquidity_tolerance_pct": 0.05,
        "eod_check_time": "15:13",
        "eod_gap_floor": 0.80,
        "eod_rollout_instead_of_straddle": True,
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
        # See module docstring's "LIQUIDITY TIE-BREAK" -- set on the
        # resolver itself so every premium-targeted pick (including the
        # delegated active_management `_adjust`) gets it.
        self.strike_liquidity_tolerance_pct = float(cfg.get("strike_liquidity_tolerance_pct", 0.05) or 0)
        if not 0 <= self.strike_liquidity_tolerance_pct < 1:
            raise ValueError(
                f"strike_liquidity_tolerance_pct must be in [0, 1), "
                f"got {self.strike_liquidity_tolerance_pct}"
            )
        self.resolver = OptionsResolver(
            runner.dispatcher, exchange=self.exchange,
            liquidity_tolerance_pct=self.strike_liquidity_tolerance_pct,
        )
        # See module docstring's "STALE-PRICE GUARD".
        self._confirm_cooldown_until: dict[str, datetime] = {}
        self._waiting_for_price = False
        self._roll_blocked_noted: dict[str, datetime] = {}   # side -> when a roll that couldn't resolve was last noted
        self._stale_logged: set = set()
        # Geek mode's decision log (runner.record_decision): what to note
        # next, and when each kind of refute was last noted.
        self._stale_report: Optional[list] = None
        self._stale_quotes: dict[int, tuple[float, datetime]] = {}   # token -> (bid/ask mid, when quoted)
        self._stale_quoted_at: Optional[datetime] = None
        self._stale_quote_logged: dict[int, float] = {}
        self._manage_ts: Optional[datetime] = None
        self._refute_noted: dict[str, datetime] = {}
        self._decision_days: set = set()
        # Attributes named EXACTLY as intraday_dtt_adjusted's own methods
        # expect, so those methods can be called unbound against this
        # instance for convergence_mode=active_management — see module
        # docstring's "ACTIVE-MANAGEMENT DELEGATION".
        self.options_underlying = self.instrument

        self.strike_selection_capital_pct = float(cfg.get("strike_selection_capital_pct", 0.03))
        # See module docstring's "LONG-DATED ENTRY OVERRIDE" (Section 3)
        # -- only ever consulted at fresh-entry time (_enter), never for
        # a roll/EOD-accumulation leg/hedge.
        self.long_days_target_percentage = float(cfg.get("long_days_target_percentage", 0.05))
        self.monthly_target_pct = float(cfg.get("monthly_target_pct", 0.02))   # informational only
        # checkpoint_pct is the pre-rename name -- read as a fallback so
        # a deployment created before this rename keeps working unchanged.
        self.checkpoint_profit_pct_of_capital = float(
            cfg.get("checkpoint_profit_pct_of_capital", cfg.get("checkpoint_pct", 0.005))
        )
        self.entry_time = _parse_hhmm(cfg.get("entry_time", "10:00"))
        if self.entry_time is None:
            raise ValueError("strangle_monthly_v2 requires a non-null entry_time")
        # See module docstring's Section 4 -- None (the default) means
        # exactly today's existing behavior, unchanged: a checkpoint hit
        # is acted on the instant it's seen, same as every deployment
        # already running before this existed.
        self.checkpoint_exit_not_before = _parse_hhmm(cfg.get("checkpoint_exit_not_before"))
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
        # See _eod_check: at 15:13, never turn the strangle into a straddle
        # right before the overnight gap; roll the tested side out instead.
        self.eod_rollout_instead_of_straddle = bool(cfg.get("eod_rollout_instead_of_straddle", True))
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
        self._trailing_floor: Optional[float] = None

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
        await self._resume_convergence(runner)

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

        if _naive_ist(ts).time() >= _SESSION_CLOSE:
            await self._decide_once_a_day(
                runner, ts, "market_closed", "blocked",
                "The market has closed: no entry, adjustment or end-of-day check until tomorrow's open"
                + ("" if self._eod_fired_today or not (self.legs["CE"] or self.legs["PE"])
                   else f" (today's {self.eod_check_time:%H:%M} end-of-day check didn't run before the close: skipped)"))
            self._eod_fired_today = True
            return

        await self._maybe_enter(runner, ts)
        if self.legs["CE"] or self.legs["PE"]:
            await self._maybe_manage(runner, ts, price)

    # ── Entry ────────────────────────────────────────────────────────────

    async def _maybe_enter(self, runner, ts) -> None:
        if self.legs["CE"] or self.legs["PE"]:
            return
        if runner.open_positions:
            # Flat by our own tracking but the database still holds
            # positions (left over from a close that failed earlier):
            # close them before any fresh entry, never trade on top.
            await runner.close_open_positions(ts, "untracked_close")
            if runner.open_positions:
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
            selector = "THIS_MONTH" if ts.date().day <= 10 else "NEXT_MONTH"
            expiry = await self.resolver.resolve_expiry(self.instrument, selector, reference_date=ts.date())
            # Override, day<=10 branch only: even within the window that
            # normally picks THIS_MONTH, if that contract's own expiry is
            # already <=14 days out, roll straight to NEXT_MONTH instead.
            # day<=10 is a fixed calendar proxy for "far enough from
            # expiry to be worth a fresh entry" -- usually accurate, but
            # a monthly expiry that falls unusually early in the month (a
            # holiday shifting it earlier) can make the proxy wrong
            # without this check. Never applies in the day>10 branch --
            # that one already means NEXT_MONTH regardless, already at
            # least ~2 weeks out, nothing to override.
            if selector == "THIS_MONTH" and (expiry - ts.date()).days <= 14:
                selector = "NEXT_MONTH"
                expiry = await self.resolver.resolve_expiry(self.instrument, selector, reference_date=ts.date())
            lot_size = await self.resolver.get_lot_size(self.instrument)
            # LONG-DATED ENTRY OVERRIDE -- see module docstring's Section
            # 3. A fresh entry resolved more than 29 days out from its
            # own expiry prices strikes so far OTM, this early, that
            # live liquidity is practically zero at the default (lower)
            # strike_selection_capital_pct -- unless entry and expiry
            # still land in the same calendar month (year+month match),
            # in which case the normal pct applies regardless of the
            # day count. Decided once, here, for this fresh entry only
            # -- never re-derived for this position's own rolls/EOD
            # legs/hedges, which price off live band math instead (see
            # Sections 5/6), not either capital-pct config.
            days_to_expiry = (expiry - ts.date()).days
            same_calendar_month = (
                ts.date().year == expiry.year and ts.date().month == expiry.month
            )
            long_days_override = days_to_expiry > 29 and not same_calendar_month
            effective_capital_pct = (
                self.long_days_target_percentage if long_days_override
                else self.strike_selection_capital_pct
            )
            if long_days_override:
                logger.info(
                    "%s: fresh entry %d days from expiry (%s), outside the "
                    "current calendar month -- using long_days_target_percentage "
                    "(%.4f) instead of strike_selection_capital_pct (%.4f) for "
                    "strike selection to avoid too-far-OTM/illiquid strikes.",
                    runner.deployment_name, days_to_expiry, expiry.isoformat(),
                    self.long_days_target_percentage, self.strike_selection_capital_pct,
                )
            target_premium = (runner.initial_capital * effective_capital_pct) / lot_size / 2
            # check_liquidity=True -- see module docstring's "LIQUIDITY
            # GUARD" and get_leg_by_premium's own docstring. Log-only for
            # now: never changes which strike gets picked, only fetches
            # one extra quote() over this same candidate window and logs
            # a warning if the strike about to be sold looks illiquid.
            # Fresh entries only (this call), not rolls/EOD legs/hedges
            # -- those price off live band math, not a capital-pct
            # target, so "closest to a target premium, maybe illiquid"
            # doesn't apply to them the same way.
            ce_leg = await self.resolver.get_leg_by_premium(
                self.instrument, expiry, "CE", target_premium, strike_window=self.adjustment_strike_window,
                check_liquidity=True,
            )
            pe_leg = await self.resolver.get_leg_by_premium(
                self.instrument, expiry, "PE", target_premium, strike_window=self.adjustment_strike_window,
                check_liquidity=True,
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
        self._trailing_floor = None
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
            # See module docstring's "LONG-DATED ENTRY OVERRIDE".
            "days_to_expiry": days_to_expiry, "long_days_override": long_days_override,
            "strike_selection_capital_pct": effective_capital_pct,
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

        # Both sides on one strike is a straddle from the start (Section 7).
        self._check_convergence(trigger, {ce_leg.instrument_token: ce_leg.last_price,
                                          pe_leg.instrument_token: pe_leg.last_price})

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
        note = getattr(runner, "note_decision", None)
        for side_legs in self.legs.values():
            for leg in side_legs:
                p = runner.dispatcher.last_prices.get(leg["token"])
                if p is None:
                    # wait for a live tick on every open leg; held for 10
                    # minutes, that's raised as an issue
                    self._waiting_for_price = True
                    if note is not None:
                        await note(ts, "leg_prices", "blocked",
                                   f"Holding all management: no price for {leg['symbol']}", fault=True)
                    return
                prices[leg["token"]] = p
        if self._waiting_for_price:
            self._waiting_for_price = False
            if note is not None:
                await note(ts, "leg_prices", "ok", "Every leg priced again")

        # STALE-PRICE GUARD layer 1 (see module docstring): no decision
        # until every open short leg has traded this session, or (layer
        # 3) is priced from a live two-sided quote.
        self._manage_ts = ts
        stale = self._stale_legs(runner, ts)
        quoted = await self._quote_stale(runner, ts, stale) if stale else None
        if quoted is not None:
            prices.update(quoted)
            if note is not None:
                await note(ts, "stale_price_guard", "ok", f"Priced {', '.join(stale)} from live quotes: not traded yet today")
        elif stale:
            if note is not None:   # every tick it holds: held long enough, it's raised as an issue
                await note(ts, "stale_price_guard", "blocked", "Holding all management: a short leg hasn't traded yet this session", fault=True)
            if self._stale_report:
                await self._decide(
                    runner, ts, "stale_price_guard", "blocked",
                    f"Holding all management: {', '.join(self._stale_report)} not traded yet this session",
                    {"legs": self._stale_report}, fault=True,
                )
                self._stale_report = None
            return

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
        def _cycle_profit(px: dict) -> float:
            return self.cycle_realized_pnl + sum(
                (leg["entry_price"] - px[leg["token"]]) * leg["qty"]
                for side_legs in self.legs.values() for leg in side_legs
            )

        total_cycle_profit = _cycle_profit(prices)
        checkpoint_target = self.checkpoint_profit_pct_of_capital * runner.initial_capital
        # See module docstring's Section 4 -- gated just means "not yet,"
        # not "ignored": skipping this whole block falls straight through
        # to Section 3 onward below, so the cycle keeps rolling/adjusting
        # normally in the meantime and gets re-evaluated fresh next tick.
        checkpoint_gated = (
            self.checkpoint_exit_not_before is not None
            and ts.time() < self.checkpoint_exit_not_before
        )
        confirmed = None
        if total_cycle_profit >= checkpoint_target and checkpoint_gated:
            await self._decide_once_a_day(
                runner, ts, "checkpoint_target", "no_action",
                f"Checkpoint reached (₹{total_cycle_profit:,.0f} ≥ ₹{checkpoint_target:,.0f}) but exits wait "
                f"until {self.checkpoint_exit_not_before.strftime('%H:%M')}",
                {"total_cycle_profit": round(total_cycle_profit, 2), "checkpoint_target": checkpoint_target},
            )
        if total_cycle_profit >= checkpoint_target and not checkpoint_gated:
            # STALE-PRICE GUARD layer 2: None = refuted by live quotes (or
            # cooling down after a refute) -- fall through as if not hit.
            confirmed = await self._confirm_trigger(
                runner, ts, "checkpoint_target", prices,
                lambda px: _cycle_profit(px) >= checkpoint_target,
            )
        if confirmed is not None:
            total_cycle_profit = _cycle_profit(confirmed)
            unrealized = total_cycle_profit - self.cycle_realized_pnl
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
            # Only once actually flat: a leg that failed to close stays
            # tracked and is retried first (via _maybe_manage next tick).
            if ts.time() >= self.entry_time and not runner.open_positions:
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
                self._trailing_floor = min(self._trailing_floor if self._trailing_floor is not None else combined_now, combined_now)
                stop_level = self._trailing_floor * (1 + self.convergence_stop_pct)
            confirmed = None
            if combined_now >= stop_level:
                confirmed = await self._confirm_trigger(
                    runner, ts, "convergence_stop", prices,
                    lambda px: sum(px[l["token"]] for sl in self.legs.values() for l in sl) >= stop_level,
                )
            if confirmed is not None:
                combined_now = sum(confirmed[l["token"]] for sl in self.legs.values() for l in sl)
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
        # left alone by this guard. Letting Section 5 OR Section 6 keep
        # rolling would silently turn the straddle back into a strangle
        # (a replacement leg lands at whatever strike currently hits the
        # target premium band, not necessarily the converged strike),
        # defeating the whole point of a converged state, and injects
        # that new leg's own freshly-resolved premium into `combined_now`,
        # corrupting the stop calculation with an artifact that has
        # nothing to do with real market movement (e.g. converged at
        # 600/stop 660, drifts to a harmless 630 — then an EOD replacement
        # leg worth ~82.5 pushes combined_now to 712.5, tripping the stop
        # on zero real loss). active_management
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
            if not self._eod_fired_today and t >= self.eod_check_time:
                self._eod_fired_today = True
                await self._decide(
                    runner, ts, "eod_gap_check", "no_action",
                    f"Skipped: converged into a straddle; only the {self.convergence_mode.replace('_', ' ')} "
                    "can close it now",
                    {"convergence_premium": self.convergence_premium},
                )
            return
        if self.converged and self.convergence_mode == "active_management":
            await self._active_management_tick(runner, ts, prices)
        else:
            await self._continuous_adjustment_check(runner, ts, prices)

    # ── STALE-PRICE GUARD (see module docstring) ────────────────────────

    async def _quote_stale(self, runner, ts, stale: list[str]) -> Optional[dict[int, float]]:
        """Layer 3: {token: bid/ask mid} for every short leg in `stale`
        (symbols with no trade yet today), from Kite's live quote, or None
        (before 09:20, a one-sided book, or no quote to be had): hold."""
        if _naive_ist(ts).time() < _QUOTE_STALE_FROM:
            return None
        legs = [leg for side_legs in self.legs.values() for leg in side_legs if leg["symbol"] in stale]
        fresh = self._stale_quoted_at is not None and ts - self._stale_quoted_at < _QUOTE_STALE_EVERY
        if fresh and all(leg["token"] in self._stale_quotes for leg in legs):
            return {leg["token"]: self._stale_quotes[leg["token"]][0] for leg in legs}
        keys = {leg["token"]: f"{self.exchange}:{leg['symbol']}" for leg in legs}
        try:
            quotes = await self.resolver.get_quotes(list(keys.values()))
        except Exception as e:
            kept = {t: v for t, v in self._stale_quotes.items() if t in keys and ts - v[1] <= _QUOTE_STALE_KEEP}
            if len(kept) == len(keys):
                return {t: v[0] for t, v in kept.items()}
            logger.warning("%s: no live quote for %s (%s) — holding", runner.deployment_name, ", ".join(stale), e)
            return None
        self._stale_quoted_at = ts
        out = {}
        for token, key in keys.items():
            mid = _quote_mid(quotes.get(key) or {})
            if mid is None:
                self._stale_quotes.pop(token, None)
                await self._decide_once_a_day(
                    runner, ts, "stale_price_guard", "blocked",
                    f"Holding: {key.split(':', 1)[1]} hasn't traded today and its quote has no bid and ask to price it from")
                return None
            out[token] = mid
            self._stale_quotes[token] = (mid, ts)
        changed = {t: px for t, px in out.items() if self._stale_quote_logged.get(t) != round(px, 2)}
        if changed:
            for t, px in changed.items():
                self._stale_quote_logged[t] = round(px, 2)
            sym = {leg["token"]: leg["symbol"] for leg in legs}
            await self._decide(runner, ts, "stale_price_guard", "no_action", QUOTED,
                               {"quote": {sym[t]: round(px, 2) for t, px in out.items()},
                                "tokens": {str(t): round(px, 2) for t, px in out.items()}})
        return out

    async def exit_price(self, runner, token: int, symbol: str, entry: float) -> tuple:
        """A leg priced from a live quote (layer 3) closes at that quote,
        not at the previous session's last trade still in the tick cache."""
        q = getattr(self, "_stale_quotes", {}).get(int(token))   # set by on_start
        at = getattr(self, "_manage_ts", None)
        if q is not None and at is not None and at - q[1] <= _QUOTE_STALE_KEEP:
            return float(q[0]), "quote"
        return await super().exit_price(runner, token, symbol, entry)

    def _stale_legs(self, runner, ts) -> list[str]:
        """Symbols of open short legs whose cached price was last traded
        before today's session open. A leg with no recorded trade time
        (ltp-mode ticks carry none) can't be judged and never blocks."""
        session_open = datetime.combine(_naive_ist(ts).date(), _SESSION_OPEN)
        stale = []
        for side_legs in self.legs.values():
            for leg in side_legs:
                traded_at = runner.dispatcher.last_price_times.get(leg["token"])
                if traded_at is not None and _naive_ist(traded_at) < session_open:
                    stale.append(leg["symbol"])
        if stale:
            key = (session_open.date(), tuple(sorted(stale)))
            if key not in self._stale_logged:
                self._stale_logged.add(key)
                self._stale_report = stale
                logger.info(
                    "%s: holding all position management — %s not traded yet this "
                    "session (cached price is from a previous session)",
                    runner.deployment_name, ", ".join(stale),
                )
        return stale

    async def _confirm_trigger(
        self, runner, ts, name: str, prices: dict[int, float],
        still_fires: Callable[[dict], bool],
    ) -> Optional[dict[int, float]]:
        """Re-prices every open short leg from a live quote() and re-checks
        `still_fires` on those prices. Returns the confirmed prices to act
        on, or None (refuted, or still cooling down from a recent refute)
        meaning: treat this trigger as not fired. If quotes can't be
        fetched, returns the tick prices unchanged — they already passed
        the freshness gate, and a REST hiccup must never block management."""
        until = self._confirm_cooldown_until.get(name)
        if until is not None and ts < until:
            return None
        legs = [leg for side_legs in self.legs.values() for leg in side_legs]
        keys = {leg["token"]: f"{self.exchange}:{leg['symbol']}" for leg in legs}
        try:
            quotes = await self.resolver.get_quotes(list(keys.values()))
        except Exception:
            logger.warning(
                "%s: %s fired but live quotes are unavailable — acting on tick prices",
                runner.deployment_name, name,
            )
            return prices
        confirmed: dict[int, float] = {}
        for token, key in keys.items():
            q = quotes.get(key)
            px = _quote_price(q) if q else None
            if px is None:
                logger.warning(
                    "%s: %s fired but no live quote for %s — acting on tick prices",
                    runner.deployment_name, name, key,
                )
                return prices
            confirmed[token] = px

        tick_view = {leg["symbol"]: prices[leg["token"]] for leg in legs}
        quote_view = {leg["symbol"]: round(confirmed[leg["token"]], 2) for leg in legs}
        if still_fires(confirmed):
            logger.info(
                "%s: %s confirmed by live quotes (tick %s, quote %s)",
                runner.deployment_name, name, tick_view, quote_view,
            )
            return confirmed
        self._confirm_cooldown_until[name] = ts + _CONFIRM_COOLDOWN
        noted = self._refute_noted.get(name)
        if noted is None or ts - noted >= _REFUTE_NOTE_EVERY:
            self._refute_noted[name] = ts
            await self._decide(
                runner, ts, name, "blocked",
                "Not acted on: live quotes don't confirm the tick prices",
                {"tick": tick_view, "quote": quote_view}, fault=True,
            )
        logger.warning(
            "%s: %s fired on tick prices %s but live quotes %s don't confirm it — "
            "not acting (re-checking in %ds)", runner.deployment_name, name,
            tick_view, quote_view, _CONFIRM_COOLDOWN.seconds,
        )
        return None

    # ── Section 4 / general: full flatten ───────────────────────────────

    async def _flatten_all(self, runner, ts, trigger: str, trigger_values: dict) -> None:
        # Each leg closes on its own: one that raises stays tracked (and
        # is retried next tick) without stopping the others. Then any
        # position still open that wasn't tracked -- a short or a hedge
        # orphaned by an earlier failure -- is closed too, so a full
        # flatten really leaves the deployment flat.
        failed = []
        for side in ("CE", "PE"):
            keep = []
            for leg in list(self.legs[side]):
                price, _src = await self.exit_price(runner, leg["token"], leg["symbol"], leg["entry_price"])
                try:
                    await self._close_leg(runner, ts, side, leg, trigger, trigger_values, _skip_list_remove=True)
                except Exception:
                    logger.exception(
                        "%s: could not close %s on flatten (%s) — keeping it tracked, retrying next tick",
                        runner.deployment_name, leg["symbol"], trigger,
                    )
                    if leg["token"] in runner.open_positions:
                        keep.append(leg)
                        failed.append(leg["symbol"])
            self.legs[side] = keep
        tracked = {leg["token"] for side_legs in self.legs.values() for leg in side_legs}
        orphans = {tok for tok in runner.open_positions if tok not in tracked}
        if orphans:
            swept = await runner.close_open_positions(ts, f"{trigger}_sweep", tokens=orphans)
            failed += swept["failed"]
        if failed:
            await runner.record_strategy_error(
                f"{trigger}: {len(failed)} leg(s) could not be closed yet ({', '.join(failed)}); retrying",
            )
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
        price, _src = await self.exit_price(runner, leg["token"], leg["symbol"], leg["entry_price"])
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

    async def _pick_leg(self, runner, side: str, target_premium: float, exclude_strikes=None,
                        without: Optional[dict] = None) -> tuple:
        """The (leg, price) `_open_leg` would sell on `side` — read-only,
        places nothing. `without`: a leg about to be closed, so not
        excluded as already held."""
        exclude = {l["strike"] for l in self.legs[side] if l is not without}
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

        # STRIKE-CROSS CAP (confirmed requirement — see module docstring's
        # new "STRIKE-CROSS CAP" note, right after Section 6): a strangle
        # roll's premium-driven strike search has no awareness of the
        # OTHER side's own strike at all — left unchecked, a smaller side
        # that keeps needing more premium (PE climbing toward spot, CE
        # descending toward spot) could roll to a strike AT OR PAST the
        # opposite side's own strike, inverting the strangle into a
        # nonsensical crossed position (e.g. a PE strike above its own
        # CE's strike). Only meaningful pre-convergence, when the
        # opposite side still has exactly the one leg Section 5/6 always
        # leave it at (see this class's own "IMPORTANT STRUCTURAL POINT"
        # above Section 5 — neither section ever grows a side's leg
        # count); post-convergence, active_management's own delegated
        # opens go through `IntradayDTTAdjustedStrategy._adjust` instead
        # of this method entirely, so this never fires there.
        opposite_side = OTHER_SIDE[side]
        opposite_legs = self.legs[opposite_side]
        if len(opposite_legs) == 1:
            opposite_strike = opposite_legs[0]["strike"]
            # PE strikes climb TOWARD the CE's strike as premium is
            # chased; CE strikes descend TOWARD the PE's strike — either
            # reaching (not just passing) the opposite strike is the cap.
            crosses = (leg.strike >= opposite_strike) if side == "PE" else (leg.strike <= opposite_strike)
            if crosses:
                logger.info(
                    "%s: %s roll's %.2f-premium-target strike (%.2f) would reach/cross "
                    "the %s side's own strike (%.2f) — capping to exactly %.2f instead "
                    "of letting the strangle invert; this forces convergence to a "
                    "straddle at that shared strike.",
                    runner.deployment_name, side, target_premium, leg.strike,
                    opposite_side, opposite_strike, opposite_strike,
                )
                leg = await self.resolver.get_leg(self.instrument, self.contract_expiry, opposite_strike, side)
                price = await self.resolver.get_ltp(leg)

        return leg, price

    async def _replace_leg(
        self, runner, ts, side: str, old: dict, target_premium: float, trigger: str,
        trigger_values: dict, prices: dict,
    ) -> bool:
        """Close `old` and sell its replacement — the replacement resolved
        FIRST, so a lookup that fails (Kite down) closes nothing: the old
        leg stays on, it's reported, and the trigger tries again. Closing
        first used to leave the side empty for the rest of the day."""
        try:
            picked = await self._pick_leg(runner, side, target_premium, without=old)
        except Exception as e:
            sig = f"{type(e).__name__}: {e}"[:200]
            last = self._roll_blocked_noted.get(side)
            if last is None or ts - last >= timedelta(minutes=5):
                self._roll_blocked_noted[side] = ts
                logger.warning("%s: %s roll of %s not done — couldn't resolve the replacement: %s",
                               runner.deployment_name, side, old["symbol"], sig)
                await self._decide(runner, ts, trigger, "blocked",
                                   f"Not rolled: couldn't find the replacement {side} leg ({sig}); {old['symbol']} stays on and the roll is tried again",
                                   {**trigger_values, "side": side, "error": sig}, fault=True)
                report = getattr(runner, "report", None)
                if report is not None:
                    await report("adjustment_delayed", f"The {side} roll ({trigger.replace('_', ' ')}) couldn't go through",
                                 f"{old['symbol']} was kept on rather than closed with nothing to replace it: {sig}. "
                                 "It's tried again on the next tick that still calls for it.",
                                 key=f"roll:{side}", day=ts.date(), ttl=timedelta(hours=6),
                                 data={"trigger": trigger, "side": side, "symbol": old["symbol"]})
            return False
        old_hedge = self.hedges.get(old["token"])
        point_distance = old_hedge["point_distance"] if old_hedge else None
        await self._close_leg(runner, ts, side, old, trigger, trigger_values)
        await self._open_leg(
            runner, ts, side, target_premium, trigger, trigger_values,
            reversed_hedge_order=True, hedge_point_distance=point_distance, prices=prices, picked=picked,
        )
        if self._roll_blocked_noted.pop(side, None) is not None:
            resolve, note = getattr(runner, "resolve", None), getattr(runner, "note_decision", None)
            if resolve is not None:
                await resolve(f"roll:{side}")
            if note is not None:   # the rule got through: clears its "blocked" too
                await note(ts, trigger, "acted", f"{side} rolled")
        return True

    async def _open_leg(
        self, runner, ts, side: str, target_premium: float, trigger: str,
        trigger_values: dict, exclude_strikes=None,
        reversed_hedge_order: bool = False, hedge_point_distance: Optional[float] = None,
        prices: Optional[dict] = None, picked: Optional[tuple] = None,
    ) -> dict:
        """
        `picked`: the (leg, price) `_pick_leg` already resolved — a
        replace resolves before it closes anything (`_replace_leg`).

        `reversed_hedge_order` / `hedge_point_distance`: set by callers
        that just closed the leg being replaced (Section 5's roll,
        Section 6's replace — every trigger, on both, since neither
        section grows a side's leg count any more) — per the module
        docstring's "HEDGING" roll-order requirement, a REPLACE must
        place the new protective leg BEFORE the new short (reversed
        from the plain short-then-hedge order the `else` branch below
        uses, for a hypothetical caller with nothing existing being
        closed first, so there's no exposure gap to protect against —
        `_enter`'s own initial entry doesn't call `_open_leg` at all,
        it sells directly and hedges via `_open_hedge_for_short_leg`,
        so no current caller actually takes that branch).
        `hedge_point_distance`, if given, is the REPLACED
        leg's own hedge distance, carried forward onto the new leg
        instead of resolving a fresh premium-target hedge — "roll in
        lockstep... to maintain a fixed point-distance."
        """
        if picked is None:
            picked = await self._pick_leg(runner, side, target_premium, exclude_strikes)
        leg, price = picked

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
            # Normal order — short first, hedge second. See this
            # method's own docstring: no current caller actually reaches
            # this branch (both Section 5's roll and Section 6's replace
            # always pass reversed_hedge_order=True); kept as the sane
            # default for a future direct `_open_leg` call with nothing
            # being closed alongside it.
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

    def _side_sums(self, prices: dict[int, float]) -> Optional[tuple]:
        """(bigger_side, smaller_side, bigger_sum, smaller_sum), or None if
        either side has no legs or a non-positive total."""
        if not (self.legs["CE"] and self.legs["PE"]):
            return None
        ce_sum = sum(prices[l["token"]] for l in self.legs["CE"])
        pe_sum = sum(prices[l["token"]] for l in self.legs["PE"])
        if ce_sum <= 0 or pe_sum <= 0:
            return None
        if ce_sum >= pe_sum:
            return "CE", "PE", ce_sum, pe_sum
        return "PE", "CE", pe_sum, ce_sum

    async def _continuous_adjustment_check(self, runner, ts, prices: dict[int, float]) -> None:
        def _fires(px):
            s = self._side_sums(px)
            return s is not None and s[3] <= self.adjustment_trigger_ratio * s[2]

        if not _fires(prices):
            return
        confirmed = await self._confirm_trigger(runner, ts, "continuous_50pct_trigger", prices, _fires)
        if confirmed is None:
            return
        prices = confirmed
        bigger_side, smaller_side, bigger_sum, smaller_sum = self._side_sums(prices)
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
        await self._replace_leg(runner, ts, side, cheapest, new_leg_target, trigger, trigger_values, prices)

    # ── Section 6: EOD 80% check — ALWAYS replaces (never grows) ────────

    async def _eod_check(self, runner, ts, prices: dict[int, float]) -> None:
        def _fires(px):
            s = self._side_sums(px)
            return s is not None and s[3] < self.eod_gap_floor * s[2]

        if not _fires(prices):
            s = self._side_sums(prices)
            if s is not None:
                await self._decide(
                    runner, ts, "eod_gap_check", "no_action",
                    f"{s[1]}/{s[0]} {s[3] / s[2]:.2f} ≥ floor {self.eod_gap_floor:.2f}: gap small enough to hold overnight",
                    {"bigger_side": s[0], "smaller_side": s[1], "bigger_sum": round(s[2], 2),
                     "smaller_sum": round(s[3], 2), "eod_gap_floor": self.eod_gap_floor},
                )
            return   # within floor, nothing to do
        # Runs once per day: a refute here means the real gap is within
        # the floor, so there is correctly nothing to do today.
        confirmed = await self._confirm_trigger(runner, ts, "eod_gap_check", prices, _fires)
        if confirmed is None:
            return
        prices = confirmed
        bigger_side, smaller_side, bigger_sum, smaller_sum = self._side_sums(prices)

        trigger_values = {
            "bigger_side": bigger_side, "smaller_side": smaller_side,
            "bigger_sum": bigger_sum, "smaller_sum": smaller_sum,
            "eod_gap_floor": self.eod_gap_floor, "check_time": ts.time().isoformat(),
        }
        band_mid = (self.eod_gap_floor + self.adjustment_band_max) / 2
        band_target_total = band_mid * bigger_sum
        side_legs = self.legs[smaller_side]

        # BUG FIX (confirmed against a real deployment — see module
        # docstring's Section 6): this used to treat a side's FIRST
        # breach of eod_gap_floor as a "GROW" (sell one additional leg,
        # leave the original leg open and permanently immune to this
        # check for the rest of its life) and only REPLACE on a later
        # breach. That defeated the whole point of this check — closing
        # the CE/PE premium gap so a gap-up/gap-down can't produce a
        # large, unhedged net loss between the two sides — since a side
        # that had never rolled before could sit with a wide gap
        # indefinitely. Every trigger now does the same thing,
        # unconditionally, structurally identical to Section 5's own
        # `_roll_side`: close the single CHEAPEST currently-open leg on
        # this side (tie-break: earliest-opened, i.e. lowest `seq` —
        # same rule as Section 5), then open exactly ONE replacement
        # sized so the side's new total lands at the band midpoint. No
        # leg is ever protected from this check, including the original
        # — net leg count on the side is unchanged (still whatever it
        # was before this call), so this can never grow a side past its
        # current leg count either.
        cheapest = min(side_legs, key=lambda l: (prices[l["token"]], l["seq"]))
        remaining_sum = sum(prices[l["token"]] for l in side_legs if l is not cheapest)
        new_leg_target = band_target_total - remaining_sum

        # EOD ROLL-OUT: if moving the untested side in would reach the
        # tested side's strike (the strike-cross cap would then snap it
        # into a straddle), roll the TESTED side out instead, so the
        # position goes into the overnight gap as a strangle. EOD only:
        # Section 5's intraday rolls still converge as before.
        if self.eod_rollout_instead_of_straddle and await self._eod_roll_would_converge(
            smaller_side, bigger_side, cheapest, new_leg_target,
        ):
            await self._eod_rollout(runner, ts, prices, bigger_side, smaller_sum, band_mid, trigger_values)
            return

        # Same reversed hedge-before-short ordering as Section 5's roll
        # -- this is ALSO a replace (close then open).
        await self._replace_leg(runner, ts, smaller_side, cheapest, new_leg_target, "eod_gap_check", trigger_values, prices)

    # ── Section 7: convergence detection + active-management delegation ─

    async def _eod_roll_would_converge(self, side: str, tested_side: str, cheapest: dict, target: float) -> bool:
        """Would the normal EOD replacement on `side` reach or cross the
        tested side's strike? Resolves the same strike _open_leg would
        (same premium target, window and exclusions). Any lookup failure
        answers False, keeping the usual roll."""
        opposite = self.legs[tested_side]
        if len(opposite) != 1:
            return False
        exclude = {l["strike"] for l in self.legs[side] if l is not cheapest}
        try:
            leg = await self.resolver.get_leg_by_premium(
                self.instrument, self.contract_expiry, side, max(target, 0.5),
                strike_window=self.adjustment_strike_window, exclude_strikes=exclude,
            )
        except Exception:
            logger.exception("EOD roll-out: couldn't resolve the replacement strike — rolling as usual")
            return False
        opp = opposite[0]["strike"]
        return leg.strike >= opp if side == "PE" else leg.strike <= opp

    async def _eod_rollout(
        self, runner, ts, prices: dict[int, float], tested_side: str, untested_sum: float,
        band_mid: float, trigger_values: dict,
    ) -> None:
        """Close the tested side's leg nearest the market (highest premium)
        and re-sell further out, sized so the untested side's current total
        is band_mid (87.5% by default) of the tested side's new total."""
        legs = self.legs[tested_side]
        nearest = max(legs, key=lambda l: (prices[l["token"]], -l["seq"]))
        remaining = sum(prices[l["token"]] for l in legs if l is not nearest)
        new_target = untested_sum / band_mid - remaining
        tv = {**trigger_values, "rolled_side": tested_side, "closed_premium": prices[nearest["token"]],
              "new_target_premium": round(new_target, 2),
              "why": "moving the untested side in would have created a straddle before the overnight gap"}
        logger.info(
            "%s: EOD roll-out — rolling the tested %s side out (%s @ %.2f -> target %.2f) "
            "instead of converging into a straddle", runner.deployment_name, tested_side,
            nearest["symbol"], prices[nearest["token"]], new_target,
        )
        await self._replace_leg(runner, ts, tested_side, nearest, new_target, "eod_rollout", tv, prices)

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
            cheapest, ties toward earliest-opened" bookkeeping —
            `intraday_dtt_adjusted` has no such concept). Section 6 (EOD)
            keeps running unmodified post-convergence even under
            active_management (per that section's own heading), and its
            `min(..., key=lambda l: (prices[...], l["seq"]))` would
            raise `KeyError` on such a leg — so any leg missing `"seq"`
            after a delegated call is stamped with one here, exactly as
            if it had been opened through this strategy's own `_open_leg`.
        """
        if not hasattr(self, "adjustment_size_pct"):
            self.adjustment_size_pct = 0.25   # intraday_dtt_adjusted's own default
        def _decide(px):
            """("adjust", side, bigger_now) / ("unwind",) / None -- the
            same conditions as before, as a pure function so the
            STALE-PRICE GUARD can re-check them on confirmed prices."""
            if self.adjusted_side is None:
                if self.legs["CE"] and self.legs["PE"]:
                    ce_now = px[self.legs["CE"][0]["token"]]
                    pe_now = px[self.legs["PE"][0]["token"]]
                    if ce_now >= pe_now:
                        smaller_side, bigger_now, smaller_total = "PE", ce_now, pe_now
                    else:
                        smaller_side, bigger_now, smaller_total = "CE", pe_now, ce_now
                    if smaller_total <= self.adjustment_trigger_ratio * bigger_now:
                        return ("adjust", smaller_side, bigger_now)
                return None
            anchor_side = OTHER_SIDE[self.adjusted_side]
            if self.legs[anchor_side]:
                bigger_now = px[self.legs[anchor_side][0]["token"]]
                smaller_total = sum(px[l["token"]] for l in self.legs[self.adjusted_side])
                if smaller_total <= self.adjustment_trigger_ratio * bigger_now:
                    return ("adjust", self.adjusted_side, bigger_now)
                if len(self.legs[self.adjusted_side]) > 1 and smaller_total >= bigger_now:
                    return ("unwind",)
            return None

        action = _decide(prices)
        if action is None:
            return
        confirmed = await self._confirm_trigger(
            runner, ts, f"active_management_{action[0]}", prices,
            lambda px: (_decide(px) or (None,))[0] == action[0],
        )
        if confirmed is None:
            return
        prices = confirmed
        action = _decide(prices)

        self.realized_pnl_today = self.cycle_realized_pnl
        try:
            if action[0] == "adjust":
                # Call via `self.` (bound in on_start, see there) rather
                # than the unbound `IntradayDTTAdjustedStrategy.
                # _handle_adjustment_trigger(self, ...)` form -- that
                # method's OWN body calls `self._adjust(...)` internally,
                # which only resolves correctly if `_adjust` was bound
                # onto this instance too, not just this outer call.
                await self._handle_adjustment_trigger(runner, ts, action[1], action[2], prices)
            else:
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
        except Exception as e:
            logger.exception(
                "%s: failed to open/roll a protective leg (side=%s) — short "
                "leg left UNHEDGED, will not retry automatically",
                runner.deployment_name, side,
            )
            report = getattr(runner, "report", None)
            if report is not None:
                await report("unhedged", f"A {side} short is open without its hedge",
                             f"The protective leg for the short at {short_leg_obj.strike:g} couldn't be bought "
                             f"({type(e).__name__}: {str(e)[:160]}). It isn't retried; the short is otherwise managed as usual.",
                             severity="critical", key=f"unhedged:{short_leg_obj.instrument_token}", day=ts.date(),
                             data={"side": side, "strike": short_leg_obj.strike})
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
            resolve = getattr(runner, "resolve", None)
            if resolve is not None:   # an unhedged short is closed: nothing left to hedge
                await resolve(f"unhedged:{short_token}")
            return
        price, _src = await self.exit_price(runner, hedge["token"], hedge["symbol"], hedge["entry_price"])
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

        # A fill stamped converged (see _trade_meta): only fills placed
        # after convergence carry it. _resume_convergence does the rest.
        for pos in sorted(closed_this_cycle, key=lambda p: p["closed_at"] or p["opened_at"]):
            meta = pos["metadata"] or {}
            if meta.get("converged"):
                self.converged = True
                self.convergence_premium = float(meta["convergence_premium"])
        for token, pos in open_legs:
            meta = pos["metadata"] or {}
            if meta.get("converged"):
                self.converged = True
                self.convergence_premium = float(meta["convergence_premium"])

        logger.info(
            "%s: resumed with %d CE / %d PE leg(s), contract=%s, cycle_id=%d, "
            "cycle_realized_pnl=%.2f, adjustments_used=%d, converged=%s",
            runner.deployment_name, len(self.legs["CE"]), len(self.legs["PE"]),
            self.contract_expiry, self.cycle_id, self.cycle_realized_pnl,
            self.adjustments_used, self.converged,
        )

    async def _resume_convergence(self, runner) -> None:
        """Convergence and the trailing stop's low, as they were (see the
        module docstring's RESUME-SAFETY): the saved state for this
        cycle, else one CE and one PE at one strike is a straddle."""
        saved = await runner.load_state()
        if saved:
            try:
                if not isinstance(saved, dict) or saved.get("version") != 1 or not isinstance(saved.get("cycle_id"), int):
                    raise ValueError(f"not this strategy's saved state: {str(saved)[:80]}")
                if saved["cycle_id"] == self.cycle_id and saved.get("converged") and (self.legs["CE"] or self.legs["PE"]):
                    premium, floor = float(saved["convergence_premium"]), saved.get("trailing_floor")
                    self.converged, self.convergence_premium = True, premium
                    self._trailing_floor = float(floor) if floor is not None else None
            except (KeyError, TypeError, ValueError) as e:
                logger.exception("%s: saved convergence state is unusable — rebuilding it from the legs",
                                 runner.deployment_name)
                await self.state_unusable(runner, f"The saved state was unreadable ({e})",
                                          "Whether the position had converged into a straddle is worked out from the open legs instead.")
        if not (self.legs["CE"] or self.legs["PE"]):
            return
        if not self.converged and len(self.legs["CE"]) == 1 and len(self.legs["PE"]) == 1 \
                and self.legs["CE"][0]["strike"] == self.legs["PE"][0]["strike"]:
            self.converged = True
            self.convergence_premium = self._premium_at_convergence(runner)
            logger.warning(
                "%s: resumed as a straddle (one CE and one PE at %.2f), not saved as converged: "
                "converged at combined premium %.2f", runner.deployment_name,
                self.legs["CE"][0]["strike"], self.convergence_premium,
            )
        if self.converged and self._trailing_floor is None:
            self._trailing_floor = self.convergence_premium

    def _premium_at_convergence(self, runner) -> float:
        """The combined premium when the straddle was made: the later of
        its two legs' fill price, plus the other side's price then (what
        that fill's trigger values held), or, for an entry, both entry
        prices."""
        ce, pe = self.legs["CE"][0], self.legs["PE"][0]
        later, other = (ce, pe) if ce["seq"] >= pe["seq"] else (pe, ce)
        side = "CE" if later is ce else "PE"
        meta = (runner.open_positions.get(later["token"]) or {}).get("metadata") or {}
        tv = meta.get("trigger_values") or {}
        price = float(meta.get("fill_price") or later["entry_price"])
        if tv.get("bigger_side") in ("CE", "PE") and tv.get("bigger_sum") is not None and tv.get("smaller_sum") is not None:
            return price + float(tv["smaller_sum"] if tv["bigger_side"] == side else tv["bigger_sum"])
        return price + float(other["entry_price"])

    def get_persistable_state(self) -> Optional[dict]:
        """What the book can't say for sure (RESUME-SAFETY): whether this
        cycle converged, at what premium, and the trailing stop's low.
        Checkpointed by the runner after every tick that changes it."""
        if not self.cycle_id:
            return None
        return {"version": 1, "cycle_id": self.cycle_id, "converged": self.converged,
                "convergence_premium": self.convergence_premium,
                "trailing_floor": self._trailing_floor if self.converged else None}

    # ── Trade-reason metadata (Section 12) ──────────────────────────────

    def tracked_tokens(self) -> set:
        """Every leg this strategy holds (StrategyBase.tracked_tokens)."""
        legs = getattr(self, "legs", None) or {}
        hedges = getattr(self, "hedges", None) or {}
        out = {int(l["token"]): l.get("symbol") for side in ("CE", "PE") for l in legs.get(side, [])}
        out.update({int(h["token"]): h.get("symbol") for h in hedges.values()})
        return out

    async def _decide(self, runner, ts, rule: str, outcome: str, summary: str, data: Optional[dict] = None,
                      fault: bool = False, missed: bool = False) -> None:
        """A check that didn't trade, for Geek mode's Decisions view
        (trades explain themselves through their fills' metadata). A
        block caused by a fault is watched (runner.note_decision)."""
        note = getattr(runner, "note_decision", None)
        if note is not None:
            await note(ts, rule, outcome, summary, fault=fault, missed=missed)
        record = getattr(runner, "record_decision", None)
        if record is not None:
            await record(ts, rule, outcome, summary, data or {})

    async def _decide_once_a_day(self, runner, ts, rule: str, outcome: str, summary: str, data: Optional[dict] = None) -> None:
        key = (rule, outcome, ts.date())
        if key not in self._decision_days:
            self._decision_days.add(key)
            await self._decide(runner, ts, rule, outcome, summary, data)

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

    def get_status_fields(self) -> Optional[list]:
        """See StrategyBase.get_status_fields's own docstring. Built for
        one confirmed real question ("why isn't the next trade
        initiated?"): a flat deployment sitting at 0 legs before
        entry_time is indistinguishable, from the Overview tab alone,
        from one that's actually stuck -- _maybe_enter's own "not yet
        time" path (Section 2) logs nothing at all. This surfaces that
        distinction directly.

        No `status_fields_from_state` counterpart: the state this
        strategy saves (get_persistable_state) is only its convergence,
        nothing a "waiting for entry time" message could be built from
        for a paused/stopped deployment; there's also no live clock
        ticking against it in that state for the message to mean anything.
        """
        if self.legs["CE"] or self.legs["PE"]:
            return [
                {"label": "Cycle", "value": self.cycle_id},
                {"label": "Contract expiry", "value": self.contract_expiry.isoformat() if self.contract_expiry else "—"},
                {"label": "Legs open", "value": f"{len(self.legs['CE'])} CE / {len(self.legs['PE'])} PE"},
            ]
        now = clock.now_ist()
        entry_label = self.entry_time.strftime("%H:%M")
        if now.time() < self.entry_time:
            status = f"Waiting for entry time ({entry_label} IST)"
        elif self.entered_ever:
            status = f"Past entry time — re-entering after {self._last_flatten_trigger or 'unknown'} on next tick"
        else:
            status = "Past entry time — entering on next tick"
        fields = [{"label": "Status", "value": status}]
        if self.entered_ever:
            fields.append({"label": "Cycles completed", "value": self.cycle_id})
        return fields

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
