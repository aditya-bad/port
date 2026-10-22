#!/usr/bin/env python3
"""
live_deploy — custom_scripts/repair_worker.py

Answers the Repair page: plans a flagged day's repair with the paths you
chose (a dry run, rolled back) and applies it when you push it (committed
only as planned) — app/repair/dayrepair.py. Jobs come from repair_jobs;
each day's Kite candles are fetched once and kept while it runs, so each
path you choose is quick.

The app starts it when a job is waiting (app/repair/launcher.py) and it
stops after --idle seconds with nothing to do. By hand:

    docker exec -it live-deploy python3 custom_scripts/repair_worker.py --idle 600

It runs on its own because a replay points the app's Kite client and
clock at the past (app/repair/market.py: replaying).
"""
import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta

os.environ["TZ"] = "Asia/Kolkata"   # Kite tick times are naive IST, and the fill engine reads them as local time
time.tzset()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config  # noqa: E402
from app.db import queries  # noqa: E402
from app.db.migrate import run_migrations  # noqa: E402
from app.db.pool import close_pool, create_pool  # noqa: E402
from app.repair import daycheck, dayrepair, engine  # noqa: E402
from app.repair.market import IST, KiteGate, MinuteBook, kite_lookup, kite_ltp, kite_minute_source, kite_quote  # noqa: E402

HEARTBEAT = "repair_worker"


async def stored(pool, day) -> dict:
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


async def claim(pool):
    return await pool.fetchrow(
        "UPDATE repair_jobs SET status = 'running', started_at = now() WHERE id = ("
        "SELECT id FROM repair_jobs WHERE status = 'queued' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *")


async def main(args) -> int:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    books: dict = {}
    try:
        await run_migrations(pool)
        session = await queries.get_kite_session(pool)
        gate = None
        if session is not None and session["access_token"]:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=cfg["api_key"])
            kite.set_access_token(session["access_token"])
            gate = KiteGate(kite)
        idle_from = time.monotonic()
        while True:
            await queries.set_app_scratch(pool, HEARTBEAT, {"pid": os.getpid(), "at": datetime.now(IST).isoformat()})
            job = await claim(pool)
            if job is None:
                # without a Kite session every job fails: don't wait for more (the app starts it again)
                if gate is None or time.monotonic() - idle_from >= args.idle:
                    return 0
                await asyncio.sleep(args.poll)
                continue
            print(f"job {job['id']}: {job['kind']} {job['day']} {job['deployment_id']} {dict(job['choices'] or {})}", flush=True)
            try:
                if gate is None:
                    raise RuntimeError("No Kite session in the database — log in to Kite in the app first (the candles come from Kite).")
                day = job["day"]
                if day not in books:
                    books[day] = MinuteBook(day, kite_minute_source(gate), await stored(pool, day), lookup=kite_lookup(gate),
                                            ltp=kite_ltp(gate), history=gate.historical_data, quote=kite_quote(gate))
                res = await dayrepair.run(pool, job["deployment_id"], day, books[day], gate, dict(job["choices"] or {}),
                                          commit=job["kind"] == "apply", expected=job["expected"], author=job["author"])
                await pool.execute("UPDATE repair_jobs SET status = 'done', result = $2, finished_at = now() WHERE id = $1",
                                   job["id"], daycheck._json(res))
                print(f"job {job['id']}: done", flush=True)
            except Exception as e:
                msg = str(e) if isinstance(e, engine.PlanChanged) else f"{type(e).__name__}: {e}"
                await pool.execute("UPDATE repair_jobs SET status = 'failed', error = $2, finished_at = now() WHERE id = $1",
                                   job["id"], msg)
                print(f"job {job['id']}: failed: {msg}", flush=True)
            idle_from = time.monotonic()
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--idle", type=float, default=600, help="seconds with nothing to do before it stops (default 600)")
    ap.add_argument("--poll", type=float, default=0.5, help="seconds between looks at the queue")
    sys.exit(asyncio.run(main(ap.parse_args())))
