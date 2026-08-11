"""
LiveDataDispatcher — owns the ONE upstream Kite Connect WebSocket
(KiteTicker) connection for this whole service.

KiteTicker runs its connection in its own background thread when started
with connect(threaded=True) — its on_ticks/on_connect/on_close/on_error
callbacks all fire from THAT thread, not from FastAPI's asyncio event
loop. The only job of this class beyond owning the connection is
bridging that thread-based callback into the event loop thread-safely
(via asyncio.run_coroutine_threadsafe), so downstream consumers — which
are all asyncio-based (WebSocket clients, and later in-process
strategies) — see a normal async broadcast() call, never a raw thread
handoff.

No matter how many downstream consumers subscribe to `broadcaster`,
exactly one KiteTicker instance, and therefore one Kite WebSocket
session, exists for the lifetime of this service.
"""

import asyncio
import logging
from datetime import datetime, timezone

from kiteconnect import KiteTicker

from .broadcaster import TickBroadcaster

logger = logging.getLogger("live_deploy.dispatcher")

MODE_MAP = {
    "ltp":   KiteTicker.MODE_LTP,
    "quote": KiteTicker.MODE_QUOTE,
    "full":  KiteTicker.MODE_FULL,
}


class LiveDataDispatcher:
    def __init__(
        self,
        api_key: str,
        access_token: str,
        tokens: list[dict],
        tick_mode: str,
        broadcaster: TickBroadcaster,
        kite_ticker_cls=KiteTicker,   # injectable for testing without real Kite
    ):
        self.broadcaster = broadcaster
        self.instrument_tokens = [t["instrument_token"] for t in tokens]
        self.token_labels = {
            t["instrument_token"]: t.get("symbol", str(t["instrument_token"]))
            for t in tokens
        }
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

    # ── Kite callbacks — fire from KiteTicker's background thread ──────

    def _on_connect(self, ws, response):
        self.connected = True
        logger.info(
            "Kite WebSocket connected — subscribing %d token(s): %s",
            len(self.instrument_tokens),
            [self.token_labels[t] for t in self.instrument_tokens],
        )
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
                {"instrument_token": t, "symbol": self.token_labels[t]}
                for t in self.instrument_tokens
            ],
            "tick_mode": self.tick_mode,
            "ticks_received": self.ticks_received,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "downstream_subscribers": self.broadcaster.subscriber_count,
        }
