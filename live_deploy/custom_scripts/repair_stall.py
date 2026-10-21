#!/usr/bin/env python3
"""
live_deploy — custom_scripts/repair_stall.py

INTERACTIVE repair of a stretch of the session the app missed — a Kite
tick stall (ticks stopped, nothing traded, P&L frozen) — one deployment at
a time. Paper trades only: it rewrites the app's own book, never places an
order.

What it does: every deployment's OWN strategy is run again over the
window on Kite's 1-minute candles, through the real runner and fill
engine, starting from exactly what the database holds (the open legs,
cash and saved state as the stall left them). Each minute is four ticks —
open, one extreme, the other, close — with every CE at its high and PE at
its low when Nifty is at its high (and the other way round). Where the
order inside a minute changes what the strategy does (adjust or not, which
strike, a stop at the high or at the low), it stops and shows you both
paths; you choose, and it carries on from your path to the next one.

  1. The list: every deployment not stopped, scanned — open legs, trades
     the replay makes, minutes you'll need to choose, the window's P&L
     repaired vs as the stall left it.
  2. Pick one: choose the unclear minutes; see the plan (trades, P&L,
     booked, charges, best/worst, P&L and Nifty through the window, legs
     after).
  3. Apply: that deployment only (paused through the app first, resumed
     after), recorded with an undo, a journal note and an Activity event.
     Then pick the next one.

USAGE (inside the app container, after the close and BEFORE the next
09:15 open — an intraday leg left open is otherwise closed at the next
day's first tick at the next day's price):

    docker exec -it live-deploy python3 custom_scripts/repair_stall.py --from 12:16
    docker exec -it live-deploy python3 custom_scripts/repair_stall.py --date 2026-10-21 --from 12:16 --to 15:30
    docker exec -it live-deploy python3 custom_scripts/repair_stall.py --date 2026-10-21 --probe   # what Kite answers, raw

--from is the first minute the app missed: the minute after the last tick
(the app's /health shows last_tick_at; asked for if not given). Needs a
Kite session (today's login) for the candles; contracts Kite no longer
lists fall back to the app's own stored candles. A contract with no
trade all day (thin SENSEX/BANKEX monthlies) is looked up by name on its
own exchange, and if Kite still has nothing, priced by model off its
index from its last known price; the plan says so.
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
from app.repair import engine  # noqa: E402
from app.repair.cli import AppApi, RepairCLI, color_ok, parse_hhmm  # noqa: E402
from app.repair.market import IST, KiteGate, MinuteBook, kite_lookup, kite_ltp, kite_minute_source  # noqa: E402


async def stored_candles(pool, day) -> dict:
    """The app's own 1-minute candles for the day, by token."""
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


async def main(args) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        await run_migrations(pool)
        session = await queries.get_kite_session(pool)
        if session is None or not session["access_token"]:
            print("No Kite session in the database — log in to Kite in the app first (the candles come from Kite).")
            return
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=cfg["api_key"])
        kite.set_access_token(session["access_token"])
        gate = KiteGate(kite)

        api = AppApi(None if args.no_api else args.api, cfg["app_auth_secret"])
        day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(IST).date()
        if args.probe:
            from app.repair.probe import probe
            await probe(pool, gate, day, kite_lookup(gate))
            return
        start = args.start
        if not start:
            last = api.last_tick_at()
            hint = ""
            if last:
                t = datetime.fromisoformat(str(last).replace("Z", "+00:00")).astimezone(IST)
                hint = f" (the app's last tick was {t:%H:%M:%S}; the next minute is {t + timedelta(minutes=1):%H:%M})"
            start = input(f"First minute the app missed, HH:MM{hint} › ").strip()
        window = engine.Window(day, parse_hhmm(start), parse_hhmm(args.end))
        why = engine.window_problem(window, datetime.now(IST))
        if why:
            print(why)
            return

        book = MinuteBook(day, kite_minute_source(gate), await stored_candles(pool, day), lookup=kite_lookup(gate),
                          ltp=kite_ltp(gate))
        cli = RepairCLI(pool, book, gate, window, api, color=color_ok(sys.stdout), author=args.author)
        await cli.run()
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="the day to repair, YYYY-MM-DD (default: today, IST)")
    ap.add_argument("--from", dest="start", help="first minute the app missed, HH:MM (asked for if not given)")
    ap.add_argument("--to", dest="end", default="15:30", help="end of the window, HH:MM (default 15:30, the close)")
    ap.add_argument("--api", default="http://localhost:8000", help="the running app, to pause/resume a deployment")
    ap.add_argument("--no-api", action="store_true", help="the app isn't running: don't try to pause/resume")
    ap.add_argument("--author", default="repair_stall", help="name on the journal note")
    ap.add_argument("--probe", action="store_true",
                    help="don't repair: ask Kite for every deployment's index and open legs on the day and print what it answers")
    asyncio.run(main(ap.parse_args()))
