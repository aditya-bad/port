"""v2 actions. Each one is built from the same DeploymentManager and
queries calls the v1 endpoints use, so nothing about how a fill or a
status change is recorded differs — only the orchestration around them.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..db import queries
from ..deployments.schemas import DeploymentCreate
from ..slippage import resolve_book_price

logger = logging.getLogger(__name__)


def exit_order(positions: list) -> list:
    """Shorts first, longs last: buying back every short before selling
    any long means no short is ever left open without the wing that
    hedges it, even if the run stops part-way."""
    return sorted(positions, key=lambda p: 0 if p["side"] == "short" else 1)


async def _close_deployment(manager, row) -> int:
    dep_id = row["id"]
    was_active = row["status"] == "active"
    if was_active:
        runner = manager.runners.pop(str(dep_id), None)
        if runner is not None:
            await runner.stop()
    open_positions = exit_order(await queries.list_open_positions(manager.pool, dep_id))
    for pos in open_positions:
        token = int(pos["instrument_token"])
        price = manager.dispatcher.last_prices.get(token)
        if price is None:
            price = float(pos["avg_entry_price"])
            logger.warning("close-everything: no live price for %s, closing at entry %.2f", pos["symbol"], price)
        await queries.force_close_position(
            manager.pool, dep_id, pos, price, datetime.now(timezone.utc), reason="close_everything",
            book_price=await resolve_book_price(
                manager.dispatcher, token, "sell" if pos["side"] == "long" else "buy", float(pos["qty"]),
            ),
        )
    if was_active:
        await queries.set_status(manager.pool, dep_id, "paused")
        await manager._record_event(
            dep_id, row["deployment_name"], row["strategy_name"], "paused",
            metadata={"reason": "close_everything", "positions_closed": len(open_positions)},
        )
    elif open_positions:
        await manager._record_event(
            dep_id, row["deployment_name"], row["strategy_name"], "flattened",
            metadata={"positions_closed": len(open_positions), "reason": "close_everything"},
        )
    return len(open_positions)


async def close_everything(manager) -> dict[str, Any]:
    """Every non-stopped deployment: stop its runner, close its legs
    shorts-first, and pause it if it was running so nothing re-enters.
    One deployment failing never stops the rest."""
    rows = [r for r in await queries.list_deployments(manager.pool) if r["status"] != "stopped"]
    closed, touched, paused, errors = 0, 0, 0, []
    for row in rows:
        try:
            n = await _close_deployment(manager, row)
        except Exception as e:
            logger.exception("close-everything failed for %s", row["deployment_name"])
            errors.append({"deployment_name": row["deployment_name"], "error": str(e)})
            continue
        closed += n
        touched += 1 if n else 0
        paused += 1 if row["status"] == "active" else 0
    return {"positions_closed": closed, "deployments_closed": touched, "deployments_paused": paused, "errors": errors}


async def create_deployment(manager, payload: DeploymentCreate, start: str, tags: Optional[list[str]] = None):
    """Create, then optionally leave it paused ("Create paused" in the
    wizard) so nothing trades until it's resumed."""
    row, registered = await manager.create_deployment(payload)
    if start == "paused":
        await manager.pause(row["id"])
    if tags:
        await queries.update_deployment_fields(manager.pool, row["id"], tags=tags)
    return await queries.get_deployment(manager.pool, row["id"]), registered
