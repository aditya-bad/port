"""
live_deploy — deployment CRUD, lifecycle control, and reporting endpoints.

One strategy, many deployments: strategy_name is just a label on each
deployment row. POST /deployments with the same strategy_name and a
different deployment_name creates a second, fully independent
deployment — own positions, own cash, own trade history, never
overlapping with the first (enforced at the DB layer, see
db/migrations/0001_init.sql).
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from ..db import queries
from ..deployments.schemas import (
    DeploymentCreate, DeploymentOut, EventOut, LotsPage, PositionOut, ReportOut,
)
from ..strategies.registry import is_registered

router = APIRouter(prefix="/deployments", tags=["deployments"])


def _annotate(row: dict) -> dict:
    row = dict(row)
    row["strategy_registered"] = is_registered(row["strategy_name"])
    return row


@router.post("", response_model=DeploymentOut, status_code=201)
async def create_deployment(payload: DeploymentCreate, request: Request):
    manager = request.app.state.deployment_manager
    existing = await queries.get_deployment_by_name(
        request.app.state.db_pool, payload.deployment_name
    )
    if existing is not None:
        raise HTTPException(409, f"deployment_name {payload.deployment_name!r} already exists")
    try:
        row, _strategy_registered = await manager.create_deployment(payload)
    except Exception as e:
        raise HTTPException(400, str(e))
    return _annotate(row)


@router.get("", response_model=list[DeploymentOut])
async def list_deployments(request: Request, status: str | None = None):
    rows = await queries.list_deployments(request.app.state.db_pool, status=status)
    return [_annotate(r) for r in rows]


@router.get("/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(deployment_id: UUID, request: Request):
    row = await queries.get_deployment(request.app.state.db_pool, deployment_id)
    if row is None:
        raise HTTPException(404, "No such deployment")
    return _annotate(row)


@router.get("/{deployment_id}/positions", response_model=list[PositionOut])
async def get_positions(deployment_id: UUID, request: Request, status: str | None = "open"):
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")

    rows = await queries.list_positions(pool, deployment_id, status=status)
    dispatcher = request.app.state.dispatcher

    out = []
    for r in rows:
        d = dict(r)
        if d["status"] == "open":
            price = dispatcher.last_prices.get(int(d["instrument_token"]))
            d["current_price"] = price
            if price is not None:
                qty, avg = float(d["qty"]), float(d["avg_entry_price"])
                d["unrealized_pnl"] = (price - avg) * qty if d["side"] == "long" \
                    else (avg - price) * qty
        out.append(d)
    return out


@router.get("/{deployment_id}/trades", response_model=LotsPage)
async def get_trades(deployment_id: UUID, request: Request, offset: int = 0, limit: int = 200):
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    lots, total = await queries.list_lots(pool, deployment_id, offset, limit)
    return {"total": total, "offset": offset, "lots": [dict(l) for l in lots]}


@router.get("/{deployment_id}/events", response_model=list[EventOut])
async def get_events(deployment_id: UUID, request: Request, offset: int = 0, limit: int = 200):
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    rows = await queries.list_events(pool, deployment_id, offset, limit)
    return [dict(r) for r in rows]


@router.get("/{deployment_id}/report", response_model=ReportOut)
async def get_report(deployment_id: UUID, request: Request):
    pool = request.app.state.db_pool
    report = await queries.build_report(pool, deployment_id)
    if not report:
        raise HTTPException(404, "No such deployment")
    return report


@router.post("/{deployment_id}/pause")
async def pause_deployment(deployment_id: UUID, request: Request):
    manager = request.app.state.deployment_manager
    try:
        await manager.pause(deployment_id)
    except KeyError:
        raise HTTPException(404, "No such deployment")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"status": "paused"}


@router.post("/{deployment_id}/resume", response_model=DeploymentOut)
async def resume_deployment(deployment_id: UUID, request: Request):
    manager = request.app.state.deployment_manager
    try:
        row = await manager.resume(deployment_id)
    except KeyError:
        raise HTTPException(404, "No such deployment")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return _annotate(row)


@router.post("/{deployment_id}/stop")
async def stop_deployment(deployment_id: UUID, request: Request, force_close: bool = False):
    manager = request.app.state.deployment_manager
    try:
        await manager.stop(deployment_id, force_close=force_close)
    except KeyError:
        raise HTTPException(404, "No such deployment")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"status": "stopped"}
