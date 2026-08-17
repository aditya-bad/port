"""
live_deploy — deployment CRUD, lifecycle control, and reporting endpoints.

One strategy, many deployments: strategy_name is just a label on each
deployment row. POST /deployments with the same strategy_name and a
different deployment_name creates a second, fully independent
deployment — own positions, own cash, own trade history, never
overlapping with the first (enforced at the DB layer, see
db/migrations/0001_init.sql).
"""

import asyncio
import secrets
from uuid import UUID

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request

from ..db import queries
from ..deployments.schemas import (
    DeploymentCreate, DeploymentOut, DeploymentUpdate, EventOut, LotsPage,
    PnlDigestRow, PositionOut, ReportOut, SnapshotOut,
)
from ..strategies.registry import is_registered

router = APIRouter(prefix="/deployments", tags=["deployments"])

# The literal phrase Admin Options' "Clear All" dialog requires typing,
# on top of re-entering the app password — see clear_all's own
# docstring for why both gates exist.
CLEAR_ALL_CONFIRM_PHRASE = "DELETE ALL"


class ClearAllIn(BaseModel):
    password: str
    confirm: str


def _annotate(row: dict) -> dict:
    row = dict(row)
    row["strategy_registered"] = is_registered(row["strategy_name"])
    return row


def _mark_to_market(position: dict, dispatcher) -> float:
    """Unrealized P&L for ONE open position row, or 0.0 if there's no
    live price for it yet — same formula used by GET
    /deployments/{id}/positions, kept in one place so every pnl-
    enrichment path (single deployment, deployment list, cross-
    deployment aggregate) computes it identically."""
    price = dispatcher.last_prices.get(int(position["instrument_token"]))
    if price is None:
        return 0.0
    qty, avg = float(position["qty"]), float(position["avg_entry_price"])
    return (price - avg) * qty if position["side"] == "long" else (avg - price) * qty


async def _enrich_pnl_many(pool, dispatcher, rows: list[dict]) -> None:
    """Mutates each row in `rows` in place, adding realized_pnl/
    unrealized_pnl -- used by the deployment LIST endpoint. Two queries
    total (not one per deployment): every deployment's realized total,
    and every OPEN position across every deployment, grouped by
    deployment_id in Python for the mark-to-market sum."""
    realized_map = await queries.realized_pnl_by_deployment(pool)
    open_positions = await queries.list_all_positions(pool, status="open")
    unrealized_map: dict[str, float] = {}
    for p in open_positions:
        dep_id = str(p["deployment_id"])
        unrealized_map[dep_id] = unrealized_map.get(dep_id, 0.0) + _mark_to_market(p, dispatcher)
    for row in rows:
        dep_id = str(row["id"])
        row["realized_pnl"] = round(realized_map.get(dep_id, 0.0), 2)
        row["unrealized_pnl"] = round(unrealized_map.get(dep_id, 0.0), 2)


async def fetch_deployments_list(pool, dispatcher) -> list[dict]:
    """The full, unfiltered deployment list, pnl-enriched -- this is
    the actual DB-round-trip-heavy work behind GET /deployments with no
    status filter (the only shape the frontend ever requests), pulled
    out on its own so app.state.cache's background loop can call it
    directly rather than going through the HTTP layer. See app/cache.py
    for why this is cached at all."""
    rows = await queries.list_deployments(pool, status=None)
    out = [_annotate(r) for r in rows]
    await _enrich_pnl_many(pool, dispatcher, out)
    return out


async def _enrich_pnl_one(pool, dispatcher, deployment_id: UUID, row: dict) -> None:
    """Single-deployment version of _enrich_pnl_many, for GET
    /deployments/{id} -- scoped queries instead of fetching every
    deployment's data just to pick one out."""
    row["realized_pnl"] = round(await queries.realized_pnl_total(pool, deployment_id), 2)
    open_positions = await queries.list_open_positions(pool, deployment_id)
    row["unrealized_pnl"] = round(sum(_mark_to_market(p, dispatcher) for p in open_positions), 2)


@router.post("", response_model=DeploymentOut, status_code=201)
async def create_deployment(payload: DeploymentCreate, request: Request):
    manager = request.app.state.deployment_manager
    existing = await queries.get_deployment_by_name(
        request.app.state.db_pool, payload.deployment_name
    )
    if existing is not None:
        raise HTTPException(409, f"deployment_name {payload.deployment_name!r} already exists")
    # A registered-but-admin-disabled strategy can't be deployed, full
    # stop — an unregistered strategy_name is a DIFFERENT, still-allowed
    # case (see DeploymentManager.create_deployment's own docstring),
    # which is exactly why this only blocks when is_registered() is
    # ALSO true: is_strategy_enabled() defaults to True for any name
    # with no settings row, so an unregistered name never gets blocked
    # here on account of a flag that was never set for it.
    if is_registered(payload.strategy_name) and not await queries.is_strategy_enabled(
        request.app.state.db_pool, payload.strategy_name
    ):
        raise HTTPException(400, f"Strategy {payload.strategy_name!r} is disabled — enable it from Admin Options first")
    try:
        row, _strategy_registered = await manager.create_deployment(payload)
    except Exception as e:
        raise HTTPException(400, str(e))
    # Refresh the cached list NOW, not on the next background tick --
    # catalog.js's submitDeploy() reloads the Catalog (which itself
    # reads this same cached list, for active-deployment counts)
    # immediately after this call resolves, and a stale cache there
    # would show the modal closing over an unchanged list, looking like
    # the deploy silently did nothing.
    await request.app.state.cache.refresh_now("deployments")
    # A freshly created deployment has traded nothing yet -- realized_pnl/
    # unrealized_pnl are correctly 0.0 via DeploymentOut's own defaults,
    # no query needed.
    return _annotate(row)


@router.get("", response_model=list[DeploymentOut])
async def list_deployments(request: Request, status: str | None = None):
    # The frontend only ever calls this with no status filter -- that's
    # the cached hot path. Anything else (unused today, but part of the
    # API's own contract) falls through to a live query so a future
    # caller passing a real filter never gets an answer for the wrong
    # question.
    if status is None:
        return await request.app.state.cache.get("deployments")
    pool = request.app.state.db_pool
    rows = await queries.list_deployments(pool, status=status)
    out = [_annotate(r) for r in rows]
    await _enrich_pnl_many(pool, request.app.state.dispatcher, out)
    return out


@router.get("/{deployment_id}", response_model=DeploymentOut)
async def get_deployment(deployment_id: UUID, request: Request):
    pool = request.app.state.db_pool
    row = await queries.get_deployment(pool, deployment_id)
    if row is None:
        raise HTTPException(404, "No such deployment")
    out = _annotate(row)
    await _enrich_pnl_one(pool, request.app.state.dispatcher, deployment_id, out)
    return out


@router.patch("/{deployment_id}", response_model=DeploymentOut)
async def update_deployment(deployment_id: UUID, payload: DeploymentUpdate, request: Request):
    """
    Rename a deployment, edit its free-text notes, and/or (new) edit its
    config — see DeploymentUpdate's own docstring for the full reasoning,
    including why strategy_name/mode/initial_capital still aren't here.
    Rename/notes work regardless of status, same as before. config is
    the one field gated here: only while `paused` — the deployment
    itself already knows this the moment it's asked, so the check lives
    here rather than in the Pydantic model.
    """
    pool = request.app.state.db_pool
    existing = await queries.get_deployment(pool, deployment_id)
    if existing is None:
        raise HTTPException(404, "No such deployment")

    if payload.deployment_name is not None:
        stripped = payload.deployment_name.strip()
        if not stripped:
            raise HTTPException(400, "deployment_name cannot be blank")
        if stripped != existing["deployment_name"]:
            collision = await queries.get_deployment_by_name(pool, stripped)
            if collision is not None:
                raise HTTPException(409, f"deployment_name {stripped!r} already exists")
        payload.deployment_name = stripped

    if payload.config is not None and existing["status"] != "paused":
        raise HTTPException(
            409,
            f"Config can only be edited while paused (this deployment is "
            f"{existing['status']!r}). Pause it first, edit, then resume "
            f"to apply the change.",
        )

    row = await queries.update_deployment_fields(
        pool, deployment_id,
        deployment_name=payload.deployment_name, notes=payload.notes,
        config=payload.config,
    )
    out = _annotate(row)
    await _enrich_pnl_one(pool, request.app.state.dispatcher, deployment_id, out)
    # A rename changes what the cached LIST shows for this row (notes
    # aren't shown in the list today, but the name is) -- refresh now so
    # the very next GET /deployments already reflects it. Config isn't
    # in the cached list either, but refreshing here is cheap and this
    # cache key doesn't get its own separate reasoning to skip it.
    await request.app.state.cache.refresh_now("deployments")
    return out


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


@router.get("/{deployment_id}/snapshots", response_model=list[SnapshotOut])
async def get_snapshots(deployment_id: UUID, request: Request, limit: int = 1000):
    """
    Equity-curve material — see DeploymentManager's periodic snapshot
    loop for how these rows get written (roughly every 5 minutes per
    active deployment, not per tick). An empty list is a normal state,
    not an error: a deployment younger than one snapshot interval, or
    one that's spent its whole life paused, genuinely has none yet.
    """
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    rows = await queries.list_snapshots(pool, deployment_id, limit=limit)
    return [dict(r) for r in rows]


@router.get("/{deployment_id}/pnl-digest", response_model=list[PnlDigestRow])
async def get_pnl_digest(deployment_id: UUID, request: Request, period: str = "day", limit: int = 400):
    """This deployment's own daily/weekly realized-P&L digest — same
    shape and realized-only philosophy as GET /portfolio/pnl-digest
    (see queries.list_pnl_digest's docstring), just scoped to one
    deployment instead of the whole portfolio. The Detail page's
    Calendar tab is the actual consumer (a GitHub-style heatmap), which
    is why the default `limit` is high enough for a full year of daily
    buckets rather than the portfolio digest's handful-of-recent-
    periods default.
    """
    pool = request.app.state.db_pool
    dep = await queries.get_deployment(pool, deployment_id)
    if dep is None:
        raise HTTPException(404, "No such deployment")
    try:
        rows = await queries.list_pnl_digest_for_deployment(pool, deployment_id, period=period, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return [dict(r) for r in rows]


@router.post("/{deployment_id}/pause")
async def pause_deployment(deployment_id: UUID, request: Request):
    manager = request.app.state.deployment_manager
    try:
        await manager.pause(deployment_id)
    except KeyError:
        raise HTTPException(404, "No such deployment")
    except ValueError as e:
        raise HTTPException(409, str(e))
    await request.app.state.cache.refresh_now("deployments")   # status changed -- see fetch_deployments_list
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
    await request.app.state.cache.refresh_now("deployments")
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
    # A force-close stop can both change status AND close a position/
    # book a trade -- refresh every cache a stop could have touched, not
    # just the deployment list.
    cache = request.app.state.cache
    await asyncio.gather(
        cache.refresh_now("deployments"),
        cache.refresh_now("positions_open"),
        cache.refresh_now("trades_recent"),
    )
    return {"status": "stopped"}


@router.post("/{deployment_id}/delete")
async def delete_deployment(deployment_id: UUID, request: Request):
    """
    Permanently removes a STOPPED deployment and everything recorded
    under it — positions, position_lots, deployment_events,
    deployment_snapshots — via the same ON DELETE CASCADE
    clear_all_deployments relies on in bulk (see
    migrations/0001_init.sql), just for one row instead of every row.
    Reuses queries.delete_deployment, previously only called internally
    to roll back a deployment whose runner failed to start right after
    creation — same DB operation, now also reachable as a genuine
    user-facing action.

    Restricted to `stopped` deployments only: Stop already tore its
    runner down and either closed its open position (force_close=true)
    or refused to run at all while one was still open — so a stopped
    deployment never has a runner to tear down or an open position
    whose remaining value this endpoint would otherwise have to
    silently decide what to do with. `active`/`paused` deployments
    aren't just discouraged here, they're refused: stop it first (with
    force_close if it still holds a position), then delete.
    """
    pool = request.app.state.db_pool
    existing = await queries.get_deployment(pool, deployment_id)
    if existing is None:
        raise HTTPException(404, "No such deployment")
    if existing["status"] != "stopped":
        raise HTTPException(
            409,
            f"Can only delete a stopped deployment (this one is "
            f"{existing['status']!r}). Stop it first, then delete.",
        )
    await queries.delete_deployment(pool, deployment_id)
    cache = request.app.state.cache
    await asyncio.gather(
        cache.refresh_now("deployments"),
        cache.refresh_now("positions_open"),
        cache.refresh_now("trades_recent"),
    )
    return {"deleted": True}


@router.post("/flatten-all")
async def flatten_all_deployments(request: Request):
    """
    The panic button: closes every open position across every
    deployment that isn't already stopped, at the last known price,
    then pauses whichever ones were active (so nothing immediately
    re-enters on the next tick). Deliberately NOT in the danger-zone
    section below — unlike clear-all, this touches no history at all
    (every position closed here is a normal, fully-recorded fill; the
    deployments themselves still exist and can be resumed) — so it
    doesn't need clear-all's password + typed-confirmation gate, just
    the frontend's own confirm-before-calling.
    """
    manager = request.app.state.deployment_manager
    result = await manager.flatten_all()
    cache = request.app.state.cache
    await asyncio.gather(
        cache.refresh_now("deployments"),
        cache.refresh_now("positions_open"),
        cache.refresh_now("trades_recent"),
    )
    return result


# ═════════════════════════════════════════════════════════════════════
# DANGER ZONE — destructive, irreversible, deliberately last in the file
# ═════════════════════════════════════════════════════════════════════

@router.post("/clear-all")
async def clear_all_deployments(payload: ClearAllIn, request: Request):
    """
    Deletes EVERY deployment and everything under it — positions,
    position_lots, deployment_events, deployment_snapshots — via a
    single `DELETE FROM deployments`, cascading at the DB level (see
    migrations/0001_init.sql's `ON DELETE CASCADE`). Deliberately
    narrow in scope: does NOT touch the Kite login session, subscribed
    instruments, or Admin Options enable/disable state — this is "clear
    all deployments," not a full factory reset of the whole app.

    Gated behind TWO things beyond the normal request auth (session
    cookie / X-API-Key) already required to reach this endpoint at all:
    re-entering the app's own login password, and typing the literal
    confirmation phrase — both required, checked server-side, so a
    stray click or a replayed request can't trigger this by itself.
    """
    app_auth_secret = request.app.state.app_auth_secret
    if not secrets.compare_digest(payload.password, app_auth_secret):
        raise HTTPException(401, "Incorrect password")
    if payload.confirm != CLEAR_ALL_CONFIRM_PHRASE:
        raise HTTPException(400, f"Type {CLEAR_ALL_CONFIRM_PHRASE!r} exactly to confirm")

    manager = request.app.state.deployment_manager
    # Stop every runner task FIRST — DB status doesn't matter, the rows
    # are about to be deleted entirely, but a still-running runner
    # holding a stale reference to a row that no longer exists would
    # error on its next write (e.g. a tick arriving mid-delete).
    await manager.shutdown_all()
    deleted = await queries.clear_all_deployments(request.app.state.db_pool)
    # Everything just vanished -- deployments, positions, trades all at
    # once. strategy_settings is untouched (see the docstring above), so
    # that cache is deliberately left alone.
    cache = request.app.state.cache
    await asyncio.gather(
        cache.refresh_now("deployments"),
        cache.refresh_now("positions_open"),
        cache.refresh_now("trades_recent"),
    )
    return {"deleted": deleted}
