#!/usr/bin/env python3
"""
live_deploy — custom_scripts/backfill_missed_force_exit.py

EMERGENCY SCRIPT — Kite WebSocket outage (invalid/expired token, repeated
403s on the ticker upgrade) meant no live ticks reached the intraday
straddle strategies around their own force_exit_time yesterday, so
force_exit_time never fired (it only runs INSIDE on_tick) and their legs
are still open into this morning, before market open.

For every intraday_dtt_simple / intraday_dtt_advanced / intraday_dtt_adjusted
deployment with an OPEN position: finds the 5-minute candle starting at
15:00 IST on the day that position was opened (the same calendar day it
should have force-exited on), uses that candle's OPEN price as the
backfilled exit price, and closes it via the exact same record_fill path
a live force_exit would have used — so cash/realized_pnl/position_lots
all come out identical to what would have happened if the tick had
actually arrived.

Needs a currently-valid Kite session (for historical_data) — same
requirement as fix_strangle_instrument_tokens.py.

FULLY HANDS-OFF: after fixing the DB, Pause+Resume via the app's own API
for every deployment touched, same reasoning as every other script here —
a running deployment doesn't re-read closed positions live on its own.

USAGE (run INSIDE the app container):
    docker exec live-deploy python3 custom_scripts/backfill_missed_force_exit.py
    docker exec live-deploy python3 custom_scripts/backfill_missed_force_exit.py --dry-run
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiteconnect import KiteConnect

from app.config import load_config
from app.db import queries
from app.db.pool import close_pool, create_pool

IST = ZoneInfo("Asia/Kolkata")
STRADDLE_STRATEGIES = ("intraday_dtt_simple", "intraday_dtt_advanced", "intraday_dtt_adjusted")


async def price_at_3pm(kite, instrument_token: int, day) -> float:
    start = datetime(day.year, day.month, day.day, 9, 0, tzinfo=IST)
    end = datetime(day.year, day.month, day.day, 15, 30, tzinfo=IST)
    candles = await asyncio.to_thread(kite.historical_data, instrument_token, start, end, "5minute")
    if not candles:
        raise ValueError(f"No 5-minute candles returned for token {instrument_token} on {day}")
    target = datetime(day.year, day.month, day.day, 15, 0, tzinfo=IST)
    exact = [c for c in candles if c["date"].astimezone(IST) == target]
    if exact:
        return float(exact[0]["open"])
    # Fall back to the latest candle AT OR BEFORE 15:00 if the exact
    # 15:00 candle is missing (a gap in the feed that day) — never a
    # candle AFTER 15:00, which would silently use a later, wrong price.
    before = [c for c in candles if c["date"].astimezone(IST) <= target]
    if not before:
        raise ValueError(f"No candle at/before 15:00 IST for token {instrument_token} on {day}")
    return float(before[-1]["close"])


async def main(dry_run: bool) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        session = await queries.get_kite_session(pool)
        if session is None or not session["access_token"]:
            print("ERROR: no Kite session in the database — log in first.", file=sys.stderr)
            sys.exit(1)
        kite = KiteConnect(api_key=cfg["api_key"])
        kite.set_access_token(session["access_token"])

        deployments = await pool.fetch(
            "SELECT * FROM deployments WHERE strategy_name = ANY($1::text[]) ORDER BY deployment_name",
            list(STRADDLE_STRATEGIES),
        )

        plan = []   # [(dep, [(position_row, exit_price), ...])]
        for dep in deployments:
            open_positions = await pool.fetch(
                "SELECT * FROM positions WHERE deployment_id = $1 AND status = 'open'", dep["id"],
            )
            if not open_positions:
                continue
            fixes = []
            for pos in open_positions:
                day = pos["opened_at"].astimezone(IST).date()
                try:
                    price = await price_at_3pm(kite, int(pos["instrument_token"]), day)
                except Exception as e:
                    print(f"SKIP  {dep['deployment_name']} / {pos['symbol']}: {e}")
                    continue
                fixes.append((pos, price, day))
            if fixes:
                plan.append((dep, fixes))

        if not plan:
            print("Nothing to do — no open positions on any straddle-intraday deployment.")
            return

        print("=== Plan ===\n")
        for dep, fixes in plan:
            print(f"{dep['deployment_name']!r} ({dep['strategy_name']}, status={dep['status']})")
            for pos, price, day in fixes:
                action = "sell" if pos["side"] == "long" else "buy"
                print(f"    {pos['symbol']}: {action} {pos['qty']} @ {price:.2f}  "
                      f"(3pm {day} candle, opened {pos['avg_entry_price']})")
            print()

        if dry_run:
            print("DRY RUN — re-run without --dry-run to apply.")
            return

        touched = []
        for dep, fixes in plan:
            exec_ts = None
            for pos, price, day in fixes:
                action = "sell" if pos["side"] == "long" else "buy"
                naive_3pm = datetime(day.year, day.month, day.day, 15, 0, 0)  # naive local, matches runner._fill's own convention
                exec_ts = naive_3pm.astimezone(timezone.utc)
                await queries.record_fill(
                    pool, dep["id"], pos["symbol"], int(pos["instrument_token"]), action,
                    float(pos["qty"]), price, exec_ts, reason="force_exit_time_backfill",
                    metadata={"backfill": True, "note": "Kite WebSocket outage — force_exit_time never fired live; backfilled from the 3pm 5-min candle."},
                )
                print(f"  {dep['deployment_name']} / {pos['symbol']}: closed @ {price:.2f}")
            touched.append(dep)

        print("\nApplying live (pause+resume each touched deployment)...")
        import requests
        headers = {"X-API-Key": cfg["app_auth_secret"]}
        base = "http://localhost:8000"
        for dep in touched:
            try:
                if dep["status"] == "active":
                    requests.post(f"{base}/deployments/{dep['id']}/pause", headers=headers, timeout=15).raise_for_status()
                requests.post(f"{base}/deployments/{dep['id']}/resume", headers=headers, timeout=15).raise_for_status()
                print(f"  {dep['deployment_name']}: paused+resumed")
            except Exception as e:
                print(f"  {dep['deployment_name']}: pause/resume failed ({e}) — DB fix is saved, resume manually")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
