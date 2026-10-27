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
import json
import logging
import math
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import asyncpg

from ..db import queries
from ..slippage import resolve_book_price
from .. import shadow
from .health import DROP_WORDS, BlockedWatch, Breaker, Issues, TickFilter, describe
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
        push_config: Optional[dict] = None,
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
        # Optional (app.state.kite_config's own vapid_* keys, or None if
        # push isn't configured at all — see app/notifications.py's own
        # is_push_configured). Used ONLY by notify_execution below; the
        # broader per-fill event recording (_record_event) never sends a
        # push notification regardless of this.
        self.push_config = push_config
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
        # When this runner last processed a tick (wall clock, UTC), and when
        # it was built: the health monitor (app/ops_health.py) flags one
        # that's running but has stopped acting on prices that still flow.
        self.created_at = datetime.now(timezone.utc)
        self.last_tick_at: Optional[datetime] = None

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
        # candles a candle strategy completed, not yet stored (_keep_candles)
        self._candles: list[dict] = []
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
        self._last_saved_state: Optional[str] = None   # see checkpoint_state

        # Intraday safety net (see _intraday_guard): the configured
        # square-off time, if any, and when the last sweep was tried.
        self._square_off_at: Optional[time] = _parse_hhmm(self.config.get("force_exit_time"))
        self._last_sweep_try: Optional[datetime] = None

        # The underlying's session so far (open, high, low, last) and the
        # previous session's last price, per signal token: the market
        # context stamped on every fill (see market_context). Logging only.
        self._session: dict[int, dict] = {}
        self._last_index_tick: Optional[datetime] = None   # IST, for _note_gap

        # What keeps it safe and how it says so (app/deployments/health.py):
        # ticks checked before the strategy sees them, what it reports
        # about itself, a strategy that keeps failing called less often,
        # a rule blocked for a while raised.
        self.ticks = TickFilter()
        self.issues = Issues(pool, self.deployment_id, self._record_event, self.deployment_name)
        self.breaker = Breaker()
        self.blocked = BlockedWatch()
        self.filter_ticks = True        # the repair engine's replays turn these off (app/repair/engine.py)
        self.use_breaker = True
        self.resync_enabled = True
        self.started = strategy is None   # the strategy's on_start has completed
        self._check_legs = False          # a failure may have left a trade half-done
        self._legs_checked_at: Optional[datetime] = None
        self._mismatch_seen = 0
        self._state_fails = 0
        # "now" for the protections above (when to call a resting strategy
        # again): the wall clock live; a simulation sets it to its own time
        self.clock_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

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
            await self.start_strategy()
        self._task = asyncio.create_task(self._run(), name=f"runner:{self.deployment_name}")
        logger.info(
            "Runner started: %s (%s, %s) — %d open position(s), tokens=%s",
            self.deployment_name, self.strategy_name, self.mode,
            len(self.open_positions), sorted(self.tokens),
        )

    START_TIMEOUT = 120   # seconds for on_start (it may fetch candles and the instrument list from Kite)
    TICK_TIMEOUT = 90     # seconds for one on_tick: longer means a call hung

    async def start_strategy(self) -> None:
        """Start the strategy (its on_start), within START_TIMEOUT. A
        failure raises, as it always has: the manager rolls a create or
        resume back to paused and says why, and its watchdog retries a
        deployment that should be running (DeploymentManager.
        watchdog_loop). A strategy never starts cold over state it
        couldn't read: load_state raising fails the start."""
        await asyncio.wait_for(self.strategy.on_start(self), self.START_TIMEOUT)
        self.started = True
        agg = getattr(self.strategy, "aggregator", None)
        if agg is not None and hasattr(agg, "on_complete"):
            agg.on_complete = self._candles.append
        await self.checkpoint_state()
        await self.check_legs("start")

    async def _keep_candles(self) -> None:
        """Store the candles the strategy completed on this tick, as it
        built them from its own ticks (migration 0040): the end-of-day
        check replays it on these, not on Kite's minutes, which can put a
        candle's close a few points away. Never reaches the strategy."""
        if not self._candles:
            return
        rows, self._candles[:] = list(self._candles), []
        try:
            await queries.record_strategy_candles(self.pool, self.deployment_id, rows)
        except Exception:
            logger.exception("%s: could not store its candles", self.deployment_name)

    async def opened_on(self, day) -> bool:
        """Whether this deployment opened any position on IST `day` (the
        book, not the strategy's memory)."""
        return await self.positions_opened_on(day) > 0

    async def positions_opened_on(self, day, side: Optional[str] = None) -> int:
        """How many positions this deployment opened on IST `day`
        ("short"/"long" for one side only), from the book."""
        return int(await self.pool.fetchval(
            "SELECT count(*) FROM positions WHERE deployment_id = $1 AND (opened_at AT TIME ZONE 'Asia/Kolkata')::date = $2"
            " AND ($3::text IS NULL OR side = $3)", self.deployment_id, day, side))

    async def last_exit_at(self) -> Optional[datetime]:
        """When this deployment last closed a position, from the book."""
        return await self.pool.fetchval(
            "SELECT max(closed_at) FROM positions WHERE deployment_id = $1 AND closed_at IS NOT NULL", self.deployment_id)

    async def report(self, kind: str, title: str, detail: Optional[str] = None, **kw) -> None:
        """For a strategy to report a problem it handled but you should
        know about — an entry it had to delay or skip, a leg it couldn't
        price, anything the Repair page should look at. Keyword args:
        severity ("warning", "critical", "info"), day (the trading day
        it's about), key (one open issue per key; default `kind`), data,
        ttl (a timedelta: it clears itself unless reported again by
        then). Never raises. Resolve it with `await runner.resolve(key)`."""
        kw.setdefault("source", "strategy")
        await self.issues.report(kind, title, detail, **kw)

    async def resolve(self, key: str) -> None:
        await self.issues.resolve(key)

    async def _report(self, kind: str, title: str, detail: Optional[str] = None, **kw) -> None:
        kw.setdefault("source", "runner")
        await self.issues.report(kind, title, detail, **kw)

    async def note_decision(self, ts: datetime, rule: str, outcome: str, summary: str,
                            fault: bool = False, missed: bool = False) -> None:
        """Every check a strategy notes (StrategyBase.decide). A block
        caused by a fault: a trade lost for good (`missed`) is raised at
        once; otherwise a rule blocked for 10 minutes running is, cleared
        when it next gets through. Blocks by design (a cap, a cut-off)
        are never raised."""
        if outcome == "blocked" and missed:
            await self.report("missed_trade", f"A trade that was due didn't happen ({rule.replace('_', ' ')})", summary,
                              key=f"missed:{rule}:{ts.date()}", day=ts.date(), data={"rule": rule, "at": ts.isoformat()})
        elif outcome == "blocked" and fault:
            hit = self.blocked.blocked(rule, ts, summary)
            if hit is not None:
                await self.report("blocked", f"“{rule.replace('_', ' ')}” has been blocked since {hit[0]:%H:%M}", hit[1],
                                  key=f"blocked:{rule}", day=ts.date(), ttl=timedelta(minutes=30))
        elif outcome != "blocked" and self.blocked.through(rule):
            await self.issues.resolve(f"blocked:{rule}")

    async def stop(self) -> None:
        self._running = False
        if self.strategy is not None:
            # A failure here (strategy hook, DB write) must never leave
            # the task below running: the deployment would show paused
            # while its strategy kept trading.
            try:
                await self.strategy.on_stop(self)
                await self.dump_state()
            except Exception:
                logger.exception("%s: on_stop/state dump failed while stopping — stopping anyway",
                                 self.deployment_name)
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
        try:
            await queries.save_deployment_state(self.pool, self.deployment_id, self._clean_state(state))
        except Exception as e:
            logger.exception("%s: persisting strategy state failed", self.deployment_name)
            await self._report("state_unsaved", "The strategy's state couldn't be saved",
                               f"{describe(e)[0]}. A restart would resume from the last saved state.", severity="warning")
            return
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

    async def record_strategy_error(self, message: str, metadata: Optional[dict] = None) -> None:
        """For a strategy to flag a problem it handled itself (shows in
        the deployment's Activity and as an alert), without raising."""
        await self._record_event("strategy_error", message=message, metadata=metadata)

    async def record_decision(
        self, ts: datetime, rule: str, outcome: str, summary: str, data: Optional[dict] = None,
    ) -> None:
        """For a strategy to note a check that didn't trade ("no_action"
        or "blocked") for Geek mode's Decisions view. Display-only: a
        failure here is logged and never reaches the strategy."""
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=self._IST)   # tick time: naive IST, see _fill
        try:
            await queries.record_decision(self.pool, self.deployment_id, ts, rule, outcome, summary, data)
        except Exception:
            logger.exception("%s: could not record decision %s/%s", self.deployment_name, rule, outcome)

    async def notify_execution(self, side: str, message: str, metadata: Optional[dict] = None) -> None:
        """
        Call this EXACTLY ONCE per logical strategy execution — an
        entry or an exit — regardless of how many individual buy()/
        sell() fills that execution actually involved. A straddle's
        CE+PE legs opening together are ONE entry, not two; the same
        strategy adjusting a leg later is its own separate call, not
        folded into the original entry. `side` is "entry" or "exit" —
        used both for the event_type recorded (execution_entry /
        execution_exit) and the notification's own title.

        This is a DELIBERATELY different, narrower hook than
        _record_event's own fill_buy/fill_sell (called automatically by
        buy()/sell() for every single fill, for the Activity tab's
        complete audit trail — unaffected by any of this). Two real
        problems this separation solves:
          1. A straddle showing 2 separate "Sell" toasts/pushes for
             what is, from a trading-decision standpoint, ONE action.
          2. Wanting mobile push notifications for real entries/exits
             specifically, WITHOUT also pushing for every fill, pause,
             resume, stop, or strategy_error (those still record +
             toast via _record_event exactly as before — just never
             trigger a mobile push).

        Gated by this deployment's own notifications_enabled flag,
        checked FRESH from the DB on every call (never cached on this
        runner in memory) — a toggle flipped mid-session via PATCH
        /deployments/{id} takes effect on the very next execution, no
        restart needed. When disabled, this is a complete no-op: no
        deployment_events row, no toast, no push — the underlying
        buy()/sell() fills themselves are entirely unaffected either
        way, they always record regardless.
        """
        if side not in ("entry", "exit"):
            raise ValueError(f"side must be 'entry' or 'exit', got {side!r}")
        if not await queries.get_deployment_notifications_enabled(self.pool, self.deployment_id):
            return

        await self._record_event(f"execution_{side}", message=message, metadata=metadata)

        if self.push_config is None:
            return
        from .. import notifications   # deferred -- see notifications.send_push_for_all's own docstring on why
        title = f"{self.deployment_name} — {'Entry' if side == 'entry' else 'Exit'}"
        try:
            await notifications.send_push_for_all(self.pool, self.push_config, title, message)
        except Exception:
            # A push failure must NEVER be allowed to look like a
            # trading-logic failure to whatever strategy code called
            # this — logged and swallowed, same principle as every
            # other non-critical side effect in this class (cache
            # refresh callbacks, etc).
            logger.exception("%s: push notification failed for %s execution — trading unaffected",
                             self.deployment_name, side)

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

    # A tick whose exchange_timestamp lags the current wall clock by more
    # than this is treated as a stale subscribe-time snapshot, never a
    # live trading signal — see _is_stale_tick's own docstring. Generous
    # on purpose: real ticks during continuous trading arrive within
    # milliseconds of their own exchange_timestamp, so even a couple of
    # minutes of slack (network hiccups, a burst of queued ticks after a
    # reconnect) never falsely rejects a genuinely live one — while a
    # subscribe-time snapshot from hours or days earlier is nowhere near
    # this close.
    _STALE_TICK_MAX_AGE = timedelta(minutes=5)

    # Real trading hours, IST — a tick timestamped outside this window is
    # NEVER a live, tradeable price, no matter how close its own
    # exchange_timestamp is to the current wall clock (see _is_stale_tick's
    # own docstring, case 3, for the CONFIRMED incident this exists for:
    # a pre-open snapshot at 08:06 IST, timestamped close enough to "now"
    # to sail straight past the staleness check above, triggered a real
    # roll off a price that was never actually tradeable). 15:40, not
    # 15:30 — confirmed with the strategy owner: NSE F&O's real close
    # moved to 15:40 under the 2026 Closing Auction Session rules (the
    # auction itself runs 15:15-15:35), so 15:30 would reject genuinely
    # live ticks in the last 10 minutes of a real session.
    _MARKET_OPEN_TIME = time(9, 15)
    _MARKET_CLOSE_TIME = time(15, 40)

    def _is_stale_tick(self, tick: dict) -> bool:
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
        past entry_time enters, regardless of how late. strangle_monthly_v2
        turns out to have the identical gap in its own _maybe_enter (only
        `t < entry_time` gates it, nothing on the late side either) — this
        is a property of the tick-consumption layer, not any one strategy,
        so it belongs here rather than patched into each one separately.

        Three DISTINCT ways a tick can be untrustworthy, all checked here:

        1. Older than this deployment's own creation — airtight on its
           own: a tick genuinely reflecting live trading can NEVER claim
           to be from before this deployment existed. Catches a
           brand-new deployment's very first snapshot tick.

        2. Older than the current wall clock by more than
           `_STALE_TICK_MAX_AGE` — catches the case #1 alone misses: an
           ALREADY-EXISTING deployment (created_at long in the past)
           that gets paused/resumed — or the whole app restarted — well
           after market hours. Its very first tick after resume is once
           again a subscribe-time snapshot, this time carrying today's
           own last-trade time from hours earlier, which is well past
           `created_at` and so #1 alone lets it straight through.
           CONFIRMED live: 4 strangle_monthly_v2 deployments, fixed and
           resumed at ~21:40 IST (hours after the market's own close),
           each immediately placed a real "initial_entry" off a stale
           tick carrying that afternoon's last-trade price/time — this
           check is exactly what closes that gap.

        3. Timestamped outside real trading hours (`_MARKET_OPEN_TIME`..
           `_MARKET_CLOSE_TIME`) — checks #1/#2 catch a tick that's old,
           this one catches a tick that's TIMELY but still never a real
           trading signal. CONFIRMED live: a login at 08:06 IST delivered
           a subscribe-time snapshot for several instruments whose OWN
           exchange_timestamp read close enough to "now" (08:06ish) to
           pass check #2 outright, well within `_STALE_TICK_MAX_AGE` —
           but 08:06 IST is before the market has genuinely opened
           (pre-open is 09:00-09:15, continuous trading starts 09:15), so
           no real, tradeable price exists at that moment at all.
           strangle_monthly_v2's EOD check saw a real (if economically
           meaningless) premium comparison off it and rolled a leg for
           real. Age-based staleness alone can never catch this — the
           tick genuinely WAS "recent," just recent-and-still-not-live.

        Neither of checks #1/#2 needs real wall-clock "now" as a
        separately chosen reference — both need the SAME clock domain the
        tick itself is already in. `exchange_timestamp` is naive LOCAL
        SYSTEM TIME (see this class's `_created_at_local` comment for
        exactly why — it's whatever `datetime.fromtimestamp()` produces
        on the machine running the Kite client, not portably "IST"), so
        both `created_at` and "now" here are deliberately derived the
        identical way (`datetime.now()`, no tz arg, i.e. naive local
        system time) rather than via a hardcoded offset, keeping every
        side of both comparisons in the same domain on any server
        regardless of its system timezone. Check #3 relies on that same
        existing assumption (naive local time IS IST, the same implicit
        assumption entry_time/force_exit_time config values already make
        everywhere else) rather than introducing a new one.
        """
        ts = tick.get("exchange_timestamp")
        if ts is None:
            return False
        if ts < self._created_at_local:
            return True
        if (datetime.now() - ts) > self._STALE_TICK_MAX_AGE:
            return True
        t = ts.time()
        return t < self._MARKET_OPEN_TIME or t > self._MARKET_CLOSE_TIME

    def _stale_tick_reason(self, tick: dict) -> str:
        """Human-readable version of whichever _is_stale_tick condition
        actually fired, for the log line in _run() below -- three
        genuinely different situations (age since creation, age since
        now, outside trading hours) all used to share one vague
        "a subscribe-time snapshot" message, which stopped being accurate
        the moment case 3 (market hours) was added -- that one isn't
        necessarily a subscribe-time snapshot at all, just a timely tick
        that arrived at a meaningless moment."""
        ts = tick.get("exchange_timestamp")
        if ts is None:
            return "no exchange_timestamp"
        if ts < self._created_at_local:
            return "older than this deployment's own creation"
        if (datetime.now() - ts) > self._STALE_TICK_MAX_AGE:
            return "older than the current wall clock by more than %s" % self._STALE_TICK_MAX_AGE
        t = ts.time()
        if t < self._MARKET_OPEN_TIME or t > self._MARKET_CLOSE_TIME:
            return f"timestamped outside real trading hours ({self._MARKET_OPEN_TIME}-{self._MARKET_CLOSE_TIME} IST)"
        return "unknown"   # unreachable in practice — _is_stale_tick already returned True to get here

    async def _run(self) -> None:
        my_tokens = self.tokens
        try:
            while True:
                ticks = await self._queue.get()
                relevant = [t for t in ticks if t.get("instrument_token") in my_tokens]
                if not relevant or self.strategy is None:
                    continue
                for t in relevant:
                    if self._is_stale_tick(t):
                        logger.info(
                            "%s: ignoring an untrustworthy tick (exchange_timestamp=%s, "
                            "now=%s) — %s, not a live trading signal",
                            self.deployment_name, t.get("exchange_timestamp"),
                            datetime.now(), self._stale_tick_reason(t),
                        )
                        continue
                    await self.process_tick(t)
        except asyncio.CancelledError:
            raise

    async def process_tick(self, t: dict) -> None:
        """One live tick: checked, the intraday safety net, the strategy
        (if it's started and not resting after repeated failures), a
        state checkpoint, and the strategy's legs checked against the
        book when something failed (and every 5 minutes). Nothing here
        may raise into the tick loop."""
        if self.filter_ticks:
            t, why = self.ticks.check(t)
            if why is not None:
                await self._bad_tick(why)
            if t is None:
                return
        self.last_tick_at = datetime.now(timezone.utc)
        try:
            self._note_session(t)
            await self._note_gap(t)
        except Exception:
            logger.exception("%s: noting the session failed — continuing", self.deployment_name)
        try:
            await self._intraday_guard(t)
        except Exception:
            logger.exception("%s: intraday guard raised — continuing", self.deployment_name)
        if self.strategy is None:
            return
        if not self.started:
            return
        now = self.clock_now()
        if self.use_breaker and not self.breaker.allow(now):
            return
        try:
            await asyncio.wait_for(self.strategy.on_tick(self, t), self.TICK_TIMEOUT)
        except Exception as e:
            await self._strategy_failed(e, t)
        else:
            if self.breaker.succeeded() and self.issues.is_open("crash"):
                await self.issues.resolve("crash")
        await self._keep_candles()
        await self.checkpoint_state()
        ts = t.get("exchange_timestamp")
        if self._check_legs:
            await self.check_legs("failure")
        elif isinstance(ts, datetime) and (self._legs_checked_at is None or ts - self._legs_checked_at >= timedelta(minutes=5)
                                           or ts < self._legs_checked_at):
            self._legs_checked_at = ts
            await self.check_legs("periodic")

    async def _resync(self, lost: set, phantom: set, labels: dict) -> bool:
        """The strategy lost track of what it holds (a trade failed half-way,
        a leg recorded before its fill went through): restart it from the
        book — a fresh instance through on_start, exactly what a restart
        does, which every strategy is tested to resume from with the same
        trades (tests/strategies/test_crash_recovery.py). True if it then
        agrees with the book."""
        name = lambda x: self.open_positions.get(x, {}).get("symbol") or labels.get(x) or str(x)  # noqa: E731
        what = "; ".join(p for p in (
            f"it had lost track of {', '.join(sorted(name(x) for x in lost))}" if lost else "",
            f"it thought it held {', '.join(sorted(name(x) for x in phantom))}" if phantom else "") if p)
        old = self.strategy
        try:
            await self._reload_positions()
            fresh = type(old)()
            await asyncio.wait_for(fresh.on_start(self), self.START_TIMEOUT)
            tracked = fresh.tracked_tokens()
        except Exception:
            logger.exception("%s: re-syncing the strategy from the book failed", self.deployment_name)
            self.strategy = old
            return False
        if tracked is None or {int(x) for x in tracked} != set(self.open_positions):
            return False
        self.strategy = fresh
        self._mismatch_seen = 0
        self._last_saved_state = None
        await self.checkpoint_state()
        logger.warning("%s: re-synced the strategy from the book (%s)", self.deployment_name, what)
        await self._report("resynced", "The strategy was re-synced from the book",
                           f"After a failure {what}. It was restarted from the book, as a restart would, and now "
                           f"manages exactly what's open. Worth a look on the Repair page.",
                           severity="warning", day=self.clock_now().astimezone(self._IST).date(),
                           data={"lost": sorted(lost), "phantom": sorted(phantom)}, ttl=timedelta(hours=12))
        if self.issues.is_open("legs_mismatch"):
            await self.issues.resolve("legs_mismatch")
        return True

    async def _strategy_failed(self, e: BaseException, t: dict) -> None:
        sig, where = describe(e) if not isinstance(e, asyncio.TimeoutError) else \
            (f"on_tick took longer than {self.TICK_TIMEOUT}s (a call that hung?)", "")
        now = self.clock_now()
        skip = self.breaker.failed(now, sig)
        if self.breaker.should_log(sig, now):
            logger.error("%s: the strategy raised on a tick — %s%s", self.deployment_name, sig,
                         f" ({where})" if where else "", exc_info=(type(e), e, e.__traceback__))
            await self._record_event("strategy_error", message=f"{sig}{f' — {where}' if where else ''}",
                                     metadata={"where": where, "in_a_row": self.breaker.fails})
        self._check_legs = True
        ts = t.get("exchange_timestamp")
        if self.breaker.fails >= 2 or skip is not None:
            nxt = f" It's called again at {skip.astimezone(self._IST):%H:%M}." if skip else ""
            await self._report(
                "crash", "The strategy keeps failing",
                f"{sig}{f' (at {where})' if where else ''}. {self.breaker.fails} tick(s) in a row, {self.breaker.total} in all.{nxt}"
                f"{' The intraday safety net still squares off at the cut-off.' if self.mode == 'intraday' else ''}",
                severity="critical", data={"error": sig, "where": where, "in_a_row": self.breaker.fails, "total": self.breaker.total},
                day=ts.date() if isinstance(ts, datetime) else None)

    async def _bad_tick(self, why: str) -> None:
        if why in ("duplicate", "held"):   # a repeat carries nothing new; a held one may yet be confirmed
            return
        counts = {k: v for k, v in self.ticks.dropped.items() if k not in ("duplicate", "held")}
        await self._report(
            "bad_ticks", "Bad prices from the feed were dropped",
            "Not passed to the strategy: " + ", ".join(f"{n} {DROP_WORDS.get(k, k)}" for k, n in sorted(counts.items())) + ".",
            severity="warning", data=counts, ttl=timedelta(minutes=30))

    async def check_legs(self, why: str) -> None:
        """The legs the strategy believes it holds (tracked_tokens) against
        the book's open positions: a leg in the book it has lost track of
        is never managed (and, positional, never closed); one it thinks it
        holds that isn't there makes it act on nothing. Reported — the
        book is what's real, and a person decides what to do — and
        cleared when they agree again. After a failure it's checked at
        once; otherwise a mismatch must show twice, 5 minutes apart."""
        self._check_legs = False
        if self.strategy is None or not self.started:
            return
        try:
            tracked = self.strategy.tracked_tokens()
        except Exception:
            logger.exception("%s: tracked_tokens() raised", self.deployment_name)
            return
        if tracked is None:
            return
        labels = {int(k): v for k, v in tracked.items() if k is not None} if isinstance(tracked, dict) else {}
        tracked = {int(x) for x in tracked if x is not None}
        book = set(self.open_positions)
        lost, phantom = book - tracked, tracked - book
        if not lost and not phantom:
            self._mismatch_seen = 0
            if self.issues.is_open("legs_mismatch"):
                await self.issues.resolve("legs_mismatch")
            return
        self._mismatch_seen += 1
        if why == "periodic" and self._mismatch_seen < 2:
            return
        if why in ("failure", "periodic") and self.resync_enabled and await self._resync(lost, phantom, labels):
            return
        names = lambda toks: ", ".join(sorted(self.open_positions.get(x, {}).get("symbol") or labels.get(x) or str(x) for x in toks))  # noqa: E731
        parts = []
        if lost:
            parts.append(f"open in the book but not managed by the strategy: {names(lost)}"
                         + (" (the intraday safety net closes it at the cut-off)" if self.mode == "intraday" else " — nothing will close it"))
        if phantom:
            parts.append(f"the strategy thinks it holds {names(phantom)}, which isn't open in the book")
        await self._report("legs_mismatch", "The strategy and the book disagree on what's held", "; ".join(parts) + ".",
                           severity="critical", data={"lost": sorted(lost), "phantom": sorted(phantom), "why": why},
                           day=self.clock_now().astimezone(self._IST).date())

    # ── Crash safety: persist strategy state as it changes ────────────
    #
    # A strategy's resumable state (StrategyBase.get_persistable_state)
    # used to be saved only on a clean stop and once after the close. A
    # crash, an OOM kill or a container restart skipped that, and the
    # restarted strategy resumed from a stale snapshot — e.g. "not
    # entered today" and entered a second time. Now it is saved the
    # moment it changes (compared as JSON, so an unchanged tick costs no
    # database write), so a restart at any point resumes from the state
    # as of the last processed tick.

    async def checkpoint_state(self) -> None:
        if self.strategy is None:
            return
        try:
            state = self.strategy.get_persistable_state()
        except Exception:
            logger.exception("%s: get_persistable_state() raised — not checkpointed", self.deployment_name)
            return
        if state is None:
            return
        try:
            blob = json.dumps(self._clean_state(state), sort_keys=True, default=str, allow_nan=False)
        except (TypeError, ValueError):
            logger.exception("%s: strategy state isn't JSON-serializable — not checkpointed", self.deployment_name)
            await self._report("state_unsaved", "The strategy's state can't be saved",
                               "It isn't plain data. A restart would resume from the last state saved.", severity="warning")
            return
        if blob == self._last_saved_state:
            return
        try:
            await queries.save_deployment_state(self.pool, self.deployment_id, json.loads(blob))
            self._last_saved_state = blob
            if self._state_fails:
                self._state_fails = 0
                await self.issues.resolve("state_unsaved")
        except Exception as e:
            self._state_fails += 1
            logger.exception("%s: saving strategy state failed — will retry on the next tick", self.deployment_name)
            if self._state_fails >= 3:
                await self._report("state_unsaved", "The strategy's state isn't being saved",
                                   f"{describe(e)[0]} ({self._state_fails} tries). A restart would resume from the last state saved.",
                                   severity="warning")

    @staticmethod
    def _clean_state(v):
        """A number that isn't finite (NaN, ∞ — an indicator a bad price got
        into) saved as None: JSON can't hold it, and the database refuses it."""
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: DeploymentRunner._clean_state(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [DeploymentRunner._clean_state(x) for x in v]
        return v

    # ── Intraday safety net ───────────────────────────────────────────
    #
    # An intraday deployment must be flat at its square-off time and
    # must never carry a leg into the next session. Each strategy closes
    # its own legs at force_exit_time, but only the legs it is tracking
    # in memory; a leg it lost track of (a close that raised mid-
    # flatten, a restart after the close, a day rollover that reset its
    # leg list) stayed open in the database with nothing left to close
    # it. This guard checks the database's view (open_positions) on
    # every live tick, before the strategy sees the tick:
    #   1. any position opened on an EARLIER trading day is closed now
    #      ("carried_over_close"), before the new day's entry logic runs;
    #   2. once the tick time is _SWEEP_GRACE past force_exit_time
    #      (the strategy gets the first chance to exit on its own), any
    #      position still open is closed ("square_off_sweep").
    # Positional deployments are untouched: holding overnight is the point.

    _SWEEP_GRACE = timedelta(seconds=60)
    _SWEEP_RETRY = timedelta(seconds=30)
    _IST = ZoneInfo("Asia/Kolkata")

    async def _intraday_guard(self, tick: dict) -> None:
        if self.mode != "intraday" or not self.open_positions:
            return
        ts = tick.get("exchange_timestamp")
        if ts is None:
            return
        carried = [
            pos for pos in self.open_positions.values()
            if pos.get("opened_at") is not None and pos["opened_at"].astimezone(self._IST).date() < ts.date()
        ]
        if carried:
            await self.close_open_positions(ts, "carried_over_close", tokens={int(p["instrument_token"]) for p in carried})
        if self._square_off_at is None or not self.open_positions:
            return
        due = datetime.combine(ts.date(), self._square_off_at) + self._SWEEP_GRACE
        if ts < due:
            return
        now = datetime.now()
        if self._last_sweep_try is not None and now - self._last_sweep_try < self._SWEEP_RETRY:
            return
        self._last_sweep_try = now
        await self.close_open_positions(ts, "square_off_sweep")

    async def _exit_price(self, pos: dict) -> tuple[float, str]:
        return await self.exit_price(int(pos["instrument_token"]), pos["symbol"], float(pos["avg_entry_price"]))

    async def exit_price(self, token: int, symbol: str, entry: float) -> tuple[float, str]:
        """The best price to close a leg at when it must close now: the
        live tick cache, else one quote, else its last stored 1-minute
        candle (today's), else its entry — zero P&L on the leg, reported,
        and left for the Repair page to price from Kite's candles.
        (price, source): "live", "quote", "candle" or "entry". For every
        strategy's own forced exits too, so none closes at its entry price
        without saying so."""
        token = int(token)
        price = self.dispatcher.last_prices.get(token)
        if price is not None and isinstance(price, (int, float)) and math.isfinite(price) and price > 0:
            return float(price), "live"
        try:
            from ..options import OptionsResolver   # lazy: options imports the dispatcher layer
            quotes = await asyncio.wait_for(OptionsResolver(self.dispatcher).get_quotes([str(token)]), 5)
            q = next((q for q in quotes.values() if q.get("instrument_token") == token), None)
            if q and q.get("last_price"):
                return float(q["last_price"]), "quote"
        except Exception:
            pass
        try:
            row = await self.pool.fetchrow(
                "SELECT close, minute FROM candles_1m WHERE instrument_token = $1 AND minute >= $2 ORDER BY minute DESC LIMIT 1",
                token, datetime.now(timezone.utc) - timedelta(hours=18))
            if row and row["close"] and float(row["close"]) > 0:
                return float(row["close"]), "candle"
        except Exception:
            pass
        logger.warning(
            "%s: no price for %s — closing at its entry %.2f (zero P&L on this leg)",
            self.deployment_name, symbol, entry,
        )
        await self._report(
            "exit_at_entry", f"{symbol} was closed at its entry price",
            "No live price, quote or stored candle for it at the time, so it was booked at zero P&L. "
            "Its real exit price can be taken from Kite's candles on the Repair page.",
            severity="critical", key=f"exit_at_entry:{symbol}", day=self.clock_now().astimezone(self._IST).date(),
            data={"symbol": symbol, "token": token, "entry": entry})
        return float(entry), "entry"

    async def close_open_positions(
        self, executed_at: Optional[datetime], reason: str, tokens: Optional[set[int]] = None,
    ) -> dict:
        """Close every open position (or just `tokens`), each on its own:
        one failure is logged and recorded and never stops the rest.
        Shorts are bought back before longs are sold, so a hedge is
        never removed while its short is still open. Returns
        {"closed": [symbols], "failed": [symbols]}."""
        targets = [
            pos for tok, pos in list(self.open_positions.items())
            if tokens is None or tok in tokens
        ]
        targets.sort(key=lambda p: 0 if p["side"] == "short" else 1)
        closed, failed = [], []
        for pos in targets:
            token = int(pos["instrument_token"])
            if token not in self.open_positions:
                continue   # closed meanwhile
            price, source = await self._exit_price(pos)
            action = self.buy if pos["side"] == "short" else self.sell
            try:
                await action(
                    pos["symbol"], token, float(pos["qty"]), price, executed_at, reason=reason,
                    metadata={"trigger": reason, "action": "close", "price_source": source},
                )
                closed.append(pos["symbol"])
            except Exception as e:
                logger.exception("%s: %s could not close %s", self.deployment_name, reason, pos["symbol"])
                failed.append(pos["symbol"])
                await self._record_event(
                    "strategy_error", message=f"{reason}: could not close {pos['symbol']} ({e})",
                    metadata={"reason": reason, "symbol": pos["symbol"]},
                )
        if failed:
            await self._report(
                "square_off_failed", f"{reason.replace('_', ' ').capitalize()} couldn't close {', '.join(failed)}",
                "Tried again on the next ticks. Until then these legs stay open.",
                severity="critical", key=f"close_failed:{reason}", data={"failed": failed, "closed": closed},
                day=self.clock_now().astimezone(self._IST).date())
        elif self.issues.is_open(f"close_failed:{reason}") and not any(
                tokens is None or tok in tokens for tok in self.open_positions):
            await self.issues.resolve(f"close_failed:{reason}")
        if closed:
            logger.warning("%s: %s closed %s", self.deployment_name, reason, ", ".join(closed))
            await self._record_event(
                reason, message=f"{reason}: closed {len(closed)} position(s) the strategy had left open",
                metadata={"closed": closed, "failed": failed},
            )
            await self.notify_execution("exit", f"{reason}: closed {', '.join(closed)}", metadata={"reason": reason})
        return {"closed": closed, "failed": failed}

    # ── Trade execution — the only DB writes a strategy triggers ──────

    # ── Market context on every fill (Geek mode's Conditions view) ──────
    #
    # What the market looked like when a fill happened: the underlying's
    # gap, its range and move so far today, the time of day, and for an
    # option the strike's distance from spot and the days to expiry.
    # Added to the fill's metadata under "context" (never replacing one a
    # strategy set); logging only: computing it can never fail or change
    # a fill.

    FEED_GAP = timedelta(minutes=5)

    async def _note_gap(self, t: dict) -> None:
        """A stretch of the session with no tick for the underlying is put
        on that day's record (info: the feed monitor alerts on the outage
        itself) — what the strategy didn't see, and why it may have done
        less that day."""
        tok, ts = t.get("instrument_token"), t.get("exchange_timestamp")
        if tok not in self.tokens or not isinstance(ts, datetime):
            return
        at = ts.astimezone(self._IST) if ts.tzinfo else ts
        last, self._last_index_tick = self._last_index_tick, at
        if last is None or last.date() != at.date() or at - last < self.FEED_GAP or last.time() < time(9, 15):
            return
        mins = round((at - last).total_seconds() / 60)
        await self._report("feed_gap", f"No prices from {last:%H:%M} to {at:%H:%M}",
                           f"{mins} minutes without a tick for the underlying. Whatever the strategy would have "
                           "done in that window it didn't see: an entry or exit due then was skipped or came late.",
                           severity="info", key=f"feed_gap:{at.date()}:{last:%H%M}", day=at.date(), ttl=timedelta(hours=12),
                           data={"from": last.isoformat(), "to": at.isoformat(), "minutes": mins})

    def _note_session(self, t: dict) -> None:
        """The day so far from the ticks. A full-mode tick carries the
        exchange's own open/high/low and previous close (`ohlc`), which
        hold after a restart; without it the day is only what this runner
        has seen, and is marked partial if it started after the open."""
        tok, px, ts = t.get("instrument_token"), t.get("last_price"), t.get("exchange_timestamp")
        if tok not in self.tokens or px is None or ts is None:
            return
        at = ts.astimezone(self._IST) if ts.tzinfo else ts
        s = self._session.get(tok)
        if s is None or s["day"] != at.date():
            prev = s["last"] if s is not None else None
            s = self._session[tok] = {"day": at.date(), "open": px, "high": px, "low": px, "last": px, "prev_close": prev,
                                      "partial": at.time() > time(9, 16)}
        s["high"], s["low"], s["last"] = max(s["high"], px), min(s["low"], px), px
        o = t.get("ohlc") or {}
        if o.get("open"):
            s["open"], s["partial"] = float(o["open"]), False
            if o.get("high"):
                s["high"] = max(s["high"], float(o["high"]))
            if o.get("low"):
                s["low"] = min(s["low"], float(o["low"]))
        if o.get("close"):
            s["prev_close"] = float(o["close"])

    def market_context(self, at: datetime, metadata: Optional[dict] = None) -> dict:
        at = at.astimezone(self._IST) if at.tzinfo else at
        toks = self.config.get("instrument_tokens") or []
        tok = toks[0] if toks else None
        s = self._session.get(tok) if tok is not None else None
        if s is not None and s["day"] != at.date():
            s = None
        spot = s["last"] if s else self.dispatcher.last_prices.get(tok) if tok is not None else None
        r2 = lambda v: None if v is None else round(float(v), 2)  # noqa: E731
        ctx = {"spot": r2(spot), "minute": at.hour * 60 + at.minute - (9 * 60 + 15), "weekday": at.weekday()}
        if s:
            if s.get("partial"):   # high/low/open only since this runner started
                ctx["partial"] = True
            ctx.update({"day_open": r2(s["open"]), "day_high": r2(s["high"]), "day_low": r2(s["low"]),
                        "range_pts": r2(s["high"] - s["low"]),
                        "from_open_pct": r2((s["last"] - s["open"]) / s["open"] * 100) if s["open"] else None})
            if s["prev_close"]:
                ctx["prev_close"] = r2(s["prev_close"])
                ctx["gap_pct"] = r2((s["open"] - s["prev_close"]) / s["prev_close"] * 100)
        from .. import vix
        vtok = vix.state.get("token")
        vx = self.dispatcher.last_prices.get(vtok) if vtok is not None else None
        if vx:
            ctx["vix"] = r2(vx)
        m = metadata or {}
        strike = m.get("strike") or (m.get("target_basis") or {}).get("selected_strike")
        if strike is not None and spot:
            ctx["strike_from_spot"] = r2(float(strike) - float(spot))
        if m.get("expiry"):
            try:
                ctx["dte"] = (datetime.fromisoformat(str(m["expiry"])[:10]).date() - at.date()).days
            except ValueError:
                pass
        return ctx

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
        for what, v in (("price", price), ("quantity", qty)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                await self._report("bad_order", f"Refused a {action} of {symbol} at {what} {v!r}",
                                   f"A {what} has to be a positive number; the strategy produced {v!r}. Nothing was traded.",
                                   severity="critical", key=f"bad_order:{symbol}", data={"symbol": symbol, what: repr(v)})
                self._check_legs = True
                raise ValueError(f"refusing a {action} of {symbol}: {what} {v!r}")
        if executed_at.tzinfo is None:
            # Every strategy passes the TICK's own exchange_timestamp
            # here — naive, per Kite's own convention (see
            # _is_stale_tick's docstring: naive LOCAL
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
        if isinstance(metadata, dict) and "context" not in metadata:
            try:
                metadata = {**metadata, "context": self.market_context(executed_at, metadata)}
            except Exception:
                logger.exception("%s: market context failed — the fill goes ahead without it", self.deployment_name)
        try:
            book_price = await resolve_book_price(self.dispatcher, instrument_token, action, qty)
        except Exception:
            book_price = None   # slippage is an estimate: never a reason not to fill
        try:
            result = await queries.record_fill(
                self.pool, self.deployment_id, symbol, instrument_token, action,
                qty, price, executed_at, reason=reason, metadata=metadata,
                book_price=book_price,
            )
        except Exception as e:
            self._check_legs = True
            await self._report(
                "fill_failed", f"A {action} of {qty:g} {symbol} didn't go through",
                f"{describe(e)[0]}. If it was one leg of a trade, the others may have gone through without it — "
                f"the strategy's legs are checked against the book.",
                severity="critical", key=f"fill_failed:{symbol}",
                day=executed_at.astimezone(self._IST).date(),
                data={"symbol": symbol, "action": action, "qty": qty, "price": price, "reason": reason, "error": describe(e)[0]})
            raise
        if self.issues.is_open(f"fill_failed:{symbol}"):
            await self.issues.resolve(f"fill_failed:{symbol}")
        # would Kite have taken it? checked in the background (app/shadow.py)
        shadow.submit({"lot_id": result["lot_id"], "deployment_id": self.deployment_id, "executed_at": executed_at,
                       "symbol": symbol, "instrument_token": instrument_token, "action": action,
                       "qty": qty, "paper_price": price})

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
        if self.cash < 0:
            await self._report("cash_negative", "Cash went below zero",
                               f"Closing {symbol} cost more than the cash left: cash is now {self.cash:,.2f}. "
                               "Exits are never refused for cash; new entries are until it's back above their cost.",
                               severity="critical", data={"cash": self.cash})
        elif self.issues.is_open("cash_negative"):
            await self.issues.resolve("cash_negative")

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


def _parse_hhmm(value) -> Optional[time]:
    """"15:00" -> time(15, 0); None or anything unparsable -> None."""
    if not value:
        return None
    try:
        h, m = str(value).split(":")[:2]
        return time(int(h), int(m))
    except (TypeError, ValueError):
        return None
