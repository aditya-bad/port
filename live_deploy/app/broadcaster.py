"""
Broadcaster — pure async fan-out.

N downstream consumers, each with their own bounded asyncio.Queue, all
fed from a single upstream `broadcast()` call. No Kite/network
dependency at all, and no assumption about WHAT'S being broadcast —
this is what makes "one Kite connection, N downstream tick consumers"
possible (app.state.broadcaster), and it's the exact same mechanism
app.state.event_broadcaster reuses for deployment events (fills,
pause/resume/stop, strategy errors — see DeploymentManager/
DeploymentRunner's own _record_event()) rather than duplicating this
subscribe/unsubscribe/backpressure logic a second time for a second
kind of payload. Fully testable in isolation either way — nothing here
knows or cares whether a "payload" is a tick batch or a single event
dict, only that it's JSON-serializable.

Was named TickBroadcaster until deployment events needed the exact same
fan-out mechanism — renamed rather than copy-pasted a near-identical
EventBroadcaster class. Ticks and events still use SEPARATE instances
(app.state.broadcaster vs app.state.event_broadcaster) — DeploymentRunner
subscribes to the tick one specifically to feed its strategy ticks, so
mixing the two streams into one instance would hand strategies event
payloads they'd wrongly try to process as ticks.
"""

import asyncio


class Broadcaster:
    def __init__(self, max_queue_size: int = 1000):
        self._subscribers: set[asyncio.Queue] = set()
        self._max_queue_size = max_queue_size
        self._lock = asyncio.Lock()
        self.messages_broadcast = 0
        self.drops = 0   # payloads dropped because a subscriber's queue was full

    async def subscribe(self) -> asyncio.Queue:
        """Register a new downstream consumer. Returns its private queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def broadcast(self, payload) -> None:
        """Fan the same payload out to every current subscriber — a tick
        batch (list) for app.state.broadcaster, or a single event dict
        for app.state.event_broadcaster. Whatever it is, it's handed to
        each subscriber's queue unchanged and sent to the browser via
        websocket.send_json() as-is."""
        self.messages_broadcast += 1
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow-consumer protection: drop the oldest queued payload
                # to make room for the newest one, rather than blocking
                # this whole broadcast — and therefore every OTHER
                # subscriber — on one lagging client.
                self.drops += 1
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except asyncio.QueueEmpty:
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
