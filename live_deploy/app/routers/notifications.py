"""
live_deploy — Web Push subscribe/unsubscribe + the public key the
frontend needs to create a subscription in the first place.

See app/notifications.py for the actual SEND side (called from
DeploymentRunner.notify_execution, not from here) and
custom_scripts/generate_vapid_keys.py for how the keypair this router
hands out gets created in the first place.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db import queries
from ..notifications import is_push_configured

router = APIRouter(prefix="/notifications", tags=["notifications"])

IST = ZoneInfo("Asia/Kolkata")


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


# ── "Mark as holiday" — mute Kite-connection alerts ─────────────────
# (kite_disconnected/kite_reconnected specifically, see
# app/main.py's _on_kite_connection_issue, which is the only place that
# actually checks this). A Kite outage on a day the market's shut (or
# any day the owner just doesn't want to be paged) would otherwise mean
# the every-60s reminder (see LiveDataDispatcher._disconnect_reminder_loop)
# firing all day for nothing actionable.

@router.get("/mute-status")
async def mute_status(request: Request):
    until = await request.app.state.cache.get("notification_mute_until")
    return {
        "muted": until is not None and datetime.now(timezone.utc) < until,
        "muted_until": until.isoformat() if until else None,
    }


@router.post("/mute-today")
async def mute_today(request: Request):
    """Suppresses every Kite-connection alert for the rest of today AND
    resumes automatically at 7:00 IST TOMORROW — not just "the next 24h"
    (pressing this at 8am would otherwise leave a gap from 8am-ish
    tomorrow with no coverage at all) and not just "until midnight"
    (would resume hours before market open, right back to buzzing about
    pre-open reconnect noise nobody's awake for anyway)."""
    now_ist = datetime.now(IST)
    tomorrow = now_ist.date() + timedelta(days=1)
    resume_at = datetime.combine(tomorrow, time(7, 0), tzinfo=IST).astimezone(timezone.utc)
    await queries.set_notification_mute_until(request.app.state.db_pool, resume_at)
    await request.app.state.cache.refresh_now("notification_mute_until")
    return {"muted": True, "muted_until": resume_at.isoformat()}


@router.post("/unmute")
async def unmute(request: Request):
    """Early opt back in — e.g. "Mark as holiday" was pressed by
    mistake, or Kite's actually back and worth knowing about again
    before 7am tomorrow after all."""
    await queries.set_notification_mute_until(request.app.state.db_pool, None)
    await request.app.state.cache.refresh_now("notification_mute_until")
    return {"muted": False, "muted_until": None}
