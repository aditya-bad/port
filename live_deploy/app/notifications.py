"""
live_deploy — Web Push (mobile notifications).

Sends a real OS-level push notification to every phone/browser that
opted in via Account -> Notifications' "Enable notifications" button —
delivered by the browser's own push service (FCM under the hood on
Android Chrome) straight to the device's service worker, working even
when the tab is closed and Chrome isn't running in the foreground (see
static/sw.js's own push event handler for the receiving side).

Entirely OPTIONAL: if no VAPID keypair is configured (see
custom_scripts/generate_vapid_keys.py + app/config.py's own VAPID
handling), every function here is a silent no-op — the rest of the app
runs completely normally, this is a feature toggle, not a credential
it refuses to start without.

WHO calls this: DeploymentRunner.notify_execution — see that method's
own docstring — the ONLY place that triggers a mobile push (entry/exit
executions only, never per-fill, never for pause/resume/stop/errors),
plus the /notifications router for the subscribe/unsubscribe endpoints
themselves.
"""

import logging
from typing import Optional

import asyncpg

from .db import queries

logger = logging.getLogger("live_deploy.notifications")


def is_push_configured(config: dict) -> bool:
    return bool(config.get("vapid_public_key") and config.get("vapid_private_key")
                and config.get("vapid_subject"))


async def send_push_for_all(pool: asyncpg.Pool, config: dict, title: str, body: str) -> None:
    """Send one push notification to EVERY subscribed device — used by
    DeploymentRunner.notify_execution (there's no per-user targeting in
    this single-operator app; every subscribed device gets every
    notification its deployment-level notifications_enabled check let
    through). A subscription a push service reports as gone (410 Gone —
    uninstalled the PWA, cleared site data, revoked permission) is
    deleted here so it isn't silently retried forever; any OTHER
    per-device failure (a transient network error, a malformed
    subscription) is logged and skipped, never allowed to stop the rest
    of the batch or propagate up into the strategy code that triggered
    this — a push notification failing must never affect trading."""
    if not is_push_configured(config):
        return

    # Imported here, not at module level: pywebpush pulls in
    # `cryptography` + `http_ece`, both binary/compiled dependencies —
    # deferring the import means a deployment that never configures
    # VAPID keys at all (is_push_configured() already returned above)
    # never pays that import cost, and a broken/missing install of this
    # optional dependency can't break the rest of the app at startup.
    from pywebpush import WebPushException, webpush

    subscriptions = await queries.list_push_subscriptions(pool)
    if not subscriptions:
        return

    vapid_claims = {"sub": config["vapid_subject"]}
    payload = _build_payload(title, body)

    sent = 0
    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=config["vapid_private_key"],
                vapid_claims=dict(vapid_claims),   # webpush mutates the dict it's given (adds "aud") -- a fresh copy per subscriber, never share one across the loop
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                logger.info(
                    "Push subscription gone (HTTP %s) — removing endpoint %s...",
                    status, sub["endpoint"][:60],
                )
                await queries.delete_push_subscription(pool, sub["endpoint"])
            else:
                logger.warning("Push send failed (HTTP %s) for endpoint %s...: %s",
                               status, sub["endpoint"][:60], e)
        except Exception:
            logger.exception("Push send raised unexpectedly for endpoint %s...", sub["endpoint"][:60])

    logger.info("Push notification sent to %d/%d subscribed device(s): %r",
               sent, len(subscriptions), title)


def _build_payload(title: str, body: str) -> str:
    import json
    # Shape matches exactly what static/sw.js's own push event handler
    # expects (see that file) -- keep the two in sync if this changes.
    return json.dumps({"title": title, "body": body})
