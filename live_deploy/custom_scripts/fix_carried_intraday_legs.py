#!/usr/bin/env python3
"""
live_deploy — custom_scripts/fix_carried_intraday_legs.py

Finds, and optionally corrects, every leg of an INTRADAY deployment that
was not closed on the day it opened: legs the strategy lost track of at
its force_exit_time (see the commit "Exits leave nothing open"), carried
into the next session and either still open or closed later at the
wrong time and price.

REPORT (default, read-only): every carried leg, per deployment, with the
events logged that afternoon (strategy errors, pauses, restarts) — which
tell you which cause it was:
  - "strategy_error … on_tick raised" near force_exit_time: a close
    raised mid-flatten and the leg was dropped from tracking;
  - no events at all and no fills after force_exit_time: no runner, or
    no ticks (Kite down / app restarted before login);
  - "paused": the deployment was paused over the square-off.

--fix: puts each carried leg back where it should have ended, AT the
deployment's force_exit_time (default 15:00) on the day it opened:
  - still open  -> closed now with a fill dated that time, at that price;
  - closed late -> its closing fill is re-dated and re-priced, and the
                   position's realized P&L, charges, slippage and the
                   deployment's cash are corrected — one transaction per
                   leg; the original fill is kept in the lot's metadata.
The price is the option's own 1-minute price at force_exit_time: Kite
history while the contract is listed, else the app's own recorded marks
(position_marks), else the leg is skipped and reported (nothing is
guessed). Re-running is safe: a fixed leg closes on its own day and no
longer qualifies. Active deployments touched are paused and resumed so
their runners reload from the database. Snapshots already taken (the
equity curve between the two dates) are not rewritten.

USAGE (inside the app container):
    docker exec live-deploy python3 custom_scripts/fix_carried_intraday_legs.py
    docker exec live-deploy python3 custom_scripts/fix_carried_intraday_legs.py --since 2026-09-01
    docker exec live-deploy python3 custom_scripts/fix_carried_intraday_legs.py --deployment "BankNifty DTT Adjusted"
    docker exec live-deploy python3 custom_scripts/fix_carried_intraday_legs.py --fix
"""
import argparse
import asyncio
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.charges import estimate_options_charges
from app.config import load_config
from app.db import queries
from app.db.pool import close_pool, create_pool
from app.slippage import fill_slippage

IST = ZoneInfo("Asia/Kolkata")
MARKER = "carried_leg_fix"


def ist_date(ts: datetime) -> date:
    return ts.astimezone(IST).date()


def exit_time_of(dep) -> time:
    raw = (dep["config"] or {}).get("force_exit_time") or "15:00"
    try:
        h, m = str(raw).split(":")[:2]
        return time(int(h), int(m))
    except ValueError:
        return time(15, 0)


async def kite_client(cfg, pool):
    try:
        from kiteconnect import KiteConnect
        session = await queries.get_kite_session(pool)
        if session is None or not session["access_token"]:
            return None
        kite = KiteConnect(api_key=cfg["api_key"])
        kite.set_access_token(session["access_token"])
        return kite
    except Exception:
        return None


async def price_at(pool, kite, pos, when: datetime) -> tuple[Optional[float], str]:
    """The option's price at `when` (aware): Kite's 1-minute candle
    starting then (its open), else the last recorded mark at or before
    it (within 10 minutes). (None, why) when neither exists."""
    token = int(pos["instrument_token"])
    if kite is not None:
        try:
            candles = await asyncio.to_thread(
                kite.historical_data, token, when - timedelta(minutes=10), when + timedelta(minutes=1), "minute",
            )
            exact = [c for c in candles if c["date"].astimezone(IST) == when.astimezone(IST)]
            if exact:
                return float(exact[0]["open"]), "kite 1-minute candle"
            before = [c for c in candles if c["date"].astimezone(IST) <= when.astimezone(IST)]
            if before:
                return float(before[-1]["close"]), "kite 1-minute candle (last before)"
        except Exception:
            pass   # expired contract, or no session: try our own marks
    row = await pool.fetchrow(
        """SELECT price, marked_at FROM position_marks
           WHERE position_id = $1 AND marked_at <= $2 AND marked_at >= $3
           ORDER BY marked_at DESC LIMIT 1""",
        pos["id"], when, when - timedelta(minutes=10),
    )
    if row is not None:
        return float(row["price"]), f"recorded mark at {row['marked_at'].astimezone(IST):%H:%M}"
    return None, "no Kite history and no recorded marks around that time"


async def events_around(pool, dep_id, day: date, at: time) -> list[str]:
    start = datetime.combine(day, at, IST) - timedelta(minutes=15)
    end = datetime.combine(day, time(15, 45), IST)
    rows = await pool.fetch(
        """SELECT event_type, message, created_at FROM deployment_events
           WHERE deployment_id = $1 AND created_at BETWEEN $2 AND $3
             AND event_type NOT LIKE 'fill_%'
           ORDER BY created_at""",
        dep_id, start, end,
    )
    return [f"{r['created_at'].astimezone(IST):%H:%M} {r['event_type']}: {(r['message'] or '')[:90]}" for r in rows]


async def fix_closed(pool, dep, pos, new_price: float, when: datetime, source: str) -> float:
    """Re-date and re-price a late close. Returns the P&L change."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            p = await conn.fetchrow("SELECT * FROM positions WHERE id = $1 FOR UPDATE", pos["id"])
            close_action = "buy" if p["side"] == "short" else "sell"
            lots = await conn.fetch(
                "SELECT * FROM position_lots WHERE position_id = $1 ORDER BY executed_at", p["id"],
            )
            closing = [l for l in lots if l["action"] == close_action]
            if len(closing) != 1:
                raise ValueError(f"expected one closing fill, found {len(closing)}")
            lot = closing[0]
            qty = float(lot["qty"])
            entry = float(p["avg_entry_price"])
            old_price = float(lot["price"])
            if p["side"] == "short":
                pnl = (entry - new_price) * qty
                buy_price, sell_price = new_price, entry
            else:
                pnl = (new_price - entry) * qty
                buy_price, sell_price = entry, new_price
            old_exit_slip = fill_slippage(close_action, old_price, float(lot["book_price"]) if lot["book_price"] is not None else None, qty)
            meta = dict(lot["metadata"] or {})
            meta[MARKER] = {
                "original_price": old_price, "original_executed_at": lot["executed_at"].isoformat(),
                "original_reason": lot["reason"], "price_source": source, "fixed_at": datetime.now(timezone.utc).isoformat(),
            }
            await conn.execute(
                """UPDATE position_lots SET price = $2, executed_at = $3, book_price = NULL,
                       reason = 'force_exit_backfill', metadata = $4 WHERE id = $1""",
                lot["id"], new_price, when, meta,
            )
            await conn.execute(
                """UPDATE positions SET realized_pnl = $2, closed_at = $3, estimated_charges = $4,
                       estimated_slippage = GREATEST(0, estimated_slippage - $5) WHERE id = $1""",
                p["id"], round(pnl, 2), when, estimate_options_charges(buy_price, sell_price, qty), old_exit_slip,
            )
            # Cash moved by qty*price on the close: undo the old, apply the new.
            cash_delta = (old_price - new_price) * qty if close_action == "buy" else (new_price - old_price) * qty
            await conn.execute(
                "UPDATE deployments SET current_cash = current_cash + $2, updated_at = now() WHERE id = $1",
                dep["id"], cash_delta,
            )
            await conn.execute(
                "INSERT INTO deployment_events (deployment_id, event_type, message, metadata) VALUES ($1, $2, $3, $4)",
                dep["id"], MARKER,
                f"{p['symbol']}: late close re-priced to {new_price:.2f} at {when.astimezone(IST):%d %b %H:%M} (was {old_price:.2f})",
                {"position_id": str(p["id"]), "price_source": source},
            )
            return pnl - float(p["realized_pnl"])


async def main(args) -> None:
    cfg = load_config()
    pool = await create_pool(cfg["database_url"])
    try:
        kite = await kite_client(cfg, pool)
        if kite is None:
            print("Note: no Kite session — prices come from recorded marks only.\n")
        deps = await pool.fetch("SELECT * FROM deployments WHERE mode = 'intraday' ORDER BY deployment_name")
        if args.deployment:
            deps = [d for d in deps if d["deployment_name"] == args.deployment]
            if not deps:
                print(f"No intraday deployment named {args.deployment!r}.")
                return
        since = date.fromisoformat(args.since) if args.since else date(2000, 1, 1)
        today = datetime.now(IST).date()

        plan = []   # (dep, pos, when, price, source, kind)
        for dep in deps:
            at = exit_time_of(dep)
            rows = await pool.fetch(
                "SELECT * FROM positions WHERE deployment_id = $1 ORDER BY opened_at", dep["id"],
            )
            carried = [
                p for p in rows if ist_date(p["opened_at"]) >= since and (
                    (p["status"] == "open" and ist_date(p["opened_at"]) < today)
                    or (p["status"] == "closed" and p["closed_at"] is not None and ist_date(p["closed_at"]) > ist_date(p["opened_at"]))
                )
            ]
            if not carried:
                continue
            print(f"=== {dep['deployment_name']} ({dep['strategy_name']}, {dep['status']}) · exit time {at:%H:%M} ===")
            seen_days = set()
            for p in carried:
                day = ist_date(p["opened_at"])
                when = datetime.combine(day, at, IST)
                price, source = await price_at(pool, kite, p, when)
                if p["status"] == "open":
                    state = "STILL OPEN"
                else:
                    state = f"closed {p['closed_at'].astimezone(IST):%d %b %H:%M} · P&L {float(p['realized_pnl']):+,.2f}"
                fix = f"→ {price:.2f} ({source})" if price is not None else f"→ cannot fix: {source}"
                print(f"  {day:%d %b} {p['side']:5} {p['symbol']:24} qty {float(p['qty']) or '-'!s:>6}  {state}  {fix}")
                if day not in seen_days:
                    seen_days.add(day)
                    for e in await events_around(pool, dep["id"], day, at) or ["(no events logged that afternoon)"]:
                        print(f"        {e}")
                if price is not None:
                    plan.append((dep, p, when, price, source, p["status"]))
            print()

        if not plan:
            print("Nothing to fix." if args.fix else "No fixable carried legs found.")
            return
        print(f"{len(plan)} leg(s) can be fixed.")
        if not args.fix:
            print("Report only. Re-run with --fix to apply.")
            return

        touched = {}
        for dep, p, when, price, source, kind in plan:
            try:
                if kind == "open":
                    await queries.record_fill(
                        pool, dep["id"], p["symbol"], int(p["instrument_token"]),
                        "buy" if p["side"] == "short" else "sell", float(p["qty"]), price, when,
                        reason="force_exit_backfill", metadata={MARKER: {"price_source": source}},
                    )
                    print(f"  closed  {dep['deployment_name']} / {p['symbol']} @ {price:.2f}")
                else:
                    d = await fix_closed(pool, dep, p, price, when, source)
                    print(f"  re-priced {dep['deployment_name']} / {p['symbol']} @ {price:.2f} (P&L {d:+,.2f})")
                touched[dep["id"]] = dep
            except Exception as e:
                print(f"  FAILED  {dep['deployment_name']} / {p['symbol']}: {e}")

        active = [d for d in touched.values() if d["status"] == "active"]
        if active:
            print("\nReloading active deployments (pause + resume)...")
            import requests
            headers = {"X-API-Key": cfg["app_auth_secret"]}
            for dep in active:
                try:
                    requests.post(f"http://localhost:8000/deployments/{dep['id']}/pause", headers=headers, timeout=15).raise_for_status()
                    requests.post(f"http://localhost:8000/deployments/{dep['id']}/resume", headers=headers, timeout=15).raise_for_status()
                    print(f"  {dep['deployment_name']}: reloaded")
                except Exception as e:
                    print(f"  {dep['deployment_name']}: pause/resume failed ({e}) — the DB fix is saved; resume it from the app")
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix", action="store_true", help="apply the fixes (default: report only)")
    parser.add_argument("--since", help="only legs opened on or after this IST date, YYYY-MM-DD")
    parser.add_argument("--deployment", help="only this deployment (exact name)")
    asyncio.run(main(parser.parse_args()))
