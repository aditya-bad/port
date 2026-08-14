"""live_deploy — GET /strategies, backed by the registry in app.strategies.registry.

Also owns the admin enabled/disabled toggle (queries.strategy_settings)
layered on top of the registry: registration itself stays pure Python/
import-time (see registry.py's own docstring), this is only the
"should this show up in the catalog / be deployable" flag on top of
that, persisted so it survives a restart.
"""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request

from ..db import queries
from ..strategies.registry import is_registered, list_strategies

router = APIRouter(prefix="/strategies", tags=["strategies"])


class StrategyEnabledIn(BaseModel):
    enabled: bool


async def fetch_strategies(pool) -> list[dict]:
    """The registry-plus-enabled-map payload behind GET /strategies,
    pulled out on its own so app.state.cache's background loop can call
    it directly. See app/cache.py for why this is cached at all."""
    strategies = list_strategies()
    enabled_map = await queries.get_strategy_enabled_map(pool)
    for s in strategies:
        s["enabled"] = enabled_map.get(s["name"], True)   # True: see ensure_strategy_settings — a row should always exist, this is just defense in depth
    return strategies


@router.get("")
async def get_strategies(request: Request):
    """
    Every registered strategy, each annotated with `enabled` — the
    Strategy Catalog's own "Browse" tab filters this to enabled-only
    client-side; its "Admin Options" tab shows all of them with a
    toggle. One endpoint, not two, since the underlying data (registry
    + enabled map) is small and this is read far more often than the
    enabled flag ever changes.
    """
    return await request.app.state.cache.get("strategies")


@router.put("/{strategy_name}/enabled")
async def set_strategy_enabled(strategy_name: str, payload: StrategyEnabledIn, request: Request):
    """
    Admin toggle. 404s for a name nothing has ever registered — toggling
    a strategy that doesn't exist isn't a meaningful action, unlike
    creating a DEPLOYMENT for an unregistered strategy_name (allowed
    elsewhere, see DeploymentManager.create_deployment's own docstring)
    where the deployment itself is still a real, useful row even before
    matching code exists.
    """
    if not is_registered(strategy_name):
        raise HTTPException(404, f"No such strategy: {strategy_name!r}")
    await queries.set_strategy_enabled(request.app.state.db_pool, strategy_name, payload.enabled)
    # Refresh now -- Catalog.switchTab/openClearAllModal etc. read this
    # cache right after a toggle and a stale read would make the button
    # look like it didn't do anything.
    await request.app.state.cache.refresh_now("strategies")
    return {"strategy_name": strategy_name, "enabled": payload.enabled}
