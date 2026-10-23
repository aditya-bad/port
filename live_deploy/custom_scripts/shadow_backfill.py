#!/usr/bin/env python3
"""
live_deploy — custom_scripts/shadow_backfill.py

Shadow mode (app/shadow.py) checks each fill as it happens. This
estimates the fills from before it existed (or any it missed: the app
was down, the check failed to store), from what was stored at the time,
and marks every one "estimated":

  lot size      only while the contract is still listed (today's
                instrument list); an expired one says so
  freeze limit  today's limits (config shadow.freeze_qty, or the last
                NSE file stored)
  order book    the book's average for the whole quantity, recorded with
                each fill since slippage capture began
                (position_lots.book_price), against the limit live would
                have sent; no book recorded -> "couldn't check"
  price band, margin, legs placed one by one, the 60 s after
                not stored for past fills: left out, never guessed

Estimated checks show on every page with "(est.)" and count in the
"ready for live?" stats, but not towards its record (sessions, cycles,
recent refusals), which only counts checks made as fills happened.

Safe to re-run: a fill that already has a check is skipped. Pure DB
read/write; no Kite session. Run it wherever DATABASE_URL (env var or
config.json) is reachable — inside the container if the database is on
its network:

    python3 custom_scripts/shadow_backfill.py --dry-run
    python3 custom_scripts/shadow_backfill.py
    python3 custom_scripts/shadow_backfill.py --deployment <id>

The same runs from the app: Geek mode → Live-ready → "Estimate …".
"""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import shadow   # noqa: E402
from app.config import load_config   # noqa: E402
from app.db.pool import close_pool, create_pool   # noqa: E402


async def main(args) -> None:
    config = load_config()
    pool = await create_pool(config["database_url"])
    try:
        cfg = config.get("shadow") or {}
        freeze = shadow.FreezeLimits(pool, cfg.get("freeze_qty"))
        await freeze.load()
        out = await shadow.backfill(pool, freeze, deployment_id=UUID(args.deployment) if args.deployment else None,
                                    buffer_pct=float(cfg.get("limit_buffer_pct", shadow.BUFFER_PCT)), dry_run=args.dry_run)
    finally:
        await close_pool(pool)
    print(f"{out['fills']} fill(s) without a shadow check")
    for v, n in sorted(out["verdicts"].items(), key=lambda x: -x[1]):
        print(f"  {v:<8} {n}")
    print("dry run: nothing written" if args.dry_run else f"{out['written']} estimated check(s) written")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deployment", help="only this deployment's fills (its id)")
    ap.add_argument("--dry-run", action="store_true", help="count and estimate, write nothing")
    asyncio.run(main(ap.parse_args()))
