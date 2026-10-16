"""Tests for the pure parts of the /v2 API (app/v2)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.v2.actions import exit_order
from app.v2.book import episodes_to_trades
from app.v2.catalog import describe_fields, humanize
from app.v2.preferences import clean
from app.v2.session import group_fills, session_bounds

T0 = datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc)   # 09:30 IST


def _pos(opened, closed=None, pnl=0.0, charges=0.0, slippage=0.0):
    return {
        "id": uuid4(), "opened_at": opened, "closed_at": closed,
        "status": "closed" if closed else "open", "realized_pnl": pnl,
        "estimated_charges": charges, "estimated_slippage": slippage,
    }


def test_trades_merge_legs_and_count_adjustments():
    ce = _pos(T0, T0 + timedelta(days=3), pnl=1200, charges=40, slippage=10)
    pe = _pos(T0 + timedelta(seconds=20), T0 + timedelta(days=1), pnl=-500, charges=40, slippage=5)
    roll = _pos(T0 + timedelta(days=1, minutes=1), T0 + timedelta(days=3), pnl=300, charges=40)
    later = _pos(T0 + timedelta(days=5), T0 + timedelta(days=6), pnl=-100)
    rows = [ce, pe, roll, later]
    trades = episodes_to_trades("dep", rows, {roll["id"]: "checkpoint"})
    assert len(trades) == 2
    first = trades[0]
    assert first["legs"] == 3
    assert first["adjustments"] == 1          # the roll, not the second entry leg
    assert first["pnl"] == 1000
    assert first["pnl_net"] == 880
    assert first["pnl_net_slip"] == 865
    assert first["exit_reason"] == "checkpoint"
    assert first["wins"] == 2


def test_open_episode_is_not_a_trade():
    rows = [_pos(T0, T0 + timedelta(hours=1), pnl=50), _pos(T0 + timedelta(days=2))]
    trades = episodes_to_trades("dep", rows, {})
    assert len(trades) == 1 and trades[0]["pnl"] == 50


def test_fill_groups_classify_entry_exit_adjust():
    def lot(sec, action, side):
        return {"executed_at": T0 + timedelta(seconds=sec), "action": action, "side": side, "reason": None}
    lots = [lot(0, "sell", "short"), lot(5, "sell", "short"),          # entry
            lot(3600, "buy", "short"), lot(3610, "sell", "short"),     # roll
            lot(9000, "buy", "short")]                                  # exit
    kinds = [g["kind"] for g in group_fills(lots)]
    assert kinds == ["fill", "adjust", "exit"]


def test_exit_order_closes_shorts_before_longs():
    legs = [{"side": "long", "n": 1}, {"side": "short", "n": 2}, {"side": "long", "n": 3}, {"side": "short", "n": 4}]
    assert [p["n"] for p in exit_order(legs)] == [2, 4, 1, 3]


def test_session_bounds_are_ist():
    start, open_at = session_bounds(datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 10, 15, 18, 30, tzinfo=timezone.utc)
    assert open_at == datetime(2026, 10, 16, 3, 45, tzinfo=timezone.utc)


def test_field_metadata_falls_back_for_unknown_keys():
    fields = {f["key"]: f for f in describe_fields({
        "entry_time": "10:00", "lots_per_trade": 1, "enable_hedging": False,
        "expiry_selector": "THIS_WEEK", "brand_new_knob_pct": 0.2, "instrument_tokens": [256265],
    })}
    assert fields["entry_time"]["type"] == "time" and fields["entry_time"]["label"] == "Entry time"
    assert fields["lots_per_trade"]["suffix"] == "lots"
    assert fields["enable_hedging"]["type"] == "bool"
    assert fields["expiry_selector"]["type"] == "choice"
    assert fields["brand_new_knob_pct"]["label"] == "Brand new knob %"
    assert fields["instrument_tokens"]["type"] == "json"
    assert humanize("eod_check_time") == "EOD check time"


def test_preferences_keep_only_small_scalars():
    out = clean({"theme": "night", "size": 1.1, "big": True, "x" * 50: 1, "nested": {"a": 1}, "long": "y" * 200})
    assert out == {"theme": "night", "size": 1.1, "big": True}
