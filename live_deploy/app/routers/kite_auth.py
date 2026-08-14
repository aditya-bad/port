"""
live_deploy — Kite Connect login/callback flow ("re-upload it on next day").

Kite's access_token expires daily and can only be issued through a login
flow a human completes in a browser — there's no way to script around
that part, Kite requires the actual account holder's credentials/2FA.
What THIS router automates is everything around that unavoidable human
step, via TWO alternative paths that both end at the same place
(a fresh access_token persisted + the live dispatcher hot-swapped):

  REDIRECT FLOW (the default, unchanged):
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

  MANUAL-ENTRY FLOW (POST /kite/manual-login, an ALTERNATIVE, not a
  replacement — see the module docstring's own header on this file
  history): for someone who already completed Kite's own login in a
  separate tab/window and has the `request_token` from the resulting
  redirect URL in hand, without wanting to go through this app's own
  popup again (or because the popup genuinely can't reach this service
  from wherever they're logging in from). Reuses the EXACT SAME
  session-generation logic as step 4 above (see `_complete_kite_login`)
  — the only difference is JSON in/out instead of a query-string
  GET + HTML page, since this is called via fetch() from inside the
  already-authenticated SPA rather than a raw browser redirect Kite
  itself controls.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from kiteconnect import KiteConnect
from pydantic import BaseModel

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


async def _complete_kite_login(
    request: Request, request_token: str, api_key: str, api_secret: str,
) -> str:
    """
    The one place that actually exchanges a request_token for a fresh
    access_token and wires it into the running service — shared,
    verbatim, by BOTH /kite/callback (redirect flow) and
    /kite/manual-login (manual-entry flow) below, so there is exactly
    one implementation of "what a successful Kite login does to this
    process's state" rather than two that could drift apart.

    `api_key`/`api_secret` are passed in explicitly rather than read
    from `request.app.state.kite_config` internally — the manual-entry
    caller may be using a ONE-OFF override instead of the app's
    configured credentials (see /kite/manual-login's own docstring), and
    this function has no business knowing which case it's in. Whatever
    is passed here is used for exactly this one `generate_session` call
    and nothing is written back anywhere by this function — the caller
    decides separately whether what it passed in came from config or a
    request body, and only the latter is who needs to be careful about
    NOT persisting it (see /kite/manual-login).

    Raises whatever `generate_session` raises on failure — callers
    translate that into their own response shape (an HTML failure page
    for the redirect flow, an HTTPException for the JSON flow).
    """
    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token, api_secret=api_secret)

    access_token = session["access_token"]
    login_time = _coerce_login_time(session.get("login_time"))

    await queries.set_kite_session(request.app.state.db_pool, access_token, login_time)

    dispatcher = request.app.state.dispatcher
    dispatcher.reconnect(access_token)

    return access_token


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
    try:
        await _complete_kite_login(request, request_token, config["api_key"], config["api_secret"])
    except Exception as e:
        logger.exception("Kite generate_session failed")
        return HTMLResponse(_FAILURE_PAGE.format(reason=str(e)), status_code=400)

    logger.info("Kite login successful (redirect flow) — dispatcher reconnected with fresh token")
    return HTMLResponse(_SUCCESS_PAGE)


class ManualLoginIn(BaseModel):
    request_token: str
    # Optional ONE-OFF override — see this endpoint's own docstring
    # below and the module docstring's "MANUAL-ENTRY FLOW" section for
    # exactly what happens (and does NOT happen) with these if given.
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


@router.post("/manual-login")
async def manual_login(payload: ManualLoginIn, request: Request):
    """
    Alternative to the popup/redirect flow above, for someone who
    already has a `request_token` in hand (completed Kite's login in a
    separate tab, copied `request_token` out of the resulting redirect
    URL's query string themselves). Called via fetch() from the SPA —
    JSON in, JSON out, matching how every other write in this UI already
    works (deploy, subscribe/unsubscribe, ...), unlike /kite/callback's
    HTML response, which exists for a raw browser redirect Kite itself
    controls, not a fetch() caller.

    api_key/api_secret are OPTIONAL here:
      - Omitted (the common case): reuses this app's already-configured
        credentials (`request.app.state.kite_config`) — the form only
        needs request_token.
      - Provided: used for THIS ONE `generate_session` call only, and
        NEVER written to config.json or anywhere else on disk, never
        logged, never stored in the DB, never assigned onto
        `request.app.state.kite_config` (which would leak them into
        every SUBSEQUENT request, including the ordinary redirect flow's
        own login-url/callback, defeating the whole point of "just this
        one exchange"). They exist only as this request's own local
        Pydantic model + the local variables below, both released the
        moment this function returns. The resulting access_token is
        persisted to the DB exactly as it already is for every other
        login — that part is unchanged and was already correct; it is
        SPECIFICALLY the typed-in api_key/api_secret that must never
        outlive this one request.
    """
    if bool(payload.api_key) != bool(payload.api_secret):
        raise HTTPException(400, "Provide both api_key and api_secret, or neither.")

    config = request.app.state.kite_config
    api_key = payload.api_key or config["api_key"]
    api_secret = payload.api_secret or config["api_secret"]

    try:
        await _complete_kite_login(request, payload.request_token, api_key, api_secret)
    except Exception as e:
        # Deliberately str(e) only, same as /kite/callback's own failure
        # path — kiteconnect's own exceptions describe what Kite
        # rejected (bad/expired request_token, checksum mismatch, ...),
        # they don't echo back api_secret, and nothing here adds it to
        # the message either.
        logger.exception("Kite generate_session failed (manual-entry flow)")
        raise HTTPException(400, str(e))

    logger.info(
        "Kite login successful (manual-entry flow, %s credentials) — "
        "dispatcher reconnected with fresh token",
        "overridden" if payload.api_key else "configured",
    )
    dispatcher = request.app.state.dispatcher
    return {
        "kite_connected": dispatcher.connected,
        "needs_login": dispatcher.status["needs_login"],
        "last_error": dispatcher.last_error,
    }


@router.get("/status")
async def kite_status(request: Request):
    """Convenience — same info as the kite_* fields in /health, scoped here too."""
    dispatcher = request.app.state.dispatcher
    return {
        "kite_connected": dispatcher.connected,
        "needs_login": dispatcher.status["needs_login"],
        "last_error": dispatcher.last_error,
    }
