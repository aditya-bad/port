"""
live_deploy — Web Push subscribe/unsubscribe + the public key the
frontend needs to create a subscription in the first place.

See app/notifications.py for the actual SEND side (called from
DeploymentRunner.notify_execution, not from here) and
custom_scripts/generate_vapid_keys.py for how the keypair this router
hands out gets created in the first place.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import queries
from ..notifications import is_push_configured

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/vapid-public-key")
async def vapid_public_key(request: Request):
    """The frontend calls this before ever calling PushManager.subscribe()
    — that call needs the public key as its applicationServerKey.
    public_key is null (not an error) when this deployment hasn't
    configured VAPID at all — the frontend treats that as "notifications
    unavailable here" and hides the Enable button rather than showing
    one that would fail the moment it's tapped."""
    config = request.app.state.kite_config
    if not is_push_configured(config):
        return {"public_key": None}
    return {"public_key": config["vapid_public_key"]}


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class SubscribeIn(BaseModel):
    endpoint: str
    keys: PushKeys


@router.post("/subscribe", status_code=204)
async def subscribe(payload: SubscribeIn, request: Request):
    """Called once, right after the browser's own PushManager.subscribe()
    resolves (see static/js/account.js) — persists the subscription so
    DeploymentRunner.notify_execution can reach this device from then
    on, including across server restarts (this is why it's a DB row,
    not in-memory state). Upserts by endpoint (see
    queries.save_push_subscription's own docstring) so re-subscribing
    the same device is a no-op, not a duplicate."""
    await queries.save_push_subscription(
        request.app.state.db_pool, payload.endpoint,
        payload.keys.p256dh, payload.keys.auth,
        user_agent=request.headers.get("user-agent"),
    )


class UnsubscribeIn(BaseModel):
    endpoint: str


@router.post("/unsubscribe", status_code=204)
async def unsubscribe(payload: UnsubscribeIn, request: Request):
    """Called when the user turns notifications off from Account (as
    opposed to the SILENT removal app/notifications.py does on its own
    when a push service reports a subscription as gone) — deletion is a
    no-op if the endpoint was already gone either way, never a 404."""
    await queries.delete_push_subscription(request.app.state.db_pool, payload.endpoint)
