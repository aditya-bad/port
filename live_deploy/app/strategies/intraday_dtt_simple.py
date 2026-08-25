"""
live_deploy — intraday_dtt_simple: a plain intraday short straddle.

Live paper-trading only — no backtested version exists for this one.

RULES:
  Entry (once per day, at `entry_time`, default 10:00): resolve THIS_WEEK
      (configurable) contract's BALANCED strike — the strike whose CE and
      PE premiums are closest to each other, NOT simply whichever strike
      is nearest live spot (see resolve_atm_straddle_legs' own docstring
      for why those two differ, and OptionsResolver.get_balanced_straddle_strike
      for the mechanics) — SELL the CE and SELL the PE at that SAME
      strike, same lot count both legs.
  Exit — checked continuously once both legs are open, in this priority
      order:
    1. Stop loss: EITHER leg's own premium has risen `per_leg_stop_loss_pct`
       (default 40%) from ITS OWN entry premium -> exit BOTH legs, even
       though only one leg breached. Checked first — a sharp one-sided
       move that spikes one leg while the other leg's decay drags the
       COMBINED premium past combined_premium_profit_pct too (on the
       same tick) is real one-sided directional exposure, not calm
       two-sided decay, and the risk stop should win that tie.
    2. Profit target: combined premium (CE price + PE price) has decayed
       `combined_premium_profit_pct` (default 10%) from the combined
       ENTRY premium -> exit both legs.
    3. Time stop: if neither has fired, force-exit both legs at
       `force_exit_time` (default 15:00) — required for this strategy,
       not optional, since the hard exit is one of its three defining
       rules.
  Exactly ONE entry per day. Once exited (any of the 3 reasons above), no
  same-day re-entry — it waits for the next day's `entry_time`.
  NEVER skips a trading day, including the resolved contract's OWN
  expiry day (config: `switch_to_next_week_on_expiry`, default False) —
  selling options that expire that same afternoon is a fast-decay,
  sharp-gamma scenario, so this decides which contract gets traded
  instead of whether to trade at all:
    False (default) -> sell the same-day-expiry contract as resolved
                        (the old no-check behavior — opt in with eyes
                        open to same-day gamma).
    True             -> re-resolve using NEXT_WEEK instead, just for
                        today's entry (the configured `expiry_selector`
                        itself is untouched for every other day).
  Checked against the ACTUAL resolved expiry date, not a hardcoded
  weekday — the weekly expiry day has changed before and isn't
  guaranteed to stay put, so nothing here assumes it lands on any
  particular day of the week. (Formerly `allow_expiry_day_entry`, which
  could skip the day's entry entirely — that skip path no longer
  exists; every day gets an entry attempt now, this only picks which
  contract.)

WHY NO CANDLE AGGREGATION (unlike pivot_supertrend): there's no OHLC-
based signal here at all — entry is a plain time-of-day check against the
raw tick's own `exchange_timestamp` (needs `tick_mode: "full"`, same
requirement as pivot_supertrend), firing on the very first tick at/after
`entry_time` rather than waiting for a 5-min candle to close.

HOW EXIT MONITORING WORKS WITHOUT REST POLLING: once sold, both legs'
instrument_tokens are dynamically subscribed on the dispatcher (same
pattern as pivot_supertrend_options — see that module's docstring), so
their live ticks continuously update `dispatcher.last_prices`. Every
subsequent underlying tick then checks both legs' CURRENT prices via a
cheap in-memory dict lookup — no repeated REST calls, no polling
interval to tune. REST (`OptionsResolver.get_ltp`) is only ever used
once per leg, at the entry instant, to establish the entry price.

"LATE START" / catch-up entry (config: `catch_up_late_entry`, default
True): if this strategy instance's very FIRST observed tick already
shows a time-of-day past `entry_time` (deployed, or resumed, after
10:00 with no entry yet today) — as opposed to normally crossing
`entry_time` while already running — this flag decides what happens:
  True  (default) -> enter immediately on that first tick, using the
                      then-current spot price, same as any other entry.
  False             -> skip entry for the rest of TODAY only; the next
                      day's `entry_time` is unaffected and behaves
                      normally (this flag only ever governs the very
                      first tick of a fresh start/resume, never a
                      normal day-to-day crossing).
Deploying BEFORE `entry_time` (e.g. at 9:30 for a 10:00 entry) is never
"late" — it just waits for 10:00 like it would on any other day,
regardless of this flag.

CONFIG:
  "instrument_tokens": [<single token>] — the UNDERLYING's token (e.g.
      NIFTY 50's 256265), used ONLY to drive on_tick's time-of-day clock
      and for ATM strike resolution's spot price. The options actually
      traded are resolved dynamically and are never this token.
  "symbol": underlying's display name — logging only.
  "options_underlying": REQUIRED, the options chain's own `name`, e.g.
      "NIFTY" — NOT the spot tradingsymbol "NIFTY 50" (see
      app/options/resolver.py's INDEX_SPOT_SYMBOL note).
  "expiry_selector": "THIS_WEEK" (default) — any selector OptionsResolver
      accepts.
  "entry_time": "10:00" (default).
  "force_exit_time": "15:00" (default) — REQUIRED (cannot be null) for
      this strategy; the hard 3pm exit is one of its defining rules, not
      an optional extra the way it is for pivot_supertrend.
  "combined_premium_profit_pct": 0.10 (default) — profit target: how far
      the COMBINED (CE+PE) premium must decay from the combined ENTRY
      premium, as a fraction (0.10 = 10%). Named `decay_pct` before this
      was renamed for clarity — still read as a fallback, see on_start.
  "per_leg_stop_loss_pct": 0.40 (default) — stop loss: how far EITHER
      leg's OWN premium may rise from ITS OWN entry premium before
      exiting both, as a fraction (0.40 = 40%) — a PER-LEG threshold,
      not combined. Named `spike_pct` before this was renamed.
  "lots_per_trade": 1 (default) — lots sold per leg (same for both).
  "catch_up_late_entry": true (default) — see "LATE START" above.
  "switch_to_next_week_on_expiry": false (default) — when the resolved
      contract expires today, false sells it anyway (same-day gamma);
      true re-resolves NEXT_WEEK instead for that one entry (see
      "NEVER skips a trading day" above). Either way, today still gets
      an entry — this never causes a day to be skipped.

No margin model, same simplification as pivot_supertrend_options — a
short straddle's two SELL fills both credit premium (record_fill already
treats sell-first/buy-later as a short position, realized_pnl =
qty*(sell_price - buy_price) per leg), buying back to close is
cash-checked for real like any other buy.
"""

import logging
from datetime import date
from typing import Optional

from ..deployments.strategy_base import StrategyBase
from ..options import NoKiteSession, OptionsResolver, options_exchange_for
from .pivot_supertrend import _parse_hhmm
from .registry import register_strategy
from .trade_meta import build_trade_meta

logger = logging.getLogger("live_deploy.strategies.intraday_dtt_simple")


async def resolve_atm_straddle_legs(
    resolver: OptionsResolver, options_underlying: str, expiry_selector,
    ts, switch_to_next_week_on_expiry: bool, deployment_name: str,
) -> tuple:
    """
    Shared by intraday_dtt_simple AND intraday_dtt_adjusted — both sell
    an ATM CE + ATM PE straddle at entry_time with the identical
    expiry-day handling, so this is the ONE place that logic lives
    rather than being duplicated (and potentially drifting) across both
    strategy files.

    Resolves THIS_WEEK (or whatever expiry_selector says) ATM CE/PE legs
    for an entry. NEVER skips the entry — if the resolved contract
    expires TODAY (`ts.date()`), `switch_to_next_week_on_expiry` decides
    which contract gets traded instead of whether to trade at all:
      False (default) -> proceed with the same-day-expiry contract as
                          already resolved (same-day gamma, opted into).
      True             -> re-resolve using "NEXT_WEEK" instead, for THIS
                          entry only — `expiry_selector` itself is never
                          mutated, so every other day still resolves
                          however it's configured to.
    That decision is made as early as possible, right after the first
    resolve_expiry() and before strike/leg resolution or pricing.

    Strike itself comes from OptionsResolver.get_balanced_straddle_strike
    (the strike where CE and PE premiums are closest to each other, i.e.
    the forward-implied "fair" strike), NOT the plain spot-rounded ATM
    strike — see that method's own docstring for why the two differ and
    by how much (growing with time to expiry, ~0 right at expiry). A
    same-strike straddle built on the spot-rounded strike alone can
    start with a real premium skew between its two legs (one leg
    meaningfully pricier than the other from the moment it's sold) —
    which matters a lot for intraday_dtt_adjusted's adjustment trigger
    specifically: `smaller_side <= adjustment_trigger_ratio *
    bigger_side` compares the two sides' CURRENT premiums against each
    other, with no allowance for however skewed they already were at
    entry, so a skewed start leaves less real headroom before that
    trigger fires than the strategy's own rule intends.

    Returns `(ce_leg, pe_leg, expiry, strike, switched_to_next_week, spot)`
    — always a 6-tuple, never `None` (there is no skip case left).
    `switched_to_next_week` reflects what actually happened (true only
    when the NEXT_WEEK re-resolution above fired), for callers that want
    to record it in trade metadata. `spot` is the live underlying price
    this call actually used to pick the ATM strike — fetched explicitly,
    ONCE, here, and threaded into `get_atm_strike(spot_price=...)` rather
    than left for that method to fetch (and immediately discard)
    internally, so callers get the EXACT number "ATM" was computed from
    for their own trade-metadata recording (letting a strike selection
    be independently double-checked later — "was this genuinely the ATM
    strike for the spot at that moment") instead of a second, separately-
    timed spot read that could disagree with it by a few ticks.

    Does NOT catch NoKiteSession or any other exception — callers wrap
    this in their own try/except (both currently log and skip today's
    entry the same way, but that's a caller decision, not baked in here).
    """
    expiry = await resolver.resolve_expiry(options_underlying, expiry_selector)
    switched_to_next_week = False
    if expiry == ts.date():
        if switch_to_next_week_on_expiry:
            switched_to_next_week = True
            logger.info(
                "%s: resolved %s contract expires today (%s) — "
                "switch_to_next_week_on_expiry=true, re-resolving "
                "NEXT_WEEK for today's entry instead.",
                deployment_name, expiry_selector, expiry,
            )
            expiry = await resolver.resolve_expiry(options_underlying, "NEXT_WEEK")
        else:
            logger.info(
                "%s: resolved %s contract expires today (%s) — "
                "switch_to_next_week_on_expiry=false, selling the "
                "same-day-expiry straddle as resolved.",
                deployment_name, expiry_selector, expiry,
            )
    spot = await resolver.get_spot_price(options_underlying)
    strike = await resolver.get_balanced_straddle_strike(options_underlying, expiry)
    ce_leg = await resolver.get_leg(options_underlying, expiry, strike, "CE")
    pe_leg = await resolver.get_leg(options_underlying, expiry, strike, "PE")
    return ce_leg, pe_leg, expiry, strike, switched_to_next_week, spot


@register_strategy(
    "intraday_dtt_simple",
    description="Intraday short straddle — sell THIS_WEEK ATM CE+PE at "
               "entry_time, exit both if either leg is up 40% (stop) or "
               "10% combined-premium decay (profit), else hard exit at "
               "force_exit_time. Live paper-trading only.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "expiry_selector": "THIS_WEEK",
        "entry_time": "10:00",
        "force_exit_time": "15:00",
        "combined_premium_profit_pct": 0.10,
        "per_leg_stop_loss_pct": 0.40,
        "lots_per_trade": 1,
        "catch_up_late_entry": True,
        "switch_to_next_week_on_expiry": False,
    },
)
class IntradayDTTSimpleStrategy(StrategyBase):

    async def on_start(self, runner) -> None:
        cfg = runner.config
        tokens = cfg.get("instrument_tokens") or []
        if len(tokens) != 1:
            raise ValueError(
                "intraday_dtt_simple requires config.instrument_tokens to "
                f"be a ONE-ELEMENT list — the underlying's token, used only "
                f"for timing/spot — got {tokens!r}"
            )
        self.instrument_token = tokens[0]
        self.symbol = cfg.get("symbol", str(self.instrument_token))

        self.options_underlying = cfg.get("options_underlying")
        if not self.options_underlying:
            raise ValueError(
                "intraday_dtt_simple requires config.options_underlying "
                "(the options chain's own `name`, e.g. \"NIFTY\" — NOT the "
                "spot tradingsymbol \"NIFTY 50\")"
            )
        self.expiry_selector = cfg.get("expiry_selector", "THIS_WEEK")

        self.entry_time = _parse_hhmm(cfg.get("entry_time", "10:00"))
        if self.entry_time is None:
            raise ValueError("intraday_dtt_simple requires a non-null entry_time")

        raw_force_exit = cfg.get("force_exit_time", "15:00")
        if raw_force_exit is None:
            raise ValueError(
                "intraday_dtt_simple requires a non-null force_exit_time — "
                "the hard exit is one of this strategy's three defining "
                "rules, not an optional extra"
            )
        self.force_exit_time = _parse_hhmm(raw_force_exit)

        # decay_pct/spike_pct are the pre-rename names -- read as a
        # fallback so a deployment created before this rename (config
        # already persisted in Postgres with the old keys) keeps working
        # unchanged; a fresh deploy only ever sees the new names via
        # default_config above.
        self.combined_premium_profit_pct = float(
            cfg.get("combined_premium_profit_pct", cfg.get("decay_pct", 0.10))
        )
        self.per_leg_stop_loss_pct = float(
            cfg.get("per_leg_stop_loss_pct", cfg.get("spike_pct", 0.40))
        )
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

        # Restore today/entered_today from a previous graceful stop, if
        # any — see get_persistable_state below for why this matters:
        # without it, EVERY restart (redeploy, pause/resume) makes the
        # next tick look like this deployment's very first-ever
        # observation, so if that tick happens to land after entry_time,
        # it gets wrongly treated as a "late start" (catch_up_late_entry
        # question) even for a deployment that's been running fine for
        # weeks and simply had an operational restart. Restoring these
        # two fields means a same-day restart just resumes exactly where
        # it would have been — the late-start question only genuinely
        # applies on this deployment's real first day. Only ever
        # matters for the FLAT case; if a position is already open (the
        # block below), entered_today=True is established from the DB
        # regardless, unaffected by any of this.
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

        # Leg state — two independent legs, unlike pivot_supertrend_options'
        # single active_leg_*.
        self.ce_token: Optional[int] = None
        self.ce_symbol: Optional[str] = None
        self.ce_exchange: Optional[str] = None
        self.ce_entry_price: Optional[float] = None
        self.pe_token: Optional[int] = None
        self.pe_symbol: Optional[str] = None
        self.pe_exchange: Optional[str] = None
        self.pe_entry_price: Optional[float] = None

        # Resume-safety: reattach to any already-open leg(s) from the DB.
        found_legs = [
            (token, pos) for token, pos in runner.open_positions.items()
            if pos["symbol"].endswith("CE") or pos["symbol"].endswith("PE")
        ]
        if found_legs:
            self.entered_today = True
            for token, pos in found_legs:
                exchange = (pos["metadata"] or {}).get("exchange", "NFO")
                entry_price = float(pos["avg_entry_price"])
                if pos["symbol"].endswith("CE"):
                    self.ce_token, self.ce_symbol = token, pos["symbol"]
                    self.ce_exchange, self.ce_entry_price = exchange, entry_price
                else:
                    self.pe_token, self.pe_symbol = token, pos["symbol"]
                    self.pe_exchange, self.pe_entry_price = exchange, entry_price
                runner.dispatcher.add_instruments(
                    [{"instrument_token": token, "symbol": pos["symbol"]}]
                )
            if self.ce_token and self.pe_token:
                logger.info(
                    "%s: resumed with both straddle legs open: %s / %s",
                    runner.deployment_name, self.ce_symbol, self.pe_symbol,
                )
            else:
                logger.warning(
                    "%s: resumed with only ONE straddle leg open (%s) — "
                    "asymmetric state. Combined-premium exit checks need "
                    "BOTH legs' entry prices and are skipped until this is "
                    "reconciled; force_exit_time still applies to whatever "
                    "is open.", runner.deployment_name,
                    self.ce_symbol or self.pe_symbol,
                )

    async def on_tick(self, runner, tick: dict) -> None:
        ts = tick.get("exchange_timestamp")
        price = tick.get("last_price")
        if ts is None or price is None:
            # exchange_timestamp only exists in Kite's "full" tick mode.
            return

        day = ts.date()
        if self.today is None:
            self.today = day
            # This is the very first tick this instance has ever
            # processed — decide once whether we're starting "late"
            # (already past entry_time with no entry yet today).
            self._late_start_today = ts.time() >= self.entry_time and not self.entered_today
        elif day != self.today:
            self.today = day
            self.entered_today = False
            self._late_start_today = False   # a live day-boundary crossing is never "late"

        await self._maybe_enter(runner, ts)
        await self._maybe_exit(runner, ts)

    # ── Entry ────────────────────────────────────────────────────────────

    async def _maybe_enter(self, runner, ts) -> None:
        if self.entered_today or self.ce_token is not None or self.pe_token is not None:
            return
        t = ts.time()
        if t < self.entry_time:
            return
        if t >= self.force_exit_time:
            # Started (or resumed) after the whole trading window has
            # already closed for today — nothing sensible to do.
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
            ce_leg, pe_leg, expiry, strike, switched_to_next_week, spot = resolved
            ce_price = await self.resolver.get_ltp(ce_leg)
            pe_price = await self.resolver.get_ltp(pe_leg)
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

        qty = self.lots_per_trade * ce_leg.lot_size   # CE/PE share the same lot_size

        runner.dispatcher.add_instruments([
            {"instrument_token": ce_leg.instrument_token, "symbol": ce_leg.tradingsymbol},
            {"instrument_token": pe_leg.instrument_token, "symbol": pe_leg.tradingsymbol},
        ])

        # Continuous tick-check strategy (unlike the pivot_supertrend
        # family) -- no detection/execution split, so trigger_values are
        # just read straight out of local scope at the call site.
        trigger_values = {
            "tick_time": ts.time().isoformat(), "entry_time": self.entry_time.isoformat(),
            "late_start_today": self._late_start_today,
            "switched_to_next_week": switched_to_next_week,
        }
        # entry_spot: the live underlying price at entry -- same key name
        # intraday_dtt_adjusted.py already established for the identical
        # concept, plus "underlying_price" inside each target_basis below
        # so it's visible in the SAME structured block "selected_strike"
        # is, for a direct "how far was the balanced strike from spot"
        # check (see resolve_atm_straddle_legs -- the two can legitimately
        # differ by a few strike-steps now, that's the whole point).
        common_meta = {"strike": strike, "expiry": expiry.isoformat(), "entry_spot": round(spot, 2)}
        await runner.sell(
            ce_leg.tradingsymbol, ce_leg.instrument_token, qty, ce_price, ts,
            reason="entry",
            metadata=build_trade_meta(
                trigger="entry_time_reached", action="sell_open_CE",
                trigger_values=trigger_values,
                resulting_state={"CE": {"strike": strike, "entry_price": round(ce_price, 2)}},
                target_basis={"selection_basis": "ATM", "selected_strike": strike,
                            "underlying_price": round(spot, 2), "fill_premium": ce_price},
                **common_meta, leg="CE", exchange=ce_leg.exchange,
            ),
        )
        await runner.sell(
            pe_leg.tradingsymbol, pe_leg.instrument_token, qty, pe_price, ts,
            reason="entry",
            metadata=build_trade_meta(
                trigger="entry_time_reached", action="sell_open_PE",
                trigger_values=trigger_values,
                resulting_state={
                    "CE": {"strike": strike, "entry_price": round(ce_price, 2)},
                    "PE": {"strike": strike, "entry_price": round(pe_price, 2)},
                },
                target_basis={"selection_basis": "ATM", "selected_strike": strike,
                            "underlying_price": round(spot, 2), "fill_premium": pe_price},
                **common_meta, leg="PE", exchange=pe_leg.exchange,
            ),
        )

        self.ce_token, self.ce_symbol = ce_leg.instrument_token, ce_leg.tradingsymbol
        self.ce_exchange, self.ce_entry_price = ce_leg.exchange, ce_price
        self.pe_token, self.pe_symbol = pe_leg.instrument_token, pe_leg.tradingsymbol
        self.pe_exchange, self.pe_entry_price = pe_leg.exchange, pe_price

        # ONE notification for the whole straddle (2 fills above), not
        # two -- see runner.notify_execution's own docstring.
        await runner.notify_execution(
            "entry",
            f"Sold straddle — CE {ce_leg.tradingsymbol}@{ce_price:.2f}, "
            f"PE {pe_leg.tradingsymbol}@{pe_price:.2f} (combined={ce_price + pe_price:.2f})",
            metadata=common_meta,
        )

        logger.info(
            "%s: sold straddle — CE %s@%.2f, PE %s@%.2f (combined=%.2f), "
            "underlying=%.2f",
            runner.deployment_name, ce_leg.tradingsymbol, ce_price,
            pe_leg.tradingsymbol, pe_price, ce_price + pe_price, spot,
        )

    # ── Exit ─────────────────────────────────────────────────────────────

    async def _maybe_exit(self, runner, ts) -> None:
        if self.ce_token is None and self.pe_token is None:
            return
        t = ts.time()

        # Time stop always applies, regardless of whether live premium
        # data is available for either leg.
        if t >= self.force_exit_time:
            await self._exit_both(runner, ts, "force_exit", trigger_values={
                "tick_time": t.isoformat(), "force_exit_time": self.force_exit_time.isoformat(),
            })
            return

        # Combined-premium / single-leg checks need BOTH legs' live
        # prices — an asymmetric resume (only one leg reattached) skips
        # these entirely (see on_start's warning) until force_exit_time.
        if self.ce_token is None or self.pe_token is None:
            return

        ce_now = runner.dispatcher.last_prices.get(self.ce_token)
        pe_now = runner.dispatcher.last_prices.get(self.pe_token)
        if ce_now is None or pe_now is None:
            return   # no live tick yet for one of the legs — check again next tick

        # Risk stop checked BEFORE the profit target: if a sharp
        # one-sided move spikes one leg past per_leg_stop_loss_pct while
        # the other leg collapses enough that the combined sum ALSO nets
        # past combined_premium_profit_pct on the same tick, that's real
        # one-sided directional exposure, not calm two-sided decay — the
        # risk stop should win that tie, not the profit target.
        ce_spike_threshold = self.ce_entry_price * (1 + self.per_leg_stop_loss_pct)
        pe_spike_threshold = self.pe_entry_price * (1 + self.per_leg_stop_loss_pct)
        if ce_now >= ce_spike_threshold or pe_now >= pe_spike_threshold:
            await self._exit_both(runner, ts, "leg_spike_stop", ce_now, pe_now, trigger_values={
                "ce_now": round(ce_now, 2), "pe_now": round(pe_now, 2),
                "ce_entry_price": round(self.ce_entry_price, 2), "pe_entry_price": round(self.pe_entry_price, 2),
                "per_leg_stop_loss_pct": self.per_leg_stop_loss_pct,
                "ce_threshold": round(ce_spike_threshold, 2), "pe_threshold": round(pe_spike_threshold, 2),
            })
            return

        combined_entry = self.ce_entry_price + self.pe_entry_price
        combined_now = ce_now + pe_now
        target_combined = combined_entry * (1 - self.combined_premium_profit_pct)
        if combined_now <= target_combined:
            await self._exit_both(runner, ts, "profit_target_decay", ce_now, pe_now, trigger_values={
                "combined_entry": round(combined_entry, 2), "combined_now": round(combined_now, 2),
                "combined_premium_profit_pct": self.combined_premium_profit_pct,
                "target_combined": round(target_combined, 2),
            })
            return

    async def _exit_both(
        self, runner, ts, reason: str,
        ce_now: Optional[float] = None, pe_now: Optional[float] = None,
        trigger_values: Optional[dict] = None,
    ) -> None:
        trigger_values = trigger_values or {}
        had_position = self.ce_token is not None or self.pe_token is not None
        if self.ce_token is not None:
            price = ce_now if ce_now is not None else \
                (runner.dispatcher.last_prices.get(self.ce_token) or self.ce_entry_price)
            pos = runner.open_positions.get(self.ce_token)
            if pos is not None:
                meta = build_trade_meta(
                    trigger=reason, action="buy_close_CE",
                    trigger_values=trigger_values, resulting_state={"position": "flat"},
                )
                await runner.buy(self.ce_symbol, self.ce_token, float(pos["qty"]), price, ts,
                                 reason=reason, metadata=meta)
            runner.dispatcher.release_instruments([self.ce_token])
            self.ce_token = self.ce_symbol = self.ce_exchange = self.ce_entry_price = None

        if self.pe_token is not None:
            price = pe_now if pe_now is not None else \
                (runner.dispatcher.last_prices.get(self.pe_token) or self.pe_entry_price)
            pos = runner.open_positions.get(self.pe_token)
            if pos is not None:
                meta = build_trade_meta(
                    trigger=reason, action="buy_close_PE",
                    trigger_values=trigger_values, resulting_state={"position": "flat"},
                )
                await runner.buy(self.pe_symbol, self.pe_token, float(pos["qty"]), price, ts,
                                 reason=reason, metadata=meta)
            runner.dispatcher.release_instruments([self.pe_token])
            self.pe_token = self.pe_symbol = self.pe_exchange = self.pe_entry_price = None

        if had_position:
            await runner.notify_execution("exit", f"{reason}: exited straddle", metadata=trigger_values)

        logger.info("%s: exited straddle (%s)", runner.deployment_name, reason)

    async def on_stop(self, runner) -> None:
        # Release any dynamic subscriptions still held, whether pause or
        # stop — on_start re-subscribes on resume if a position is still
        # open (see the resume-safety block above).
        tokens = [t for t in (self.ce_token, self.pe_token) if t is not None]
        if tokens:
            runner.dispatcher.release_instruments(tokens)
        logger.info(
            "%s: strategy stopped (ce=%s, pe=%s)",
            runner.deployment_name, self.ce_symbol, self.pe_symbol,
        )

    def get_persistable_state(self) -> Optional[dict]:
        """today/entered_today only — see on_start's restore block for
        why this matters (a restart shouldn't be able to look like a
        fresh "late start" for a deployment that's been running for
        days). Everything else (open legs, entry prices) is already
        resume-safe via runner.open_positions, no need to duplicate it
        here. None once self.today is None -- nothing meaningful yet."""
        if self.today is None:
            return None
        return {"version": 1, "today": self.today.isoformat(), "entered_today": self.entered_today}
