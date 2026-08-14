"""live_deploy — GET /health."""

from fastapi import APIRouter, Request

router = APIRouter()


async def check_db_health(pool) -> bool:
    """The DB-round-trip half of GET /health, pulled out on its own so
    app.state.cache's background loop can call it directly (see
    app/cache.py). Before this, /health ran a live `SELECT 1` against
    Neon on EVERY call — the one endpoint left paying the same flat
    per-round-trip cost every other hot read used to pay before it got
    cached, made worse by the frontend polling /health every 5s
    (pollHealth() in index.html). A DB outage still surfaces within one
    cache interval (15s) instead of instantly, which is the right
    trade for a status indicator, not a gate on any real decision."""
    try:
        await pool.fetchval("SELECT 1")
        return True
    except Exception:
        return False


@router.get("/health")
async def health(request: Request):
    dispatcher = request.app.state.dispatcher
    manager = request.app.state.deployment_manager
    db_ok = await request.app.state.cache.get("db_health")

    return {
        "status": "ok" if db_ok else "degraded",
        "database_connected": db_ok,
        "running_deployments": len(manager.runners),
        **dispatcher.status,
    }
