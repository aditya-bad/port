"""
live_deploy — FastAPI service.

Step 1: the live data dispatcher — a single Kite Connect WebSocket
connection, fanned out to any number of downstream consumers.

Step 2: persistent, resumable paper-trading deployments. Every
deployment (one strategy + one config = one isolated,
independently-tracked "instance") gets its own DeploymentRunner, its own
positions/cash/trade history in Postgres (Neon), and survives the server
being turned off overnight — on startup, every deployment still marked
'active' in the DB is reloaded with its last-known positions and resumes
reacting to live ticks, no replay needed, because every fill was durably
committed before the process ever stopped.

Step 3 (this revision): onboarding — the Kite login/re-login flow (see
routers/kite_auth.py; access_token expires daily and this is what "next
day, re-upload it" turns into: click a button, no restart), a strategy
registry so available strategies are discoverable and deployable by
name (app/strategies/registry.py), and a single-page UI
(static/index.html) tying all of it together: connection status, deploy
a strategy, watch running deployments, positions, trades, reports.

Step 7 (this revision): application-level authentication — this whole
service (every router, /sse/ticks, and the UI) now sits behind a single
shared secret (config.json's app_auth_secret), enforced by ASGI
middleware (see app/auth.py) rather than per-route dependencies, so
anything added later is protected by default instead of needing to
remember to opt in. Kite's own /kite/callback redirect and the
/auth/login endpoint itself are the only two paths that stay reachable
unauthenticated — see app/auth.py's module docstring for exactly why.

Actual strategy TRADING LOGIC is still not built — "once infra is
ready, I'll tell you the strategies." Deployments can be created against
any strategy_name right now; they just won't trade until something
registers under that name.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import strategies  # noqa: F401 — importing runs every @register_strategy in it
from .strategies.registry import list_strategies
from .auth import AuditLogMiddleware, AuthMiddleware, HostAwareSessionMiddleware
from .broadcaster import Broadcaster
from .cache import AggregateCache
from .config import load_config, load_tokens
from .db import queries
from .db.migrate import run_migrations
from .db.pool import close_pool, create_pool
from .deployments.manager import DeploymentManager
from .dispatcher import LiveDataDispatcher
from .notifications import is_push_configured
from .routers import admin as admin_router
from .routers import aggregate as aggregate_router
from .routers import auth as auth_router
from .routers import deployments as deployments_router
from .routers import health as health_router
from .routers import instruments as instruments_router
from .routers import kite_auth as kite_auth_router
from .routers import notifications as notifications_router
from .routers import strategies as strategies_router
from .routers import tags as tags_router
from .routers import ux_summary as ux_summary_router
from .routers.aggregate import (
    fetch_portfolio_equity_curve, fetch_positions_open, fetch_strategy_leaderboard, fetch_trades_recent,
)
from .routers.deployments import fetch_deployments_list
from .routers.health import check_db_health
from .routers.strategies import fetch_strategies

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("live_deploy")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Loaded at IMPORT time, not inside the startup event, on purpose:
# add_middleware() must run before Starlette builds its middleware
# stack (the first ASGI message this app ever handles, including the
# lifespan startup event itself) — and AuthMiddleware/
# HostAwareSessionMiddleware both need app_auth_secret right away.
# Reused (not re-read) inside startup() below for everything else
# load_config() provides.
config = load_config()

app = FastAPI(title="NiftyShop Live Deploy — Data Dispatcher + Paper Trading")

app.include_router(health_router.router)
app.include_router(instruments_router.router)
app.include_router(deployments_router.router)
app.include_router(kite_auth_router.router)
app.include_router(strategies_router.router)
app.include_router(auth_router.router)
app.include_router(aggregate_router.router)
app.include_router(tags_router.router)
app.include_router(notifications_router.router)
app.include_router(admin_router.router)
app.include_router(ux_summary_router.router)

# Middleware order matters: add_middleware() makes the MOST RECENTLY
# added one OUTERMOST (it runs first on the way in, last on the way
# out) — see app/auth.py's HostAwareSessionMiddleware docstring. Adding
# AuthMiddleware first (innermost), then AuditLogMiddleware, then
# HostAwareSessionMiddleware last (outermost) means:
#   1. The session cookie is decoded (HostAwareSessionMiddleware) before
#      anything else sees the request.
#   2. AuditLogMiddleware can read that decoded session for user
#      attribution, AND still observes the real final status code even
#      when AuthMiddleware (further in) rejects the request with a 401
#      — see AuditLogMiddleware's own docstring for why both matter.
app.add_middleware(AuthMiddleware, secret=config["app_auth_secret"], static_dir=STATIC_DIR)
app.add_middleware(AuditLogMiddleware)
app.add_middleware(HostAwareSessionMiddleware, secret_key=config["app_auth_secret"])


@app.on_event("startup")
async def startup() -> None:
    tokens = load_tokens()

    # ── Database: connect, migrate (idempotent — safe on every boot) ──
    db_pool = await create_pool(config["database_url"])
    applied = await run_migrations(db_pool)
    if applied:
        logger.info("Applied migrations: %s", applied)
    app.state.db_pool = db_pool

    # Every currently-registered strategy gets a settings row (enabled=
    # true by default) on every boot — new strategies added to the
    # codebase since the last restart pick up a row automatically;
    # existing rows (including anything an admin already disabled) are
    # left untouched. See queries.ensure_strategy_settings's own
    # docstring.
    await queries.ensure_strategy_settings(db_pool, [s["name"] for s in list_strategies()])
    app.state.kite_config = config   # api_key/api_secret, for the login/callback router
    app.state.app_auth_secret = config["app_auth_secret"]   # session-signing key + X-API-Key value — see auth router

    # ── First-boot user bootstrap: config.json's app_auth_secret used to
    # BE the login credential; now it's only a one-time seed for the
    # first user's password, so upgrading from the old single-secret
    # world doesn't lock anyone out. Runs every boot but is a no-op past
    # the very first one — `users` has at least one row from then on.
    if await queries.count_users(db_pool) == 0:
        bootstrap_username = "admin"
        await queries.create_user(
            db_pool, bootstrap_username,
            auth_router.hash_password(config["app_auth_secret"]),
        )
        logger.info(
            "No users existed yet — created initial user '%s' from config.json's "
            "app_auth_secret (log in with that as the password, then change it via "
            "POST /auth/change-password).",
            bootstrap_username,
        )

    # ── Kite session: DB (freshest, survives a same-day restart) wins,
    # config.json's access_token (if any) is only a first-ever-boot
    # fallback. Neither existing is a normal, expected state — not an
    # error — the dispatcher just starts in needs_login mode and the UI
    # shows a login prompt.
    kite_session = await queries.get_kite_session(db_pool)
    initial_token = (kite_session["access_token"] if kite_session else None) \
        or config.get("access_token")

    # Set the instant shutdown() starts running — the ONLY way a
    # /ws/ticks handler blocked in queue.get() (nothing to forward,
    # e.g. market closed) finds out the server wants to exit. See
    # ws_ticks's own docstring for the full story.
    app.state.shutdown_event = asyncio.Event()

    broadcaster = Broadcaster()
    dispatcher = LiveDataDispatcher(
        api_key=config["api_key"],
        tokens=tokens,
        tick_mode=config["tick_mode"],
        broadcaster=broadcaster,
        initial_access_token=initial_token,
    )
    loop = asyncio.get_running_loop()
    dispatcher.bind_loop(loop)
    app.state.broadcaster = broadcaster
    app.state.dispatcher = dispatcher

    # ── In-app real-time alerts: a SECOND, separate Broadcaster instance
    # (same fan-out class as ticks, own subscriber set) carrying
    # deployment events (fills, pause/resume/stop, strategy errors) out
    # to /ws/events. Kept apart from the tick broadcaster on purpose —
    # DeploymentRunner subscribes to the tick one to feed its own
    # strategy, and would wrongly try to treat an event payload as a
    # tick if the two streams were merged.
    event_broadcaster = Broadcaster()
    app.state.event_broadcaster = event_broadcaster

    # ── Aggregate-read cache: GET /deployments, /positions, /trades/
    # recent, /strategies were reported taking 3-6s to load on every
    # single page view — a flat per-round-trip cost against Neon, not
    # query complexity (see app/cache.py's own docstring for the full
    # reasoning). Each key is refreshed once here (so the very first
    # real request never pays a cold-cache penalty) and then on its own
    # background loop for the life of the process; mutating endpoints
    # (and, via DeploymentManager below, every strategy fill too) call
    # cache.refresh_now(key) right after their own write so an action
    # the user just took — or a trade a strategy just made — is
    # reflected immediately, not after the next tick. Set up BEFORE the
    # DeploymentManager below, which needs a live cache reference to
    # pass to it.
    # Every key below except db_health/user_session_versions is now
    # fully covered by a cache.refresh_now() call at its own exact
    # mutation point (deploy/pause/resume/stop/flatten/fill for
    # deployments+positions_open+trades_recent+strategy_leaderboard;
    # the strategy-enabled toggle for strategies; each snapshot round
    # for portfolio_equity_curve — see DeploymentManager/
    # routers/deployments.py/routers/strategies.py). These interval
    # values are therefore no longer "how fresh can this be," they're
    # purely a DEFENSIVE BACKSTOP against a missed refresh_now() call
    # (a bug, a crash mid-mutation) — deliberately much longer than the
    # tight few-seconds intervals this used to run at, since the
    # frontend no longer blindly polls on a matching short timer either
    # (see index.html's own _AUTO_REFRESH_VIEWS comment): the primary
    # "the UI just updated" path is now /ws/events firing the instant a
    # mutation happens, not either side polling the other.
    cache = AggregateCache()
    cache.register("deployments", lambda: fetch_deployments_list(db_pool, dispatcher), interval=90.0)
    cache.register("positions_open", lambda: fetch_positions_open(db_pool, dispatcher), interval=90.0)
    cache.register("trades_recent", lambda: fetch_trades_recent(db_pool), interval=90.0)
    cache.register("portfolio_equity_curve", lambda: fetch_portfolio_equity_curve(db_pool), interval=90.0)
    cache.register("strategies", lambda: fetch_strategies(db_pool), interval=120.0)
    cache.register("strategy_leaderboard", lambda: fetch_strategy_leaderboard(db_pool), interval=90.0)
    # 15s: /health is polled by the frontend every 5s (pollHealth() in
    # index.html) -- without this it was the one endpoint left paying a
    # live Neon round trip (700-800ms) on every single call, the exact
    # per-round-trip cost every other hot read used to pay before it
    # got cached. A DB outage still surfaces within one interval, which
    # is the right trade for a status indicator.
    cache.register("db_health", lambda: check_db_health(db_pool), interval=15.0)
    # Read by AuthMiddleware on every single authenticated request (see
    # app/auth.py's _session_ok) -- has to be a cached in-memory read,
    # not a live query per request, or this revocation check would add
    # a Neon round trip to literally everything the app serves. The
    # periodic 10s refresh is just a backstop; the actual "revoke this
    # user's sessions right now" cases (change-password, explicit
    # logout-everywhere -- see routers/auth.py) call
    # cache.refresh_now() themselves right after bumping the version,
    # same mutation-triggered-refresh pattern every other cached key
    # here already uses.
    cache.register("user_session_versions", lambda: queries.get_all_session_versions(db_pool), interval=10.0)
    await cache.start()
    app.state.cache = cache

    # ── Deployment lifecycle: resume everything still 'active' ────────
    # push_config: only handed to the manager (and, through it, every
    # runner it starts) if a real VAPID keypair is actually configured —
    # see app/notifications.py's own is_push_configured. None here means
    # every DeploymentRunner's notify_execution silently skips the push
    # step entirely (still records + toasts as normal), same "feature
    # entirely optional, never a startup requirement" reasoning as the
    # VAPID config fields themselves (see config.py's own comment).
    push_config = config if is_push_configured(config) else None
    manager = DeploymentManager(
        db_pool, broadcaster, dispatcher, cache=cache, event_broadcaster=event_broadcaster,
        push_config=push_config,
    )
    resumed = await manager.load_active_on_startup()
    app.state.deployment_manager = manager

    # ── Equity-curve snapshots: periodic, not per-tick — see
    # DeploymentManager.snapshot_loop's own docstring for why.
    app.state.snapshot_task = asyncio.create_task(
        manager.snapshot_loop(), name="deployment-snapshot-loop",
    )

    # ── Post-market state checkpoint: once a day, not per-tick, not tied
    # to any pause/stop event — a second, independent safety net for
    # strategy state (SuperTrend internals, pivots, ...) on top of the
    # one runner.stop() already takes — see DeploymentManager.
    # post_market_dump_loop's own docstring for the full reasoning.
    app.state.post_market_dump_task = asyncio.create_task(
        manager.post_market_dump_loop(), name="post-market-dump-loop",
    )

    logger.info(
        "live_deploy started — %d static token(s), mode=%s, %d deployment(s) resumed, "
        "kite_session=%s",
        len(tokens), config["tick_mode"], resumed,
        "loaded" if initial_token else "needs login",
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    # FIRST thing, before any other teardown: wake up every /ws/ticks
    # handler currently blocked waiting for a tick that may never come
    # (see ws_ticks's own docstring) so they can exit cleanly instead of
    # leaving Uvicorn's graceful shutdown hanging on "Waiting for
    # background tasks to complete" forever, needing a second Ctrl+C
    # (which then surfaces as an ugly unhandled-CancelledError
    # traceback from the forced cancellation — this fixes the root
    # cause, not just the symptom).
    app.state.shutdown_event.set()

    # Cancel the snapshot loop before tearing down runners/DB pool below
    # — it's a plain infinite-sleep loop with no cleanup of its own, so
    # cancellation is all it needs.
    snapshot_task = app.state.snapshot_task
    snapshot_task.cancel()
    try:
        await snapshot_task
    except asyncio.CancelledError:
        pass

    # Same for the post-market dump loop — also a plain infinite-sleep
    # loop with no cleanup of its own. Note this does NOT replace the
    # dump every runner.stop() below already takes for itself as part of
    # manager.shutdown_all() — this just stops the SEPARATE daily-clock
    # background task, it isn't the thing doing today's shutdown-time
    # dump.
    post_market_dump_task = app.state.post_market_dump_task
    post_market_dump_task.cancel()
    try:
        await post_market_dump_task
    except asyncio.CancelledError:
        pass

    # Stop the aggregate-read cache's background refresh loops before
    # the DB pool closes below — otherwise a loop mid-sleep can wake up
    # after close_pool() and try to query a closed pool.
    await app.state.cache.stop()

    # Stop deployment runner tasks first (they hold broadcaster
    # subscriptions and DB connections) — this does NOT change any
    # deployment's status in the DB, so 'active' ones resume
    # automatically on next startup.
    manager: DeploymentManager = app.state.deployment_manager
    await manager.shutdown_all()

    dispatcher: LiveDataDispatcher = app.state.dispatcher
    dispatcher.stop()
    # dispatcher.stop() schedules the close onto Kite's reactor thread
    # (see dispatcher.py's docstring on why it can't call it directly) —
    # give that thread a brief moment to actually run it before the
    # process exits, since it's a daemon thread that gets killed
    # abruptly otherwise. Not required for correctness (the OS closes
    # the socket regardless) — just a cleaner shutdown from Kite's side.
    await asyncio.sleep(0.1)

    await close_pool(app.state.db_pool)
    logger.info("live_deploy stopped")


def _json_default(obj):
    """
    json.dumps' `default=` hook — handles the ONE type this app's own
    payloads are actually known to carry that isn't natively JSON-safe:
    a raw `datetime` (a tick's own `exchange_timestamp`, straight from
    kiteconnect, never converted to a string anywhere upstream of this
    — see runner.py's own extensive comments on why that's naive local
    time, not portably IST). Deployment-event payloads (_record_event)
    already call `.isoformat()` themselves before this ever sees them,
    so in practice this only ever fires for ticks — kept generic rather
    than tick-specific in case something else datetime-shaped shows up
    here later.

    THIS WAS LIKELY THE REAL ROOT CAUSE of the "WebSocket dies within
    100-300ms, every time" issue chased at length earlier this session
    (blamed on Tailscale Serve's own WebSocket handling — a real,
    separately-confirmed upstream bug, but probably not what was
    actually happening here): the old `/ws/ticks` handler's
    `_forward_ticks()` called `websocket.send_json(ticks)` with NO
    try/except around it, and its own cleanup path did
    `await asyncio.gather(forward_task, ..., return_exceptions=True)`
    — which swallows ANY exception from that task completely silently,
    no log line, no traceback, nothing. A raw `datetime` in a REAL
    Kite tick (present in "full"/"quote" mode, which is what this app
    defaults to) would have hit this exact TypeError on the very FIRST
    real tick after connecting, silently closing the connection every
    time — which looks identical to "the connection just dies for no
    reason," the exact symptom driving that whole investigation. The
    fake ticks used in every test written during that investigation
    never included `exchange_timestamp` at all, so none of them ever
    exercised this path — a real gap in test realism, not a subtle bug.
    This new SSE version doesn't swallow exceptions the same way,
    which is WHY this became visible as a real traceback instead of a
    silent, mysterious reconnect loop the moment it hit production.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


async def _sse_stream(request: Request, broadcaster: Broadcaster, shutdown_event: asyncio.Event):
    """
    Shared generator behind /sse/ticks and /sse/events — see each
    endpoint's own docstring for what's actually flowing through it.

    WAS a WebSocket (/ws/ticks, /ws/events) — replaced after discovering
    those connections were dying within ~100-300ms. Neither direction of
    this stream ever needed to be bidirectional — every consumer (the
    ticker bar, Detail's live prices, toast notifications) only ever
    RECEIVES, never sends anything back — so a WebSocket's own defining
    feature (full-duplex) was dead weight the whole time. SSE (a single
    long-lived plain HTTP response the server keeps writing to) is what
    "the server pushes events, the client only ever listens" is
    actually FOR — no protocol upgrade handshake, no custom reconnect
    logic needed (EventSource retries on its own, natively, browser-
    side) — strictly simpler code for something that was always one-way.
    See _json_default's own docstring above for what turned out to be
    the actual, likely root cause of the dying-connection symptom that
    prompted this switch in the first place — found only because THIS
    version's error handling doesn't hide exceptions the way the old
    WebSocket cleanup path did.

    A heartbeat comment line (`: heartbeat`) is sent whenever nothing
    real has gone out for 15s — SSE comment lines are ignored by
    EventSource's own parsing (never reach onmessage), but keep bytes
    flowing over the connection, which is what stops some proxies from
    treating a quiet-but-healthy connection as idle and killing it.

    A single payload that fails to serialize is logged and SKIPPED,
    not left to kill the whole connection — deliberately more resilient
    than the old WebSocket version's failure mode (which silently
    dropped the entire CONNECTION on exactly this), and a genuine safety
    net against whatever future payload shape nobody's thought of yet,
    not just today's known datetime case.
    """
    queue = await broadcaster.subscribe()
    try:
        while not shutdown_event.is_set():
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            try:
                data = json.dumps(payload, default=_json_default)
            except Exception:
                logger.exception(
                    "_sse_stream: failed to JSON-serialize a payload — "
                    "skipping it, connection stays open"
                )
                continue
            yield f"data: {data}\n\n"
    finally:
        # Reached either via the loop's own exit (shutdown_event) or via
        # this generator being cancelled out from under it — Starlette's
        # StreamingResponse does exactly that the moment it notices the
        # client disconnected, same signal a WebSocket's own disconnect
        # event used to give us, so this one finally: block is the
        # entire cleanup story now (no separate disconnect-watcher task
        # needed, unlike the old WebSocket version above).
        await broadcaster.unsubscribe(queue)


_SSE_HEADERS = {
    # No caching, obviously — and X-Accel-Buffering:no is the standard
    # nginx-family signal to stream this straight through rather than
    # buffering the whole response before sending anything (irrelevant
    # for uvicorn itself, but harmless and correct if any nginx-like
    # proxy ever ends up in front of this instead of/alongside Tailscale
    # Serve).
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@app.get("/sse/ticks")
async def sse_ticks(request: Request):
    """
    Downstream requesters connect HERE — not to Kite. No matter how many
    clients are connected at once, they're all fed from the ONE upstream
    Kite connection owned by LiveDataDispatcher; connecting here never
    opens a new Kite session. See _sse_stream's own docstring for why
    this is SSE and not a WebSocket.
    """
    broadcaster: Broadcaster = request.app.state.broadcaster
    shutdown_event: asyncio.Event = request.app.state.shutdown_event
    return StreamingResponse(
        _sse_stream(request, broadcaster, shutdown_event),
        media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@app.get("/sse/events")
async def sse_events(request: Request):
    """
    Real-time in-app alerts — every deployment event (a fill, a
    pause/resume/stop, a strategy error) as soon as it's recorded,
    pushed to every connected browser tab. Subscribed to
    app.state.event_broadcaster instead of app.state.broadcaster —
    deliberately NOT reusing the tick stream for this: mixing event
    payloads into the tick stream would mean every existing tick
    consumer (including every DeploymentRunner feeding its own
    strategy) would need to start filtering out non-tick messages. See
    _sse_stream's own docstring for why this is SSE and not a WebSocket.
    """
    broadcaster: Broadcaster = request.app.state.event_broadcaster
    shutdown_event: asyncio.Event = request.app.state.shutdown_event
    return StreamingResponse(
        _sse_stream(request, broadcaster, shutdown_event),
        media_type="text/event-stream", headers=_SSE_HEADERS,
    )


# UI — served last so it doesn't shadow the API routes above (StaticFiles
# with html=True serves index.html at "/" and falls through to the
# filesystem for anything else under /static-mounted paths).
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui")


if __name__ == "__main__":
    # Convenience entrypoint: run from inside live_deploy/ as
    #   python -m app.main
    # The primary/recommended way to run this service is still:
    #   uvicorn app.main:app --reload --port 8000
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
