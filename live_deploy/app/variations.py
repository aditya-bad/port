"""
Variations: a deployment run beside another (its primary) with different
settings, on the same market, to see which settings do better.

Each variation is a full deployment of its own: its own capital, strikes,
cycles, decisions, book and runner. Nothing is shared except the price
feed. So everything that works per deployment (fills, Geek, repair, a
restart or a rebuild) works for a variation unchanged.

What makes one a variation (deployments.variation_of, migration 0048):
- Totals count the primary only: a variation is out of reports
  (include_in_reports false) and the pages show primaries, each with its
  variations a click away.
- Its alerts go to its log, except the critical ones: every kind that can
  be turned off is off (deployments.alerts_off, app/alert_types.py), so a
  variation that stops running, errors or loses track of a leg still
  alerts, and nothing else does. Each can be turned on again.
- The end-of-day check runs for primaries; a variation's day is checked
  when asked (the Repair page's check a deployment).
- At most MAX_PER_PRIMARY not stopped, per primary: each variation of a
  strangle searches strikes and asks Kite for quotes on its own, and Kite
  allows about three a second.

A variation can start fresh (it enters by its own rules) or continue the
primary's current cycle (app/variations.copy_cycle: a copy of the open
legs, the legs closed in the cycle, and the strategy's saved state, which
it then manages under its own settings).

Make primary swaps the two: the variation becomes the primary (into
reports, with the primary's alert settings) and the old primary one of its
variations. Nothing else moves; each keeps its own history.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from . import alert_types
from .db import queries

logger = logging.getLogger("live_deploy.variations")

MAX_PER_PRIMARY = 10
LABEL_MAX = 40
LOG_ONLY = list(alert_types.TYPES)      # a variation's alerts: the critical ones only


class VariationError(ValueError):
    """Refused, with a reason a person can act on."""


def name_of(primary_name: str, label: str) -> str:
    return f"{primary_name} · {label}"


async def primary_of(pool, deployment_id: UUID) -> Optional[dict]:
    """The deployment's primary: itself when it is one."""
    row = await queries.get_deployment(pool, deployment_id)
    if row is None:
        return None
    if row["variation_of"] is not None:
        top = await queries.get_deployment(pool, row["variation_of"])
        if top is not None:
            return dict(top)
    return dict(row)


async def list_of(pool, primary_id: UUID) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM deployments WHERE variation_of = $1 ORDER BY created_at", primary_id)
    return [dict(r) for r in rows]


async def create(manager, primary_id: UUID, label: str, config: dict, *, capital: Optional[float] = None,
                 start: str = "fresh", run: str = "paused") -> dict:
    """A new variation of primary_id (or of its primary, when it's a
    variation itself). start: fresh or continue (the primary's current
    cycle). run: paused or now. Returns the new deployment's row."""
    pool = manager.pool
    label = (label or "").strip()
    if not label:
        raise VariationError("Give the variation a short label: what it tries, like “checkpoint 0.75%”")
    if len(label) > LABEL_MAX:
        raise VariationError(f"Keep the label under {LABEL_MAX} characters")
    if start not in ("fresh", "continue"):
        raise VariationError("start is fresh or continue")
    if run not in ("paused", "now"):
        raise VariationError("run is paused or now")
    p = await primary_of(pool, primary_id)
    if p is None:
        raise KeyError(f"No such deployment: {primary_id}")
    if p["status"] == "stopped":
        raise VariationError("A stopped deployment can't have new variations: copy it as a new deployment instead")
    live = [v for v in await list_of(pool, p["id"]) if v["status"] != "stopped"]
    if len(live) >= MAX_PER_PRIMARY:
        raise VariationError(f"{p['deployment_name']} already has {MAX_PER_PRIMARY} variations running or paused: stop one first")
    name = name_of(p["deployment_name"], label)
    if await queries.get_deployment_by_name(pool, name) is not None:
        raise VariationError(f"A deployment named {name!r} already exists: pick another label")
    cap = float(capital) if capital is not None else float(p["initial_capital"])
    if cap <= 0:
        raise VariationError("Capital must be more than zero")

    row = await queries.create_deployment(pool, name, p["strategy_name"], p["mode"], cap, config,
                                          notes=f"Variation of {p['deployment_name']}: {label}")
    try:
        await pool.execute(
            "UPDATE deployments SET variation_of = $2, variation_label = $3, include_in_reports = false, "
            "alerts_off = $4, tags = $5 WHERE id = $1",
            row["id"], p["id"], label, LOG_ONLY, list(p["tags"] or []))
        if start == "continue":
            await copy_cycle(manager, p, row["id"])
        if run == "now":
            await queries.set_status(pool, row["id"], "active")
            await manager._start_runner(await queries.get_deployment(pool, row["id"]))
        else:
            await queries.set_status(pool, row["id"], "paused")
    except Exception:
        await queries.delete_deployment(pool, row["id"])
        raise
    await manager._record_event(row["id"], name, p["strategy_name"], "created",
                                message=f"Variation of {p['deployment_name']} ({'continuing its cycle' if start == 'continue' else 'starting fresh'})",
                                metadata={"variation_of": str(p["id"]), "label": label, "start": start})
    return dict(await queries.get_deployment(pool, row["id"]))


async def copy_cycle(manager, primary: dict, variation_id: UUID) -> None:
    """Continue the primary's current cycle: filled in by
    app/variations_copy.py (the next stage). Refused until then."""
    raise VariationError("Continuing the primary's cycle isn't available yet: start fresh")


async def promote(pool, variation_id: UUID) -> dict:
    """Make a variation the primary. Returns the new primary's row."""
    v = await queries.get_deployment(pool, variation_id)
    if v is None:
        raise KeyError(f"No such deployment: {variation_id}")
    if v["variation_of"] is None:
        raise VariationError(f"{v['deployment_name']} is already a primary")
    p = await queries.get_deployment(pool, v["variation_of"])
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE deployments SET variation_of = $1 WHERE variation_of = $2 AND id <> $1", v["id"], p["id"])
            await conn.execute(
                "UPDATE deployments SET variation_of = $2, variation_label = COALESCE(variation_label, 'Original'), "
                "include_in_reports = $3, alerts_off = $4, updated_at = now() WHERE id = $1",
                p["id"], v["id"], v["include_in_reports"], list(v["alerts_off"] or []))
            await conn.execute(
                "UPDATE deployments SET variation_of = NULL, include_in_reports = $2, alerts_off = $3, updated_at = now() "
                "WHERE id = $1", v["id"], p["include_in_reports"], list(p["alerts_off"] or []))
    return dict(await queries.get_deployment(pool, v["id"]))
