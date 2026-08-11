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

Actual strategy TRADING LOGIC is still not built — "once infra is
ready, I'll tell you the strategies." Deployments can be created against
any strategy_name right now; they just won't trade until something
registers under that name.
"""

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import strategies  # noqa: F401 — importing runs every @register_strategy in it
from .broadcaster import TickBroadcaster
from .config import load_config, load_tokens
from .db import queries
from .db.migrate import run_migrations
from .db.pool import close_pool, create_pool
from .deployments.manager import DeploymentManager
from .dispatcher import LiveDataDispatcher
from .routers import deployments as deployments_router
from .routers import health as health_router
from .routers import instruments as instruments_router
from .routers import kite_auth as kite_auth_router
from .routers import strategies as strategies_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("live_deploy")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="NiftyShop Live Deploy — Data Dispatcher + Paper Trading")

app.include_router(health_router.router)
app.include_router(instruments_router.router)
app.include_router(deployments_router.router)
app.include_router(kite_auth_router.router)
app.include_router(strategies_router.router)


@app.on_event("startup")
async def startup() -> None:
    config = load_config()
    tokens = load_tokens()

    # ── Database: connect, migrate (idempotent — safe on every boot) ──
    db_pool = await create_pool(config["database_url"])
    applied = await run_migrations(db_pool)
    if applied:
        logger.info("Applied migrations: %s", applied)
    app.state.db_pool = db_pool
    app.state.kite_config = config   # api_key/api_secret, for the login/callback router

    # ── Kite session: DB (freshest, survives a same-day restart) wins,
    # config.json's access_token (if any) is only a first-ever-boot
    # fallback. Neither existing is a normal, expected state — not an
    # error — the dispatcher just starts in needs_login mode and the UI
    # shows a login prompt.
    kite_session = await queries.get_kite_session(db_pool)
    initial_token = (kite_session["access_token"] if kite_session else None) \
        or config.get("access_token")

    broadcaster = TickBroadcaster()
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

    # ── Deployment lifecycle: resume everything still 'active' ────────
    manager = DeploymentManager(db_pool, broadcaster, dispatcher)
    resumed = await manager.load_active_on_startup()
    app.state.deployment_manager = manager

    logger.info(
        "live_deploy started — %d static token(s), mode=%s, %d deployment(s) resumed, "
        "kite_session=%s",
        len(tokens), config["tick_mode"], resumed,
        "loaded" if initial_token else "needs login",
    )


@app.on_event("shutdown")
async def shutdown() -> None:
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


@app.websocket("/ws/ticks")
async def ws_ticks(websocket: WebSocket):
    """
    Downstream requesters connect HERE — not to Kite. No matter how many
    clients are connected at once, they're all fed from the ONE upstream
    Kite connection owned by LiveDataDispatcher; connecting here never
    opens a new Kite session.
    """
    await websocket.accept()
    broadcaster: TickBroadcaster = websocket.app.state.broadcaster
    queue = await broadcaster.subscribe()
    try:
        while True:
            ticks = await queue.get()
            await websocket.send_json(ticks)
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unsubscribe(queue)


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
