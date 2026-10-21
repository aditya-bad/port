"""/v2 endpoints for the Linck UI. See app/v2/__init__.py."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import queries
from ..deployments.schemas import DeploymentCreate
from ..strategies.registry import is_registered
from . import actions, book, catalog, preferences, session

router = APIRouter(prefix="/v2", tags=["v2"])

_BOOK_TTL = 15.0   # seconds; the book only changes when something closes


async def _refresh_caches(request: Request) -> None:
    cache = request.app.state.cache
    await asyncio.gather(
        cache.refresh_now("deployments"), cache.refresh_now("positions_open"), cache.refresh_now("trades_recent"),
    )
    request.app.state.v2_book = None


@router.get("/book")
async def get_book(request: Request, since: Optional[datetime] = None):
    """Closed trades and daily realized P&L for every deployment."""
    cached = getattr(request.app.state, "v2_book", None)
    if since is None and cached and time.monotonic() - cached[0] < _BOOK_TTL:
        return cached[1]
    pool = request.app.state.db_pool
    trades, daily = await asyncio.gather(book.list_trades(pool, since), book.list_daily(pool, since))
    out = {"trades": trades, "daily": daily}
    if since is None:
        request.app.state.v2_book = (time.monotonic(), out)
    return out


@router.get("/session")
async def get_session(request: Request):
    """Today's P&L lane and event markers for every deployment."""
    return await session.build_session(request.app.state.db_pool)


@router.get("/strategy-health")
async def get_strategy_health(request: Request, fresh: bool = False):
    """Deployments drifting from how they normally trade (app/strategy_health.py):
    {checked_at, deployments: {id: [findings]}}. The background check's
    last result, or a fresh one (`fresh`, or before the first check)."""
    from .. import strategy_health
    cached = getattr(request.app.state, "strategy_health", None)
    if fresh or not cached:
        now = datetime.now(strategy_health.IST)
        cached = {"checked_at": now.isoformat(), "deployments": await strategy_health.check_all(request.app.state.db_pool, now)}
        request.app.state.strategy_health = cached
    return cached


@router.get("/strategies")
async def get_strategies(request: Request):
    enabled = await queries.get_strategy_enabled_map(request.app.state.db_pool)
    out = catalog.catalog()
    for s in out:
        s["enabled"] = enabled.get(s["name"], True)
    return out


def _user_id(request: Request) -> Optional[UUID]:
    uid = request.session.get("user_id") if "session" in request.scope else None
    return UUID(uid) if uid else None


@router.get("/preferences")
async def get_preferences(request: Request):
    return await preferences.get(request.app.state.db_pool, _user_id(request))


@router.put("/preferences")
async def put_preferences(payload: dict, request: Request):
    uid = _user_id(request)
    if uid is None:
        raise HTTPException(400, "Preferences are saved per signed-in user")
    return await preferences.merge(request.app.state.db_pool, uid, payload)


class CloseEverythingIn(BaseModel):
    confirm: str


@router.post("/close-everything")
async def close_everything(payload: CloseEverythingIn, request: Request):
    """Close every open leg everywhere, shorts before longs, then pause
    every running deployment. Needs the typed confirmation the UI asks for."""
    if payload.confirm.strip().upper() != "CLOSE ALL":
        raise HTTPException(400, "Type CLOSE ALL to confirm")
    result = await actions.close_everything(request.app.state.deployment_manager)
    await _refresh_caches(request)
    return result


class CreateIn(DeploymentCreate):
    start: Literal["paused", "now"] = "paused"
    tags: list[str] = Field(default_factory=list)


@router.post("/deployments", status_code=201)
async def create_deployment(payload: CreateIn, request: Request):
    """Same checks as POST /deployments, plus a start mode and tags."""
    pool = request.app.state.db_pool
    if await queries.get_deployment_by_name(pool, payload.deployment_name) is not None:
        raise HTTPException(409, f"A deployment named {payload.deployment_name!r} already exists")
    if is_registered(payload.strategy_name) and not await queries.is_strategy_enabled(pool, payload.strategy_name):
        raise HTTPException(400, f"Strategy {payload.strategy_name!r} is disabled")
    if payload.tags:
        known = {t["name"] for t in await queries.list_tags(pool)}
        missing = [t for t in payload.tags if t not in known]
        if missing:
            raise HTTPException(400, f"Unknown tag(s): {', '.join(missing)}")
    base = DeploymentCreate(**payload.model_dump(exclude={"start", "tags"}))
    try:
        row, registered = await actions.create_deployment(
            request.app.state.deployment_manager, base, payload.start, payload.tags,
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    await _refresh_caches(request)
    return {"id": str(row["id"]), "status": row["status"], "strategy_registered": registered}


@router.post("/deployments/{deployment_id}/restart")
async def restart_deployment(deployment_id: UUID, request: Request):
    """Start the strategy of a deployment that's active but not running
    (see DeploymentManager.find_not_running). 409 with the strategy's own
    error if it still can't start."""
    manager = request.app.state.deployment_manager
    try:
        await manager.restart(deployment_id)
    except KeyError:
        raise HTTPException(404, "No such deployment")
    except ValueError as e:
        raise HTTPException(409, str(e))
    await _refresh_caches(request)
    return {"ok": True, "running": True}
