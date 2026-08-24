"""UX-v2 aggregate summaries for the trading control-room UI.

This deliberately lives server-side so Dashboard/Deployments can answer
"what is this deployment doing now?" in O(1) browser requests instead of
issuing three requests per deployment.  It does not change execution,
positions, fills, snapshots, or the SSE transports.

Definitions:
* intraday active period = the current Asia/Kolkata trading date;
* positional active period = the currently-open overlapping position
  episode (all legs/adjustments/rolls that form the same strategic bet).

Unrealized P&L is intentionally not computed here.  The existing
GET /positions?status=open aggregate endpoint already enriches all open
positions from the dispatcher's current prices in one request; the UI
joins those rows to these settled-period summaries and keeps the open
part live via the existing /sse/ticks + LivePnl path.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from ..db import queries

router = APIRouter(tags=["aggregate"])
_IST = ZoneInfo("Asia/Kolkata")


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _active_rows(
    deployments: list[Any], positions: list[Any], last_actions: dict[Any, Any], now: datetime,
) -> list[dict[str, Any]]:
    """Pure reduction used by the HTTP endpoint and easy to unit-test."""
    now_ist = now.astimezone(_IST)
    day_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_ist = day_start_ist + timedelta(days=1)
    day_start = day_start_ist.astimezone(timezone.utc)
    day_end = day_end_ist.astimezone(timezone.utc)

    by_deployment: dict[Any, list[Any]] = defaultdict(list)
    for row in positions:
        by_deployment[row["deployment_id"]].append(row)

    out: list[dict[str, Any]] = []
    for dep in deployments:
        dep_id = dep["id"]
        mode = dep["mode"]
        rows = sorted(by_deployment.get(dep_id, []), key=lambda r: r["opened_at"])
        open_rows = [r for r in rows if r["status"] == "open"]
        today_closed = [
            r for r in rows
            if r["status"] == "closed" and r["closed_at"] is not None
            and day_start <= r["closed_at"] < day_end
        ]
        today_realized = sum(float(r["realized_pnl"] or 0) for r in today_closed)

        result: dict[str, Any] = {
            "deployment_id": str(dep_id),
            "mode": mode,
            "period_kind": "positional_cycle" if mode == "positional" else "intraday_day",
            "period_label": "Current cycle" if mode == "positional" else "Today",
            "active": mode != "positional",
            "started_at": _iso(day_start),
            "realized_pnl": today_realized if mode != "positional" else 0.0,
            "today_realized_pnl": today_realized,
            "open_positions": len(open_rows),
            "last_cycle_pnl": None,
            "last_cycle_opened_at": None,
            "last_cycle_closed_at": None,
            "last_action_at": None,
            "last_action": None,
        }

        action = last_actions.get(dep_id)
        if action:
            result["last_action_at"] = _iso(action["executed_at"])
            result["last_action"] = f"{action['action']} {action['symbol']}".strip()

        if mode == "positional" and rows:
            episodes = queries._group_into_episodes(rows)  # same canonical grouping as Detail Stats
            open_episodes = [ep for ep in episodes if ep["end"] is None]
            if open_episodes:
                result["active"] = True
                result["period_label"] = (
                    f"{len(open_episodes)} active cycles" if len(open_episodes) > 1 else "Current cycle"
                )
                result["started_at"] = _iso(min(ep["start"] for ep in open_episodes))
                result["realized_pnl"] = sum(
                    float(row["realized_pnl"] or 0)
                    for ep in open_episodes for row in ep["rows"]
                )
            else:
                result["active"] = False
                result["started_at"] = None
                closed = [ep for ep in episodes if ep["end"] is not None]
                if closed:
                    last = max(closed, key=lambda ep: ep["end"])
                    result["last_cycle_pnl"] = sum(float(r["realized_pnl"] or 0) for r in last["rows"])
                    result["last_cycle_opened_at"] = _iso(last["start"])
                    result["last_cycle_closed_at"] = _iso(last["end"])

        out.append(result)
    return out


@router.get("/portfolio/active-periods")
async def active_periods(request: Request) -> list[dict[str, Any]]:
    """Return mode-aware settled active-period summaries for ALL deployments.

    Exactly three database reads regardless of deployment count:
    deployments, all positions, and the latest lot per deployment.
    Current open MTM comes from the app's existing aggregate positions
    endpoint/SSE stream, so it is not duplicated here.
    """
    pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        deployments = await conn.fetch(
            "SELECT id, mode, initial_capital FROM deployments ORDER BY created_at"
        )
        positions = await conn.fetch(
            "SELECT * FROM positions ORDER BY deployment_id, opened_at ASC"
        )
        actions = await conn.fetch(
            """
            SELECT DISTINCT ON (p.deployment_id)
                   p.deployment_id, l.executed_at, l.action, p.symbol
            FROM position_lots l
            JOIN positions p ON p.id = l.position_id
            ORDER BY p.deployment_id, l.executed_at DESC
            """
        )
    last_actions = {r["deployment_id"]: r for r in actions}
    return _active_rows(deployments, positions, last_actions, datetime.now(timezone.utc))
