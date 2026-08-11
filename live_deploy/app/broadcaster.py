"""
TickBroadcaster — pure async fan-out.

N downstream consumers, each with their own bounded asyncio.Queue, all
fed from a single upstream `broadcast()` call. No Kite/network
dependency at all — this is what makes "one Kite connection, N
downstream consumers" possible, and it's fully testable in isolation
from the real Kite WebSocket.
"""

import asyncio


class TickBroadcaster:
    def __init__(self, max_queue_size: int = 1000):
        self._subscribers: set[asyncio.Queue] = set()
        self._max_queue_size = max_queue_size
        self._lock = asyncio.Lock()
        self.ticks_broadcast = 0
        self.drops = 0   # batches dropped because a subscriber's queue was full

    async def subscribe(self) -> asyncio.Queue:
        """Register a new downstream consumer. Returns its private queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def broadcast(self, ticks: list) -> None:
        """Fan the same tick batch out to every current subscriber."""
        self.ticks_broadcast += 1
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(ticks)
            except asyncio.QueueFull:
                # Slow-consumer protection: drop the oldest queued batch to
                # make room for the newest one, rather than blocking this
                # whole broadcast — and therefore every OTHER subscriber —
                # on one lagging client.
                self.drops += 1
                try:
                    q.get_nowait()
                    q.put_nowait(ticks)
                except asyncio.QueueEmpty:
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
