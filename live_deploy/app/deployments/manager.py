"""
live_deploy — DeploymentManager.

Owns the lifecycle of every DeploymentRunner in the process:
  - On FastAPI startup: reload every deployment with status='active'
    from the DB and start a runner for each — this IS "resume next day
    from where the strategy paused." Positions/cash come straight from
    Postgres; nothing needs to be replayed, because every fill was
    already durably persisted before the previous process exited (or
    was killed, or the server was turned off overnight).
  - create / pause / resume / stop — the only ways a deployment's
    status changes, always through here so runners and DB status never
    drift apart.
  - On FastAPI shutdown: stop every runner task cleanly, but DO NOT
    change any deployment's DB status — 'active' stays 'active', so the
    next startup resumes it automatically without anyone touching the
    API.
  - Dynamic subscription: whenever a runner starts (fresh create,
    resume, or startup reload), its config's instrument_tokens are
    registered with the dispatcher via add_instruments() — subscribing
    on the ALREADY-LIVE Kite connection if a token isn't already
    covered, no restart needed. When a deployment stops for good,
    release_instruments() drops its claim on those tokens (a
    reference count under the hood — a token used by two deployments
    stays subscribed as long as either one is still active/paused).
    Pausing does NOT release tokens — it's meant to be a lightweight,
    reversible halt, not a full teardown.

Multiple deployments of the SAME strategy_name are just multiple rows
with different deployment_name/config — the manager holds one
independent DeploymentRunner (own queue, own task, own in-memory
position cache) per deployment_id, so they never share state, never
overlap.
"""

import logging
from typing import Optional
from uuid import UUID

import asyncpg

from ..db import queries
from ..strategies.registry import get_strategy_class, is_registered
from .runner import DeploymentRunner
from .schemas import DeploymentCreate

logger = logging.getLogger("live_deploy.manager")


class DeploymentManager:
    def __init__(self, pool: asyncpg.Pool, broadcaster, dispatcher):
        self.pool = pool
        self.broadcaster = broadcaster
        self.dispatcher = dispatcher
        self.runners: dict[str, DeploymentRunner] = {}   # str(deployment_id) -> runner

    # ── Startup / shutdown ───────────────────────────────────────────

    async def load_active_on_startup(self) -> int:
        """Resume every 'active' deployment. Returns how many were started."""
        rows = await queries.list_deployments(self.pool, status="active")
        for row in rows:
            await self._start_runner(row)
        logger.info("Resumed %d active deployment(s) on startup", len(rows))
        return len(rows)

    async def shutdown_all(self) -> None:
        """Stop every runner task. Deployment status in the DB is untouched."""
        for runner in list(self.runners.values()):
            await runner.stop()
        self.runners.clear()

    # ── CRUD / control ───────────────────────────────────────────────

    async def create_deployment(self, payload: DeploymentCreate) -> tuple[asyncpg.Record, bool]:
        """
        Returns (row, strategy_registered). strategy_registered is
        informational, not enforced — creating a deployment for a
        strategy_name nothing has registered yet is ALLOWED (you can
        set up the deployment — name, capital, tokens, config — before
        the strategy code exists), it just won't trade: the runner's
        strategy stays None until a matching @register_strategy exists
        AND the server restarts (or this deployment is paused/resumed,
        which also re-attaches against the current registry state).
        """
        row = await queries.create_deployment(
            self.pool, payload.deployment_name, payload.strategy_name,
            payload.mode, payload.initial_capital, payload.config,
        )
        await self._start_runner(row)
        return row, is_registered(payload.strategy_name)

    async def pause(self, deployment_id: UUID) -> None:
        row = await queries.get_deployment(self.pool, deployment_id)
        if row is None:
            raise KeyError(f"No such deployment: {deployment_id}")
        if row["status"] != "active":
            raise ValueError(f"Deployment is {row['status']!r}, not active — cannot pause")

        runner = self.runners.pop(str(deployment_id), None)
        if runner is not None:
            await runner.stop()
        await queries.set_status(self.pool, deployment_id, "paused")
        await queries.record_event(self.pool, deployment_id, "paused")
        logger.info("Paused deployment %s", row["deployment_name"])

    async def resume(self, deployment_id: UUID) -> asyncpg.Record:
        row = await queries.get_deployment(self.pool, deployment_id)
        if row is None:
            raise KeyError(f"No such deployment: {deployment_id}")
        if row["status"] == "stopped":
            raise ValueError("Deployment is stopped — stopped deployments cannot be resumed")
        if row["status"] == "active":
            return row   # already running, no-op

        await queries.set_status(self.pool, deployment_id, "active")
        row = await queries.get_deployment(self.pool, deployment_id)
        await self._start_runner(row)
        await queries.record_event(self.pool, deployment_id, "resumed")
        logger.info("Resumed deployment %s", row["deployment_name"])
        return row

    async def stop(self, deployment_id: UUID, force_close: bool = False) -> None:
        row = await queries.get_deployment(self.pool, deployment_id)
        if row is None:
            raise KeyError(f"No such deployment: {deployment_id}")
        if row["status"] == "stopped":
            return   # already stopped, no-op

        open_positions = await queries.list_open_positions(self.pool, deployment_id)
        if open_positions and not force_close:
            raise ValueError(
                f"{len(open_positions)} open position(s) — pass force_close=true "
                f"to flatten them at the last known price, or close them via the "
                f"strategy first."
            )
        if open_positions and force_close:
            from datetime import datetime, timezone
            for pos in open_positions:
                price = self.dispatcher.last_prices.get(int(pos["instrument_token"]))
                if price is None:
                    price = float(pos["avg_entry_price"])
                    logger.warning(
                        "No live price for token %s on force_close — using "
                        "avg_entry_price %.2f (zero P&L on this close)",
                        pos["instrument_token"], price,
                    )
                await queries.force_close_position(
                    self.pool, deployment_id, pos, price,
                    datetime.now(timezone.utc), reason="force_close_on_stop",
                )

        runner = self.runners.pop(str(deployment_id), None)
        if runner is not None:
            await runner.stop()
        self.dispatcher.release_instruments(self._deployment_tokens(row))
        await queries.set_status(self.pool, deployment_id, "stopped")
        await queries.record_event(
            self.pool, deployment_id, "stopped",
            metadata={"force_close": force_close, "positions_closed": len(open_positions)},
        )
        logger.info("Stopped deployment %s (force_close=%s)", row["deployment_name"], force_close)

    # ── Internal ─────────────────────────────────────────────────────

    async def _start_runner(self, row: asyncpg.Record) -> DeploymentRunner:
        # Ensure the dispatcher is (or will be, on next connect) actually
        # subscribed to whatever this deployment trades — dynamically,
        # on the already-live Kite connection if one exists, no restart.
        self.dispatcher.add_instruments(self._deployment_tokens(row))

        # Attach a real strategy instance if one is registered under this
        # name; otherwise the runner just observes ticks without trading
        # (today's behavior for every deployment, since nothing is
        # registered yet at all).
        strategy_cls = get_strategy_class(row["strategy_name"])
        strategy = strategy_cls() if strategy_cls else None
        if strategy is None:
            logger.warning(
                "Deployment %s: strategy %r is not registered — it will "
                "observe ticks but never trade until a matching "
                "@register_strategy exists and this deployment restarts",
                row["deployment_name"], row["strategy_name"],
            )

        runner = DeploymentRunner(row, self.pool, self.broadcaster, self.dispatcher, strategy)
        await runner.start()
        self.runners[str(row["id"])] = runner
        return runner

    @staticmethod
    def _deployment_tokens(row: asyncpg.Record) -> list[dict]:
        """
        A deployment's config only stores bare instrument_token ints
        (see DeploymentRunner.tokens) — wrap them in the
        {"instrument_token":..., "symbol":...} shape add_instruments()/
        release_instruments() expect, matching tokens.json's shape.
        Falls back to the token number itself as the label since the
        deployment config has no symbol name to offer.
        """
        tokens = row["config"].get("instrument_tokens", [])
        return [{"instrument_token": t, "symbol": str(t)} for t in tokens]

    def get_runner(self, deployment_id: UUID) -> Optional[DeploymentRunner]:
        return self.runners.get(str(deployment_id))
