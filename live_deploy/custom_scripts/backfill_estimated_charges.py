#!/usr/bin/env python3
"""
live_deploy — custom_scripts/backfill_estimated_charges.py

ONE-TIME BACKFILL for the "with brokerage" global toggle: migration
0016 added positions.estimated_charges (DEFAULT 0), and queries.
record_fill now computes it for every NEWLY closed position going
forward — this script fills it in for every position that was already
closed BEFORE that migration ran, using the exact same formula
(app/charges.py's estimate_options_charges) against each position's own
entry price (avg_entry_price) and exit price (the qty-weighted average
of its closing-direction lots — the same `exit_price` list_positions
itself already computes via _POSITION_LOT_AGG_JOIN; every position here
closes via exactly one fill, so this is just that fill's own price, not
a real average).

Selects WHERE estimated_charges = 0 (the column's own default) rather
than every closed position, so it's safe to re-run: a position already
backfilled (or closed live, post-migration) is skipped, not
recomputed. A genuinely free (Rs 0) trade is not a real scenario this
app's strategies ever produce, so this filter is a safe proxy for
"not yet backfilled," not a real false-negative risk.

Does NOT touch realized_pnl, cash, or anything else — purely fills in
the one new column, nothing a running deployment's own logic reads.

USAGE:
    python3 custom_scripts/backfill_estimated_charges.py --dry-run
    python3 custom_scripts/backfill_estimated_charges.py
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.charges import estimate_options_charges
from app.config import load_config
from app.db.pool import close_pool, create_pool


async def main(dry_run: bool) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        async with pool.acquire() as conn:
            # p.qty is reset to 0 on close (see record_fill) -- the real
            # closed quantity is the OPENING-direction lots' own total,
            # same total_qty computation _POSITION_LOT_AGG_JOIN uses.
            rows = await conn.fetch(
                """
                SELECT p.id, p.deployment_id, p.symbol, p.side, p.avg_entry_price,
                       lot_agg.total_qty, lot_agg.exit_price
                FROM positions p
                LEFT JOIN LATERAL (
                    SELECT
                        COALESCE(SUM(pl.qty) FILTER (
                            WHERE (p.side = 'long' AND pl.action = 'buy') OR (p.side = 'short' AND pl.action = 'sell')
                        ), 0) AS total_qty,
                        CASE WHEN SUM(pl.qty) FILTER (
                            WHERE (p.side = 'long' AND pl.action = 'sell') OR (p.side = 'short' AND pl.action = 'buy')
                        ) > 0
                        THEN SUM(pl.qty * pl.price) FILTER (
                            WHERE (p.side = 'long' AND pl.action = 'sell') OR (p.side = 'short' AND pl.action = 'buy')
                        ) / SUM(pl.qty) FILTER (
                            WHERE (p.side = 'long' AND pl.action = 'sell') OR (p.side = 'short' AND pl.action = 'buy')
                        )
                        ELSE NULL END AS exit_price
                    FROM position_lots pl
                    WHERE pl.position_id = p.id
                ) lot_agg ON true
                WHERE p.status = 'closed' AND p.estimated_charges = 0
                ORDER BY p.opened_at
                """
            )

            print(f"{len(rows)} closed position(s) need estimated_charges backfilled.")
            if not rows:
                return

            total_charges = 0.0
            skipped = 0
            updates = []
            for r in rows:
                if r["exit_price"] is None:
                    # No closing lot found at all -- shouldn't happen for a
                    # 'closed' position, but skip rather than guess if it does.
                    skipped += 1
                    continue
                entry_price = float(r["avg_entry_price"])
                exit_price = float(r["exit_price"])
                qty = float(r["total_qty"] or 0)
                if qty <= 0:
                    skipped += 1
                    continue
                buy_price, sell_price = (entry_price, exit_price) if r["side"] == "long" else (exit_price, entry_price)
                charges = estimate_options_charges(buy_price, sell_price, qty)
                total_charges += charges
                updates.append((r["id"], charges))

            print(f"  {len(updates)} to update, {skipped} skipped (no closing lot / zero qty found).")
            print(f"  Total estimated_charges to apply: {total_charges:,.2f}")

            if dry_run:
                print("  --dry-run: no writes made.")
                return

            async with conn.transaction():
                for position_id, charges in updates:
                    await conn.execute(
                        "UPDATE positions SET estimated_charges = $2 WHERE id = $1",
                        position_id, charges,
                    )
            print(f"  Done -- {len(updates)} position(s) updated.")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
