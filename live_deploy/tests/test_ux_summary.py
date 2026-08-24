"""Focused tests for the UX-v2 mode-aware active-period aggregation."""
from datetime import datetime, timezone
from uuid import uuid4

from app.routers.ux_summary import _active_rows


def _pos(dep_id, *, opened, closed=None, status="open", pnl=0.0):
    return {
        "id": uuid4(),
        "deployment_id": dep_id,
        "status": status,
        "opened_at": opened,
        "closed_at": closed,
        "realized_pnl": pnl,
    }


def test_intraday_uses_current_ist_day_only():
    dep_id = uuid4()
    deps = [{"id": dep_id, "mode": "intraday", "initial_capital": 100_000.0}]
    positions = [
        _pos(dep_id, opened=datetime(2026, 8, 24, 4, 0, tzinfo=timezone.utc),
             closed=datetime(2026, 8, 24, 7, 0, tzinfo=timezone.utc), status="closed", pnl=1250),
        _pos(dep_id, opened=datetime(2026, 8, 23, 4, 0, tzinfo=timezone.utc),
             closed=datetime(2026, 8, 23, 7, 0, tzinfo=timezone.utc), status="closed", pnl=900),
    ]
    rows = _active_rows(deps, positions, {}, datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc))
    assert rows[0]["period_label"] == "Today"
    assert rows[0]["realized_pnl"] == 1250
    assert rows[0]["today_realized_pnl"] == 1250


def test_positional_cycle_keeps_realized_adjustments_until_whole_episode_is_flat():
    dep_id = uuid4()
    deps = [{"id": dep_id, "mode": "positional", "initial_capital": 100_000.0}]
    # The replacement leg opens before the old leg closes, so the existing
    # canonical episode merge treats both as one strategic position.
    positions = [
        _pos(dep_id, opened=datetime(2026, 8, 22, 4, 30, tzinfo=timezone.utc),
             closed=datetime(2026, 8, 24, 5, 30, tzinfo=timezone.utc), status="closed", pnl=2200),
        _pos(dep_id, opened=datetime(2026, 8, 24, 5, 0, tzinfo=timezone.utc), status="open", pnl=0),
    ]
    rows = _active_rows(deps, positions, {}, datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc))
    assert rows[0]["active"] is True
    assert rows[0]["period_label"] == "Current cycle"
    assert rows[0]["realized_pnl"] == 2200
    assert rows[0]["open_positions"] == 1
    assert rows[0]["started_at"].startswith("2026-08-22")
