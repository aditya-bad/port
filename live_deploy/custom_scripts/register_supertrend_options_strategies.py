#!/usr/bin/env python3
"""
live_deploy — custom_scripts/register_supertrend_options_strategies.py

Registers 4 real deployments — pivot_supertrend_options and
pivot_supertrend_options_inverse, each for NIFTY and SENSEX.

SIMPLIFIED (this version): this script used to run a "phase 1" —
fetching today's real candles from Kite, computing SuperTrend(7,3)
through the strategy's own code, and validating it against a chart
reading you provided — BEFORE registering anything. That made sense
back when it was ALSO the seed source for the 4 deployments it
created. It stopped being the seed source the moment every
pivot_supertrend* strategy learned to self-seed live from Kite's own
REST API the instant its own `on_start` runs (see
app/strategies/pivot_supertrend.py's `fetch_seed_from_kite` /
`StrategyBase.on_post_market_checkpoint`) — at that point phase 1
turned into a pre-flight sanity check with nothing left to feed, and
now it's gone entirely: a standalone script re-deriving "is my Kite
session sane" the same way the deployment's own `on_start` is about to
anyway is duplicate work that only adds a second place to fail (a
non-trading day, a stale chart reading, a wrong `--date`) for zero
benefit — if the seed IS bad, `on_start` finds out live and logs it,
same as it always has for every OTHER strategy that doesn't get a
pre-flight check at all. This script's only job now is REGISTRATION:
build the 4 deployments' config (no seed keys in it at all — nothing
for this script to fetch or hand over) and POST them to a running app
server.

Needs the app server actually running — real deployments only start
trading once a real DeploymentManager picks them up; a bare database
insert would leave an orphaned row with no live runner, which a
standalone script has no way to start on its own. Each deployment's
own `on_start` does its own live Kite fetch the moment it starts
trading — nothing from this script is passed into it.

USAGE:
    cd live_deploy
    python3 custom_scripts/register_supertrend_options_strategies.py
        # DRY RUN (default, same safety convention as every other
        # script here): prints the 4 deployments' exact config, creates
        # nothing. Needs nothing but app/config.py -- no Kite session,
        # no running server, no database at all.

    python3 custom_scripts/register_supertrend_options_strategies.py --register
        # Actually creates all 4 deployments against --base-url
        # (default http://127.0.0.1:8000) -- needs that server running
        # and reachable, and its own app_auth_secret (from config.json /
        # APP_AUTH_SECRET, same as any other authenticated request).
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LIVE_DEPLOY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIVE_DEPLOY_DIR))   # so `import app.*` works regardless of cwd

from app.config import load_config   # noqa: E402

import httpx   # noqa: E402

_IST = ZoneInfo("Asia/Kolkata")

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
    },
    "SENSEX": {
        "instrument_token": 265,
        "symbol": "SENSEX",
        "options_underlying": "SENSEX",
    },
}

# Deployment name + initial capital for each of the 4 -- exactly what
# was confirmed in chat. The inverse pair also gets "exclude_from_reports"
# -- POST /deployments' own DeploymentCreate schema has no
# include_in_reports field at all (only PATCH /deployments/{id}'s
# DeploymentUpdate does — see that schema's own docstring: it's
# deliberately editable "regardless of status," no create-time
# equivalent exists), so this is applied as an immediate follow-up
# PATCH right after each inverse deployment registers, not a field in
# the POST body itself. See register_deployment() below.
DEPLOYMENTS = [
    {"underlying": "NIFTY", "strategy": "pivot_supertrend_options", "deployment_name": "ST_PV_NIFTY", "initial_capital": 250000},
    {"underlying": "SENSEX", "strategy": "pivot_supertrend_options", "deployment_name": "ST_PV_SENSEX", "initial_capital": 250000},
    {"underlying": "NIFTY", "strategy": "pivot_supertrend_options_inverse", "deployment_name": "ST_PV_INV_NIFTY", "initial_capital": 100000, "exclude_from_reports": True},
    {"underlying": "SENSEX", "strategy": "pivot_supertrend_options_inverse", "deployment_name": "ST_PV_INV_SENSEX", "initial_capital": 100000, "exclude_from_reports": True},
]


def build_config(spec: dict) -> dict:
    """No seed_candles/prev_day_ohlc/supertrend_seed here at all —
    every pivot_supertrend* strategy self-seeds live from Kite the
    moment its own on_start runs (see this module's own docstring).
    Both strategies' full config contracts are read straight off their
    own `cfg.get(...)` calls in app/strategies/pivot_supertrend_options*
    .py, not guessed — every key either of them actually reads is set
    here explicitly, and nothing else."""
    underlying = UNDERLYINGS[spec["underlying"]]
    config = {
        "instrument_tokens": [underlying["instrument_token"]],
        "symbol": underlying["symbol"],
        "options_underlying": underlying["options_underlying"],
        "expiry_selector": "THIS_WEEK",
        "atr_smoothing": "wilder",
        "force_exit_time": "15:00",
        "market_open_time": "09:15",
        "lots_per_trade": 1,
    }
    if spec["strategy"] == "pivot_supertrend_options":
        config["pivot_type"] = "classic"
    else:   # pivot_supertrend_options_inverse -- no pivot_type key at all
        config["hold_candles"] = 1
    return config


async def register_deployment(base_url: str, api_key: str, spec: dict, config: dict) -> None:
    today = datetime.now(_IST).date().isoformat()
    payload = {
        "deployment_name": spec["deployment_name"],
        "strategy_name": spec["strategy"],
        "mode": "intraday",
        "initial_capital": spec["initial_capital"],
        "config": config,
        "notes": f"Registered by custom_scripts/register_supertrend_options_strategies.py "
                 f"on {today} — self-seeds live from Kite on its own on_start, "
                 f"no static seed passed in config.",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{base_url}/deployments", headers={"X-API-Key": api_key}, json=payload)
        if r.status_code != 201:
            print(f"  FAILED  {spec['deployment_name']}: HTTP {r.status_code} — {r.text}")
            return
        new_id = r.json()["id"]
        print(f"  OK  {spec['deployment_name']} ({spec['strategy']}) -> id={new_id}")

        if spec.get("exclude_from_reports"):
            # PATCH, not part of the POST body above -- see DEPLOYMENTS'
            # own comment for why (DeploymentCreate has no
            # include_in_reports field at all). Editable "regardless of
            # status" per DeploymentUpdate's own docstring, so this is
            # safe to fire immediately, no pause needed first.
            pr = await client.patch(
                f"{base_url}/deployments/{new_id}", headers={"X-API-Key": api_key},
                json={"include_in_reports": False},
            )
            if pr.status_code == 200:
                print(f"      excluded from reports")
            else:
                print(f"      FAILED to exclude from reports: HTTP {pr.status_code} — {pr.text}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--register", action="store_true",
                    help="Actually create the 4 deployments (needs the app server running). Default: print the config and create nothing.")
    ap.add_argument("--base-url", type=str, default="http://127.0.0.1:8000",
                    help="Running app server to register against (default: http://127.0.0.1:8000)")
    args = ap.parse_args()

    if not args.register:
        # No load_config() call at all in this branch, deliberately —
        # dry-run previews pure in-script data (UNDERLYINGS/DEPLOYMENTS/
        # build_config), nothing that actually needs config.json/env
        # vars to exist yet. Only --register needs app_auth_secret,
        # loaded below, right where it's first actually used.
        print("DRY RUN — nothing will be created. Re-run with --register to actually "
              "create these deployments.\n")
        for spec in DEPLOYMENTS:
            config = build_config(spec)
            tag = " [excluded from reports]" if spec.get("exclude_from_reports") else ""
            print(f"  {spec['deployment_name']} ({spec['strategy']}, capital={spec['initial_capital']}){tag}")
            for k, v in config.items():
                print(f"      {k}: {v}")
        return 0

    cfg = load_config()
    print(f"Registering 4 deployments against {args.base_url} ...")
    for spec in DEPLOYMENTS:
        config = build_config(spec)
        await register_deployment(args.base_url, cfg["app_auth_secret"], spec, config)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
