"""
live_deploy — REST KiteConnect client, reusing the dispatcher's session.

The dispatcher (app/dispatcher.py) owns the one Kite WebSocket connection
and — since the "public credentials" edit — publicly exposes api_key/
access_token from that SAME login. Options resolution needs Kite's REST
endpoints (instruments/quote/ltp), which is a completely separate
`KiteConnect` client class from `KiteTicker`, but there is no reason to
make the user log in twice: build the REST client from the identical
api_key + access_token the WebSocket is already using.

access_token is re-issued daily and can change mid-process via
dispatcher.reconnect() (see kite_auth.py's callback). Never cache a
KiteConnect keyed only by api_key — cache is keyed by (api_key,
access_token) so a token refresh transparently produces a fresh client
next call, with no explicit invalidation needed anywhere.
"""

from kiteconnect import KiteConnect

# Process-wide cache — a KiteConnect instance is just a thin HTTP client
# wrapper (no per-instance state worth isolating), so one shared instance
# per (api_key, access_token) is safe and avoids rebuilding it on every
# single resolver call.
_cache_key = None
_cache_client: KiteConnect | None = None


class NoKiteSession(Exception):
    """Raised when options utils are used before any Kite login has ever completed."""


def get_kite_connect(dispatcher) -> KiteConnect:
    """
    Return a KiteConnect REST client authenticated with the SAME session
    dispatcher's WebSocket is using. Raises NoKiteSession if no one has
    ever completed the /kite/login-url -> /kite/callback flow yet (or if
    the token was invalidated and reconnect() hasn't been called since).
    """
    global _cache_key, _cache_client

    if not dispatcher.access_token:
        raise NoKiteSession(
            "No active Kite session yet — complete the login flow "
            "(GET /kite/login-url) before using options utils."
        )

    key = (dispatcher.api_key, dispatcher.access_token)
    if key != _cache_key:
        kite = KiteConnect(api_key=dispatcher.api_key)
        kite.set_access_token(dispatcher.access_token)
        _cache_key = key
        _cache_client = kite
    return _cache_client
