#!/usr/bin/env python3
"""
live_deploy — custom_scripts/day_check.py

The end-of-day check of every deployment (app/repair/daycheck.py): the
rules of a day's end, and the day replayed from its open on Kite's
1-minute candles against what the app did. Results go to day_checks and
show on the Repair page. Writes nothing else: every replay is rolled back.

The app starts this by itself once the 15:45 export succeeds (and from
the Repair page's "Run the day check"); by hand:

    docker exec -it live-deploy python3 custom_scripts/day_check.py --date 2026-10-22
    docker exec -it live-deploy python3 custom_scripts/day_check.py --date 2026-10-22 --deployment <id>

It runs in its own process because a replay points the app's Kite client
and clock at the past (app/repair/market.py: replaying), which the running
app must never see.
"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime

os.environ["TZ"] = "Asia/Kolkata"   # Kite tick times are naive IST, and the fill engine reads them as local time
time.tzset()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config  # noqa: E402
from app.db import queries  # noqa: E402
from app.db.migrate import run_migrations  # noqa: E402
from app.db.pool import close_pool, create_pool  # noqa: E402
from app.repair import daycheck  # noqa: E402
from app.repair.market import IST, KiteGate, MinuteBook, kite_lookup, kite_ltp, kite_minute_source, kite_quote  # noqa: E402


async def _stored(pool, day) -> dict:
    """The app's own 1-minute candles for the day, by token."""
    from datetime import timedelta
    start = datetime.combine(day, datetime.min.time(), IST)
    rows = await pool.fetch(
        "SELECT instrument_token, minute, open, high, low, close, volume FROM candles_1m "
        "WHERE minute >= $1 AND minute < $2 ORDER BY minute", start, start + timedelta(days=1))
    out: dict = {}
    for r in rows:
        out.setdefault(int(r["instrument_token"]), []).append(
            {"date": r["minute"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
             "close": float(r["close"]), "volume": int(r["volume"] or 0)})
    return out


async def main(args) -> int:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(IST).date()
    run_id = args.run
    try:
        await run_migrations(pool)
        if run_id is None:
            run_id = await pool.fetchval("INSERT INTO day_check_runs (day, trigger) VALUES ($1, 'manual') RETURNING id", day)
        session = await queries.get_kite_session(pool)
        if session is None or not session["access_token"]:
            raise RuntimeError("No Kite session in the database — log in to Kite in the app first (the candles come from Kite).")
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=cfg["api_key"])
        kite.set_access_token(session["access_token"])
        gate = KiteGate(kite, log=(lambda line: print(line, flush=True)) if args.verbose else None)
        book = MinuteBook(day, kite_minute_source(gate), await _stored(pool, day), lookup=kite_lookup(gate),
                          ltp=kite_ltp(gate), history=gate.historical_data, quote=kite_quote(gate))
        results = await daycheck.run_day(pool, day, book, gate, run_id=run_id,
                                         only=[args.deployment] if args.deployment else None,
                                         say=lambda s: print(s, flush=True))
        await pool.execute("UPDATE day_check_runs SET status = 'done', finished_at = now() WHERE id = $1", run_id)
        counts: dict = {}
        for r in results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        print(f"Day check {day}: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())), flush=True)
        return 0
    except Exception as e:
        print(f"Day check {day} failed: {type(e).__name__}: {e}", flush=True)
        if run_id is not None:
            await pool.execute("UPDATE day_check_runs SET status = 'failed', error = $2, finished_at = now() WHERE id = $1",
                               run_id, f"{type(e).__name__}: {e}")
        return 1
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="the day to check, YYYY-MM-DD (default: today, IST)")
    ap.add_argument("--deployment", help="only this deployment (its id)")
    ap.add_argument("--run", type=int, help="the day_check_runs row to report into (the app passes it)")
    ap.add_argument("--verbose", action="store_true", help="print every Kite request as it's made")
    sys.exit(asyncio.run(main(ap.parse_args())))
