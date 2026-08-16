"""
live_deploy — application-level authentication.

Multiple named users can log in (see the `users` table, migration
0005) — but there's still no multi-tenancy: every authenticated user
sees the same shared set of deployments, and there's no RBAC yet (see
app/rbac.py's docstring for the deliberately-a-no-op extension point
that's meant to make adding RBAC later NOT a rearchitecture).
`app_auth_secret` in config.json is no longer the ongoing login
credential — it's a one-time bootstrap seed for the first user's
password (see main.py's startup) — but it's kept around afterward
because it's ALSO the session-cookie signing key and the X-API-Key
value for scripted/API access, neither of which the user table
replaces.

Two ways to authenticate, either is accepted:
  1. A session cookie, for the browser UI — obtained via POST
     /auth/login (now username + password, bcrypt-verified against the
     `users` table), verified/signed using Starlette's own
     SessionMiddleware (see HostAwareSessionMiddleware below). The
     session stores `user_id` + `username`, not just an `authed` flag,
     so a request can attribute itself to a real user (audit logging,
     "who am I").
  2. An `X-API-Key` header, for scripted/curl use — compared against
     `app_auth_secret` with secrets.compare_digest (constant-time, avoids
     a timing side-channel on the comparison itself). This path has no
     associated user (it authenticates the SCRIPT, not a person) — audit
     log rows from API-key requests have a null user_id/username.

Implemented as ASGI MIDDLEWARE, not per-route `Depends()`, on purpose:
middleware fails CLOSED (anything not on the two-item allowlist needs
auth, including any router added later and forgotten about) — a
Depends()-based check fails OPEN (unprotected until someone remembers to
add the dependency to the new router). "Protect everything by default"
only actually holds with the fail-closed shape.

Allowlist — exactly two paths, both for the same underlying reason
(neither one can carry OUR auth on the request that reaches it):
  - "/kite/callback": Kite's own servers redirect the user's browser
    here after a successful login. Kite doesn't and can't attach our
    session cookie or API key to that redirect. This is still safe
    without our auth layer: it only does anything with a request_token
    that Kite itself validates server-side during the token exchange —
    hitting this URL without a real token from an actual Kite login is
    a clean failure, not a way in.
  - "/auth/login": the login endpoint itself obviously can't require
    being already logged in to reach it.

Everything else — every router, `/ws/ticks`, and the UI at "/" — is
protected, INCLUDING /health (low-sensitivity today, but "protect
everything by default" means not carving out silent exceptions; revisit
explicitly if a future monitoring setup needs it, don't let it happen by
omission).
"""

import ipaddress
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Optional

from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("live_deploy.audit")

ALLOWLIST = frozenset({"/kite/callback", "/auth/login"})

# Hosts that count as "local dev", for the Secure cookie flag decision
# below — deliberately just hostnames, not a network/CIDR check: this
# is a convenience for running the service directly on your own machine
# during development, not a security boundary in itself (the actual
# boundary is the password/API key).
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Tailscale's own address ranges (CGNAT /10 for IPv4, its ULA /48 for
# IPv6 — same ranges for every tailnet, not account-specific) — a
# recommended deployment shape for this app (see RUN_GUIDE.md) is
# reachable ONLY over a tailnet address, over plain HTTP, because
# WireGuard already encrypts the whole hop; there's no TLS terminator
# in front to make a `Secure`-flagged cookie make sense. Without this,
# HostAwareSessionMiddleware fell through to the "assume a real public
# HTTPS deployment" branch for a tailnet IP, which sets `Secure` on the
# session cookie — the browser then silently refuses to send it back
# over plain HTTP, so login appears to succeed (200, server-side session
# set) but the very next request looks logged-out again. Found by
# exactly that symptom during a real Tailscale-only deployment.
_TAILSCALE_V4_RANGE = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_V6_RANGE = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def _is_tailscale_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False   # a real hostname, not a bare IP -- not our concern here
    return addr in _TAILSCALE_V4_RANGE or addr in _TAILSCALE_V6_RANGE


_FALLBACK_LOGIN_HTML = (
    "<!doctype html><html><body style=\"font-family:sans-serif;text-align:center;"
    "padding-top:3em;\"><h2>Login</h2><p>static/login.html is missing — "
    "restore it to get the real login form.</p></body></html>"
)


def _request_host(scope: Scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"host":
            return value.decode("latin-1").split(":")[0].strip().lower()
    return ""


class HostAwareSessionMiddleware:
    """
    Signs/verifies the session cookie via Starlette's own
    SessionMiddleware (no new package needed — itsdangerous is already
    a Starlette dependency) — but SessionMiddleware's `https_only` flag
    is fixed once at construction, and "Secure cookie when reached
    over anything other than localhost or a tailnet address" is
    inherently a PER-REQUEST decision (the same running process might
    be hit as http://localhost:8000 during dev, http://100.x.x.x:8000
    over Tailscale in production, or through a real reverse proxy
    terminating HTTPS in front of it). Delegates each request to one of
    two SessionMiddleware instances — both signing with the identical
    secret, so a cookie either one issues is valid to the other too —
    chosen by whether the request's Host header looks like localhost or
    a Tailscale address (both cases where a `Secure`-only cookie would
    just silently break the browser's ability to send it back, with no
    matching security benefit — see _is_tailscale_ip's own comment).
    """

    # This is now an IDLE timeout, not an absolute one -- see
    # AuthMiddleware._session_ok's own "touch" logic, which is what
    # actually makes that true. Confirmed directly from Starlette's own
    # source (not assumed): SessionMiddleware only re-signs and
    # re-issues Set-Cookie when `session.modified` is true for that
    # request (starlette/middleware/sessions.py) -- a plain read never
    # extends anything on its own. itsdangerous's TimestampSigner
    # embeds a fresh timestamp on every (re-)sign too, so a touched
    # request refreshes the check at BOTH layers: the browser-visible
    # Max-Age hint, and the server-side signature timestamp
    # unsign(..., max_age=...) actually verifies against -- not just a
    # client-trusted expiry. 2h: short enough that a lost/stolen,
    # unused device or an abandoned tab closes the exposure window
    # quickly, long enough not to interrupt anyone actively using it.
    SESSION_MAX_AGE_SECONDS = 2 * 60 * 60

    def __init__(self, app: ASGIApp, secret_key: str):
        self._secure = SessionMiddleware(
            app, secret_key=secret_key, https_only=True, max_age=self.SESSION_MAX_AGE_SECONDS,
        )
        self._insecure = SessionMiddleware(
            app, secret_key=secret_key, https_only=False, max_age=self.SESSION_MAX_AGE_SECONDS,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._insecure(scope, receive, send)
            return
        host = _request_host(scope)
        insecure_ok = host in _LOCALHOST_HOSTS or _is_tailscale_ip(host)
        target = self._insecure if insecure_ok else self._secure
        await target(scope, receive, send)


class AuthMiddleware:
    """
    The actual gate. Must sit INSIDE HostAwareSessionMiddleware in the
    stack (added to the app BEFORE it — Starlette's add_middleware()
    makes the most-recently-added one outermost) so `conn.session` is
    already decoded from the cookie by the time this runs.
    """

    def __init__(self, app: ASGIApp, secret: str, static_dir: Path):
        self.app = app
        self.secret = secret
        self.login_html_path = static_dir / "login.html"

    def _login_html(self) -> str:
        try:
            return self.login_html_path.read_text()
        except OSError:
            return _FALLBACK_LOGIN_HTML

    async def _session_ok(self, conn: HTTPConnection) -> bool:
        user_id = conn.session.get("user_id")
        if user_id is None:
            return False
        # Beyond "is there a user_id in here at all" (the only check
        # before this): the session's OWN embedded session_version,
        # from whenever it was issued, must still match this user's
        # CURRENT version — see migration 0006 + queries.
        # bump_session_version's own comments. A session issued before
        # this check existed at all carries no session_version, which
        # never matches (every real user's column defaults to 1, not
        # None) — so shipping this once, deliberately, invalidates
        # every previously-issued session and forces one fresh login;
        # after that, everything works as normal until the version is
        # next bumped.
        cache = getattr(getattr(conn.scope.get("app"), "state", None), "cache", None)
        if cache is None:
            # Only reachable before the ASGI lifespan startup event has
            # finished (see app/cache.py) -- shouldn't happen for a
            # real request at all, but degrade to the pre-revocation
            # check rather than locking out every user over it.
            return True
        versions = await cache.get("user_session_versions")
        if versions.get(user_id) != conn.session.get("session_version"):
            return False

        # The actual sliding-window mechanic: writing to the session
        # dict (any key, any value) is what makes Starlette's own
        # SessionMiddleware re-sign and re-issue Set-Cookie for this
        # response, which is what refreshes both the browser-visible
        # Max-Age AND the server-verified itsdangerous timestamp — see
        # SESSION_MAX_AGE_SECONDS's own comment on HostAwareSession
        # Middleware. Only reached once a session has already passed
        # the checks above, so this never touches (or creates) a
        # session for a request that isn't genuinely, currently valid.
        conn.session["last_seen"] = int(time.time())
        return True

    def _header_api_key_ok(self, conn: HTTPConnection) -> bool:
        supplied = conn.headers.get("x-api-key")
        return bool(supplied) and secrets.compare_digest(supplied, self.secret)

    def _query_api_key_ok(self, conn: HTTPConnection) -> bool:
        # WebSocket-only, and only as a fallback — see module docstring
        # and README: a plain browser WebSocket can't set custom headers
        # at all, and some minimal script WS clients can't either, so a
        # query param is the one deliberate exception to "never put the
        # key in a URL." Never accepted for plain HTTP requests, where
        # the header is always available and a query param would risk
        # ending up in a reverse proxy's access log.
        supplied = conn.query_params.get("api_key")
        return bool(supplied) and secrets.compare_digest(supplied, self.secret)

    async def _is_authorized(self, conn: HTTPConnection) -> bool:
        if await self._session_ok(conn) or self._header_api_key_ok(conn):
            return True
        if conn.scope["type"] == "websocket" and self._query_api_key_ok(conn):
            return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if scope["path"] in ALLOWLIST:
            await self.app(scope, receive, send)
            return

        conn = HTTPConnection(scope, receive)
        if await self._is_authorized(conn):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # 4401 is in the private-use range (4000-4999) — there's no
            # standard WS close code for "unauthorized".
            await send({"type": "websocket.close", "code": 4401})
            return

        if scope["path"] == "/":
            # Serve the login page's own content directly, in place of
            # the real UI — NOT a redirect to a separate "/login.html"
            # URL, which would itself need to be on the allowlist
            # (keeping the allowlist at exactly the two paths above).
            response = HTMLResponse(self._login_html(), status_code=401)
        else:
            response = JSONResponse(
                {"detail": "Unauthorized — provide a valid session cookie or X-API-Key header"},
                status_code=401,
            )
        await response(scope, receive, send)


# Body keys never written to the audit log verbatim — checked
# case-insensitively at every nesting level (redaction walks the whole
# JSON tree, not just the top level, since e.g. bulk/nested payloads
# could carry a secret a level down and this costs nothing to get
# right). Extend this set rather than special-casing an endpoint if a
# new sensitive field shows up later.
_AUDIT_REDACT_KEYS = frozenset({
    "password", "new_password", "old_password", "current_password",
    "app_auth_secret", "access_token", "api_secret", "request_token", "api_key",
})


def _redact(value):
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if k.lower() in _AUDIT_REDACT_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class AuditLogMiddleware:
    """
    Writes one `audit_log` row (see migration 0005) per state-changing
    (POST/PUT/PATCH/DELETE) HTTP request that reaches this ASGI app —
    implemented as middleware, not a per-router dependency, for the same
    fail-closed reasoning AuthMiddleware itself uses: a new router added
    later is audited automatically, without anyone remembering to wire
    it in.

    Placement in the stack matters and is deliberate (see main.py):
    this sits BETWEEN HostAwareSessionMiddleware (outermost) and
    AuthMiddleware (innermost) —
      - Outside AuthMiddleware, so the row still gets written for a
        request AuthMiddleware itself rejects with 401 — a rejected
        state-changing attempt is exactly the kind of thing an audit
        log exists to catch, not something to silently skip.
      - Inside HostAwareSessionMiddleware, so `scope["session"]` is
        already the decoded session dict by the time this runs, letting
        a request attribute itself to a real user without a second
        cookie-decode.

    Reads the request body to log it (redacted — see _redact above) by
    buffering every ASGI `http.request` message up front and replaying
    them verbatim to the real `receive` the downstream app gets, so the
    actual route handler still sees a normal, once-only-readable body —
    it never knows this middleware looked at it first.
    """

    _AUDITED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in self._AUDITED_METHODS:
            await self.app(scope, receive, send)
            return

        # Buffer the full body up front (these are small JSON request
        # bodies, never a large upload) and hand the downstream app a
        # replay of the exact same messages — it can't tell the
        # difference from reading `receive` directly.
        messages = []
        more_body = True
        while more_body:
            message = await receive()
            messages.append(message)
            more_body = message.get("more_body", False) if message["type"] == "http.request" else False

        body_bytes = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.request")

        idx = 0

        async def replay_receive():
            nonlocal idx
            if idx < len(messages):
                message = messages[idx]
                idx += 1
                return message
            return await receive()

        status_holder: dict = {}

        async def capturing_send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        # Captured before the downstream app runs, as a fallback for
        # e.g. logout (which clears the session during the request —
        # without this we'd lose who logged out) — the post-request read
        # below wins whenever it has a user, which is what makes login
        # itself show the newly-authenticated username in its own row.
        pre_session = scope.get("session") or {}
        pre_user_id, pre_username = pre_session.get("user_id"), pre_session.get("username")

        try:
            await self.app(scope, replay_receive, capturing_send)
        finally:
            post_session = scope.get("session") or {}
            user_id = post_session.get("user_id") or pre_user_id
            username = post_session.get("username") or pre_username

            request_body = None
            if body_bytes:
                try:
                    request_body = _redact(json.loads(body_bytes))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    request_body = {"_unparsed": True}

            client = scope.get("client")
            remote_addr = client[0] if client else None

            pool = getattr(getattr(scope.get("app"), "state", None), "db_pool", None)
            if pool is not None:
                try:
                    from .db import queries  # local import: avoids a hard import-time
                                              # dependency from this module on the DB layer
                    await queries.record_audit_log(
                        pool, user_id, username, scope["method"], scope["path"],
                        status_holder.get("status"), request_body, remote_addr,
                    )
                except Exception:
                    # Audit logging must never be the reason a real
                    # request fails — log and move on.
                    logger.exception("Failed to write audit_log row for %s %s",
                                      scope["method"], scope["path"])
