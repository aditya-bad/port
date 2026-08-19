#!/usr/bin/env python3
"""
live_deploy — custom_scripts/resync_supertrend_state.py

Corrects a live deployment's persisted SuperTrend(7,3) state after it has
drifted away from reality — the specific real incident this was written
for: CandleAggregator has no gap detection (before this same session
added it — see pivot_supertrend.py's own comment on this), so a
WebSocket reconnect (LiveDataDispatcher.reconnect_count) can silently
skip one or more 5-min candles. SuperTrend is RECURSIVE (each candle's
bands depend on the previous candle's), so one skipped candle doesn't
just leave a small gap — it permanently shifts every subsequent
ATR/band value away from what a continuous data feed (a real chart,
or a fresh Kite REST fetch) would show. ST_PV_NIFTY was found reading
`trend=up` in its live persisted state while its own reference chart
showed a clearly bearish SuperTrend — this is how.

WHAT THIS DOES: fetches gap-free 5-min candles straight from Kite's REST
historical_data API (the same authoritative source a real chart is built
from, NOT the WebSocket tick stream this app's own aggregator uses) for
the last `--lookback-days` calendar days through right now, replays them
through a completely fresh SuperTrendState (imported from
pivot_supertrend.py, never reimplemented — see the sibling
register_supertrend_options_strategies.py's own reasoning for why that
matters), and overwrites ONLY the `supertrend`/`prev_trend` fields of the
deployment's persisted state — `pivots`/`prev_day_ohlc` are left exactly
as they already are (those come from a single daily OHLC read, not the
tick stream, so they're never exposed to this failure mode at all).

WHAT THIS DELIBERATELY DOES NOT DO: place any trade, touch
open_positions, or affect a CURRENTLY RUNNING deployment's in-memory
state. Writing to the deployment_state table only changes what the NEXT
on_start() will load — a deployment that's currently ACTIVE keeps
running on its already-drifted in-memory SuperTrendState until it is
next paused+resumed (or the whole app is redeployed). This script prints
that reminder explicitly every run; it does not pause/resume anything
for you.

USAGE:
    cd live_deploy
    python3 custom_scripts/resync_supertrend_state.py
        # resyncs all 4 of the standard ST_PV_* deployments (whichever
        # of them actually exist right now — a missing one is skipped
        # with a note, not an error).

    python3 custom_scripts/resync_supertrend_state.py --deployment-name ST_PV_NIFTY
        # just one.

    python3 custom_scripts/resync_supertrend_state.py --dry-run
        # fetch + compute + print the before/after comparison, write nothing.

    python3 custom_scripts/resync_supertrend_state.py --lookback-days 14
        # fetch a longer window before now (default 7 calendar days --
        # comfortably several real trading days even across a long
        # weekend; ATR(7)'s Wilder smoothing converges to the true
        # trajectory well within that, so this is about safety margin,
        # not precision -- there's no real downside to more).
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

LIVE_DEPLOY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIVE_DEPLOY_DIR))   # so `import app.*` works regardless of cwd

from app.config import load_config                              # noqa: E402
from app.db import queries                                       # noqa: E402
from app.strategies.pivot_supertrend import (                    # noqa: E402
    ST_MULTIPLIER, ST_PERIOD, SuperTrendState,
)

import asyncpg                                                    # noqa: E402
from kiteconnect import KiteConnect                               # noqa: E402

_IST = ZoneInfo("Asia/Kolkata")

# Same 4 deployment names + underlyings as
# register_supertrend_options_strategies.py (which created them) -- kept
# as an independent literal list rather than importing that script's
# DEPLOYMENTS constant, since this one only needs name+token, not the
# full registration spec, and standing scripts in this folder don't
# import from one another (each is meant to run standalone -- see
# custom_scripts/README.md).
DEFAULT_DEPLOYMENT_NAMES = ["ST_PV_NIFTY", "ST_PV_SENSEX", "ST_PV_INV_NIFTY", "ST_PV_INV_SENSEX"]

INSTRUMENT_TOKEN_FOR_UNDERLYING = {"NIFTY": 256265, "SENSEX": 265}

DEFAULT_LOOKBACK_DAYS = 7


def _fmt_kite_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _underlying_from_config(config: dict) -> str:
    """Every ST_PV_* deployment's config carries options_underlying
    ("NIFTY" or "SENSEX") -- see pivot_supertrend_options[.py's
    module docstring's CONFIG section. Falls back to instrument_tokens
    if options_underlying is somehow absent (shouldn't happen for any
    deployment this script targets, but fail loud rather than guess
    wrong)."""
    underlying = config.get("options_underlying")
    if underlying:
        return underlying.strip().upper()
    tokens = config.get("instrument_tokens") or []
    for name, token in INSTRUMENT_TOKEN_FOR_UNDERLYING.items():
        if token in tokens:
            return name
    raise ValueError(f"Could not determine underlying from config: {config!r}")


def compute_fresh_supertrend(candles: list[dict]) -> SuperTrendState:
    """Replay a full, gap-free candle sequence through a brand new
    SuperTrendState -- returns the state object itself (not just a
    summary dict, unlike register_supertrend_options_strategies.py's
    compute_supertrend_line) since this needs the full snapshot()/trend
    to write back into deployment_state, not just a display value."""
    st = SuperTrendState(period=ST_PERIOD, multiplier=ST_MULTIPLIER, atr_method="wilder")
    for c in candles:
        st.update({"high": c["high"], "low": c["low"], "close": c["close"]})
    return st


async def resync_one(pool, kite: KiteConnect, name: str, lookback_days: int, dry_run: bool) -> bool:
    """Returns True if this deployment was found and processed (whether
    or not anything was actually written), False if it doesn't exist."""
    row = await queries.get_deployment_by_name(pool, name)
    if row is None:
        print(f"  [{name}] SKIPPED — no deployment with this name exists")
        return False

    strategy_name = row["strategy_name"]
    if strategy_name not in ("pivot_supertrend", "pivot_supertrend_options", "pivot_supertrend_options_inverse"):
        print(f"  [{name}] SKIPPED — strategy_name={strategy_name!r} isn't one this "
              f"script knows how to resync")
        return False

    config = row["config"] or {}
    underlying = _underlying_from_config(config)
    token = INSTRUMENT_TOKEN_FOR_UNDERLYING.get(underlying)
    if token is None:
        print(f"  [{name}] SKIPPED — unknown underlying {underlying!r}, no instrument_token mapping")
        return False

    old_state = await queries.load_deployment_state(pool, row["id"])
    old_trend = old_state.get("prev_trend") if old_state else None
    old_snapshot = old_state.get("supertrend") if old_state else None
    old_value = None
    if old_snapshot and old_trend:
        old_value = old_snapshot.get("final_lower") if old_trend == "up" else old_snapshot.get("final_upper")

    now = datetime.now(_IST).replace(tzinfo=None)
    start = datetime.combine((now - timedelta(days=lookback_days)).date(), dtime(0, 0))
    raw_candles = await asyncio.to_thread(kite.historical_data, token, start, now, "5minute")
    if len(raw_candles) < ST_PERIOD:
        print(f"  [{name}] SKIPPED — only {len(raw_candles)} candle(s) came back for "
              f"the last {lookback_days} day(s), fewer than SuperTrend needs to warm up")
        return False

    candles = []
    for c in raw_candles:
        d = c["date"]
        if d.tzinfo is not None:
            d = d.astimezone(_IST).replace(tzinfo=None)
        candles.append({"date": d, "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])})

    st = compute_fresh_supertrend(candles)
    if st.trend is None:
        print(f"  [{name}] SKIPPED — fresh replay never warmed up (shouldn't happen "
              f"with {len(candles)} candles — investigate before trusting this deployment)")
        return False

    new_value = st.final_lower if st.trend == "up" else st.final_upper
    today = now.date()
    today_candles = [c for c in candles if c["date"].date() == today]
    today_high = max((c["high"] for c in today_candles), default=None)
    today_low = min((c["low"] for c in today_candles), default=None)
    today_last_close = today_candles[-1]["close"] if today_candles else None

    changed = old_trend != st.trend
    marker = "CHANGED" if changed else "unchanged"
    print(f"  [{name}] {underlying}: replayed {len(candles)} gap-free candle(s) "
          f"({start.date()} -> {now.strftime('%Y-%m-%d %H:%M')})")
    print(f"           before: trend={old_trend!r} value={old_value}")
    print(f"           after:  trend={st.trend!r} value={round(new_value, 2)}  <- {marker}")

    if dry_run:
        print(f"           (--dry-run: nothing written)")
        return True

    new_state = {
        "version": 1,
        "supertrend": st.snapshot(),
        "prev_trend": st.trend,
        "pending_exit": None,   # deliberately reset, not carried forward -- see module docstring
        "pending_entry": None,
    }
    if strategy_name != "pivot_supertrend_options_inverse":
        # pivot_supertrend / pivot_supertrend_options carry pivots +
        # prev_day_ohlc + today's running high/low/close too -- the
        # inverse strategy's own get_persistable_state() never has these
        # keys at all (see its module docstring: no pivot levels used).
        new_state["prev_day_ohlc"] = old_state.get("prev_day_ohlc") if old_state else None
        new_state["pivots"] = old_state.get("pivots") if old_state else None
        new_state["today"] = today.isoformat()
        new_state["today_high"] = today_high
        new_state["today_low"] = today_low
        new_state["today_last_close"] = today_last_close

    await queries.save_deployment_state(pool, row["id"], new_state)
    print(f"           WRITTEN to deployment_state.")
    if row["status"] == "active":
        print(f"           *** {name} is currently ACTIVE — this write does NOT "
              f"touch its running in-memory state. Pause then Resume it (or "
              f"redeploy) for the corrected trend to actually take effect. ***")
    return True


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deployment-name", action="append", default=None,
                    help="Resync just this deployment (repeatable). Default: all of "
                         f"{DEFAULT_DEPLOYMENT_NAMES} that currently exist.")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                    help=f"Calendar days of 5-min candles to replay through now (default: {DEFAULT_LOOKBACK_DAYS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and print the before/after comparison; write nothing.")
    args = ap.parse_args()

    names = args.deployment_name or DEFAULT_DEPLOYMENT_NAMES

    cfg = load_config()
    pool = await asyncpg.create_pool(cfg["database_url"])
    try:
        session = await queries.get_kite_session(pool)
        if session is None or not session["access_token"]:
            print("No Kite session found in the database — log in via the app first, then re-run this.")
            return 1

        kite = KiteConnect(api_key=cfg["api_key"])
        kite.set_access_token(session["access_token"])

        print(f"Resyncing SuperTrend state for: {', '.join(names)}"
              f"{' (dry run)' if args.dry_run else ''}\n")
        any_found = False
        for name in names:
            found = await resync_one(pool, kite, name, args.lookback_days, args.dry_run)
            any_found = any_found or found
        if not any_found:
            print("\nNothing was resynced — none of the given deployment name(s) exist.")
            return 1
    finally:
        await pool.close()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
