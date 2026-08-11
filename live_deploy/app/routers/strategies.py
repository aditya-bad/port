"""live_deploy — GET /strategies, backed by the registry in app.strategies.registry."""

from fastapi import APIRouter

from ..strategies.registry import list_strategies

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("")
async def get_strategies():
    return list_strategies()
