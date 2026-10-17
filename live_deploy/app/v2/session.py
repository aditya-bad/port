"""Today's session, deployment by deployment — the Desk's lanes.

For every deployment this returns:

* equity_open — its P&L (realized + unrealized) as of the session open,
  in each basis. Today's P&L is then simply "P&L now − equity_open",
  which the UI computes live from the same realized/unrealized figures
  GET /deployments already serves. Gross comes from the last 5-minute
  snapshot at or before 09:15 IST (total_value − initial capital), so
  legs carried overnight count only their move since the open; the
  after-charges bases subtract the charges/slippage of everything that
  closed before today. With no snapshot to go on, unrealized at the open
  is taken as 0.
* series — [epoch ms, gross P&L since the open] from today's snapshots,
  the shape of the lane. The UI appends a live point for "now".
* events — fills grouped into entries / exits / adjustments, plus
  errors, pauses, stops and closes, for the lane's diamonds.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

_IST = ZoneInfo("Asia/Kolkata")
_OPEN = time(9, 15)
_FILL_GROUP = timedelta(seconds=90)
_EVENT_KINDS = {
    "strategy_error": "error", "paused": "pause", "stopped": "stop",
    "flattened": "close", "kite_disconnected": "error",
}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def session_bounds(now: datetime) -> tuple[datetime, datetime]:
    """(midnight IST today, 09:15 IST today) as UTC datetimes."""
    day = now.astimezone(_IST).date()
    start = datetime.combine(day, time(0), _IST)
    return start.astimezone(timezone.utc), datetime.combine(day, _OPEN, _IST).astimezone(timezone.utc)


def group_fills(lots: list) -> list[dict[str, Any]]:
    """Pure: one deployment's fills today (sorted) -> lane markers. A
    burst of fills that only opens is an entry ("fill"), one that only
    closes is an exit, a mix is an adjustment/roll."""
    groups: list[dict] = []
    for lot in lots:
        opening = (lot["side"] == "long") == (lot["action"] == "buy")
        if groups and lot["executed_at"] - groups[-1]["last"] <= _FILL_GROUP:
            g = groups[-1]
            g["last"] = lot["executed_at"]
        else:
            g = {"first": lot["executed_at"], "last": lot["executed_at"], "open": 0, "close": 0, "n": 0, "reasons": set()}
            groups.append(g)
        g["open" if opening else "close"] += 1
        g["n"] += 1
        if lot["reason"]:
            g["reasons"].add(lot["reason"])
    out = []
    for g in groups:
        kind = "rollout" if "eod_rollout" in g["reasons"] else "fill" if not g["close"] else "exit" if not g["open"] else "adjust"
        out.append({"t": _ms(g["first"]), "kind": kind, "fills": g["n"], "reason": ", ".join(sorted(g["reasons"]))})
    return out


async def build_session(pool: asyncpg.Pool, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    day_start, open_at = session_bounds(now)
    async with pool.acquire() as conn:
        deps = await conn.fetch("SELECT id, initial_capital::float8 AS cap FROM deployments")
        before = await conn.fetch(
            """
            SELECT deployment_id,
                   SUM(realized_pnl)::float8 AS g,
                   SUM(COALESCE(estimated_charges, 0))::float8 AS c,
                   SUM(COALESCE(estimated_slippage, 0))::float8 AS s
            FROM positions
            WHERE status = 'closed' AND closed_at < $1
            GROUP BY 1
            """,
            day_start,
        )
        open_snaps = await conn.fetch(
            """
            SELECT DISTINCT ON (deployment_id) deployment_id, total_value::float8 AS v
            FROM deployment_snapshots
            WHERE snapshot_at <= $1 AND snapshot_at >= $1 - interval '4 days'
            ORDER BY deployment_id, snapshot_at DESC
            """,
            open_at,
        )
        snaps = await conn.fetch(
            """
            SELECT deployment_id, snapshot_at, total_value::float8 AS v
            FROM deployment_snapshots
            WHERE snapshot_at > $1 AND snapshot_at <= $2
            ORDER BY deployment_id, snapshot_at
            """,
            open_at, now,
        )
        lots = await conn.fetch(
            """
            SELECT l.deployment_id, l.action, l.executed_at, l.reason, p.side
            FROM position_lots l JOIN positions p ON p.id = l.position_id
            WHERE l.executed_at >= $1
            ORDER BY l.deployment_id, l.executed_at
            """,
            day_start,
        )
        events = await conn.fetch(
            """
            SELECT deployment_id, event_type, message, created_at
            FROM deployment_events
            WHERE created_at >= $1 AND event_type = ANY($2::text[])
            ORDER BY created_at
            """,
            day_start, list(_EVENT_KINDS),
        )

    before_by = {r["deployment_id"]: r for r in before}
    open_by = {r["deployment_id"]: r["v"] for r in open_snaps}
    snaps_by: dict = defaultdict(list)
    for r in snaps:
        snaps_by[r["deployment_id"]].append(r)
    lots_by: dict = defaultdict(list)
    for r in lots:
        lots_by[r["deployment_id"]].append(r)
    events_by: dict = defaultdict(list)
    for r in events:
        events_by[r["deployment_id"]].append(r)

    lanes = {}
    for d in deps:
        b = before_by.get(d["id"])
        g, c, s = (b["g"], b["c"], b["s"]) if b else (0.0, 0.0, 0.0)
        snap = open_by.get(d["id"])
        gross_open = (snap - d["cap"]) if snap is not None else g
        markers = group_fills(lots_by.get(d["id"], []))
        markers += [
            {"t": _ms(e["created_at"]), "kind": _EVENT_KINDS[e["event_type"]], "reason": e["message"] or ""}
            for e in events_by.get(d["id"], [])
        ]
        markers.sort(key=lambda m: m["t"])
        lanes[str(d["id"])] = {
            "equity_open": {
                "gross": round(gross_open, 2),
                "net": round(gross_open - c, 2),
                "net_slip": round(gross_open - c - s, 2),
            },
            "series": [[_ms(r["snapshot_at"]), round(r["v"] - d["cap"] - gross_open, 2)] for r in snaps_by.get(d["id"], [])],
            "events": markers,
        }
    return {"date": now.astimezone(_IST).date().isoformat(), "open_at": _ms(open_at), "now": _ms(now), "lanes": lanes}
