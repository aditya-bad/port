"""
live_deploy — Kite Connect login/callback flow ("re-upload it on next day").

Kite's access_token expires daily and can only be issued through a login
flow a human completes in a browser — there's no way to script around
that part, Kite requires the actual account holder's credentials/2FA.
What THIS router automates is everything around that unavoidable human
step:

  1. GET /kite/login-url  — the frontend sends the user here (a popup).
  2. The user logs in on Kite's own site.
  3. Kite redirects the browser to whatever URL is registered as this
     app's redirect URL in the Kite Developer Console
     (https://developers.kite.trade/apps) — that URL MUST point at
     GET /kite/callback on this service. This is a one-time manual setup
     step in Kite's console; it can't be done from here.
  4. /kite/callback exchanges the request_token for a fresh access_token,
     persists it to the kite_sessions table, and hot-swaps the live
     dispatcher's connection via LiveDataDispatcher.reconnect() — no
     process restart needed. The popup then closes itself; the main UI
     tab picks up the new "connected" status on its next /health poll.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from kiteconnect import KiteConnect

from ..db import queries

logger = logging.getLogger("live_deploy.kite_auth")

router = APIRouter(prefix="/kite", tags=["kite"])


def _coerce_login_time(value) -> Optional[datetime]:
    """
    kiteconnect's own generate_session() parses login_time into a
    datetime ONLY when the string is exactly 19 characters
    ("YYYY-MM-DD HH:MM:SS") — anything else (a different format, a
    library version that stops doing this, the field missing) passes
    the raw string straight through. Rather than trust that upstream
    parsing always happened, coerce defensively here too: accept an
    already-a-datetime value as-is, try to parse a string ourselves,
    and fall back to None (not a crash) if it's something unexpected —
    the access_token itself is what actually matters for reconnecting;
    losing login_time just means the DB row's login_time is null.
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            logger.warning("Could not parse login_time %r — storing as null", value)
            return None
    logger.warning("Unexpected login_time type %s — storing as null", type(value))
    return None

_SUCCESS_PAGE = """<!doctype html><html><body style="font-family:sans-serif;
text-align:center;padding-top:3em;">
<h2>✓ Kite login successful</h2><p>You can close this window.</p>
<script>setTimeout(() => window.close(), 1200)</script>
</body></html>"""

_FAILURE_PAGE = """<!doctype html><html><body style="font-family:sans-serif;
text-align:center;padding-top:3em;">
<h2>✗ Kite login failed</h2><p>{reason}</p><p>You can close this window and try again.</p>
</body></html>"""


@router.get("/login-url")
async def login_url(request: Request):
    """The frontend opens this in a popup to start the daily login flow."""
    config = request.app.state.kite_config
    kite = KiteConnect(api_key=config["api_key"])
    return {"login_url": kite.login_url()}


@router.get("/callback")
async def callback(request: Request, request_token: str | None = None, status: str | None = None):
    """
    Kite redirects the browser HERE after login — this must be the exact
    URL registered as this app's redirect URL in the Kite Developer
    Console. Returns an HTML page (not JSON) since the browser lands
    here directly, not via a fetch() call.
    """
    if status != "success" or not request_token:
        logger.error("Kite login callback did not succeed: status=%r", status)
        return HTMLResponse(
            _FAILURE_PAGE.format(reason=f"status={status!r}"), status_code=400,
        )

    config = request.app.state.kite_config
    kite = KiteConnect(api_key=config["api_key"])
    try:
        session = kite.generate_session(request_token, api_secret=config["api_secret"])
    except Exception as e:
        logger.exception("Kite generate_session failed")
        return HTMLResponse(_FAILURE_PAGE.format(reason=str(e)), status_code=400)

    access_token = session["access_token"]
    login_time = _coerce_login_time(session.get("login_time"))

    await queries.set_kite_session(request.app.state.db_pool, access_token, login_time)

    dispatcher = request.app.state.dispatcher
    dispatcher.reconnect(access_token)

    logger.info("Kite login successful — dispatcher reconnected with fresh token")
    return HTMLResponse(_SUCCESS_PAGE)


@router.get("/status")
async def kite_status(request: Request):
    """Convenience — same info as the kite_* fields in /health, scoped here too."""
    dispatcher = request.app.state.dispatcher
    return {
        "kite_connected": dispatcher.connected,
        "needs_login": dispatcher.status["needs_login"],
        "last_error": dispatcher.last_error,
    }
