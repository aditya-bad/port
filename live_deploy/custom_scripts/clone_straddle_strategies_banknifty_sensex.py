#!/usr/bin/env python3
"""
live_deploy — custom_scripts/clone_straddle_strategies_banknifty_sensex.py

Clones every existing NIFTY deployment of `intraday_dtt_simple`,
`intraday_dtt_advanced`, and `intraday_dtt_adjusted` into a BANKNIFTY
version and a SENSEX version each — same strategy, same params
(entry_time, force_exit_time, combined_premium_profit_pct,
adjustment_trigger_ratio/adjustment_size_pct/max_adjustments,
lots_per_trade, catch_up_late_entry, switch_to_next_week_on_expiry,
...) VERBATIM off whatever that specific NIFTY deployment is actually
running with right now — not reset to the strategy's own registered
defaults, which may well have drifted from what's really deployed.
Only `instrument_tokens`/`symbol`/`options_underlying` (and the
`deployment_name` itself) are swapped for the new underlying.

BANKNIFTY, NO SPECIAL-CASING: NSE discontinued BANKNIFTY's weekly
options a while back — it only has MONTHLY contracts listed now. None
of these 3 strategies (or `expiry_selector` generally) has any
awareness of "week" vs "month" built in anywhere at all —
`expiry_selector="THIS_WEEK"` just means "the soonest LISTED expiry"
(see `OptionsResolver._resolve_from_list`: `sorted(e for e in expiries
if e >= today)[0]`), completely mechanically. So for an underlying
whose only listed expiries are monthly, "THIS_WEEK" ALREADY,
AUTOMATICALLY, means "this month" — nothing to add, nothing to branch
on. This script verifies that's genuinely still true against REAL Kite
instrument data before creating any BANKNIFTY deployment (an up-front
live check, not an assumption) — if BANKNIFTY has no upcoming listed
expiry at all for some reason, BANKNIFTY is skipped entirely (SENSEX
still proceeds) rather than registering something that could never
place its very first entry. If that check ever needs to fire, the fix
belongs in the check's own strictness or in Kite's own data — never a
BANKNIFTY branch bolted onto the strategies themselves.

TWO PHASES, same safety convention as every other script here:

  1. DRY RUN (default) — fetches the source NIFTY deployments straight
     from the database and prints exactly what BANKNIFTY/SENSEX clones
     it WOULD create, full config included. Needs the database, NOT
     the app server or a Kite session (BANKNIFTY's inclusion is always
     previewed as "yes" here — see below for why the real answer is
     only known at --register time). Creates nothing.

  2. REGISTER (--register) — needs the app server actually running (a
     bare database insert would leave an orphaned row with no live
     runner trading it, same reasoning as every other script here) and
     a real Kite session in the database (for the BANKNIFTY viability
     check above — skipped, with a clear warning, if no session is
     available, rather than guessing). Actually creates the
     deployments.

USAGE:
    cd live_deploy
    python3 custom_scripts/clone_straddle_strategies_banknifty_sensex.py
        # dry run — prints the plan, creates nothing.

    python3 custom_scripts/clone_straddle_strategies_banknifty_sensex.py --register
        # actually creates them (needs the app server running).
"""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

LIVE_DEPLOY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIVE_DEPLOY_DIR))   # so `import app.*` works regardless of cwd

from app.config import load_config    # noqa: E402
from app.db import queries            # noqa: E402
from app.db.pool import create_pool   # noqa: E402

TARGET_STRATEGIES = ("intraday_dtt_simple", "intraday_dtt_advanced", "intraday_dtt_adjusted")

# Same well-known, stable index tokens register_supertrend_options_strategies.py
# already uses for NIFTY/SENSEX — not looked up dynamically, on purpose
# (see that script's own comment on why). BANKNIFTY's spot token/symbol
# match tokens.json + app/options/resolver.py's own INDEX_SPOT_SYMBOL
# exactly.
NEW_UNDERLYINGS = {
    "BANKNIFTY": {"instrument_token": 260105, "symbol": "NIFTY BANK", "options_underlying": "BANKNIFTY"},
    "SENSEX": {"instrument_token": 265, "symbol": "SENSEX", "options_underlying": "SENSEX"},
}


def _clone_name(source_name: str, new_underlying: str) -> str:
    """"Straddle Nifty Advanced" -> "Straddle BankNifty Advanced" / a
    "...Sensex..." twin. Case-sensitive substring replace of the
    literal "Nifty" — matches exactly what was asked ("just rename
    Nifty to BankNifty and Sensex"), not a fuzzy/case-insensitive one
    that could mangle an unrelated word elsewhere in the name. A source
    name with no "Nifty" in it at all (shouldn't happen for these 3
    strategies' own NIFTY deployments, but never assumed) falls back to
    appending " (<Underlying>)" instead of silently producing a name
    identical to the source — deployment_name is UNIQUE in the DB, so
    an unmodified duplicate would just fail to register anyway, but
    with a far less clear error than this."""
    label = "BankNifty" if new_underlying == "BANKNIFTY" else "Sensex"
    if "Nifty" in source_name:
        return source_name.replace("Nifty", label)
    return f"{source_name} ({label})"


def build_clone_config(source_config: dict, new_underlying: str) -> dict:
    """Everything from the source deployment's OWN config carries over
    VERBATIM — "same params" per the request, not reset to whatever the
    strategy's registered default_config happens to say today. Only the
    underlying-identifying keys are swapped; the strategy itself derives
    which OPTIONS EXCHANGE to use (NFO vs BFO) straight from
    options_underlying on its own (see app/options/resolver.py's
    options_exchange_for, and the Step 81 fix that made every strategy
    here do this instead of hardcoding NFO) — no separate exchange key
    needed in config at all."""
    info = NEW_UNDERLYINGS[new_underlying]
    config = dict(source_config)
    config["instrument_tokens"] = [info["instrument_token"]]
    config["symbol"] = info["symbol"]
    config["options_underlying"] = info["options_underlying"]
    return config


async def banknifty_has_a_listed_expiry(kite) -> bool:
    """The one live check this script performs. Confirms BANKNIFTY
    genuinely has at least one CE/PE contract listed on NFO with an
    expiry today-or-later, using a raw instruments() call plus the
    exact same "soonest expiry >= today" comparison
    expiry_selector="THIS_WEEK" itself boils down to (see
    OptionsResolver._resolve_from_list) — not reimplemented cleverly,
    the same two-line idea inline, since pulling in the real
    OptionsResolver here would need a live dispatcher this standalone
    script has no reason to construct just for this. If this comes
    back False, "THIS_WEEK" would raise ValueError the instant a
    BANKNIFTY deployment's own on_start tried to resolve it — catching
    that HERE means skipping cleanly instead of registering something
    that can never place its first entry."""
    rows = await asyncio.to_thread(kite.instruments, "NFO")
    today = date.today()
    for row in rows:
        if row.get("name") == "BANKNIFTY" and row.get("instrument_type") in ("CE", "PE"):
            expiry = row.get("expiry")
            if expiry and expiry >= today:
                return True
    return False


async def register_deployment(
    base_url: str, api_key: str, deployment_name: str, strategy_name: str,
    initial_capital: float, config: dict, source_name: str,
) -> None:
    import httpx   # deferred -- only --register needs this, never dry-run
    payload = {
        "deployment_name": deployment_name,
        "strategy_name": strategy_name,
        "mode": "intraday",
        "initial_capital": initial_capital,
        "config": config,
        "notes": f"Cloned by custom_scripts/clone_straddle_strategies_banknifty_sensex.py "
                 f"from {source_name!r} — same params, new underlying.",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{base_url}/deployments", headers={"X-API-Key": api_key}, json=payload)
    if r.status_code == 201:
        print(f"  OK  {deployment_name} ({strategy_name}) -> id={r.json()['id']}")
    else:
        print(f"  FAILED  {deployment_name}: HTTP {r.status_code} — {r.text}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--register", action="store_true",
                    help="Actually create the clones (needs the app server running). Default: dry run, prints only.")
    ap.add_argument("--base-url", type=str, default="http://127.0.0.1:8000",
                    help="Running app server to register against (default: http://127.0.0.1:8000)")
    args = ap.parse_args()

    cfg = load_config()
    pool = await create_pool(cfg["database_url"])   # app.db.pool's own helper, not raw asyncpg -- registers the JSONB<->dict codec `config` below needs
    try:
        all_deployments = await queries.list_deployments(pool)
        sources = [
            d for d in all_deployments
            if d["strategy_name"] in TARGET_STRATEGIES
            and (d["config"] or {}).get("options_underlying") == "NIFTY"
        ]

        if not sources:
            print(f"No NIFTY deployments found for {TARGET_STRATEGIES} — nothing to clone.")
            return 1

        print(f"Found {len(sources)} source NIFTY deployment(s) to clone:")
        for d in sources:
            print(f"  {d['deployment_name']} ({d['strategy_name']}, status={d['status']}, capital={d['initial_capital']})")

        # BANKNIFTY's inclusion is only ever DECIDED here, at --register
        # time, against a real Kite session — dry-run below always
        # previews it as included, with a note that this is the part
        # actually checked live.
        include_banknifty = True
        if args.register:
            session = await queries.get_kite_session(pool)
            if session is None or not session["access_token"]:
                print("\nNo Kite session in the database — can't verify BANKNIFTY has a "
                      "listed expiry, so BANKNIFTY clones will be SKIPPED (SENSEX still "
                      "proceeds). Log in via the app first to include BANKNIFTY too.")
                include_banknifty = False
            else:
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key=cfg["api_key"])
                kite.set_access_token(session["access_token"])
                include_banknifty = await banknifty_has_a_listed_expiry(kite)
                if include_banknifty:
                    print("\nBANKNIFTY has a listed NFO expiry >= today — THIS_WEEK "
                          "resolves fine (to that, whatever its actual real-world cadence "
                          "is), including it.")
                else:
                    print("\nBANKNIFTY has NO listed NFO CE/PE expiry >= today at all right "
                          "now — skipping BANKNIFTY clones entirely (SENSEX still proceeds).")
    finally:
        await pool.close()

    plan = []
    for d in sources:
        for underlying in ("BANKNIFTY", "SENSEX"):
            if underlying == "BANKNIFTY" and not include_banknifty:
                continue
            plan.append({
                "source_name": d["deployment_name"],
                "strategy_name": d["strategy_name"],
                "deployment_name": _clone_name(d["deployment_name"], underlying),
                "initial_capital": float(d["initial_capital"]),
                "config": build_clone_config(d["config"] or {}, underlying),
            })

    if not args.register:
        print(f"\nDRY RUN — would create {len(plan)} deployment(s) (BANKNIFTY previewed as "
              f"included; actually checked live against Kite at --register time). Re-run "
              f"with --register to create them.\n")
        for item in plan:
            print(f"  {item['deployment_name']} ({item['strategy_name']}, from {item['source_name']!r}, capital={item['initial_capital']})")
            for k, v in item["config"].items():
                print(f"      {k}: {v}")
        return 0

    print(f"\nRegistering {len(plan)} deployment(s) against {args.base_url} ...")
    for item in plan:
        await register_deployment(
            args.base_url, cfg["app_auth_secret"], item["deployment_name"],
            item["strategy_name"], item["initial_capital"], item["config"], item["source_name"],
        )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
