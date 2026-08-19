"""
live_deploy — DB access layer.

All persistence for deployments, positions, fills, events, and
snapshots goes through here. Every function takes an asyncpg Pool (or,
for record_fill's internals, a Connection already inside a transaction)
— no ORM, plain SQL, matching the rest of this project's style.

Position/lot semantics mirror backtest.py's Position/Lot model exactly:
a same-direction fill (buy while long, sell while short) ADDS a lot
(quantity-weighted average price, i.e. averaging); an opposite-direction
fill must close the ENTIRE position — qty must exactly match the open
quantity. No partial exits, no reversal in a single fill. This is a
deliberate simplification, not an oversight: no strategy exists yet to
tell us partial exits are actually needed, and this keeps a future
strategy ported from the backtest engine's buy()/sell() calling
convention unchanged. Loosening this later only requires relaxing the
one qty-equality check in record_fill — the schema already supports it.
"""

import json
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

import asyncpg


# ═════════════════════════════════════════════════════════════════════
# DEPLOYMENTS
# ═════════════════════════════════════════════════════════════════════

async def create_deployment(
    pool: asyncpg.Pool,
    deployment_name: str,
    strategy_name: str,
    mode: str,
    initial_capital: float,
    config: dict,
    notes: Optional[str] = None,
) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO deployments
                (deployment_name, strategy_name, mode, initial_capital,
                 current_cash, config, notes)
            VALUES ($1, $2, $3, $4, $4, $5, $6)
            RETURNING *
            """,
            deployment_name, strategy_name, mode, initial_capital, config, notes,
        )


async def delete_deployment(pool: asyncpg.Pool, deployment_id: UUID) -> None:
    """Deletes a single deployment row (and, via the same ON DELETE
    CASCADE foreign keys clear_all_deployments relies on in bulk,
    anything already written under it — positions, position_lots,
    deployment_events, deployment_snapshots, deployment_state). Two
    call sites: (1) create_deployment's own rollback, when a deployment
    is created in the DB but its runner then fails to start (e.g. the
    strategy's own on_start() rejects the config), so a failed POST
    /deployments never leaves an orphaned row behind for a caller who
    was told it failed; (2) POST /deployments/{id}/delete, the
    genuine user-facing "permanently remove this stopped deployment"
    action — that router endpoint is what actually restricts this to
    stopped deployments; this function itself has no such restriction,
    on purpose, since the rollback call site (1) needs to delete a
    deployment that was never even started."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM deployments WHERE id = $1", deployment_id)


async def get_deployment(pool: asyncpg.Pool, deployment_id: UUID) -> Optional[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM deployments WHERE id = $1", deployment_id
        )


async def get_deployment_by_name(pool: asyncpg.Pool, deployment_name: str) -> Optional[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM deployments WHERE deployment_name = $1", deployment_name
        )


async def list_deployments(pool: asyncpg.Pool, status: Optional[str] = None) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        if status:
            return await conn.fetch(
                "SELECT * FROM deployments WHERE status = $1 ORDER BY created_at", status
            )
        return await conn.fetch("SELECT * FROM deployments ORDER BY created_at")


async def update_deployment_fields(
    pool: asyncpg.Pool, deployment_id: UUID,
    deployment_name: Optional[str] = None, notes: Optional[str] = None,
    config: Optional[dict] = None, include_in_reports: Optional[bool] = None,
    tags: Optional[list[str]] = None, notifications_enabled: Optional[bool] = None,
) -> Optional[asyncpg.Record]:
    """
    Partial update for PATCH /deployments/{id} — only the field(s)
    actually passed get written; omitted ones (None) are left untouched,
    NOT overwritten with NULL (a caller renaming a deployment shouldn't
    accidentally blank out its notes or config, and vice versa). Uses
    COALESCE against the row's own current value rather than a
    dynamically-built SQL string — same fixed query every call, no
    string-built column list to get wrong.

    Renamed from update_deployment_metadata once `config` joined
    deployment_name/notes here — deliberately still just this one call
    site's own concern, NOT where the "only while paused" restriction on
    editing config lives (see DeploymentUpdate's own docstring) — that's
    a status check the router makes before ever calling this, since it
    needs the deployment's CURRENT row to decide, and this function's
    job is purely "write whatever fields were passed," not "decide
    whether it's currently allowed to."

    config=None (the default, meaning "field omitted") relies on
    asyncpg never invoking the jsonb type codec for a bare Python None —
    None always maps straight to SQL NULL regardless of column type, so
    COALESCE(NULL, config) correctly keeps the existing value. An
    explicit config={} is NOT None, so it DOES get encoded and DOES
    overwrite — "clear every config key" is a real, distinct intent
    from "don't touch config," same distinction notes already draws
    between omitted (None) and explicitly-blanked ("").

    include_in_reports has the identical None-means-omitted distinction,
    just over a plain boolean column instead of jsonb — an explicit
    False is a real value COALESCE must not mistake for "don't touch."

    tags is the same story again over a TEXT[] column: a bare Python
    None still maps straight to SQL NULL (same reasoning as config's
    own comment on asyncpg's jsonb codec never firing for None,
    equally true for the array codec), so COALESCE(NULL, tags) keeps
    the existing list untouched. An explicit [] is NOT None, so it DOES
    get encoded and DOES overwrite -- "clear every tag" is a real,
    distinct intent from "don't touch tags."

    notifications_enabled has the identical None-means-omitted
    distinction as include_in_reports above, over its own plain boolean
    column (migration 0011) — gates DeploymentRunner.notify_execution's
    BOTH channels (in-app toast and mobile push) for this deployment,
    read fresh from the DB on every execution rather than cached, so
    this takes effect on the very next entry/exit, no restart needed.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE deployments
            SET deployment_name = COALESCE($2, deployment_name),
                notes = COALESCE($3, notes),
                config = COALESCE($4, config),
                include_in_reports = COALESCE($5, include_in_reports),
                tags = COALESCE($6, tags),
                notifications_enabled = COALESCE($7, notifications_enabled),
                updated_at = now()
            WHERE id = $1
            RETURNING *
            """,
            deployment_id, deployment_name, notes, config, include_in_reports, tags,
            notifications_enabled,
        )


async def set_status(pool: asyncpg.Pool, deployment_id: UUID, status: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE deployments SET status = $2, updated_at = now() WHERE id = $1",
            deployment_id, status,
        )


async def save_deployment_state(pool: asyncpg.Pool, deployment_id: UUID, state: dict) -> None:
    """Upsert this deployment's latest resumable state blob — see
    DeploymentRunner.stop() (the only caller) and StrategyBase.
    get_persistable_state() for what actually goes in it. Wholesale
    overwrite, not a merge — whatever the strategy returns IS the new
    snapshot, replacing whatever was there before."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deployment_state (deployment_id, state, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (deployment_id) DO UPDATE SET state = $2, updated_at = now()
            """,
            deployment_id, state,
        )


async def load_deployment_state(pool: asyncpg.Pool, deployment_id: UUID) -> Optional[dict]:
    """The last state blob this deployment's strategy persisted via
    save_deployment_state, or None if it never has (fresh deploy, or a
    strategy that doesn't use this hook at all)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state FROM deployment_state WHERE deployment_id = $1", deployment_id,
        )
    return row["state"] if row else None


async def clear_all_deployments(pool: asyncpg.Pool) -> int:
    """
    Delete EVERY deployment row — cascades (ON DELETE CASCADE, see
    migrations/0001_init.sql) to positions, position_lots,
    deployment_events, and deployment_snapshots automatically, wiping
    all trading history in one statement. Deliberately narrow: does NOT
    touch kite_sessions, strategy_settings, or anything instrument-
    related — the router endpoint calling this is "clear all
    deployments," not a full factory reset.

    Returns how many deployment rows were actually deleted, so the
    caller can report a real number back rather than just "done."
    """
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM deployments")
        # asyncpg's execute() returns a command-status string like
        # "DELETE 3" for a DELETE statement — the row count is the last
        # whitespace-separated token.
        return int(result.split()[-1])


# ═════════════════════════════════════════════════════════════════════
# POSITIONS
# ═════════════════════════════════════════════════════════════════════

async def list_positions(
    pool: asyncpg.Pool, deployment_id: UUID, status: Optional[str] = None,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        if status:
            return await conn.fetch(
                """
                SELECT * FROM positions
                WHERE deployment_id = $1 AND status = $2
                ORDER BY opened_at DESC
                """,
                deployment_id, status,
            )
        return await conn.fetch(
            "SELECT * FROM positions WHERE deployment_id = $1 ORDER BY opened_at DESC",
            deployment_id,
        )


async def list_open_positions(pool: asyncpg.Pool, deployment_id: UUID) -> list[asyncpg.Record]:
    return await list_positions(pool, deployment_id, status="open")


async def get_adjustment_histogram(
    pool: asyncpg.Pool, deployment_id: UUID, group_by: str,
) -> list[dict]:
    """Step 87 — backs GET /deployments/{id}/adjustment-histogram: "how
    many trading units (days or cycles, see `group_by`) had 0
    adjustments, how many had 1, 2, 3+." Only called for a strategy
    whose class sets StrategyBase.ADJUSTMENT_GROUP_BY (see the router),
    which is also what `group_by` comes from.

    Every position this deployment has EVER opened carries a
    `metadata->>'leg_role'` of "original" (the day/cycle's own entry
    leg) or "adjustment_<n>" (every later rebalancing leg opened
    without a fresh entry) — see intraday_dtt_adjusted.py's `_enter`/
    `_adjust` and strangle_monthly_v2.py's `_trade_meta` for where this
    gets written. A leg with no `leg_role` at all (persisted before
    Step 87, or a strangle hedge leg, which never carries one) is
    treated as "original" by the COALESCE below — the safe default,
    since it means "don't count this as an adjustment," never the
    reverse.

    One SQL round-trip; the actual day/cycle -> (has_entry, adjustment
    count) -> histogram-bucket reduction happens in Python below rather
    than in SQL, since it's a two-level GROUP BY (first by unit, THEN by
    that unit's own adjustment count) that reads far more clearly this
    way than as nested SQL aggregates for what's a small, deployment-
    scoped row count."""
    if group_by == "day":
        group_expr = "date_trunc('day', opened_at AT TIME ZONE 'Asia/Kolkata')"
    elif group_by == "cycle_id":
        group_expr = "(metadata->>'cycle_id')"
    else:
        raise ValueError(f"group_by must be 'day' or 'cycle_id', got {group_by!r}")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {group_expr} AS grp, COALESCE(metadata->>'leg_role', 'original') AS leg_role
            FROM positions
            WHERE deployment_id = $1
            """,
            deployment_id,
        )

    units: dict = {}   # group key -> {"has_entry": bool, "adjustments": int}
    for r in rows:
        grp = r["grp"]
        if grp is None:
            continue   # a leg predating cycle_id/opened_at ever being set -- can't place it in any unit
        u = units.setdefault(grp, {"has_entry": False, "adjustments": 0})
        if r["leg_role"] == "original":
            u["has_entry"] = True
        elif r["leg_role"] and r["leg_role"].startswith("adjustment_"):
            u["adjustments"] += 1

    # 3+ collapsed into one bucket -- a real distinction between 5 vs 7
    # adjustments in one unit matters far less than "was this unit
    # calm (0-2) or actively rebalanced (3+)", and keeps the bucket
    # list a fixed, small size regardless of how high a single unit's
    # count ever climbs.
    ADJUSTMENT_HISTOGRAM_CAP = 3
    counts: dict = {}
    for u in units.values():
        if not u["has_entry"]:
            continue   # not a real trading unit (an adjustment leg somehow outliving its own entry's own group -- shouldn't happen, but never surface a phantom unit if it does)
        bucket = min(u["adjustments"], ADJUSTMENT_HISTOGRAM_CAP)
        counts[bucket] = counts.get(bucket, 0) + 1

    return [
        {"adjustments": k, "label": f"{k}+" if k == ADJUSTMENT_HISTOGRAM_CAP else str(k), "units": v}
        for k, v in sorted(counts.items())
    ]


# ═════════════════════════════════════════════════════════════════════
# CROSS-DEPLOYMENT AGGREGATES — the Dashboard's consolidated views.
# Every query above this point is scoped to one deployment_id; these
# two deliberately are NOT, so the frontend doesn't have to do N+1
# client-side fetching across every deployment to build a combined
# table. Both join back to `deployments` for deployment_name/
# strategy_name, since a bare position/lot row doesn't carry either.
# ═════════════════════════════════════════════════════════════════════

async def list_all_positions(
    pool: asyncpg.Pool, status: Optional[str] = "open",
) -> list[asyncpg.Record]:
    """Deliberately NOT filtered by include_in_reports (unlike
    list_pnl_digest/pnl_summary_for_range/etc above) — this is raw
    per-position data shared by callers that need EVERY position
    regardless of the toggle (Deployments list's own live per-row
    Unrealized column, keyed off exactly this endpoint) as well as
    callers that only want the toggled-on subset (Dashboard, Portfolio's
    exposure table). Since the two needs genuinely differ per caller,
    the exclusion is applied client-side by whoever wants it (see
    dashboard.js/portfolio.js), not baked into this shared query — see
    the 0009 migration's own comment for the toggle itself."""
    async with pool.acquire() as conn:
        if status:
            return await conn.fetch(
                """
                SELECT p.*, d.deployment_name, d.strategy_name
                FROM positions p
                JOIN deployments d ON d.id = p.deployment_id
                WHERE p.status = $1
                ORDER BY p.opened_at DESC
                """,
                status,
            )
        return await conn.fetch(
            """
            SELECT p.*, d.deployment_name, d.strategy_name
            FROM positions p
            JOIN deployments d ON d.id = p.deployment_id
            ORDER BY p.opened_at DESC
            """
        )


async def list_recent_trades(pool: asyncpg.Pool, limit: int = 20) -> list[asyncpg.Record]:
    """Deliberately NOT filtered by include_in_reports, same reasoning
    as list_all_positions just above — raw fills across every
    deployment; Dashboard's own activity feed (currently the only
    caller) excludes toggled-off deployments client-side instead, so
    this stays reusable raw data rather than baking in one caller's
    opinion of what counts as a "report." """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT pl.*, p.symbol, d.deployment_name, d.strategy_name
            FROM position_lots pl
            JOIN positions p ON p.id = pl.position_id
            JOIN deployments d ON d.id = pl.deployment_id
            ORDER BY pl.executed_at DESC
            LIMIT $1
            """,
            limit,
        )


async def realized_pnl_by_deployment(pool: asyncpg.Pool) -> dict[str, float]:
    """{deployment_id (str) -> cumulative realized P&L}, every deployment
    that has at least one closed position — used to enrich the
    deployment LIST endpoint in one query instead of one-per-row.
    Deliberately NOT filtered by include_in_reports — every deployment's
    OWN row (on the Deployments list, on its own Detail page) always
    shows its own accurate numbers regardless of the toggle; only
    cross-deployment aggregates (Dashboard, Portfolio, Reports) skip a
    toggled-off deployment, and none of those read this function."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT deployment_id, COALESCE(SUM(realized_pnl), 0) AS total
            FROM positions
            WHERE status = 'closed'
            GROUP BY deployment_id
            """
        )
        return {str(r["deployment_id"]): float(r["total"]) for r in rows}


async def realized_pnl_total(pool: asyncpg.Pool, deployment_id: UUID) -> float:
    """Single-deployment version of realized_pnl_by_deployment, for
    endpoints already scoped to one deployment_id (cheaper than fetching
    every deployment's total just to look up one)."""
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            """
            SELECT COALESCE(SUM(realized_pnl), 0) FROM positions
            WHERE deployment_id = $1 AND status = 'closed'
            """,
            deployment_id,
        )
        return float(val)


# ═════════════════════════════════════════════════════════════════════
# FILLS — the one place position/cash/lot state changes
# ═════════════════════════════════════════════════════════════════════

class ClosingQtyMismatch(ValueError):
    """Raised when a closing fill's qty doesn't exactly match the open qty."""


class InsufficientCash(ValueError):
    """Raised when a buy would take a deployment's cash negative."""


async def record_fill(
    pool: asyncpg.Pool,
    deployment_id: UUID,
    symbol: str,
    instrument_token: int,
    action: str,          # "buy" | "sell"
    qty: float,
    price: float,
    executed_at: datetime,
    reason: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Record one paper fill, atomically: opens a new position, adds a lot
    (averaging) to an existing same-direction position, or fully closes
    an opposite-direction position — then always inserts the lot row and
    adjusts the deployment's cash, all in one transaction.

    Raises InsufficientCash / ClosingQtyMismatch as clean, catchable
    exceptions from application-level checks made INSIDE the same
    transaction (after locking the deployment row) — the DB's own
    `current_cash >= 0` CHECK constraint still exists underneath as a
    last-resort safety net, but callers should never see it directly.
    """
    if action not in ("buy", "sell"):
        raise ValueError(f"action must be 'buy' or 'sell', got {action!r}")
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    metadata = metadata or {}

    async with pool.acquire() as conn:
        async with conn.transaction():
            deployment = await conn.fetchrow(
                "SELECT * FROM deployments WHERE id = $1 FOR UPDATE", deployment_id,
            )
            if deployment is None:
                raise ValueError(f"No such deployment: {deployment_id}")

            if action == "buy":
                cost = qty * price
                if cost > float(deployment["current_cash"]) + 1e-9:
                    raise InsufficientCash(
                        f"Buy {qty} @ {price} costs {cost:.2f}, but deployment "
                        f"{deployment['deployment_name']} only has "
                        f"{float(deployment['current_cash']):.2f} cash."
                    )

            existing = await conn.fetchrow(
                """
                SELECT * FROM positions
                WHERE deployment_id = $1 AND instrument_token = $2 AND status = 'open'
                FOR UPDATE
                """,
                deployment_id, instrument_token,
            )

            realized_pnl: Optional[float] = None

            if existing is None:
                side = "long" if action == "buy" else "short"
                position = await conn.fetchrow(
                    """
                    INSERT INTO positions
                        (deployment_id, symbol, instrument_token, side,
                         status, qty, avg_entry_price, opened_at, metadata)
                    VALUES ($1, $2, $3, $4, 'open', $5, $6, $7, $8)
                    RETURNING *
                    """,
                    deployment_id, symbol, instrument_token, side,
                    qty, price, executed_at, metadata,
                )
                position_id = position["id"]

            else:
                adding = (existing["side"] == "long" and action == "buy") or \
                         (existing["side"] == "short" and action == "sell")

                if adding:
                    new_qty = float(existing["qty"]) + qty
                    new_avg = (float(existing["qty"]) * float(existing["avg_entry_price"])
                              + qty * price) / new_qty
                    await conn.execute(
                        "UPDATE positions SET qty = $2, avg_entry_price = $3 WHERE id = $1",
                        existing["id"], new_qty, new_avg,
                    )
                    position_id = existing["id"]

                else:
                    if abs(qty - float(existing["qty"])) > 1e-9:
                        raise ClosingQtyMismatch(
                            f"Closing fill qty ({qty}) must exactly match the "
                            f"open position qty ({existing['qty']}) for "
                            f"{symbol} — partial exits and same-fill reversals "
                            f"aren't supported. Close fully, then open a new "
                            f"position as a separate fill."
                        )
                    if existing["side"] == "long":
                        realized_pnl = (price - float(existing["avg_entry_price"])) * qty
                    else:
                        realized_pnl = (float(existing["avg_entry_price"]) - price) * qty

                    await conn.execute(
                        """
                        UPDATE positions
                        SET status = 'closed', qty = 0, realized_pnl = $2, closed_at = $3
                        WHERE id = $1
                        """,
                        existing["id"], realized_pnl, executed_at,
                    )
                    position_id = existing["id"]

            lot = await conn.fetchrow(
                """
                INSERT INTO position_lots
                    (position_id, deployment_id, action, qty, price,
                     executed_at, reason, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                position_id, deployment_id, action, qty, price,
                executed_at, reason, metadata,
            )

            # Cash: a buy spends cash, a sell (short entry OR long exit) receives it.
            cash_delta = -(qty * price) if action == "buy" else (qty * price)
            await conn.execute(
                """
                UPDATE deployments
                SET current_cash = current_cash + $2, updated_at = now()
                WHERE id = $1
                """,
                deployment_id, cash_delta,
            )

            return {
                "position_id": position_id,
                "lot_id": lot["id"],
                "realized_pnl": realized_pnl,
            }


async def force_close_position(
    pool: asyncpg.Pool, deployment_id: UUID, position: asyncpg.Record,
    price: float, executed_at: datetime, reason: str,
) -> dict[str, Any]:
    """Convenience wrapper — close an existing open position at `price`."""
    action = "sell" if position["side"] == "long" else "buy"
    return await record_fill(
        pool, deployment_id, position["symbol"], position["instrument_token"],
        action, float(position["qty"]), price, executed_at, reason=reason,
    )


# ═════════════════════════════════════════════════════════════════════
# LOTS / TRADES (for reporting)
# ═════════════════════════════════════════════════════════════════════

async def list_lots(
    pool: asyncpg.Pool, deployment_id: UUID, offset: int = 0, limit: int = 200,
) -> tuple[list[asyncpg.Record], int]:
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM position_lots WHERE deployment_id = $1", deployment_id
        )
        # `symbol` via a correlated SUBQUERY, not a JOIN -- a lot row
        # itself doesn't carry it (only its position does), and the
        # Trades tab needs it as a visible column (see LotOut). A JOIN
        # here would still be logically correct, but changes the query
        # plan enough to perturb Postgres's (never actually guaranteed,
        # but previously stable in practice) row order for two fills
        # sharing the exact same `executed_at` -- e.g. a roll's
        # close-then-open pair, timestamped identically by the strategy
        # that placed them. The subquery form leaves the PRIMARY scan
        # (position_lots alone, filtered + ordered) structurally
        # unchanged from before this column was added, keeping that
        # ordering exactly as stable as it already was.
        rows = await conn.fetch(
            """
            SELECT pl.*, (SELECT symbol FROM positions WHERE id = pl.position_id) AS symbol
            FROM position_lots pl
            WHERE pl.deployment_id = $1
            ORDER BY pl.executed_at DESC
            OFFSET $2 LIMIT $3
            """,
            deployment_id, offset, limit,
        )
        return rows, total


# ═════════════════════════════════════════════════════════════════════
# EVENTS
# ═════════════════════════════════════════════════════════════════════

async def record_event(
    pool: asyncpg.Pool, deployment_id: UUID, event_type: str,
    message: Optional[str] = None, metadata: Optional[dict] = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deployment_events (deployment_id, event_type, message, metadata)
            VALUES ($1, $2, $3, $4)
            """,
            deployment_id, event_type, message, metadata or {},
        )


async def list_events(
    pool: asyncpg.Pool, deployment_id: UUID, offset: int = 0, limit: int = 200,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM deployment_events
            WHERE deployment_id = $1
            ORDER BY created_at DESC
            OFFSET $2 LIMIT $3
            """,
            deployment_id, offset, limit,
        )


# ═════════════════════════════════════════════════════════════════════
# SNAPSHOTS (equity curve material)
# ═════════════════════════════════════════════════════════════════════

async def record_snapshot(
    pool: asyncpg.Pool, deployment_id: UUID, snapshot_at: datetime,
    cash: float, open_positions_value: float, total_value: float,
    realized_pnl_cumulative: float, metadata: Optional[dict] = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deployment_snapshots
                (deployment_id, snapshot_at, cash, open_positions_value,
                 total_value, realized_pnl_cumulative, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            deployment_id, snapshot_at, cash, open_positions_value,
            total_value, realized_pnl_cumulative, metadata or {},
        )


async def list_snapshots(
    pool: asyncpg.Pool, deployment_id: UUID, limit: int = 1000,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM deployment_snapshots
            WHERE deployment_id = $1
            ORDER BY snapshot_at
            LIMIT $2
            """,
            deployment_id, limit,
        )


async def list_portfolio_equity_curve(
    pool: asyncpg.Pool, bucket_seconds: int = 300, limit: int = 1000,
) -> list[asyncpg.Record]:
    """One combined equity-curve point per time bucket, summed across
    EVERY deployment's snapshots (not just currently-active ones) —
    the Portfolio view's whole-account equity curve.

    Snapshots for all active deployments are recorded in the same
    snapshot_loop iteration (see DeploymentManager.snapshot_all_active),
    but each deployment's own row still gets its own datetime.now() call,
    so rows from the same "tick" can differ by a few milliseconds —
    bucket_seconds (default 300, matching DEFAULT_SNAPSHOT_INTERVAL_SECONDS)
    floors snapshot_at down to the nearest bucket so same-tick rows from
    different deployments always land together instead of scattering
    into their own single-row buckets.

    Deliberately not scoped to any particular deployment status: a
    bucket's sum reflects however many deployments actually had a
    runner (i.e. were active) AT THAT POINT IN TIME — a since-paused
    deployment's older snapshots still contribute to its own past
    buckets (paper-trading history doesn't retroactively change), it
    just stops contributing to NEW buckets the moment it's no longer
    active, same as it stops accumulating its own per-deployment curve.

    IS scoped by include_in_reports though, unlike status — see the
    0009 migration's own comment: this is Portfolio's own combined
    equity curve, a cross-deployment aggregate the toggle exists
    specifically to let a deployment opt out of, same as every other
    view in this file that joins deployments for exactly this reason.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                to_timestamp(floor(extract(epoch FROM ds.snapshot_at) / $1) * $1) AS bucket_at,
                SUM(ds.total_value) AS total_value,
                SUM(ds.realized_pnl_cumulative) AS realized_pnl_cumulative,
                COUNT(DISTINCT ds.deployment_id) AS deployments_count
            FROM deployment_snapshots ds
            JOIN deployments d ON d.id = ds.deployment_id
            WHERE d.include_in_reports = true
            GROUP BY bucket_at
            ORDER BY bucket_at
            LIMIT $2
            """,
            float(bucket_seconds), limit,
        )


_DIGEST_PERIODS = ("day", "week", "month")


async def list_pnl_digest(
    pool: asyncpg.Pool, period: str = "day", limit: int = 30,
) -> list[asyncpg.Record]:
    """Portfolio-wide realized-P&L digest, bucketed into calendar
    days/weeks (Asia/Kolkata — this app's own timezone, matching the
    ticker clock) — the "how much did I actually bank, and how much
    did I trade" summary a periodic digest is supposed to answer,
    across every deployment at once.

    Deliberately REALIZED P&L only, not unrealized/mark-to-market —
    that's what Dashboard/Portfolio's live views already show, and
    mixing a live, only-true-right-now unrealized number into a
    digest of past, settled days would misrepresent history (an open
    position's unrealized P&L on a PAST day isn't a fact anymore, it's
    just whatever the price happened to be — the position may have
    since gone the other way entirely). positions.realized_pnl +
    positions.closed_at are set exactly once, atomically, when a
    position actually closes (see record_fill) — an honest, permanent
    record of what was actually booked and when, unlike trying to
    diff deployment_snapshots' cumulative totals across days (which
    would silently under-count any day a deployment was paused and
    recording no snapshots at all).

    Two source tables, closes (realized P&L, only present when a
    position actually closed) and fills (every buy/sell, entries
    included — the "how much did I actually trade" half of the
    digest) don't share a row-per-row grain, so they're bucketed
    separately as CTEs and combined with a FULL OUTER JOIN — a period
    with fills but no closes yet (a position opened today, still
    open) is real and must still appear with realized_pnl=0, not be
    silently dropped.

    Both CTEs JOIN deployments and filter on include_in_reports=true —
    a deployment toggled out of reports contributes nothing to this
    portfolio-wide digest, same as if it never existed for this view's
    purposes (see the 0009 migration's own comment). This is the ONE
    place that distinction actually matters for this query; the
    per-deployment sibling below (list_pnl_digest_for_deployment) is
    deliberately never filtered this way.
    """
    if period not in _DIGEST_PERIODS:
        raise ValueError(f"period must be one of {_DIGEST_PERIODS}, got {period!r}")
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            WITH closes AS (
                SELECT
                    (date_trunc($1, p.closed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    SUM(p.realized_pnl) AS realized_pnl,
                    COUNT(*) AS positions_closed,
                    COUNT(*) FILTER (WHERE p.realized_pnl > 0) AS wins,
                    COUNT(*) FILTER (WHERE p.realized_pnl < 0) AS losses
                FROM positions p
                JOIN deployments d ON d.id = p.deployment_id
                WHERE p.status = 'closed' AND p.closed_at IS NOT NULL AND d.include_in_reports = true
                GROUP BY 1
            ),
            fills AS (
                SELECT
                    (date_trunc($1, pl.executed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    COUNT(*) AS fills
                FROM position_lots pl
                JOIN deployments d ON d.id = pl.deployment_id
                WHERE d.include_in_reports = true
                GROUP BY 1
            )
            SELECT
                COALESCE(closes.period_start, fills.period_start) AS period_start,
                COALESCE(closes.realized_pnl, 0)::float8 AS realized_pnl,
                COALESCE(closes.positions_closed, 0) AS positions_closed,
                COALESCE(closes.wins, 0) AS wins,
                COALESCE(closes.losses, 0) AS losses,
                COALESCE(fills.fills, 0) AS fills
            FROM closes
            FULL OUTER JOIN fills USING (period_start)
            ORDER BY period_start DESC
            LIMIT $2
            """,
            period, limit,
        )


async def list_pnl_digest_for_deployment(
    pool: asyncpg.Pool, deployment_id: UUID, period: str = "day", limit: int = 400,
) -> list[asyncpg.Record]:
    """Same shape and philosophy as list_pnl_digest (see its own
    docstring for the realized-only reasoning and the closes/fills
    FULL OUTER JOIN), scoped to ONE deployment instead of the whole
    portfolio — the P&L calendar heatmap's per-deployment source on
    Detail's own tab. Both `positions` and `position_lots` carry
    `deployment_id` directly (not just via position_id -> positions),
    so this is a straight WHERE addition to the same query shape, not a
    different join structure. `limit` defaults higher than the
    portfolio digest's 30 (400 comfortably covers a full year of daily
    buckets, ~371 for a GitHub-style 53-week grid) since a calendar
    heatmap wants a full year in view, not a handful of recent periods.
    Deliberately NOT filtered by include_in_reports, unlike its
    portfolio-wide sibling above — a deployment's own P&L calendar on
    its own Detail page shows its own history regardless of whether
    it's opted out of cross-deployment reports (see the 0009
    migration's own comment).
    """
    if period not in _DIGEST_PERIODS:
        raise ValueError(f"period must be one of {_DIGEST_PERIODS}, got {period!r}")
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            WITH closes AS (
                SELECT
                    (date_trunc($1, closed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    SUM(realized_pnl) AS realized_pnl,
                    COUNT(*) AS positions_closed,
                    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
                    COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses
                FROM positions
                WHERE status = 'closed' AND closed_at IS NOT NULL AND deployment_id = $2
                GROUP BY 1
            ),
            fills AS (
                SELECT
                    (date_trunc($1, executed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    COUNT(*) AS fills
                FROM position_lots
                WHERE deployment_id = $2
                GROUP BY 1
            )
            SELECT
                COALESCE(closes.period_start, fills.period_start) AS period_start,
                COALESCE(closes.realized_pnl, 0)::float8 AS realized_pnl,
                COALESCE(closes.positions_closed, 0) AS positions_closed,
                COALESCE(closes.wins, 0) AS wins,
                COALESCE(closes.losses, 0) AS losses,
                COALESCE(fills.fills, 0) AS fills
            FROM closes
            FULL OUTER JOIN fills USING (period_start)
            ORDER BY period_start DESC
            LIMIT $3
            """,
            period, deployment_id, limit,
        )


async def list_pnl_digest_for_range(
    pool: asyncpg.Pool, start: datetime, end: datetime, period: str = "day",
) -> list[asyncpg.Record]:
    """Same shape, philosophy, and include_in_reports filtering as
    list_pnl_digest (see its own docstring) — a full [start, end) window
    bucketed into calendar days/weeks, rather than "the most recent N
    buckets". The Calendar heatmap's year-picker (Step 74) is the actual
    caller: "show me all of 2025" needs every real bucket in that exact
    window, which ORDER BY period_start DESC LIMIT N can't express
    (LIMIT N always means "the N most recent", so a past year would
    just silently return recent 2026 buckets instead of reaching back
    into 2025 at all, once there's more than N periods of history)."""
    if period not in _DIGEST_PERIODS:
        raise ValueError(f"period must be one of {_DIGEST_PERIODS}, got {period!r}")
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            WITH closes AS (
                SELECT
                    (date_trunc($1, p.closed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    SUM(p.realized_pnl) AS realized_pnl,
                    COUNT(*) AS positions_closed,
                    COUNT(*) FILTER (WHERE p.realized_pnl > 0) AS wins,
                    COUNT(*) FILTER (WHERE p.realized_pnl < 0) AS losses
                FROM positions p
                JOIN deployments d ON d.id = p.deployment_id
                WHERE p.status = 'closed' AND p.closed_at >= $2 AND p.closed_at < $3
                    AND d.include_in_reports = true
                GROUP BY 1
            ),
            fills AS (
                SELECT
                    (date_trunc($1, pl.executed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    COUNT(*) AS fills
                FROM position_lots pl
                JOIN deployments d ON d.id = pl.deployment_id
                WHERE pl.executed_at >= $2 AND pl.executed_at < $3 AND d.include_in_reports = true
                GROUP BY 1
            )
            SELECT
                COALESCE(closes.period_start, fills.period_start) AS period_start,
                COALESCE(closes.realized_pnl, 0)::float8 AS realized_pnl,
                COALESCE(closes.positions_closed, 0) AS positions_closed,
                COALESCE(closes.wins, 0) AS wins,
                COALESCE(closes.losses, 0) AS losses,
                COALESCE(fills.fills, 0) AS fills
            FROM closes
            FULL OUTER JOIN fills USING (period_start)
            ORDER BY period_start DESC
            """,
            period, start, end,
        )


async def list_pnl_digest_for_deployment_range(
    pool: asyncpg.Pool, deployment_id: UUID, start: datetime, end: datetime, period: str = "day",
) -> list[asyncpg.Record]:
    """Same shape as list_pnl_digest_for_range, scoped to one deployment
    — the year-picker's per-deployment source on Detail's own Calendar
    tab, same relationship list_pnl_digest_for_deployment already has to
    list_pnl_digest. Deliberately NOT filtered by include_in_reports,
    same reasoning as that function's own docstring."""
    if period not in _DIGEST_PERIODS:
        raise ValueError(f"period must be one of {_DIGEST_PERIODS}, got {period!r}")
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            WITH closes AS (
                SELECT
                    (date_trunc($1, closed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    SUM(realized_pnl) AS realized_pnl,
                    COUNT(*) AS positions_closed,
                    COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
                    COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses
                FROM positions
                WHERE status = 'closed' AND deployment_id = $2 AND closed_at >= $3 AND closed_at < $4
                GROUP BY 1
            ),
            fills AS (
                SELECT
                    (date_trunc($1, executed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    COUNT(*) AS fills
                FROM position_lots
                WHERE deployment_id = $2 AND executed_at >= $3 AND executed_at < $4
                GROUP BY 1
            )
            SELECT
                COALESCE(closes.period_start, fills.period_start) AS period_start,
                COALESCE(closes.realized_pnl, 0)::float8 AS realized_pnl,
                COALESCE(closes.positions_closed, 0) AS positions_closed,
                COALESCE(closes.wins, 0) AS wins,
                COALESCE(closes.losses, 0) AS losses,
                COALESCE(fills.fills, 0) AS fills
            FROM closes
            FULL OUTER JOIN fills USING (period_start)
            ORDER BY period_start DESC
            """,
            period, deployment_id, start, end,
        )


async def pnl_summary_for_range(
    pool: asyncpg.Pool, start: datetime, end: datetime,
) -> dict[str, Any]:
    """Portfolio-wide realized P&L + activity for one exact [start, end)
    window — the Reports page's per-period stat-card row. Same
    realized-only philosophy as list_pnl_digest (see its docstring),
    just for a single caller-chosen window instead of many calendar
    buckets at once — used to compute both the SELECTED period's
    numbers and the PREVIOUS period's (for the delta shown next to
    each stat), two calls with different [start, end) rather than one
    query trying to do both at once.

    Both queries JOIN deployments and filter on include_in_reports=true
    — same portfolio-wide exclusion as list_pnl_digest above, for the
    same reason (this is a cross-deployment aggregate through and
    through).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(p.realized_pnl), 0)::float8 AS realized_pnl,
                COUNT(*) AS positions_closed,
                COUNT(*) FILTER (WHERE p.realized_pnl > 0) AS wins,
                COUNT(*) FILTER (WHERE p.realized_pnl < 0) AS losses
            FROM positions p
            JOIN deployments d ON d.id = p.deployment_id
            WHERE p.status = 'closed' AND p.closed_at >= $1 AND p.closed_at < $2
                AND d.include_in_reports = true
            """,
            start, end,
        )
        fills = await conn.fetchval(
            """
            SELECT COUNT(*) FROM position_lots pl
            JOIN deployments d ON d.id = pl.deployment_id
            WHERE pl.executed_at >= $1 AND pl.executed_at < $2 AND d.include_in_reports = true
            """,
            start, end,
        )
        return {**dict(row), "fills": fills}


async def pnl_by_strategy_for_range(
    pool: asyncpg.Pool, start: datetime, end: datetime,
) -> list[asyncpg.Record]:
    """Realized P&L within [start, end), grouped by strategy_name --
    the Reports page's "By Strategy" breakdown (this app's analogue of
    a personal-finance report's spend-by-category table): which
    strategies actually made or lost money in the SELECTED period,
    not all-time — a strategy that's up all-time but had a bad week
    should show that clearly here, not get hidden behind its own
    lifetime total. Excludes deployments with include_in_reports=false
    (see the 0009 migration's own comment) — a strategy run only by a
    toggled-off deployment simply won't appear here, same as if it were
    never deployed."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                d.strategy_name,
                SUM(p.realized_pnl)::float8 AS realized_pnl,
                COUNT(*) AS positions_closed
            FROM positions p
            JOIN deployments d ON d.id = p.deployment_id
            WHERE p.status = 'closed' AND p.closed_at >= $1 AND p.closed_at < $2
                AND d.include_in_reports = true
            GROUP BY d.strategy_name
            ORDER BY realized_pnl DESC
            """,
            start, end,
        )


async def pnl_by_deployment_for_range(
    pool: asyncpg.Pool, start: datetime, end: datetime,
) -> list[asyncpg.Record]:
    """Realized P&L within [start, end), grouped by deployment -- the
    Reports page's "By Deployment" leaderboard, sorted best to worst
    for the selected period specifically. Excludes deployments with
    include_in_reports=false outright (see the 0009 migration's own
    comment) — the whole point of this row-per-deployment breakdown is
    "which deployments moved the needle," and a toggled-off one has
    opted out of moving it here."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                d.id AS deployment_id,
                d.deployment_name,
                d.strategy_name,
                SUM(p.realized_pnl)::float8 AS realized_pnl,
                COUNT(*) AS positions_closed
            FROM positions p
            JOIN deployments d ON d.id = p.deployment_id
            WHERE p.status = 'closed' AND p.closed_at >= $1 AND p.closed_at < $2
                AND d.include_in_reports = true
            GROUP BY d.id, d.deployment_name, d.strategy_name
            ORDER BY realized_pnl DESC
            """,
            start, end,
        )


async def list_strategy_leaderboard(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """All-time (no date bound at all) realized P&L per strategy_name,
    across every deployment that ever ran it — active, paused, AND
    stopped alike. Deliberately not scoped to live deployments only
    (unlike Portfolio's own capital-utilization section, a few lines
    away in the frontend): "which strategy has actually made the most
    money since I started" is a question a since-stopped strategy's
    history is very much part of the answer to, not something that
    should quietly disappear once you stop the deployment that
    produced it.

    Returns gross_win/gross_loss (sums of only the positive/negative
    realized_pnl values) rather than a pre-computed profit factor —
    same division-with-Infinity-and-null-edge-cases convention Detail's
    own Stats tab already computes client-side from raw pnls (see
    detail.js), so the frontend does the exact same math here instead
    of a second, potentially-drifting server-side formula.

    Excludes closed positions booked under an include_in_reports=false
    deployment (see the 0009 migration's own comment) — unlike the
    active/paused/stopped inclusivity above, this one IS a real
    exclusion: a toggled-off deployment's history doesn't count toward
    "which strategy has made the most money" here, by the same logic
    that keeps it out of every other cross-deployment view. Note this
    can shrink deployments_count for a strategy that also has
    toggled-on deployments, same as it shrinks the P&L sum -- it's not
    just zeroing out one deployment's contribution in isolation.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                d.strategy_name,
                SUM(p.realized_pnl)::float8 AS realized_pnl,
                COUNT(*) AS positions_closed,
                COUNT(*) FILTER (WHERE p.realized_pnl > 0) AS wins,
                COUNT(*) FILTER (WHERE p.realized_pnl < 0) AS losses,
                COALESCE(SUM(p.realized_pnl) FILTER (WHERE p.realized_pnl > 0), 0)::float8 AS gross_win,
                COALESCE(SUM(p.realized_pnl) FILTER (WHERE p.realized_pnl < 0), 0)::float8 AS gross_loss,
                COUNT(DISTINCT d.id) AS deployments_count
            FROM positions p
            JOIN deployments d ON d.id = p.deployment_id
            WHERE p.status = 'closed' AND d.include_in_reports = true
            GROUP BY d.strategy_name
            ORDER BY realized_pnl DESC
            """,
        )


# ═════════════════════════════════════════════════════════════════════
# REPORTS
# ═════════════════════════════════════════════════════════════════════

async def build_report(pool: asyncpg.Pool, deployment_id: UUID) -> dict[str, Any]:
    """One deployment's own report -- deliberately NOT filtered by
    include_in_reports, same reasoning as list_pnl_digest_for_deployment
    above: a specific deployment_id was asked for, so its own numbers
    are shown regardless of whether it's opted out of the CROSS-
    deployment reports the toggle actually governs."""
    async with pool.acquire() as conn:
        deployment = await conn.fetchrow(
            "SELECT * FROM deployments WHERE id = $1", deployment_id
        )
        if deployment is None:
            return {}

        closed = await conn.fetch(
            """
            SELECT realized_pnl, opened_at, closed_at FROM positions
            WHERE deployment_id = $1 AND status = 'closed'
            """,
            deployment_id,
        )
        open_positions = await conn.fetch(
            "SELECT * FROM positions WHERE deployment_id = $1 AND status = 'open'",
            deployment_id,
        )

        pnls = [float(r["realized_pnl"]) for r in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        return {
            "deployment_id": str(deployment_id),
            "deployment_name": deployment["deployment_name"],
            "strategy_name": deployment["strategy_name"],
            "mode": deployment["mode"],
            "status": deployment["status"],
            "initial_capital": float(deployment["initial_capital"]),
            "current_cash": float(deployment["current_cash"]),
            "closed_positions": len(closed),
            "open_positions": len(open_positions),
            "total_realized_pnl": round(sum(pnls), 2) if pnls else 0.0,
            "win_rate_pct": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        }


# ═════════════════════════════════════════════════════════════════════
# KITE SESSION — the one place the daily access_token is persisted
# ═════════════════════════════════════════════════════════════════════

async def get_kite_session(pool: asyncpg.Pool) -> Optional[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM kite_sessions WHERE id = 1")


async def set_kite_session(
    pool: asyncpg.Pool, access_token: str, login_time: Optional[datetime] = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO kite_sessions (id, access_token, login_time, updated_at)
            VALUES (1, $1, $2, now())
            ON CONFLICT (id) DO UPDATE
                SET access_token = $1, login_time = $2, updated_at = now()
            """,
            access_token, login_time,
        )


# ═════════════════════════════════════════════════════════════════════
# STRATEGY SETTINGS — the admin enable/disable toggle layered on top of
# app.strategies.registry's in-memory registrations (see registry.py's
# own module docstring: registration itself is still pure Python/import-
# time, this table is ONLY the "should this show up in the catalog"
# flag on top of that).
# ═════════════════════════════════════════════════════════════════════

async def ensure_strategy_settings(pool: asyncpg.Pool, strategy_names: list[str]) -> None:
    """
    Insert a default enabled=true row for any of the given (currently-
    registered) strategy names that doesn't already have one — called
    once at startup so every registered strategy always has a row from
    its very first boot, meaning "missing row = enabled" never actually
    has to be relied on at query time. Existing rows (including ones an
    admin has already disabled) are left untouched — ON CONFLICT DO
    NOTHING, not an upsert.
    """
    if not strategy_names:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO strategy_settings (strategy_name) VALUES ($1) ON CONFLICT DO NOTHING",
            [(n,) for n in strategy_names],
        )


async def get_strategy_enabled_map(pool: asyncpg.Pool) -> dict[str, bool]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT strategy_name, enabled FROM strategy_settings")
        return {r["strategy_name"]: r["enabled"] for r in rows}


async def is_strategy_enabled(pool: asyncpg.Pool, strategy_name: str) -> bool:
    """True if there's no row at all (see ensure_strategy_settings — in
    practice this only happens for a strategy_name nothing has ever
    registered, which create_deployment already allows independently of
    this check — see DeploymentManager.create_deployment's own
    docstring)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enabled FROM strategy_settings WHERE strategy_name = $1", strategy_name,
        )
        return True if row is None else row["enabled"]


async def set_strategy_enabled(pool: asyncpg.Pool, strategy_name: str, enabled: bool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategy_settings (strategy_name, enabled, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (strategy_name) DO UPDATE
                SET enabled = $2, updated_at = now()
            """,
            strategy_name, enabled,
        )


# ═════════════════════════════════════════════════════════════════════
# USERS  (see app/db/migrations/0005_users_and_audit.sql for the schema
# and app/rbac.py for the (currently no-op) authorization extension
# point `role` exists for)
# ═════════════════════════════════════════════════════════════════════

async def count_users(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT count(*) FROM users")


async def create_user(
    pool: asyncpg.Pool, username: str, password_hash: str, role: str = "member",
) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            username, password_hash, role,
        )


async def get_user_by_username(pool: asyncpg.Pool, username: str) -> Optional[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE username = $1", username)


async def get_user_by_id(pool: asyncpg.Pool, user_id: UUID) -> Optional[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)


async def list_users(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, username, role, is_active, created_at, last_login_at
            FROM users ORDER BY created_at
            """,
        )


async def update_user_password(pool: asyncpg.Pool, user_id: UUID, password_hash: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET password_hash = $2 WHERE id = $1", user_id, password_hash,
        )


async def update_user_last_login(pool: asyncpg.Pool, user_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_login_at = now() WHERE id = $1", user_id,
        )


async def bump_session_version(pool: asyncpg.Pool, user_id: UUID) -> int:
    """Invalidates every session ever issued for this user — see
    migration 0006's own comment. Callers follow this with
    cache.refresh_now("user_session_versions") so the bump is visible
    to AuthMiddleware immediately, not after the cache's next scheduled
    refresh — the whole point is a leaked/stolen session stops working
    the moment you act, not up to N seconds later. Returns the new
    version so a caller can, if it wants to, immediately re-stamp its
    OWN current session with it (see change_password's own use of
    this — the account owner making the change stays logged in; every
    OTHER already-issued session for this user does not)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET session_version = session_version + 1 WHERE id = $1 "
            "RETURNING session_version",
            user_id,
        )
        return row["session_version"]


async def get_all_session_versions(pool: asyncpg.Pool) -> dict[str, int]:
    """Powers the cached `user_session_versions` key AuthMiddleware
    checks on every authenticated request (see app/cache.py) — one
    query for every user's current version, not a live per-request DB
    hit. Keyed by user_id as a STRING, matching how it's stored in the
    session cookie's own JSON payload (see routers/auth.py's login())."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, session_version FROM users")
        return {str(r["id"]): r["session_version"] for r in rows}


# ═════════════════════════════════════════════════════════════════════
# AUDIT LOG  (written by app/auth.py's AuditLogMiddleware — one row per
# state-changing request that reached the ASGI app, not by individual
# routers; see the middleware's own docstring)
# ═════════════════════════════════════════════════════════════════════

async def record_audit_log(
    pool: asyncpg.Pool,
    user_id: Optional[UUID],
    username: Optional[str],
    method: str,
    path: str,
    status_code: Optional[int],
    request_body: Optional[dict],
    remote_addr: Optional[str],
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit_log
                (user_id, username, method, path, status_code, request_body, remote_addr)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            user_id, username, method, path, status_code, request_body, remote_addr,
        )


async def list_audit_log(
    pool: asyncpg.Pool, offset: int = 0, limit: int = 200,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM audit_log
            ORDER BY occurred_at DESC
            OFFSET $1 LIMIT $2
            """,
            offset, limit,
        )


# ═════════════════════════════════════════════════════════════════════
# STRATEGY PRESETS  (named, reusable config snapshots for the Deploy
# modal — see migration 0007's own comment)
# ═════════════════════════════════════════════════════════════════════

async def create_preset(
    pool: asyncpg.Pool, strategy_name: str, preset_name: str, config: dict,
) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO strategy_presets (strategy_name, preset_name, config)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            strategy_name, preset_name, config,
        )


async def list_presets(pool: asyncpg.Pool, strategy_name: str) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM strategy_presets WHERE strategy_name = $1 ORDER BY preset_name",
            strategy_name,
        )


async def get_preset(pool: asyncpg.Pool, preset_id: UUID) -> Optional[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM strategy_presets WHERE id = $1", preset_id)


async def delete_preset(pool: asyncpg.Pool, preset_id: UUID) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM strategy_presets WHERE id = $1", preset_id)
        return result != "DELETE 0"


# ═════════════════════════════════════════════════════════════════════
# TAG CATALOG  (predefined, admin-managed deployment labels — Settings
# -> Tags; see migration 0010's own comment for why this is a curated
# catalog rather than freeform per-deployment text, and why the
# synthetic "Excluded from reports" tag deliberately never lives here)
# ═════════════════════════════════════════════════════════════════════

async def list_tags(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM tag_catalog ORDER BY name")


async def get_tag_by_name(pool: asyncpg.Pool, name: str) -> Optional[asyncpg.Record]:
    """Router-side duplicate check before create_tag, same "check first,
    DB constraint as backstop" pattern get_deployment_by_name already
    establishes for deployment_name uniqueness."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM tag_catalog WHERE name = $1", name)


async def create_tag(pool: asyncpg.Pool, name: str) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "INSERT INTO tag_catalog (name) VALUES ($1) RETURNING *", name,
        )


async def delete_tag(pool: asyncpg.Pool, tag_id: UUID) -> bool:
    """Deletes the catalog row AND strips this tag's name out of every
    deployment currently carrying it -- deployments.tags has no FK back
    to tag_catalog (an array column can't express that), so a deleted
    tag would otherwise leave a dangling name sitting in some
    deployment's list forever, invisible to the picker that created it
    but still rendered as a chip. Both statements run against the same
    connection so a crash between them can't leave the two halves
    inconsistent -- not wrapped in an explicit transaction since
    asyncpg's default autocommit-per-statement is fine here (a crash
    between them just means the harmless direction: the tag_catalog row
    is gone but a now-orphaned name lingers in one array a bit longer,
    the same state a delete-then-crash would leave without this second
    statement at all)."""
    async with pool.acquire() as conn:
        tag = await conn.fetchrow("SELECT name FROM tag_catalog WHERE id = $1", tag_id)
        if tag is None:
            return False
        result = await conn.execute("DELETE FROM tag_catalog WHERE id = $1", tag_id)
        await conn.execute(
            "UPDATE deployments SET tags = array_remove(tags, $1) WHERE $1 = ANY(tags)",
            tag["name"],
        )
        return result != "DELETE 0"


# ── Web Push subscriptions (mobile notifications) ───────────────────────

async def save_push_subscription(
    pool: asyncpg.Pool, endpoint: str, p256dh: str, auth: str, user_agent: Optional[str] = None,
) -> None:
    """Upsert by endpoint (UNIQUE — see migration 0011's own comment):
    re-subscribing the same device (e.g. after clearing/re-granting
    permission) updates the existing row's keys rather than
    accumulating a duplicate that would double-send that phone."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (endpoint) DO UPDATE SET p256dh = $2, auth = $3, user_agent = $4
            """,
            endpoint, p256dh, auth, user_agent,
        )


async def delete_push_subscription(pool: asyncpg.Pool, endpoint: str) -> None:
    """Called both from the explicit "turn off notifications" action AND
    from app/notifications.py whenever a push service reports an
    endpoint as gone (410 Gone / 404) — a subscription a phone itself
    revoked (uninstalled the PWA, cleared site data, ...) is dead
    weight otherwise, silently retried forever."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM push_subscriptions WHERE endpoint = $1", endpoint)


async def list_push_subscriptions(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM push_subscriptions ORDER BY created_at")


async def get_deployment_notifications_enabled(pool: asyncpg.Pool, deployment_id: UUID) -> bool:
    """Read fresh from the DB on every execution notification (see
    DeploymentRunner.notify_execution's own docstring) rather than
    cached on the runner in memory — a toggle flipped via PATCH
    /deployments/{id} then takes effect on the very next execution, no
    restart needed. Defaults True (matches the column's own DEFAULT) if
    the deployment somehow doesn't exist any more by the time this
    runs — fail open on notifying, never silently swallow a real trade
    because of a race with e.g. a delete."""
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT notifications_enabled FROM deployments WHERE id = $1", deployment_id,
        )
    return True if value is None else bool(value)
