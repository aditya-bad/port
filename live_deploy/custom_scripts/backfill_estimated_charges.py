#!/usr/bin/env python3
"""
live_deploy — custom_scripts/backfill_estimated_charges.py

ONE-TIME BACKFILL for the "with brokerage" global toggle: migration
0016 added positions.estimated_charges (DEFAULT 0), and queries.
record_fill now computes it for every NEWLY closed position going
forward — this script fills it in for every position that was already
closed BEFORE that migration ran, using the exact same formula
(app/charges.py's charge_breakdown/estimate_options_charges) against
each position's own entry price (avg_entry_price) and exit price (the
qty-weighted average of its closing-direction lots — the same
`exit_price` list_positions itself already computes via
_POSITION_LOT_AGG_JOIN; every position here closes via exactly one
fill, so this is just that fill's own price, not a real average).

Selects WHERE estimated_charges = 0 (the column's own default) rather
than every closed position, so it's safe to re-run: a position already
backfilled (or closed live, post-migration) is skipped, not
recomputed. A genuinely free (Rs 0) trade is not a real scenario this
app's strategies ever produce, so this filter is a safe proxy for
"not yet backfilled," not a real false-negative risk.

Does NOT touch realized_pnl, cash, or anything else — purely fills in
the one new column, nothing a running deployment's own logic reads.

Prints three things before writing anything (same under --dry-run or a
real run): a per-DEPLOYMENT table (so a surprising total can be traced
to which deployment it came from, not assumed to be a bug), a per-
CHARGE-TYPE table (brokerage/STT/exchange/SEBI/GST/stamp duty, each
with its own total and how many positions it actually applied to --
spot-check any one of these against the Tools page's own Brokerage
calculator on a single real trade), and a final "current P&L after
brokerage" headline (total gross realized minus total charges, across
everything being backfilled).

USAGE:
    python3 custom_scripts/backfill_estimated_charges.py --dry-run
    python3 custom_scripts/backfill_estimated_charges.py
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.charges import charge_breakdown
from app.config import load_config
from app.db.pool import close_pool, create_pool

# Order these are reported in -- matches the Tools page's own Brokerage
# calculator breakdown table (static/js/tools.js) and app/charges.py's
# own charge_breakdown() keys, so a figure here can be cross-checked
# against that page for a single trade. "total" (the sum of the other
# six) is reported separately, not as a seventh "type".
_CHARGE_TYPES = ["brokerage", "stt", "exchange", "sebi", "gst", "stamp_duty"]
_CHARGE_LABELS = {
    "brokerage": "Brokerage (flat Rs 20/order)",
    "stt": "STT (0.15% of sell premium)",
    "exchange": "Exchange txn charges (0.03553% of turnover)",
    "sebi": "SEBI charges (Rs 10/crore)",
    "gst": "GST (18% of brokerage+exchange+SEBI)",
    "stamp_duty": "Stamp duty (0.003% of buy premium, rounded to Re 1)",
}


async def main(dry_run: bool) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        async with pool.acquire() as conn:
            # p.qty is reset to 0 on close (see record_fill) -- the real
            # closed quantity is the OPENING-direction lots' own total,
            # same total_qty computation _POSITION_LOT_AGG_JOIN uses.
            # Deliberately NOT scoped by deployment status or
            # include_in_reports -- this fills in a real historical fact
            # (what a position actually cost to trade) for EVERY closed
            # position in the database, stopped/excluded ones included,
            # same reasoning list_pnl_digest_for_deployment's own
            # docstring already uses for a deployment's own numbers. The
            # per-deployment breakdown below exists specifically so a
            # total that looks surprising can be traced to WHICH
            # deployment(s) it actually came from, rather than assumed
            # to be a bug.
            rows = await conn.fetch(
                """
                SELECT p.id, p.deployment_id, d.deployment_name, d.status AS deployment_status,
                       p.symbol, p.side, p.realized_pnl, p.avg_entry_price,
                       lot_agg.total_qty, lot_agg.exit_price
                FROM positions p
                JOIN deployments d ON d.id = p.deployment_id
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
            total_realized = 0.0
            total_turnover = 0.0
            skipped = 0
            updates = []
            by_dep = {}   # deployment_name -> {status, count, realized, charges, turnover}
            # Per-charge-type stats -- sum, and a count of how many
            # positions actually had a NON-ZERO amount for that
            # component (every component but stamp_duty is nonzero for
            # any real trade; stamp_duty itself can round down to Rs 0
            # on a very small trade, so "how many times" is a genuinely
            # different number there).
            by_type = {t: {"sum": 0.0, "count": 0} for t in _CHARGE_TYPES}
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
                breakdown = charge_breakdown(buy_price, sell_price, qty)
                charges = breakdown["total"]
                turnover = (buy_price + sell_price) * qty
                total_charges += charges
                total_realized += float(r["realized_pnl"] or 0)
                total_turnover += turnover
                updates.append((r["id"], charges))

                for t in _CHARGE_TYPES:
                    by_type[t]["sum"] += breakdown[t]
                    if breakdown[t] > 0:
                        by_type[t]["count"] += 1

                dep_key = f"{r['deployment_name']} ({r['deployment_status']})"
                dep = by_dep.setdefault(dep_key, {"count": 0, "realized": 0.0, "charges": 0.0, "turnover": 0.0})
                dep["count"] += 1
                dep["realized"] += float(r["realized_pnl"] or 0)
                dep["charges"] += charges
                dep["turnover"] += turnover

            print(f"  {len(updates)} to update, {skipped} skipped (no closing lot / zero qty found).")
            print()
            print("  By deployment (gross realized P&L vs. estimated charges vs. net, for positions being backfilled):")
            print(f"  {'Deployment':<45} {'Positions':>9} {'Turnover':>14} {'Gross P&L':>14} {'Charges':>12} {'Net P&L':>14}")
            for dep_key, d in sorted(by_dep.items(), key=lambda kv: kv[1]["charges"], reverse=True):
                net = d["realized"] - d["charges"]
                print(f"  {dep_key:<45} {d['count']:>9} {d['turnover']:>14,.2f} {d['realized']:>14,.2f} {d['charges']:>12,.2f} {net:>14,.2f}")
            print()
            print("  By charge type, summed across every position above (verify this against the Tools")
            print("  page's own Brokerage calculator on a single real trade if you want to spot-check it):")
            print(f"  {'Charge type':<55} {'Applied to':>12} {'Total amount':>14} {'Avg/position':>14}")
            for t in _CHARGE_TYPES:
                d = by_type[t]
                avg = d["sum"] / len(updates) if updates else 0
                print(f"  {_CHARGE_LABELS[t]:<55} {d['count']:>7}/{len(updates):<4} {d['sum']:>14,.2f} {avg:>14,.2f}")
            print()
            print(f"  TOTAL across all deployments above (every closed position in the DB, any status):")
            print(f"    Positions backfilled:  {len(updates)}")
            print(f"    Total turnover:        {total_turnover:,.2f}  (buy value + sell value, summed)")
            print(f"    Total gross realized P&L:          {total_realized:>14,.2f}")
            print(f"    Total estimated charges to apply:  {total_charges:>14,.2f}")
            print(f"    ────────────────────────────────────────────────")
            print(f"    CURRENT P&L AFTER BROKERAGE (net):  {total_realized - total_charges:>13,.2f}")
            print()
            print("  If this total looks too high, check the per-deployment table above first --")
            print("  it's summed across EVERY deployment regardless of status/include_in_reports,")
            print("  so an old/stopped/test deployment's history can dominate it even though your")
            print("  dashboard's own 'Total P&L' is scoped to active+paused live deployments only.")

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
