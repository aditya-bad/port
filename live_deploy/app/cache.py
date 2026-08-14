"""
live_deploy — a small in-memory cache for the aggregate read endpoints
(GET /deployments, /positions, /trades/recent, /strategies) reported as
taking 3-6 seconds to load on every single page view. The queries
themselves aren't the bottleneck — it's a flat per-round-trip cost
against Neon that swamps every request about equally no matter how many
queries it runs (this was already established while investigating
whether to parallelize /deployments' own queries: deployments and
positions took the same ~3s despite very different query counts). A
cache with a background refresh loop moves that round trip off the
request's critical path entirely: a background task refreshes each
entry on a fixed interval, and every GET just reads memory — instant,
regardless of what Neon's latency happens to be that moment. As a side
effect, the periodic loop also keeps a connection to Neon's serverless
compute warm, which is plausibly *why* requests were slow in the first
place (a suspended/cold compute waking up on the next query) — worth
watching for once this is live.

Five fixed keys, not a general-purpose cache. Each is registered once
at startup with its own async fetch function and refresh interval, then
refreshed on a loop for the life of the process. `refresh_now()` lets a
mutating endpoint (create/pause/resume/stop/clear-all a deployment,
enable/disable a strategy) force an immediate refresh right after its
own write completes, so the very next GET already reflects the change
instead of waiting out the interval — this matters because the
frontend re-fetches right after every action it takes (e.g. Deployments.
pause() calls this.load() immediately after the POST resolves) and a
stale cache there would otherwise make the UI look like the action
silently failed. The periodic loop is still necessary on its own even
without any mutation at all — unrealized P&L drifts continuously with
live prices, not through any of these endpoints — refresh_now() is
purely for snappier feedback on an action just taken, not a
replacement for the loop.

A failed refresh logs and keeps serving the last good value rather than
raising — a single slow/dropped Neon round trip should degrade to "data
is a few seconds stale," never to a 500 on every page load.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("live_deploy.cache")


@dataclass
class _Entry:
    fetch: Callable[[], Awaitable[Any]]
    interval: float
    value: Any = None
    updated_at: float = 0.0
    populated: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class AggregateCache:
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._tasks: list[asyncio.Task] = []

    def register(self, key: str, fetch: Callable[[], Awaitable[Any]], interval: float) -> None:
        """Call during startup, before start() — every key the app will
        ever `.get()` must be registered first."""
        self._entries[key] = _Entry(fetch=fetch, interval=interval)

    async def _refresh(self, key: str) -> None:
        entry = self._entries[key]
        # Only one refresh in flight per key at a time — refresh_now()
        # (mutation-triggered) and the periodic loop can otherwise land
        # on the same key back to back; the lock just makes the second
        # one wait and reuse the first one's result rather than firing
        # a redundant Neon round trip.
        async with entry.lock:
            try:
                value = await entry.fetch()
            except Exception:
                logger.exception(
                    "cache refresh failed for %r -- serving last known value (age=%.1fs)",
                    key, self.age(key) or -1.0,
                )
                return
            entry.value = value
            entry.updated_at = time.monotonic()
            entry.populated = True

    async def get(self, key: str) -> Any:
        entry = self._entries[key]
        if not entry.populated:
            # Cold start only: nobody's refreshed this key yet (start()
            # normally does this before the app accepts traffic) -- do
            # it inline once rather than returning nothing.
            await self._refresh(key)
        return entry.value

    def age(self, key: str) -> float | None:
        entry = self._entries[key]
        return (time.monotonic() - entry.updated_at) if entry.populated else None

    async def refresh_now(self, key: str) -> None:
        await self._refresh(key)

    async def _loop(self, key: str) -> None:
        entry = self._entries[key]
        while True:
            await asyncio.sleep(entry.interval)
            await self._refresh(key)

    async def start(self) -> None:
        """Populate every registered key once (so the first real
        request never pays a cold-cache penalty), then start each on
        its own periodic background loop. Call once at app startup,
        after every register() call."""
        await asyncio.gather(*(self._refresh(key) for key in self._entries))
        for key in self._entries:
            self._tasks.append(asyncio.create_task(self._loop(key), name=f"cache-refresh-{key}"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
