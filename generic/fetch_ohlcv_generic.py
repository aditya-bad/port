#!/usr/bin/env python3
"""
Generic OHLCV fetcher — works with any user-defined universe.

Reuses the symbol resolution chain (exact → underscore-split → prefix →
fuzzy), retry/backoff logic, rate limiting, and SYMBOL_OVERRIDES mechanism
from the Nifty-specific fetch_ohlcv.py.  New/parameterized: universe path,
start date, output directory.

Usage:
    python -m generic.fetch_ohlcv_generic --universe universes/midcapshop
    python -m generic.fetch_ohlcv_generic --universe universes/midcapshop --start-date 2015-01-01
    python -m generic.fetch_ohlcv_generic --universe universes/midcapshop --dry-run
    python -m generic.fetch_ohlcv_generic --universe universes/midcapshop --resume
"""

import csv
import json
import sys
import argparse
from datetime import date
from pathlib import Path

# Kite-related imports are deferred to main() so that this module can be
# imported without kiteconnect installed (the universe loader function is
# used by backtest_generic.py which doesn't need Kite at all).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GENERIC_DIR = Path(__file__).resolve().parent


# ── Universe loading ────────────────────────────────────────────────

def load_universe_symbols(universe_dir: Path) -> list[str]:
    """
    Auto-detect and load the universe symbol list.

    Supported formats (detected by file extension):
      - .txt  — one symbol per line (blank lines / # comments ignored)
      - .csv  — single column, or a column named 'symbol'
      - .json — array of strings
    """
    candidates = list(universe_dir.glob("universe_input.*"))
    if not candidates:
        sys.exit(
            f"No universe_input.* file found in {universe_dir}/.\n"
            f"Create one: universe_input.txt (one symbol per line), "
            f".csv, or .json (array of strings)."
        )

    # Prefer the first match; warn if multiple
    path = candidates[0]
    if len(candidates) > 1:
        print(f"  ⚠ Multiple universe_input files found, using {path.name}")

    ext = path.suffix.lower()

    if ext == ".txt":
        symbols = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                symbols.append(line.upper())
        return symbols

    elif ext == ".csv":
        symbols = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            # If 'symbol' column exists, use it; else use the first column
            if reader.fieldnames and "symbol" in [fn.lower() for fn in reader.fieldnames]:
                col = next(fn for fn in reader.fieldnames
                           if fn.lower() == "symbol")
                for row in reader:
                    val = row[col].strip().upper()
                    if val:
                        symbols.append(val)
            else:
                # Re-read as plain rows, first column
                f.seek(0)
                plain = csv.reader(f)
                for row in plain:
                    if row:
                        val = row[0].strip().upper()
                        if val and val != "SYMBOL":
                            symbols.append(val)
        return symbols

    elif ext == ".json":
        raw = json.loads(path.read_text())
        if isinstance(raw, list):
            return [s.upper() for s in raw if isinstance(s, str)]
        sys.exit(f"{path}: expected a JSON array of strings")

    else:
        sys.exit(f"Unsupported universe file extension: {ext} "
                 f"(use .txt, .csv, or .json)")


# ── Fetch log (parameterized write) ────────────────────────────────

def write_generic_fetch_log(
    log_path: Path,
    resolved: dict,
    unresolved: list,
    no_data: list,
    fetch_results: dict,
    today: date,
    fetch_start: date,
) -> None:
    """Write _fetch_log.json manifest — same shape as Nifty's, path-independent."""
    log = {
        "fetch_date": today.isoformat(),
        "fetch_range": {
            "from": fetch_start.isoformat(),
            "to":   today.isoformat(),
        },
        "summary": {
            "total_symbols":  len(resolved) + len(unresolved) + len(no_data),
            "resolved":       len(resolved),
            "unresolved":     len(unresolved),
            "no_kite_data":   len(no_data),
            "fetched_ok":     sum(1 for r in fetch_results.values()
                                  if r.get("status") == "success"),
            "fetch_errors":   sum(1 for r in fetch_results.values()
                                  if r.get("status") == "error"),
            "skipped_resume": sum(1 for r in fetch_results.values()
                                  if r.get("status") == "skipped_existing"),
        },
        "symbols": {},
    }

    for sym, info in resolved.items():
        entry = {
            "instrument_token":       info["instrument_token"],
            "resolved_tradingsymbol": info["tradingsymbol"],
            "name":                   info["name"],
            "match_type":             info["match_type"],
        }
        if sym in fetch_results:
            entry.update(fetch_results[sym])
        log["symbols"][sym] = entry

    for sym in unresolved:
        log["symbols"][sym] = {
            "status":           "unresolved",
            "instrument_token": None,
            "reason":           "No matching instrument found on NSE",
        }

    for sym in no_data:
        log["symbols"][sym] = {
            "status":           "no_kite_data",
            "instrument_token": None,
            "reason":           "Manually flagged in SYMBOL_OVERRIDES",
        }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)


# ── Main ────────────────────────────────────────────────────────────

def main() -> None:
    # Deferred imports — these pull in kiteconnect, which is only needed
    # when actually fetching data (not when this module is imported for
    # its load_universe_symbols function by backtest_generic.py).
    from fetch_ohlcv import load_config, resolve_symbols, fetch_symbol
    from kiteconnect import KiteConnect

    parser = argparse.ArgumentParser(
        description="Generic OHLCV fetcher — any universe",
    )
    parser.add_argument(
        "--universe", required=True,
        help="Path to universe directory (e.g. universes/midcapshop). "
             "Must contain a universe_input.{txt|csv|json} file.",
    )
    parser.add_argument(
        "-c", "--config", default="config.json",
        help="Path to Kite credentials JSON. Default: config.json",
    )
    parser.add_argument(
        "--start-date", default=None,
        help="Fetch start date (YYYY-MM-DD). Defaults to 2015-01-01. "
             "Set based on your backtest window + DMA warmup period.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve symbols only, no OHLCV fetch.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip symbols whose output file already exists.",
    )
    args = parser.parse_args()

    # Resolve universe path — allow both absolute and relative to generic/
    universe_dir = Path(args.universe)
    if not universe_dir.is_absolute():
        universe_dir = GENERIC_DIR / universe_dir
    universe_dir = universe_dir.resolve()

    if not universe_dir.is_dir():
        sys.exit(f"Universe directory not found: {universe_dir}")

    ohlcv_dir = universe_dir / "data" / "ohlcv"
    ohlcv_dir.mkdir(parents=True, exist_ok=True)
    fetch_log_path = ohlcv_dir / "_fetch_log.json"

    fetch_start = (
        date.fromisoformat(args.start_date)
        if args.start_date
        else date(2015, 1, 1)
    )
    today = date.today()

    print(f"\n{'═' * 60}")
    print(f"  Generic OHLCV Fetch")
    print(f"  Universe : {universe_dir.name}")
    print(f"  Fetch    : {fetch_start} → {today}")
    print(f"  Output   : {ohlcv_dir}/")
    print(f"{'═' * 60}\n")

    # Load universe symbols
    symbols = load_universe_symbols(universe_dir)
    if not symbols:
        sys.exit("No symbols found in universe input file.")
    print(f"Universe: {len(symbols)} symbols")

    # Kite session
    config = load_config(args.config)
    kite = KiteConnect(api_key=config["api_key"])
    kite.set_access_token(config["access_token"])

    # Resolve — reuses the full resolution chain from fetch_ohlcv.py
    resolved, unresolved, no_data = resolve_symbols(kite, symbols)
    print(f"\n  Resolved   : {len(resolved)}")
    print(f"  Unresolved : {len(unresolved)}")
    print(f"  No Kite data (override): {len(no_data)}")
    if unresolved:
        print(f"    → {', '.join(unresolved)}")
    if no_data:
        print(f"    → {', '.join(no_data)}")

    non_exact = {s: i for s, i in resolved.items() if i["match_type"] != "exact"}
    if non_exact:
        print(f"\n  ⚠ {len(non_exact)} non-exact match(es) — review before trusting:")
        for s, i in non_exact.items():
            print(f"    {s} → {i['tradingsymbol']} ({i['match_type']})")

    if args.dry_run:
        print("\n--dry-run: skipping OHLCV fetch.\n")
        write_generic_fetch_log(
            fetch_log_path, resolved, unresolved, no_data, {}, today, fetch_start)
        print(f"Fetch log → {fetch_log_path}")
        return

    # Fetch
    fetch_results: dict[str, dict] = {}
    total = len(resolved)

    for i, (sym, info) in enumerate(resolved.items(), 1):
        token = info["instrument_token"]
        ts    = info["tradingsymbol"]
        out   = ohlcv_dir / f"{sym}.json"

        if args.resume and out.exists():
            print(f"[{i}/{total}] {sym} — skipped (file exists)")
            fetch_results[sym] = {"status": "skipped_existing"}
            continue

        print(f"[{i}/{total}] {sym} → {ts}  (token {token})")

        try:
            candles = fetch_symbol(kite, token, fetch_start, today, label=sym)
            with open(out, "w") as f:
                json.dump(candles, f)
            first = candles[0]["date"] if candles else None
            last  = candles[-1]["date"] if candles else None
            print(f"  ✓ {len(candles)} candles  ({first} … {last})")
            fetch_results[sym] = {
                "status":     "success",
                "candles":    len(candles),
                "first_date": first,
                "last_date":  last,
                "file":       f"{sym}.json",
            }
        except Exception as exc:
            print(f"  ✗ {exc}")
            fetch_results[sym] = {"status": "error", "error": str(exc)}

    write_generic_fetch_log(
        fetch_log_path, resolved, unresolved, no_data,
        fetch_results, today, fetch_start)

    ok    = sum(1 for r in fetch_results.values() if r["status"] == "success")
    errs  = sum(1 for r in fetch_results.values() if r["status"] == "error")
    skips = sum(1 for r in fetch_results.values() if r["status"] == "skipped_existing")
    print(f"\nDone — {ok} fetched, {errs} errors, {skips} skipped")
    print(f"Log → {fetch_log_path}")


if __name__ == "__main__":
    main()
