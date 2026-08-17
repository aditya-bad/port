"""
live_deploy — intraday_dtt_adjusted: intraday_dtt_simple's ATM straddle,
but with a dynamic rebalancing layer instead of a fixed per-leg stop.

Live paper-trading only — no backtested version exists for this one.

RELATIONSHIP TO intraday_dtt_simple: identical entry (10:00 ATM CE+PE,
same expiry-day exclusion, same "once per day" rule — literally reused
via `resolve_atm_straddle_legs()` from that module, not reimplemented)
and identical 3:00 PM hard exit. Everything BETWEEN entry and 3pm is
different: instead of a fixed 40%/10% per-leg stop/target, this adds
MORE legs to whichever side is losing (an "adjustment"), unwinds them
one at a time if the market reverses, and falls back to a break-even
underlying-price stop as the true worst case. The 10% combined-premium
profit target from the simple version is NOT replaced — it keeps running
the whole time, see "PROFIT TARGET" below.

SUBCLASSED BY intraday_dtt_advanced: that strategy shares this one's
entry, profit-target check, break-even check, and reversal-unwind
mechanics UNCHANGED, differing only in (a) what happens when the
adjustment trigger fires while already at the leg cap (a rejection here
vs. a "close cheapest, then reopen" roll there) and (b) an optional
break-even band multiplier. To make that a clean subclass rather than a
fork of this whole file, two small seams exist here specifically for
that reuse:
  - `self.breakeven_multiplier` (defaulted to 1.0, i.e. a no-op, and NOT
    part of THIS strategy's own documented config) is what both
    break-even computation sites (`_enter`, `_resume_from_db`) actually
    multiply by — intraday_dtt_advanced sets it from its own
    `breakeven_multiplier` config key and gets a working configurable
    band for free, with no override of either method needed.
  - `_handle_adjustment_trigger(runner, ts, side, bigger_now, prices)`
    is what actually runs once the trigger condition is confirmed true
    (`_maybe_manage` no longer inlines the cap check itself) — THIS
    class's implementation is the lifetime-cap "reject once spent"
    behavior described in Section 4 below; intraday_dtt_advanced
    overrides just this one method for its rolling behavior. `_adjust`
    and `_unwind_one` both took an optional `reason` parameter for the
    same purpose — intraday_dtt_advanced's roll calls them directly with
    "roll_close"/"roll_open" instead of duplicating either method.

RULES:

1. ENTRY — see intraday_dtt_simple's docstring; behavior is identical
   here (`entry_time`, `switch_to_next_week_on_expiry`, `catch_up_late_entry`,
   ATM strike from spot, sell CE+PE, once per day, no same-day
   re-entry). Additionally records `entry_spot` (the live spot price at
   entry) and `combined_entry_premium` (CE + PE entry price, the
   ORIGINAL 2-leg premium only — never includes adjustment legs' premium,
   see "PROFIT TARGET" and "BREAK-EVEN" below) — both fixed for the day.

2. ADJUSTMENT TRIGGER (checked continuously once both original legs are
   open) — compare the CURRENT premium of the bigger side against the
   CURRENT combined premium of the smaller side (initially a single leg;
   after the first adjustment, the smaller side may be several legs
   summed). Symmetric — either side can be "bigger":

       smaller_side_total <= adjustment_trigger_ratio * bigger_side_current
       (default ratio 0.5 — "smaller side has halved relative to bigger")

   SIDE IDENTITY IS STICKY once the first adjustment fires: whichever
   side triggers as the "smaller" side becomes `adjusted_side` for the
   REST OF THE DAY. From that point on, `adjusted_side` is always the
   side being compared as a running SUM of its legs (1 up to
   `max_adjustments + 1` of them); the other side is the "anchor" side
   and never receives adjustment legs — it stays exactly 1 leg for the
   whole day. This isn't spelled out as a literal formula in the spec,
   but it's the only reading consistent with every worked example (the
   Call side never gains a second leg in the 2-adjustment walkthrough;
   only Put does) — documented here explicitly since it's a real design
   decision, not a triviality.

   Before the first adjustment, "bigger"/"smaller" are evaluated fresh
   each tick between the two original single legs (genuinely symmetric —
   whichever side is currently bigger becomes the anchor once triggered).

3. ADJUSTMENT SIZING AND EXECUTION — new leg's target premium =
   `adjustment_size_pct * bigger_side_current` (default 25%). Sell one
   MORE leg of the adjusted side's option type, at whichever currently-
   listed strike has a live premium closest to that target — via
   `OptionsResolver.get_leg_by_premium(..., exclude_strikes=...)`,
   excluding every strike already held on that side (a different strike
   from any existing leg on that side, per the spec — never a same-
   option-type collision; the anchor side's strike is irrelevant since
   it's a different option_type entirely).

4. HARD CAP: `max_adjustments` (default 2) — a LIFETIME counter for the
   day, not a "how many are concurrently open" count. Once it's used up,
   no more legs are ever added, even if every adjustment leg added so
   far later gets fully unwound by reversal (Section 6) and the
   adjustment trigger condition is met again from a clean 2-leg state —
   the day's adjustment budget is spent, permanently, once used.

5. REVERSAL / UNWIND — checked continuously once `adjusted_side` has
   more than 1 leg open (i.e. at least one adjustment leg is currently
   open). Trigger:

       smaller_side_total >= bigger_side_current
       (smaller = sum of all currently-open legs on adjusted_side;
        bigger = the anchor side's single current premium)

   On trigger, close exactly ONE leg: whichever individual leg on
   adjusted_side currently has the LOWEST premium among all legs on that
   side (the original leg is eligible too, not just adjustment legs — it
   competes on equal footing). Re-evaluated fresh every time this fires,
   not "the first one added". TIE-BREAK on an exact lowest-premium tie:
   the EARLIEST-OPENED leg on that side wins (closes first) — i.e. ties
   break toward whichever leg has been open longest, a simple,
   deterministic rule consistent with how `get_leg_by_premium`'s own
   strike-matching tie-break is documented (Section 7 below). Continues
   to re-check every subsequent tick; a deep-enough reversal can fire
   this multiple times, closing legs one at a time.

6. PROFIT TARGET — the SAME 10% combined-premium idea from
   intraday_dtt_simple (config: `combined_premium_profit_pct`, default
   0.10), but redefined as a running TOTAL PROFIT check that stays
   active continuously, even while adjustment legs are open:

       total_profit = realized_pnl_today (every leg closed earlier today
                        via reversal-unwind, each one's own entry premium
                        minus its own exit premium, summed)
                      + unrealized P&L of every leg still open right now
                        (each one's own entry premium minus its current
                        premium, summed)

   Target = `combined_premium_profit_pct * combined_entry_premium` — the ORIGINAL 2-leg
   entry premium ONLY (1200 in the spec's worked example -> target 120),
   NOT the total premium collected across every leg including
   adjustments. THIS IS A DELIBERATE, CONFIRMED CHOICE, not a silent
   default: the alternative reading (10% of everything ever collected,
   growing as adjustments add premium) was explicitly raised and
   rejected in favor of this one before writing any code — the day's
   profit goal stays fixed regardless of how much rebalancing happens.
   When hit: close EVERY currently open leg immediately (full flatten),
   regardless of how many adjustment legs are open.

7. BREAK-EVEN FALLBACK — computed once at entry, fixed for the day:
   `lower = entry_spot - combined_entry_premium`,
   `upper = entry_spot + combined_entry_premium`. Checked against the
   LIVE UNDERLYING PRICE (not any option's premium) — active
   continuously from entry onward, not gated on any adjustment having
   happened. If the underlying trades at or beyond either level, close
   every remaining open leg immediately, regardless of what the
   adjustment/reversal/profit-target logic would otherwise say.

8. STILL APPLIES ON TOP OF ALL OF THE ABOVE: 3:00 PM hard exit (closes
   everything regardless of state); never skips a day, including the
   resolved contract's own expiry day (`switch_to_next_week_on_expiry`,
   reused from intraday_dtt_simple, not reimplemented); exactly one
   entry attempt per day, no same-day re-entry after any exit.

PRIORITY ORDER, every tick, once both original legs are open (CONFIRMED
before writing this code — the spec states force_exit above everything
and profit_target above adjustment/reversal, but does not explicitly
place break-even; this ordering was confirmed rather than assumed):

    1. force_exit_time (3pm)              -- closes everything, always wins
    2. break-even fallback                -- closes everything
    3. profit target (total profit)       -- closes everything
    4. adjustment trigger (if under cap)  -- adds one leg
    5. reversal / unwind                  -- closes one leg

Steps 1-3 each stop the tick's evaluation immediately on firing (nothing
below them is checked that tick). Steps 4 and 5 are mutually exclusive by
construction (their trigger conditions, <=0.5x and >=1.0x of the same
comparison, can't both be true at once for a positive premium), so their
relative order doesn't matter in practice; 4 is checked first only
because it's discussed first in the spec.

WHY BREAK-EVEN OUTRANKS THE PROFIT TARGET: break-even is the risk-
containment backstop ("the true worst-case exit" per the spec) — a
catastrophic underlying move blowing through a break-even level is a
more urgent stop than a profit-target check should be allowed to "race"
and win. In practice the two conditions are rarely both true on the same
tick (breaching break-even means the position is normally deep
underwater, not up 10%), so this ordering choice will rarely be
outcome-determining — but it's still a real, confirmed decision, not
left to fall out of whatever order the code happened to be written in.

POSITION-STATE TRACKING: `self.legs = {"CE": [...], "PE": [...]}`, each
entry a dict of `{token, symbol, exchange, entry_price, strike, role}`
where `role` is `"original"` or `f"adjustment_{n}"` (n = 1..max_adjustments)
— stored in each leg's own opening-fill metadata too, so it survives a
restart. `self.realized_pnl_today` is a running total of realized P&L
from every leg closed earlier the same day via reversal-unwind (or a
force/profit/breakeven flatten) — NOT reset when a leg closes, only at
a genuine day rollover — because Section 6's total-profit check needs it
every tick, not just at day's end.

RESUME-SAFETY: on restart, `on_start()` reattaches every currently OPEN
leg from `runner.open_positions` (role + side read back from that leg's
own stored metadata). If there's at least one open leg, that's proof
today's entry already happened — in that case, "today" for reconciliation
purposes is taken directly from any open leg's own `opened_at` date (no
need to wait for a live tick to learn the date), and
`runner.list_closed_positions()` is consulted to reconstruct:
  - `self.realized_pnl_today` — sum of `realized_pnl` for every CLOSED
    position belonging to this deployment whose `closed_at` falls on
    that same day. Resetting this to 0 on resume (instead of
    reconstructing it) would make the 10% total-profit target
    artificially harder to reach for the rest of the day — a real
    correctness bug, not a cosmetic one, so it is NOT skipped.
  - `self.adjustments_used` — the highest adjustment index (`n` in
    `f"adjustment_{n}"`) seen across BOTH open and closed-today legs;
    open-only would under-count if an adjustment leg was already
    unwound and closed before the restart.
  - `self.adjusted_side` — whichever side (open or closed-today) has any
    non-"original" leg.
If NO legs are open at resume, this strategy behaves like
intraday_dtt_simple's own resume path in the same situation: it does NOT
try to infer "did today's full entry-to-exit cycle already happen" from
closed-position history (that would need to know "today" without a live
tick, which this whole family deliberately avoids depending on) — a
restart between a same-day exit and the next day's entry_time carries the
same known, pre-existing limitation intraday_dtt_simple already has, not
a new gap introduced here.

CONFIG (all shared entry/exit-scaffolding keys carry the exact same
meaning as intraday_dtt_simple — see that module's docstring):
  "instrument_tokens", "symbol", "options_underlying", "expiry_selector",
  "entry_time", "force_exit_time" (required, non-null), "lots_per_trade",
  "catch_up_late_entry", "switch_to_next_week_on_expiry" — identical.
  "combined_premium_profit_pct": 0.10 (default) — the total-profit
      target, as a fraction of the ORIGINAL 2-leg combined entry
      premium (see "PROFIT TARGET"). Named `decay_pct` before this was
      renamed for clarity — still read as a fallback, see on_start.
  "adjustment_trigger_ratio": 0.5 (default) — Section 2's trigger ratio.
  "adjustment_size_pct": 0.25 (default) — Section 3's sizing fraction.
  "max_adjustments": 2 (default) — Section 4's lifetime cap.
  "adjustment_strike_window": 40 (default) — forwarded to
      `OptionsResolver.get_leg_by_premium`'s `strike_window` — wider than
      that method's own default (15), since a 25%-of-ATM target premium
      is typically well further OTM than a "nearby" strike search
      usually needs.
No spike_pct here — the per-leg stop-loss from intraday_dtt_simple is
fully REPLACED by the adjustment/reversal layer, not kept alongside it.

No margin model, same simplification as intraday_dtt_simple and every
other options strategy in this family — every SELL (original or
adjustment) credits premium, every BUY (unwind, flatten) is cash-checked
for real like any other buy.
"""

import logging
from datetime import date
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver
from .intraday_dtt_simple import resolve_atm_straddle_legs
from .pivot_supertrend import _parse_hhmm
from .registry import register_strategy
from .trade_meta import build_trade_meta

logger = logging.getLogger("live_deploy.strategies.intraday_dtt_adjusted")

OTHER_SIDE = {"CE": "PE", "PE": "CE"}


def _legs_snapshot(legs: dict) -> dict:
    """Compact per-side leg snapshot for a fill's `resulting_state` — same
    reasoning as strangle_monthly_v2's own `_snapshot_state`. Deliberately
    a plain function of `legs` (not an instance method) rather than
    `_legs_snapshot(self.legs)`: `_adjust`/`_unwind_one`/`_flatten_all` are
    also reused verbatim by strangle_monthly_v2's "active_management"
    convergence mode via unbound-method binding onto a
    StrangleMonthlyV2Strategy instance (see that module's "ACTIVE-
    MANAGEMENT DELEGATION" section) — an instance method here would
    AttributeError the moment `self` is actually that other class, since
    it doesn't define or inherit this name. A plain function taking
    `legs` needs nothing from `self` but the one dict every caller
    (including the delegated one) already keeps in the exact same
    `{"CE": [...], "PE": [...]}` shape."""
    return {
        side: [{"strike": l["strike"], "role": l["role"]} for l in legs.get(side, [])]
        for side in ("CE", "PE")
    }


@register_strategy(
    "intraday_dtt_adjusted",
    description="intraday_dtt_simple's ATM straddle, but with dynamic "
               "rebalancing instead of a fixed per-leg stop: adds a leg to "
               "whichever side halves relative to the other (up to "
               "max_adjustments), unwinds legs one at a time on reversal, "
               "keeps the 10% total-profit target running throughout, and "
               "falls back to a break-even underlying-price stop as the "
               "true worst case. Live paper-trading only.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "expiry_selector": "THIS_WEEK",
        "entry_time": "10:00",
        "force_exit_time": "15:00",
        "combined_premium_profit_pct": 0.10,
        "adjustment_trigger_ratio": 0.5,
        "adjustment_size_pct": 0.25,
        "max_adjustments": 2,
        "adjustment_strike_window": 40,
        "lots_per_trade": 1,
        "catch_up_late_entry": True,
        "switch_to_next_week_on_expiry": False,
    },
)
class IntradayDTTAdjustedStrategy(StrategyBase):

    async def on_start(self, runner) -> None:
        cfg = runner.config
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "intraday_dtt_adjusted requires config.instrument_tokens to "
                f"be a ONE-ELEMENT list — the underlying's token, used only "
                f"for timing/spot — got {tokens!r}"
            )
        self.instrument_token = tokens[0]
        self.symbol = cfg.get("symbol", str(self.instrument_token))

        self.options_underlying = cfg.get("options_underlying")
        if not self.options_underlying:
            raise ValueError(
                "intraday_dtt_adjusted requires config.options_underlying "
                "(the options chain's own `name`, e.g. \"NIFTY\" — NOT the "
                "spot tradingsymbol \"NIFTY 50\")"
            )
        self.expiry_selector = cfg.get("expiry_selector", "THIS_WEEK")

        self.entry_time = _parse_hhmm(cfg.get("entry_time", "10:00"))
        if self.entry_time is None:
            raise ValueError("intraday_dtt_adjusted requires a non-null entry_time")

        raw_force_exit = cfg.get("force_exit_time", "15:00")
        if raw_force_exit is None:
            raise ValueError(
                "intraday_dtt_adjusted requires a non-null force_exit_time — "
                "the hard exit is a defining rule of this strategy family, "
                "not an optional extra"
            )
        self.force_exit_time = _parse_hhmm(raw_force_exit)

        # decay_pct is the pre-rename name -- read as a fallback so a
        # deployment created before this rename keeps working unchanged.
        self.combined_premium_profit_pct = float(
            cfg.get("combined_premium_profit_pct", cfg.get("decay_pct", 0.10))
        )
        self.adjustment_trigger_ratio = float(cfg.get("adjustment_trigger_ratio", 0.5))
        if not 0 < self.adjustment_trigger_ratio < 1:
            # The adjustment trigger (smaller <= ratio * bigger) and the
            # reversal-unwind trigger (smaller >= bigger) are only
            # guaranteed mutually exclusive -- never both true on the
            # same tick -- when ratio < 1.0. _maybe_manage's control flow
            # relies on that: it returns right after handling the
            # adjustment trigger, without falling through to also check
            # reversal-unwind that same tick. At ratio >= 1.0 the two can
            # overlap, silently skipping a reversal-unwind that should
            # have fired. ratio <= 0 is separately nonsensical (never
            # fires, or compares against a premium that can't be <= 0).
            raise ValueError(
                f"adjustment_trigger_ratio must be strictly between 0 and 1, "
                f"got {self.adjustment_trigger_ratio}"
            )
        self.adjustment_size_pct = float(cfg.get("adjustment_size_pct", 0.25))
        self.max_adjustments = int(cfg.get("max_adjustments", 2))
        if self.max_adjustments < 0:
            raise ValueError(f"max_adjustments must be >= 0, got {self.max_adjustments}")
        self.adjustment_strike_window = int(cfg.get("adjustment_strike_window", 40))
        # Not part of THIS strategy's own documented config surface (its
        # break-even is always exactly entry_spot +/- combined_entry_premium)
        # -- exists here, defaulted to a no-op 1.0, purely so
        # intraday_dtt_advanced can subclass this class and get a
        # configurable break-even band without duplicating _enter() or
        # _resume_from_db(). See that module's docstring.
        self.breakeven_multiplier = float(cfg.get("breakeven_multiplier", 1.0))

        self.lots_per_trade = int(cfg.get("lots_per_trade") or 1)
        if self.lots_per_trade < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {self.lots_per_trade}")
        self.catch_up_late_entry = bool(cfg.get("catch_up_late_entry", True))
        self.switch_to_next_week_on_expiry = bool(cfg.get("switch_to_next_week_on_expiry", False))

        self.resolver = OptionsResolver(runner.dispatcher)

        self.today: Optional[date] = None
        self.entered_today = False
        self._late_start_today = False

        # Variable-length per-side leg lists -- see module docstring's
        # "POSITION-STATE TRACKING". Each entry:
        #   {"token", "symbol", "exchange", "entry_price", "strike", "role"}
        self.legs: dict[str, list[dict]] = {"CE": [], "PE": []}
        self.adjusted_side: Optional[str] = None
        self.adjustments_used = 0
        self.realized_pnl_today = 0.0

        self.entry_spot: Optional[float] = None
        self.combined_entry_premium: Optional[float] = None
        self.breakeven_lower: Optional[float] = None
        self.breakeven_upper: Optional[float] = None

        # Restore today/entered_today from a previous graceful stop, if
        # any — covers the FLAT case specifically (no open legs), which
        # _resume_from_db below deliberately leaves untouched (its own
        # "nothing open -- see the docstring's known limitation note"
        # early return). Without this, every restart makes the next
        # tick look like this deployment's very first-ever observation,
        # so a tick landing after entry_time gets wrongly treated as a
        # fresh "late start" even for a deployment that's been running
        # for weeks. _resume_from_db (next) still takes full precedence
        # whenever legs ARE open — it unconditionally sets both fields
        # itself from the DB in that case, more accurately than this
        # ever could.
        persisted = await runner.load_state()
        if persisted and persisted.get("version") == 1:
            try:
                today_str = persisted.get("today")
                self.today = date.fromisoformat(today_str) if today_str else None
                self.entered_today = bool(persisted.get("entered_today", False))
            except (KeyError, TypeError, ValueError):
                logger.exception(
                    "%s: persisted today/entered_today state was malformed — "
                    "ignoring it", runner.deployment_name,
                )
                self.today = None
                self.entered_today = False

        await self._resume_from_db(runner)

    async def _resume_from_db(self, runner) -> None:
        """See module docstring's "RESUME-SAFETY" section for the full
        reasoning; this only reattaches state, it never re-derives prices
        or re-fires any check."""
        open_legs = [
            (token, pos) for token, pos in runner.open_positions.items()
            if pos["symbol"].endswith("CE") or pos["symbol"].endswith("PE")
        ]
        if not open_legs:
            return   # nothing open -- see the docstring's known limitation note

        for token, pos in open_legs:
            side = "CE" if pos["symbol"].endswith("CE") else "PE"
            meta = pos["metadata"] or {}
            role = meta.get("leg_role", "original")
            leg = {
                "token": token, "symbol": pos["symbol"],
                "exchange": meta.get("exchange", "NFO"),
                "entry_price": float(pos["avg_entry_price"]),
                "strike": float(meta.get("strike", 0.0)),
                "role": role,
            }
            self.legs[side].append(leg)
            runner.dispatcher.add_instruments(
                [{"instrument_token": token, "symbol": pos["symbol"]}]
            )
            if role != "original":
                self.adjusted_side = side

        self.entered_today = True
        # An open leg's own opened_at date IS "today" for reconciliation
        # -- known synchronously, no need to wait for a live tick.
        any_leg = next(iter(open_legs))[1]
        today = any_leg["opened_at"].date()
        self.today = today

        # Reconstruct entry_spot / combined_entry_premium / break-even
        # from the ORIGINAL two legs specifically (adjustment legs must
        # never contribute to these, per "PROFIT TARGET"/"BREAK-EVEN").
        # Only possible if BOTH originals are still traceable (open now,
        # or closed-today) -- if one original was itself already unwound
        # (fully legitimate: it competes on equal footing in Section 5),
        # break-even/the profit-target's fixed basis can't be
        # reconstructed from a metadata field that isn't stored anywhere
        # (this strategy doesn't persist combined_entry_premium itself),
        # so those checks are skipped for the rest of the day and a
        # warning is logged -- force_exit_time still applies regardless.
        closed = await runner.list_closed_positions()
        closed_today = [
            dict(p) for p in closed
            if p["closed_at"] is not None and p["closed_at"].date() == today
            and (p["symbol"].endswith("CE") or p["symbol"].endswith("PE"))
        ]

        for pos in closed_today:
            self.realized_pnl_today += float(pos["realized_pnl"] or 0.0)
            meta = pos["metadata"] or {}
            role = meta.get("leg_role", "original")
            if role != "original":
                side = "CE" if pos["symbol"].endswith("CE") else "PE"
                self.adjusted_side = self.adjusted_side or side

        for role_source in (
            [l["role"] for side_legs in self.legs.values() for l in side_legs]
            + [p["metadata"].get("leg_role", "original") for p in closed_today]
        ):
            if role_source.startswith("adjustment_"):
                n = int(role_source.split("_")[1])
                self.adjustments_used = max(self.adjustments_used, n)

        original_ce = self._find_original(self.legs["CE"], closed_today, "CE")
        original_pe = self._find_original(self.legs["PE"], closed_today, "PE")
        if original_ce is not None and original_pe is not None:
            self.combined_entry_premium = original_ce + original_pe
            # entry_spot isn't on the leg dict -- pull it from whichever
            # original fill's metadata still exists (open leg preferred,
            # else the closed-today record).
            entry_spot = self._find_entry_spot(runner, closed_today)
            if entry_spot is not None:
                self.entry_spot = entry_spot
                band = self.breakeven_multiplier * self.combined_entry_premium
                self.breakeven_lower = entry_spot - band
                self.breakeven_upper = entry_spot + band

        if self.entry_spot is None:
            logger.warning(
                "%s: resumed but could not reconstruct entry_spot/"
                "combined_entry_premium (an original leg's fill metadata "
                "is missing) — the 10%% profit target and break-even "
                "fallback are DISABLED for the rest of today; "
                "adjustment/reversal and force_exit_time still apply.",
                runner.deployment_name,
            )

        logger.info(
            "%s: resumed with %d CE leg(s) / %d PE leg(s) open, "
            "adjusted_side=%s, adjustments_used=%d, "
            "realized_pnl_today=%.2f, entry_spot=%s, "
            "combined_entry_premium=%s", runner.deployment_name,
            len(self.legs["CE"]), len(self.legs["PE"]), self.adjusted_side,
            self.adjustments_used, self.realized_pnl_today, self.entry_spot,
            self.combined_entry_premium,
        )

    @staticmethod
    def _find_original(open_side_legs, closed_today, side) -> Optional[float]:
        for l in open_side_legs:
            if l["role"] == "original":
                return l["entry_price"]
        for pos in closed_today:
            meta = pos["metadata"] or {}
            if meta.get("leg_role", "original") == "original" and pos["symbol"].endswith(side):
                return float(pos["avg_entry_price"])
        return None

    @staticmethod
    def _find_entry_spot(runner, closed_today) -> Optional[float]:
        for pos in runner.open_positions.values():
            meta = pos["metadata"] or {}
            if meta.get("leg_role", "original") == "original" and "entry_spot" in meta:
                return float(meta["entry_spot"])
        for pos in closed_today:
            meta = pos["metadata"] or {}
            if meta.get("leg_role", "original") == "original" and "entry_spot" in meta:
                return float(meta["entry_spot"])
        return None

    # ── Tick consumption ────────────────────────────────────────────────

    async def on_tick(self, runner, tick: dict) -> None:
        ts = tick.get("exchange_timestamp")
        price = tick.get("last_price")
        if ts is None or price is None:
            return

        day = ts.date()
        if self.today is None:
            self.today = day
            self._late_start_today = ts.time() >= self.entry_time and not self.entered_today
        elif day != self.today:
            self.today = day
            self.entered_today = False
            self._late_start_today = False
            self.legs = {"CE": [], "PE": []}
            self.adjusted_side = None
            self.adjustments_used = 0
            self.realized_pnl_today = 0.0
            self.entry_spot = None
            self.combined_entry_premium = None
            self.breakeven_lower = self.breakeven_upper = None

        await self._maybe_enter(runner, ts)
        await self._maybe_manage(runner, ts, price)

    # ── Entry (thin wrapper -- shared resolution lives in intraday_dtt_simple) ──

    async def _maybe_enter(self, runner, ts) -> None:
        if self.entered_today or self.legs["CE"] or self.legs["PE"]:
            return
        t = ts.time()
        if t < self.entry_time:
            return
        if t >= self.force_exit_time:
            self.entered_today = True
            logger.info(
                "%s: entry_time already passed AND force_exit_time too — "
                "skipping today's entry entirely", runner.deployment_name,
            )
            return
        if self._late_start_today and not self.catch_up_late_entry:
            self.entered_today = True
            logger.info(
                "%s: past entry_time on first observation and "
                "catch_up_late_entry=False — skipping today's entry, will "
                "try again at tomorrow's %s", runner.deployment_name, self.entry_time,
            )
            return

        await self._enter(runner, ts)
        self.entered_today = True

    async def _enter(self, runner, ts) -> None:
        try:
            resolved = await resolve_atm_straddle_legs(
                self.resolver, self.options_underlying, self.expiry_selector,
                ts, self.switch_to_next_week_on_expiry, runner.deployment_name,
            )
            ce_leg, pe_leg, expiry, strike, switched_to_next_week = resolved
            ce_price = await self.resolver.get_ltp(ce_leg)
            pe_price = await self.resolver.get_ltp(pe_leg)
            entry_spot = await self.resolver.get_spot_price(self.options_underlying)
        except NoKiteSession:
            logger.warning(
                "%s: entry_time reached but no Kite session yet — skipping "
                "today's entry", runner.deployment_name,
            )
            return
        except Exception:
            logger.exception(
                "%s: failed to resolve/price the ATM straddle for entry — "
                "skipping today's entry", runner.deployment_name,
            )
            return

        qty = self.lots_per_trade * ce_leg.lot_size

        runner.dispatcher.add_instruments([
            {"instrument_token": ce_leg.instrument_token, "symbol": ce_leg.tradingsymbol},
            {"instrument_token": pe_leg.instrument_token, "symbol": pe_leg.tradingsymbol},
        ])

        # Continuous tick-check strategy (same as intraday_dtt_simple) —
        # trigger_values read straight out of local scope.
        trigger_values = {
            "tick_time": ts.time().isoformat(), "entry_time": self.entry_time.isoformat(),
            "late_start_today": self._late_start_today,
            "switched_to_next_week": switched_to_next_week,
        }
        common_meta = {
            "strike": strike, "expiry": expiry.isoformat(), "leg_role": "original",
            "entry_spot": entry_spot,
        }

        # Each leg is appended to self.legs BEFORE its own fill is
        # recorded, so _legs_snapshot(self.legs) (used for resulting_state)
        # always reflects the book exactly as of immediately after that fill —
        # the CE fill's resulting_state shows CE-only, the PE fill's
        # shows both legs.
        self.legs["CE"].append({
            "token": ce_leg.instrument_token, "symbol": ce_leg.tradingsymbol,
            "exchange": ce_leg.exchange, "entry_price": ce_price,
            "strike": strike, "role": "original",
        })
        await runner.sell(
            ce_leg.tradingsymbol, ce_leg.instrument_token, qty, ce_price, ts,
            reason="entry",
            metadata=build_trade_meta(
                trigger="entry_time_reached", action="sell_open_CE",
                trigger_values=trigger_values, resulting_state=_legs_snapshot(self.legs),
                target_basis={"selection_basis": "ATM", "selected_strike": strike, "fill_premium": ce_price},
                **common_meta, leg="CE", exchange=ce_leg.exchange,
            ),
        )

        self.legs["PE"].append({
            "token": pe_leg.instrument_token, "symbol": pe_leg.tradingsymbol,
            "exchange": pe_leg.exchange, "entry_price": pe_price,
            "strike": strike, "role": "original",
        })
        await runner.sell(
            pe_leg.tradingsymbol, pe_leg.instrument_token, qty, pe_price, ts,
            reason="entry",
            metadata=build_trade_meta(
                trigger="entry_time_reached", action="sell_open_PE",
                trigger_values=trigger_values, resulting_state=_legs_snapshot(self.legs),
                target_basis={"selection_basis": "ATM", "selected_strike": strike, "fill_premium": pe_price},
                **common_meta, leg="PE", exchange=pe_leg.exchange,
            ),
        )

        self.entry_spot = entry_spot
        self.combined_entry_premium = ce_price + pe_price
        band = self.breakeven_multiplier * self.combined_entry_premium
        self.breakeven_lower = entry_spot - band
        self.breakeven_upper = entry_spot + band

        logger.info(
            "%s: sold straddle — CE %s@%.2f, PE %s@%.2f (combined=%.2f), "
            "entry_spot=%.2f, break-even=[%.2f, %.2f]",
            runner.deployment_name, ce_leg.tradingsymbol, ce_price,
            pe_leg.tradingsymbol, pe_price, self.combined_entry_premium,
            entry_spot, self.breakeven_lower, self.breakeven_upper,
        )

    # ── Ongoing management: force-exit > breakeven > profit-target > ──
    # ── adjustment > reversal — see module docstring's "PRIORITY ORDER" ──

    async def _maybe_manage(self, runner, ts, spot_price: float) -> None:
        if not (self.legs["CE"] or self.legs["PE"]):
            return
        t = ts.time()

        if t >= self.force_exit_time:
            await self._flatten_all(runner, ts, "force_exit", trigger_values={
                "tick_time": t.isoformat(), "force_exit_time": self.force_exit_time.isoformat(),
            })
            return

        if self.breakeven_lower is not None:
            if spot_price <= self.breakeven_lower or spot_price >= self.breakeven_upper:
                await self._flatten_all(runner, ts, "breakeven_fallback", trigger_values={
                    "spot_price": round(spot_price, 2),
                    "breakeven_lower": round(self.breakeven_lower, 2),
                    "breakeven_upper": round(self.breakeven_upper, 2),
                })
                return

        # Every remaining check needs EVERY open leg's live premium.
        prices: dict[int, float] = {}
        for side_legs in self.legs.values():
            for leg in side_legs:
                p = runner.dispatcher.last_prices.get(leg["token"])
                if p is None:
                    return   # no live tick yet for some leg -- check again next tick
                prices[leg["token"]] = p

        if self.combined_entry_premium is not None:
            unrealized = sum(
                leg["entry_price"] - prices[leg["token"]]
                for side_legs in self.legs.values() for leg in side_legs
            )
            total_profit = self.realized_pnl_today + unrealized
            target = self.combined_premium_profit_pct * self.combined_entry_premium
            if total_profit >= target:
                await self._flatten_all(runner, ts, "profit_target_total", trigger_values={
                    "realized_pnl_today": round(self.realized_pnl_today, 2),
                    "unrealized": round(unrealized, 2), "total_profit": round(total_profit, 2),
                    "target": round(target, 2),
                    "combined_premium_profit_pct": self.combined_premium_profit_pct,
                    "combined_entry_premium": round(self.combined_entry_premium, 2),
                })
                return

        if self.adjusted_side is None:
            # No adjustment has fired yet -- bigger/smaller are whichever
            # of the two ORIGINAL single legs is currently bigger/smaller,
            # evaluated fresh (genuinely symmetric pre-first-adjustment).
            if self.legs["CE"] and self.legs["PE"]:
                ce_now = prices[self.legs["CE"][0]["token"]]
                pe_now = prices[self.legs["PE"][0]["token"]]
                if ce_now >= pe_now:
                    bigger_side, smaller_side, bigger_now, smaller_total = "CE", "PE", ce_now, pe_now
                else:
                    bigger_side, smaller_side, bigger_now, smaller_total = "PE", "CE", pe_now, ce_now
                if smaller_total <= self.adjustment_trigger_ratio * bigger_now:
                    await self._handle_adjustment_trigger(runner, ts, smaller_side, bigger_now, prices)
                    return
        else:
            anchor_side = OTHER_SIDE[self.adjusted_side]
            if self.legs[anchor_side]:
                bigger_now = prices[self.legs[anchor_side][0]["token"]]
                smaller_total = sum(prices[l["token"]] for l in self.legs[self.adjusted_side])

                if smaller_total <= self.adjustment_trigger_ratio * bigger_now:
                    await self._handle_adjustment_trigger(runner, ts, self.adjusted_side, bigger_now, prices)
                    return

                if len(self.legs[self.adjusted_side]) > 1 and smaller_total >= bigger_now:
                    await self._unwind_one(runner, ts, prices)
                    return

    # ── What happens when the adjustment trigger fires — this is the
    # extension point intraday_dtt_advanced overrides. Base behavior:
    # the LIFETIME cap (Section 4) gates whether an add happens at all;
    # once spent, the trigger firing again does nothing (`prices` is
    # accepted for signature parity with the override, unused here). ──

    async def _handle_adjustment_trigger(
        self, runner, ts, side: str, bigger_now: float, prices: dict[int, float],
    ) -> None:
        if self.adjustments_used < self.max_adjustments:
            await self._adjust(runner, ts, side, bigger_now)
        # else: hard cap already spent for the day -- see module
        # docstring's "HARD CAP". Nothing else to do; reversal-unwind
        # (checked right after this in _maybe_manage) can't ALSO fire on
        # this same tick regardless (its own trigger, >=1.0x, is mutually
        # exclusive with this one, <=adjustment_trigger_ratio).

    # ── Adjustment: sell one more leg on the smaller side ──────────────

    async def _adjust(self, runner, ts, side: str, bigger_now: float, reason: str = "adjustment") -> None:
        target = self.adjustment_size_pct * bigger_now
        exclude = {leg["strike"] for leg in self.legs[side]}
        try:
            leg = await self.resolver.get_leg_by_premium(
                self.options_underlying, self.expiry_selector, side, target,
                strike_window=self.adjustment_strike_window, exclude_strikes=exclude,
            )
            price = await self.resolver.get_ltp(leg)
        except NoKiteSession:
            logger.warning(
                "%s: adjustment trigger fired but no Kite session yet — "
                "skipping this %s (will re-check next tick)",
                runner.deployment_name, reason,
            )
            return
        except Exception:
            logger.exception(
                "%s: failed to resolve/price the adjustment leg (%s, target "
                "premium %.2f) — skipping this %s", runner.deployment_name,
                side, target, reason,
            )
            return

        # trigger_values computed HERE, internally, from `side`/`bigger_now`
        # (already parameters) plus a fresh read of the smaller side's own
        # current premiums off the dispatcher — deliberately NOT a new
        # parameter, so intraday_dtt_advanced's existing call sites
        # (`self._adjust(runner, ts, side, bigger_now)` /
        # `..., reason="roll_open")`) need no changes at all.
        if reason == "adjustment":
            smaller_total = sum(
                (runner.dispatcher.last_prices.get(l["token"]) or l["entry_price"])
                for l in self.legs[side]
            ) if self.legs[side] else None
            trigger_values = {
                "side": side, "bigger_now": round(bigger_now, 2),
                "smaller_total": round(smaller_total, 2) if smaller_total is not None else None,
                "adjustment_trigger_ratio": self.adjustment_trigger_ratio,
                "trigger_threshold": round(self.adjustment_trigger_ratio * bigger_now, 2),
            }
        else:
            # roll_open (intraday_dtt_advanced) -- reopening immediately
            # after _unwind_one closed the cheapest leg at the concurrent
            # cap; the trigger is the cap being hit again, not a fresh
            # smaller/bigger comparison (see that module's docstring).
            trigger_values = {
                "side": side, "bigger_now": round(bigger_now, 2),
                "concurrent_legs_before_roll_open": len(self.legs[side]),
            }

        self.adjustments_used += 1
        role = f"adjustment_{self.adjustments_used}"
        self.adjusted_side = side
        qty = self.lots_per_trade * leg.lot_size

        runner.dispatcher.add_instruments(
            [{"instrument_token": leg.instrument_token, "symbol": leg.tradingsymbol}]
        )
        self.legs[side].append({
            "token": leg.instrument_token, "symbol": leg.tradingsymbol,
            "exchange": leg.exchange, "entry_price": price,
            "strike": leg.strike, "role": role,
        })
        await runner.sell(
            leg.tradingsymbol, leg.instrument_token, qty, price, ts,
            reason=reason,
            metadata=build_trade_meta(
                trigger=reason, action=f"sell_open_{side}",
                trigger_values=trigger_values, resulting_state=_legs_snapshot(self.legs),
                target_basis={
                    "target_premium": round(target, 2), "selected_strike": leg.strike, "fill_premium": price,
                },
                leg_role=role, leg=side, strike=leg.strike,
                expiry=leg.expiry.isoformat(), exchange=leg.exchange,
            ),
        )
        logger.info(
            "%s: %s #%d — sold %s %s@%.2f (target was %.2f) — %s "
            "side now %d leg(s)", runner.deployment_name, reason, self.adjustments_used,
            side, leg.tradingsymbol, price, target, side, len(self.legs[side]),
        )

    # ── Reversal: close the single cheapest leg on the adjusted side ───
    # (also reused, unmodified, as the "close" half of intraday_dtt_
    # advanced's roll — see that module's docstring)

    async def _unwind_one(
        self, runner, ts, prices: dict[int, float], reason: str = "reversal_unwind",
    ) -> None:
        side = self.adjusted_side
        # Lowest current premium wins; ties broken toward the
        # EARLIEST-OPENED leg (list order == open order, since legs are
        # only ever appended) -- documented in the module docstring.
        cheapest = min(self.legs[side], key=lambda l: (prices[l["token"]], self.legs[side].index(l)))
        price = prices[cheapest["token"]]

        # trigger_values computed HERE, internally, from `side`/`prices`
        # (already parameters) -- deliberately no new parameter, so
        # intraday_dtt_advanced's existing
        # `self._unwind_one(runner, ts, prices, reason="roll_close")` call
        # site needs no changes.
        leg_premiums = {l["symbol"]: round(prices[l["token"]], 2) for l in self.legs[side]}
        if reason == "reversal_unwind":
            anchor_side = OTHER_SIDE[side]
            smaller_total = sum(prices[l["token"]] for l in self.legs[side])
            bigger_now = prices[self.legs[anchor_side][0]["token"]] if self.legs[anchor_side] else None
            trigger_values = {
                "side": side, "smaller_total": round(smaller_total, 2),
                "bigger_now": round(bigger_now, 2) if bigger_now is not None else None,
                "leg_premiums": leg_premiums,
            }
        else:
            # roll_close (intraday_dtt_advanced) -- fired because `side`
            # is AT its concurrent leg cap, not a reversal condition; see
            # that module's _handle_adjustment_trigger.
            trigger_values = {
                "side": side, "concurrent_legs_before_roll": len(self.legs[side]),
                "leg_premiums": leg_premiums,
            }

        pos = runner.open_positions.get(cheapest["token"])
        self.legs[side].remove(cheapest)
        if pos is not None:
            result = await runner.buy(
                cheapest["symbol"], cheapest["token"], float(pos["qty"]), price, ts,
                reason=reason,
                metadata=build_trade_meta(
                    trigger=reason, action=f"buy_close_{side}",
                    trigger_values=trigger_values, resulting_state=_legs_snapshot(self.legs),
                ),
            )
            if result.get("realized_pnl") is not None:
                self.realized_pnl_today += result["realized_pnl"]
        runner.dispatcher.release_instruments([cheapest["token"]])
        logger.info(
            "%s: %s — closed %s %s@%.2f (%s side now %d leg(s), "
            "realized_pnl_today=%.2f)", runner.deployment_name, reason, side,
            cheapest["symbol"], price, side, len(self.legs[side]), self.realized_pnl_today,
        )

    # ── Full flatten: force-exit / break-even / profit-target ──────────

    async def _flatten_all(self, runner, ts, reason: str, trigger_values: dict) -> None:
        for side in ("CE", "PE"):
            for leg in list(self.legs[side]):
                price = runner.dispatcher.last_prices.get(leg["token"])
                if price is None:
                    price = leg["entry_price"]
                    logger.warning(
                        "%s: no live price for %s on flatten (%s) — closing "
                        "at entry_price %.2f (zero P&L on this leg)",
                        runner.deployment_name, leg["symbol"], reason, price,
                    )
                pos = runner.open_positions.get(leg["token"])
                self.legs[side].remove(leg)
                if pos is not None:
                    result = await runner.buy(
                        leg["symbol"], leg["token"], float(pos["qty"]), price, ts, reason=reason,
                        metadata=build_trade_meta(
                            trigger=reason, action=f"buy_close_{side}",
                            trigger_values=trigger_values, resulting_state=_legs_snapshot(self.legs),
                        ),
                    )
                    if result.get("realized_pnl") is not None:
                        self.realized_pnl_today += result["realized_pnl"]
                runner.dispatcher.release_instruments([leg["token"]])
        logger.info(
            "%s: flattened everything (%s) — realized_pnl_today=%.2f",
            runner.deployment_name, reason, self.realized_pnl_today,
        )

    async def on_stop(self, runner) -> None:
        tokens = [l["token"] for side_legs in self.legs.values() for l in side_legs]
        if tokens:
            runner.dispatcher.release_instruments(tokens)
        logger.info(
            "%s: strategy stopped (CE legs=%d, PE legs=%d, "
            "adjustments_used=%d, realized_pnl_today=%.2f)",
            runner.deployment_name, len(self.legs["CE"]), len(self.legs["PE"]),
            self.adjustments_used, self.realized_pnl_today,
        )

    def get_persistable_state(self) -> Optional[dict]:
        """today/entered_today only — see on_start's restore block for
        why this matters (the FLAT case specifically, which
        _resume_from_db can't reconstruct from the DB since there's no
        open/closed-today leg to reconstruct it from). Everything else
        is already resume-safe via runner.open_positions/
        list_closed_positions whenever a leg genuinely exists. None
        once self.today is None -- nothing meaningful yet."""
        if self.today is None:
            return None
        return {"version": 1, "today": self.today.isoformat(), "entered_today": self.entered_today}
