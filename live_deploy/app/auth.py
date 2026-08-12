"""
live_deploy — application-level authentication.

This is a single-user personal tool, not a multi-tenant service: ONE
shared secret (`app_auth_secret` in config.json) is both the login
password and the cookie-signing key. No user table, no per-user
anything — deliberately as simple as the threat model allows.

Two ways to authenticate, either is accepted:
  1. A session cookie, for the browser UI — obtained via POST
     /auth/login, verified/signed using Starlette's own SessionMiddleware
     (see HostAwareSessionMiddleware below).
  2. An `X-API-Key` header, for scripted/curl use — compared against
     `app_auth_secret` with secrets.compare_digest (constant-time, avoids
     a timing side-channel on the comparison itself).

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

import secrets
from pathlib import Path
from typing import Optional

from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ALLOWLIST = frozenset({"/kite/callback", "/auth/login"})

# Hosts that count as "local dev", for the Secure cookie flag decision
# below — deliberately just hostnames, not a network/CIDR check: this
# is a convenience for running the service directly on your own machine
# during development, not a security boundary in itself (the actual
# boundary is the password/API key).
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

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
    over anything other than localhost" is inherently a PER-REQUEST
    decision (the same running process might be hit as both
    http://localhost:8000 during dev and through a real reverse proxy
    in front of it). Delegates each request to one of two
    SessionMiddleware instances — both signing with the identical
    secret, so a cookie either one issues is valid to the other too —
    chosen by whether the request's Host header looks like localhost.
    """

    def __init__(self, app: ASGIApp, secret_key: str):
        self._secure = SessionMiddleware(app, secret_key=secret_key, https_only=True)
        self._insecure = SessionMiddleware(app, secret_key=secret_key, https_only=False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._insecure(scope, receive, send)
            return
        target = self._insecure if _request_host(scope) in _LOCALHOST_HOSTS else self._secure
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

    def _session_ok(self, conn: HTTPConnection) -> bool:
        return conn.session.get("authed") is True

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

    def _is_authorized(self, conn: HTTPConnection) -> bool:
        if self._session_ok(conn) or self._header_api_key_ok(conn):
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
        if self._is_authorized(conn):
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
