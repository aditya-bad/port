"""The health monitor (app/ops_health.py) inside the running app: each
problem is an issue that alerts every minute until it clears (then once
more, "Fixed"), can be snoozed or dismissed, and is served at /v2/ops and
in /health. Driven with check_once(now) at chosen times."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import ops_health
from app.db import queries
from tests.conftest import add_fill, make_deployment, sql

IST = ZoneInfo("Asia/Kolkata")
WED = date(2026, 10, 14)       # a market day


def at(hhmm, day=WED, sec=0):
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m, sec), IST)


def mgr(client):
    return client.app.state.deployment_manager


def check(client, when):
    return client.portal.call(client.app.state.ops.check_once, when)


def keys(client):
    return [x["key"] for x in client.get("/v2/ops").json()["open"]]


@pytest.fixture
def push_on(client, monkeypatch):
    monkeypatch.setattr(mgr(client), "push_config", {"vapid": "test"})
    return client


@pytest.fixture
def kite_up(client, monkeypatch):
    """Connected, with a tick a moment ago (set last_tick_at per test)."""
    d = client.app.state.dispatcher
    monkeypatch.setattr(d, "connected", True)
    monkeypatch.setattr(d, "_kws", object())
    monkeypatch.setattr(d, "last_tick_at", None)
    return d


def titles(pushes, word=""):
    return [t for t, _ in pushes if word in t]


# ── Kite ──────────────────────────────────────────────────────────────

def test_kite_not_connected_alerts_every_minute_until_it_is_back(push_on, pushes, monkeypatch):
    client = push_on
    check(client, at("09:10"))
    assert keys(client) == ["kite:login"]
    assert titles(pushes) == ["Kite isn't connected"]
    check(client, at("09:10", sec=30))
    assert len(pushes) == 1, "not twice within the minute"
    check(client, at("09:11"))
    assert titles(pushes) == ["Kite isn't connected"] * 2
    [x] = client.get("/v2/ops").json()["open"]
    assert x["alerts"] == 2 and x["severity"] == "critical" and "can't trade until you log in" in x["detail"]
    # back: closed as cleared, and said once
    d = client.app.state.dispatcher
    monkeypatch.setattr(d, "connected", True)
    monkeypatch.setattr(d, "_kws", object())
    check(client, at("09:12"))
    assert keys(client) == [] and pushes[-1][0] == "Fixed: Kite isn't connected"
    [r] = client.get("/v2/ops").json()["recent"]
    assert r["resolved_by"] == "cleared" and r["key"] == "kite:login"
    check(client, at("09:13"))
    assert len(pushes) == 3


@pytest.mark.parametrize("when", [at("09:04"), at("15:30"), at("10:00", day=WED + timedelta(days=3))])
def test_kite_is_only_watched_in_market_hours_on_market_days(push_on, pushes, when):
    check(push_on, when)
    assert keys(push_on) == [] and pushes == []


def test_a_day_marked_as_a_holiday_is_quiet(push_on, pushes):
    client = push_on
    client.post("/notifications/mute-today")
    try:
        check(client, at("10:00"))
        assert keys(client) == [] and pushes == []
    finally:
        client.post("/notifications/unmute")


def test_a_silent_feed_is_an_issue_while_it_lasts(push_on, pushes, kite_up, monkeypatch):
    monkeypatch.setattr(kite_up, "last_tick_at", at("12:15"))
    check(push_on, at("12:20"))
    assert keys(push_on) == ["kite:stall"]
    x = client_issue(push_on, "kite:stall")
    assert "No ticks since 12:15" in x["detail"] and x["data"]["seconds"] == 300
    monkeypatch.setattr(kite_up, "last_tick_at", at("12:21"))
    check(push_on, at("12:21", sec=20))
    assert keys(push_on) == [] and pushes[-1][0] == "Fixed: Kite prices stopped"


def client_issue(client, key):
    return next(x for x in client.get("/v2/ops").json()["open"] if x["key"] == key)


# ── Snooze, dismiss ───────────────────────────────────────────────────

def test_snooze_stops_the_alerts_not_the_issue_and_dismiss_closes_it(push_on, pushes):
    client = push_on
    check(client, at("09:10"))
    iid = client_issue(client, "kite:login")["id"]
    r = client.post(f"/v2/ops/issues/{iid}/snooze", json={"for": "30m"})
    assert r.status_code == 200 and r.json()["snoozed_until"]
    check(client, at("09:12"))
    check(client, at("09:14"))
    assert len(pushes) == 1 and keys(client) == ["kite:login"], "still open, just quiet"
    client.post(f"/v2/ops/issues/{iid}/snooze", json={"for": "clear"})
    check(client, at("09:15"))
    assert len(pushes) == 2
    assert client.post(f"/v2/ops/issues/{iid}/snooze", json={"for": "soon"}).status_code == 422
    # dismissed by hand: closed — and if the check still finds it, it's a new issue
    r = client.post(f"/v2/ops/issues/{iid}/dismiss")
    assert r.json()["resolved_by"] == "dismissed"
    check(client, at("09:16"))
    again = client_issue(client, "kite:login")
    assert again["id"] != iid and again["alerts"] == 1
    assert client.post(f"/v2/ops/issues/{iid}/dismiss").status_code == 404


def test_snooze_choices():
    now = datetime(2026, 10, 14, 10, 0, tzinfo=IST)
    assert ops_health.snooze_until("30m", now) == now + timedelta(minutes=30)
    assert ops_health.snooze_until("2h", now) == now + timedelta(hours=2)
    assert ops_health.snooze_until("today", now) == datetime(2026, 10, 15, 7, 0, tzinfo=IST)
    with pytest.raises(ValueError):
        ops_health.snooze_until("later", now)


# ── Deployments ───────────────────────────────────────────────────────

def test_a_deployment_that_isnt_running_alerts_with_its_open_legs(push_on, pushes):
    client = push_on
    d = make_deployment(client, name="Broken", start="now")
    add_fill(client, d["id"], "CE", 1, "sell", 75, 100, at("10:00"))
    runner = mgr(client).runners.pop(d["id"])
    client.portal.call(runner.stop)
    sql(client, "UPDATE deployments SET config = config || '{\"instrument_tokens\": []}'::jsonb WHERE id = $1::uuid", d["id"])
    client.portal.call(mgr(client).check_health_once, at("18:00"))
    check(client, at("18:00"))
    x = client_issue(client, f"dep:{d['id']}:down")
    assert x["title"] == "Broken isn't running" and x["deployment_id"] == d["id"]
    assert "1 open position" in x["detail"] and "instrument_tokens" in x["detail"]
    assert titles(pushes, "Broken") == ["Broken isn't running"]


def test_strategy_errors_open_an_issue_that_clears_after_five_quiet_minutes(push_on, pushes):
    client = push_on
    d = make_deployment(client, name="Errs")
    sql(client, "INSERT INTO deployment_events (deployment_id, event_type, message, created_at) "
                "VALUES ($1::uuid, 'strategy_error', 'on_tick raised: boom', $2)", d["id"], at("18:00"))
    check(client, at("18:02"))
    x = client_issue(client, f"dep:{d['id']}:errors")
    assert x["title"] == "Errs: strategy errors" and "1 error(s)" in x["detail"] and "boom" in x["detail"]
    check(client, at("18:06"))
    assert f"dep:{d['id']}:errors" not in keys(client)


def test_a_runner_that_stops_acting_on_flowing_prices_is_flagged(push_on, pushes, kite_up, monkeypatch):
    client = push_on
    d = make_deployment(client, name="Quiet", start="now")
    r = mgr(client).runners[d["id"]]
    monkeypatch.setattr(kite_up, "last_tick_at", at("11:00"))
    monkeypatch.setattr(r, "created_at", at("10:00"))
    monkeypatch.setattr(r, "last_tick_at", at("10:50"))
    check(client, at("11:00", sec=30))
    x = client_issue(client, f"dep:{d['id']}:silent")
    assert x["title"] == "Quiet isn't acting on prices" and "10 min" in x["detail"]
    monkeypatch.setattr(r, "last_tick_at", at("11:00", sec=20))
    check(client, at("11:00", sec=40))
    assert f"dep:{d['id']}:silent" not in keys(client)
    # just started: not silent yet
    monkeypatch.setattr(r, "created_at", at("11:00"))
    monkeypatch.setattr(r, "last_tick_at", None)
    check(client, at("11:01"))
    assert f"dep:{d['id']}:silent" not in keys(client)


def test_legs_with_no_trade_today_are_a_warning(push_on, pushes, kite_up, monkeypatch):
    client = push_on
    d = make_deployment(client, name="BSE strangle", start="now")
    r = mgr(client).runners[d["id"]]
    monkeypatch.setattr(kite_up, "last_tick_at", at("11:00"))
    monkeypatch.setattr(r, "last_tick_at", at("11:00"))
    monkeypatch.setattr(r, "open_positions", {501: {"symbol": "BANKEX26NOV66300CE"}, 502: {"symbol": "BANKEX26NOV59400PE"}})
    kite_up.last_prices.update({501: 275.7, 502: 232.8})
    monkeypatch.setitem(kite_up.last_price_times, 501, datetime(2026, 10, 1, 11, 0))   # naive IST, as Kite sends it
    monkeypatch.setitem(kite_up.last_price_times, 502, at("10:30"))
    check(client, at("11:00", sec=30))
    x = client_issue(client, f"dep:{d['id']}:stale-legs")
    assert x["severity"] == "warning" and x["data"]["legs"] == ["BANKEX26NOV66300CE"]
    assert "stale-price guard" in x["detail"]
    monkeypatch.setitem(kite_up.last_price_times, 501, at("10:59"))
    check(client, at("11:01"))
    assert f"dep:{d['id']}:stale-legs" not in keys(client)
    # before 09:45 legs may simply not have traded yet
    monkeypatch.setitem(kite_up.last_price_times, 501, datetime(2026, 10, 1, 11, 0))
    monkeypatch.setattr(kite_up, "last_tick_at", at("09:30"))
    monkeypatch.setattr(r, "last_tick_at", at("09:30"))
    check(client, at("09:30", sec=20))
    assert f"dep:{d['id']}:stale-legs" not in keys(client)


# ── The 15:45 export ──────────────────────────────────────────────────

def test_a_failed_export_alerts_until_a_run_succeeds_then_says_so_once(push_on, pushes):
    client = push_on
    pool = client.app.state.db_pool
    client.portal.call(lambda: queries.set_app_scratch(pool, "candle_backfill_last", {
        "day": WED.isoformat(), "instruments": 12, "fetched": 4, "rows": 900, "seconds": 3.0,
        "error": "Kite isn't logged in", "complete": False}))
    check(client, at("15:50"))
    x = client_issue(client, f"export:{WED.isoformat()}")
    assert x["title"] == "Option history export failed" and "Kite isn't logged in" in x["detail"] and "4 of 12" in x["detail"]
    client.portal.call(lambda: queries.set_app_scratch(pool, "candle_backfill_last", {
        "day": WED.isoformat(), "instruments": 12, "fetched": 12, "rows": 4400, "seconds": 9.5,
        "error": None, "complete": True}))
    check(client, at("15:52"))
    assert keys(client) == []
    assert "Fixed: Option history export failed" in titles(pushes) and titles(pushes, "saved") == ["Option history saved"]
    check(client, at("15:53"))
    assert titles(pushes, "saved") == ["Option history saved"], "said once"
    assert "12 contract(s)" in next(b for t, b in pushes if t == "Option history saved")


def test_an_export_that_never_ran_is_flagged_after_1615(push_on, pushes):
    check(push_on, at("16:00"))
    assert keys(push_on) == []
    check(push_on, at("16:20"))
    assert client_issue(push_on, f"export:{WED.isoformat()}")["kind"] == "export_missing"


# ── The database, /health, the toast ─────────────────────────────────

def test_a_database_that_doesnt_answer_alerts_every_minute(push_on, pushes):
    import app.routers.health as H
    real = H.check_db_health

    async def down(pool):
        return False
    H.check_db_health = down
    check(push_on, at("18:00"))
    check(push_on, at("18:00", sec=30))
    check(push_on, at("18:01"))
    assert titles(pushes) == ["The database isn't answering"] * 2
    assert push_on.app.state.ops.open[0]["key"] == "db"
    H.check_db_health = real
    check(push_on, at("18:02"))
    assert "The database is back" in titles(pushes)


def test_health_carries_the_open_issues_for_the_header(push_on):
    check(push_on, at("09:10"))
    h = push_on.get("/health").json()
    assert [x["key"] for x in h["open_issues"]] == ["kite:login"] and h["open_issues"][0]["severity"] == "critical"


def test_alerts_go_to_open_tabs_as_events(push_on, monkeypatch):
    seen = []
    eb = push_on.app.state.event_broadcaster

    async def grab(ev):
        seen.append(ev)
    monkeypatch.setattr(eb, "broadcast", grab)
    check(push_on, at("09:10"))
    assert [e["event_type"] for e in seen] == ["health_issue"] and seen[0]["metadata"]["key"] == "kite:login"


def test_no_pushes_without_push_configured_but_issues_still_open(client, pushes):
    check(client, at("09:10"))
    assert keys(client) == ["kite:login"] and pushes == []
