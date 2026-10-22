"""The trade book across every deployment, in two shapes the UI's
analytics are built from (leaderboards, compare, reports, a deployment's
performance tab):

* trades — one row per closed EPISODE: every leg, adjustment and roll of
  one strategic bet, from its first entry to the moment the deployment
  is flat again. Grouping is queries._group_into_episodes, the same
  merge the Detail page's cycles and positional digest already use, so
  a "trade" means the same thing everywhere.
* daily — realized P&L per deployment per IST trading day.
* repairs — the days each deployment was repaired, or flagged and not yet.

Both carry gross, after-charges and after-charges-and-slippage figures
(the UI's P&L basis switch picks one). Reads only; nothing is written.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

import asyncpg

from ..clock import IST
from ..db import queries

# Openings within this gap are one entry (the legs of a strangle go out
# a few seconds apart); a later opening inside the same episode is an
# adjustment or roll.
_ENTRY_GAP = timedelta(seconds=90)


def _f(v) -> float:
    return float(v) if v is not None else 0.0


def _bases(rows: list) -> tuple[float, float, float]:
    gross = sum(_f(r["realized_pnl"]) for r in rows)
    charges = sum(_f(r["estimated_charges"]) for r in rows)
    slip = sum(_f(r["estimated_slippage"]) for r in rows)
    return gross, gross - charges, gross - charges - slip


def _adjustments(rows: list) -> int:
    opens = sorted(r["opened_at"] for r in rows)
    count, last = 0, opens[0]
    for t in opens[1:]:
        if t - last > _ENTRY_GAP:
            count += 1
        last = t
    return count


def episodes_to_trades(dep_id, rows: list, reasons: dict, repaired: Optional[dict] = None) -> list[dict[str, Any]]:
    """Pure: one deployment's positions (sorted by opened_at) -> its
    closed trades. Split out so it can be unit-tested without a DB.
    `repaired`: {position id: the day a repair wrote into it} (app/repair)."""
    out = []
    for ep in queries._group_into_episodes(rows, reasons):
        if ep["end"] is None:
            continue   # still open: not a trade yet
        legs = ep["rows"]
        gross, net, net_slip = _bases(legs)
        # Legs closed together (a checkpoint exits everything at once):
        # prefer one that recorded why.
        last = max(legs, key=lambda r: (r["closed_at"], bool(reasons.get(r["id"]))))
        out.append({
            "deployment_id": str(dep_id),
            "opened_at": ep["start"].isoformat(),
            "closed_at": ep["end"].isoformat(),
            "pnl": round(gross, 2),
            "pnl_net": round(net, 2),
            "pnl_net_slip": round(net_slip, 2),
            "legs": len(legs),
            "adjustments": _adjustments(legs),
            "exit_reason": reasons.get(last["id"]) or "",
            "wins": sum(1 for r in legs if _f(r["realized_pnl"]) > 0),
            "repaired": next((repaired[r["id"]] for r in legs if r["id"] in (repaired or {})), None),
            "_start": ep["start"], "_end": ep["end"],
        })
    return out


async def _worst_points(conn, trades: list[dict]) -> None:
    """Deepest mark-to-market point inside each trade, from the 5-minute
    deployment snapshots (the same source the positional digest's
    max_loss uses). One query for every trade. None where no snapshot
    fell inside the trade (short intraday trades often have none)."""
    if not trades:
        return
    rows = await conn.fetch(
        """
        SELECT t.i,
               MIN(s.total_value)::float8 AS low,
               (array_agg(s.total_value ORDER BY s.snapshot_at ASC))[1]::float8 AS first
        FROM unnest($1::uuid[], $2::timestamptz[], $3::timestamptz[]) WITH ORDINALITY AS t(dep, st, en, i)
        JOIN deployment_snapshots s
          ON s.deployment_id = t.dep AND s.snapshot_at >= t.st AND s.snapshot_at <= t.en
        GROUP BY t.i
        """,
        [t["deployment_id"] for t in trades], [t["_start"] for t in trades], [t["_end"] for t in trades],
    )
    by_i = {r["i"]: r for r in rows}
    for i, t in enumerate(trades, start=1):
        r = by_i.get(i)
        if r is None or r["first"] is None:
            t["worst"] = None
        else:
            # Never shallower than the result itself: a trade that closed
            # at −₹900 went at least that far down.
            t["worst"] = round(min(0.0, r["low"] - r["first"], t["pnl"]), 2)


async def list_trades(pool: asyncpg.Pool, since: Optional[datetime] = None) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        positions = await conn.fetch(
            """
            SELECT id, deployment_id, opened_at, closed_at, status,
                   realized_pnl::float8 AS realized_pnl,
                   COALESCE(estimated_charges, 0)::float8 AS estimated_charges,
                   CASE WHEN status = 'closed' THEN COALESCE(estimated_slippage, 0) ELSE 0 END::float8 AS estimated_slippage
            FROM positions
            ORDER BY deployment_id, opened_at ASC
            """
        )
        reasons = await queries._fetch_closing_reasons(conn, [r["id"] for r in positions])
        repaired = {r["position_id"]: r["day"] or True for r in await conn.fetch(
            "SELECT DISTINCT ON (position_id) position_id, metadata->'repair'->>'day' AS day "
            "FROM position_lots WHERE metadata ? 'repair' ORDER BY position_id, executed_at")}
        by_dep: dict = defaultdict(list)
        for r in positions:
            by_dep[r["deployment_id"]].append(r)
        trades = []
        for dep_id, rows in by_dep.items():
            trades.extend(episodes_to_trades(dep_id, rows, reasons, repaired))
        if since is not None:
            trades = [t for t in trades if t["_end"] >= since]
        await _worst_points(conn, trades)
    trades.sort(key=lambda t: t["_end"])
    for t in trades:
        del t["_start"], t["_end"]
    return trades


async def list_daily(pool: asyncpg.Pool, since: Optional[datetime] = None) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT deployment_id,
               (closed_at AT TIME ZONE 'Asia/Kolkata')::date AS day,
               SUM(realized_pnl)::float8 AS pnl,
               SUM(realized_pnl - COALESCE(estimated_charges, 0))::float8 AS pnl_net,
               SUM(realized_pnl - COALESCE(estimated_charges, 0) - COALESCE(estimated_slippage, 0))::float8 AS pnl_net_slip,
               COUNT(*) AS closes
        FROM positions
        WHERE status = 'closed' AND closed_at IS NOT NULL
          AND ($1::timestamptz IS NULL OR closed_at >= $1)
        GROUP BY 1, 2
        ORDER BY 2, 1
        """,
        since,
    )
    return [
        {
            "deployment_id": str(r["deployment_id"]), "day": r["day"].isoformat(),
            "pnl": round(r["pnl"], 2), "pnl_net": round(r["pnl_net"], 2),
            "pnl_net_slip": round(r["pnl_net_slip"], 2), "closes": r["closes"],
        }
        for r in rows
    ]


SESSION_OPEN = time(9, 15)


def _next_session(day: date) -> datetime:
    """When the trading session after `day` opens (the next weekday, 09:15 IST)."""
    d = day + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, SESSION_OPEN, IST)


def cycle_of(mode: str, episodes: list, day: date, now: datetime) -> dict[str, Any]:
    """The cycle a repaired day belongs to — intraday: that day; positional:
    the episode (entry, adjustments, exit) it falls in — and whether it's
    still the deployment's current cycle: until the next one begins (the
    next session's open; a new entry). Pure, for the marks on every page."""
    if mode != "positional":
        return {"from": day.isoformat(), "to": day.isoformat(), "current": now < _next_session(day)}
    today = now.astimezone(IST).date()
    for k, ep in enumerate(episodes):
        start = ep["start"].astimezone(IST).date()
        end = ep["end"].astimezone(IST).date() if ep["end"] is not None else None
        if start <= day <= (end or today):
            return {"from": start.isoformat(), "to": end.isoformat() if end else None, "current": k == len(episodes) - 1}
    later = any(ep["start"].astimezone(IST).date() > day for ep in episodes)
    return {"from": day.isoformat(), "to": day.isoformat(), "current": not later}


async def list_repairs(pool: asyncpg.Pool, now: Optional[datetime] = None,
                       between: Optional[tuple[date, date]] = None) -> list[dict[str, Any]]:
    """Each deployment's repairs, for the marks on every page: the days a
    repair was applied to (app/repair), each with the cycle it belongs to
    and whether that's still the deployment's current cycle; and the days
    the end-of-day check flagged that nothing has repaired and nobody has
    dismissed (app/repair/daycheck.py) — their numbers aren't what the
    strategy would have done. `between` (from, to): only those days (the
    weekly review)."""
    now = now or datetime.now(IST)
    lo, hi = between or (date.min, date.max)
    rows = await pool.fetch(
        """
        SELECT r.deployment_id, r.day, 'repaired' AS state, NULL AS status, NULL AS headline, d.mode
        FROM stall_repairs r JOIN deployments d ON d.id = r.deployment_id
        WHERE r.undone_at IS NULL AND r.day >= $1 AND r.day < $2
        UNION ALL
        SELECT c.deployment_id, c.day, 'flagged', c.status, c.headline, d.mode
        FROM day_checks c JOIN deployments d ON d.id = c.deployment_id
        WHERE c.status IN ('rule', 'mismatch', 'ambiguous') AND c.dismissed_at IS NULL AND c.day >= $1 AND c.day < $2
          AND NOT EXISTS (SELECT 1 FROM stall_repairs r
                          WHERE r.deployment_id = c.deployment_id AND r.day = c.day AND r.undone_at IS NULL)
        ORDER BY 2, 1
        """,
        lo, hi,
    )
    positional = sorted({r["deployment_id"] for r in rows if r["state"] == "repaired" and r["mode"] == "positional"})
    episodes: dict = defaultdict(list)
    if positional:
        async with pool.acquire() as conn:
            pos = await conn.fetch(
                "SELECT id, deployment_id, opened_at, closed_at FROM positions WHERE deployment_id = ANY($1::uuid[]) "
                "ORDER BY deployment_id, opened_at", positional)
            reasons = await queries._fetch_closing_reasons(conn, [p["id"] for p in pos])
        by_dep: dict = defaultdict(list)
        for p in pos:
            by_dep[p["deployment_id"]].append(p)
        for dep, ps in by_dep.items():
            episodes[dep] = queries._group_into_episodes(ps, reasons)
    out = []
    for r in rows:   # one live repair a day (one_live_stall_repair)
        x = {"deployment_id": str(r["deployment_id"]), "day": r["day"].isoformat(), "state": r["state"],
             "status": r["status"], "headline": r["headline"]}
        if r["state"] == "repaired":
            x["cycle"] = cycle_of(r["mode"], episodes.get(r["deployment_id"], []), r["day"], now)
        out.append(x)
    return out
