"""
live_deploy — cross-deployment aggregate views.

Every other router in this package is scoped to a single deployment_id
(a URL of the shape /deployments/{deployment_id}/...). These two
endpoints are the deliberate exception: they exist specifically for the
Dashboard's consolidated views, which need data spanning EVERY
deployment at once. Aggregating server-side here, rather than having
the frontend fetch every deployment's own positions/trades and merge
them client-side, avoids N+1 requests and stays correct as the number
of deployments grows — the whole reason this router exists rather than
just leaving it to the frontend.
"""

from fastapi import APIRouter, Request

from ..db import queries
from ..deployments.schemas import AggregatePositionOut, RecentTradeOut

router = APIRouter(tags=["aggregate"])


async def fetch_positions_open(pool, dispatcher) -> list[dict]:
    """status="open" is the only value the frontend ever actually
    requests (Dashboard.load() calls Api.getAllPositions('open')) --
    pulled out on its own so app.state.cache's background loop can call
    it directly. See app/cache.py for why this is cached at all."""
    rows = await queries.list_all_positions(pool, status="open")
    out = []
    for r in rows:
        d = dict(r)
        price = dispatcher.last_prices.get(int(d["instrument_token"]))
        d["current_price"] = price
        if price is not None:
            qty, avg = float(d["qty"]), float(d["avg_entry_price"])
            d["unrealized_pnl"] = (price - avg) * qty if d["side"] == "long" \
                else (avg - price) * qty
        out.append(d)
    return out


@router.get("/positions", response_model=list[AggregatePositionOut])
async def list_all_positions(request: Request, status: str | None = "open"):
    """
    Every position across every deployment (default: open only — pass
    status=closed or status= (empty) for everything), each annotated
    with which deployment/strategy it belongs to. Mark-to-market pricing
    uses the same dispatcher.last_prices cache and the same formula as
    the per-deployment /deployments/{id}/positions endpoint — a
    deployment's own positions tab and this aggregate table are
    computed identically, so they can never silently disagree.
    """
    # status="open" (the default, and the only value the frontend ever
    # sends) is the cached hot path -- anything else falls through to a
    # live query.
    if status == "open":
        return await request.app.state.cache.get("positions_open")
    pool = request.app.state.db_pool
    rows = await queries.list_all_positions(pool, status=status)
    dispatcher = request.app.state.dispatcher

    out = []
    for r in rows:
        d = dict(r)
        if d["status"] == "open":
            price = dispatcher.last_prices.get(int(d["instrument_token"]))
            d["current_price"] = price
            if price is not None:
                qty, avg = float(d["qty"]), float(d["avg_entry_price"])
                d["unrealized_pnl"] = (price - avg) * qty if d["side"] == "long" \
                    else (avg - price) * qty
        out.append(d)
    return out


async def fetch_trades_recent(pool) -> list[dict]:
    """limit=20 is the only value the frontend ever actually requests
    (Dashboard.load() calls Api.getRecentTrades(20)) -- pulled out on
    its own so app.state.cache's background loop can call it directly."""
    rows = await queries.list_recent_trades(pool, limit=20)
    return [dict(r) for r in rows]


@router.get("/trades/recent", response_model=list[RecentTradeOut])
async def recent_trades(request: Request, limit: int = 20):
    """Latest fills across every deployment, newest first — the
    Dashboard's "what just happened" activity feed."""
    if limit == 20:
        return await request.app.state.cache.get("trades_recent")
    pool = request.app.state.db_pool
    rows = await queries.list_recent_trades(pool, limit=limit)
    return [dict(r) for r in rows]
