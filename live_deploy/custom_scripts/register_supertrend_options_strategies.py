#!/usr/bin/env python3
"""
live_deploy — custom_scripts/register_supertrend_options_strategies.py

Registers 4 real deployments — pivot_supertrend_options and
pivot_supertrend_options_inverse, each for NIFTY and SENSEX — seeded
with today's real 5-min candles + daily OHLC fetched straight from
Kite, so pivots/SuperTrend are correct from the very first tick
tomorrow instead of needing a full cold-start day to warm up.

TWO PHASES, deliberately separate:

  1. FETCH + VALIDATE (the default, no flags needed) — standalone,
     talks directly to Kite's REST API + this app's own database (via
     app/config.py + app/db/queries.py, exactly like
     clean_deployment_names.py) to pull today's data, computes
     SuperTrend(7,3) through the EXACT SAME code the strategy itself
     runs (imported directly from app/strategies/pivot_supertrend.py,
     not reimplemented — bit-for-bit identical, no drift risk), and
     checks it against the last SuperTrend value you read off your own
     chart. Writes everything fetched to a JSON file. Does NOT need
     the app server running, and does NOT create anything.

  2. REGISTER (only with --register) — needs the app server actually
     running (real deployments only start trading once a real
     DeploymentManager picks them up — a bare database insert would
     leave an orphaned row with no live runner, which a standalone
     script has no way to start on its own). Posts the 4 deployments
     to that running server's real API, so registering one is
     indistinguishable from doing it through the UI.

USAGE:
    cd live_deploy
    python3 custom_scripts/register_supertrend_options_strategies.py
        # phase 1 only: fetch, validate, save JSON, print a report.
        # Nothing is created. Safe to run as many times as you want.

    python3 custom_scripts/register_supertrend_options_strategies.py --register
        # phase 1, THEN (only if validation passed) phase 2: actually
        # creates all 4 deployments against --base-url (default
        # http://127.0.0.1:8000).

    python3 custom_scripts/register_supertrend_options_strategies.py --register --force
        # register even if SuperTrend validation was flagged as a
        # mismatch -- use only after you've looked at the flagged
        # numbers yourself and decided it's fine.

    python3 custom_scripts/register_supertrend_options_strategies.py --date 2026-08-18
        # override "today" (defaults to the real current IST date) --
        # useful if you don't run this the same evening you read the
        # chart values off.

    python3 custom_scripts/register_supertrend_options_strategies.py \\
        --nifty-st 24200.27 --sensex-st 77429.01
        # override the chart reference values (defaults below are
        # exactly what was read off the (7,3) chart in chat).

WHY TODAY'S data, not yesterday's -- the one thing explicitly flagged
for double-checking: prev_day_ohlc means "the most recently completed
trading day's H/L/C", and seed_candles means "recent real 5-min
candles to warm SuperTrend up from". By the time these deployments
actually start reacting to ticks TOMORROW, TODAY is that most-recently-
completed session (the market is closed right now) -- so both fields
correctly come from today's data, not yesterday's. Confirmed directly
against the strategy's own code, not assumed: pivot_supertrend.py's
own _roll_over_day() sets prev_day_ohlc from whatever the CURRENTLY
just-finished trading day looked like, the moment the day rolls over --
today's close is exactly that roll-over's source data from tomorrow's
point of view.
"""

import argparse
import asyncio
import json
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
DATA_DIR = Path(__file__).resolve().parent / "data"

# The two underlyings this registers, both ways round (options + the
# inverse). Instrument tokens match tokens.json (this app's own static
# subscription list) exactly -- these are NOT looked up dynamically,
# on purpose: they're well-known, stable NSE/BSE index tokens, and a
# script meant to run standalone shouldn't need a live instruments
# dump just to know NIFTY 50 is 256265.
UNDERLYINGS = {
    "NIFTY": {
        "instrument_token": 256265,
        "symbol": "NIFTY 50",
        "options_underlying": "NIFTY",
        "name_suffix": "NIFTY",
    },
    "SENSEX": {
        "instrument_token": 265,
        "symbol": "SENSEX",
        "options_underlying": "SENSEX",
        "name_suffix": "SENSEX",
    },
}

# Deployment name + initial capital for each of the 4 -- exactly what
# was confirmed in chat.
DEPLOYMENTS = [
    {"underlying": "NIFTY", "strategy": "pivot_supertrend_options", "deployment_name": "ST_PV_NIFTY", "initial_capital": 200000},
    {"underlying": "SENSEX", "strategy": "pivot_supertrend_options", "deployment_name": "ST_PV_SENSEX", "initial_capital": 200000},
    {"underlying": "NIFTY", "strategy": "pivot_supertrend_options_inverse", "deployment_name": "ST_PV_INV_NIFTY", "initial_capital": 50000},
    {"underlying": "SENSEX", "strategy": "pivot_supertrend_options_inverse", "deployment_name": "ST_PV_INV_SENSEX", "initial_capital": 50000},
]

# Chart reference values from chat -- "Last supertrend value according
# to Charts (7,3)". Overridable via --nifty-st/--sensex-st for a
# future re-run with fresh numbers.
DEFAULT_CHART_ST = {"NIFTY": 24200.27, "SENSEX": 77429.01}

# How close the computed SuperTrend value has to land to your chart
# reading to count as a match, in index points -- charts/data vendors
# can differ by a few points on the same real candle even with
# identical math (a slightly different tick included right at a 5-min
# boundary, etc.), so this isn't "must be bit-for-bit equal to the
# chart", just "close enough that this is clearly the same real
# SuperTrend line, not a bug". Override with --tolerance.
DEFAULT_TOLERANCE_POINTS = 5.0


def _fmt_kite_date(dt: datetime) -> str:
    """Kite's historical_data wants 'YYYY-MM-DD HH:MM:SS' (or just
    'YYYY-MM-DD' for interval='day')."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _candle_to_seed(c: dict) -> dict:
    """Kite's own historical_data row -> this strategy family's
    seed_candles shape (see pivot_supertrend.py's own docstring):
    {"date": "YYYY-MM-DD HH:MM:SS", "open", "high", "low", "close"}.
    Drops volume/oi -- unused by SuperTrendState.update(). Kite's
    `date` comes back as an aware IST datetime; formatted naive here to
    match exactly what _parse_dt (pivot_supertrend.py) expects, same
    convention every live tick's own exchange_timestamp already uses
    throughout this app."""
    d = c["date"]
    if d.tzinfo is not None:
        d = d.astimezone(_IST).replace(tzinfo=None)
    return {
        "date": _fmt_kite_date(d),
        "open": float(c["open"]), "high": float(c["high"]),
        "low": float(c["low"]), "close": float(c["close"]),
    }


def compute_supertrend_line(seed_candles: list[dict]) -> dict:
    """Feeds `seed_candles` through the REAL SuperTrendState the
    strategy itself uses (imported, not reimplemented) and returns the
    single number a chart would actually be showing as "the SuperTrend
    value" -- the currently ACTIVE band (final_lower while trend is
    up, final_lower being the line price rides above; final_upper
    while trend is down, the line price rides below), not both bands
    at once, matching what one number on a real chart means."""
    st = SuperTrendState(period=ST_PERIOD, multiplier=ST_MULTIPLIER, atr_method="wilder")
    for c in seed_candles:
        st.update({"high": c["high"], "low": c["low"], "close": c["close"]})
    if st.trend is None:
        return {"ready": False, "trend": None, "value": None, "atr": None}
    value = st.final_lower if st.trend == "up" else st.final_upper
    return {
        "ready": True, "trend": st.trend, "value": round(value, 2),
        "atr": round(st.atr, 2), "final_upper": round(st.final_upper, 2),
        "final_lower": round(st.final_lower, 2),
    }


async def fetch_one_underlying(kite: KiteConnect, name: str, info: dict, trade_date) -> dict:
    """Real Kite REST calls -- today's daily OHLC (-> prev_day_ohlc)
    and today's 5-min candles (-> seed_candles), for one underlying.
    Raises on any Kite API error (bad/expired session, no data for a
    non-trading day, etc.) -- this is exactly the "validate it" step
    from the request, not something to silently paper over."""
    token = info["instrument_token"]
    day_start = datetime.combine(trade_date, dtime(0, 0))
    day_end = datetime.combine(trade_date, dtime(23, 59, 59))

    daily = await asyncio.to_thread(
        kite.historical_data, token, day_start, day_end, "day",
    )
    if not daily:
        raise RuntimeError(
            f"{name}: Kite returned no daily candle for {trade_date} — is this "
            f"actually a trading day? (weekend/market holiday would explain it; "
            f"pass --date to point at the correct most-recent trading day instead.)"
        )
    day_row = daily[0]
    prev_day_ohlc = {
        "high": round(float(day_row["high"]), 2),
        "low": round(float(day_row["low"]), 2),
        "close": round(float(day_row["close"]), 2),
    }

    session_start = datetime.combine(trade_date, dtime(9, 15))
    session_end = datetime.combine(trade_date, dtime(15, 30))
    raw_candles = await asyncio.to_thread(
        kite.historical_data, token, session_start, session_end, "5minute",
    )
    if len(raw_candles) < ST_PERIOD:
        raise RuntimeError(
            f"{name}: only {len(raw_candles)} five-minute candle(s) came back for "
            f"{trade_date}, fewer than SuperTrend's own ATR period ({ST_PERIOD}) "
            f"needs to warm up at all — not enough real data for a valid seed."
        )
    seed_candles = [_candle_to_seed(c) for c in raw_candles]

    return {
        "underlying": name,
        "instrument_token": token,
        "trade_date": trade_date.isoformat(),
        "prev_day_ohlc": prev_day_ohlc,
        "seed_candles": seed_candles,
        "candle_count": len(seed_candles),
    }


def validate_against_chart(name: str, computed: dict, chart_value: float, tolerance: float) -> bool:
    """Prints a clear PASS/FLAGGED line and returns whether it passed.
    Never raises -- a flagged mismatch is reported, not a crash, so
    the rest of the fetch/report still completes and gets saved."""
    if not computed["ready"]:
        print(f"  [{name}] FLAGGED: SuperTrend never warmed up from today's candles "
              f"alone ({computed})")
        return False
    diff = abs(computed["value"] - chart_value)
    ok = diff <= tolerance
    status = "MATCH" if ok else "FLAGGED — MISMATCH"
    print(f"  [{name}] computed={computed['value']} (trend={computed['trend']}, "
          f"atr={computed['atr']})  vs  chart={chart_value}  ->  diff={diff:.2f}  {status}")
    return ok


async def register_deployment(base_url: str, api_key: str, spec: dict, config: dict, trade_date) -> None:
    import httpx
    payload = {
        "deployment_name": spec["deployment_name"],
        "strategy_name": spec["strategy"],
        "mode": "intraday",
        "initial_capital": spec["initial_capital"],
        "config": config,
        "notes": f"Registered by custom_scripts/register_supertrend_options_strategies.py "
                 f"— seeded from real Kite data on {trade_date}.",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{base_url}/deployments", headers={"X-API-Key": api_key}, json=payload)
    if r.status_code == 201:
        print(f"  OK  {spec['deployment_name']} ({spec['strategy']}) -> id={r.json()['id']}")
    else:
        print(f"  FAILED  {spec['deployment_name']}: HTTP {r.status_code} — {r.text}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", type=str, default=None,
                    help="Trading date to seed from, YYYY-MM-DD (default: today, IST)")
    ap.add_argument("--nifty-st", type=float, default=DEFAULT_CHART_ST["NIFTY"],
                    help=f"Chart's last SuperTrend(7,3) value for NIFTY (default: {DEFAULT_CHART_ST['NIFTY']})")
    ap.add_argument("--sensex-st", type=float, default=DEFAULT_CHART_ST["SENSEX"],
                    help=f"Chart's last SuperTrend(7,3) value for SENSEX (default: {DEFAULT_CHART_ST['SENSEX']})")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_POINTS,
                    help=f"Max allowed diff (index points) before flagging a mismatch (default: {DEFAULT_TOLERANCE_POINTS})")
    ap.add_argument("--register", action="store_true",
                    help="Also create the 4 deployments (needs the app server running). Default: fetch+validate+save only.")
    ap.add_argument("--force", action="store_true",
                    help="Register even if validation was flagged. Ignored without --register.")
    ap.add_argument("--base-url", type=str, default="http://127.0.0.1:8000",
                    help="Running app server to register against (default: http://127.0.0.1:8000)")
    args = ap.parse_args()

    cfg = load_config()
    trade_date = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date \
        else datetime.now(_IST).date()

    pool = await asyncpg.create_pool(cfg["database_url"])
    try:
        session = await queries.get_kite_session(pool)
    finally:
        await pool.close()
    if session is None or not session["access_token"]:
        print("No Kite session found in the database — log in via the app first "
              "(GET /kite/login-url -> complete the OAuth flow), then re-run this.")
        return 1

    kite = KiteConnect(api_key=cfg["api_key"])
    kite.set_access_token(session["access_token"])

    print(f"Fetching real Kite data for {trade_date} (today's session, per the "
          f"module docstring's own reasoning for why today and not yesterday)...")
    chart_values = {"NIFTY": args.nifty_st, "SENSEX": args.sensex_st}
    fetched: dict[str, dict] = {}
    for name, info in UNDERLYINGS.items():
        try:
            fetched[name] = await fetch_one_underlying(kite, name, info, trade_date)
        except Exception as e:
            print(f"  [{name}] FAILED to fetch: {e}")
            return 1
        print(f"  [{name}] {fetched[name]['candle_count']} five-minute candle(s), "
              f"prev_day_ohlc={fetched[name]['prev_day_ohlc']}")

    print("\nValidating computed SuperTrend(7,3) against your chart reading "
          f"(tolerance ±{args.tolerance} points):")
    all_ok = True
    for name in UNDERLYINGS:
        computed = compute_supertrend_line(fetched[name]["seed_candles"])
        fetched[name]["computed_supertrend"] = computed
        ok = validate_against_chart(name, computed, chart_values[name], args.tolerance)
        all_ok = all_ok and ok

    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"supertrend_seed_{trade_date.isoformat()}.json"
    out_path.write_text(json.dumps({
        "trade_date": trade_date.isoformat(),
        "chart_values": chart_values,
        "tolerance": args.tolerance,
        "validated": all_ok,
        "fetched": fetched,
    }, indent=2, default=str))
    print(f"\nSaved all fetched data + computed results -> {out_path}")

    if not all_ok:
        print("\n*** VALIDATION FLAGGED A MISMATCH — see above. ***")
        if not args.register:
            print("(Nothing was registered — this was a fetch+validate-only run anyway.)")
            return 0
        if not args.force:
            print("Refusing to --register with a flagged mismatch. Re-run with --force "
                  "once you've reviewed the numbers above and are OK proceeding.")
            return 2
        print("--force given: proceeding to register despite the flagged mismatch.")

    if not args.register:
        print("\nFetch+validate complete. Re-run with --register to actually create "
              "the 4 deployments (needs the app server running).")
        return 0

    print(f"\nRegistering 4 deployments against {args.base_url} ...")
    for spec in DEPLOYMENTS:
        underlying = UNDERLYINGS[spec["underlying"]]
        seed = fetched[spec["underlying"]]
        config = {
            "instrument_tokens": [underlying["instrument_token"]],
            "symbol": underlying["symbol"],
            "options_underlying": underlying["options_underlying"],
            "expiry_selector": "THIS_WEEK",
            "atr_smoothing": "wilder",
            "force_exit_time": "15:00",
            "market_open_time": "09:15",
            "lots_per_trade": 1,
            "seed_candles": seed["seed_candles"],
            "supertrend_seed": None,
        }
        if spec["strategy"] == "pivot_supertrend_options":
            config["prev_day_ohlc"] = seed["prev_day_ohlc"]
            config["pivot_type"] = "classic"
        else:   # pivot_supertrend_options_inverse -- no prev_day_ohlc/pivot_type key at all
            config["hold_candles"] = 1
        await register_deployment(args.base_url, cfg["app_auth_secret"], spec, config, trade_date)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
