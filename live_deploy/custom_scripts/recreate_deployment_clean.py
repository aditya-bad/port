#!/usr/bin/env python3
"""
live_deploy — custom_scripts/recreate_deployment_clean.py

"Delete and re-register, same config" for one or more deployments —
built for exactly one situation: a deployment that placed real trades
off bad data (this repo's own stale-subscribe-snapshot bug, see
app/deployments/runner.py's _is_stale_tick) and the owner wants it
gone entirely, not just flattened. Flatten still leaves the bogus
closed position rows counted in win_rate/total_realized_pnl forever
(queries.build_report and friends count every closed position, no
reason-based filtering) — this instead deletes the deployment outright
(cascades away every position/event/snapshot row under it, per
migrations/0001_init.sql's ON DELETE CASCADE) and creates a BRAND NEW
deployment (new id, entered_ever=False, cycle_id=0, zero trade history)
with the EXACT SAME deployment_name/strategy_name/mode/initial_capital/
config/notes the old one had — captured from the DB itself right before
deleting, never retyped by hand, so "same config" can't drift from
whatever was actually running.

Sequence per deployment, each step only run if the previous succeeded:
    1. Read the row (config snapshot for later reuse + audit printout).
    2. POST /{id}/stop?force_close=true — force_close=true so any open
       position (the whole reason you're here) gets closed first; the
       delete endpoint refuses anything but an already-'stopped'
       deployment (see routers/deployments.py's own delete route).
       No-op if it's already stopped.
    3. POST /{id}/delete.
    4. POST / (create) with the captured deployment_name/strategy_name/
       mode/initial_capital/config/notes — a genuinely new deployment,
       which (create_deployment's own default) starts life 'active'.

Goes through the running app's own API for every step (X-API-Key auth,
same header fix_strangle_instrument_tokens.py uses) rather than the
database directly — stop/delete both have real in-process side effects
(tearing down a live DeploymentRunner, dispatcher subscriptions) that a
raw SQL DELETE would skip entirely, exactly the same reasoning that
script's own docstring gives for why pause/resume has to go through the
API too.

USAGE — run INSIDE the app container (needs this app's own installed
dependencies, and the API it's calling is on localhost from there):
    docker exec live-deploy python3 custom_scripts/recreate_deployment_clean.py "DTT Bankex Strangle" "DTT Nifty Strangle"
    docker exec live-deploy python3 custom_scripts/recreate_deployment_clean.py "DTT Bankex Strangle" "DTT Nifty Strangle" --dry-run
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from app.config import load_config
from app.db import queries
from app.db.pool import close_pool, create_pool


async def main(names: list[str], dry_run: bool) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        rows = await queries.list_deployments(pool)
        by_name = {r["deployment_name"]: r for r in rows}

        targets = []
        for name in names:
            row = by_name.get(name)
            if row is None:
                print(f"SKIP  {name!r}: no such deployment — check the exact name (case-sensitive)")
                continue
            targets.append(row)

        if not targets:
            print("Nothing to do — none of the given names matched an existing deployment.")
            return

        print(f"Found {len(targets)} deployment(s) to recreate:\n")
        for row in targets:
            print(f"  {row['deployment_name']!r}")
            print(f"    id: {row['id']}")
            print(f"    strategy_name: {row['strategy_name']}, mode: {row['mode']}, "
                  f"status: {row['status']}, initial_capital: {row['initial_capital']}")
            print(f"    config: {dict(row['config'])}")
            print(f"    notes: {row['notes']!r}")
            print()

        if dry_run:
            print(f"DRY RUN — would have deleted and recreated {len(targets)} deployment(s) "
                  f"with the config shown above. Re-run without --dry-run to apply.")
            return

        api_key = cfg["app_auth_secret"]
        headers = {"X-API-Key": api_key}
        base = "http://localhost:8000"

        for row in targets:
            name = row["deployment_name"]
            dep_id = row["id"]
            payload = {
                "deployment_name": name,
                "strategy_name": row["strategy_name"],
                "mode": row["mode"],
                "initial_capital": float(row["initial_capital"]),
                "config": dict(row["config"]),
                "notes": row["notes"],
            }
            try:
                r = requests.post(
                    f"{base}/deployments/{dep_id}/stop", headers=headers,
                    params={"force_close": "true"}, timeout=15,
                )
                r.raise_for_status()
                print(f"  {name}: stopped (any open position force-closed)")

                r = requests.post(f"{base}/deployments/{dep_id}/delete", headers=headers, timeout=15)
                r.raise_for_status()
                print(f"  {name}: deleted (old id {dep_id}, along with all its trade history)")

                r = requests.post(f"{base}/deployments", headers=headers, json=payload, timeout=15)
                r.raise_for_status()
                new_row = r.json()
                print(f"  {name}: recreated — new id {new_row['id']}, status {new_row['status']!r}, "
                      f"same config as before, zero trade history")
            except requests.RequestException as e:
                body = getattr(e.response, "text", "")
                print(f"  {name}: FAILED ({e}) {body} — stopped/deleted/recreated as far as it got; "
                      f"check the deployment's current state via the UI before retrying")
            print()
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("deployment_names", nargs="+", help="Exact deployment_name(s) to delete and recreate")
    parser.add_argument("--dry-run", action="store_true", help="Preview (prints captured config) without writing anything.")
    args = parser.parse_args()
    asyncio.run(main(args.deployment_names, args.dry_run))
