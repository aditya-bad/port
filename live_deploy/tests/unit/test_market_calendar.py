"""The market's sessions (app/market_calendar.py): a normal weekday, and
special ones at their own times. 8 Nov 2026, a Sunday: Muhurat trading,
14:20-15:30, manage only."""

from datetime import date, datetime, time

from app import market_calendar as C
from app.deployments.runner import DeploymentRunner

MUHURAT = date(2026, 11, 8)


def test_a_normal_day_a_weekend_and_muhurat():
    assert C.session(date(2026, 11, 6)) == C.NORMAL and not C.manage_only(date(2026, 11, 6))
    assert C.session(date(2026, 11, 7)) is None, "a Saturday: no session"
    s = C.session(MUHURAT)
    assert (s.open, s.close, s.manage_only) == (time(14, 20), time(15, 30), True)
    assert C.manage_only(MUHURAT) and "no new positions" in C.note(MUHURAT)


def test_a_normal_days_times_move_to_the_session():
    assert C.shifted(MUHURAT, time(9, 15)) == time(14, 20)
    assert C.shifted(MUHURAT, time(9, 20)) == time(14, 25), "5 minutes after the open"
    assert C.shifted(MUHURAT, time(9, 10)) == time(14, 15), "5 minutes before it"
    assert C.shifted(MUHURAT, time(15, 40)) == time(15, 40), "after the close: kept from the close"
    assert C.shifted(date(2026, 11, 6), time(9, 20)) == time(9, 20), "a normal day: unchanged"


def test_the_runner_takes_no_tick_from_the_muhurat_pre_open_or_a_weekend():
    r = DeploymentRunner.__new__(DeploymentRunner)
    assert not r._in_session(datetime(2026, 11, 8, 14, 10)), "14:05-14:20 is the pre-open"
    assert r._in_session(datetime(2026, 11, 8, 14, 20))
    assert r._in_session(datetime(2026, 11, 8, 15, 35))
    assert not r._in_session(datetime(2026, 11, 8, 10, 0)), "nothing in the morning"
    assert not r._in_session(datetime(2026, 11, 7, 10, 0)), "a Saturday"
    assert r._in_session(datetime(2026, 11, 6, 9, 15)) and not r._in_session(datetime(2026, 11, 6, 9, 14))
