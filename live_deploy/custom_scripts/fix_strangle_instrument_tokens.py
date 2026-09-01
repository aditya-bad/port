#!/usr/bin/env python3
"""
live_deploy — custom_scripts/fix_strangle_instrument_tokens.py

One-off fix for a real production bug (see strangle_monthly_v2.py's
own on_start): a deployment of that strategy with an empty/missing
config.instrument_tokens receives ZERO ticks ever — DeploymentRunner
filters every incoming tick down to a deployment's own
instrument_tokens BEFORE ever calling the strategy at all — so it sits
"active" with 0 positions forever, completely silently, no error, no
skipped-entry log line, nothing. Confirmed against 4 real deployments
(Nifty/BankNifty/Sensex/Bankex Strangle) before this script and that
strategy's own new validation existed.

Finds every strangle_monthly_v2 deployment whose config.instrument_tokens
is missing or empty, resolves the correct SPOT instrument_token for
whatever underlying that deployment's own config.instrument says
(NIFTY/BANKNIFTY/SENSEX/BANKEX) straight from Kite's live instruments
API — not guessed, not hardcoded (BANKEX in particular isn't in this
repo's tokens.json at all) — using the EXACT SAME exchange/tradingsymbol
mapping app/options/resolver.py's own INDEX_SPOT_SYMBOL already uses,
so this can never disagree with what the running app itself would
resolve. Updates each deployment's config directly in the database
(a targeted JSONB merge — every other config key is left untouched).

Needs a currently-valid Kite session — the exact same one the running
app itself uses (read from the kite_sessions table). Complete the
daily login via the UI first if you haven't today; this script does
NOT do its own login flow.

FULLY HANDS-OFF: after fixing the DB, this ALSO calls the running
app's own API (http://localhost:8000, same container) to Pause then
Resume every deployment it just fixed — a raw DB update alone isn't
enough, since a running deployment doesn't re-read its own config
live (pause/resume tears down and rebuilds the actual in-process
DeploymentRunner, dispatcher subscriptions included, not just a status
flag), so this does exactly what clicking Pause then Resume in the UI
would, automatically, right after the fix. If the API call fails for
any reason, the DB fix is still saved either way — it just tells you
to pause/resume that one manually instead of silently leaving it
half-done.

USAGE — run INSIDE the app container (needs both DB access on its
Docker network and this app's own installed dependencies):
    docker exec live-deploy python3 custom_scripts/fix_strangle_instrument_tokens.py
    docker exec live-deploy python3 custom_scripts/fix_strangle_instrument_tokens.py --dry-run
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiteconnect import KiteConnect

from app.config import load_config
from app.db import queries
from app.db.pool import close_pool, create_pool

# Mirrors app/options/resolver.py's own INDEX_SPOT_SYMBOL exactly.
INDEX_SPOT_SYMBOL = {
    "NIFTY": ("NSE", "NIFTY 50"),
    "BANKNIFTY": ("NSE", "NIFTY BANK"),
    "SENSEX": ("BSE", "SENSEX"),
    "BANKEX": ("BSE", "BANKEX"),
}


async def main(dry_run: bool) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        session = await queries.get_kite_session(pool)
        if session is None or not session["access_token"]:
            print(
                "ERROR: no Kite session in the database yet — complete the "
                "daily login via the UI first, then re-run this.",
                file=sys.stderr,
            )
            sys.exit(1)

        kite = KiteConnect(api_key=cfg["api_key"])
        kite.set_access_token(session["access_token"])

        rows = await pool.fetch(
            "SELECT id, deployment_name, config, status FROM deployments "
            "WHERE strategy_name = 'strangle_monthly_v2' ORDER BY deployment_name"
        )
        if not rows:
            print("No strangle_monthly_v2 deployments found — nothing to do.")
            return

        # Fetch each exchange's full instrument master ONCE, lazily — only
        # if some deployment actually needs it (NSE for NIFTY/BANKNIFTY,
        # BSE for SENSEX/BANKEX), not both unconditionally.
        instrument_cache: dict[str, list[dict]] = {}

        async def spot_token_for(instrument: str) -> int:
            exchange, symbol = INDEX_SPOT_SYMBOL[instrument]
            if exchange not in instrument_cache:
                print(f"-- fetching {exchange} instrument master from Kite...")
                instrument_cache[exchange] = await asyncio.to_thread(kite.instruments, exchange)
            for row in instrument_cache[exchange]:
                if row["tradingsymbol"] == symbol:
                    return row["instrument_token"]
            raise ValueError(
                f"No {exchange} row found for tradingsymbol {symbol!r} (instrument={instrument})"
            )

        fixed = []   # [(id, name, status), ...]
        for row in rows:
            config = row["config"] or {}
            tokens = config.get("instrument_tokens") or []
            name = row["deployment_name"]
            instrument = str(config.get("instrument", "NIFTY")).strip().upper()

            if tokens:
                print(f"SKIP  {name}: instrument_tokens already set to {tokens} — untouched.")
                continue

            if instrument not in INDEX_SPOT_SYMBOL:
                print(f"SKIP  {name}: unrecognized instrument {instrument!r} — can't resolve a token, fix manually.")
                continue

            try:
                token = await spot_token_for(instrument)
            except ValueError as e:
                print(f"SKIP  {name}: {e}")
                continue

            print(f"FIX   {name}: instrument={instrument} — instrument_tokens {tokens} -> [{token}]")
            if not dry_run:
                await pool.execute(
                    "UPDATE deployments SET config = config || $2::jsonb, updated_at = now() WHERE id = $1",
                    row["id"], {"instrument_tokens": [token]},
                )
            fixed.append((row["id"], name, row["status"]))

        print()
        if dry_run:
            print(f"DRY RUN — would have fixed {len(fixed)} deployment(s). Re-run without --dry-run to apply.")
        elif not fixed:
            print("Nothing to fix — every strangle_monthly_v2 deployment already had instrument_tokens set.")
        else:
            print(f"Fixed {len(fixed)} deployment(s) in the database. Now applying it live...")
            print()
            # A running deployment doesn't re-read its own config live --
            # each FIXED one still needs a genuine Pause+Resume cycle to
            # tear down its in-memory runner (built from the OLD,
            # instrument_tokens=[] config) and rebuild it from what's
            # actually in the DB now. Can't do this with a raw DB update
            # (pause/resume is real in-process state -- dispatcher
            # subscriptions, the live DeploymentRunner object -- not just
            # a status flag), so this calls the app's own API instead,
            # exactly like clicking Pause then Resume in the UI would.
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
                    print(f"  {name}: resumed — now running with instrument_tokens set, should trade normally.")
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
