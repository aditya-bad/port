#!/usr/bin/env python3
"""
live_deploy — custom_scripts/remove_todays_trades.py

"Undo today entirely, everywhere" — deletes every trade (open or
closed, intraday or positional, every deployment) dated today, and
correctly unwinds everything that trade touched: cash, the position/lot
rows themselves, each strategy's own persisted resume-state, the
day's deployment_events, and the day's equity-curve snapshots. Built for
a real incident: force_exit_time silently failed to fire for several
intraday deployments, and the resulting flatten used a stale/mistimed
LTP as the exit price — the booked P&L for today is provably wrong, not
just suspicious, and the owner would rather have NO data for today than
WRONG data permanently in the trade history.

WHY THIS NEEDS TO BE MORE THAN "DELETE FROM positions" — same reasoning
fix_strangle_instrument_tokens.py and recreate_deployment_clean.py both
already established for this codebase: a raw delete only touches one of
several places a trade's effects live.

1. CASH. record_fill's own cash formula (app/db/queries.py) is exactly
   `cash_delta = -(qty*price) if action == 'buy' else (qty*price)`,
   applied at EVERY fill (open AND close). This script sums that exact
   formula over every position_lots row dated today, per deployment, and
   subtracts the total back out of current_cash -- mathematically
   reverses today's fills regardless of whether they were opens, adds,
   or closes, with no assumptions about position state.

2. THE POSITION ROWS. Every position touched by a lot dated today is
   deleted OUTRIGHT if it was ALSO opened today (cascades away its lots
   automatically, ON DELETE CASCADE). A position opened on an EARLIER day
   that picked up a lot today is a mixed-day case, split into two:

   - Exactly ONE lot today, and it's the one that CLOSED the position:
     safe to auto-handle, no averaging math involved -- undo just that
     lot (delete it, reverse its own cash delta) and flip the position
     back to 'open' with qty restored from that same lot's own qty
     (record_fill's own ClosingQtyMismatch check guarantees a closing
     fill's qty always exactly equals what was open before it, so
     there's no ambiguity in what to restore). CONFIRMED real, not
     theoretical: several genuine checkpoint_target/continuous_50pct_
     trigger closes, correctly triggered by real strategy logic, off a
     tick that should never have reached it at all (a pre-market
     snapshot timestamped close enough to "now" to look live -- see the
     market-hours fix in app/deployments/runner.py). The trigger wasn't
     wrong; the tick behind it was.
   - Anything else (more than one lot today, or a lot that ADDED to /
     left the position open rather than closing it) would need replaying
     the position's remaining older lots to reconstruct qty/
     avg_entry_price/status in-application, the same averaging math
     record_fill itself does -- this script does NOT guess at that. It
     detects the case per deployment and ABORTS EARLY, PRINTS which
     deployment/position needs manual handling, and touches NOTHING for
     that deployment (every deployment is independent -- one abort never
     blocks the others).

3. PERSISTED STRATEGY STATE (deployment_state table). A strategy's
   cycle_id/entered_ever/entered_today/etc. isn't derived fresh from
   `positions` on every tick -- it's a stored JSON snapshot, read back
   verbatim in on_start(). Deleting today's positions without also
   clearing this leaves it stale (e.g. still says "entered_ever=true,
   cycle_id=1" after the only entry that ever set that is gone). Cleared
   entirely per affected deployment -- every strategy's own on_start/
   _resume_from_db already knows how to reconstruct correctly from
   positions/position_lots history alone when this is empty (that's
   the exact resume-safety path already exercised on every real restart
   this session).

4. deployment_events / deployment_snapshots dated today, for affected
   deployments -- the Activity tab and equity curve would otherwise keep
   showing fills/values for trades that no longer exist. Deleted outright
   (both are pure records/audit trail, nothing else reads or depends on
   them for trading logic).

5. LIVE IN-MEMORY STATE. Exactly like fix_strangle_instrument_tokens.py's
   own reasoning: a running deployment's in-memory runner (self.legs,
   cash, etc.) doesn't re-read any of the above live. FULLY HANDS-OFF:
   after fixing the DB, this calls the app's own API to Pause then Resume
   every deployment it touched, tearing down and rebuilding each one's
   actual DeploymentRunner against the now-corrected DB -- same as
   clicking Pause then Resume in the UI, automatically.

Runs INSIDE the app container (DB access + this app's own dependencies):
    docker exec live-deploy python3 custom_scripts/remove_todays_trades.py --date 2026-09-02
    docker exec live-deploy python3 custom_scripts/remove_todays_trades.py --date 2026-09-02 --dry-run
    docker exec live-deploy python3 custom_scripts/remove_todays_trades.py --date 2026-09-11 \
        --deployment "Straddle Nifty Advanced" --deployment "Straddle Nifty Breakeven Adjustments"

--date is REQUIRED and deliberately not defaulted to "today" -- this is
a destructive, irreversible operation; naming the exact date explicitly
is a deliberate extra guard against running it against the wrong day by
accident. Dates are compared in IST (Asia/Kolkata), matching every other
"day boundary" concept in this app (entry_time, force_exit_time, the
day-rollover checks in each strategy's own on_tick) -- NOT the UTC date
the database timestamps are actually stored in.
"""
import argparse
import asyncio
import os
import sys
from datetime import date
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from app.config import load_config
from app.db.pool import close_pool, create_pool

IST = ZoneInfo("Asia/Kolkata")


async def main(target_date_str: str, dry_run: bool, only_deployments: list[str] | None = None) -> None:
    # Parsed ONCE here into a real date object -- asyncpg's date codec
    # needs an actual datetime.date for a $n::date parameter, a plain
    # ISO string doesn't auto-cast (confirmed by hitting exactly that
    # AttributeError against a real Postgres while testing this script).
    # target_date_str is kept around only for the human-readable prints
    # below.
    target_date = date.fromisoformat(target_date_str)
    only_set = set(only_deployments) if only_deployments else None

    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        deployments = await pool.fetch("SELECT * FROM deployments ORDER BY deployment_name")
        if only_set is not None:
            deployments = [d for d in deployments if d["deployment_name"] in only_set]
            found_names = {d["deployment_name"] for d in deployments}
            for missing in only_set - found_names:
                print(f"SKIP  {missing!r}: no such deployment — check the exact name (case-sensitive)")

        plan = []          # [(deployment, cash_delta, position_ids_to_delete, positions_to_reopen, lot_count)]
        aborted = []       # [(deployment_name, reason)]

        for dep in deployments:
            dep_id = dep["id"]

            lots_today = await pool.fetch(
                """
                SELECT l.*, p.opened_at, p.status, p.closed_at
                FROM position_lots l
                JOIN positions p ON p.id = l.position_id
                WHERE l.deployment_id = $1
                  AND (l.executed_at AT TIME ZONE 'Asia/Kolkata')::date = $2
                """,
                dep_id, target_date,
            )
            if not lots_today:
                continue

            lots_by_position: dict = {}
            position_info: dict = {}
            for row in lots_today:
                lots_by_position.setdefault(row["position_id"], []).append(row)
                position_info[row["position_id"]] = row

            positions_to_delete: list = []   # opened today -- delete outright
            positions_to_reopen: list = []   # [(position_id, lot_id, reopen_qty)]
            unhandled: list = []             # mixed-day cases this script won't guess at

            for pid, lots in lots_by_position.items():
                info = position_info[pid]
                opened_today = info["opened_at"].astimezone(IST).date() == target_date
                if opened_today:
                    positions_to_delete.append(pid)
                    continue

                # A position from an EARLIER day that picked up exactly ONE
                # lot today, and that lot is the one that closed it -- the
                # one mixed-day case safe to auto-handle without any lot-
                # replay averaging: reopening means undoing that single
                # close, nothing else. qty to restore is that lot's own
                # qty -- record_fill's own ClosingQtyMismatch guarantees a
                # closing fill's qty always exactly equals what was open
                # before it, so there's no ambiguity to resolve.
                # CONFIRMED real case (not theoretical): a genuine
                # checkpoint_target/continuous_50pct_trigger close,
                # correctly triggered by strategy logic -- just off a
                # pre-market tick that should never have reached it (see
                # the market-hours fix in app/deployments/runner.py). The
                # trigger itself isn't the problem; the tick behind it is.
                if len(lots) == 1 and info["status"] == "closed" \
                        and info["closed_at"] is not None \
                        and info["closed_at"].astimezone(IST).date() == target_date:
                    positions_to_reopen.append((pid, lots[0]["id"], float(lots[0]["qty"])))
                    continue

                # Anything else on a pre-existing position (an add/adjustment
                # lot that left it open, more than one lot today, a close
                # whose own closed_at doesn't match today for some reason)
                # would need replaying its remaining older lots' averaging
                # math -- not guessed at here, see module docstring point 2.
                unhandled.append(pid)

            if unhandled:
                aborted.append((
                    dep["deployment_name"],
                    f"{len(unhandled)} position(s) opened before {target_date} picked up a lot "
                    f"dated {target_date} in a way that isn't a single same-day close (e.g. a "
                    f"same-day adjustment on an older position) -- not auto-handled, see script "
                    f"docstring point 2. position_id(s): {unhandled}",
                ))
                continue

            cash_delta = sum(
                -(float(row["qty"]) * float(row["price"])) if row["action"] == "buy"
                else (float(row["qty"]) * float(row["price"]))
                for row in lots_today
            )
            plan.append((dep, cash_delta, positions_to_delete, positions_to_reopen, len(lots_today)))

        print(f"=== Plan for {target_date} (IST) ===\n")
        if aborted:
            print(f"ABORTED (untouched) -- {len(aborted)} deployment(s) need manual handling:")
            for name, reason in aborted:
                print(f"  {name}: {reason}")
            print()

        if not plan:
            print("Nothing to do -- no deployment has a trade dated that day.")
            return

        for dep, cash_delta, position_ids, reopen_list, lot_count in plan:
            print(f"{dep['deployment_name']!r}")
            if position_ids:
                print(f"    positions to delete: {len(position_ids)}")
            if reopen_list:
                print(f"    positions to REOPEN (undoing today's close only): {len(reopen_list)}")
            print(f"    lots: {lot_count}")
            print(f"    current_cash: {dep['current_cash']} -> {float(dep['current_cash']) - cash_delta:.2f} "
                  f"(reversing {cash_delta:+.2f})")
            print(f"    deployment_state: cleared")
            print()

        if dry_run:
            print(f"DRY RUN -- would have touched {len(plan)} deployment(s), "
                  f"skipped {len(aborted)}. Re-run without --dry-run to apply.")
            return

        api_key = cfg["app_auth_secret"]
        headers = {"X-API-Key": api_key}
        base = "http://localhost:8000"

        for dep, cash_delta, position_ids, reopen_list, lot_count in plan:
            dep_id = dep["id"]
            name = dep["deployment_name"]
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE deployments SET current_cash = current_cash - $2, updated_at = now() "
                        "WHERE id = $1",
                        dep_id, cash_delta,
                    )
                    if position_ids:
                        await conn.execute(
                            "DELETE FROM positions WHERE id = ANY($1::uuid[])", position_ids,
                        )
                    for pid, lot_id, reopen_qty in reopen_list:
                        # Undo exactly the one lot that closed this position
                        # today -- the position itself goes back to 'open'
                        # with its pre-close qty restored, everything about
                        # it from before today (the original opening lot,
                        # its own metadata) untouched.
                        await conn.execute(
                            "UPDATE positions SET status = 'open', qty = $2, "
                            "closed_at = NULL, realized_pnl = 0 WHERE id = $1",
                            pid, reopen_qty,
                        )
                        await conn.execute("DELETE FROM position_lots WHERE id = $1", lot_id)
                    await conn.execute(
                        "DELETE FROM deployment_state WHERE deployment_id = $1", dep_id,
                    )
                    await conn.execute(
                        "DELETE FROM deployment_events WHERE deployment_id = $1 "
                        "AND (created_at AT TIME ZONE 'Asia/Kolkata')::date = $2",
                        dep_id, target_date,
                    )
                    await conn.execute(
                        "DELETE FROM deployment_snapshots WHERE deployment_id = $1 "
                        "AND (snapshot_at AT TIME ZONE 'Asia/Kolkata')::date = $2",
                        dep_id, target_date,
                    )
            print(f"  {name}: DB corrected (cash reversed, {len(position_ids)} position(s) deleted, "
                  f"{len(reopen_list)} position(s) reopened, state/events/snapshots for "
                  f"{target_date} cleared)")

            try:
                if dep["status"] == "active":
                    r = requests.post(f"{base}/deployments/{dep_id}/pause", headers=headers, timeout=15)
                    r.raise_for_status()
                r = requests.post(f"{base}/deployments/{dep_id}/resume", headers=headers, timeout=15)
                r.raise_for_status()
                print(f"  {name}: paused+resumed -- runner rebuilt from the corrected DB")
            except requests.RequestException as e:
                print(f"  {name}: DB fix applied, but pause/resume via the API failed ({e}) -- "
                      f"pause then resume it manually via the UI to pick up the corrected state")
            print()

        if aborted:
            print(f"Reminder: {len(aborted)} deployment(s) were skipped entirely -- see above.")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", required=True, help="Date to remove, YYYY-MM-DD, compared in IST (required, no default).")
    parser.add_argument("--dry-run", action="store_true", help="Preview the plan without writing anything.")
    parser.add_argument(
        "--deployment", action="append", dest="deployments", metavar="NAME",
        help="Only touch this exact deployment_name (repeatable for several). Omit to process every "
             "deployment with a trade on --date -- use this when the date's contamination is scoped to "
             "specific deployments (e.g. a few intraday ones holding a stale multi-day-old position) and "
             "OTHER deployments have a legitimate, wanted trade on that same date that must not be touched.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.date, args.dry_run, args.deployments))
