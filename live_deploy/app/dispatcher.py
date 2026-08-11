"""
LiveDataDispatcher — owns the ONE upstream Kite Connect WebSocket
(KiteTicker) connection for this whole service.

KiteTicker runs its connection in its own background thread when started
with connect(threaded=True) — its on_ticks/on_connect/on_close/on_error
callbacks all fire from THAT thread, not from FastAPI's asyncio event
loop. Two bridges cross that thread boundary, in opposite directions:

  1. Kite thread -> asyncio loop (incoming ticks): _on_ticks() hands
     each batch to the broadcaster via asyncio.run_coroutine_threadsafe.
  2. asyncio loop -> Kite thread (outgoing subscribe/unsubscribe/set_mode
     control messages, e.g. when a new deployment needs a token that
     isn't already subscribed): add_instruments()/release_instruments()
     schedule the actual KiteTicker call onto Kite's own thread via
     Twisted's reactor.callFromThread.

Bridge #2 is NOT optional. kiteconnect's KiteTicker.subscribe()/
unsubscribe()/set_mode() call self.ws.sendMessage(...) directly, with no
internal thread-safety of their own (confirmed by reading kiteconnect's
source — they're plain synchronous calls, not wrapped in
reactor.callFromThread). Calling them straight from the FastAPI/asyncio
thread while Kite's reactor thread is concurrently reading/writing the
same socket is a genuine race on the underlying transport, not a style
nitpick — it just happens to not show up in a quick test because nothing
is fighting over the socket at that exact moment. Every dynamic
subscribe/unsubscribe in this file goes through
_schedule_on_ticker_thread for this reason.

No matter how many downstream consumers subscribe to `broadcaster`,
exactly one KiteTicker instance, and therefore one Kite WebSocket
session, exists for the lifetime of this service — dynamic
subscribe/unsubscribe changes what THAT one connection is subscribed
to; it never opens a second one.
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
        access_token: str,
        tokens: list[dict],
        tick_mode: str,
        broadcaster: TickBroadcaster,
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

        self._loop: asyncio.AbstractEventLoop | None = None
        self._kws = kite_ticker_cls(api_key, access_token)
        self._kws.on_ticks = self._on_ticks
        self._kws.on_connect = self._on_connect
        self._kws.on_close = self._on_close
        self._kws.on_error = self._on_error
        self._kws.on_reconnect = self._on_reconnect
        self._kws.on_noreconnect = self._on_noreconnect

        self._schedule_on_ticker_thread = (
            schedule_on_ticker_thread or _default_ticker_thread_scheduler()
        )

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

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the single upstream Kite connection in its own thread."""
        self._loop = loop
        self._kws.connect(threaded=True)

    def stop(self) -> None:
        try:
            self._kws.close()
        except Exception:
            logger.exception("Error closing Kite WebSocket")

    # ── Dynamic subscription — the actual point of this file ───────────

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
        subscribe if we're not connected yet (e.g. still reconnecting).

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

        if newly_live and self.connected:
            self._schedule_on_ticker_thread(self._kws.subscribe, newly_live)
            self._schedule_on_ticker_thread(self._kws.set_mode, self._kite_mode, newly_live)
            logger.info(
                "Dynamically subscribed %d new token(s): %s",
                len(newly_live), [self.token_labels[t] for t in newly_live],
            )
        elif newly_live:
            # Not connected right now (startup race, or mid-reconnect) —
            # the next on_connect subscribes the full self.instrument_tokens
            # list, which already includes these, so nothing is lost.
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

        if newly_removed and self.connected:
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
        logger.info(
            "Kite WebSocket connected — subscribing %d token(s): %s",
            len(self.instrument_tokens),
            [self.token_labels[t] for t in self.instrument_tokens],
        )
        # Called from the reactor thread already (this callback IS the
        # reactor thread), so calling subscribe()/set_mode() directly
        # here — unlike from add_instruments() — is safe, no marshaling
        # needed.
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
