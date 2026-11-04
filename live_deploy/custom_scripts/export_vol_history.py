"""
Export the index history the TimesFM volatility experiment needs
(live_deploy/research/timesfm_vol), from Kite, with the app's own Kite
session (today's login in the app).

What it writes: one gzipped CSV, a row per candle,
    symbol,interval,ts,open,high,low,close
    NIFTY 50,day,2019-01-01T00:00:00+05:30,10881.7,10923.6,10807.1,10910.1
for NIFTY 50, NIFTY BANK and SENSEX (daily and 5-minute candles) and
INDIA VIX (daily). Kite answers at most 2,000 days of daily candles and
100 days of 5-minute candles a request, so it asks in pieces, spaced to
Kite's ~3 requests a second (KiteGate). From 2019 that's about 90
requests: under a minute.

Run it on the server, while the app is logged in to Kite:

    docker exec live-deploy python3 custom_scripts/export_vol_history.py > vol_history.csv.gz
    docker exec live-deploy python3 custom_scripts/export_vol_history.py --from 2021-01-01 --no-intraday > daily.csv.gz

Progress goes to stderr; only the file goes to stdout. It reads Kite and
nothing else: no table is written, no order placed.
"""

import argparse
import asyncio
import csv
import gzip
import io
import os
import sys
import time
from datetime import date, datetime, timedelta

os.environ["TZ"] = "Asia/Kolkata"
time.tzset()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (symbol, Kite token, with 5-minute candles)
SERIES = [("NIFTY 50", 256265, True), ("NIFTY BANK", 260105, True), ("SENSEX", 265, True), ("INDIA VIX", 264969, False)]
# Kite's longest span a request, by interval (a little under its limits)
SPAN = {"day": timedelta(days=1900), "5minute": timedelta(days=95)}
COLUMNS = ["symbol", "interval", "ts", "open", "high", "low", "close"]


def pieces(start: date, end: date, span: timedelta) -> list[tuple[date, date]]:
    """[start, end] cut into back-to-back spans of at most `span` days,
    each inclusive of its own last day, with no gap and no overlap."""
    out, a = [], start
    while a <= end:
        b = min(end, a + span - timedelta(days=1))
        out.append((a, b))
        a = b + timedelta(days=1)
    return out


def export(historical, out, start: date, end: date, intraday: bool = True, say=lambda s: None) -> int:
    """Every series' candles from `historical(token, from, to, interval)`
    (Kite's historical_data) written to `out` (a csv.writer) in COLUMNS'
    order. Returns the number of rows."""
    n = 0
    for symbol, token, has_intraday in SERIES:
        for interval in ("day", "5minute") if (intraday and has_intraday) else ("day",):
            got = 0
            for a, b in pieces(start, end, SPAN[interval]):
                frm = datetime.combine(a, datetime.min.time())
                to = datetime.combine(b, datetime.max.time().replace(microsecond=0))
                for c in historical(token, frm, to, interval) or []:
                    ts = c["date"]
                    out.writerow([symbol, interval, ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                                  c["open"], c["high"], c["low"], c["close"]])
                    got += 1
            say(f"{symbol} {interval}: {got} candles")
            n += got
    return n


async def _session(cfg) -> str:
    from app.db import queries
    from app.db.pool import close_pool, create_pool
    pool = await create_pool(cfg["database_url"])
    try:
        s = await queries.get_kite_session(pool)
    finally:
        await close_pool(pool)
    if s is None or not s["access_token"]:
        raise SystemExit("No Kite session in the database: log in to Kite in the app first.")
    return s["access_token"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", default="2019-01-01", help="first day (YYYY-MM-DD), default 2019-01-01")
    ap.add_argument("--to", dest="end", default=None, help="last day (YYYY-MM-DD), default today")
    ap.add_argument("--no-intraday", action="store_true", help="daily candles only (the experiment then uses each day's high and low)")
    args = ap.parse_args()

    from kiteconnect import KiteConnect

    from app.config import load_config
    from app.repair.market import KiteGate

    cfg = load_config()
    kite = KiteConnect(api_key=cfg["api_key"])
    kite.set_access_token(asyncio.run(_session(cfg)))
    gate = KiteGate(kite)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        w = csv.writer(text)
        w.writerow(COLUMNS)
        n = export(gate.historical_data, w, start, end, intraday=not args.no_intraday,
                   say=lambda s: print(s, file=sys.stderr, flush=True))
        text.flush()
        text.detach()
    sys.stdout.buffer.write(buf.getvalue())
    sys.stdout.buffer.flush()
    print(f"{n} candles, {start} to {end}, {gate.requests} Kite requests", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
