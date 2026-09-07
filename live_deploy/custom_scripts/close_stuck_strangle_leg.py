#!/usr/bin/env python3
"""
live_deploy — custom_scripts/close_stuck_strangle_leg.py

One-off manual close for a leg strangle_monthly_v2's OLD _eod_check bug
left stranded (see that strategy's own module docstring, Section 6's
"BUG FIX" note): before the fix, a side's very first breach of
eod_gap_floor GREW instead of REPLACED — permanently shielding that
side's original leg from ever being rolled by this check. Any
deployment whose EOD check fired under the old code may be carrying one
of these now-stale legs. The code fix (already applied) prevents this
going forward; this script is ONLY for cleaning up legs that already
exist from before the fix, one at a time, named explicitly by
deployment + symbol so there's no ambiguity about what gets closed.

WHAT THIS DOES NOT DO: it does not try to guess which legs are "stuck".
You identify the exact deployment + symbol yourself first (Detail page
-> Positions tab: an "original"-role leg on a side that also has a
newer, EOD-added leg is the pattern to look for), then name it here.
This is deliberately narrow and manual, not a bulk sweep — telling a
genuinely-fine single leg apart from a stuck one needs a human judgment
call this script has no safe way to make.

SAFE BY DESIGN, NOT A CASCADE: closing one leg in isolation (rather
than force-closing/stopping the WHOLE deployment) works cleanly because
of this app's own resume-safety architecture (see StrategyBase and
strangle_monthly_v2._resume_from_db) — a running deployment's in-memory
self.legs is never the source of truth between restarts; on every
on_start (including a Pause+Resume cycle) it's rebuilt FRESH from
whatever `positions` actually shows as open in the DB. So this script
is exactly: (1) Pause — tears the runner down, no more ticks reach the
old in-memory state; (2) close the ONE named leg directly in the DB via
the same queries.force_close_position() helper the app's own
manager.stop()/flatten() already use; (3) Resume — on_start rebuilds
self.legs from the DB, which now correctly has only what's genuinely
still open. No manual surgery on any in-memory Python object, no
special-casing needed anywhere else in the strategy.

Needs a currently-valid Kite session (reads kite_sessions, same as
fix_strangle_instrument_tokens.py) to price the close at a real live
LTP — pass --price to override with a specific number instead (e.g. if
Kite has no quote for an already-illiquid/near-expiry contract).

USAGE — run INSIDE the app container:
    docker exec live-deploy python3 custom_scripts/close_stuck_strangle_leg.py \
        --deployment "BANKEX Strangle" --symbol BANKEX26SEP61100PE --dry-run

    docker exec live-deploy python3 custom_scripts/close_stuck_strangle_leg.py \
        --deployment "BANKEX Strangle" --symbol BANKEX26SEP61100PE

    # Override the close price instead of fetching a live quote:
    docker exec live-deploy python3 custom_scripts/close_stuck_strangle_leg.py \
        --deployment "BANKEX Strangle" --symbol BANKEX26SEP61100PE --price 41.2
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config
from app.db import queries
from app.db.pool import close_pool, create_pool


async def main(deployment_name: str, symbol: str, price_override: Optional[float], dry_run: bool) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        dep = await queries.get_deployment_by_name(pool, deployment_name)
        if dep is None:
            print(f"ERROR: no deployment named {deployment_name!r}", file=sys.stderr)
            sys.exit(1)
        if dep["strategy_name"] != "strangle_monthly_v2":
            print(
                f"WARNING: {deployment_name!r} is a {dep['strategy_name']!r} deployment, "
                f"not strangle_monthly_v2 — continuing anyway (force_close_position works "
                f"identically for any strategy), but double-check this is really the one you mean."
            )

        open_positions = await queries.list_open_positions(pool, dep["id"])
        matches = [p for p in open_positions if p["symbol"] == symbol]
        if not matches:
            open_symbols = [p["symbol"] for p in open_positions]
            print(
                f"ERROR: no OPEN position named {symbol!r} on {deployment_name!r}. "
                f"Currently open: {open_symbols or '(none)'}", file=sys.stderr,
            )
            sys.exit(1)
        if len(matches) > 1:
            # Shouldn't be reachable — one_open_position_per_instrument is a
            # DB-level unique index (migrations/0001_init.sql) — but never
            # silently pick one if this somehow isn't true.
            print(
                f"ERROR: found {len(matches)} open positions named {symbol!r} on "
                f"{deployment_name!r} — that should be impossible. Investigate "
                f"before proceeding; refusing to guess which one you mean.",
                file=sys.stderr,
            )
            sys.exit(1)
        pos = matches[0]

        print(f"Deployment: {deployment_name} (status={dep['status']})")
        print(
            f"Position:   {pos['symbol']}  side={pos['side']}  qty={pos['qty']}  "
            f"avg_entry_price={pos['avg_entry_price']}"
        )

        if price_override is not None:
            price = price_override
            print(f"Using OVERRIDE close price: {price}")
        else:
            session = await queries.get_kite_session(pool)
            if session is None or not session["access_token"]:
                print(
                    "ERROR: no Kite session in the database, and no --price override given — "
                    "complete the daily login via the UI first, or pass --price.", file=sys.stderr,
                )
                sys.exit(1)
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=cfg["api_key"])
            kite.set_access_token(session["access_token"])
            quote = await asyncio.to_thread(kite.quote, [int(pos["instrument_token"])])
            entry = quote.get(str(pos["instrument_token"]))
            if entry is None or entry.get("last_price") is None:
                print(
                    f"ERROR: Kite returned no quote for instrument_token={pos['instrument_token']} "
                    f"({symbol}) — pass --price to close at a specific price instead.", file=sys.stderr,
                )
                sys.exit(1)
            price = float(entry["last_price"])
            print(f"Live LTP: {price}")

        if dry_run:
            print(f"DRY RUN — would close {symbol} at {price}. Re-run without --dry-run to apply.")
            return

        was_active = dep["status"] == "active"
        api_key = cfg["app_auth_secret"]
        headers = {"X-API-Key": api_key}
        base = "http://localhost:8000"
        import requests

        if was_active:
            r = requests.post(f"{base}/deployments/{dep['id']}/pause", headers=headers, timeout=15)
            r.raise_for_status()
            print("Paused deployment.")
        elif dep["status"] != "paused":
            print(
                f"ERROR: deployment status is {dep['status']!r} — refusing to touch a "
                f"{dep['status']} deployment's positions this way. Investigate manually.",
                file=sys.stderr,
            )
            sys.exit(1)

        result = await queries.force_close_position(
            pool, dep["id"], pos, price, datetime.now(timezone.utc),
            reason="manual_close_stuck_eod_leg",
        )
        print(f"Closed {symbol}: realized_pnl={result.get('realized_pnl')}")

        if was_active:
            r = requests.post(f"{base}/deployments/{dep['id']}/resume", headers=headers, timeout=15)
            r.raise_for_status()
            print("Resumed deployment — on_start rebuilt its legs fresh from the DB.")
        else:
            print(
                "Deployment left paused (it was already paused before this script ran) — "
                "resume it yourself via the UI when ready."
            )
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deployment", required=True, help="Exact deployment_name to act on.")
    parser.add_argument("--symbol", required=True, help="Exact tradingsymbol of the OPEN position to close.")
    parser.add_argument("--price", type=float, default=None, help="Close at this price instead of fetching a live Kite quote.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing anything.")
    args = parser.parse_args()
    asyncio.run(main(args.deployment, args.symbol, args.price, args.dry_run))
