"""live_deploy — /candles: price history from the one candle store
(app/candles.py), for any chart that needs it."""

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..candles import IST, INTERVAL_MINUTES, SESSION_CLOSE, SESSION_OPEN
from ..db import queries

router = APIRouter(prefix="/candles", tags=["candles"])


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


@router.get("/{instrument_token}")
async def get_candles(instrument_token: int, start: datetime, end: datetime, request: Request,
                      interval: str = "minute", fetch: bool = True):
    """Candles for [start, end) at `interval` (minute, 3/5/10/15/30/60minute,
    day), rolled up from stored 1-minute rows; missing minutes are fetched
    from Kite first while the instrument is listed (fetch=false: stored
    only). Naive times are IST."""
    if interval not in INTERVAL_MINUTES and interval != "day":
        raise HTTPException(400, f"interval must be one of {sorted(INTERVAL_MINUTES) + ['day']}")
    start, end = _aware(start), _aware(end)
    if end <= start or end - start > timedelta(days=400):
        raise HTTPException(400, "end must be after start, at most 400 days apart")
    contract = await queries.get_contract(request.app.state.db_pool, instrument_token)
    candles, source, error = await request.app.state.candles.series(
        instrument_token, start, end, interval, fetch=fetch,
        symbol=contract["tradingsymbol"] if contract else None,
    )
    return {"instrument_token": instrument_token, "interval": interval, "source": source, "error": error,
            "contract": dict(contract) if contract else None, "candles": candles}


@router.get("")
async def coverage(request: Request, day: Optional[date] = None):
    """What's stored for a trading day: each instrument held that day (and
    each other instrument with candles) with its minutes and sources."""
    day = day or datetime.now(IST).date()
    start, end = datetime.combine(day, SESSION_OPEN, IST), datetime.combine(day, SESSION_CLOSE, IST)
    pool = request.app.state.db_pool
    held = {int(r["instrument_token"]): r["symbol"] for r in await queries.instruments_held_between(pool, start, end)}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.instrument_token, count(*) AS minutes, array_agg(DISTINCT c.source) AS sources,
                   k.tradingsymbol
            FROM candles_1m c LEFT JOIN contracts k ON k.instrument_token = c.instrument_token
            WHERE c.minute >= $1 AND c.minute < $2
            GROUP BY c.instrument_token, k.tradingsymbol
            """,
            start, end,
        )
    stored = {int(r["instrument_token"]): r for r in rows}
    out = []
    for tok in sorted(set(held) | set(stored)):
        r = stored.get(tok)
        out.append({"instrument_token": tok, "symbol": held.get(tok) or (r["tradingsymbol"] if r else None),
                    "held": tok in held, "minutes": r["minutes"] if r else 0,
                    "sources": sorted(r["sources"]) if r else []})
    return {"day": day.isoformat(), "session_minutes": 375, "instruments": out}


class BackfillIn(BaseModel):
    day: Optional[date] = None


@router.post("/backfill")
async def backfill(payload: BackfillIn, request: Request):
    """Fill a day's missing minutes from Kite for everything held that
    day plus the indices — what runs by itself after every close."""
    day = payload.day or datetime.now(IST).date()
    manager = request.app.state.deployment_manager
    static = list(request.app.state.dispatcher._static_tokens) if hasattr(request.app.state.dispatcher, "_static_tokens") else []
    return await request.app.state.candles.backfill_day(day, extra_tokens=tuple(manager.watched_tokens(static)))
