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
) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO deployments
                (deployment_name, strategy_name, mode, initial_capital,
                 current_cash, config)
            VALUES ($1, $2, $3, $4, $4, $5)
            RETURNING *
            """,
            deployment_name, strategy_name, mode, initial_capital, config,
        )


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


async def set_status(pool: asyncpg.Pool, deployment_id: UUID, status: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE deployments SET status = $2, updated_at = now() WHERE id = $1",
            deployment_id, status,
        )


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
        rows = await conn.fetch(
            """
            SELECT * FROM position_lots
            WHERE deployment_id = $1
            ORDER BY executed_at DESC
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
