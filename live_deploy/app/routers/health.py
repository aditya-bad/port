"""live_deploy — GET /health."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    dispatcher = request.app.state.dispatcher
    manager = request.app.state.deployment_manager

    db_ok = True
    try:
        await request.app.state.db_pool.fetchval("SELECT 1")
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database_connected": db_ok,
        "running_deployments": len(manager.runners),
        **dispatcher.status,
    }
