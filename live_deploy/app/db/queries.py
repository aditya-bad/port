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
    """Rolls back a single just-created deployment row (and, via the
    same ON DELETE CASCADE foreign keys clear_all_deployments relies
    on, anything already written under it) — used when a deployment is
    created in the DB but its runner then fails to start (e.g. the
    strategy's own on_start() rejects the config), so a failed POST
    /deployments never leaves an orphaned row behind for a caller who
    was told it failed."""
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


async def update_deployment_metadata(
    pool: asyncpg.Pool, deployment_id: UUID,
    deployment_name: Optional[str] = None, notes: Optional[str] = None,
) -> Optional[asyncpg.Record]:
    """
    Partial update for PATCH /deployments/{id} — only the field(s)
    actually passed get written; omitted ones (None) are left untouched,
    NOT overwritten with NULL (a caller renaming a deployment shouldn't
    accidentally blank out its notes, and vice versa). Uses COALESCE
    against the row's own current value rather than a dynamically-built
    SQL string — same fixed query every call, no string-built column
    list to get wrong.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE deployments
            SET deployment_name = COALESCE($2, deployment_name),
                notes = COALESCE($3, notes),
                updated_at = now()
            WHERE id = $1
            RETURNING *
            """,
            deployment_id, deployment_name, notes,
        )


async def set_status(pool: asyncpg.Pool, deployment_id: UUID, status: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE deployments SET status = $2, updated_at = now() WHERE id = $1",
            deployment_id, status,
        )


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
    deployment LIST endpoint in one query instead of one-per-row."""
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
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT
                to_timestamp(floor(extract(epoch FROM snapshot_at) / $1) * $1) AS bucket_at,
                SUM(total_value) AS total_value,
                SUM(realized_pnl_cumulative) AS realized_pnl_cumulative,
                COUNT(DISTINCT deployment_id) AS deployments_count
            FROM deployment_snapshots
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
                WHERE status = 'closed' AND closed_at IS NOT NULL
                GROUP BY 1
            ),
            fills AS (
                SELECT
                    (date_trunc($1, executed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    COUNT(*) AS fills
                FROM position_lots
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
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(realized_pnl), 0)::float8 AS realized_pnl,
                COUNT(*) AS positions_closed,
                COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
                COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses
            FROM positions
            WHERE status = 'closed' AND closed_at >= $1 AND closed_at < $2
            """,
            start, end,
        )
        fills = await conn.fetchval(
            "SELECT COUNT(*) FROM position_lots WHERE executed_at >= $1 AND executed_at < $2",
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
    lifetime total."""
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
    for the selected period specifically."""
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
            WHERE p.status = 'closed'
            GROUP BY d.strategy_name
            ORDER BY realized_pnl DESC
            """,
        )


# ═════════════════════════════════════════════════════════════════════
# REPORTS
# ═════════════════════════════════════════════════════════════════════

async def build_report(pool: asyncpg.Pool, deployment_id: UUID) -> dict[str, Any]:
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
