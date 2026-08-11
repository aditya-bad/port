"""
live_deploy — FastAPI service.

Step 1: the live data dispatcher — a single Kite Connect WebSocket
connection, fanned out to any number of downstream consumers.

Step 2 (this revision): persistent, resumable paper-trading
deployments. Every deployment (one strategy + one config = one
isolated, independently-tracked "instance") gets its own DeploymentRunner,
its own positions/cash/trade history in Postgres (Neon), and survives
the server being turned off overnight — on startup, every deployment
still marked 'active' in the DB is reloaded with its last-known
positions and resumes reacting to live ticks, no replay needed, because
every fill was durably committed before the process ever stopped.

Step 3 (later, per instructions — not built yet): actual strategy logic.
See app/deployments/strategy_base.py for the interface strategies will
implement, and app/strategies/ for where they'll live.
"""

import asyncio
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .broadcaster import TickBroadcaster
from .config import load_config, load_tokens
from .db.migrate import run_migrations
from .db.pool import close_pool, create_pool
from .deployments.manager import DeploymentManager
from .dispatcher import LiveDataDispatcher
from .routers import deployments as deployments_router
from .routers import health as health_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("live_deploy")

app = FastAPI(title="NiftyShop Live Deploy — Data Dispatcher + Paper Trading")

app.include_router(health_router.router)
app.include_router(deployments_router.router)


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

    # ── Live data dispatcher (unchanged from step 1) ──────────────────
    broadcaster = TickBroadcaster()
    dispatcher = LiveDataDispatcher(
        api_key=config["api_key"],
        access_token=config["access_token"],
        tokens=tokens,
        tick_mode=config["tick_mode"],
        broadcaster=broadcaster,
    )
    loop = asyncio.get_running_loop()
    dispatcher.start(loop)
    app.state.broadcaster = broadcaster
    app.state.dispatcher = dispatcher

    # ── Deployment lifecycle: resume everything still 'active' ────────
    manager = DeploymentManager(db_pool, broadcaster, dispatcher)
    resumed = await manager.load_active_on_startup()
    app.state.deployment_manager = manager

    logger.info(
        "live_deploy started — %d token(s), mode=%s, %d deployment(s) resumed",
        len(tokens), config["tick_mode"], resumed,
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


if __name__ == "__main__":
    # Convenience entrypoint: run from inside live_deploy/ as
    #   python -m app.main
    # The primary/recommended way to run this service is still:
    #   uvicorn app.main:app --reload --port 8000
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
