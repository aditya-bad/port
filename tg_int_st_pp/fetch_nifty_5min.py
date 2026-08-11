#!/usr/bin/env python3
"""
tg_int_st_pp — NIFTY 50 index, 5-minute candle fetcher.

Standalone script: does NOT import anything from the rest of the `port`
repo. Everything this needs (config loading, instrument resolution,
chunked fetch with retry/backoff, rate limiting, fetch-log manifest) is
self-contained in this one file.

Fetches 5-minute OHLCV candles for the NIFTY 50 index (not the
constituent stocks) via Kite Connect.

Kite's historical-data API caps how much history you can pull in a
single request per interval. For "5minute" the cap used here is 200
days per request (see MAX_DAYS_PER_REQUEST below) — requests are
chunked to respect that, with retry/backoff per chunk, even though a
10-day default lookback fits in a single chunk. This keeps the script
correct if the lookback window is later widened past 200 days.

Usage:
    # With config file (default: config.json in this folder)
    python fetch_nifty_5min.py

    # Explicit config path
    python fetch_nifty_5min.py -c /path/to/config.json

    # Custom lookback window (default: 10 days)
    python fetch_nifty_5min.py --days 15

    # Resolve the NIFTY 50 index instrument only, no fetch
    python fetch_nifty_5min.py --dry-run
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime, date, time as dtime, timedelta
from pathlib import Path

from kiteconnect import KiteConnect


# ── Constants ────────────────────────────────────────────────────────

INTERVAL = "5minute"
MAX_DAYS_PER_REQUEST = 200      # Kite's per-request cap for 5minute interval
RATE_LIMIT_DELAY = 0.35         # seconds between API calls (< 3 req/sec)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2          # exponential backoff: 2^attempt seconds
DEFAULT_LOOKBACK_DAYS = 10

# Well-known NIFTY 50 index instrument_token on Kite/NSE. Used ONLY as a
# last-resort fallback if dynamic resolution below fails, and only with
# a loud warning — never silently. Dynamic resolution (by segment +
# tradingsymbol) is the primary path so this script doesn't repeat the
# "confident wrong match" mistakes documented in the main repo's Stage 1
# symbol-resolution notes (BHARTIARTL_INFRATEL / IDFC).
NIFTY50_FALLBACK_TOKEN = 256265

THIS_DIR = Path(__file__).parent
DATA_DIR = THIS_DIR / "data"
OUTPUT_FILE = DATA_DIR / "NIFTY50_5minute.json"
FETCH_LOG_FILE = DATA_DIR / "_fetch_log.json"


# ── Config ───────────────────────────────────────────────────────────

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


# ── Instrument resolution — NIFTY 50 index only ─────────────────────

def resolve_nifty50_index(kite: KiteConnect) -> tuple[dict, str]:
    """
    Resolve the NIFTY 50 index instrument.

    Strategy:
      1. Exact match: segment == "INDICES" and tradingsymbol == "NIFTY 50"
      2. Fallback: segment == "INDICES" and "NIFTY 50" in name (case-insensitive)
      3. Last resort: hardcoded instrument_token, with a loud warning —
         only used if steps 1-2 both fail to produce exactly one candidate.

    Returns (instrument_dict, match_type).
    """
    print("Fetching NSE instrument list…")
    instruments = kite.instruments("NSE")
    print(f"  {len(instruments)} instruments from NSE")

    # 1 — exact tradingsymbol match on the INDICES segment
    exact = [
        i for i in instruments
        if i.get("segment") == "INDICES"
        and i.get("tradingsymbol", "").strip().upper() == "NIFTY 50"
    ]
    if len(exact) == 1:
        return exact[0], "exact"
    if len(exact) > 1:
        print(f"  ⚠ {len(exact)} exact matches for 'NIFTY 50' on INDICES — "
              f"using the first, review manually if this looks wrong.")
        return exact[0], "exact_ambiguous"

    # 2 — fallback: name contains "NIFTY 50" on INDICES segment
    name_hits = [
        i for i in instruments
        if i.get("segment") == "INDICES"
        and "NIFTY 50" in (i.get("name") or "").upper()
    ]
    if len(name_hits) == 1:
        return name_hits[0], "name_match"
    if len(name_hits) > 1:
        print(f"  ⚠ {len(name_hits)} name matches for 'NIFTY 50' on INDICES — "
              f"using the first, review manually if this looks wrong.")
        return name_hits[0], "name_match_ambiguous"

    # 3 — last resort: hardcoded fallback token, loudly flagged
    print(f"  ⚠⚠ Could not dynamically resolve NIFTY 50 index instrument. "
          f"Falling back to hardcoded instrument_token={NIFTY50_FALLBACK_TOKEN}. "
          f"VERIFY this is correct before trusting the fetched data.")
    return {
        "instrument_token": NIFTY50_FALLBACK_TOKEN,
        "tradingsymbol": "NIFTY 50",
        "name": "NIFTY 50",
        "segment": "INDICES",
        "exchange": "NSE",
    }, "hardcoded_fallback"


# ── Date chunking (respects MAX_DAYS_PER_REQUEST) ───────────────────

def date_chunks(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    """Split [start, end] into sub-ranges of at most max_days each."""
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


# ── Candle formatting ────────────────────────────────────────────────

def format_candle(row: dict) -> dict:
    d = row["date"]
    ts = d.strftime("%Y-%m-%d %H:%M:%S") if hasattr(d, "strftime") else str(d)
    return {
        "date":   ts,
        "open":   row["open"],
        "high":   row["high"],
        "low":    row["low"],
        "close":  row["close"],
        "volume": row.get("volume", 0),
    }


# ── Chunked fetch with retry/backoff + rate limiting ────────────────

def fetch_candles(
    kite: KiteConnect,
    instrument_token: int,
    start: date,
    end: date,
    interval: str,
    label: str = "",
) -> tuple[list[dict], list[dict]]:
    """
    Fetch intraday candles for [start, end], chunked to respect
    MAX_DAYS_PER_REQUEST, with retry/backoff per chunk and rate limiting
    between every API call.

    Returns (candles, chunk_log) — chunk_log records per-chunk outcome
    for the fetch-log manifest (pagination detail).
    """
    chunks = date_chunks(start, end, MAX_DAYS_PER_REQUEST)
    all_candles: list[dict] = []
    chunk_log: list[dict] = []

    print(f"  {len(chunks)} chunk(s) for {start} → {end} "
          f"(max {MAX_DAYS_PER_REQUEST} days/request)")

    for ci, (cs, ce) in enumerate(chunks):
        # Intraday interval: to_date's TIME component matters — Kite
        # filters candles strictly by timestamp, so anchoring to_date at
        # 00:00:00 would silently drop the entire last day's candles.
        from_dt = datetime.combine(cs, dtime.min)
        to_dt   = datetime.combine(ce, dtime(23, 59, 59))

        for attempt in range(MAX_RETRIES):
            try:
                time.sleep(RATE_LIMIT_DELAY)
                raw = kite.historical_data(
                    instrument_token=instrument_token,
                    from_date=from_dt,
                    to_date=to_dt,
                    interval=interval,
                )
                formatted = [format_candle(r) for r in raw]
                all_candles.extend(formatted)
                chunk_log.append({
                    "chunk_index": ci,
                    "from": cs.isoformat(),
                    "to": ce.isoformat(),
                    "candles": len(formatted),
                    "status": "success",
                })
                print(f"    chunk {ci+1}/{len(chunks)}: {cs} → {ce} "
                      f"— {len(formatted)} candles")
                break
            except Exception as exc:
                wait = RETRY_BACKOFF_BASE ** (attempt + 1)
                if attempt < MAX_RETRIES - 1:
                    print(f"    ↻ chunk {ci+1}/{len(chunks)} attempt {attempt+1} "
                          f"failed, retry in {wait}s: {exc}")
                    time.sleep(wait)
                else:
                    chunk_log.append({
                        "chunk_index": ci,
                        "from": cs.isoformat(),
                        "to": ce.isoformat(),
                        "status": "error",
                        "error": str(exc),
                    })
                    raise RuntimeError(
                        f"{label}: chunk {cs}→{ce} failed after "
                        f"{MAX_RETRIES} retries: {exc}"
                    ) from exc

    # Chunks are contiguous/non-overlapping by construction, but sort +
    # dedupe by timestamp defensively before writing to disk.
    seen = set()
    deduped = []
    for c in sorted(all_candles, key=lambda c: c["date"]):
        if c["date"] not in seen:
            seen.add(c["date"])
            deduped.append(c)

    return deduped, chunk_log


# ── Fetch log ────────────────────────────────────────────────────────

def write_fetch_log(
    instrument: dict,
    match_type: str,
    requested_from: date,
    requested_to: date,
    candles: list[dict],
    chunk_log: list[dict],
    lookback_days: int,
    status: str,
    error: str | None = None,
) -> None:
    log = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "instrument": {
            "instrument_token": instrument["instrument_token"],
            "tradingsymbol":    instrument.get("tradingsymbol"),
            "name":             instrument.get("name"),
            "segment":          instrument.get("segment"),
            "exchange":         instrument.get("exchange"),
            "match_type":       match_type,
        },
        "interval": INTERVAL,
        "requested_range": {
            "from": requested_from.isoformat(),
            "to":   requested_to.isoformat(),
            "lookback_days": lookback_days,
        },
        "max_days_per_request": MAX_DAYS_PER_REQUEST,
        "chunks": chunk_log,
        "summary": {
            "total_chunks":  len(chunk_log),
            "total_candles": len(candles),
            "first_candle":  candles[0]["date"] if candles else None,
            "last_candle":   candles[-1]["date"] if candles else None,
        },
        "status": status,
    }
    if error:
        log["error"] = error

    FETCH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FETCH_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="tg_int_st_pp — fetch NIFTY 50 index 5-minute candles",
    )
    parser.add_argument(
        "-c", "--config", default=str(THIS_DIR / "config.json"),
        help="Path to JSON config (api_key, api_secret, access_token). "
             "Default: config.json in this folder. Prompts interactively if missing.",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_LOOKBACK_DAYS,
        help=f"Lookback window in calendar days (default: {DEFAULT_LOOKBACK_DAYS}). "
             f"Requests are chunked at {MAX_DAYS_PER_REQUEST} days regardless of "
             f"how large this is.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Resolve the NIFTY 50 index instrument only, skip the fetch.",
    )
    args = parser.parse_args()

    if args.days < 1:
        sys.exit("--days must be >= 1")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    today = date.today()
    start_date = today - timedelta(days=args.days - 1)

    print(f"\n{'═' * 60}")
    print(f"  tg_int_st_pp — NIFTY 50 index, {INTERVAL} candles")
    print(f"  Window: {start_date} → {today}  ({args.days} day(s))")
    print(f"{'═' * 60}\n")

    # ── Kite session ────────────────────────────────────────────────
    kite = KiteConnect(api_key=config["api_key"])
    kite.set_access_token(config["access_token"])

    # ── Resolve instrument ─────────────────────────────────────────
    instrument, match_type = resolve_nifty50_index(kite)
    token = instrument["instrument_token"]
    print(f"\nResolved: {instrument.get('tradingsymbol')} "
          f"(token={token}, match={match_type})")

    if args.dry_run:
        print("\n--dry-run: skipping fetch.")
        write_fetch_log(
            instrument, match_type, start_date, today,
            [], [], args.days, status="dry_run",
        )
        print(f"Fetch log → {FETCH_LOG_FILE}")
        return

    # ── Fetch (chunked + retried + rate-limited) ───────────────────
    try:
        candles, chunk_log = fetch_candles(
            kite, token, start_date, today, INTERVAL, label="NIFTY50",
        )
    except Exception as exc:
        print(f"\n✗ Fetch failed: {exc}")
        write_fetch_log(
            instrument, match_type, start_date, today,
            [], [], args.days, status="error", error=str(exc),
        )
        sys.exit(1)

    # ── Write output ────────────────────────────────────────────────
    with open(OUTPUT_FILE, "w") as f:
        json.dump(candles, f)

    write_fetch_log(
        instrument, match_type, start_date, today,
        candles, chunk_log, args.days, status="success",
    )

    print(f"\n{'═' * 60}")
    print(f"  ✓ {len(candles)} candles fetched")
    if candles:
        print(f"    {candles[0]['date']} … {candles[-1]['date']}")
    print(f"  → {OUTPUT_FILE}")
    print(f"  → {FETCH_LOG_FILE}")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    main()
