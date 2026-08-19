#!/usr/bin/env python3
"""
live_deploy — custom_scripts/validate_supertrend_pivots.py

Independently re-derives SuperTrend + pivot values for a pivot_supertrend*
deployment straight from Kite's REST API — using the EXACT SAME
`fetch_seed_from_kite` / `SuperTrendState` / `compute_pivots` /
`supertrend_status_fields` code the strategy and Step 87's own
GET /deployments/{id}/strategy-status endpoint both run (imported, not
reimplemented — bit-for-bit identical, no drift risk) — and compares the
result against what that live endpoint is CURRENTLY reporting.

WHY TWO CANDIDATE ANSWERS, NOT ONE: pivots' own source day depends on
whether the daily post-market checkpoint (15:45 IST — see
on_post_market_checkpoint's own docstring) has already run today for
this deployment. This script has no way to know that in advance, so it
computes BOTH:
  - "BEFORE checkpoint" (include_today_ohlc=False) — today's pivots as
    they were all day today, sourced from the most recently COMPLETED
    trading day strictly before today.
  - "AFTER checkpoint" (include_today_ohlc=True) — pivots already
    rolled forward to TOMORROW, sourced from today's own now-final
    daily candle.
Whichever one matches the live API tells you which state the
deployment is actually in (a legitimate, expected difference depending
on what time you run this). If NEITHER matches, that's a real
discrepancy worth investigating — the full logs below are meant to be
copy-pasteable as-is for exactly that.

USAGE:
    cd live_deploy
    python3 custom_scripts/validate_supertrend_pivots.py
        # validates ST_PV_NIFTY and ST_PV_SENSEX (the default pair)

    python3 custom_scripts/validate_supertrend_pivots.py --deployment "My Custom Name"
        # validate one specific deployment by its exact deployment_name
        # (repeatable: --deployment A --deployment B)

    python3 custom_scripts/validate_supertrend_pivots.py --base-url http://127.0.0.1:8000
        # point at a non-default running app server

Needs: the app server running (this hits its real GET .../strategy-status
endpoint, not the database directly, so the comparison is against
exactly what the UI itself would show), a real Kite session in the
database, and app/config.py's usual credentials.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

LIVE_DEPLOY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIVE_DEPLOY_DIR))   # so `import app.*` works regardless of cwd

from app.config import load_config    # noqa: E402
from app.db import queries            # noqa: E402
from app.db.pool import create_pool   # noqa: E402
from app.strategies.pivot_supertrend import (   # noqa: E402
    AUTOSEED_LOOKBACK_DAYS,
    compute_pivots,
    fetch_seed_from_kite,
    supertrend_from_seed_candles,
    supertrend_status_fields,
)

DEFAULT_DEPLOYMENTS = ["ST_PV_NIFTY", "ST_PV_SENSEX"]

# Two decimal places is what supertrend_status_fields itself rounds
# every numeric field to (see its own round(..., 2) calls) — so an
# exact-after-rounding comparison is the correct check here, not a
# fuzzy tolerance: if both sides really did compute from the identical
# candle set, they round to the identical number, full stop. A
# mismatch smaller than this would only ever be a display artifact, not
# a real difference — but nothing here is expected to produce one.
COMPARE_TOLERANCE = 0.01


def _fields_to_dict(fields) -> dict:
    return {f["label"]: f["value"] for f in (fields or [])}


async def compute_expected_fields(dispatcher, instrument_token: int, atr_method: str,
                                   pivot_type: str, include_today_ohlc: bool,
                                   lookback_days: int) -> tuple[list, dict]:
    """Returns (fields, debug_info) — debug_info carries every raw
    intermediate number (candle count, last candle timestamp,
    prev_day_ohlc used) so the printed log can show its own work, not
    just a final answer."""
    seed = await fetch_seed_from_kite(
        dispatcher, instrument_token, lookback_days=lookback_days,
        include_today_ohlc=include_today_ohlc,
    )
    st = supertrend_from_seed_candles(seed["seed_candles"], atr_method)
    pivots = None
    if seed["prev_day_ohlc"]:
        pivots = compute_pivots(
            seed["prev_day_ohlc"]["high"], seed["prev_day_ohlc"]["low"],
            seed["prev_day_ohlc"]["close"], pivot_type,
        )
    fields = supertrend_status_fields(st, pivots)
    debug = {
        "candle_count": len(seed["seed_candles"]),
        "last_candle_date": seed["seed_candles"][-1]["date"].isoformat() if seed["seed_candles"] else None,
        "first_candle_date": seed["seed_candles"][0]["date"].isoformat() if seed["seed_candles"] else None,
        "prev_day_ohlc": seed["prev_day_ohlc"],
        "raw_st_trend": st.trend,
        "raw_st_final_upper": round(st.final_upper, 4) if st.final_upper is not None else None,
        "raw_st_final_lower": round(st.final_lower, 4) if st.final_lower is not None else None,
        "raw_st_atr": round(st.atr, 4) if st.atr is not None else None,
    }
    return fields, debug


def _print_fields_block(title: str, fields, debug: dict) -> None:
    print(f"  --- {title} ---")
    print(f"      candles used: {debug['candle_count']} "
          f"(first={debug['first_candle_date']}, last={debug['last_candle_date']})")
    print(f"      prev_day_ohlc: {debug['prev_day_ohlc']}")
    print(f"      raw SuperTrendState: trend={debug['raw_st_trend']} "
          f"final_upper={debug['raw_st_final_upper']} final_lower={debug['raw_st_final_lower']} "
          f"atr={debug['raw_st_atr']}")
    if not fields:
        print("      fields: NONE (SuperTrend never warmed up from this candle window)")
        return
    for f in fields:
        print(f"      {f['label']}: {f['value']}")


def _compare(expected: dict, actual: dict) -> tuple[bool, list[str]]:
    """Returns (all_match, mismatch_lines). Checks every key in
    `expected` against `actual` — a key present in one but not the
    other is itself reported as a mismatch, not silently skipped."""
    mismatches = []
    all_keys = sorted(set(expected) | set(actual))
    for k in all_keys:
        ev, av = expected.get(k, "<missing>"), actual.get(k, "<missing>")
        if isinstance(ev, (int, float)) and isinstance(av, (int, float)):
            if abs(ev - av) > COMPARE_TOLERANCE:
                mismatches.append(f"    {k}: expected={ev}  actual(API)={av}  diff={abs(ev - av):.4f}")
        elif ev != av:
            mismatches.append(f"    {k}: expected={ev!r}  actual(API)={av!r}")
    return (not mismatches), mismatches


async def validate_one(pool, dispatcher, base_url: str, api_key: str, deployment_name: str,
                        lookback_days: int) -> bool:
    import httpx

    print(f"\n=== {deployment_name} ===")
    dep = await queries.get_deployment_by_name(pool, deployment_name)
    if dep is None:
        print(f"  NOT FOUND — no deployment named {deployment_name!r} in the database. "
              f"(Typo? Was it renamed or deleted?)")
        return False

    print(f"  id={dep['id']}  status={dep['status']}  strategy={dep['strategy_name']}")
    config = dep["config"] or {}
    tokens = config.get("instrument_tokens") or []
    if not tokens:
        print(f"  SKIPPED — this deployment's own config has no instrument_tokens at all: {config}")
        return False
    instrument_token = tokens[0]
    pivot_type = config.get("pivot_type", "classic")
    atr_method = config.get("atr_smoothing", "wilder")
    print(f"  instrument_token={instrument_token}  options_underlying={config.get('options_underlying')}  "
          f"pivot_type={pivot_type}  atr_smoothing={atr_method}")

    try:
        fields_before, debug_before = await compute_expected_fields(
            dispatcher, instrument_token, atr_method, pivot_type,
            include_today_ohlc=False, lookback_days=lookback_days,
        )
        fields_after, debug_after = await compute_expected_fields(
            dispatcher, instrument_token, atr_method, pivot_type,
            include_today_ohlc=True, lookback_days=lookback_days,
        )
    except Exception as e:
        print(f"  FAILED to fetch/compute from Kite: {type(e).__name__}: {e}")
        return False

    print(f"\n  Independently computed from real Kite data:")
    _print_fields_block("BEFORE today's post-market checkpoint (today's pivots, as used all day today)",
                         fields_before, debug_before)
    _print_fields_block("AFTER today's post-market checkpoint (pivots already rolled forward to tomorrow)",
                         fields_after, debug_after)

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{base_url}/deployments/{dep['id']}/strategy-status",
                              headers={"X-API-Key": api_key})
    if r.status_code != 200:
        print(f"\n  FAILED to fetch GET .../strategy-status: HTTP {r.status_code} — {r.text}")
        return False
    api_result = r.json()
    print(f"\n  Live API response (GET /deployments/{dep['id']}/strategy-status):")
    print(f"      source: {api_result.get('source')}   (\"live\" = read straight off the running "
          f"strategy instance right now; \"persisted\" = from the last checkpoint/pause/stop, "
          f"this deployment isn't currently running; \"unavailable\" = neither)")
    if not api_result.get("fields"):
        print(f"      fields: NONE — raw response: {api_result}")
        return False
    for f in api_result["fields"]:
        print(f"      {f['label']}: {f['value']}")

    expected_before = _fields_to_dict(fields_before)
    expected_after = _fields_to_dict(fields_after)
    actual = _fields_to_dict(api_result["fields"])

    ok_before, mismatches_before = _compare(expected_before, actual)
    ok_after, mismatches_after = _compare(expected_after, actual)

    print(f"\n  VERDICT:")
    if ok_before:
        print(f"    MATCH — API agrees exactly with the BEFORE-checkpoint computation "
              f"(today's own pivots, checkpoint hasn't rolled them forward yet).")
        return True
    if ok_after:
        print(f"    MATCH — API agrees exactly with the AFTER-checkpoint computation "
              f"(pivots already rolled forward to tomorrow).")
        return True

    print(f"    MISMATCH — API matches NEITHER computed candidate. This is worth investigating.")
    print(f"    Diff vs BEFORE-checkpoint:")
    for line in mismatches_before:
        print(line)
    print(f"    Diff vs AFTER-checkpoint:")
    for line in mismatches_after:
        print(line)
    return False


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deployment", action="append", dest="deployments", default=None,
                    help=f"Deployment name to validate (repeatable). Default: {DEFAULT_DEPLOYMENTS}")
    ap.add_argument("--base-url", type=str, default="http://127.0.0.1:8000",
                    help="Running app server to check against (default: http://127.0.0.1:8000)")
    ap.add_argument("--lookback-days", type=int, default=AUTOSEED_LOOKBACK_DAYS,
                    help=f"Calendar days of 5-min candles to replay (default: {AUTOSEED_LOOKBACK_DAYS}, "
                         f"same as the strategy's own self-seed)")
    args = ap.parse_args()
    deployment_names = args.deployments or DEFAULT_DEPLOYMENTS

    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        session = await queries.get_kite_session(pool)
        if session is None or not session["access_token"]:
            print("No Kite session found in the database — log in via the app first "
                  "(GET /kite/login-url -> complete the OAuth flow), then re-run this.")
            return 1
        dispatcher = SimpleNamespace(api_key=cfg["api_key"], access_token=session["access_token"])

        all_ok = True
        for name in deployment_names:
            ok = await validate_one(pool, dispatcher, args.base_url, cfg["app_auth_secret"], name, args.lookback_days)
            all_ok = all_ok and ok
    finally:
        await pool.close()

    print(f"\n{'ALL MATCHED' if all_ok else 'AT LEAST ONE MISMATCH — see above'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
