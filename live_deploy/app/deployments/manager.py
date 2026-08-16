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

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import asyncpg

from ..db import queries
from ..strategies.registry import get_strategy_class, is_registered
from .runner import DeploymentRunner
from .schemas import DeploymentCreate

logger = logging.getLogger("live_deploy.manager")

# How often to record an equity-curve point per active deployment. This
# is for a CHART, not a backtest engine or an audit trail (every fill is
# already durably recorded via position_lots regardless) — 5 minutes is
# plenty of resolution for "how has this deployment's equity moved over
# a trading day/week" without bloating deployment_snapshots on a scale
# that buys no visible benefit. Overridable per-instance (tests pass a
# much shorter interval rather than waiting 5 real minutes).
DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 300.0


class DeploymentManager:
    def __init__(
        self, pool: asyncpg.Pool, broadcaster, dispatcher,
        snapshot_interval_seconds: float = DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
        cache=None, event_broadcaster=None,
    ):
        self.pool = pool
        self.broadcaster = broadcaster
        self.dispatcher = dispatcher
        self.runners: dict[str, DeploymentRunner] = {}   # str(deployment_id) -> runner
        self.snapshot_interval_seconds = snapshot_interval_seconds
        # Optional (app.state.event_broadcaster — see app/broadcaster.py).
        # Used directly here for events this manager records itself
        # (pause/resume/stop/flatten — see _record_event below) AND
        # handed down to every DeploymentRunner this manager starts, so
        # runner-originated events (fills, strategy errors) go out over
        # the SAME broadcaster instance/websocket stream. None in any
        # context that doesn't have one (e.g. tests), same
        # optional-hook pattern as `cache` above.
        self.event_broadcaster = event_broadcaster
        # Optional (app.state.cache — see app/cache.py). When given,
        # every runner this manager starts gets wired to call back in
        # here after each fill, so a strategy-driven trade shows up in
        # the cached GET /deployments/positions/trades-recent lists
        # immediately instead of waiting out the background refresh
        # loop — see DeploymentRunner's own on_fill docstring for why
        # this matters. None in any context that doesn't have a cache
        # (kept optional rather than required so the manager itself
        # stays testable/constructible without standing up the whole
        # app).
        self.cache = cache

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
            notes=payload.notes,
        )
        try:
            await self._start_runner(row)
        except Exception:
            # The row above is already committed -- if the runner then
            # fails to start (most commonly a strategy's own on_start()
            # rejecting the config, e.g. pivot_supertrend requiring
            # exactly one instrument_token), roll it back rather than
            # leaving an orphaned row a caller who was just told "this
            # failed" (a 400 from the router) has no idea exists. Without
            # this, retrying with the same deployment_name would 409 as
            # "already exists" for a deployment that supposedly never
            # got created.
            await queries.delete_deployment(self.pool, row["id"])
            raise
        # Only after the runner actually started successfully (the
        # rollback-on-failure path above must NOT announce a deployment
        # that's about to be deleted) -- a genuine gap until now: no
        # "created" deployment_event ever existed at all, so a new
        # deployment was invisible to both the Activity tab AND the
        # real-time alert toasts, undermining the whole point of moving
        # off blind polling (see README's Step 44) if the one mutation
        # that starts a deployment's whole existence wasn't covered.
        await self._record_event(row["id"], row["deployment_name"], row["strategy_name"], "created")
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
        await self._record_event(deployment_id, row["deployment_name"], row["strategy_name"], "paused")
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
        await self._record_event(deployment_id, row["deployment_name"], row["strategy_name"], "resumed")
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
        await self._record_event(
            deployment_id, row["deployment_name"], row["strategy_name"], "stopped",
            metadata={"force_close": force_close, "positions_closed": len(open_positions)},
        )
        logger.info("Stopped deployment %s (force_close=%s)", row["deployment_name"], force_close)

    async def flatten(self, deployment_id: UUID) -> int:
        """
        The panic-button primitive: closes every open position for ONE
        deployment at the last known price, then PAUSES it (not
        stops) — deliberately in between the two existing primitives,
        not a variant of either: pause() alone leaves positions open,
        stop(force_close=True) closes them but permanently stops the
        deployment (resume() explicitly refuses a 'stopped' one). This
        is "get out of every position right now, decide what to do
        about the deployment itself later" — same reversibility as a
        manual pause, just with nothing left open when you get there.

        Works on 'paused' deployments too, not just 'active' ones — a
        paused deployment can absolutely still have open positions
        (pause() never touches them), and flattening should mean
        exactly that regardless of whether a runner is currently
        ticking. A 'stopped' deployment has nothing left to flatten
        (stop() already closed everything if it was force_close=True,
        or the positions are staying open on a dead deployment by
        design if it wasn't).

        Returns how many positions were actually closed, so a bulk
        caller (flatten_all, below) can report a real number rather
        than "done" for however many deployments had nothing open.
        """
        row = await queries.get_deployment(self.pool, deployment_id)
        if row is None:
            raise KeyError(f"No such deployment: {deployment_id}")
        if row["status"] == "stopped":
            return 0

        open_positions = await queries.list_open_positions(self.pool, deployment_id)
        for pos in open_positions:
            price = self.dispatcher.last_prices.get(int(pos["instrument_token"]))
            if price is None:
                price = float(pos["avg_entry_price"])
                logger.warning(
                    "No live price for token %s on flatten — using "
                    "avg_entry_price %.2f (zero P&L on this close)",
                    pos["instrument_token"], price,
                )
            await queries.force_close_position(
                self.pool, deployment_id, pos, price,
                datetime.now(timezone.utc), reason="flatten_all",
            )

        if row["status"] == "active":
            runner = self.runners.pop(str(deployment_id), None)
            if runner is not None:
                await runner.stop()
            await queries.set_status(self.pool, deployment_id, "paused")
            await self._record_event(
                deployment_id, row["deployment_name"], row["strategy_name"], "paused",
                metadata={"reason": "flatten_all", "positions_closed": len(open_positions)},
            )
        elif open_positions:
            # Already paused -- no runner/status transition needed, but
            # still worth a row in the deployment's own Activity tab
            # recording that this happened and how many positions it closed.
            await self._record_event(
                deployment_id, row["deployment_name"], row["strategy_name"], "flattened",
                metadata={"positions_closed": len(open_positions)},
            )

        logger.info(
            "Flattened deployment %s: %d position(s) closed", row["deployment_name"], len(open_positions),
        )
        return len(open_positions)

    async def flatten_all(self) -> dict:
        """The actual panic button: flatten() every deployment that
        isn't already stopped. Never lets one deployment's failure
        (e.g. a stale/missing price it can't recover from) abort the
        rest — the whole point is "get out of everything," so a
        partial failure should still flatten everything it can and
        report which one(s) didn't, not silently do less than asked."""
        rows = await queries.list_deployments(self.pool)
        targets = [r for r in rows if r["status"] != "stopped"]

        deployments_flattened = 0
        positions_closed = 0
        errors: list[dict] = []
        for row in targets:
            try:
                closed = await self.flatten(row["id"])
            except Exception as e:
                logger.exception("flatten_all: failed to flatten deployment %s", row["deployment_name"])
                errors.append({"deployment_name": row["deployment_name"], "error": str(e)})
                continue
            if closed:
                deployments_flattened += 1
                positions_closed += closed

        return {
            "deployments_checked": len(targets),
            "deployments_flattened": deployments_flattened,
            "positions_closed": positions_closed,
            "errors": errors,
        }

    # ── Equity-curve snapshots (periodic, not per-tick) ─────────────────

    async def snapshot_loop(self) -> None:
        """
        Runs for the lifetime of the process (started as a background
        task from main.py's startup(), cancelled on shutdown) — sleeps
        `snapshot_interval_seconds`, records one snapshot per currently
        ACTIVE deployment, repeats. Deliberately NOT hooked into the
        runner's own tick loop (module docstring's "not per tick") —
        this task is the one place that decides "how often," completely
        independent of how fast ticks are actually arriving.
        """
        while True:
            await asyncio.sleep(self.snapshot_interval_seconds)
            await self.snapshot_all_active()

    async def snapshot_all_active(self) -> None:
        """One equity-curve point for every currently-running deployment
        (self.runners — paused/stopped deployments have no runner and
        are correctly skipped; their existing snapshot history is
        untouched). A single deployment's snapshot failing (e.g. a
        transient DB hiccup) must not stop the rest from being recorded,
        so each is isolated and logged rather than propagated."""
        for runner in list(self.runners.values()):
            try:
                await self._snapshot_one(runner)
            except Exception:
                logger.exception(
                    "Failed to record an equity snapshot for %s — continuing",
                    runner.deployment_name,
                )
        # Once per round (not once per deployment) -- this IS the exact
        # moment new deployment_snapshots rows exist, so
        # portfolio_equity_curve's cache no longer needs to guess via a
        # fixed poll interval when fresh data might be ready; it can
        # just be told. self.cache is optional (see __init__) for the
        # same reason as _on_fill_committed above.
        if self.cache is not None:
            await self.cache.refresh_now("portfolio_equity_curve")

    async def _snapshot_one(self, runner: DeploymentRunner) -> None:
        # Mark-to-market off the runner's OWN already-loaded
        # open_positions cache (no extra DB round trip needed — this is
        # exactly the state DeploymentRunner already maintains for the
        # strategy itself) — see SnapshotOut's own docstring for why
        # `open_positions_value` here means unrealized P&L, not notional
        # position value.
        open_positions_value = 0.0
        for token, pos in runner.open_positions.items():
            price = self.dispatcher.last_prices.get(token)
            if price is None:
                continue
            qty, avg = float(pos["qty"]), float(pos["avg_entry_price"])
            open_positions_value += (price - avg) * qty if pos["side"] == "long" \
                else (avg - price) * qty

        realized = await queries.realized_pnl_total(self.pool, runner.deployment_id)
        await queries.record_snapshot(
            self.pool, runner.deployment_id, datetime.now(timezone.utc),
            cash=runner.cash, open_positions_value=open_positions_value,
            total_value=runner.cash + open_positions_value,
            realized_pnl_cumulative=realized,
        )

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

        runner = DeploymentRunner(
            row, self.pool, self.broadcaster, self.dispatcher, strategy,
            on_fill=self._on_fill_committed,
            event_broadcaster=self.event_broadcaster,
        )
        await runner.start()
        self.runners[str(row["id"])] = runner
        return runner

    async def _record_event(
        self, deployment_id: UUID, deployment_name: str, strategy_name: str,
        event_type: str, message: Optional[str] = None, metadata: Optional[dict] = None,
    ) -> None:
        """DeploymentManager's own equivalent of DeploymentRunner._record_event
        (see that one's docstring) — used here for events the MANAGER
        records directly (pause/resume/stop/flatten), as opposed to
        events a running strategy triggers (fills, strategy errors),
        which go through the runner's own copy instead since a runner
        doesn't hold a reference back to its manager. Same DB-write-then-
        broadcast shape, deployment_name/strategy_name passed in rather
        than re-fetched since every call site here already has `row` in
        scope from its own earlier `get_deployment` call."""
        await queries.record_event(self.pool, deployment_id, event_type, message=message, metadata=metadata)
        if self.event_broadcaster is None:
            return
        await self.event_broadcaster.broadcast({
            "deployment_id": str(deployment_id),
            "deployment_name": deployment_name,
            "strategy_name": strategy_name,
            "event_type": event_type,
            "message": message,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def _on_fill_committed(self) -> None:
        """Passed to every DeploymentRunner as on_fill. Fire-and-forget
        on purpose — scheduled as a background task, never awaited here
        — so a slow Neon round trip refreshing the cache can never add
        latency to the strategy's own tick-processing loop. A cache-less
        context (self.cache is None) is a silent no-op, not an error."""
        if self.cache is None:
            return
        asyncio.create_task(self._refresh_cache_after_fill())

    async def _refresh_cache_after_fill(self) -> None:
        await asyncio.gather(
            self.cache.refresh_now("deployments"),
            self.cache.refresh_now("positions_open"),
            self.cache.refresh_now("trades_recent"),
            # A fill can close a position (realized_pnl booked), which is
            # the only thing the leaderboard's numbers depend on -- an
            # opening fill makes this a harmless no-op refresh (nothing
            # actually changed for that fill), but there's no cheap way
            # to know which kind of fill just happened from here, and a
            # refresh_now() is just a cache-store write, not another
            # Neon round trip on the hot path -- see AggregateCache's own
            # docstring for why this is safe to call speculatively.
            self.cache.refresh_now("strategy_leaderboard"),
            return_exceptions=True,   # one failed refresh shouldn't crash the others -- see AggregateCache's own docstring on why a failed refresh degrades to stale, not an error
        )

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
