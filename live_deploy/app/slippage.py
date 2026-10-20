"""
live_deploy — estimated slippage: what a fill would have cost against
the live order book instead of the last traded price.

Paper fills are recorded at LTP. A real sell gets the bids (best bid
first, then the next level down for whatever quantity the first level
can't absorb) and a real buy pays the asks, so a real fill is usually
worse than LTP. This module only ESTIMATES that difference; it never
changes a fill. Recorded per fill (position_lots.book_price) and summed
per position (positions.estimated_slippage), shown via the "After
charges + slippage" step of the global P&L toggle.

Order book source: Kite full-mode ticks carry 5 levels of depth, which
the dispatcher caches per instrument (see LiveDataDispatcher.
last_depths). When a fill happens on an instrument with no recent cached
book — typically a brand-new leg subscribed in the same instant it is
sold — the runner fetches one quote() instead (see DeploymentRunner.
_book_price).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("live_deploy.slippage")

# A cached book older than this is not trusted for a fill happening now.
_MAX_BOOK_AGE = timedelta(seconds=30)
# Upper bound on the quote() fallback, so a slow Kite REST call can only
# ever delay a paper fill briefly, never hang it.
_QUOTE_TIMEOUT_S = 3.0


async def resolve_book_price(dispatcher, token: int, action: str, qty: float) -> Optional[float]:
    """Book price for a fill happening now: the dispatcher's cached depth
    if it arrived within _MAX_BOOK_AGE, else one quote() call. Never
    raises — any failure just means "no book" (None)."""
    try:
        cached = dispatcher.last_depths.get(token)
        if cached is not None and datetime.now(timezone.utc) - cached[1] <= _MAX_BOOK_AGE:
            return book_fill_price(cached[0], action, qty)

        from .options import OptionsResolver   # lazy: options imports the dispatcher layer
        quotes = await asyncio.wait_for(
            OptionsResolver(dispatcher).get_quotes([str(token)]), _QUOTE_TIMEOUT_S,
        )
        entry = next((q for q in quotes.values() if q.get("instrument_token") == token), None)
        return book_fill_price(entry.get("depth") if entry else None, action, qty)
    except Exception:
        logger.warning("No order book for token %s — slippage for this fill recorded as unknown", token)
        return None


def book_fill_price(depth: Optional[dict], action: str, qty: float) -> Optional[float]:
    """Average price `qty` would fill at by walking the book: a "sell"
    hits the bids, a "buy" lifts the asks. If the visible levels hold
    less than `qty`, the remainder is assumed to fill at the worst
    visible level (Kite shows only 5 levels, so this is an optimistic
    floor on real slippage for very large orders). None when that side
    of the book is empty."""
    if not depth or qty <= 0:
        return None
    levels = depth.get("buy" if action == "sell" else "sell") or []
    levels = [lv for lv in levels if (lv.get("price") or 0) > 0 and (lv.get("quantity") or 0) > 0]
    if not levels:
        return None
    remaining, cost = qty, 0.0
    for lv in levels:
        take = min(remaining, lv["quantity"])
        cost += take * lv["price"]
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        cost += remaining * levels[-1]["price"]
    return cost / qty


def realized_slippage(row) -> float:
    """A position row's slippage as it counts toward REALIZED P&L: only
    once closed. An open position's entry slippage is already in its
    estimated_slippage, but belongs to the unrealized side
    (projected_slippage) until it closes."""
    return float(row["estimated_slippage"] or 0) if row["status"] == "closed" else 0.0


def projected_slippage(position: dict, dispatcher, price=None) -> float:
    """Slippage for an OPEN position if it were closed now: what its own
    fills already cost (positions.estimated_slippage, the entry side) plus
    the exit side priced against the latest cached book. Display-only and
    hypothetical, like unrealized P&L: 0 without a live price (the same
    "not computed" convention as unrealized P&L and projected charges),
    entry side only without a cached book. Never fetches anything.
    `price` (app.prices.Price) overrides the live lookup; a stored mark
    has no current book, so only the entry side counts then."""
    token = int(position["instrument_token"])
    ltp = price.value if price is not None else dispatcher.last_prices.get(token)
    if ltp is None:
        return 0.0
    already = float(position.get("estimated_slippage") or 0)
    if price is not None and not price.live:
        return already
    cached = dispatcher.last_depths.get(token)
    if cached is None:
        return already
    exit_action = "sell" if position["side"] == "long" else "buy"
    qty = float(position["qty"])
    return already + fill_slippage(exit_action, ltp, book_fill_price(cached[0], exit_action, qty), qty)


def fill_slippage(action: str, ltp_price: float, book_price: Optional[float], qty: float) -> float:
    """Rupee cost of filling at `book_price` instead of `ltp_price`:
    positive = the book fill is worse (sold lower / bought higher). Can
    be negative when the last trade printed outside the current book.
    0 when there was no book."""
    if book_price is None:
        return 0.0
    per_unit = (ltp_price - book_price) if action == "sell" else (book_price - ltp_price)
    return per_unit * qty
