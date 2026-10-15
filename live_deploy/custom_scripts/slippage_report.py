#!/usr/bin/env python3
"""
live_deploy — custom_scripts/slippage_report.py

READ-ONLY audit of the estimated slippage recorded alongside every fill
(migration 0017, app/slippage.py), for checking the logic against real
trades before relying on the "After charges + slippage" P&L view.

Fills are still recorded at LTP. For each fill this shows that LTP, the
price the same fill would have got against the live order book at that
moment (position_lots.book_price — a sell walks the bids, a buy walks
the asks), the per-unit difference, and the rupee slippage (positive =
the book fill is worse). A fill with no book price had no order book
available (empty book, or a fill from before this feature) and counts as
zero slippage — the "coverage" column shows how many fills were priced.

Things to sanity-check against Kite's market depth / your own sense of
the market:
  - per-unit differences should be around half the bid-ask spread for
    liquid strikes (a few paise to a rupee or two), larger for thin ones;
  - a NEGATIVE slippage means the last trade printed outside the book at
    that instant — occasional is normal, frequent is worth a closer look;
  - low coverage means many fills had no book (check tick_mode is
    "full" in config.json — ltp/quote mode ticks carry no depth).

USAGE (inside the app container, where the database resolves):
    docker exec live-deploy python3 custom_scripts/slippage_report.py
    docker exec live-deploy python3 custom_scripts/slippage_report.py --days 30 --fills 20
    docker exec live-deploy python3 custom_scripts/slippage_report.py --deployment "Straddle Nifty Advanced"
"""
import argparse
import asyncio
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config
from app.db.pool import close_pool, create_pool
from app.slippage import fill_slippage

_IST = ZoneInfo("Asia/Kolkata")


async def main(days: int, fills_per_dep: int, deployment: str | None) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        rows = await pool.fetch(
            """
            SELECT d.deployment_name, pl.executed_at, p.symbol, pl.action,
                   pl.qty::float8 AS qty, pl.price::float8 AS price,
                   pl.book_price::float8 AS book_price
            FROM position_lots pl
            JOIN positions p ON p.id = pl.position_id
            JOIN deployments d ON d.id = pl.deployment_id
            WHERE pl.executed_at >= now() - make_interval(days => $1)
              AND ($2::text IS NULL OR d.deployment_name = $2)
            ORDER BY pl.executed_at
            """,
            days, deployment,
        )
    finally:
        await close_pool(pool)

    print(f"{len(rows)} fill(s) in the last {days} day(s)"
          + (f" for '{deployment}'" if deployment else "") + ".")
    if not rows:
        return

    by_dep: dict[str, list] = {}
    for r in rows:
        by_dep.setdefault(r["deployment_name"], []).append(r)

    grand_slip = grand_value = 0.0
    grand_priced = 0
    print()
    print(f"  {'Deployment':<42} {'Fills':>6} {'Priced':>7} {'Slippage Rs':>12} {'Per priced fill':>16} {'% of fill value':>16}")
    for name, dep_rows in sorted(by_dep.items()):
        priced = [r for r in dep_rows if r["book_price"] is not None]
        slip = sum(fill_slippage(r["action"], r["price"], r["book_price"], r["qty"]) for r in priced)
        value = sum(r["price"] * r["qty"] for r in priced)
        grand_slip += slip
        grand_value += value
        grand_priced += len(priced)
        per_fill = slip / len(priced) if priced else 0.0
        pct = (slip / value * 100) if value else 0.0
        print(f"  {name[:42]:<42} {len(dep_rows):>6} {len(priced):>4}/{len(dep_rows):<3}"
              f"{slip:>12,.2f} {per_fill:>16,.2f} {pct:>15.3f}%")
    pct = (grand_slip / grand_value * 100) if grand_value else 0.0
    print(f"  {'TOTAL':<42} {len(rows):>6} {grand_priced:>4}/{len(rows):<3}{grand_slip:>12,.2f}"
          f" {'':>16} {pct:>15.3f}%")

    if fills_per_dep <= 0:
        return
    print()
    print(f"  Most recent {fills_per_dep} fill(s) per deployment (newest first):")
    for name, dep_rows in sorted(by_dep.items()):
        print()
        print(f"  {name}:")
        print(f"    {'When (IST)':<17} {'Symbol':<24} {'Side':<4} {'Qty':>6} {'LTP fill':>10} {'Book fill':>10} {'Diff/unit':>10} {'Slippage Rs':>12}")
        for r in reversed(dep_rows[-fills_per_dep:]):
            when = r["executed_at"].astimezone(_IST).strftime("%Y-%m-%d %H:%M")
            if r["book_price"] is None:
                book, diff, slip = "no book", "", ""
            else:
                s = fill_slippage(r["action"], r["price"], r["book_price"], r["qty"])
                book, diff, slip = f"{r['book_price']:,.2f}", f"{s / r['qty']:+.2f}", f"{s:,.2f}"
            print(f"    {when:<17} {r['symbol'][:24]:<24} {r['action']:<4} {r['qty']:>6,.0f} "
                  f"{r['price']:>10,.2f} {book:>10} {diff:>10} {slip:>12}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="How many days back to report (default 7).")
    parser.add_argument("--fills", type=int, default=10, help="Recent fills listed per deployment (0 = totals only).")
    parser.add_argument("--deployment", help="Only this deployment_name.")
    args = parser.parse_args()
    asyncio.run(main(args.days, args.fills, args.deployment))
