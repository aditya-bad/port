"""
live_deploy — DeploymentRunner.

One instance per deployment. Subscribes to the SAME tick Broadcaster
every other consumer uses (no extra Kite connection), filters ticks down
to this deployment's own instrument_tokens, and — once a strategy is
attached — feeds it those ticks. The strategy calls back into buy()/
sell() to record paper fills; the runner is the only thing that talks to
the DB on a deployment's behalf, keeping persistence out of strategy code
entirely.

After every fill, the runner re-reads the affected position straight
from the DB rather than replicating record_fill's averaging/closing math
in memory — the DB is the single source of truth, and this is what makes
"resume next day from where it paused" trivially correct: whatever's in
Postgres when a runner starts up IS the current state, no replay needed.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import asyncpg

from ..db import queries
from .strategy_base import StrategyBase

logger = logging.getLogger("live_deploy.runner")


class DeploymentRunner:
    def __init__(
        self,
        deployment: asyncpg.Record,
        pool: asyncpg.Pool,
        broadcaster,
        dispatcher,
        strategy: Optional[StrategyBase] = None,
        on_fill: Optional[Callable[[], None]] = None,
        event_broadcaster=None,
    ):
        self.deployment_id = deployment["id"]
        self.deployment_name = deployment["deployment_name"]
        self.strategy_name = deployment["strategy_name"]
        self.mode = deployment["mode"]
        self.config: dict = deployment["config"]
        self.pool = pool
        self.broadcaster = broadcaster
        self.dispatcher = dispatcher
        self.strategy = strategy
        # Optional (app.state.event_broadcaster — see app/broadcaster.py
        # and _record_event below). None in any context that doesn't
        # have one (e.g. tests constructing a runner directly), same
        # optional-hook shape as on_fill/self.cache elsewhere in this
        # codebase — trading logic never depends on or blocks on it.
        self.event_broadcaster = event_broadcaster
        # Optional, fire-and-forget: DeploymentManager passes a callback
        # here that schedules a background aggregate-cache refresh (see
        # app/cache.py) after every fill. The runner itself stays
        # deliberately ignorant of what a "cache" even is — it just
        # calls this if given one, same shape as any other hook — so
        # trading logic never depends on or blocks on the HTTP layer.
        # Without this, a trade booked by the strategy (never going
        # through any HTTP endpoint) would only show up in the cached
        # GET /deployments list once the background loop next ticks,
        # while GET /deployments/{id} (not cached) would already show
        # it — a real, if brief, disagreement between the two views.
        self._on_fill = on_fill

        self.open_positions: dict[int, dict] = {}   # instrument_token -> position row (as dict)
        self.cash: float = float(deployment["current_cash"])
        # Fixed for the deployment's lifetime (unlike `cash`, which moves
        # with every fill) — for strategies that need a stable reference
        # value distinct from the compounding cash balance (e.g. sizing a
        # single unit's premium target against the ORIGINAL capital, then
        # scaling unit COUNT rather than per-unit size as cash compounds).
        self.initial_capital: float = float(deployment["initial_capital"])

        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def tokens(self) -> set[int]:
        return set(self.config.get("instrument_tokens", []))

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        await self._reload_positions()
        self._queue = await self.broadcaster.subscribe()
        self._running = True
        self._task = asyncio.create_task(self._run(), name=f"runner:{self.deployment_name}")
        if self.strategy is not None:
            await self.strategy.on_start(self)
        logger.info(
            "Runner started: %s (%s, %s) — %d open position(s), tokens=%s",
            self.deployment_name, self.strategy_name, self.mode,
            len(self.open_positions), sorted(self.tokens),
        )

    async def stop(self) -> None:
        self._running = False
        if self.strategy is not None:
            await self.strategy.on_stop(self)
            await self._dump_state()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._queue is not None:
            await self.broadcaster.unsubscribe(self._queue)
            self._queue = None
        logger.info("Runner stopped: %s", self.deployment_name)

    async def _dump_state(self) -> None:
        """Called from stop() (pause, stop, AND a graceful full-server
        shutdown all route through here — see StrategyBase.
        get_persistable_state's own docstring for exactly when/why).
        A strategy that doesn't override get_persistable_state gets None
        back and this is a no-op, same as always — existing strategies
        are entirely unaffected."""
        try:
            state = self.strategy.get_persistable_state()
        except Exception:
            logger.exception(
                "%s: get_persistable_state() raised — skipping this "
                "state dump (previous persisted state, if any, is left "
                "untouched)", self.deployment_name,
            )
            return
        if state is None:
            return
        await queries.save_deployment_state(self.pool, self.deployment_id, state)
        logger.info("%s: persisted strategy state (%d top-level key(s))",
                    self.deployment_name, len(state))

    async def load_state(self) -> Optional[dict]:
        """Whatever this deployment's strategy last persisted via
        get_persistable_state(), or None if it never has (fresh deploy,
        or a strategy that doesn't use this hook). Conventionally called
        at the top of on_start(), before applying any config-provided
        seed — see StrategyBase.get_persistable_state's docstring."""
        return await queries.load_deployment_state(self.pool, self.deployment_id)

    async def _reload_positions(self) -> None:
        rows = await queries.list_open_positions(self.pool, self.deployment_id)
        self.open_positions = {int(r["instrument_token"]): dict(r) for r in rows}

    async def _record_event(
        self, event_type: str, message: Optional[str] = None, metadata: Optional[dict] = None,
    ) -> None:
        """Records to deployment_events (unchanged persistence) AND, if
        an event_broadcaster was given, fans the same event out live to
        every connected /ws/events subscriber — the in-app real-time
        alert feature. The two call sites below (strategy_error,
        fill_buy/fill_sell) used to call queries.record_event directly;
        routed through here instead so a trade a strategy makes shows up
        as a toast in the browser the same instant it's recorded, not
        just the next time someone opens the Activity tab."""
        await queries.record_event(self.pool, self.deployment_id, event_type, message=message, metadata=metadata)
        if self.event_broadcaster is None:
            return
        await self.event_broadcaster.broadcast({
            "deployment_id": str(self.deployment_id),
            "deployment_name": self.deployment_name,
            "strategy_name": self.strategy_name,
            "event_type": event_type,
            "message": message,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    async def list_closed_positions(self) -> list[dict]:
        """
        Every CLOSED position ever recorded for this deployment, most-
        recently-closed first — for strategies whose resume-safety needs
        more than "what's currently open" (e.g. reconstructing today's
        already-realized P&L, or how many of a capped resource were
        already used, from legs that were opened AND closed earlier the
        same day, before a restart). `open_positions` alone can't answer
        that — a fully-closed leg isn't in it at all.

        Same sanctioned-access-point principle as buy()/sell(): a
        strategy calls this rather than touching `self.pool`/`queries`
        directly, keeping "strategies never touch the DB directly" true
        even for this read-only, resume-safety-only need.
        """
        rows = await queries.list_positions(self.pool, self.deployment_id, status="closed")
        return [dict(r) for r in rows]

    # ── Tick consumption ─────────────────────────────────────────────

    async def _run(self) -> None:
        my_tokens = self.tokens
        try:
            while True:
                ticks = await self._queue.get()
                relevant = [t for t in ticks if t.get("instrument_token") in my_tokens]
                if not relevant or self.strategy is None:
                    continue
                for t in relevant:
                    try:
                        await self.strategy.on_tick(self, t)
                    except Exception:
                        logger.exception(
                            "Strategy on_tick raised for deployment %s — continuing",
                            self.deployment_name,
                        )
                        await self._record_event(
                            "strategy_error",
                            message="on_tick raised an exception; see server logs",
                        )
        except asyncio.CancelledError:
            raise

    # ── Trade execution — the only DB writes a strategy triggers ──────

    async def buy(
        self, symbol: str, instrument_token: int, qty: float, price: float,
        executed_at: Optional[datetime] = None, reason: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        return await self._fill("buy", symbol, instrument_token, qty, price,
                                executed_at, reason, metadata)

    async def sell(
        self, symbol: str, instrument_token: int, qty: float, price: float,
        executed_at: Optional[datetime] = None, reason: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        return await self._fill("sell", symbol, instrument_token, qty, price,
                                executed_at, reason, metadata)

    async def _fill(
        self, action: str, symbol: str, instrument_token: int, qty: float,
        price: float, executed_at: Optional[datetime], reason: Optional[str],
        metadata: Optional[dict],
    ) -> dict:
        executed_at = executed_at or datetime.now(timezone.utc)
        result = await queries.record_fill(
            self.pool, self.deployment_id, symbol, instrument_token, action,
            qty, price, executed_at, reason=reason, metadata=metadata,
        )

        # Refresh from the DB rather than replicating averaging/closing
        # math here — see module docstring.
        row = await self.pool.fetchrow(
            "SELECT * FROM positions WHERE id = $1", result["position_id"],
        )
        if row["status"] == "open":
            self.open_positions[instrument_token] = dict(row)
        else:
            self.open_positions.pop(instrument_token, None)

        dep_row = await queries.get_deployment(self.pool, self.deployment_id)
        self.cash = float(dep_row["current_cash"])

        await self._record_event(
            f"fill_{action}",
            message=f"{action} {qty} {symbol} @ {price} ({reason or 'n/a'})",
            metadata={"position_id": str(result["position_id"]), **(metadata or {})},
        )
        logger.info(
            "%s: %s %s %s @ %s (%s)%s",
            self.deployment_name, action, qty, symbol, price, reason or "",
            f" realized_pnl={result['realized_pnl']:.2f}" if result["realized_pnl"] is not None else "",
        )
        if self._on_fill:
            self._on_fill()
        return result
