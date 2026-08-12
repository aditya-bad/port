"""
live_deploy — intraday_dtt_simple: a plain intraday short straddle.

Live paper-trading only — no backtested version exists for this one.

RULES:
  Entry (once per day, at `entry_time`, default 10:00): resolve THIS_WEEK
      (configurable) ATM strike from the live spot price, SELL the ATM CE
      and SELL the ATM PE at that SAME strike — a classic short straddle,
      same lot count both legs.
  Exit — checked continuously once both legs are open, in this priority
      order:
    1. Profit target: combined premium (CE price + PE price) has decayed
       `decay_pct` (default 10%) from the combined ENTRY premium -> exit
       both legs.
    2. Stop loss: EITHER leg's own premium has risen `spike_pct` (default
       40%) from ITS OWN entry premium -> exit BOTH legs, even though
       only one leg breached.
    3. Time stop: if neither has fired, force-exit both legs at
       `force_exit_time` (default 15:00) — required for this strategy,
       not optional, since the hard exit is one of its three defining
       rules.
  Exactly ONE entry per day. Once exited (any of the 3 reasons above), no
  same-day re-entry — it waits for the next day's `entry_time`.

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
  "decay_pct": 0.10 (default) — combined-premium profit-target decay,
      as a fraction (0.10 = 10%).
  "spike_pct": 0.40 (default) — single-leg stop-loss threshold, as a
      fraction (0.40 = 40%).
  "lots_per_trade": 1 (default) — lots sold per leg (same for both).
  "catch_up_late_entry": true (default) — see "LATE START" above.

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
from ..options import NoKiteSession, OptionsResolver
from .pivot_supertrend import _parse_hhmm
from .registry import register_strategy

logger = logging.getLogger("live_deploy.strategies.intraday_dtt_simple")


@register_strategy(
    "intraday_dtt_simple",
    description="Intraday short straddle — sell THIS_WEEK ATM CE+PE at "
               "entry_time, exit both on 10% combined-premium decay "
               "(profit) or either leg up 40% (stop), else hard exit at "
               "force_exit_time. Live paper-trading only.",
    default_config={
        "instrument_tokens": [256265],
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "expiry_selector": "THIS_WEEK",
        "entry_time": "10:00",
        "force_exit_time": "15:00",
        "decay_pct": 0.10,
        "spike_pct": 0.40,
        "lots_per_trade": 1,
        "catch_up_late_entry": True,
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

        self.decay_pct = float(cfg.get("decay_pct", 0.10))
        self.spike_pct = float(cfg.get("spike_pct", 0.40))
        self.lots_per_trade = int(cfg.get("lots_per_trade") or 1)
        if self.lots_per_trade < 1:
            raise ValueError(f"lots_per_trade must be >= 1, got {self.lots_per_trade}")
        self.catch_up_late_entry = bool(cfg.get("catch_up_late_entry", True))

        self.resolver = OptionsResolver(runner.dispatcher)

        self.today: Optional[date] = None
        self.entered_today = False
        self._late_start_today = False

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
            expiry = await self.resolver.resolve_expiry(self.options_underlying, self.expiry_selector)
            strike = await self.resolver.get_atm_strike(self.options_underlying, expiry)
            ce_leg = await self.resolver.get_leg(self.options_underlying, expiry, strike, "CE")
            pe_leg = await self.resolver.get_leg(self.options_underlying, expiry, strike, "PE")
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

        common_meta = {"strike": strike, "expiry": expiry.isoformat()}
        await runner.sell(
            ce_leg.tradingsymbol, ce_leg.instrument_token, qty, ce_price, ts,
            reason="entry", metadata={**common_meta, "leg": "CE", "exchange": ce_leg.exchange},
        )
        await runner.sell(
            pe_leg.tradingsymbol, pe_leg.instrument_token, qty, pe_price, ts,
            reason="entry", metadata={**common_meta, "leg": "PE", "exchange": pe_leg.exchange},
        )

        self.ce_token, self.ce_symbol = ce_leg.instrument_token, ce_leg.tradingsymbol
        self.ce_exchange, self.ce_entry_price = ce_leg.exchange, ce_price
        self.pe_token, self.pe_symbol = pe_leg.instrument_token, pe_leg.tradingsymbol
        self.pe_exchange, self.pe_entry_price = pe_leg.exchange, pe_price

        logger.info(
            "%s: sold straddle — CE %s@%.2f, PE %s@%.2f (combined=%.2f)",
            runner.deployment_name, ce_leg.tradingsymbol, ce_price,
            pe_leg.tradingsymbol, pe_price, ce_price + pe_price,
        )

    # ── Exit ─────────────────────────────────────────────────────────────

    async def _maybe_exit(self, runner, ts) -> None:
        if self.ce_token is None and self.pe_token is None:
            return
        t = ts.time()

        # Time stop always applies, regardless of whether live premium
        # data is available for either leg.
        if t >= self.force_exit_time:
            await self._exit_both(runner, ts, "force_exit")
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

        combined_entry = self.ce_entry_price + self.pe_entry_price
        combined_now = ce_now + pe_now
        if combined_now <= combined_entry * (1 - self.decay_pct):
            await self._exit_both(runner, ts, "profit_target_decay", ce_now, pe_now)
            return

        if (ce_now >= self.ce_entry_price * (1 + self.spike_pct)
                or pe_now >= self.pe_entry_price * (1 + self.spike_pct)):
            await self._exit_both(runner, ts, "leg_spike_stop", ce_now, pe_now)
            return

    async def _exit_both(
        self, runner, ts, reason: str,
        ce_now: Optional[float] = None, pe_now: Optional[float] = None,
    ) -> None:
        if self.ce_token is not None:
            price = ce_now if ce_now is not None else \
                (runner.dispatcher.last_prices.get(self.ce_token) or self.ce_entry_price)
            pos = runner.open_positions.get(self.ce_token)
            if pos is not None:
                await runner.buy(self.ce_symbol, self.ce_token, float(pos["qty"]), price, ts, reason=reason)
            runner.dispatcher.release_instruments([self.ce_token])
            self.ce_token = self.ce_symbol = self.ce_exchange = self.ce_entry_price = None

        if self.pe_token is not None:
            price = pe_now if pe_now is not None else \
                (runner.dispatcher.last_prices.get(self.pe_token) or self.pe_entry_price)
            pos = runner.open_positions.get(self.pe_token)
            if pos is not None:
                await runner.buy(self.pe_symbol, self.pe_token, float(pos["qty"]), price, ts, reason=reason)
            runner.dispatcher.release_instruments([self.pe_token])
            self.pe_token = self.pe_symbol = self.pe_exchange = self.pe_entry_price = None

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
