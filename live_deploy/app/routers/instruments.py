"""
live_deploy — manual instrument subscription control.

Deployments already trigger this automatically (see
DeploymentManager._start_runner / .stop), but this exists for direct
control too: e.g. subscribing to a token BEFORE deploying a strategy
that needs it, or just watching a token's ticks via /ws/ticks without
any deployment involved at all.
"""

import asyncio

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request

from ..options import NoKiteSession, OptionsResolver, get_kite_connect

router = APIRouter(prefix="/instruments", tags=["instruments"])


class InstrumentIn(BaseModel):
    instrument_token: int
    symbol: str | None = None


@router.get("")
async def list_instruments(request: Request):
    dispatcher = request.app.state.dispatcher
    return {
        "subscribed": dispatcher.status["subscribed_tokens"],
        "tick_mode": dispatcher.tick_mode,
    }


@router.get("/search")
async def search_instruments(q: str, request: Request):
    """
    Symbol/name substring search across NSE/NFO/BSE/BFO (see
    OptionsResolver.search_instruments's own docstring for the exact
    matching/caching rules) — this is what the Instrument Browser page
    calls as the user types, so a person can find an instrument_token by
    symbol instead of already having to know the raw number to POST here.

    Requires an active Kite session (the instrument master is fetched
    over Kite's own REST API) — a clear 400, not a 500, if no one has
    ever logged in yet, same failure shape strategies already get from
    NoKiteSession elsewhere in this codebase.
    """
    q = (q or "").strip()
    if len(q) < 2:
        raise HTTPException(400, "q must be at least 2 characters")
    resolver = OptionsResolver(request.app.state.dispatcher)
    try:
        results = await resolver.search_instruments(q)
    except NoKiteSession as e:
        raise HTTPException(400, str(e))
    return {"query": q, "results": results}


@router.get("/quotes")
async def get_quotes(tokens: str, request: Request):
    """
    One-shot REST snapshot (last_price + previous day's close) for the
    given instrument_tokens, straight from Kite's quote() endpoint —
    NOT a live feed. Exists for the ticker bar's fallback: outside
    market hours Kite simply sends no ticks at all over /ws/ticks (a
    live, correctly-connected Kite session with nothing to say, not a
    broken one) — a stale-but-honest last-known price beats an
    indefinite "connecting…" placeholder that would otherwise never
    resolve on its own. See resolver.py's get_ltp/get_quote for the
    same asyncio.to_thread(kite.quote, ...) pattern this reuses (the
    kiteconnect client is a synchronous/blocking HTTP client).

    `tokens` — comma-separated instrument_token integers, e.g.
    "256265,265,260105". Kite's quote() accepts raw instrument_tokens
    directly (not just exchange:tradingsymbol strings), so no exchange
    lookup is needed here.
    """
    try:
        token_list = [int(t) for t in tokens.split(",") if t.strip()]
    except ValueError:
        raise HTTPException(400, "tokens must be a comma-separated list of instrument_token integers")
    if not token_list:
        raise HTTPException(400, "tokens must not be empty")

    dispatcher = request.app.state.dispatcher
    try:
        kite = get_kite_connect(dispatcher)
    except NoKiteSession as e:
        raise HTTPException(400, str(e))

    raw = await asyncio.to_thread(kite.quote, token_list)
    out = {}
    for entry in raw.values():
        token = entry.get("instrument_token")
        if token is None:
            continue
        ohlc = entry.get("ohlc") or {}
        out[str(token)] = {
            "last_price": entry.get("last_price"),
            "prev_close": ohlc.get("close"),
        }
    return out


@router.post("")
async def add_instruments(payload: list[InstrumentIn], request: Request):
    """
    Subscribe to one or more tokens on the already-live Kite connection
    — no restart. Each is a manual "claim", reference-counted the same
    way a deployment's claim is; call DELETE to release it.
    """
    dispatcher = request.app.state.dispatcher
    tokens = [{"instrument_token": i.instrument_token, "symbol": i.symbol or str(i.instrument_token)}
              for i in payload]
    added = dispatcher.add_instruments(tokens)
    return {
        "newly_subscribed": added,
        "already_covered": [t["instrument_token"] for t in tokens if t["instrument_token"] not in added],
    }


@router.delete("/{instrument_token}")
async def remove_instrument(instrument_token: int, request: Request):
    """
    Release a manual claim on one token. It stays subscribed as long as
    ANY claim remains (another manual add, or a deployment still using
    it) — tokens.json's static set is never affected by this at all.

    One token per call, in the path — not a bulk DELETE-with-body, which
    several HTTP clients (including httpx's shorthand .delete()) don't
    support cleanly and which isn't universally proxy-safe either.
    """
    dispatcher = request.app.state.dispatcher
    removed = dispatcher.release_instruments([instrument_token])
    return {"unsubscribed": removed}
