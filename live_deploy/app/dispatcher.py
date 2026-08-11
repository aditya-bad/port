"""
LiveDataDispatcher — owns the ONE upstream Kite Connect WebSocket
(KiteTicker) connection for this whole service.

KiteTicker runs its connection in its own background thread when started
with connect(threaded=True) — its on_ticks/on_connect/on_close/on_error
callbacks all fire from THAT thread, not from FastAPI's asyncio event
loop. Three things cross that thread boundary:

  1. Kite thread -> asyncio loop (incoming ticks): _on_ticks() hands
     each batch to the broadcaster via asyncio.run_coroutine_threadsafe.
  2. asyncio loop -> Kite thread (outgoing subscribe/unsubscribe/set_mode
     control messages, e.g. when a new deployment needs a token that
     isn't already subscribed): add_instruments()/release_instruments()
     schedule the actual KiteTicker call via _schedule_on_ticker_thread.
  3. asyncio loop -> Kite thread (closing a connection, e.g. during a
     reconnect() hot-swap or final shutdown): also goes through
     _schedule_on_ticker_thread.

None of these are optional. kiteconnect's KiteTicker.subscribe()/
unsubscribe()/set_mode()/close() all write straight to the WebSocket
transport with no internal thread-safety of their own (confirmed by
reading kiteconnect's source — plain synchronous calls, not wrapped in
reactor.callFromThread). Calling any of them from the FastAPI/asyncio
thread while Kite's reactor thread is concurrently reading/writing the
same socket is a genuine race on the transport, not a style nitpick —
it just happens to not show up in a quick test because nothing is
fighting over the socket at that exact moment. Every one of them goes
through _schedule_on_ticker_thread (Twisted's reactor.callFromThread in
production) for this reason — the only exception is _on_connect calling
ws.subscribe()/set_mode() directly, which is safe because _on_connect
itself already runs ON the reactor thread.

Kite's access_token expires daily and needs re-issuing through a login
flow a human has to complete in a browser (see routers/kite_auth.py).
reconnect(access_token) hot-swaps the underlying KiteTicker with a fresh
token WITHOUT restarting the FastAPI process — the broadcaster, the
subscribed-token set (static + dynamic refcounts), and every downstream
consumer (WS clients, deployment runners) are completely undisturbed;
only the Kite connection itself is replaced. The dispatcher can also
start with NO token at all (first boot, before anyone has ever logged
in) — bind_loop() just records the event loop and leaves the service in
a "needs_login" state until reconnect() is called for the first time.

No matter how many downstream consumers subscribe to `broadcaster`, and
no matter how many times the token gets refreshed over the service's
life, exactly one KiteTicker instance is ever live at once.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from kiteconnect import KiteTicker

from .broadcaster import TickBroadcaster

logger = logging.getLogger("live_deploy.dispatcher")

MODE_MAP = {
    "ltp":   KiteTicker.MODE_LTP,
    "quote": KiteTicker.MODE_QUOTE,
    "full":  KiteTicker.MODE_FULL,
}


def _default_ticker_thread_scheduler() -> Callable:
    """
    Twisted's reactor.callFromThread is the only supported way to safely
    call into reactor-owned objects (which is what KiteTicker's
    WebSocket connection is) from a different thread. Imported lazily
    so this module doesn't hard-fail to import in a context where
    twisted isn't installed but a test is supplying its own scheduler
    anyway.
    """
    from twisted.internet import reactor
    return reactor.callFromThread


class LiveDataDispatcher:
    def __init__(
        self,
        api_key: str,
        tokens: list[dict],
        tick_mode: str,
        broadcaster: TickBroadcaster,
        initial_access_token: Optional[str] = None,
        kite_ticker_cls=KiteTicker,   # injectable for testing without real Kite
        schedule_on_ticker_thread: Optional[Callable] = None,   # injectable for tests
    ):
        self.broadcaster = broadcaster
        self.instrument_tokens = [t["instrument_token"] for t in tokens]
        self.token_labels = {
            t["instrument_token"]: t.get("symbol", str(t["instrument_token"]))
            for t in tokens
        }
        # Tokens loaded from tokens.json at startup are the permanent
        # baseline — editable only by hand-editing that file and
        # restarting, never auto-removed by a deployment stopping.
        # Everything added at runtime (via a deployment or the manual
        # /instruments API) is reference-counted in _dynamic_refcounts
        # and unsubscribed once nothing needs it anymore.
        self._static_tokens: set[int] = set(self.instrument_tokens)
        self._dynamic_refcounts: dict[int, int] = {}

        self.tick_mode = tick_mode
        self._kite_mode = MODE_MAP[tick_mode]

        self._api_key = api_key
        self._kite_ticker_cls = kite_ticker_cls
        self._schedule_on_ticker_thread = (
            schedule_on_ticker_thread or _default_ticker_thread_scheduler()
        )

        self._loop: asyncio.AbstractEventLoop | None = None
        self._kws = None   # no connection at all until bind_loop()/reconnect()

        self.connected = False
        self.ticks_received = 0
        self.last_tick_at: datetime | None = None
        self.reconnect_count = 0
        self.last_error: str | None = None

        # instrument_token -> most recent last_price seen. Lets deployment
        # position/report reads (and force-close-on-stop) mark positions
        # to market without a separate REST round-trip to Kite — the tick
        # stream already carries this.
        self.last_prices: dict[int, float] = {}

        self._pending_initial_token = initial_access_token

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Call exactly once, at startup, regardless of whether a Kite
        access_token is available yet. If one was passed as
        initial_access_token, connects immediately; otherwise the
        dispatcher just sits idle (status.needs_login == True) until
        reconnect() is called for the first time (from the /kite/callback
        login flow).
        """
        self._loop = loop
        if self._pending_initial_token:
            self._connect_with(self._pending_initial_token)
        self._pending_initial_token = None

    def reconnect(self, access_token: str) -> None:
        """
        Hot-swap the Kite connection with a fresh access_token — e.g.
        after the user completes the daily login flow — WITHOUT
        restarting the FastAPI process. Safe to call whether or not a
        connection currently exists (first-ever login, or a same-day
        re-login after a token got revoked).
        """
        old_kws = self._kws
        if old_kws is not None:
            try:
                self._schedule_on_ticker_thread(old_kws.close)
            except Exception:
                logger.exception("Error closing previous Kite WebSocket during reconnect")
        self.connected = False
        self._connect_with(access_token)
        logger.info("Reconnected to Kite with a fresh access_token")

    def stop(self) -> None:
        if self._kws is None:
            return
        try:
            self._schedule_on_ticker_thread(self._kws.close)
        except Exception:
            logger.exception("Error closing Kite WebSocket")

    def _connect_with(self, access_token: str) -> None:
        self._kws = self._kite_ticker_cls(self._api_key, access_token)
        self._kws.on_ticks = self._on_ticks
        self._kws.on_connect = self._on_connect
        self._kws.on_close = self._on_close
        self._kws.on_error = self._on_error
        self._kws.on_reconnect = self._on_reconnect
        self._kws.on_noreconnect = self._on_noreconnect
        self._kws.connect(threaded=True)

    # ── Dynamic subscription ────────────────────────────────────────────

    def add_instruments(self, tokens: list[dict]) -> list[int]:
        """
        Subscribe to additional instrument tokens on the ALREADY-LIVE
        Kite connection — no restart needed. `tokens` is the same shape
        as tokens.json: [{"symbol": ..., "instrument_token": ...}, ...].

        A token already in the static (tokens.json) set is a no-op. A
        token already added dynamically just has its refcount bumped
        (so two deployments both using NIFTY BANK doesn't unsubscribe it
        the moment ONE of them stops — see release_instruments). A
        genuinely new token gets subscribed live if connected, or is
        simply registered to go out with the next on_connect's full
        subscribe if we're not connected yet (e.g. still reconnecting,
        or no one has logged in yet at all).

        Returns the instrument_tokens that were newly subscribed on the
        wire this call (empty if every token was already covered).
        """
        newly_live: list[int] = []
        for t in tokens:
            token = t["instrument_token"]
            label = t.get("symbol", str(token))

            if token in self._static_tokens:
                continue   # tokens.json already covers this permanently

            was_new = self._dynamic_refcounts.get(token, 0) == 0
            self._dynamic_refcounts[token] = self._dynamic_refcounts.get(token, 0) + 1

            if was_new:
                if token not in self.instrument_tokens:
                    self.instrument_tokens.append(token)
                self.token_labels[token] = label
                newly_live.append(token)

        if newly_live and self.connected and self._kws is not None:
            self._schedule_on_ticker_thread(self._kws.subscribe, newly_live)
            self._schedule_on_ticker_thread(self._kws.set_mode, self._kite_mode, newly_live)
            logger.info(
                "Dynamically subscribed %d new token(s): %s",
                len(newly_live), [self.token_labels[t] for t in newly_live],
            )
        elif newly_live:
            # Not connected right now (no login yet, startup race, or
            # mid-reconnect) — the next on_connect subscribes the full
            # self.instrument_tokens list, which already includes these,
            # so nothing is lost.
            logger.info(
                "Registered %d new token(s), will subscribe once (re)connected: %s",
                len(newly_live), [self.token_labels[t] for t in newly_live],
            )
        return newly_live

    def release_instruments(self, tokens: list[dict] | list[int]) -> list[int]:
        """
        Drop one "claim" on each token (e.g. a deployment stopping).
        Only actually unsubscribes on the wire once a token's refcount
        reaches zero AND it isn't in the static tokens.json set — so two
        deployments sharing a token don't fight over it.

        Returns the instrument_tokens that were actually unsubscribed
        (refcount hit zero) this call.
        """
        token_ids = [t["instrument_token"] if isinstance(t, dict) else t for t in tokens]

        newly_removed: list[int] = []
        for token in token_ids:
            if token in self._static_tokens:
                continue
            if token not in self._dynamic_refcounts:
                continue
            self._dynamic_refcounts[token] -= 1
            if self._dynamic_refcounts[token] <= 0:
                del self._dynamic_refcounts[token]
                if token in self.instrument_tokens:
                    self.instrument_tokens.remove(token)
                newly_removed.append(token)

        if newly_removed and self.connected and self._kws is not None:
            self._schedule_on_ticker_thread(self._kws.unsubscribe, newly_removed)
            logger.info(
                "Dynamically unsubscribed %d token(s) (no longer needed): %s",
                len(newly_removed), newly_removed,
            )
        for token in newly_removed:
            self.token_labels.pop(token, None)
        return newly_removed

    # ── Kite callbacks — fire from KiteTicker's background thread ──────

    def _on_connect(self, ws, response):
        self.connected = True
        self.last_error = None
        logger.info(
            "Kite WebSocket connected — subscribing %d token(s): %s",
            len(self.instrument_tokens),
            [self.token_labels[t] for t in self.instrument_tokens],
        )
        # Called from the reactor thread already (this callback IS the
        # reactor thread), so calling subscribe()/set_mode() directly
        # here — unlike from add_instruments() — is safe, no marshaling
        # needed.
        if self.instrument_tokens:
            ws.subscribe(self.instrument_tokens)
            ws.set_mode(self._kite_mode, self.instrument_tokens)

    def _on_close(self, ws, code, reason):
        self.connected = False
        logger.warning("Kite WebSocket closed: %s %s", code, reason)

    def _on_error(self, ws, code, reason):
        self.last_error = f"{code}: {reason}"
        logger.error("Kite WebSocket error: %s %s", code, reason)

    def _on_reconnect(self, ws, attempts_count):
        self.reconnect_count += 1
        logger.warning("Kite WebSocket reconnecting (attempt %d)", attempts_count)

    def _on_noreconnect(self, ws):
        self.connected = False
        logger.error("Kite WebSocket gave up reconnecting")

    def _on_ticks(self, ws, ticks):
        """
        Called from the Kite background thread. This is the ONLY bridge
        point between that thread and the asyncio event loop the
        broadcaster (and every downstream consumer) runs on.
        """
        self.ticks_received += len(ticks)
        self.last_tick_at = datetime.now(timezone.utc)
        for t in ticks:
            token = t.get("instrument_token")
            price = t.get("last_price")
            if token is not None and price is not None:
                self.last_prices[token] = price
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.broadcaster.broadcast(ticks), self._loop
            )

    @property
    def status(self) -> dict:
        return {
            "kite_connected": self.connected,
            "needs_login": self._kws is None,
            "subscribed_tokens": [
                {
                    "instrument_token": t,
                    "symbol": self.token_labels[t],
                    "static": t in self._static_tokens,
                }
                for t in self.instrument_tokens
            ],
            "tick_mode": self.tick_mode,
            "ticks_received": self.ticks_received,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "downstream_subscribers": self.broadcaster.subscriber_count,
        }
