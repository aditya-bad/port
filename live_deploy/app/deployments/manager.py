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
import time as _time
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg

from ..db import queries
from ..slippage import resolve_book_price
from ..strategies.registry import get_strategy_class, is_registered
from .runner import DeploymentRunner
from .schemas import DeploymentCreate

logger = logging.getLogger("live_deploy.manager")

# See DEFAULT_POST_MARKET_DUMP_TIME's own comment below — used to
# compute "now" explicitly in IST for the one scheduling calculation in
# this file that targets a specific WALL-CLOCK time rather than a fixed
# interval, instead of trusting the host machine's own OS timezone to
# already be IST.
_IST = ZoneInfo("Asia/Kolkata")
# Window for position_marks (marks_loop).
_MARKS_OPEN, _MARKS_CLOSE = time(9, 15), time(15, 30)

# How often to record an equity-curve point per active deployment. This
# is for a CHART, not a backtest engine or an audit trail (every fill is
# already durably recorded via position_lots regardless) — 5 minutes is
# plenty of resolution for "how has this deployment's equity moved over
# a trading day/week" without bloating deployment_snapshots on a scale
# that buys no visible benefit. Overridable per-instance (tests pass a
# much shorter interval rather than waiting 5 real minutes).
DEFAULT_SNAPSHOT_INTERVAL_SECONDS = 300.0

# Wall-clock time ("HH:MM", IST) at which post_market_dump_loop fires
# once a day. 15:45: strategies' force_exit_time defaults to "15:00" and
# NSE cash/options both close at 15:30, so 15:45 gives a 15-minute
# buffer for the day's last square-off fills to land and get committed
# before this checkpoint reads get_persistable_state() — a run any
# earlier risks catching a strategy mid-exit and persisting a stale/
# half-updated indicator state.
#
# _seconds_until_next_post_market_dump computes "now" via
# datetime.now(_IST) explicitly (Step 92) — NOT bare datetime.now(),
# which silently means whatever timezone the HOST MACHINE's own OS
# happens to be configured with, not IST, unless something external
# guarantees otherwise. The Dockerfile sets ENV TZ=Asia/Kolkata for
# exactly this reason (see its own comment — this exact "naive datetime
# silently means UTC, not IST" class of bug already mistimed real
# trades here once before) — but that fix only reaches you if you're
# actually running via that Dockerfile. RUN_GUIDE.md documents two
# OTHER ways to run this app (bare `uvicorn`, supervisord) that never
# touch the Dockerfile at all, so relying on the container's TZ env var
# here was a real, live gap: on a host whose own OS timezone isn't IST
# (the common case — most cloud VMs default to UTC), "15:45" silently
# meant 15:45 in the WRONG timezone, and pivot_supertrend*'s daily
# pivot rollover could sit hours late — or, depending on the offset,
# not fire again until the FOLLOWING day, leaving a deployment trading
# an extra full day on stale pivots. Confirmed via
# custom_scripts/validate_supertrend_pivots.py: a live deployment still
# reported yesterday's pivots at 10:40pm IST, seven hours past the
# intended 15:45 checkpoint.
DEFAULT_POST_MARKET_DUMP_TIME = "15:45"


def fold_tick(building: dict, token: int, price: float, minute: datetime) -> None:
    """Fold one tick into its minute's OHLC candle: [minute, o, h, l, c]."""
    v = building.get(token)
    if v is None or v[0] != minute:
        building[token] = [minute, price, price, price, price]
    else:
        v[2], v[3], v[4] = max(v[2], price), min(v[3], price), price


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":")
    return int(hour_str), int(minute_str)


class DeploymentManager:
    def __init__(
        self, pool: asyncpg.Pool, broadcaster, dispatcher,
        snapshot_interval_seconds: float = DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
        post_market_dump_time: str = DEFAULT_POST_MARKET_DUMP_TIME,
        cache=None, event_broadcaster=None, push_config: Optional[dict] = None,
    ):
        self.pool = pool
        self.broadcaster = broadcaster
        self.dispatcher = dispatcher
        # Optional — the app config's own vapid_* keys, or None if push
        # notifications aren't configured at all (see app/notifications
        # .py's own is_push_configured). Handed straight through to
        # every DeploymentRunner this manager starts, for
        # notify_execution's own use — this manager never sends a push
        # itself, it only passes the config along.
        self.push_config = push_config
        self.runners: dict[str, DeploymentRunner] = {}   # str(deployment_id) -> runner
        self.snapshot_interval_seconds = snapshot_interval_seconds
        self.post_market_dump_hour, self.post_market_dump_minute = _parse_hhmm(post_market_dump_time)
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
        # Health (see watchdog_loop): why a deployment's runner last failed
        # to start, what's currently wrong, and which problems already
        # got a push (so each one is sent once, not every check).
        self.start_errors: dict[str, str] = {}
        self.health_issues: list[dict] = []
        self._alerted: set[str] = set()
        self._restart_tried: dict[str, float] = {}   # deployment id -> monotonic time of the last auto-restart
        self._kite_alerted_on = None

    # ── Startup / shutdown ───────────────────────────────────────────

    async def load_active_on_startup(self) -> int:
        """Resume every 'active' deployment. Returns how many were actually started.

        One deployment's on_start() rejecting its own config (e.g. a
        strategy validating that a required field is set) must NEVER
        take down every other deployment's resume, let alone the
        entire app's startup — this loop used to have no try/except at
        all, so a single bad row raised straight out of this method,
        which app startup does not catch either, which crashed the
        whole process before it ever started serving requests. Under
        `--restart unless-stopped` that's not a crash, it's a
        deterministic infinite restart loop: the exact same row fails
        the exact same way on every single restart, so the container
        never recovers on its own — confirmed the hard way in
        production. Mirror the try/except already used around this
        same call in create_deployment/resume below: log and skip the
        broken row, keep going.
        """
        rows = await queries.list_deployments(self.pool, status="active")
        started = 0
        for row in rows:
            try:
                await self._start_runner(row)
            except Exception as e:
                self.start_errors[str(row["id"])] = f"{type(e).__name__}: {e}"
                logger.exception(
                    "Failed to resume deployment %s (%s) on startup -- "
                    "left 'active' in the DB but with no runner; fix the "
                    "underlying config issue and Pause+Resume it (or "
                    "restart the app) to retry",
                    row["deployment_name"], row["id"],
                )
                continue
            started += 1
        logger.info("Resumed %d/%d active deployment(s) on startup", started, len(rows))
        return started

    async def shutdown_all(self) -> None:
        """Stop every runner task. Deployment status in the DB is untouched."""
        for runner in list(self.runners.values()):
            try:
                await runner.stop()
            except Exception:
                logger.exception("Stopping %s failed — continuing with the rest", runner.deployment_name)
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
        try:
            await self._start_runner(row)
        except Exception:
            # A resume that fails to actually start (most reachable
            # cause now: config was edited while paused into something
            # a strategy's own on_start() validation rejects, e.g. a
            # malformed instrument_tokens list -- see DeploymentUpdate's
            # docstring for how that editing is meant to work) must NOT
            # leave the row claiming "active" with no runner actually
            # tracking it -- that's a silently-broken deployment: still
            # shows as running, never processes another tick, and
            # nothing about its own state says so. Roll back to paused
            # (its state immediately before this call) so the failure is
            # recoverable -- fix the config and resume again -- rather
            # than needing a server restart to notice and self-heal via
            # load_active_on_startup. The original exception (typically
            # the strategy's own descriptive ValueError) still propagates
            # to the caller, which already turns a ValueError into a
            # clean 409 (see the router's resume endpoint).
            await queries.set_status(self.pool, deployment_id, "paused")
            raise
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
                    book_price=await resolve_book_price(
                        self.dispatcher, int(pos["instrument_token"]),
                        "sell" if pos["side"] == "long" else "buy", float(pos["qty"]),
                    ),
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

    async def flatten(self, deployment_id: UUID, pause_after: bool = False) -> int:
        """
        Closes every open position for ONE deployment at the last known
        price. The deployment keeps its status: an active one stays
        active — its runner is stopped first (so the strategy can't act
        mid-close), then restarted once everything is closed, so the
        strategy rebuilds its in-memory state from the now-flat database
        exactly as a resume would (it must not keep managing legs that no
        longer exist). It may re-enter on its next signal, per its own
        rules. `pause_after=True` (flatten_all, the panic button) leaves
        it paused instead, so nothing re-enters. If the restart itself
        fails, the deployment is left paused rather than claiming
        "active" with no runner.

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

        was_active = row["status"] == "active"
        if was_active:
            runner = self.runners.pop(str(deployment_id), None)
            if runner is not None:
                await runner.stop()

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
                book_price=await resolve_book_price(
                    self.dispatcher, int(pos["instrument_token"]),
                    "sell" if pos["side"] == "long" else "buy", float(pos["qty"]),
                ),
            )

        if was_active and pause_after:
            await queries.set_status(self.pool, deployment_id, "paused")
            await self._record_event(
                deployment_id, row["deployment_name"], row["strategy_name"], "paused",
                metadata={"reason": "flatten_all", "positions_closed": len(open_positions)},
            )
        elif was_active:
            try:
                await self._start_runner(await queries.get_deployment(self.pool, deployment_id))
            except Exception:
                logger.exception(
                    "Flatten: %s could not be restarted after closing its positions — left paused",
                    row["deployment_name"],
                )
                await queries.set_status(self.pool, deployment_id, "paused")
                await self._record_event(
                    deployment_id, row["deployment_name"], row["strategy_name"], "paused",
                    metadata={"reason": "restart_failed_after_flatten", "positions_closed": len(open_positions)},
                )
                return len(open_positions)
            await self._record_event(
                deployment_id, row["deployment_name"], row["strategy_name"], "flattened",
                metadata={"positions_closed": len(open_positions), "still_active": True},
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

    async def remove_trades(self, deployment_id: UUID) -> dict:
        """
        Flatten's destructive sibling: instead of CLOSING the current
        trade/cycle, this DELETES it outright — positions, lots, the
        fill/execution events it logged, its cash impact reversed — so
        it leaves no trace in the report, not just a closed-out entry
        in it. See queries.resolve_current_trade_scope for exactly what
        "the current trade" means (just what's open right now for most
        strategies; the WHOLE current cycle including its already-
        closed legs for a strategy like strangle_monthly_v2 that sets
        StrategyBase.ADJUSTMENT_GROUP_BY) and queries.remove_trade_scope
        for exactly what gets deleted/reversed/left alone.

        Irreversible — unlike flatten (which only ever adds one more,
        completely ordinary closing fill to the record), there is no
        "resume and it's back" from this. Raises ValueError if there is
        nothing to remove (a genuinely flat deployment, or a strategy
        with no ADJUSTMENT_GROUP_BY that has never traded) rather than
        silently no-op'ing the way flatten does for 0 open positions —
        the caller asked for something specific to be gone; staying
        silent about "there was nothing there" would be misleading for
        an action this irreversible.

        Works on 'active' or 'paused' deployments, same as flatten.
        Unlike flatten (which parks the deployment in 'paused' either
        way), an 'active' deployment stays active: its in-memory runner
        is torn down, the DB surgery runs, then a FRESH runner is built
        immediately from the now-corrected row — the exact same "call
        the app's own API to Pause then Resume" trick
        custom_scripts/remove_todays_trades.py already uses from
        outside the process, just done natively in-process here since
        this runs INSIDE it. A 'paused' deployment simply stays paused,
        nothing to rebuild.
        """
        row = await queries.get_deployment(self.pool, deployment_id)
        if row is None:
            raise KeyError(f"No such deployment: {deployment_id}")
        if row["status"] == "stopped":
            raise ValueError("Deployment is stopped — resume it first, or use Delete instead.")

        cls = get_strategy_class(row["strategy_name"])
        group_by = getattr(cls, "ADJUSTMENT_GROUP_BY", None) if cls is not None else None
        scope = await queries.resolve_current_trade_scope(self.pool, deployment_id, group_by)
        if not scope["position_ids"]:
            raise ValueError("Nothing to remove — no open position (or current cycle) for this deployment.")

        was_active = row["status"] == "active"
        if was_active:
            runner = self.runners.pop(str(deployment_id), None)
            if runner is not None:
                await runner.stop()   # dumps its (about-to-be-stale) state; remove_trade_scope clears it right after

        result = await queries.remove_trade_scope(
            self.pool, deployment_id, scope["position_ids"], scope["min_opened_at"],
        )

        if was_active:
            fresh_row = await queries.get_deployment(self.pool, deployment_id)
            try:
                await self._start_runner(fresh_row)
            except Exception:
                # Same recovery shape as resume()'s own except block --
                # the deletion above already committed, so this must NOT
                # leave the row claiming 'active' with no runner actually
                # tracking it (a silently broken deployment: still shows
                # as running, never processes another tick). Roll back to
                # 'paused' so it's recoverable via an ordinary Resume once
                # whatever on_start rejected is fixed, instead of needing
                # a server restart to notice and self-heal.
                await queries.set_status(self.pool, deployment_id, "paused")
                logger.exception(
                    "%s: trades removed, but the runner failed to restart — "
                    "left paused for manual Resume", row["deployment_name"],
                )
                raise

        await self._record_event(
            deployment_id, row["deployment_name"], row["strategy_name"], "trades_removed",
            metadata={"positions_removed": result["positions_removed"], "cash_delta": result["cash_delta"]},
        )
        logger.info(
            "Removed trades for deployment %s: %d position(s) deleted, cash reversed by %.2f",
            row["deployment_name"], result["positions_removed"], result["cash_delta"],
        )
        return result

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
                closed = await self.flatten(row["id"], pause_after=True)
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

    # ── Per-position price marks (Cycle Replay chart) ──────────────────

    async def marks_loop(self) -> None:
        """Every minute, on the minute, during market hours: records each
        open leg's last traded price (position_marks) — every minute for
        intraday deployments, every 5th minute for positional ones. Kite
        has no history for expired options, so these are what keep a
        cycle's per-leg P&L curve drawable afterwards."""
        while True:
            now = datetime.now(_IST)
            await asyncio.sleep(60 - now.second - now.microsecond / 1e6 + 0.5)
            try:
                await self.record_marks_once(datetime.now(_IST))
            except Exception:
                logger.exception("Recording position price marks failed — continuing")

    async def record_marks_once(self, now: datetime) -> int:
        marked_at = now.replace(second=0, microsecond=0)
        if marked_at.weekday() >= 5 or not (_MARKS_OPEN <= marked_at.time() <= _MARKS_CLOSE):
            return 0
        session_open = datetime.combine(marked_at.date(), _MARKS_OPEN, _IST)
        rows = []
        for runner in list(self.runners.values()):
            if runner.mode != "intraday" and marked_at.minute % 5 != 0:
                continue
            for token, pos in runner.open_positions.items():
                price = self.dispatcher.last_prices.get(token)
                traded_at = self.dispatcher.last_price_times.get(token)
                if price is None or pos.get("id") is None:
                    continue
                # Skip a price still carrying a previous session's trade
                # (holiday, not traded yet today) — it isn't today's mark.
                if traded_at is not None:
                    t = traded_at.astimezone(_IST) if traded_at.tzinfo else traded_at.replace(tzinfo=_IST)
                    if t < session_open:
                        continue
                rows.append((pos["id"], runner.deployment_id, marked_at, price))
        await queries.record_position_marks(self.pool, rows)
        return len(rows)

    # ── 1-minute candles (Cycle / Session charts, app/candles.py) ──────

    def watched_tokens(self, static_tokens=()) -> set[int]:
        """The option contracts whose minutes the app stores: every CE/PE
        any running deployment holds, each once however many hold it.
        Indices and stocks are never stored — Kite keeps their history
        (app/candles.py)."""
        watch: set[int] = set()
        for runner in self.runners.values():
            for tok, pos in runner.open_positions.items():
                if str(pos.get("symbol", "")).endswith(("CE", "PE")):
                    watch.add(int(tok))
        return watch

    async def candles_loop(self, static_tokens: list[int]) -> None:
        """Builds a 1-minute OHLC candle from the live ticks of every held
        option contract (watched_tokens) and stores it once the minute
        closes, so a contract's chart survives its expiry even though
        Kite then has no history for it. Market hours only; a tick still
        carrying an earlier day's trade (holiday, pre-open snapshot) is
        ignored."""
        q = await self.broadcaster.subscribe()
        building: dict[int, list] = {}   # token -> [minute, o, h, l, c]
        try:
            while True:
                try:
                    ticks = await asyncio.wait_for(q.get(), timeout=5)
                except asyncio.TimeoutError:
                    ticks = []
                try:
                    now = datetime.now(_IST)
                    minute = now.replace(second=0, microsecond=0)
                    done = [tok for tok, v in building.items() if v[0] < minute]
                    if done:
                        await queries.record_index_candles(
                            self.pool, [(tok, *building.pop(tok)) for tok in done],
                        )
                    if not ticks or now.weekday() >= 5 or not (_MARKS_OPEN <= now.time() < _MARKS_CLOSE):
                        continue
                    watch = self.watched_tokens(static_tokens)
                    for t in ticks if isinstance(ticks, list) else []:
                        tok, price = t.get("instrument_token"), t.get("last_price")
                        if tok not in watch or not price:
                            continue
                        traded = t.get("last_trade_time") or t.get("exchange_timestamp")
                        if traded is not None and traded.date() != now.date():
                            continue
                        fold_tick(building, tok, float(price), minute)
                except Exception:
                    logger.exception("Recording candles failed — continuing")
        finally:
            await self.broadcaster.unsubscribe(q)

    index_candles_loop = candles_loop   # earlier name


    async def candle_backfill_loop(self, candles) -> None:
        """Once a trading day at 15:45: fill the day's history of every
        option contract held today from Kite while it still has it —
        expired contracts can't be fetched later. Also runs right away on
        a start after 15:45, in case the app was down then. Cheap to
        repeat: covered days are never fetched."""
        from ..candles import BACKFILL_AT
        done: Optional[date] = None
        while True:
            now = datetime.now(_IST)
            if now.weekday() < 5 and now.time() >= BACKFILL_AT and done != now.date():
                try:
                    r = await candles.backfill_day(now.date())
                    if r.get("complete"):
                        done = now.date()      # else (Kite logged out mid-way) retried in 5 minutes
                except Exception:
                    logger.exception("Candle backfill failed — will retry")
            await asyncio.sleep(60 if done != datetime.now(_IST).date() else 300)

    # ── Health watchdog: active but not running; Kite not connected ────

    def _is_running(self, deployment_id: str) -> bool:
        runner = self.runners.get(deployment_id)
        return runner is not None and runner._task is not None and not runner._task.done()

    async def find_not_running(self) -> list[dict]:
        """Deployments the database says are active but that have no
        live runner: their strategy isn't entering, adjusting or exiting
        anything. Usually a start that failed (start_errors says why)."""
        out = []
        for row in await queries.list_deployments(self.pool, status="active"):
            did = str(row["id"])
            if self._is_running(did):
                continue
            n_open = await self.pool.fetchval(
                "SELECT count(*) FROM positions WHERE deployment_id = $1 AND status = 'open'", row["id"],
            )
            runner = self.runners.get(did)
            reason = self.start_errors.get(did) or (
                "its runner stopped unexpectedly" if runner is not None else "it was never started"
            )
            out.append({
                "id": did, "name": row["deployment_name"], "strategy": row["strategy_name"],
                "open_positions": int(n_open or 0), "reason": reason,
            })
        return out

    async def restart(self, deployment_id: UUID) -> None:
        """Start the runner of an active deployment that isn't running.
        Raises ValueError with the strategy's own error if it fails again."""
        row = await queries.get_deployment(self.pool, deployment_id)
        if row is None:
            raise KeyError(f"No such deployment: {deployment_id}")
        if row["status"] != "active":
            raise ValueError(f"Deployment is {row['status']!r}; resume it instead")
        did = str(deployment_id)
        if self._is_running(did):
            return
        stale = self.runners.pop(did, None)
        if stale is not None:
            try:
                await stale.stop()
            except Exception:
                pass
        try:
            await self._start_runner(row)
        except Exception as e:
            self.start_errors[did] = f"{type(e).__name__}: {e}"
            raise ValueError(f"Still can't start: {self.start_errors[did]}") from e
        await self._record_event(deployment_id, row["deployment_name"], row["strategy_name"], "restarted")
        self.health_issues = [x for x in self.health_issues if x["id"] != did]
        self._alerted.discard(did)

    async def _push(self, title: str, body: str) -> None:
        if self.push_config is None:
            return
        from .. import notifications   # deferred, same as DeploymentRunner.notify_execution
        try:
            await notifications.send_push_for_all(self.pool, self.push_config, title, body)
        except Exception:
            logger.exception("Watchdog push failed")

    async def watchdog_loop(self, interval_seconds: float = 30.0) -> None:
        """Every 30s: refresh health_issues (shown by GET /health and the
        UI) and push once for each new problem:
          - a deployment that's active but not running;
          - on a market day, Kite still not connected at 09:05 (skipped
            on days marked as a market holiday)."""
        while True:
            try:
                await self.check_health_once(datetime.now(_IST))
            except Exception:
                logger.exception("Watchdog check failed — continuing")
            await asyncio.sleep(interval_seconds)

    AUTO_RESTART_EVERY = 60.0   # seconds between automatic restart attempts per deployment

    async def _auto_restart(self, issues: list[dict]) -> list[dict]:
        """Try to bring every not-running deployment back by itself (at
        most once a minute each): a start that failed on a passing Kite
        hiccup, or a runner that died, recovers without anyone pressing
        Restart. Returns the ones still not running."""
        still = []
        for x in issues:
            did = x["id"]
            last = self._restart_tried.get(did)
            if last is not None and _time.monotonic() - last < self.AUTO_RESTART_EVERY:
                still.append(x)
                continue
            self._restart_tried[did] = _time.monotonic()
            was_alerted = did in self._alerted
            try:
                await self.restart(UUID(did))
            except Exception as e:
                logger.warning("Auto-restart of %s failed: %s", x["name"], e)
                x = {**x, "reason": self.start_errors.get(did, x["reason"])}
                still.append(x)
                continue
            logger.info("Auto-restarted %s", x["name"])
            self._restart_tried.pop(did, None)
            if was_alerted:
                await self._push(f"{x['name']} is running again", "Restarted automatically. It picked up its open positions from where it left off.")
        return still

    async def check_health_once(self, now: datetime) -> None:
        issues = await self._auto_restart(await self.find_not_running())
        self.health_issues = issues
        current = {x["id"] for x in issues}
        for x in issues:
            if x["id"] in self._alerted:
                continue
            self._alerted.add(x["id"])
            legs = f" It holds {x['open_positions']} open position(s) that nothing will exit." if x["open_positions"] else ""
            await self._push(f"{x['name']} isn't running",
                             f"Marked active but its strategy isn't running ({x['reason']}).{legs} "
                             "Retrying automatically every minute — or open it and press Restart.")
            await self._record_event(UUID(x["id"]), x["name"], x["strategy"], "not_running", message=x["reason"])
        self._alerted &= current
        for did in list(self._restart_tried):
            if did not in current:
                self._restart_tried.pop(did, None)

        connected = bool(getattr(self.dispatcher, "connected", False)) and not self.dispatcher.status.get("needs_login")
        market_day = now.weekday() < 5
        if connected or not market_day or not (time(9, 5) <= now.time() < time(15, 30)):
            return
        if self._kite_alerted_on == now.date():
            return
        muted = None
        if self.cache is not None:
            try:
                muted = await self.cache.get("notification_mute_until")
            except Exception:
                muted = None
        if muted is not None and datetime.now(timezone.utc) < muted:
            return
        self._kite_alerted_on = now.date()
        n = len(self.runners)
        await self._push(
            "Kite isn't connected",
            f"It's {now:%H:%M} and Kite isn't logged in — {n} running deployment(s) can't trade until you connect.",
        )

    # ── Post-market state checkpoint (once a day, not on shutdown) ──────

    async def post_market_dump_loop(self) -> None:
        """
        Runs for the lifetime of the process (started as a background
        task from main.py's startup(), cancelled on shutdown) — fires
        once per calendar day at post_market_dump_hour:
        post_market_dump_minute (default 15:45 IST — see
        DEFAULT_POST_MARKET_DUMP_TIME's own comment), dumps
        get_persistable_state() for every currently-active deployment,
        then sleeps until the same time tomorrow.

        This exists as a SECOND, independent checkpoint alongside the
        one DeploymentRunner.stop() already takes on pause/stop/graceful
        shutdown — that one only fires if something asks the runner to
        stop; a redeploy that gets SIGKILLed before finishing its
        graceful-shutdown window, or any other ungraceful exit, skips it
        entirely (see StrategyBase.get_persistable_state's own docstring
        on why that case is accepted, not fixed — no hook runs on
        SIGKILL/OOM, full stop). Firing this once a day, unconditionally,
        after the trading day is actually done, means the worst case for
        "how stale can persisted state be if today's graceful shutdown
        never happens" is "up to one day," not "since whenever this
        deployment was last paused" — a meaningfully safer floor for
        very-low-traffic strategies that might otherwise go weeks
        between an actual pause/stop event.

        Deliberately clock-time-based, not interval-based like
        snapshot_loop above — "once, shortly after market close" is a
        calendar concept, not a "how fresh" one, so a fixed interval
        would either fire uselessly overnight/on weekends or need its
        own separate market-hours gating to avoid it.

        CATCH-UP ON START (Step 92): _seconds_until_next_post_market_dump
        always schedules forward to the NEXT occurrence of the target
        time — so a process that starts (or restarts: a deploy, a
        crash, routine maintenance) at a moment AFTER today's own target
        has already passed would otherwise schedule straight for
        TOMORROW's occurrence and silently skip today's entirely, every
        pivot_supertrend* deployment trading an extra full day on
        yesterday's pivots. One immediate dump here, before ever
        entering the wait loop below, closes that gap — harmless if
        today's checkpoint genuinely already ran earlier this same
        process's lifetime (idempotent: just a redundant Kite re-fetch,
        not incorrect data), but never silently defers an un-run day to
        tomorrow.
        """
        now = datetime.now(_IST).replace(tzinfo=None)
        target_today = now.replace(
            hour=self.post_market_dump_hour, minute=self.post_market_dump_minute,
            second=0, microsecond=0,
        )
        if target_today <= now:
            logger.info(
                "Post-market dump loop starting after today's own %02d:%02d IST target has "
                "already passed (now=%s) — catching up with an immediate dump instead of "
                "waiting until tomorrow's occurrence.",
                self.post_market_dump_hour, self.post_market_dump_minute, now,
            )
            await self.dump_state_all_active()
        while True:
            await asyncio.sleep(self._seconds_until_next_post_market_dump())
            await self.dump_state_all_active()

    def _seconds_until_next_post_market_dump(self) -> float:
        # datetime.now(_IST), not bare datetime.now() -- see
        # DEFAULT_POST_MARKET_DUMP_TIME's own comment for exactly why
        # trusting the host machine's OS timezone here was a real bug.
        now = datetime.now(_IST).replace(tzinfo=None)
        target = now.replace(
            hour=self.post_market_dump_hour, minute=self.post_market_dump_minute,
            second=0, microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def dump_state_all_active(self) -> None:
        """Checkpoint every currently-running deployment (self.runners —
        same scope as snapshot_all_active above) via
        runner.post_market_checkpoint() — for a strategy that overrides
        StrategyBase.on_post_market_checkpoint (currently the
        pivot_supertrend family), this actively refreshes its live
        in-memory state from an authoritative outside source BEFORE
        persisting; for every other strategy it's exactly
        runner.dump_state() as before this hook existed. WITHOUT
        stopping or otherwise touching any of them — each runner keeps
        trading through this exactly as it does through a snapshot
        round. A single deployment's checkpoint failing (e.g. a
        transient DB hiccup, or its own refresh hook raising) must not
        stop the rest from being checkpointed, so each is isolated and
        logged rather than propagated — same pattern as
        snapshot_all_active."""
        dumped = 0
        for runner in list(self.runners.values()):
            try:
                await runner.post_market_checkpoint()
                dumped += 1
            except Exception:
                logger.exception(
                    "Post-market checkpoint failed for %s — continuing",
                    runner.deployment_name,
                )
        logger.info("Post-market state dump: checked %d active deployment(s)", dumped)

    async def _snapshot_one(self, runner: DeploymentRunner) -> None:
        # Step 99 fix — this used to compute total_value as `cash +
        # open_positions_value`, which DOUBLE-COUNTS a still-open
        # SHORT leg's entry premium: `cash` already carries the full
        # premium credit the instant a leg opens (record_fill credits
        # it immediately, no margin model — see routers/deployments.py's
        # own _open_cost_basis, which documents the always-true identity
        # `current_cash == initial_capital + realized_pnl +
        # open_cost_basis`, where open_cost_basis is +qty*avg for an
        # open short — that SAME premium, already sitting in cash).
        # open_positions_value's own (avg - price)*qty term ALSO
        # implicitly re-adds that same +avg*qty on top when summed with
        # cash, since (avg-price) = +avg - price. The two together
        # overstated total_value by exactly the entry premium for as
        # long as a short leg stayed open — which is exactly what got
        # reported as "max profit" once Step 97 started tracking each
        # day's high/low off this same inflated number: it was
        # measuring premium COLLECTED, not premium actually EARNED.
        #
        # Correct total is `initial_capital + realized_pnl +
        # open_positions_value` (NOT cash) — this is the same identity
        # rearranged: cash's own open_cost_basis component is exactly
        # what needs to be REPLACED by open_positions_value (the
        # CURRENT, price-dependent value), not added on top of it.
        #
        # Beyond just fixing the double-count, this also now EXCLUDES
        # open_positions_value entirely for "intraday" deployments (by
        # explicit request) — an option leg's live premium swinging
        # around while it's still open mid-session isn't real profit
        # yet, just noise, and shouldn't move the equity number even
        # once double-counting is fixed; only "positional" deployments
        # (designed to genuinely carry a position past market close)
        # get their open leg's real mark-to-market folded in, since
        # there a lingering open position IS an intended part of the
        # account's value, not an artifact of the snapshot simply
        # landing mid-trade.
        open_positions_value = 0.0
        if runner.mode == "positional":
            # Mark-to-market off the runner's OWN already-loaded
            # open_positions cache. A leg with no live price (Kite not
            # connected, a holiday, just restarted) is valued at its
            # last recorded mark; valuing it at 0 would record a fake
            # drop (and the Desk would show the whole unrealized P&L as
            # a move "today"). A leg never priced at all: no snapshot.
            missing = [pos["id"] for token, pos in runner.open_positions.items()
                       if self.dispatcher.last_prices.get(token) is None and pos.get("id") is not None]
            marks = await queries.latest_marks(self.pool, missing) if missing else {}
            for token, pos in runner.open_positions.items():
                price = self.dispatcher.last_prices.get(token)
                if price is None:
                    price = marks.get(pos.get("id"))
                if price is None:
                    logger.info("Snapshot skipped for %s: %s has never been priced",
                                runner.deployment_name, pos.get("symbol"))
                    return
                qty, avg = float(pos["qty"]), float(pos["avg_entry_price"])
                open_positions_value += (price - avg) * qty if pos["side"] == "long" \
                    else (avg - price) * qty

        realized = await queries.realized_pnl_total(self.pool, runner.deployment_id)
        await queries.record_snapshot(
            self.pool, runner.deployment_id, datetime.now(timezone.utc),
            cash=runner.cash, open_positions_value=open_positions_value,
            total_value=runner.initial_capital + realized + open_positions_value,
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
            push_config=self.push_config,
        )
        await runner.start()
        self.runners[str(row["id"])] = runner
        self.start_errors.pop(str(row["id"]), None)
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
