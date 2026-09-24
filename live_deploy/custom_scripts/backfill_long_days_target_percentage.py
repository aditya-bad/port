#!/usr/bin/env python3
"""
live_deploy — custom_scripts/backfill_long_days_target_percentage.py

One-off backfill for the new `long_days_target_percentage` config key
(see strangle_monthly_v2.py's own module docstring, "LONG-DATED ENTRY
OVERRIDE"): a fresh entry (initial or checkpoint re-entry) resolved
more than 29 days from its own expiry, outside the current calendar
month, now uses this percentage instead of the lower
strike_selection_capital_pct — the low default was pricing strikes so
far OTM, that early, that live liquidity was practically zero.

A deployment created before this option existed has no
long_days_target_percentage key in its stored config at all. The
strategy's own on_start falls back to the same 0.05 code-level default
either way, so nothing is BROKEN by leaving it unset — but it stays
invisible in the Configuration tab / config editor, and (see below) a
currently-running deployment won't pick up the new default until its
in-process runner is rebuilt. This finds every strangle_monthly_v2
deployment missing the key, sets it to 0.05 (the same value new
deployments get from default_config), and leaves every other config
key untouched.

Never touches an already-open position's legs — this only affects
which strike gets selected at each affected deployment's NEXT fresh
entry, so there is nothing to flatten and no reason to.

FULLY HANDS-OFF: after fixing the DB, this ALSO calls the running
app's own API (http://localhost:8000, same container) to Pause then
Resume every deployment it just fixed — a raw DB update alone isn't
enough, since a running deployment doesn't re-read its own config live
(on_start reads config.get("long_days_target_percentage", 0.05) once
into self.long_days_target_percentage; an already-running instance
keeps whatever it read at its last start until pause/resume rebuilds
it). If the API call fails for any reason, the DB fix is still saved
either way — it just tells you to pause/resume that one manually
instead of silently leaving it half-done.

USAGE — run INSIDE the app container (needs both DB access on its
Docker network and this app's own installed dependencies):
    docker exec live-deploy python3 custom_scripts/backfill_long_days_target_percentage.py
    docker exec live-deploy python3 custom_scripts/backfill_long_days_target_percentage.py --dry-run
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config
from app.db.pool import close_pool, create_pool

DEFAULT_VALUE = 0.05


async def main(dry_run: bool) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        rows = await pool.fetch(
            "SELECT id, deployment_name, config, status FROM deployments "
            "WHERE strategy_name = 'strangle_monthly_v2' ORDER BY deployment_name"
        )
        if not rows:
            print("No strangle_monthly_v2 deployments found — nothing to do.")
            return

        fixed = []   # [(id, name, status), ...]
        for row in rows:
            config = row["config"] or {}
            name = row["deployment_name"]

            if "long_days_target_percentage" in config:
                print(f"SKIP  {name}: long_days_target_percentage already set to "
                      f"{config['long_days_target_percentage']} — untouched.")
                continue

            print(f"FIX   {name}: long_days_target_percentage (missing) -> {DEFAULT_VALUE}")
            if not dry_run:
                await pool.execute(
                    "UPDATE deployments SET config = config || $2::jsonb, updated_at = now() WHERE id = $1",
                    row["id"], {"long_days_target_percentage": DEFAULT_VALUE},
                )
            fixed.append((row["id"], name, row["status"]))

        print()
        if dry_run:
            print(f"DRY RUN — would have fixed {len(fixed)} deployment(s). Re-run without --dry-run to apply.")
        elif not fixed:
            print("Nothing to fix — every strangle_monthly_v2 deployment already had long_days_target_percentage set.")
        else:
            print(f"Fixed {len(fixed)} deployment(s) in the database. Now applying it live...")
            print()
            # A running deployment doesn't re-read its own config live --
            # each FIXED one still needs a genuine Pause+Resume cycle to
            # rebuild its in-memory strategy instance (built from the OLD
            # config, key absent) from what's actually in the DB now.
            # Can't do this with a raw DB update alone, so this calls the
            # app's own API instead, exactly like clicking Pause then
            # Resume in the UI would.
            import requests
            api_key = cfg["app_auth_secret"]
            headers = {"X-API-Key": api_key}
            base = "http://localhost:8000"
            for dep_id, name, status in fixed:
                try:
                    if status == "active":
                        r = requests.post(f"{base}/deployments/{dep_id}/pause", headers=headers, timeout=15)
                        r.raise_for_status()
                        print(f"  {name}: paused")
                    elif status == "paused":
                        print(f"  {name}: already paused")
                    else:
                        print(f"  {name}: status is {status!r} — can't resume a {status} deployment; "
                              f"the config fix is saved, resume it manually once it's active again.")
                        continue

                    r = requests.post(f"{base}/deployments/{dep_id}/resume", headers=headers, timeout=15)
                    r.raise_for_status()
                    print(f"  {name}: resumed — now running with long_days_target_percentage set.")
                except requests.RequestException as e:
                    print(f"  {name}: pause/resume via the API failed ({e}) — the DB config fix is still saved; "
                          f"pause then resume it manually via the UI.")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing anything.")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
