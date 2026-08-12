"""
live_deploy — DeploymentRunner.

One instance per deployment. Subscribes to the SAME TickBroadcaster
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
from typing import Optional

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

        self.open_positions: dict[int, dict] = {}   # instrument_token -> position row (as dict)
        self.cash: float = float(deployment["current_cash"])

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

    async def _reload_positions(self) -> None:
        rows = await queries.list_open_positions(self.pool, self.deployment_id)
        self.open_positions = {int(r["instrument_token"]): dict(r) for r in rows}

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
                        await queries.record_event(
                            self.pool, self.deployment_id, "strategy_error",
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

        await queries.record_event(
            self.pool, self.deployment_id, f"fill_{action}",
            message=f"{action} {qty} {symbol} @ {price} ({reason or 'n/a'})",
            metadata={"position_id": str(result["position_id"]), **(metadata or {})},
        )
        logger.info(
            "%s: %s %s %s @ %s (%s)%s",
            self.deployment_name, action, qty, symbol, price, reason or "",
            f" realized_pnl={result['realized_pnl']:.2f}" if result["realized_pnl"] is not None else "",
        )
        return result
