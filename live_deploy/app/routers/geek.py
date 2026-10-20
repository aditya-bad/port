"""Geek mode API: strategy-specific analytics for one deployment (see
app/geek/). Read-only. 404 for a strategy with no Geek module, so the
UI only offers Geek mode where there's something behind it."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from .. import clock
from ..db import queries
from ..geek import GEEK

logger = logging.getLogger("live_deploy.geek")

router = APIRouter(prefix="/v2/deployments", tags=["geek"])


@router.get("/{deployment_id}/geek/{view}")
async def get_view(deployment_id: UUID, view: str, request: Request):
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    mod = GEEK.get(dep["strategy_name"])
    if mod is None:
        raise HTTPException(404, f"Geek mode isn't available for {dep['strategy_name']}")
    build = mod.VIEWS.get(view)
    if build is None:
        raise HTTPException(404, f"No such Geek view: {view}")
    runner = request.app.state.deployment_manager.get_runner(deployment_id)
    ctx = mod.Ctx(pool=pool, dep=dict(dep), runner=runner, now=clock.now_ist(),
                  candles=getattr(request.app.state, "candles", None), params=dict(request.query_params))
    return await build(ctx)
