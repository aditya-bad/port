"""
live_deploy — custom_scripts/import_candles.py

Loads price history into the app's candle store (candles_1m, see
app/candles.py), so cycle charts work for contracts the app never
recorded itself (e.g. expired before this version). Two ways in:

1. Your own backups (CSV), any number of files or folders:
       python3 custom_scripts/import_candles.py --csv backups/
   Columns (header names, case-insensitive; extra columns ignored):
       instrument_token  or  tradingsymbol (+ optional exchange)
       date | datetime | timestamp   e.g. 2026-10-14 09:15:00 (IST if no zone)
       open, high, low, close, [volume]
   A file may hold one instrument or many. Only 1-MINUTE candles are
   accepted — every other interval is rolled up from them when a chart
   reads them, never stored — so a file of 5-minute or daily candles is
   refused, with the reason. A tradingsymbol is matched to its
   instrument token from the contracts the app knows (or any position
   that held it); unknown symbols are reported and skipped.

2. Kite, for every instrument held on each trading day of a range
   (exactly what runs by itself after every close; contracts Kite no
   longer lists can't be fetched — use backups for those):
       python3 custom_scripts/import_candles.py --kite-since 2026-10-01

Nothing is ever duplicated: one row per instrument per minute. Imported
rows replace candles the app built from its own ticks, and are replaced
by Kite's own. Safe to re-run. --dry-run reports without writing.

Inside the app container:
    docker exec live-deploy python3 custom_scripts/import_candles.py --csv /app/control/backups
"""
import argparse
import asyncio
import csv
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.candles import IST, SESSION_CLOSE, SESSION_OPEN, CandleService, parse_contract
from app.config import load_config
from app.db import queries
from app.db.pool import close_pool, create_pool


def _col(header: list[str], *names: str):
    low = {h.strip().lower(): i for i, h in enumerate(header)}
    for n in names:
        if n in low:
            return low[n]
    return None


def _ts(s: str) -> datetime:
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
        try:
            d = datetime.strptime(s.replace("+05:30", "+0530"), fmt)
            return d if d.tzinfo else d.replace(tzinfo=IST)
        except ValueError:
            continue
    return datetime.fromisoformat(s) if "+" in s else datetime.fromisoformat(s).replace(tzinfo=IST)


def read_csv(path: Path) -> tuple[dict, list[str]]:
    """{(token or None, symbol or None): [(minute, o, h, l, c, vol)]}, problems"""
    out: dict = defaultdict(list)
    problems = []
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return out, [f"{path}: empty"]
    h = rows[0]
    c_tok, c_sym = _col(h, "instrument_token", "token"), _col(h, "tradingsymbol", "symbol")
    c_t = _col(h, "date", "datetime", "timestamp", "time")
    c_o, c_h, c_l, c_c = (_col(h, n) for n in ("open", "high", "low", "close"))
    c_v = _col(h, "volume")
    if c_t is None or None in (c_o, c_h, c_l, c_c) or (c_tok is None and c_sym is None):
        return out, [f"{path}: needs date, open, high, low, close and instrument_token or tradingsymbol columns"]
    for n, r in enumerate(rows[1:], start=2):
        try:
            key = (int(r[c_tok]) if c_tok is not None and r[c_tok].strip() else None,
                   r[c_sym].strip() if c_sym is not None else None)
            out[key].append((_ts(r[c_t]), float(r[c_o]), float(r[c_h]), float(r[c_l]), float(r[c_c]),
                             int(float(r[c_v])) if c_v is not None and r[c_v].strip() else None))
        except (ValueError, IndexError) as e:
            problems.append(f"{path}:{n}: skipped ({e})")
    return out, problems


def interval_seconds(rows) -> float:
    """Typical gap between consecutive candles of one day."""
    gaps = []
    rows = sorted(rows)
    for a, b in zip(rows, rows[1:]):
        if a[0].astimezone(IST).date() == b[0].astimezone(IST).date():
            gaps.append((b[0] - a[0]).total_seconds())
    return statistics.median(gaps) if gaps else 60.0


async def import_csv(pool, paths: list[Path], dry: bool) -> None:
    files = [p for path in paths for p in (sorted(path.rglob("*.csv")) if path.is_dir() else [path])]
    total = 0
    for f in files:
        data, problems = read_csv(f)
        for p in problems[:5]:
            print("  !", p)
        for (tok, sym), rows in data.items():
            name = sym or str(tok)
            gap = interval_seconds(rows)
            if gap != 60:
                print(f"  ✗ {f.name} · {name}: {gap / 60:g}-minute candles — only 1-minute candles are stored "
                      f"(every other interval is rolled up from them). Skipped.")
                continue
            if tok is None:
                tok = await queries.find_contract_token(pool, sym)
                if tok is None:
                    print(f"  ✗ {f.name} · {sym}: unknown tradingsymbol (no contract or position has it); "
                          f"add an instrument_token column. Skipped.")
                    continue
            keep = [(tok, *r) for r in rows if SESSION_OPEN <= r[0].astimezone(IST).time() < SESSION_CLOSE]
            print(f"  ✓ {f.name} · {name} ({tok}): {len(keep)} minute(s)"
                  f"{f', {len(rows) - len(keep)} outside 09:15-15:30 dropped' if len(keep) < len(rows) else ''}")
            if not dry:
                await queries.upsert_candles(pool, keep, "import")
                if sym and parse_contract(sym):
                    await queries.upsert_contracts(pool, [{"instrument_token": tok, "tradingsymbol": sym, **parse_contract(sym)}])
            total += len(keep)
    print(f"{'Would import' if dry else 'Imported'} {total} minute(s) from {len(files)} file(s).")


async def import_kite(pool, cfg, since: date, dry: bool) -> None:
    session = await queries.get_kite_session(pool)
    if not session:
        sys.exit("No Kite session in the database: log in from the app first.")
    dispatcher = SimpleNamespace(api_key=cfg["api_key"], access_token=session["access_token"],
                                 token_labels={}, last_prices={}, last_depths={})
    svc = CandleService(pool, dispatcher)
    day, today = since, datetime.now(IST).date()
    while day <= today:
        if day.weekday() < 5:
            if dry:
                held = await queries.instruments_held_between(
                    pool, datetime.combine(day, SESSION_OPEN, IST), datetime.combine(day, SESSION_CLOSE, IST))
                print(f"  {day}: {len(held)} instrument(s) held")
            else:
                r = await svc.backfill_day(day)
                print(f"  {day}: {r['instruments']} instrument(s), {len(r['fetched'])} fetched, {r['rows']} minute(s)")
        day += timedelta(days=1)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", nargs="+", type=Path, help="CSV files or folders of 1-minute candles")
    ap.add_argument("--kite-since", type=date.fromisoformat, help="fill every held instrument from Kite since this date")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.csv and not a.kite_since:
        ap.error("give --csv and/or --kite-since")
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        if a.csv:
            await import_csv(pool, a.csv, a.dry_run)
        if a.kite_since:
            await import_kite(pool, cfg, a.kite_since, a.dry_run)
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    asyncio.run(main())
