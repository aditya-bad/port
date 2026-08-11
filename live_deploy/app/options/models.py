"""
live_deploy — options data models.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class OptionLeg:
    """
    A single resolved, tradeable option (or future) contract.

    `last_price` is only populated when the leg was resolved through a
    method that necessarily fetched a live quote along the way (e.g.
    get_leg_by_premium, get_max_oi_strike) — it's a courtesy, not a
    live-refreshing field. Call resolver.get_ltp(leg) for a fresh price
    at the moment you actually need one (e.g. right before placing an
    order).
    """

    tradingsymbol: str
    instrument_token: int
    exchange: str
    underlying: str
    expiry: date
    strike: float
    option_type: str          # "CE" | "PE" | "FUT" (FUT has strike == 0)
    lot_size: int
    tick_size: float
    last_price: Optional[float] = None

    @property
    def key(self) -> str:
        """`EXCHANGE:TRADINGSYMBOL` — the format Kite's quote()/ltp() APIs expect."""
        return f"{self.exchange}:{self.tradingsymbol}"
