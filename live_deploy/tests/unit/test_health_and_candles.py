"""Index candles recorded by the app (chart source) and the health
watchdog (deployments active but not running; Kite not connected)."""

import asyncio

import pytest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.deployments.manager import DeploymentManager, fold_tick
from app.routers import cycles

IST = ZoneInfo("Asia/Kolkata")


def m(h, mi, d=16):
    return datetime(2026, 10, d, h, mi, tzinfo=IST)


def test_fold_tick_builds_ohlc_per_minute():
    b = {}
    for p in (100, 104, 98, 101):
        fold_tick(b, 1, p, m(9, 15))
    assert b[1] == [m(9, 15), 100, 104, 98, 101]
    fold_tick(b, 1, 99, m(9, 16))
    assert b[1] == [m(9, 16), 99, 99, 99, 99]


def test_bucket_anchors_at_open_like_kite():
    rows = [{"minute": m(9, 15 + i), "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i} for i in range(10)]
    out = cycles._bucket(rows, "5minute")
    assert [datetime.fromtimestamp(c["t"] / 1000, IST).strftime("%H:%M") for c in out] == ["09:15", "09:20"]
    assert out[0]["o"] == 100 and out[0]["h"] == 105 and out[0]["l"] == 99 and out[0]["c"] == 104.5


def test_session_minutes_skips_nights_and_weekends():
    assert cycles._session_minutes(m(9, 15), m(15, 30)) == 375
    # Fri 16 Oct 15:00 -> Mon 19 Oct 09:45: 30 + 30 minutes
    assert cycles._session_minutes(m(15, 0), m(9, 45, d=19)) == 60


def manager(active_rows, running_ids=(), connected=True, needs_login=False):
    mgr = object.__new__(DeploymentManager)
    mgr.runners = {i: SimpleNamespace(_task=SimpleNamespace(done=lambda: False)) for i in running_ids}
    mgr.start_errors, mgr.health_issues, mgr._alerted, mgr._kite_alerted_on = {}, [], set(), None
    mgr._restart_tried, mgr.restarts = {}, []

    async def restart(dep_id):          # Kite still down: the strategy can't start
        mgr.restarts.append(str(dep_id))
        raise ValueError("Still can't start: NoKiteSession: no session")
    mgr.restart = restart
    mgr.cache, mgr.push_config = None, {"x": 1}
    mgr.dispatcher = SimpleNamespace(connected=connected, status={"needs_login": needs_login})
    mgr.pushes, mgr.events = [], []

    async def push(title, body):
        mgr.pushes.append(title)

    async def record_event(*a, **k):
        mgr.events.append(a[3])

    async def fetchval(*a):
        return 2
    mgr._push, mgr._record_event = push, record_event
    mgr.pool = SimpleNamespace(fetchval=fetchval)

    async def list_deployments(pool, status=None):
        return active_rows
    _fake_queries["list_deployments"] = list_deployments
    return mgr


# The watchdog reads deployments through app.db.queries. The fake is
# swapped in for each test and the real query put back after: assigning
# it for good broke every later API test on the same worker (empty lists).
_fake_queries = {}


@pytest.fixture(autouse=True)
def _fake_list_deployments(monkeypatch):
    import app.deployments.manager as M
    _fake_queries.clear()

    async def through(*a, **k):
        return await _fake_queries["list_deployments"](*a, **k)
    monkeypatch.setattr(M.queries, "list_deployments", through)
    yield


ROW = {"id": "00000000-0000-0000-0000-000000000001", "deployment_name": "DTT A", "strategy_name": "intraday_dtt_adjusted"}


def test_watchdog_flags_active_without_runner_and_pushes_once():
    mgr = manager([ROW])
    mgr.start_errors[ROW["id"]] = "NoKiteSession: no session"
    asyncio.run(mgr.check_health_once(m(10, 0)))
    asyncio.run(mgr.check_health_once(m(10, 1)))
    assert [x["name"] for x in mgr.health_issues] == ["DTT A"]
    assert mgr.health_issues[0]["open_positions"] == 2 and "NoKiteSession" in mgr.health_issues[0]["reason"]
    assert mgr.pushes == ["DTT A isn't running"] and mgr.events == ["not_running"]
    assert mgr.restarts == [ROW["id"]], "tried to restart it by itself, once within the minute"


def test_watchdog_quiet_when_running():
    mgr = manager([ROW], running_ids=[ROW["id"]])
    asyncio.run(mgr.check_health_once(m(10, 0)))
    assert mgr.health_issues == [] and mgr.pushes == []


def test_kite_alert_once_on_market_days_after_0905_only():
    mgr = manager([], connected=False, needs_login=True)
    asyncio.run(mgr.check_health_once(m(9, 0)))
    assert mgr.pushes == []
    asyncio.run(mgr.check_health_once(m(9, 6)))
    asyncio.run(mgr.check_health_once(m(9, 30)))
    assert mgr.pushes == ["Kite isn't connected"]
    sat = manager([], connected=False, needs_login=True)
    asyncio.run(sat.check_health_once(m(10, 0, d=17)))
    assert sat.pushes == []
