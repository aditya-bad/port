"""
live_deploy — manual instrument subscription control.

Deployments already trigger this automatically (see
DeploymentManager._start_runner / .stop), but this exists for direct
control too: e.g. subscribing to a token BEFORE deploying a strategy
that needs it, or just watching a token's ticks via /ws/ticks without
any deployment involved at all.
"""

from pydantic import BaseModel
from fastapi import APIRouter, Request

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
