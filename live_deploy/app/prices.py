"""One price per OPEN position, for every place that values one.

The live price (dispatcher.last_prices, from the tick stream) when there
is one; otherwise the position's last recorded mark (position_marks,
written every 1–5 minutes while the market is open). Without the mark a
holiday, a Kite outage or a restart left every open leg unpriced: no
LTP, unrealized shown as ₹0, while snapshots and the Desk disagreed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from .charges import estimate_options_charges
from .db import queries
from .slippage import projected_slippage


@dataclass(frozen=True)
class Price:
    value: float
    live: bool                        # from the tick stream (else a stored mark)
    at: Optional[datetime] = None     # when it traded / was marked, if known


async def open_prices(pool, dispatcher, positions: Iterable) -> dict:
    """{position id: Price} for the open positions that have any price."""
    out, missing = {}, []
    for p in positions:
        token = int(p["instrument_token"])
        live = dispatcher.last_prices.get(token)
        if live is not None:
            out[p["id"]] = Price(float(live), True, getattr(dispatcher, "last_price_times", {}).get(token))
        else:
            missing.append(p["id"])
    for pid, (price, at) in (await queries.latest_marks_at(pool, missing)).items():
        out[pid] = Price(float(price), False, at)
    return out


def price_fields(price: Optional[Price]) -> dict:
    """The response fields that say which price a row was valued at."""
    if price is None:
        return {"current_price": None, "price_live": False, "price_at": None}
    return {"current_price": price.value, "price_live": price.live, "price_at": price.at}


def open_values(position, price: Optional[Price], dispatcher) -> tuple[float, float, float]:
    """(unrealized, projected charges, projected slippage) for one open
    position at `price`; all 0 when it has never been priced."""
    if price is None:
        return 0.0, 0.0, 0.0
    qty, avg = float(position["qty"]), float(position["avg_entry_price"])
    long = position["side"] == "long"
    unreal = (price.value - avg) * qty if long else (avg - price.value) * qty
    buy, sell = (avg, price.value) if long else (price.value, avg)
    return unreal, estimate_options_charges(buy, sell, qty), projected_slippage(position, dispatcher, price)


def value_open_row(d: dict, price: Optional[Price], dispatcher) -> None:
    """Adds the price and unrealized fields (gross, net, net + slippage)
    to one open position row, in place."""
    d.update(price_fields(price))
    if price is None:
        return
    unreal, charges, slip = open_values(d, price, dispatcher)
    d["unrealized_pnl"] = unreal
    d["unrealized_pnl_net"] = unreal - charges
    d["unrealized_pnl_net_slip"] = unreal - charges - slip
