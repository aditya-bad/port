#!/usr/bin/env python3
"""
NiftyShop Stage 1 — Fetch daily OHLCV data via Kite Connect.

Fetches historical daily candles for every symbol in the union universe
(all stocks that were ever in Nifty 50 during the backtest window)
and stores one JSON file per symbol under data/ohlcv/.

Usage:
    # With config file (default: config.json)
    python fetch_ohlcv.py

    # Explicit config path
    python fetch_ohlcv.py -c /path/to/config.json

    # Resolve symbols only, no fetch
    python fetch_ohlcv.py --dry-run

    # Resume a partial run (skip symbols that already have data files)
    python fetch_ohlcv.py --resume
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

from kiteconnect import KiteConnect


# ── Constants ──────────────────────────────────────────────────────────

FETCH_START = date(2009, 11, 1)
MAX_DAYS_PER_REQUEST = 2000     # Kite caps daily candles at ~2000 per call
RATE_LIMIT_DELAY = 0.35         # seconds between API calls (< 3 req/sec)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2          # exponential backoff: 2^attempt seconds
FUZZY_THRESHOLD = 0.5           # minimum score for fuzzy name match

DATA_DIR = Path(__file__).parent / "data"
OHLCV_DIR = DATA_DIR / "ohlcv"
UNIVERSE_FILE = DATA_DIR / "union_universe_2010_2026.json"
FETCH_LOG_FILE = OHLCV_DIR / "_fetch_log.json"


# ── Config ─────────────────────────────────────────────────────────────

def load_config(config_path: str | None) -> dict:
    """Load API credentials from a JSON file, or prompt interactively."""
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            cfg = json.load(f)
        required = ["api_key", "api_secret", "access_token"]
        missing = [k for k in required if not cfg.get(k)]
        if missing:
            sys.exit(f"Config file missing: {', '.join(missing)}")
        print(f"Loaded credentials from {config_path}")
        return cfg

    print("No config file found — enter Kite Connect credentials:")
    return {
        "api_key":      input("  api_key: ").strip(),
        "api_secret":   input("  api_secret: ").strip(),
        "access_token": input("  access_token: ").strip(),
    }


# ── Symbol resolution ─────────────────────────────────────────────────

def _build_instrument_index(instruments: list[dict]) -> dict:
    """Index NSE instruments by uppercased tradingsymbol."""
    idx = {}
    for inst in instruments:
        if inst.get("exchange") == "NSE":
            idx[inst["tradingsymbol"].upper()] = inst
    return idx


def _fuzzy_best(query: str, instruments: list[dict]) -> tuple[dict | None, float]:
    """Find the NSE instrument whose company name best matches `query`."""
    best_score, best_inst = 0.0, None
    query_upper = query.upper()

    for inst in instruments:
        if inst.get("exchange") != "NSE":
            continue
        name = (inst.get("name") or "").upper()
        if not name:
            continue

        score = SequenceMatcher(None, query_upper, name).ratio()

        # Also try with common trailing abbreviations stripped from the query
        # (e.g. SATYAMCOMP → SATYAM vs "SATYAM COMPUTER SERVICES")
        for suffix in ("COMP", "LTD", "IND", "STEL", "CEM"):
            if query_upper.endswith(suffix) and len(query_upper) > len(suffix):
                trimmed = query_upper[: -len(suffix)]
                s = SequenceMatcher(None, trimmed, name).ratio()
                score = max(score, s)

        if score > best_score:
            best_score, best_inst = score, inst

    return best_inst, best_score


def _make_entry(inst: dict, match_type: str) -> dict:
    return {
        "instrument_token": inst["instrument_token"],
        "tradingsymbol":    inst["tradingsymbol"],
        "name":             inst.get("name", ""),
        "match_type":       match_type,
    }


def resolve_symbols(
    kite: KiteConnect, target_symbols: list[str]
) -> tuple[dict, list]:
    """
    Resolve target symbols to Kite instrument tokens.

    Strategy (applied uniformly to every symbol):
      1. Exact match on tradingsymbol
      2. Underscore-separated symbols — try each part as exact match
      3. Prefix / substring overlap on tradingsymbol
      4. Fuzzy match on company name

    Returns (resolved, unresolved):
      resolved  — {original_symbol: {instrument_token, tradingsymbol, name, match_type}}
      unresolved — [symbol, ...]
    """
    print("Fetching NSE instrument list…")
    instruments = kite.instruments("NSE")
    print(f"  {len(instruments)} instruments from NSE")

    by_sym = _build_instrument_index(instruments)

    resolved = {}
    unresolved = []

    for sym in target_symbols:
        key = sym.upper()

        # 1 — exact tradingsymbol
        if key in by_sym:
            resolved[sym] = _make_entry(by_sym[key], "exact")
            continue

        # 2 — underscore-separated (e.g. BHARTIARTL_INFRATEL)
        if "_" in key:
            for part in key.split("_"):
                if part in by_sym:
                    resolved[sym] = _make_entry(by_sym[part], f"partial_split ({part})")
                    break
            if sym in resolved:
                continue

        # 3 — prefix / substring on tradingsymbol
        prefix_hits = [
            inst for ts, inst in by_sym.items()
            if ts.startswith(key) or key.startswith(ts)
        ]
        if len(prefix_hits) == 1:
            resolved[sym] = _make_entry(prefix_hits[0], "prefix")
            continue

        # 4 — fuzzy company-name match
        best_inst, best_score = _fuzzy_best(sym, instruments)
        if best_inst and best_score >= FUZZY_THRESHOLD:
            resolved[sym] = _make_entry(best_inst, f"fuzzy ({best_score:.2f})")
            continue

        unresolved.append(sym)

    return resolved, unresolved


# ── OHLCV fetch ────────────────────────────────────────────────────────

def _date_chunks(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    """Split [start, end] into sub-ranges of at most max_days each."""
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _format_candle(row: dict) -> dict:
    d = row["date"]
    return {
        "date":   d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10],
        "open":   row["open"],
        "high":   row["high"],
        "low":    row["low"],
        "close":  row["close"],
        "volume": row["volume"],
    }


def fetch_symbol(
    kite: KiteConnect,
    instrument_token: int,
    start: date,
    end: date,
    label: str = "",
) -> list[dict]:
    """Fetch daily OHLCV for one instrument, with chunking + retries."""
    chunks = _date_chunks(start, end, MAX_DAYS_PER_REQUEST)
    all_candles: list[dict] = []

    for ci, (cs, ce) in enumerate(chunks):
        for attempt in range(MAX_RETRIES):
            try:
                time.sleep(RATE_LIMIT_DELAY)
                raw = kite.historical_data(
                    instrument_token=instrument_token,
                    from_date=datetime.combine(cs, datetime.min.time()),
                    to_date=datetime.combine(ce, datetime.min.time()),
                    interval="day",
                )
                all_candles.extend(_format_candle(r) for r in raw)
                break
            except Exception as exc:
                wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                if attempt < MAX_RETRIES - 1:
                    print(f"    ↻ chunk {ci+1}/{len(chunks)} attempt {attempt+1} "
                          f"failed, retry in {wait}s: {exc}")
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"{label}: chunk {cs}→{ce} failed after "
                        f"{MAX_RETRIES} retries: {exc}"
                    ) from exc

    return all_candles


# ── Fetch log ──────────────────────────────────────────────────────────

def write_fetch_log(
    resolved: dict,
    unresolved: list,
    fetch_results: dict,
    today: date,
) -> None:
    """Write data/ohlcv/_fetch_log.json manifest."""
    log = {
        "fetch_date": today.isoformat(),
        "fetch_range": {
            "from": FETCH_START.isoformat(),
            "to":   today.isoformat(),
        },
        "summary": {
            "total_symbols":  len(resolved) + len(unresolved),
            "resolved":       len(resolved),
            "unresolved":     len(unresolved),
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
            "instrument_token":      info["instrument_token"],
            "resolved_tradingsymbol": info["tradingsymbol"],
            "name":                  info["name"],
            "match_type":            info["match_type"],
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

    FETCH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FETCH_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


# ── Main ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NiftyShop Stage 1 — fetch daily OHLCV via Kite Connect",
    )
    parser.add_argument(
        "-c", "--config", default="config.json",
        help="Path to JSON config (api_key, api_secret, access_token). "
             "Default: config.json. Prompts interactively if missing.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve symbols and write fetch log, but skip the actual OHLCV fetch.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip symbols whose output file already exists in data/ohlcv/.",
    )
    args = parser.parse_args()

    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    today = date.today()

    # ── Kite session ───────────────────────────────────────────────────
    kite = KiteConnect(api_key=config["api_key"])
    kite.set_access_token(config["access_token"])

    # ── Load universe ──────────────────────────────────────────────────
    with open(UNIVERSE_FILE) as f:
        universe = json.load(f)
    symbols = universe["symbols"]
    print(f"\nUniverse: {len(symbols)} symbols")

    # ── Resolve ────────────────────────────────────────────────────────
    resolved, unresolved = resolve_symbols(kite, symbols)
    print(f"\n  Resolved : {len(resolved)}")
    print(f"  Unresolved: {len(unresolved)}")
    if unresolved:
        print(f"    → {', '.join(unresolved)}")

    if args.dry_run:
        print("\n--dry-run: skipping OHLCV fetch.\n")
        write_fetch_log(resolved, unresolved, {}, today)
        print(f"Fetch log → {FETCH_LOG_FILE}")
        return

    # ── Fetch ──────────────────────────────────────────────────────────
    fetch_results: dict[str, dict] = {}
    total = len(resolved)

    for i, (sym, info) in enumerate(resolved.items(), 1):
        token = info["instrument_token"]
        ts    = info["tradingsymbol"]
        out   = OHLCV_DIR / f"{sym}.json"

        if args.resume and out.exists():
            print(f"[{i}/{total}] {sym} — skipped (file exists)")
            fetch_results[sym] = {"status": "skipped_existing"}
            continue

        print(f"[{i}/{total}] {sym} → {ts}  (token {token})")

        try:
            candles = fetch_symbol(kite, token, FETCH_START, today, label=sym)
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
            fetch_results[sym] = {
                "status": "error",
                "error":  str(exc),
            }

    # ── Log ────────────────────────────────────────────────────────────
    write_fetch_log(resolved, unresolved, fetch_results, today)

    ok    = sum(1 for r in fetch_results.values() if r["status"] == "success")
    errs  = sum(1 for r in fetch_results.values() if r["status"] == "error")
    skips = sum(1 for r in fetch_results.values() if r["status"] == "skipped_existing")
    print(f"\nDone — {ok} fetched, {errs} errors, {skips} skipped")
    print(f"Log → {FETCH_LOG_FILE}")


if __name__ == "__main__":
    main()
