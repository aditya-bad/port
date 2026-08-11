"""
live_deploy — FastAPI service.

Step 1 (this file): the live data dispatcher — a single Kite Connect
WebSocket connection, fanned out to any number of downstream consumers
via /ws/ticks. No matter how many clients connect there, Kite only ever
sees one connection from this process.

Step 2 (later, per instructions — not built yet): live strategies running
on top of that same tick stream. See app/strategies/ for the placeholder.

For now, beyond the dispatcher itself, this only exposes /health.
"""

import asyncio
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .broadcaster import TickBroadcaster
from .config import load_config, load_tokens
from .dispatcher import LiveDataDispatcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("live_deploy")

app = FastAPI(title="NiftyShop Live Deploy — Data Dispatcher")


@app.on_event("startup")
async def startup() -> None:
    config = load_config()
    tokens = load_tokens()

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
    logger.info("live_deploy started — %d token(s) configured, mode=%s",
               len(tokens), config["tick_mode"])


@app.on_event("shutdown")
async def shutdown() -> None:
    dispatcher: LiveDataDispatcher = app.state.dispatcher
    dispatcher.stop()
    logger.info("live_deploy stopped")


@app.get("/health")
async def health():
    dispatcher: LiveDataDispatcher = app.state.dispatcher
    return {"status": "ok", **dispatcher.status}


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
