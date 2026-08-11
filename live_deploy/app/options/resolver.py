"""
live_deploy — OptionsResolver: turn "what a strategy means" into a
concrete, tradeable OptionLeg.

    resolver = OptionsResolver(dispatcher)

    # "THIS_WEEK ATM CE"
    leg = await resolver.get_atm_leg("NIFTY", "THIS_WEEK", "CE")

    # "NEXT_WEEK ATM-10 CE"  (10 strike-steps below the ATM strike)
    leg = await resolver.get_leg_by_offset("NIFTY", "NEXT_WEEK", "CE", -10)

    # "THIS_WEEK CE with price closest to 40"
    leg = await resolver.get_leg_by_premium("NIFTY", "THIS_WEEK", "CE", 40)

    await runner.buy(leg.tradingsymbol, leg.instrument_token, leg.lot_size, leg.last_price)

Every public method is async: resolving anything here can require an
instrument-master fetch or a live quote — both are blocking HTTP calls
in kiteconnect (it's built on `requests`, not async) — so they're always
run off the event loop via asyncio.to_thread, never called directly from
async code. This keeps a slow Kite API response from stalling every
other deployment's tick processing on the same loop.

Full method list:
  Expiry:
    list_expiries(underlying)                          -> [date, ...]
    resolve_expiry(underlying, selector)                -> date
        selector: "THIS_WEEK" | "NEXT_WEEK" | "THIS_MONTH" | "NEXT_MONTH"
                  | int (0 = nearest, 1 = next, ...) | date | "YYYY-MM-DD"
  Strikes:
    list_strikes(underlying, expiry, option_type=None)  -> [float, ...]
    get_strike_step(underlying, expiry)                 -> float
    get_atm_strike(underlying, expiry)                  -> float
  Legs:
    get_leg(underlying, expiry_selector, strike, option_type)
    get_atm_leg(underlying, expiry_selector, option_type)
    get_leg_by_offset(underlying, expiry_selector, option_type, offset_steps)
    get_otm_leg(underlying, expiry_selector, option_type, steps=1)
    get_itm_leg(underlying, expiry_selector, option_type, steps=1)
    get_leg_by_premium(underlying, expiry_selector, option_type, target_price)
    get_max_oi_strike(underlying, expiry_selector, option_type=None)  -> (leg, oi)
    list_option_chain(underlying, expiry_selector)      -> {strike: {"CE": leg, "PE": leg}}
  Futures:
    get_futures_leg(underlying, expiry_selector="THIS_MONTH")
    get_futures_price(underlying, expiry_selector="THIS_MONTH")
  Pricing:
    get_ltp(leg_or_key)
    get_quote(leg_or_key)
    get_spot_price(underlying)      -- live tick cache first, REST fallback
  Misc:
    get_lot_size(underlying)
    round_to_lot(underlying, qty)   -- nearest whole-lot quantity, min 1 lot
    list_underlyings()              -- every options-eligible "name" on the exchange
"""

import asyncio
import logging
from dataclasses import replace
from datetime import date, datetime
from typing import Optional, Union

from .client import get_kite_connect
from .models import OptionLeg

logger = logging.getLogger("live_deploy.options")

# name field on option/future instrument rows doesn't always match the
# index's own spot tradingsymbol. Only indices need this — a stock's
# options are named after the stock itself (e.g. name == "RELIANCE",
# spot tradingsymbol is also "RELIANCE" on NSE), so anything not listed
# here falls back to (NSE, underlying) directly.
INDEX_SPOT_SYMBOL: dict[str, tuple[str, str]] = {
    "NIFTY": ("NSE", "NIFTY 50"),
    "BANKNIFTY": ("NSE", "NIFTY BANK"),
    "FINNIFTY": ("NSE", "NIFTY FIN SERVICE"),
    "MIDCPNIFTY": ("NSE", "NIFTY MIDCAP SELECT"),
    "SENSEX": ("BSE", "SENSEX"),
    "BANKEX": ("BSE", "BANKEX"),
}

# Instrument master is shared process-wide (not per-resolver-instance) —
# it's the same several-thousand-row list regardless of which deployment
# asks for it, and refetching it per strategy/deployment would be pure
# waste. Refreshed once per calendar day (Kite republishes it daily with
# newly-listed expiries); force_refresh=True bypasses that if ever needed.
_INSTRUMENT_CACHE: dict[str, dict] = {}
_CACHE_LOCKS: dict[str, asyncio.Lock] = {}


def _month_after(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


class OptionsResolver:
    def __init__(self, dispatcher, exchange: str = "NFO"):
        self.dispatcher = dispatcher
        self.exchange = exchange

    def _ex(self, exchange: Optional[str]) -> str:
        return exchange or self.exchange

    def _kite(self):
        return get_kite_connect(self.dispatcher)

    # ── Instrument master cache ─────────────────────────────────────────

    async def _ensure_instruments(self, exchange: str, force: bool = False) -> dict:
        cached = _INSTRUMENT_CACHE.get(exchange)
        today = date.today()
        if cached is not None and cached["date"] == today and not force:
            return cached

        lock = _CACHE_LOCKS.setdefault(exchange, asyncio.Lock())
        async with lock:
            # Re-check: another coroutine may have refreshed it while we
            # were waiting on the lock.
            cached = _INSTRUMENT_CACHE.get(exchange)
            if cached is not None and cached["date"] == today and not force:
                return cached

            kite = self._kite()
            data = await asyncio.to_thread(kite.instruments, exchange)
            by_name: dict[str, list[dict]] = {}
            for row in data:
                by_name.setdefault(row["name"], []).append(row)
            cached = {"date": today, "data": data, "by_name": by_name}
            _INSTRUMENT_CACHE[exchange] = cached
            logger.info(
                "Fetched %s instrument master: %d rows, %d underlyings",
                exchange, len(data), len(by_name),
            )
            return cached

    async def _underlying_rows(self, underlying: str, exchange: Optional[str] = None) -> list[dict]:
        exchange = self._ex(exchange)
        cache = await self._ensure_instruments(exchange)
        return cache["by_name"].get(underlying.strip().upper(), [])

    def _row_to_leg(self, row: dict) -> OptionLeg:
        return OptionLeg(
            tradingsymbol=row["tradingsymbol"],
            instrument_token=row["instrument_token"],
            exchange=row["exchange"],
            underlying=row["name"],
            expiry=row["expiry"],
            strike=row["strike"],
            option_type=row["instrument_type"],
            lot_size=row["lot_size"],
            tick_size=row["tick_size"],
        )

    async def list_underlyings(self, exchange: Optional[str] = None) -> list[str]:
        exchange = self._ex(exchange)
        cache = await self._ensure_instruments(exchange)
        names = {r["name"] for r in cache["data"] if r["instrument_type"] in ("CE", "PE")}
        return sorted(names)

    # ── Expiry resolution ───────────────────────────────────────────────

    async def list_expiries(self, underlying: str, exchange: Optional[str] = None) -> list[date]:
        rows = await self._underlying_rows(underlying, exchange)
        return sorted({r["expiry"] for r in rows if r["instrument_type"] in ("CE", "PE")})

    def _resolve_from_list(
        self, expiries: list[date], selector, reference_date: Optional[date] = None,
    ) -> date:
        if not expiries:
            raise ValueError("No expiries to resolve against")
        today = reference_date or date.today()
        upcoming = sorted(e for e in expiries if e >= today)

        if isinstance(selector, datetime):
            selector = selector.date()

        if isinstance(selector, date):
            if selector not in expiries:
                raise ValueError(f"{selector} is not a listed expiry. Available: {expiries}")
            return selector

        if isinstance(selector, bool):
            raise TypeError(f"Unsupported expiry selector: {selector!r}")

        if isinstance(selector, int):
            if selector < 0 or selector >= len(upcoming):
                raise ValueError(
                    f"No expiry at offset {selector} — only {len(upcoming)} upcoming "
                    f"expiries: {upcoming}"
                )
            return upcoming[selector]

        if isinstance(selector, str):
            sel = selector.strip().upper()
            if sel == "THIS_WEEK":
                if not upcoming:
                    raise ValueError("No upcoming expiries")
                return upcoming[0]
            if sel == "NEXT_WEEK":
                if len(upcoming) < 2:
                    raise ValueError(f"No NEXT_WEEK expiry — only {len(upcoming)} upcoming: {upcoming}")
                return upcoming[1]
            if sel in ("THIS_MONTH", "NEXT_MONTH"):
                year, month = today.year, today.month
                if sel == "NEXT_MONTH":
                    year, month = _month_after(year, month)
                in_month = sorted(e for e in expiries if e.year == year and e.month == month)
                if not in_month:
                    raise ValueError(f"No {sel} expiry listed yet ({year}-{month:02d})")
                return in_month[-1]   # the monthly contract == the last expiry within that month
            # Fall through: try as an ISO date string ("2026-08-14")
            try:
                parsed = date.fromisoformat(selector.strip())
            except ValueError:
                raise ValueError(
                    f"Unknown expiry selector {selector!r}. Use THIS_WEEK, NEXT_WEEK, "
                    f"THIS_MONTH, NEXT_MONTH, an int offset, a date, or an ISO date string."
                )
            if parsed not in expiries:
                raise ValueError(f"{parsed} is not a listed expiry. Available: {expiries}")
            return parsed

        raise TypeError(f"Unsupported expiry selector type: {type(selector)}")

    async def resolve_expiry(
        self, underlying: str, selector: Union[str, int, date], exchange: Optional[str] = None,
        reference_date: Optional[date] = None,
    ) -> date:
        expiries = await self.list_expiries(underlying, exchange)
        if not expiries:
            raise ValueError(f"No option expiries found for {underlying!r} on {self._ex(exchange)}")
        return self._resolve_from_list(expiries, selector, reference_date)

    # ── Strike resolution ───────────────────────────────────────────────

    async def list_strikes(
        self, underlying: str, expiry: date, option_type: Optional[str] = None,
        exchange: Optional[str] = None,
    ) -> list[float]:
        rows = await self._underlying_rows(underlying, exchange)
        types = ("CE", "PE") if option_type is None else (option_type.upper(),)
        return sorted({r["strike"] for r in rows if r["expiry"] == expiry and r["instrument_type"] in types})

    async def get_strike_step(self, underlying: str, expiry: date, exchange: Optional[str] = None) -> float:
        strikes = await self.list_strikes(underlying, expiry, exchange=exchange)
        if len(strikes) < 2:
            raise ValueError(f"Not enough listed strikes for {underlying} {expiry} to derive a step")
        # Real, listed strike spacing rather than a hardcoded per-underlying
        # constant — spacing changes over time (NIFTY has used 50 and 100
        # at different points) and differs by underlying.
        gaps = sorted({round(b - a, 4) for a, b in zip(strikes, strikes[1:])})
        return gaps[0]

    async def get_atm_strike(
        self, underlying: str, expiry: date, exchange: Optional[str] = None,
        spot_price: Optional[float] = None,
    ) -> float:
        spot = spot_price if spot_price is not None else await self.get_spot_price(underlying)
        step = await self.get_strike_step(underlying, expiry, exchange)
        raw_atm = round(spot / step) * step
        strikes = await self.list_strikes(underlying, expiry, exchange=exchange)
        # Snap to an actually-listed strike — handles float rounding and
        # any irregular spacing (e.g. tighter strikes right around spot).
        return min(strikes, key=lambda s: abs(s - raw_atm))

    # ── Leg resolution ───────────────────────────────────────────────────

    async def get_leg(
        self, underlying: str, expiry_selector: Union[str, int, date], strike: float,
        option_type: str, exchange: Optional[str] = None,
    ) -> OptionLeg:
        option_type = option_type.upper()
        expiry = await self.resolve_expiry(underlying, expiry_selector, exchange)
        rows = await self._underlying_rows(underlying, exchange)
        for row in rows:
            if (row["expiry"] == expiry and row["instrument_type"] == option_type
                    and abs(row["strike"] - strike) < 0.01):
                return self._row_to_leg(row)
        raise ValueError(f"No {option_type} leg for {underlying} {expiry} strike {strike}")

    async def get_atm_leg(
        self, underlying: str, expiry_selector: Union[str, int, date], option_type: str,
        exchange: Optional[str] = None,
    ) -> OptionLeg:
        expiry = await self.resolve_expiry(underlying, expiry_selector, exchange)
        strike = await self.get_atm_strike(underlying, expiry, exchange)
        return await self.get_leg(underlying, expiry, strike, option_type, exchange)

    async def get_leg_by_offset(
        self, underlying: str, expiry_selector: Union[str, int, date], option_type: str,
        offset_steps: int, exchange: Optional[str] = None,
    ) -> OptionLeg:
        """
        `offset_steps` is counted in strike-steps away from ATM: positive
        = higher strikes, negative = lower strikes. This is what "ATM-10"
        / "ATM+5" mean in chain jargon — NOT rupee offsets.

            get_leg_by_offset("NIFTY", "NEXT_WEEK", "CE", -10)  # "NEXT_WEEK ATM-10 CE"
        """
        expiry = await self.resolve_expiry(underlying, expiry_selector, exchange)
        step = await self.get_strike_step(underlying, expiry, exchange)
        atm = await self.get_atm_strike(underlying, expiry, exchange)
        target = atm + offset_steps * step
        strikes = await self.list_strikes(underlying, expiry, option_type, exchange)
        nearest = min(strikes, key=lambda s: abs(s - target))
        return await self.get_leg(underlying, expiry, nearest, option_type, exchange)

    async def get_otm_leg(
        self, underlying: str, expiry_selector: Union[str, int, date], option_type: str,
        steps: int = 1, exchange: Optional[str] = None,
    ) -> OptionLeg:
        """
        `steps` strikes out-of-the-money. Direction depends on option
        type: a CE is OTM ABOVE spot, a PE is OTM BELOW spot — this
        flips the sign so callers never have to think about it.
        """
        option_type = option_type.upper()
        if steps < 0:
            raise ValueError("steps must be >= 0")
        sign = 1 if option_type == "CE" else -1
        return await self.get_leg_by_offset(underlying, expiry_selector, option_type, sign * steps, exchange)

    async def get_itm_leg(
        self, underlying: str, expiry_selector: Union[str, int, date], option_type: str,
        steps: int = 1, exchange: Optional[str] = None,
    ) -> OptionLeg:
        """`steps` strikes in-the-money — mirror image of get_otm_leg."""
        option_type = option_type.upper()
        if steps < 0:
            raise ValueError("steps must be >= 0")
        sign = -1 if option_type == "CE" else 1
        return await self.get_leg_by_offset(underlying, expiry_selector, option_type, sign * steps, exchange)

    async def get_leg_by_premium(
        self, underlying: str, expiry_selector: Union[str, int, date], option_type: str,
        target_price: float, strike_window: int = 15, exchange: Optional[str] = None,
    ) -> OptionLeg:
        """
        The leg (of `option_type`) whose current LTP is closest to
        `target_price` — "THIS_WEEK CE with price closest to 40".

        Only searches a bounded window of `strike_window` strike-steps on
        either side of ATM (not the whole chain) — premiums move roughly
        monotonically with distance from ATM, so this stays both correct
        for realistic target premiums and cheap (one batched ltp() call
        over a few dozen strikes instead of the full chain, which can be
        150+ strikes for NIFTY).
        """
        option_type = option_type.upper()
        expiry = await self.resolve_expiry(underlying, expiry_selector, exchange)
        atm = await self.get_atm_strike(underlying, expiry, exchange)
        step = await self.get_strike_step(underlying, expiry, exchange)
        all_strikes = await self.list_strikes(underlying, expiry, option_type, exchange)

        window = [s for s in all_strikes if abs(s - atm) <= strike_window * step + step / 2]
        candidates = window or all_strikes   # fallback: window too tight for this chain
        legs = [await self.get_leg(underlying, expiry, s, option_type, exchange) for s in candidates]

        kite = self._kite()
        keys = [leg.key for leg in legs]
        ltp_resp = await asyncio.to_thread(kite.ltp, keys)

        best_leg, best_diff, best_price = None, None, None
        for leg in legs:
            entry = ltp_resp.get(leg.key)
            if not entry:
                continue
            price = entry["last_price"]
            diff = abs(price - target_price)
            if best_diff is None or diff < best_diff:
                best_leg, best_diff, best_price = leg, diff, price

        if best_leg is None:
            raise ValueError(
                f"Could not fetch quotes for any {option_type} leg near ATM "
                f"for {underlying} {expiry}"
            )
        return replace(best_leg, last_price=best_price)

    async def get_max_oi_strike(
        self, underlying: str, expiry_selector: Union[str, int, date], option_type: Optional[str] = None,
        strike_window: Optional[int] = None, exchange: Optional[str] = None,
    ) -> tuple[OptionLeg, int]:
        """
        The leg with the highest open interest — a common support/
        resistance signal (max CE OI = resistance, max PE OI = support).
        Defaults to scanning the WHOLE chain (both CE and PE, all
        strikes); pass strike_window to bound it around ATM like
        get_leg_by_premium does, for a faster/cheaper call.

        Returns (leg, open_interest) — not just the leg, since the OI
        value itself is usually the point of calling this.
        """
        expiry = await self.resolve_expiry(underlying, expiry_selector, exchange)
        types = [option_type.upper()] if option_type else ["CE", "PE"]

        legs: list[OptionLeg] = []
        for ot in types:
            strikes = await self.list_strikes(underlying, expiry, ot, exchange)
            if strike_window is not None:
                atm = await self.get_atm_strike(underlying, expiry, exchange)
                step = await self.get_strike_step(underlying, expiry, exchange)
                strikes = [s for s in strikes if abs(s - atm) <= strike_window * step + step / 2]
            for s in strikes:
                legs.append(await self.get_leg(underlying, expiry, s, ot, exchange))

        kite = self._kite()
        keys = [leg.key for leg in legs]
        quotes: dict = {}
        for i in range(0, len(keys), 200):   # chunk defensively — quote() has an instrument cap
            chunk = await asyncio.to_thread(kite.quote, keys[i:i + 200])
            quotes.update(chunk)

        best_leg, best_oi, best_price = None, -1, None
        for leg in legs:
            entry = quotes.get(leg.key)
            if not entry:
                continue
            oi = entry.get("oi")
            if oi is not None and oi > best_oi:
                best_leg, best_oi, best_price = leg, oi, entry.get("last_price")

        if best_leg is None:
            raise ValueError(f"Could not fetch OI for any leg of {underlying} {expiry}")
        return replace(best_leg, last_price=best_price), best_oi

    async def list_option_chain(
        self, underlying: str, expiry_selector: Union[str, int, date], exchange: Optional[str] = None,
    ) -> dict[float, dict[str, Optional[OptionLeg]]]:
        """{strike: {"CE": OptionLeg|None, "PE": OptionLeg|None}}, sorted by strike."""
        expiry = await self.resolve_expiry(underlying, expiry_selector, exchange)
        rows = await self._underlying_rows(underlying, exchange)
        chain: dict[float, dict[str, Optional[OptionLeg]]] = {}
        for row in rows:
            if row["expiry"] != expiry or row["instrument_type"] not in ("CE", "PE"):
                continue
            chain.setdefault(row["strike"], {"CE": None, "PE": None})[row["instrument_type"]] = \
                self._row_to_leg(row)
        return dict(sorted(chain.items()))

    # ── Futures ──────────────────────────────────────────────────────────

    async def get_futures_leg(
        self, underlying: str, expiry_selector: Union[str, int, date] = "THIS_MONTH",
        exchange: Optional[str] = None,
    ) -> OptionLeg:
        rows = await self._underlying_rows(underlying, exchange)
        fut_rows = [r for r in rows if r["instrument_type"] == "FUT"]
        if not fut_rows:
            raise ValueError(f"No futures contracts listed for {underlying!r} on {self._ex(exchange)}")
        fut_expiries = sorted({r["expiry"] for r in fut_rows})
        expiry = self._resolve_from_list(fut_expiries, expiry_selector)
        for row in fut_rows:
            if row["expiry"] == expiry:
                return self._row_to_leg(row)
        raise ValueError(f"No futures leg for {underlying} {expiry}")   # unreachable in practice

    async def get_futures_price(
        self, underlying: str, expiry_selector: Union[str, int, date] = "THIS_MONTH",
        exchange: Optional[str] = None,
    ) -> float:
        leg = await self.get_futures_leg(underlying, expiry_selector, exchange)
        return await self.get_ltp(leg)

    # ── Live pricing ─────────────────────────────────────────────────────

    async def get_ltp(self, leg_or_key: Union[OptionLeg, str]) -> float:
        key = leg_or_key.key if isinstance(leg_or_key, OptionLeg) else leg_or_key
        kite = self._kite()
        resp = await asyncio.to_thread(kite.ltp, key)
        return resp[key]["last_price"]

    async def get_quote(self, leg_or_key: Union[OptionLeg, str]) -> dict:
        key = leg_or_key.key if isinstance(leg_or_key, OptionLeg) else leg_or_key
        kite = self._kite()
        resp = await asyncio.to_thread(kite.quote, key)
        return resp[key]

    async def get_spot_price(self, underlying: str) -> float:
        """
        Prefers the dispatcher's live tick cache (no REST round-trip at
        all — the WebSocket already carries this if the spot index/stock
        happens to be subscribed) and only falls back to a REST ltp()
        call if it isn't currently in that cache.
        """
        underlying = underlying.strip().upper()
        exchange, symbol = INDEX_SPOT_SYMBOL.get(underlying, ("NSE", underlying))

        for token, label in self.dispatcher.token_labels.items():
            if label == symbol or label == underlying:
                price = self.dispatcher.last_prices.get(token)
                if price is not None:
                    return price

        key = f"{exchange}:{symbol}"
        kite = self._kite()
        resp = await asyncio.to_thread(kite.ltp, key)
        return resp[key]["last_price"]

    # ── Misc ─────────────────────────────────────────────────────────────

    async def get_lot_size(self, underlying: str, exchange: Optional[str] = None) -> int:
        rows = await self._underlying_rows(underlying, exchange)
        if not rows:
            raise ValueError(f"No instruments found for {underlying!r} on {self._ex(exchange)}")
        return rows[0]["lot_size"]

    async def round_to_lot(self, underlying: str, qty: float, exchange: Optional[str] = None) -> int:
        """Nearest whole-lot quantity for `underlying`, minimum 1 lot."""
        lot_size = await self.get_lot_size(underlying, exchange)
        lots = max(1, round(qty / lot_size))
        return lots * lot_size
