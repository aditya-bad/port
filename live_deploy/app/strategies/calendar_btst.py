"""
live_deploy — calendar_btst: an ATM calendar spread held overnight
(Buy-Today-Sell-Tomorrow), entered near the close and unwound early the
next trading day.

Live paper-trading only — no backtested version exists for this one.

STRUCTURE (4 legs, one entry, resolved together):
  SHORT a THIS_WEEK ATM straddle  (sell ATM CE, sell ATM PE)
  LONG  a NEXT_WEEK ATM straddle  (buy  ATM CE, buy  ATM PE)
  All four legs share the SAME strike — resolved ONCE from a single
  spot-price read (`OptionsResolver.get_atm_strike`) against
  THIS_WEEK's strike ladder, then reused for NEXT_WEEK's leg lookups
  too, rather than independently re-deriving "ATM" per expiry (which
  could theoretically drift by one strike step between two separate
  spot-price reads a few calls apart). Same lot count on all 4 legs
  (`lots_per_trade`).

  The trade this approximates: collect THIS_WEEK's faster theta decay
  overnight while the NEXT_WEEK long leg's slower decay gives some
  downside protection against a big overnight gap — but this strategy
  itself is mechanical (fixed time-of-day entry/exit, no gap-driven
  adjustment logic), not a gap-hedging algorithm.

RULES:
  Entry (once per day, at `entry_time`, default 15:20 — a few minutes
      before the close, the "end of day" window): resolves the
      shared ATM strike, sells the SHORT leg's CE+PE, buys the LONG
      leg's CE+PE, all at that one strike. Only fires when flat (no
      cycle currently open). Normally SHORT=THIS_WEEK/LONG=NEXT_WEEK —
      see "NEVER SKIPS" below for the one case those aren't literally
      "this/next calendar week."
  Exit (once per day, at `exit_time`, default 09:20 — the first ~5
      minutes after the 09:15 open): closes all 4 legs — buys back the
      2 short legs, sells the 2 long legs — but ONLY once the calendar
      date has actually advanced past the entry day (BTST is
      deliberately an OVERNIGHT hold; the position is never touched
      again on the same day it was opened, no matter what `exit_time`
      is set to).
  NEVER SKIPS a trading day, including THIS_WEEK's own expiry day
      (config: `switch_to_next_week_on_expiry`, default False) — a
      calendar spread's SHORT leg expiring the same afternoon it's sold
      isn't just risky the way a bare short straddle's would be, it's
      actively broken for THIS strategy specifically: BTST needs the
      short leg to still exist tomorrow morning to buy it back, and a
      contract that expired at today's close simply won't. This
      strategy's own paper-trading engine has no expiry-settlement
      simulation either — once Kite stops streaming ticks for an
      expired contract, `_exit_all`'s price lookup would silently fall
      back to a STALE pre-expiry tick, not the contract's real
      settlement value, quietly corrupting that leg's P&L rather than
      erroring. So unlike the DTT straddle family (where "sell the
      already-expiring contract anyway" is at least a coherent same-day
      trade), that's not a real option here — this decides which pair
      of expiries to trade, always shifted BOTH one step together so
      the spread's own one-week gap is preserved:
        False (default) -> sell the same-day-expiry SHORT leg anyway,
                            same as the old skip-guard's opt-in used to
                            mean — accepted with eyes open to both the
                            expiry-degenerate short leg AND the stale-
                            price gap above. NOT recommended; exists
                            for parity with every other strategy in
                            this package never silently removing a
                            config value's old meaning outright.
        True             -> shift the WHOLE spread one week later
                            instead: SHORT becomes what "NEXT_WEEK"
                            currently resolves to, LONG becomes the
                            expiry after THAT (offset 2 from today, not
                            a second "NEXT_WEEK" call — see `_enter`).
                            Shifting only the short leg while leaving
                            long at the old NEXT_WEEK would collapse
                            the spread to a zero-gap (same-expiry)
                            combo, which isn't a calendar spread at
                            all; shifting both preserves the one-week
                            gap that defines this trade.
      Checked against the ACTUAL resolved expiry date, never a
      hardcoded weekday, same as every other expiry-day guard in this
      package.

WHY "next day" IS TICK-DRIVEN, NOT CALENDAR MATH: like every other
day-boundary check in this package (see pivot_supertrend_options'
`_roll_over_day`), "the next trading day" is simply "the first tick
whose date() differs from the entry day" — ticks only ever arrive during
real trading sessions, so a weekend or a market holiday is automatically
skipped without this strategy needing its own holiday calendar.

"LATE START" / catch-up entry (config: `catch_up_late_entry`, default
True) — same semantics as intraday_dtt_simple: if this strategy
instance's very FIRST observed tick is already past `entry_time` with no
entry attempted yet today, `catch_up_late_entry=True` enters immediately
on that tick instead of waiting for tomorrow's `entry_time`.

RESUME-SAFETY: on_start reconstructs open legs from `runner.
open_positions` — a SHORT-side position (regardless of CE/PE) gets
bought back on exit, a LONG-side position gets sold on exit; which
specific expiry (THIS_WEEK vs NEXT_WEEK) each leg was originally resolved
from is irrelevant to the exit path, which only needs each leg's side,
token, symbol, and open quantity. `entry_day` (needed for the "don't
exit same-day" rule) is recovered from any resumed leg's own
`opened_at` timestamp — the same pattern strangle_monthly_v2 uses.

CASH NOTE: unlike a pure short straddle (both legs credit premium), the
2 NEXT_WEEK long legs are actually BOUGHT — real cash outlay, checked
for real by the same InsufficientCash path any other buy goes through.
All 4 legs are priced (via `OptionsResolver.get_ltp`) before ANY fill is
placed, so a pricing failure never leaves a partial entry — but once
fills start, a later leg failing (e.g. insufficient cash on the last of
the 4) can still leave a partial position, same accepted risk shape
every other multi-leg strategy in this package has.

CONFIG:
  "instrument_tokens": [<single token>] — the UNDERLYING's token, used
      ONLY to drive on_tick's time-of-day clock and as the spot-price
      reference for ATM strike resolution. Never itself traded.
  "symbol": underlying's display name — logging only.
  "options_underlying": REQUIRED, the options chain's own `name` (e.g.
      "NIFTY" — NOT the spot tradingsymbol "NIFTY 50").
  "entry_time": "15:20" (default) — end-of-day entry.
  "exit_time": "09:20" (default) — next-trading-day exit, first ~5 min
      after the open.
  "lots_per_trade": 1 (default) — lots on EVERY leg (all 4 match).
  "catch_up_late_entry": true (default) — see "LATE START" above.
  "switch_to_next_week_on_expiry": false (default) — when THIS_WEEK's
      contract expires today, false sells it anyway (same-day-expiry
      short leg, same-day-expiry P&L risk); true shifts the whole
      spread one week later instead (see "NEVER SKIPS" above). Either
      way, today still gets an entry — this never causes a day to be
      skipped.
"""

import logging
from datetime import date
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver, options_exchange_for
from .pivot_supertrend import _parse_hhmm
from .registry import register_strategy
from .trade_meta import build_trade_meta

logger = logging.getLogger("live_deploy.strategies.calendar_btst")


@register_strategy(
    "calendar_btst",
    description="Calendar BTST — sells an ATM CE+PE straddle in THIS_WEEK's "
               "expiry and buys an ATM CE+PE straddle at the SAME strike in "
               "NEXT_WEEK's expiry, entered near end-of-day and unwound in "
               "the first few minutes of the next trading day. Live "
               "paper-trading only.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "entry_time": "15:20",
        "exit_time": "09:20",
        "lots_per_trade": 1,
        "catch_up_late_entry": True,
        "switch_to_next_week_on_expiry": False,
    },
)
class CalendarBTSTStrategy(StrategyBase):

    async def on_start(self, runner) -> None:
        cfg = runner.config
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "calendar_btst requires config.instrument_tokens to be a "
                f"ONE-ELEMENT list — the underlying's token, used only for "
                f"timing/spot — got {tokens!r}"
            )
        self.instrument_token = tokens[0]
        self.symbol = cfg.get("symbol", str(self.instrument_token))

        self.options_underlying = cfg.get("options_underlying")
        if not self.options_underlying:
            raise ValueError(
                "calendar_btst requires config.options_underlying (the "
                "options chain's own `name`, e.g. \"NIFTY\" — NOT the spot "
                "tradingsymbol \"NIFTY 50\")"
            )

        self.entry_time = _parse_hhmm(cfg.get("entry_time", "15:20"))
        if self.entry_time is None:
            raise ValueError("calendar_btst requires a non-null entry_time")
        self.exit_time = _parse_hhmm(cfg.get("exit_time", "09:20"))
        if self.exit_time is None:
            raise ValueError("calendar_btst requires a non-null exit_time")

        self.lots_per_trade = int(cfg.get("lots_per_trade") or 1)
        if self.lots_per_trade < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {self.lots_per_trade}")
        self.catch_up_late_entry = bool(cfg.get("catch_up_late_entry", True))
        self.switch_to_next_week_on_expiry = bool(cfg.get("switch_to_next_week_on_expiry", False))

        # exchange=... : the options CHAIN's exchange (NFO for
        # NIFTY/BANKNIFTY/..., BFO for SENSEX/BANKEX) — see
        # options_exchange_for's own docstring in app/options/resolver.py
        # for the real bug this fixes: options_underlying is a free-form
        # config string here (not restricted to an enum), so a SENSEX/
        # BANKEX deployment without this would silently fail every
        # entry with "No option expiries found for 'SENSEX' on NFO" —
        # the same bug pivot_supertrend_options.py had (see main
        # README's Step 78).
        self.resolver = OptionsResolver(runner.dispatcher, exchange=options_exchange_for(self.options_underlying))

        self.today: Optional[date] = None
        self.entered_today = False
        self._late_start_today = False

        # Leg state -- classified by SIDE (short/long), not by which
        # expiry each was originally resolved from; exit only needs to
        # know "buy this back" vs "sell this off", never the expiry.
        # {instrument_token: {"symbol":..., "exchange":..., "entry_price":...}}
        self.short_legs: dict[int, dict] = {}
        self.long_legs: dict[int, dict] = {}
        self.entry_day: Optional[date] = None

        # Restore today/entered_today from a previous graceful stop, if
        # any — covers the FLAT case specifically (no open legs, e.g.
        # already exited this morning, or never entered yet). Without
        # this, every restart makes the next tick look like this
        # deployment's very first-ever observation, so a tick landing
        # after entry_time gets wrongly treated as a fresh "late start"
        # even for a deployment that's been running for weeks. The
        # resume-safety block right below still takes full precedence
        # whenever a leg IS open — it unconditionally sets both fields
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

        # Resume-safety: reattach to any already-open leg(s) from the DB.
        found = [
            (token, pos) for token, pos in runner.open_positions.items()
            if pos["symbol"].endswith("CE") or pos["symbol"].endswith("PE")
        ]
        if found:
            self.entered_today = True
            self.entry_day = found[0][1]["opened_at"].date()
            # Also seed self.today to entry_day, NOT left None -- on_tick's
            # day-change detection only fires on `day != self.today`, which
            # requires self.today to already be set. Leaving it None would
            # make the first post-resume tick take the "very first tick
            # ever" branch instead of a genuine day-change, so if that
            # first tick already lands on a LATER day than entry_day (the
            # common case: resumed the same morning the exit is due),
            # entered_today would never get reset for that new day and
            # today's entry would be wrongly skipped.
            self.today = self.entry_day
            for token, pos in found:
                exchange = (pos["metadata"] or {}).get("exchange", "NFO")
                leg = {"symbol": pos["symbol"], "exchange": exchange,
                       "entry_price": float(pos["avg_entry_price"])}
                target = self.short_legs if pos["side"] == "short" else self.long_legs
                target[token] = leg
                runner.dispatcher.add_instruments(
                    [{"instrument_token": token, "symbol": pos["symbol"]}]
                )
            logger.info(
                "%s: resumed with %d short leg(s) / %d long leg(s), "
                "entry_day=%s", runner.deployment_name,
                len(self.short_legs), len(self.long_legs), self.entry_day,
            )
            if len(self.short_legs) != 2 or len(self.long_legs) != 2:
                logger.warning(
                    "%s: resumed with an ASYMMETRIC leg count (expected "
                    "2 short + 2 long) — some legs may be missing from the "
                    "DB; exit will still close whatever IS present.",
                    runner.deployment_name,
                )

    # ── Tick loop ────────────────────────────────────────────────────────

    async def on_tick(self, runner, tick: dict) -> None:
        ts = tick.get("exchange_timestamp")
        price = tick.get("last_price")
        if ts is None or price is None:
            # exchange_timestamp only exists in Kite's "full" tick mode.
            return

        day = ts.date()
        if self.today is None:
            self.today = day
            self._late_start_today = (
                ts.time() >= self.entry_time and not self.entered_today
                and self.entry_day is None
            )
        elif day != self.today:
            self.today = day
            self.entered_today = False
            self._late_start_today = False

        # Exit checked first -- if yesterday's cycle is still open and
        # today has already crossed exit_time, close it before any
        # entry logic for TODAY even considers running.
        await self._maybe_exit(runner, ts)
        await self._maybe_enter(runner, ts)

    # ── Entry ────────────────────────────────────────────────────────────

    async def _maybe_enter(self, runner, ts) -> None:
        if self.entry_day is not None:
            return   # still holding an open cycle
        if self.entered_today:
            return
        t = ts.time()
        if t < self.entry_time:
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
            this_week_expiry = await self.resolver.resolve_expiry(self.options_underlying, "THIS_WEEK")
            switched_to_next_week = False
            if this_week_expiry == ts.date():
                if self.switch_to_next_week_on_expiry:
                    switched_to_next_week = True
                    logger.info(
                        "%s: THIS_WEEK's contract expires today (%s) — "
                        "switch_to_next_week_on_expiry=true, shifting the "
                        "whole calendar spread one week later instead of "
                        "selling an already-expiring short leg.",
                        runner.deployment_name, this_week_expiry,
                    )
                    # Both legs shift together, one step each, so the
                    # spread's own one-week gap is preserved -- offset 1
                    # is whatever "NEXT_WEEK" currently means, offset 2 is
                    # the expiry after THAT (deliberately NOT a second
                    # "NEXT_WEEK" resolve_expiry call, which would just
                    # return the same date again).
                    short_expiry = await self.resolver.resolve_expiry(self.options_underlying, 1)
                    long_expiry = await self.resolver.resolve_expiry(self.options_underlying, 2)
                else:
                    logger.info(
                        "%s: THIS_WEEK's contract expires today (%s) — "
                        "switch_to_next_week_on_expiry=false, selling the "
                        "same-day-expiry short leg as resolved (see this "
                        "strategy's own module docstring for why that's "
                        "NOT recommended here).",
                        runner.deployment_name, this_week_expiry,
                    )
                    short_expiry = this_week_expiry
                    long_expiry = await self.resolver.resolve_expiry(self.options_underlying, "NEXT_WEEK")
            else:
                short_expiry = this_week_expiry
                long_expiry = await self.resolver.resolve_expiry(self.options_underlying, "NEXT_WEEK")

            spot = await self.resolver.get_spot_price(self.options_underlying)
            strike = await self.resolver.get_atm_strike(self.options_underlying, short_expiry, spot_price=spot)

            short_ce = await self.resolver.get_leg(self.options_underlying, short_expiry, strike, "CE")
            short_pe = await self.resolver.get_leg(self.options_underlying, short_expiry, strike, "PE")
            long_ce = await self.resolver.get_leg(self.options_underlying, long_expiry, strike, "CE")
            long_pe = await self.resolver.get_leg(self.options_underlying, long_expiry, strike, "PE")

            # Price all 4 BEFORE any fill -- a pricing failure here never
            # leaves a partial entry.
            short_ce_price = await self.resolver.get_ltp(short_ce)
            short_pe_price = await self.resolver.get_ltp(short_pe)
            long_ce_price = await self.resolver.get_ltp(long_ce)
            long_pe_price = await self.resolver.get_ltp(long_pe)
        except NoKiteSession:
            logger.warning(
                "%s: entry_time reached but no Kite session yet — skipping "
                "today's entry", runner.deployment_name,
            )
            return
        except Exception:
            logger.exception(
                "%s: failed to resolve/price the calendar spread for entry "
                "— skipping today's entry", runner.deployment_name,
            )
            return

        qty = self.lots_per_trade * short_ce.lot_size   # all 4 legs share the same lot_size

        runner.dispatcher.add_instruments([
            {"instrument_token": short_ce.instrument_token, "symbol": short_ce.tradingsymbol},
            {"instrument_token": short_pe.instrument_token, "symbol": short_pe.tradingsymbol},
            {"instrument_token": long_ce.instrument_token, "symbol": long_ce.tradingsymbol},
            {"instrument_token": long_pe.instrument_token, "symbol": long_pe.tradingsymbol},
        ])

        trigger_values = {
            "tick_time": ts.time().isoformat(), "entry_time": self.entry_time.isoformat(),
            "late_start_today": self._late_start_today,
            "switched_to_next_week": switched_to_next_week,
        }
        # Named short_expiry/long_expiry, not this_week/next_week -- on
        # a switched day neither leg is literally "this/next calendar
        # week" anymore (see _enter's own switch branch above).
        common = {
            "strike": strike, "short_expiry": short_expiry.isoformat(),
            "long_expiry": long_expiry.isoformat(),
        }

        async def _fill_leg(leg, price, action, side_label, resulting_state):
            fn = runner.sell if action.startswith("sell") else runner.buy
            await fn(
                leg.tradingsymbol, leg.instrument_token, qty, price, ts,
                reason="entry",
                metadata=build_trade_meta(
                    trigger="entry_time_reached", action=action,
                    trigger_values=trigger_values, resulting_state=resulting_state,
                    target_basis={"selection_basis": "ATM", "selected_strike": strike, "fill_premium": price},
                    **common, leg=side_label, exchange=leg.exchange,
                ),
            )

        state: dict = {}
        state["SHORT_CE"] = {"strike": strike, "entry_price": round(short_ce_price, 2)}
        await _fill_leg(short_ce, short_ce_price, "sell_open_short_CE", "SHORT_CE", dict(state))
        state["SHORT_PE"] = {"strike": strike, "entry_price": round(short_pe_price, 2)}
        await _fill_leg(short_pe, short_pe_price, "sell_open_short_PE", "SHORT_PE", dict(state))
        state["LONG_CE"] = {"strike": strike, "entry_price": round(long_ce_price, 2)}
        await _fill_leg(long_ce, long_ce_price, "buy_open_long_CE", "LONG_CE", dict(state))
        state["LONG_PE"] = {"strike": strike, "entry_price": round(long_pe_price, 2)}
        await _fill_leg(long_pe, long_pe_price, "buy_open_long_PE", "LONG_PE", dict(state))

        self.short_legs = {
            short_ce.instrument_token: {"symbol": short_ce.tradingsymbol, "exchange": short_ce.exchange, "entry_price": short_ce_price},
            short_pe.instrument_token: {"symbol": short_pe.tradingsymbol, "exchange": short_pe.exchange, "entry_price": short_pe_price},
        }
        self.long_legs = {
            long_ce.instrument_token: {"symbol": long_ce.tradingsymbol, "exchange": long_ce.exchange, "entry_price": long_ce_price},
            long_pe.instrument_token: {"symbol": long_pe.tradingsymbol, "exchange": long_pe.exchange, "entry_price": long_pe_price},
        }
        self.entry_day = ts.date()

        logger.info(
            "%s: entered calendar spread — SHORT %s@%.2f/%s@%.2f (expiry %s), "
            "LONG %s@%.2f/%s@%.2f (expiry %s), strike=%s%s",
            runner.deployment_name,
            short_ce.tradingsymbol, short_ce_price, short_pe.tradingsymbol, short_pe_price, short_expiry,
            long_ce.tradingsymbol, long_ce_price, long_pe.tradingsymbol, long_pe_price, long_expiry,
            strike, " (switched to next week -- see above)" if switched_to_next_week else "",
        )

    # ── Exit ─────────────────────────────────────────────────────────────

    async def _maybe_exit(self, runner, ts) -> None:
        if self.entry_day is None:
            return
        if ts.date() <= self.entry_day:
            return   # BTST holds overnight -- never touched same-day, regardless of exit_time
        if ts.time() < self.exit_time:
            return
        await self._exit_all(runner, ts, "exit_time_next_day", trigger_values={
            "tick_time": ts.time().isoformat(), "exit_time": self.exit_time.isoformat(),
            "entry_day": self.entry_day.isoformat(),
        })

    async def _exit_all(self, runner, ts, reason: str, trigger_values: dict) -> None:
        for token, leg in list(self.short_legs.items()):
            pos = runner.open_positions.get(token)
            price = runner.dispatcher.last_prices.get(token) or leg["entry_price"]
            if pos is not None:
                await runner.buy(
                    leg["symbol"], token, float(pos["qty"]), price, ts,
                    reason=reason,
                    metadata=build_trade_meta(
                        trigger=reason, action="buy_close_short",
                        trigger_values=trigger_values, resulting_state={"leg": "closed"},
                    ),
                )
            runner.dispatcher.release_instruments([token])
        for token, leg in list(self.long_legs.items()):
            pos = runner.open_positions.get(token)
            price = runner.dispatcher.last_prices.get(token) or leg["entry_price"]
            if pos is not None:
                await runner.sell(
                    leg["symbol"], token, float(pos["qty"]), price, ts,
                    reason=reason,
                    metadata=build_trade_meta(
                        trigger=reason, action="sell_close_long",
                        trigger_values=trigger_values, resulting_state={"leg": "closed"},
                    ),
                )
            runner.dispatcher.release_instruments([token])

        self.short_legs = {}
        self.long_legs = {}
        self.entry_day = None
        logger.info("%s: exited calendar spread (%s)", runner.deployment_name, reason)

    async def on_stop(self, runner) -> None:
        tokens = list(self.short_legs) + list(self.long_legs)
        if tokens:
            runner.dispatcher.release_instruments(tokens)
        logger.info(
            "%s: strategy stopped (%d short leg(s), %d long leg(s) still open)",
            runner.deployment_name, len(self.short_legs), len(self.long_legs),
        )

    def get_persistable_state(self) -> Optional[dict]:
        """today/entered_today only — see on_start's restore block for
        why this matters (the FLAT case specifically, which the
        resume-safety reattach block can't reconstruct since there's no
        open leg to reconstruct it from). Everything else is already
        resume-safe via runner.open_positions whenever a leg genuinely
        exists. None once self.today is None -- nothing meaningful yet."""
        if self.today is None:
            return None
        return {"version": 1, "today": self.today.isoformat(), "entered_today": self.entered_today}
