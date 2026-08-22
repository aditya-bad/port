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
from datetime import datetime, timedelta, timezone
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


async def list_positions_with_episode(
    pool: asyncpg.Pool, deployment_id: UUID, status: Optional[str] = None,
) -> list[dict]:
    """Step 103 — same rows as list_positions, each tagged with which
    EPISODE it belongs to (see _group_into_episodes/
    get_positional_episode_mtm_rows) via `episode_opened_at`/
    `episode_closed_at` fields: the earliest `opened_at`/latest
    `closed_at` (None if any leg in the episode is still open) across
    every position that overlapped in time with this one.

    Backs the Detail page's Stats tab "per trade" vs "per position"
    toggle (Step 103): a straddle's CE leg and PE leg, or a leg before
    and after a roll, are separate `positions` rows but the SAME
    episode tag — grouping client-side by that tag turns "per trade"
    stats (one verdict per row) into "per position" stats (one verdict
    per whole strategic bet) without a second, differently-shaped
    endpoint. Deliberately mode-agnostic — an intraday deployment's
    same-day straddle-plus-adjustments needs exactly the same combining
    a positional deployment's multi-day hold does; nothing here reads
    `deployments.mode` at all.

    ALWAYS fetches and groups the deployment's FULL position history
    (every status) before filtering to the requested `status` — an
    episode's correct start/end can depend on a leg with a DIFFERENT
    status than the one being filtered for (e.g. a closed leg whose
    episode also includes a still-open one), so filtering first would
    tag some rows with an incomplete, wrong episode window.
    `status=None` (or `"all"`/`""`, from a query string that can't pass
    a real None) returns every position, still tagged.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM positions WHERE deployment_id = $1 ORDER BY opened_at ASC",
            deployment_id,
        )
    if not rows:
        return []

    episode_of: dict[UUID, tuple] = {}
    for ep in _group_into_episodes(rows):
        for r in ep["rows"]:
            episode_of[r["id"]] = (ep["start"], ep["end"])

    want = None if status in (None, "", "all") else status
    out = []
    for r in rows:
        if want is not None and r["status"] != want:
            continue
        d = dict(r)
        d["episode_opened_at"], d["episode_closed_at"] = episode_of[r["id"]]
        out.append(d)
    out.sort(key=lambda d: d["opened_at"], reverse=True)
    return out


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
    """One point per IST CALENDAR DAY (Step 96), not every ~5-minute row
    snapshot_loop ever wrote — specifically the LAST snapshot recorded
    that day. Since the app process keeps running (and snapshot_loop
    keeps firing) straight through and past market close, that last
    snapshot is effectively "today's post-market equity" — cash plus
    whatever's still open (nothing, for a normal intraday deployment
    that's already force-exited everything by then) — not a mid-session
    reading. This directly replaces the old "every 5 minutes, all day"
    equity curve, which visibly moved on nothing more than an open
    option leg's live mark-to-market premium ticking around intraday —
    real information for "how's today going right now," but noise for
    "how has this deployment actually performed," which is what the
    equity curve is for.

    Step 97 briefly added `day_high`/`day_low`/`max_profit`/`max_loss`
    (an intraday range per day) on top of this — REMOVED again in Step
    99: those were computed off `total_value`, which at the time still
    double-counted a still-open short leg's entry premium (see
    DeploymentManager._snapshot_one's own comment for the full
    derivation), so "max profit that day" was really measuring premium
    COLLECTED, not premium actually EARNED. Rather than re-derive the
    same feature off the now-corrected number, it was dropped entirely
    by explicit request — one clean value per day, no range.

    `limit` means "at most this many most-recent DAYS" (picked via the
    inner ORDER BY ... DESC LIMIT, then re-sorted ascending for the
    chart), not raw rows — 1000 days is years of history, same
    practical headroom the old row-based limit had.

    Deliberately still returns each row's REAL `snapshot_at` (an actual,
    precise timestamptz from a real snapshot_loop tick), never a
    synthesized day-boundary value — `date_trunc('day', ... AT TIME ZONE
    'Asia/Kolkata')` is used only to GROUP rows by IST calendar day
    (DISTINCT ON's key), never returned as the value itself, so this
    can't reintroduce the naive-datetime class of bug this codebase has
    already been bitten by more than once (see Step 92/95's own
    write-ups) — every timestamp leaving this function is exactly as
    timezone-aware as the one that went into the database.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM (
                SELECT DISTINCT ON (date_trunc('day', snapshot_at AT TIME ZONE 'Asia/Kolkata'))
                    *
                FROM deployment_snapshots
                WHERE deployment_id = $1
                ORDER BY date_trunc('day', snapshot_at AT TIME ZONE 'Asia/Kolkata') DESC, snapshot_at DESC
                LIMIT $2
            ) recent_days
            ORDER BY snapshot_at
            """,
            deployment_id, limit,
        )


async def list_portfolio_equity_curve(
    pool: asyncpg.Pool, limit: int = 1000,
) -> list[asyncpg.Record]:
    """One combined equity-curve point per IST CALENDAR DAY (Step 96),
    summed across EVERY deployment's snapshots (not just currently-active
    ones) — the Portfolio view's whole-account equity curve.

    Used to be one point per fixed `bucket_seconds` time bucket (5
    minutes) — replaced with one point per day for the same reason
    list_snapshots (the per-deployment version, see its own docstring)
    was: an intraday reading moves on nothing more than an open
    option leg's live mark-to-market premium ticking around, which is
    noise for "how has this portfolio actually performed," not signal.
    `bucket_seconds` is gone entirely — there's no fixed-interval
    bucketing left to parameterize.

    For each deployment, only its OWN last snapshot of a given IST day
    contributes to that day's sum (not a same-instant bucket the way
    fixed time-bucketing needed — different deployments' snapshot_loop
    rows land at slightly different literal timestamps even within the
    same iteration, but "each one's last snapshot that day" doesn't
    depend on them lining up at all). `bucket_at` is the MAX of those
    real per-deployment snapshot_at values for the day, not a
    synthesized day-boundary — deliberately still a genuine, precise
    timestamptz for the same naive-datetime-avoidance reason
    list_snapshots documents.

    Deliberately not scoped to any particular deployment status: a
    day's sum reflects however many deployments actually had a runner
    (i.e. were active) with at least one snapshot THAT DAY — a
    since-paused deployment's older days still contribute their own
    historical sums (paper-trading history doesn't retroactively
    change), it just stops contributing to NEW days the moment it's no
    longer active, same as it stops accumulating its own per-deployment
    curve.

    IS scoped by include_in_reports though, unlike status — see the
    0009 migration's own comment: this is Portfolio's own combined
    equity curve, a cross-deployment aggregate the toggle exists
    specifically to let a deployment opt out of, same as every other
    view in this file that joins deployments for exactly this reason.

    `limit` means "at most this many most-recent DAYS" (same convention
    as list_snapshots), not raw rows.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            WITH daily_per_deployment AS (
                SELECT DISTINCT ON (
                    ds.deployment_id, date_trunc('day', ds.snapshot_at AT TIME ZONE 'Asia/Kolkata')
                )
                    ds.deployment_id, ds.snapshot_at, ds.total_value, ds.realized_pnl_cumulative
                FROM deployment_snapshots ds
                JOIN deployments d ON d.id = ds.deployment_id
                WHERE d.include_in_reports = true
                ORDER BY
                    ds.deployment_id,
                    date_trunc('day', ds.snapshot_at AT TIME ZONE 'Asia/Kolkata'),
                    ds.snapshot_at DESC
            ),
            daily_totals AS (
                SELECT
                    date_trunc('day', snapshot_at AT TIME ZONE 'Asia/Kolkata') AS day_key,
                    MAX(snapshot_at) AS bucket_at,
                    SUM(total_value) AS total_value,
                    SUM(realized_pnl_cumulative) AS realized_pnl_cumulative,
                    COUNT(DISTINCT deployment_id) AS deployments_count
                FROM daily_per_deployment
                GROUP BY day_key
            )
            SELECT bucket_at, total_value, realized_pnl_cumulative, deployments_count
            FROM (
                SELECT * FROM daily_totals ORDER BY day_key DESC LIMIT $1
            ) recent
            ORDER BY day_key
            """,
            limit,
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


async def get_intraday_mtm_range(
    pool: asyncpg.Pool, deployment_id: UUID, period: str = "day",
) -> dict[datetime, dict[str, float]]:
    """Step 100 — {period_start: {"max_profit": ..., "max_loss": ...}}
    for an "intraday" deployment: the best/worst RUNNING TOTAL of
    realized_pnl reached at any point within each period, tracked
    trade-by-trade (every position close that period, in the exact
    chronological order they actually closed — a SQL window function's
    running SUM, not a coarse snapshot sample), starting fresh at 0
    each period. This is exact, not approximate: an intraday deployment
    by design has nothing genuinely "open" once the day's done (see
    DeploymentManager._snapshot_one's own Step 99 comment for why
    open_positions_value is never even computed for these), so every
    bit of that day's P&L movement happened at a discrete trade-close
    instant — summing THOSE, in order, in exactly the sequence they
    occurred, captures the day's real M2M swing regardless of how many
    trades or adjustments happened, with no risk of missing a peak
    between snapshot_loop's ~5-minute samples the way a snapshot-based
    approach would.

    `max_profit` can be negative (the least-bad point reached on an
    all-losing period); `max_loss` can be positive (the least-good
    point reached on an all-winning one) — both are just "the running
    total's own high/low that period," not clamped to a sign.

    Works for any period (day/week/month) — a week's own running total,
    tracked trade-by-trade across every day in it, is just as
    well-defined as a single day's.
    """
    if period not in _DIGEST_PERIODS:
        raise ValueError(f"period must be one of {_DIGEST_PERIODS}, got {period!r}")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH per_close AS (
                SELECT
                    (date_trunc($2, closed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS period_start,
                    SUM(realized_pnl) OVER (
                        PARTITION BY date_trunc($2, closed_at AT TIME ZONE 'Asia/Kolkata')
                        ORDER BY closed_at
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ) AS running_pnl
                FROM positions
                WHERE deployment_id = $1 AND status = 'closed' AND closed_at IS NOT NULL
            )
            SELECT period_start, MAX(running_pnl)::float8 AS max_profit, MIN(running_pnl)::float8 AS max_loss
            FROM per_close
            GROUP BY period_start
            """,
            deployment_id, period,
        )
    return {r["period_start"]: {"max_profit": r["max_profit"], "max_loss": r["max_loss"]} for r in rows}


# Step 102 — how close two positions' opened_at/closed_at have to be to
# count as the SAME strategy episode rather than two separate ones (see
# get_positional_episode_mtm_rows). A straddle's own legs, plus any
# adjustment/roll that opens a new leg while another leg is still open,
# already OVERLAP in time and merge with no tolerance needed at all —
# this constant only matters for the one pattern that wouldn't
# otherwise overlap: a single-leg roll with no other leg bridging the
# gap, where "close the old leg" and "open the new leg" are two
# sequential awaits a fraction of a second apart. 5 minutes matches the
# smallest candle interval used across every strategy in this codebase
# (a natural "still the same decision" bound) — comfortably longer than
# any real roll's execution latency, comfortably shorter than any
# genuine "flat, waiting for the next signal" gap on a positional
# deployment (realistically minutes to days).
_EPISODE_GAP_TOLERANCE = timedelta(minutes=5)


def _group_into_episodes(positions: list) -> list[dict]:
    """Step 103 — the actual interval-merge, factored out of
    get_positional_episode_mtm_rows (Step 102) so it can also back
    list_positions_with_episode below: standard sweep-line merge of
    every position's own [opened_at, closed_at] interval (open
    positions treated as unbounded/"now"), bridging real time overlap
    with no tolerance needed, plus `_EPISODE_GAP_TOLERANCE` for the one
    pattern that wouldn't otherwise overlap (a single-leg roll with
    nothing else open) — see get_positional_episode_mtm_rows' own
    docstring for the full reasoning, which still applies unchanged
    here. `positions` must already be sorted by opened_at ASCENDING.
    Returns `[{"start": ..., "end": ... or None, "rows": [...]}]` in
    THE SAME order as the input (oldest episode first) — callers sort
    however they need afterward.
    """
    episodes: list[dict] = []
    for p in positions:
        if episodes and (
            episodes[-1]["end"] is None
            or p["opened_at"] <= episodes[-1]["end"] + _EPISODE_GAP_TOLERANCE
        ):
            ep = episodes[-1]
            ep["rows"].append(p)
            if ep["end"] is not None:
                ep["end"] = None if p["closed_at"] is None else max(ep["end"], p["closed_at"])
        else:
            episodes.append({"start": p["opened_at"], "end": p["closed_at"], "rows": [p]})
    return episodes


async def get_positional_episode_mtm_rows(
    pool: asyncpg.Pool, deployment_id: UUID, limit: int = 400,
) -> list[dict]:
    """Step 102 — ONE ROW PER EPISODE for a "positional" deployment, not
    per individual `positions` table row the way Step 101 first did it.

    Explicit correction from the user: a straddle's ATM sell PLUS every
    adjustment PLUS every roll are all still ONE strategic position, and
    should combine into ONE max-profit/max-loss number — exactly the
    same "combine everything currently open" principle already used for
    a deployment's own realized_pnl/unrealized_pnl totals (see
    routers/deployments.py's `unrealized_map` summing `_mark_to_market`
    across every open position for a deployment, not reporting one
    number per instrument). Step 101 treated each `positions` row (one
    per instrument_token's own open->close lifecycle) as its own M2M
    row — wrong for any multi-leg strategy: a CE leg and a PE leg of the
    same straddle, or the old and new leg of a roll, are separate
    `positions` rows but the SAME strategic bet.

    An "episode" is a maximal run of time during which this deployment
    had AT LEAST ONE open position, found by merging every position's
    own [opened_at, closed_at] interval (closed_at treated as
    unbounded/"now" while still open) with any other interval it
    overlaps or is within `_EPISODE_GAP_TOLERANCE` of — standard
    sweep-line interval merging, sorted by opened_at ascending. Once an
    episode contains a still-open position, it has no end (stays
    "ongoing") until queried again later, since the deployment hasn't
    gone flat yet. A deployment that never fully flattens (always
    something open, forever rolling) legitimately collapses to ONE
    episode covering its entire history — that's not a bug, it's the
    same "whole position combined" principle taken to its natural
    conclusion.

    For each episode: `realized_pnl` sums every constituent position's
    own realized_pnl (0 for the ones still open); `positions_closed`/
    `wins`/`losses` count constituent positions by their own outcome;
    `fills` counts every position_lot across every constituent position.
    `max_profit`/`max_loss` come from every deployment_snapshot recorded
    across the WHOLE episode's span (start of its earliest leg to the
    end of its latest, or now) — MAX/MIN of total_value relative to the
    value at the episode's own first snapshot, same "delta from where
    THIS thing started" principle the intraday version applies per-day.
    This is already deployment-wide per snapshot (DeploymentManager.
    _snapshot_one sums open_positions_value across every open position),
    so merging positions into episodes for the ROW STRUCTURE is the only
    change needed here — the underlying snapshot numbers were already
    correct for a multi-leg position.

    One extra snapshot-range query per episode (not a single joined
    query) — same reasoning as Step 101: a positional deployment doesn't
    churn positions (or episodes) the way an intraday one churns trades,
    so this is a small, cheap number of extra round trips on a
    Detail-page load, not a hot path.

    None (not 0.0) if no deployment_snapshots exist yet inside an
    episode's window, and no attempt to reconstruct pre-Step-99
    double-counted historical snapshots — both same as Step 101.

    `limit` most-recent EPISODES (by their own start), not positions or
    calendar periods — one multi-leg episode can span many `positions`
    rows, so this is not simply "the most recent `limit` positions."
    """
    async with pool.acquire() as conn:
        positions = await conn.fetch(
            """
            SELECT id, opened_at, closed_at, status, realized_pnl::float8 AS realized_pnl
            FROM positions
            WHERE deployment_id = $1
            ORDER BY opened_at ASC
            """,
            deployment_id,
        )
        if not positions:
            return []

        episodes = _group_into_episodes(positions)
        episodes.sort(key=lambda e: e["start"], reverse=True)
        episodes = episodes[:limit]

        now = datetime.now(timezone.utc)
        out = []
        for ep in episodes:
            window_end = ep["end"] or now
            snap = await conn.fetchrow(
                """
                SELECT MAX(total_value) AS high, MIN(total_value) AS low,
                       (array_agg(total_value ORDER BY snapshot_at ASC))[1] AS open_value
                FROM deployment_snapshots
                WHERE deployment_id = $1 AND snapshot_at >= $2 AND snapshot_at <= $3
                """,
                deployment_id, ep["start"], window_end,
            )
            if snap and snap["open_value"] is not None:
                max_profit = float(snap["high"]) - float(snap["open_value"])
                max_loss = float(snap["low"]) - float(snap["open_value"])
            else:
                max_profit = max_loss = None

            position_ids = [r["id"] for r in ep["rows"]]
            fills = await conn.fetchval(
                "SELECT COUNT(*) FROM position_lots WHERE position_id = ANY($1::uuid[])",
                position_ids,
            )
            closed_rows = [r for r in ep["rows"] if r["status"] == "closed"]
            out.append({
                "is_position_row": True,
                "period_start": ep["start"],
                "period_end": ep["end"],
                "realized_pnl": sum(r["realized_pnl"] for r in ep["rows"]),
                "positions_closed": len(closed_rows),
                "wins": sum(1 for r in closed_rows if r["realized_pnl"] > 0),
                "losses": sum(1 for r in closed_rows if r["realized_pnl"] < 0),
                "fills": fills,
                "max_profit": max_profit,
                "max_loss": max_loss,
            })
        return out


async def list_pnl_digest_for_deployment(
    pool: asyncpg.Pool, deployment_id: UUID, mode: str, period: str = "day", limit: int = 400,
) -> list[dict]:
    """This deployment's own realized-P&L trend, shaped differently
    depending on `mode` (Step 101 — passed in by the caller, which
    already has the deployment row for its own 404 check, rather than
    re-fetched here):

    - "intraday": the same calendar day/week/month digest this always
      was (see list_pnl_digest's own docstring for the realized-only
      reasoning and the closes/fills FULL OUTER JOIN this shares), with
      max_profit/max_loss (Step 100) merged on from get_intraday_mtm_range
      by matching `period_start` (both compute it with the exact same
      `date_trunc(..., ... AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE
      'Asia/Kolkata'` round-trip, so the values compare equal as the
      same real timestamptz instant).
    - "positional": `period` is IGNORED entirely — see
      get_positional_episode_mtm_rows' own docstring (Step 102) for why
      a positional deployment's own M2M is "every currently-open
      position combined into one episode," not one row per individual
      `positions` table row (Step 101's first correction) and not a
      calendar bucket (Step 100's original attempt, wrong on both
      counts) — a straddle's legs plus every adjustment/roll on top of
      them are one strategic bet, and get one combined max-profit/
      max-loss number covering that whole episode's own opened_at
      (earliest constituent leg) through closed_at (latest, or None if
      still open).

    Both `positions` and `position_lots` carry `deployment_id` directly
    (not just via position_id -> positions), so the intraday digest is
    a straight WHERE addition to list_pnl_digest's own query shape, not
    a different join structure. `limit` defaults higher than the
    portfolio digest's 30 (400 comfortably covers a full year of daily
    buckets, ~371 for a GitHub-style 53-week grid) since the Detail
    page's Calendar heatmap (an intraday-only view — see its own
    router) wants a full year in view, not a handful of recent periods.
    Deliberately NOT filtered by include_in_reports, unlike its
    portfolio-wide sibling above — a deployment's own trend on its own
    Detail page shows its own history regardless of whether it's opted
    out of cross-deployment reports (see the 0009 migration's own
    comment).

    Returns `list[dict]`, not `list[asyncpg.Record]` like every other
    function here — asyncpg.Record is immutable, and merging a second
    query's columns onto rows from this one (the intraday path) needs a
    mutable structure.
    """
    if mode == "positional":
        return await get_positional_episode_mtm_rows(pool, deployment_id, limit=limit)

    if period not in _DIGEST_PERIODS:
        raise ValueError(f"period must be one of {_DIGEST_PERIODS}, got {period!r}")
    async with pool.acquire() as conn:
        digest_rows = await conn.fetch(
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

    mtm_map = await get_intraday_mtm_range(pool, deployment_id, period=period)
    out = []
    for r in digest_rows:
        d = dict(r)
        d["is_position_row"] = False
        mtm = mtm_map.get(r["period_start"])
        d["max_profit"] = mtm["max_profit"] if mtm else None
        d["max_loss"] = mtm["max_loss"] if mtm else None
        out.append(d)
    return out


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


# ═════════════════════════════════════════════════════════════════════
# SCRATCH STORAGE (Step 104) — see migration 0012's own comment for the
# "why a schema-less escape hatch" reasoning: a generic (key -> JSONB
# value) slot, per-deployment or app-wide, for whatever small bit of
# state a future ad-hoc feature needs to persist before it's clear it
# deserves a real typed column. Deliberately minimal: get/set/delete
# only, no "list every key" helper -- nothing needs to enumerate a
# whole scratch space yet, and adding that later is a one-function
# addition, not a migration. No API router or UI wired to these yet
# either, on purpose -- there's no concrete feature asking for one, and
# building that speculatively would just be unused surface area; wiring
# it up is cheap the moment a real request actually needs it.
# ═════════════════════════════════════════════════════════════════════

async def get_deployment_scratch(
    pool: asyncpg.Pool, deployment_id: UUID, key: str, default: Any = None,
) -> Any:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM deployment_scratch WHERE deployment_id = $1 AND key = $2",
            deployment_id, key,
        )
    return row["value"] if row is not None else default


async def set_deployment_scratch(
    pool: asyncpg.Pool, deployment_id: UUID, key: str, value: Any,
) -> None:
    """`value` is handed straight to the JSONB column -- any JSON-
    serializable Python value (dict, list, str, number, bool, None) is
    fine, per the codec pool.py registers on every connection."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO deployment_scratch (deployment_id, key, value, updated_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (deployment_id, key) DO UPDATE SET value = $3, updated_at = now()
            """,
            deployment_id, key, value,
        )


async def delete_deployment_scratch(pool: asyncpg.Pool, deployment_id: UUID, key: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM deployment_scratch WHERE deployment_id = $1 AND key = $2",
            deployment_id, key,
        )


async def get_app_scratch(pool: asyncpg.Pool, key: str, default: Any = None) -> Any:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM app_scratch WHERE key = $1", key)
    return row["value"] if row is not None else default


async def set_app_scratch(pool: asyncpg.Pool, key: str, value: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_scratch (key, value, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()
            """,
            key, value,
        )


async def delete_app_scratch(pool: asyncpg.Pool, key: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM app_scratch WHERE key = $1", key)


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
