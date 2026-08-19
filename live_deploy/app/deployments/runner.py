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

        # Fixed for the deployment's lifetime, same as initial_capital —
        # used ONLY by _stale_tick_guard below, converted once here so
        # every tick doesn't redo the conversion.
        #
        # datetime.fromtimestamp() with NO tz argument, deliberately —
        # NOT a hardcoded IST offset (an earlier version of this guard
        # used +5:30, assuming Kite's exchange_timestamp is always naive
        # IST; it isn't guaranteed to be). The real kiteconnect library
        # builds exchange_timestamp via this exact same call —
        # `datetime.fromtimestamp(unix_ts)`, no tz arg — which means it's
        # naive LOCAL SYSTEM TIME of whatever machine runs the Kite
        # WebSocket client, not portably "IST": correct only if/when
        # that machine's own system timezone happens to be IST (the
        # implicit assumption this whole app already makes everywhere
        # else too — entry_time/force_exit_time config values like
        # "10:00" only mean 10am NSE time if the server's clock agrees).
        # Deriving created_at the SAME way keeps this guard consistent
        # with real ticks on ANY server, whatever its system tz is,
        # rather than silently assuming IST and comparing two different
        # clocks — which produced a permanent, every-tick mismatch (see
        # this file's own git history) whenever the server's system tz
        # wasn't actually IST.
        self.created_at: datetime = deployment["created_at"]
        self._created_at_local: datetime = datetime.fromtimestamp(self.created_at.timestamp())

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
        # strategy.on_start() must FULLY complete before _run() (the
        # tick-consuming loop) is even created, let alone start pulling
        # from _queue -- on_start() typically does its own `await`
        # partway through (e.g. `await runner.load_state()`), and every
        # `await` is a point where the event loop can run something
        # else. If _task already existed at that point, a real tick
        # arriving in that exact window would reach strategy.on_tick()
        # on a HALF-INITIALIZED strategy object -- e.g.
        # intraday_dtt_simple's self.ce_token/self.pe_token are only
        # set AFTER its own `await runner.load_state()` call, so a tick
        # landing in that gap raised a bare AttributeError (caught by
        # _run()'s own try/except, so it didn't crash the runner, but a
        # real tick -- possibly the one that should have triggered an
        # entry -- was silently dropped). Subscribing to the broadcaster
        # above still happens first, so no tick is ever missed entirely
        # once _task does start — ticks that arrive during on_start()
        # just sit in the queue and get processed normally the moment
        # _run() begins pulling from it, against a now-fully-initialized
        # strategy.
        if self.strategy is not None:
            await self.strategy.on_start(self)
        self._task = asyncio.create_task(self._run(), name=f"runner:{self.deployment_name}")
        logger.info(
            "Runner started: %s (%s, %s) — %d open position(s), tokens=%s",
            self.deployment_name, self.strategy_name, self.mode,
            len(self.open_positions), sorted(self.tokens),
        )

    async def stop(self) -> None:
        self._running = False
        if self.strategy is not None:
            await self.strategy.on_stop(self)
            await self.dump_state()
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

    async def post_market_checkpoint(self) -> None:
        """Called once a day by DeploymentManager.post_market_dump_loop
        — gives the strategy a chance to actively REFRESH its own live
        in-memory state (see StrategyBase.on_post_market_checkpoint's own
        docstring — typically a REST re-fetch correcting for a tick-gap-
        drifted recursive indicator) before persisting via dump_state()
        below. A strategy that doesn't override the hook is completely
        unaffected — this is then exactly equivalent to calling
        dump_state() directly, same as before this method existed. A
        failure in the hook is caught and logged here, not propagated —
        the checkpoint still falls through to persisting whatever's
        already in memory rather than skipping the whole round over one
        deployment's refresh failing."""
        if self.strategy is not None:
            try:
                await self.strategy.on_post_market_checkpoint(self)
            except Exception:
                logger.exception(
                    "%s: on_post_market_checkpoint() raised — persisting "
                    "whatever state already exists in memory instead",
                    self.deployment_name,
                )
        await self.dump_state()

    async def dump_state(self) -> None:
        """Persist get_persistable_state()'s current return value, if
        any, without touching anything else about this runner (no stop,
        no unsubscribe, no task cancellation) — safe to call while the
        runner keeps trading. Called from stop() (pause, stop, AND a
        graceful full-server shutdown all route through here) AND, once
        a day, from post_market_checkpoint() above (itself called from
        DeploymentManager.post_market_dump_loop) as a standing checkpoint
        — see StrategyBase.get_persistable_state's own docstring for
        exactly when/why each of those fires. A strategy that doesn't
        override get_persistable_state gets None back and this is a
        no-op, same as always — existing strategies are entirely
        unaffected."""
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
        every connected /sse/events subscriber — the in-app real-time
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

    async def list_recent_lots(self, limit: int = 50) -> list[dict]:
        """
        Most-recent individual FILLS for this deployment (open AND close
        alike), most-recent first — for resume-safety that needs the
        CLOSE fill's own metadata specifically (e.g. what trigger caused
        the most recent flatten), which `list_closed_positions()` can't
        answer: a `positions` row's `metadata` column is written once, at
        that position's OPEN, and is never overwritten by its later close
        (see `queries.record_fill`) — only the `position_lots` row for
        that close fill itself carries the close's own trigger/metadata.
        Same sanctioned-access-point principle as buy()/sell()/
        list_closed_positions(): a strategy calls this rather than
        touching `self.pool`/`queries` directly.
        """
        rows, _total = await queries.list_lots(self.pool, self.deployment_id, offset=0, limit=limit)
        return [dict(r) for r in rows]

    # ── Tick consumption ─────────────────────────────────────────────

    def _is_stale_pre_creation_tick(self, tick: dict) -> bool:
        """
        Kite (and this app's own dispatcher) can deliver a tick whose
        `exchange_timestamp` is the LAST TRADE time, not the moment it
        was actually received — most commonly an immediate snapshot
        Kite sends right when you subscribe to an instrument, carrying
        whatever the last traded price/time was even if that was hours
        ago (e.g. the prior session's closing print, if you subscribe
        after market hours). Every strategy here treats
        `exchange_timestamp` as "now" for entry/exit/day-boundary
        decisions — with NO other check for whether that's actually
        current, a strategy created after hours could see this one
        stale tick, find its own `entry_time <= tick time < force_exit
        _time` (a snapshot near a real close often lands well inside
        that window), and — with `catch_up_late_entry=True` (the
        default) — place a real "catch up" entry using an hours-old
        price, the moment it's deployed. calendar_btst has it worse:
        no force_exit_time-style upper bound at all, so ANY stale tick
        past entry_time enters, regardless of how late.

        The fix doesn't need real wall-clock "now" as a separately
        chosen reference — it needs the SAME clock domain the tick
        itself is already in. `exchange_timestamp` is naive LOCAL
        SYSTEM TIME (see this class's `_created_at_local` comment for
        exactly why — it's whatever `datetime.fromtimestamp()` produces
        on the machine running the Kite client, not portably "IST"), so
        `created_at` is deliberately derived the identical way rather
        than via a hardcoded offset, keeping both sides of this
        comparison in the same domain on any server regardless of its
        system timezone. The actual test is airtight either way: a tick
        genuinely reflecting live trading can NEVER claim to be from
        before this deployment existed. So: if `exchange_timestamp` is
        earlier than this deployment's own `created_at`, it is
        DEFINITELY stale — reject it outright, for every strategy,
        before it ever reaches on_tick. A deployment resumed/restarted
        long after its original creation is completely unaffected
        (created_at never changes after the initial deploy, so this
        only ever matters for the first few ticks after a brand-new
        deployment, exactly where the bug lives).
        """
        ts = tick.get("exchange_timestamp")
        return ts is not None and ts < self._created_at_local

    async def _run(self) -> None:
        my_tokens = self.tokens
        try:
            while True:
                ticks = await self._queue.get()
                relevant = [t for t in ticks if t.get("instrument_token") in my_tokens]
                if not relevant or self.strategy is None:
                    continue
                for t in relevant:
                    if self._is_stale_pre_creation_tick(t):
                        logger.info(
                            "%s: ignoring a tick timestamped before this "
                            "deployment's own creation (%s < %s) — a stale "
                            "snapshot, not a live trading signal",
                            self.deployment_name, t.get("exchange_timestamp"),
                            self._created_at_local,
                        )
                        continue
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
        if executed_at.tzinfo is None:
            # Every strategy passes the TICK's own exchange_timestamp
            # here — naive, per Kite's own convention (see
            # _is_stale_pre_creation_tick's docstring: naive LOCAL
            # SYSTEM TIME, i.e. correct only insofar as the server's own
            # system tz is set to IST, the same implicit assumption
            # entry_time/force_exit_time config values already make
            # everywhere else). Postgres has no way to know that on its
            # own — a naive datetime.timestamptz insert is silently
            # treated as UTC, not IST, meaning every fill's stored/
            # displayed time came out ~5.5h AHEAD of when it actually
            # happened (confirmed against a real Postgres instance: a
            # naive 10:00:09 IST fill was coming back out, and
            # redisplaying, as 15:30:09). datetime.astimezone() on a
            # naive value is documented to presume system-local time and
            # convert correctly from there — the exact fix, no manual
            # offset, correct on any server regardless of its configured
            # tz (verified: on an IST-tz machine, naive 10:00:09 ->
            # 04:30:09 UTC -> redisplays as the true 10:00:09 IST).
            executed_at = executed_at.astimezone(timezone.utc)
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
