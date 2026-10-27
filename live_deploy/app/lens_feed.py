"""
LinckLens's own Kite websocket: live prices for the shares you hold and
for NIFTY 50, on a connection of its own (Kite allows three per app), so
nothing LinckLens subscribes to or drops ever touches the trading feed.

It is a second LiveDataDispatcher (app/dispatcher.py) in quote mode: the
same thread handling, reconnects and subscribe bookkeeping as the
trading feed, with its own broadcaster. It logs in with the trading
feed's session: whenever that session's token changes (the daily Kite
login), this connection is redone with it.

Each tick keeps the price now and the previous close (the tick's
ohlc.close in quote mode). Browsers get them on /sse/lens at most once a
second, only what changed: {"at": iso, "p": {symbol: [price, prev close]}}.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from .broadcaster import Broadcaster
from .dispatcher import LiveDataDispatcher

logger = logging.getLogger("live_deploy.lens_feed")

PUSH_EVERY_S = 1.0
FOLLOW_EVERY_S = 5.0


class LensFeed:
    def __init__(self, main, *, ticker_cls=None, schedule_on_ticker_thread: Optional[Callable] = None,
                 push_every: float = PUSH_EVERY_S, follow_every: float = FOLLOW_EVERY_S):
        self.main = main                       # the trading feed: its api_key and session
        self.raw = Broadcaster()               # tick batches from Kite
        self.out = Broadcaster()               # price changes for browsers (/sse/lens)
        kw = {"kite_ticker_cls": ticker_cls} if ticker_cls is not None else {}
        self.ws = LiveDataDispatcher(api_key=main.api_key, tokens=[], tick_mode="quote", broadcaster=self.raw,
                                     schedule_on_ticker_thread=schedule_on_ticker_thread, **kw)
        self.prices: dict[int, dict] = {}      # token -> {"ltp", "close", "at"}
        self.symbols: dict[int, str] = {}      # what's subscribed: token -> symbol
        self.token: Optional[str] = None       # the session this connection was opened with
        self.push_every, self.follow_every = push_every, follow_every
        self._changed: set[int] = set()
        self._tasks: list[asyncio.Task] = []

    # ── what to follow ─────────────────────────────────────────────────

    def want(self, symbols: dict[int, str]) -> None:
        """Subscribe to exactly these {token: symbol}: new ones are added,
        ones no longer held are dropped. Connects on the first wish if the
        trading feed has a session."""
        new = {int(t): s for t, s in symbols.items() if t}
        add = [{"instrument_token": t, "symbol": s} for t, s in new.items() if t not in self.symbols]
        drop = [t for t in self.symbols if t not in new]
        self.symbols = new
        if drop:
            self.ws.release_instruments(drop)
            for t in drop:
                self.prices.pop(t, None)
        if add:
            self.ws.add_instruments(add)
        self._follow_session()

    def _follow_session(self) -> None:
        """Open (or redo) the connection with the trading feed's session
        when there's something to follow and that session is new."""
        tok = getattr(self.main, "access_token", None)
        if not self.symbols or not tok or tok == self.token:
            return
        self.token = tok
        try:
            self.ws.reconnect(tok)
        except Exception:
            logger.exception("LinckLens feed: connecting failed — retried with the next session check")
            self.token = None

    # ── running ────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self.ws.bind_loop(loop)
        self._tasks = [loop.create_task(self._take_ticks(), name="lens-feed-ticks"),
                       loop.create_task(self._push(), name="lens-feed-push"),
                       loop.create_task(self._follow(), name="lens-feed-session")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []

    def take(self, ticks: list[dict]) -> None:
        """One batch of ticks: the price now, and the previous close."""
        now = datetime.now(timezone.utc).isoformat()
        for t in ticks or []:
            tok = t.get("instrument_token")
            if tok not in self.symbols or t.get("last_price") is None:
                continue
            prev = (t.get("ohlc") or {}).get("close")
            old = self.prices.get(tok) or {}
            cur = {"ltp": float(t["last_price"]), "close": float(prev) if prev else old.get("close"), "at": now}
            if cur["ltp"] != old.get("ltp") or cur["close"] != old.get("close"):
                self._changed.add(tok)
            self.prices[tok] = cur

    async def _take_ticks(self) -> None:
        q = await self.raw.subscribe()
        try:
            while True:
                self.take(await q.get())
        finally:
            await self.raw.unsubscribe(q)

    def changes(self) -> Optional[dict]:
        """What changed since the last call, by symbol, or None."""
        if not self._changed:
            return None
        p = {self.symbols[t]: [self.prices[t]["ltp"], self.prices[t].get("close")]
             for t in self._changed if t in self.symbols and t in self.prices}
        self._changed.clear()
        return {"at": datetime.now(timezone.utc).isoformat(), "p": p} if p else None

    async def _push(self) -> None:
        while True:
            await asyncio.sleep(self.push_every)
            msg = self.changes()
            if msg:
                await self.out.broadcast(msg)

    async def _follow(self) -> None:
        while True:
            self._follow_session()
            await asyncio.sleep(self.follow_every)

    # ── reading ────────────────────────────────────────────────────────

    def price(self, token) -> Optional[dict]:
        return self.prices.get(int(token)) if token else None

    def snapshot(self) -> dict:
        """Every followed symbol's price now and previous close."""
        return {"at": datetime.now(timezone.utc).isoformat(),
                "p": {self.symbols[t]: [v["ltp"], v.get("close")] for t, v in self.prices.items() if t in self.symbols}}

    @property
    def status(self) -> dict:
        return {"connected": self.ws.connected, "following": len(self.symbols), "priced": len(self.prices),
                "last_tick_at": self.ws.last_tick_at.isoformat() if self.ws.last_tick_at else None,
                "last_error": self.ws.last_error}
