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

from .broadcaster import Broadcaster

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
        broadcaster: Broadcaster,
        initial_access_token: Optional[str] = None,
        kite_ticker_cls=KiteTicker,   # injectable for testing without real Kite
        schedule_on_ticker_thread: Optional[Callable] = None,   # injectable for tests
        on_connection_issue: Optional[Callable] = None,
    ):
        self.broadcaster = broadcaster
        # Optional async callback: on_connection_issue(event_type, message),
        # called ONLY for the two states worth waking a human up for --
        # "kite_disconnected" (kiteconnect's own reconnect loop gave up
        # entirely -- see _on_noreconnect) and "kite_reconnected" (came
        # back afterward -- see _on_connect). Deliberately NOT fired for
        # every _on_close/_on_reconnect blip: those are kiteconnect's own
        # normal, usually-self-healing backoff cycle, and a human getting
        # paged for each one would train them to ignore the channel
        # entirely by the time a real, sustained outage happens. Wired in
        # main.py to both an in-app toast (via event_broadcaster, same
        # pipe deployment fills already use) and a mobile push (see
        # app/notifications.py) -- "my real money is sitting in a
        # deployment nothing is watching right now" is exactly the kind
        # of thing that should reach a phone, not just a sidebar badge
        # someone has to be looking at.
        self._on_connection_issue = on_connection_issue
        self._was_ever_broken = False   # has _on_noreconnect fired at least once THIS process?
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

        # Public — so other code (e.g. app/options/'s REST client) can
        # build its own KiteConnect using the SAME session this
        # dispatcher's WebSocket is authenticated with, instead of
        # needing a separate login/credential path. access_token is
        # None until the first successful bind_loop()/reconnect() —
        # always read it fresh rather than caching it elsewhere, since
        # it changes on every daily re-login.
        self.api_key = api_key
        self.access_token: str | None = None

        self._kite_ticker_cls = kite_ticker_cls
        self._schedule_on_ticker_thread = (
            schedule_on_ticker_thread or _default_ticker_thread_scheduler()
        )

        self._loop: asyncio.AbstractEventLoop | None = None
        self._kws = None   # no connection at all until bind_loop()/reconnect()
        # Separate from `self._kws is None` on purpose -- see reconnect()'s
        # own docstring for why. Once Twisted's reactor thread has been
        # bootstrapped by a real connect() call, it keeps running for the
        # rest of this process's life even through a give-up (see
        # _on_noreconnect), so `_kws` going back to None on give-up must
        # NOT make a later reconnect() think it's talking to a
        # not-yet-started reactor again -- this flag is the actual
        # "has the reactor thread ever been started" answer, and it never
        # resets back to False once set.
        self._reactor_started = False

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

        THE BUG THIS FIXES: `_connect_with` -> `KiteTicker.connect()`
        calls Twisted's `connectWS()` directly, on whatever thread calls
        it. For the very FIRST connection this process ever makes
        (`old_kws is None`, e.g. from bind_loop() at startup), that's
        fine — Twisted's reactor isn't running yet, so nothing else is
        touching it concurrently; connect() is what bootstraps the
        reactor's own background thread. But on every call AFTER that,
        the reactor is already alive and running on that background
        thread from the PREVIOUS connection — calling connectWS() again
        directly from THIS (the FastAPI/asyncio) thread races the
        reactor thread over Twisted's own internal state, exactly the
        class of bug this module's own docstring already warns about
        for subscribe/unsubscribe/close (every one of which correctly
        goes through _schedule_on_ticker_thread) — this call was simply
        missed. The race doesn't reliably crash; it just as often leaves
        the new connection silently stuck mid-handshake forever
        (`on_connect` never fires, `self.connected` never flips back to
        True) — matching exactly the reported symptom: a re-login (or
        any login after the first) leaves the banner stuck on
        "disconnected" indefinitely, only ever cleared by a full process
        restart (a fresh, not-yet-running reactor).

        Fix: for every reconnect except the very first, do the
        close-old-then-connect-new sequence as ONE callback scheduled
        onto the reactor thread via _schedule_on_ticker_thread, instead
        of calling connect() directly from here. Bundled as one callback
        (not two separately-scheduled ones) so close and connect can't
        be reordered or race each other either.
        """
        old_kws = self._kws
        self.connected = False

        if not self._reactor_started:
            # First-ever connection this process -- the reactor hasn't
            # started yet, so there's no concurrent thread to race with.
            # Mirrors bind_loop()'s own direct call for this exact case.
            # Deliberately NOT `old_kws is None` (see `_reactor_started`'s
            # own comment in __init__) -- _kws can go back to None later,
            # after a give-up (_on_noreconnect), with the reactor thread
            # from that earlier connection still very much alive; using
            # `old_kws is None` here would then skip straight back to this
            # branch's direct, unmarshaled connect() call and reintroduce
            # the exact race this whole method exists to prevent.
            self._connect_with(access_token)
        else:
            def _do_reconnect():
                if old_kws is not None:
                    try:
                        old_kws.close()
                    except Exception:
                        logger.exception("Error closing previous Kite WebSocket during reconnect")
                self._connect_with(access_token)

            try:
                self._schedule_on_ticker_thread(_do_reconnect)
            except Exception:
                logger.exception("Error scheduling Kite reconnect on ticker thread")

        logger.info("Reconnected to Kite with a fresh access_token")

    def stop(self) -> None:
        if self._kws is None:
            return
        try:
            self._schedule_on_ticker_thread(self._kws.close)
        except Exception:
            logger.exception("Error closing Kite WebSocket")

    def _connect_with(self, access_token: str) -> None:
        self.access_token = access_token
        self._reactor_started = True
        self._kws = self._kite_ticker_cls(self.api_key, access_token)
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
        # Only worth telling a human "it's back" if it was ever ACTUALLY
        # broken (_on_noreconnect fired) -- otherwise every normal boot's
        # first-ever connect would fire a "Kite reconnected!" alert for
        # nothing having gone wrong at all.
        if self._was_ever_broken:
            self._was_ever_broken = False
            self._fire_connection_issue(
                "kite_reconnected",
                "Kite WebSocket reconnected — live tick/order monitoring has resumed.",
            )

    def _on_close(self, ws, code, reason):
        self.connected = False
        logger.warning("Kite WebSocket closed: %s %s", code, reason)

    def _on_error(self, ws, code, reason):
        self.last_error = f"{code}: {reason}"
        logger.error("Kite WebSocket error: %s %s", code, reason)

    def _on_reconnect(self, ws, attempts_count):
        self.reconnect_count += 1
        logger.warning("Kite WebSocket reconnecting (attempt %d)", attempts_count)

    # Cool-down before an automatic post-give-up retry attempt -- see
    # _on_noreconnect. Deliberately modest, not aggressive: kiteconnect's
    # own internal reconnect loop (reconnect_max_tries=50,
    # reconnect_max_delay=60s, exponential up to that cap) already spends
    # a long time backing off on its own before ever reaching give-up, so
    # this doesn't need to retry fast -- CONFIRMED IN PRODUCTION that a
    # give-up can resolve on its own with the SAME access_token still
    # valid the whole time (an access_token expiring was the first
    # hypothesis here and turned out to be wrong that time -- the token
    # was issued that morning and never refreshed, yet it worked again
    # hours later with no new login) -- so a real Kite-side transient
    # outage, not just a stale token, is a genuine, confirmed case this
    # needs to recover from on its own.
    _AUTO_RETRY_AFTER_GIVEUP_DELAY = 60   # seconds

    def _on_noreconnect(self, ws):
        self.connected = False
        # `status.needs_login` is computed as `self._kws is None` (see
        # that property below) -- leaving _kws pointing at this now-dead
        # ticker object meant the UI kept showing "Disconnected —
        # reconnecting…" (static/index.html's own needs_login ? ... : ...
        # banner text) forever, when the true state needed EITHER a fresh
        # login (if the token really had expired) OR just time (if this
        # was a transient Kite-side outage -- confirmed to happen, see
        # _AUTO_RETRY_AFTER_GIVEUP_DELAY's own comment). Either way,
        # "quietly still retrying with no visible state change" was never
        # the honest answer.
        self._kws = None
        self._was_ever_broken = True
        logger.error(
            "Kite WebSocket gave up reconnecting -- scheduling an automatic "
            "retry in %ds (will keep retrying on this same cycle until it "
            "succeeds or a fresh login provides a new access_token)",
            self._AUTO_RETRY_AFTER_GIVEUP_DELAY,
        )
        self._fire_connection_issue(
            "kite_disconnected",
            "Kite WebSocket gave up reconnecting — no live ticks are reaching any "
            "deployment right now. Retrying automatically in the background; "
            "log in again if this doesn't clear on its own.",
        )
        # Fires on kiteconnect's own reactor thread, same as every other
        # callback here -- marshal onto the asyncio loop to sleep and
        # retry, same bridge _on_ticks already uses in the other
        # direction (asyncio.run_coroutine_threadsafe).
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._auto_retry_after_giveup(), self._loop)

    def _fire_connection_issue(self, event_type: str, message: str) -> None:
        """Marshal the optional on_connection_issue callback onto the
        asyncio loop -- both call sites here (_on_noreconnect, _on_connect)
        fire from kiteconnect's own reactor thread, and the callback is a
        coroutine (see its own comment in __init__: it awaits a DB write
        via event_broadcaster and a webpush call)."""
        if self._on_connection_issue is None:
            return
        if self._loop is None or not self._loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._on_connection_issue(event_type, message), self._loop)

    async def _auto_retry_after_giveup(self) -> None:
        """
        Self-healing follow-up to a give-up kiteconnect's own reconnect
        loop can't come back from on its own (it's a one-shot: 50 tries,
        then done, forever, until something outside it calls connect()
        again). Without this, "Kite WebSocket gave up reconnecting" meant
        the dispatcher just sat there until a HUMAN noticed (via the
        banner fix above) and either re-logged in or restarted the
        process -- for a transient Kite-side outage that would have
        cleared on its own, that's a needless outage stretched out by
        however long it took someone to notice.

        Retries with the SAME access_token that was last used (self.
        access_token, set by _connect_with on every successful connect
        attempt) -- confirmed safe to reuse: see this method's caller's
        own comment on a real give-up that self-resolved with no new
        login. If the token genuinely has expired, this retry (and every
        one after it, since a renewed give-up re-schedules another one
        of these) just keeps failing harmlessly with 403 until a human
        completes a fresh login, which calls reconnect() with a NEW
        token directly -- this loop doesn't fight that, it only acts
        while _kws is still None (i.e. nothing else has already
        reconnected first).
        """
        await asyncio.sleep(self._AUTO_RETRY_AFTER_GIVEUP_DELAY)
        if self._kws is not None:
            return   # something else (a manual re-login) already beat us to it
        if not self.access_token:
            logger.warning(
                "Kite auto-retry: no access_token has ever been set yet -- "
                "waiting for a first login instead of guessing"
            )
            return
        logger.info("Kite auto-retry: attempting reconnect with the last known access_token")
        self.reconnect(self.access_token)

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
