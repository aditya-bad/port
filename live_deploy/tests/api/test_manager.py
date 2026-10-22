"""The DeploymentManager inside the running app: startup resume that
survives a broken deployment, snapshots, price marks, the not-running /
watchdog's recovery (its alerts are app/ops_health.py's: tests/api/
test_ops_health.py), and recovery when a restart fails after flatten or
remove-trades."""

from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import add_fill, make_deployment, sql

IST = ZoneInfo("Asia/Kolkata")
WED = datetime(2026, 10, 14, tzinfo=IST)       # a market day


def mgr(client):
    return client.app.state.deployment_manager


def call(client, fn, *args):
    return client.portal.call(fn, *args)


def break_config(client, dep_id):
    """What a strategy's on_start rejects: no underlying token."""
    sql(client, "UPDATE deployments SET config = config || '{\"instrument_tokens\": []}'::jsonb WHERE id = $1::uuid", dep_id)


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day.date(), time(h, m), IST)


# ── Startup ───────────────────────────────────────────────────────────

def test_startup_resumes_active_deployments_and_skips_a_broken_one(client):
    good = make_deployment(client, name="Good", start="now")
    bad = make_deployment(client, name="Bad", start="now")
    call(client, mgr(client).shutdown_all)
    mgr(client).runners.clear()
    break_config(client, bad["id"])
    started = call(client, mgr(client).load_active_on_startup)
    assert started == 1
    assert good["id"] in mgr(client).runners and bad["id"] not in mgr(client).runners
    assert "instrument_tokens" in mgr(client).start_errors[bad["id"]]
    [nr] = call(client, mgr(client).find_not_running)
    assert nr["id"] == bad["id"] and "instrument_tokens" in nr["reason"]


# ── Watchdog ──────────────────────────────────────────────────────────

def test_watchdog_brings_a_dead_runner_back_by_itself(client, pushes, monkeypatch):
    monkeypatch.setattr(mgr(client), "push_config", {"vapid": "test"})
    d = make_deployment(client, name="Dead", start="now")
    add_fill(client, d["id"], "CE", 1, "sell", 75, 100, at(WED, "10:00"))
    runner = mgr(client).runners.pop(d["id"])
    call(client, runner.stop)
    call(client, mgr(client).check_health_once, at(WED, "18:00"))
    assert mgr(client)._is_running(d["id"]) and mgr(client).health_issues == []
    assert pushes == [], "recovered on the first pass: nothing to alert about"
    events = [e["event_type"] for e in client.get(f"/deployments/{d['id']}/events").json()]
    assert "restarted" in events
    assert mgr(client).runners[d["id"]].open_positions, "picked its open leg back up"


def test_watchdog_records_it_once_keeps_retrying_and_says_when_it_recovers(client, pushes, monkeypatch):
    monkeypatch.setattr(mgr(client), "push_config", {"vapid": "test"})
    d = make_deployment(client, name="Broken", start="now")
    add_fill(client, d["id"], "CE", 1, "sell", 75, 100, at(WED, "10:00"))
    runner = mgr(client).runners.pop(d["id"])
    call(client, runner.stop)
    good = sql(client, "SELECT config FROM deployments WHERE id = $1::uuid", d["id"])[0]["config"]
    break_config(client, d["id"])
    evening = at(WED, "18:00")
    call(client, mgr(client).check_health_once, evening)
    call(client, mgr(client).check_health_once, evening)
    assert pushes == [], "the health monitor alerts, not the watchdog"
    assert [x["id"] for x in mgr(client).health_issues] == [d["id"]]
    assert mgr(client).health_issues[0]["open_positions"] == 1
    assert "instrument_tokens" in mgr(client).health_issues[0]["reason"]
    events = [e["event_type"] for e in client.get(f"/deployments/{d['id']}/events").json()]
    assert events.count("not_running") == 1
    # the cause clears (Kite back, config fixed): the next retry succeeds
    sql(client, "UPDATE deployments SET config = $2::jsonb WHERE id = $1::uuid", d["id"], good)
    mgr(client)._restart_tried[d["id"]] -= 120          # a minute and more has passed
    call(client, mgr(client).check_health_once, evening)
    assert mgr(client)._is_running(d["id"]) and mgr(client).health_issues == []
    events = [e["event_type"] for e in client.get(f"/deployments/{d['id']}/events").json()]
    assert "running_again" in events and pushes == []


def test_watchdog_retries_at_most_once_a_minute(client, monkeypatch):
    d = make_deployment(client, name="Broken", start="now")
    runner = mgr(client).runners.pop(d["id"])
    call(client, runner.stop)
    break_config(client, d["id"])
    tries = []
    real = mgr(client).restart

    async def counting(dep_id):
        tries.append(dep_id)
        return await real(dep_id)
    monkeypatch.setattr(mgr(client), "restart", counting)
    for _ in range(5):
        call(client, mgr(client).check_health_once, at(WED, "18:00"))
    assert len(tries) == 1


def test_watchdog_no_longer_pushes_about_kite(client, pushes, monkeypatch):
    """Kite not connected in market hours is the health monitor's to alert
    (every minute until it's back), not a once-a-day watchdog push."""
    monkeypatch.setattr(mgr(client), "push_config", {"vapid": "test"})
    call(client, mgr(client).check_health_once, at(WED, "09:10"))
    assert pushes == []


# ── Snapshots and marks ───────────────────────────────────────────────

def test_snapshots_value_positional_legs_at_market_and_intraday_at_cash(client):
    pos = make_deployment(client, name="P", strategy="strangle_monthly_v2", mode="positional", start="now")
    intra = make_deployment(client, name="I", start="now")
    add_fill(client, pos["id"], "CE", 1, "sell", 75, 100, at(WED, "10:00"))
    add_fill(client, intra["id"], "PE", 2, "sell", 75, 100, at(WED, "10:00"))
    for d in (pos, intra):
        call(client, mgr(client).runners[d["id"]]._reload_positions)
    client.app.state.dispatcher.last_prices.update({1: 80.0, 2: 80.0})
    call(client, mgr(client).snapshot_all_active)
    rows = {str(r["deployment_id"]): r for r in sql(client, "SELECT * FROM deployment_snapshots")}
    assert float(rows[pos["id"]]["open_positions_value"]) == pytest.approx(1500)
    assert float(rows[pos["id"]]["total_value"]) == pytest.approx(500000 + 1500)
    assert float(rows[intra["id"]]["open_positions_value"]) == 0
    assert float(rows[intra["id"]]["total_value"]) == pytest.approx(500000)


def test_snapshot_values_an_unpriced_leg_at_its_last_mark_not_zero(client):
    """Kite down / holiday / just restarted: no live price. Valuing the leg
    at 0 recorded a fake drop and made the Desk show the leg's whole
    unrealized P&L as today's move."""
    d = make_deployment(client, name="P", strategy="strangle_monthly_v2", mode="positional", start="now")
    add_fill(client, d["id"], "CE", 11, "sell", 75, 100, at(WED, "10:00"))
    add_fill(client, d["id"], "PE", 12, "sell", 75, 100, at(WED, "10:00"))
    runner = mgr(client).runners[d["id"]]
    call(client, runner._reload_positions)
    disp = client.app.state.dispatcher
    disp.last_prices.pop(11, None)
    disp.last_prices[12] = 90.0
    ce = runner.open_positions[11]["id"]
    sql(client, "INSERT INTO position_marks (position_id, deployment_id, marked_at, price) VALUES "
                "($1::uuid, $2::uuid, $3, 70), ($1::uuid, $2::uuid, $4, 80)",
        str(ce), d["id"], at(WED, "14:00"), at(WED, "15:25"))
    try:
        call(client, mgr(client).snapshot_all_active)
    finally:
        disp.last_prices.pop(12, None)
    [row] = sql(client, "SELECT * FROM deployment_snapshots WHERE deployment_id = $1::uuid", d["id"])
    # CE at its latest mark 80: +1500; PE live at 90: +750
    assert float(row["open_positions_value"]) == pytest.approx(2250)


def test_snapshot_is_skipped_while_a_leg_has_never_been_priced(client):
    d = make_deployment(client, name="P", strategy="strangle_monthly_v2", mode="positional", start="now")
    add_fill(client, d["id"], "CE", 13, "sell", 75, 100, at(WED, "10:00"))
    call(client, mgr(client).runners[d["id"]]._reload_positions)
    client.app.state.dispatcher.last_prices.pop(13, None)
    call(client, mgr(client).snapshot_all_active)
    assert sql(client, "SELECT 1 FROM deployment_snapshots WHERE deployment_id = $1::uuid", d["id"]) == []


def test_marks_every_minute_intraday_every_fifth_positional_in_session_only(client):
    pos = make_deployment(client, name="P", strategy="strangle_monthly_v2", mode="positional", start="now")
    intra = make_deployment(client, name="I", start="now")
    add_fill(client, pos["id"], "CE", 1, "sell", 75, 100, at(WED, "10:00"))
    add_fill(client, intra["id"], "PE", 2, "sell", 75, 100, at(WED, "10:00"))
    add_fill(client, intra["id"], "OLD", 3, "sell", 75, 100, at(WED, "10:00"))
    for d in (pos, intra):
        call(client, mgr(client).runners[d["id"]]._reload_positions)
    disp = client.app.state.dispatcher
    disp.last_prices.update({1: 90.0, 2: 95.0, 3: 50.0})
    disp.last_price_times[3] = datetime(2026, 10, 13, 15, 29)    # yesterday's trade: not today's mark
    rec = mgr(client).record_marks_once
    try:
        assert call(client, rec, at(WED, "10:01")) == 1, "intraday leg only"
        assert call(client, rec, at(WED, "10:05")) == 2, "positional joins on the 5th minute"
        assert call(client, rec, at(WED, "09:00")) == 0
        assert call(client, rec, at(WED, "15:31")) == 0
        assert call(client, rec, at(WED + timedelta(days=3), "10:05")) == 0
    finally:
        disp.last_price_times.pop(3, None)


def test_post_market_checkpoint_persists_strategy_state_without_stopping(client):
    d = make_deployment(client, start="now")
    mgr(client).runners[d["id"]].strategy.today = WED.date()     # it has seen a session
    sql(client, "DELETE FROM deployment_state")
    call(client, mgr(client).dump_state_all_active)
    assert sql(client, "SELECT 1 FROM deployment_state WHERE deployment_id = $1::uuid", d["id"])
    assert mgr(client)._is_running(d["id"])


# ── Recovery when the restart after an action fails ───────────────────

def test_flatten_of_a_deployment_that_cant_restart_leaves_it_paused(client):
    d = make_deployment(client, start="now")
    add_fill(client, d["id"], "CE", 1, "sell", 75, 100, at(WED, "10:00"))
    break_config(client, d["id"])
    assert client.post(f"/deployments/{d['id']}/flatten").status_code == 200
    assert client.get(f"/deployments/{d['id']}").json()["status"] == "paused"
    assert client.get(f"/deployments/{d['id']}/positions").json() == []


def test_remove_trades_that_cant_restart_rolls_back_to_paused(client):
    d = make_deployment(client, start="now")
    add_fill(client, d["id"], "CE", 1, "sell", 75, 100, at(WED, "10:00"))
    break_config(client, d["id"])
    r = client.post(f"/deployments/{d['id']}/remove-trades")
    assert r.status_code >= 400
    assert client.get(f"/deployments/{d['id']}").json()["status"] == "paused"
    assert client.get(f"/deployments/{d['id']}/positions?status=all").json() == [], "the removal itself stands"


def test_remove_trades_refusals(client):
    d = make_deployment(client)
    assert client.post(f"/deployments/{d['id']}/remove-trades").status_code == 409, "nothing to remove"
    client.post(f"/deployments/{d['id']}/stop")
    assert client.post(f"/deployments/{d['id']}/remove-trades").status_code == 409, "stopped"


def test_resume_that_fails_records_why(client):
    d = make_deployment(client)
    break_config(client, d["id"])
    r = client.post(f"/deployments/{d['id']}/resume")
    assert r.status_code in (400, 409) and "instrument_tokens" in r.text
    assert client.get(f"/deployments/{d['id']}").json()["status"] == "paused"


def test_a_fill_refreshes_the_list_cache(client):
    d = make_deployment(client, start="now")
    runner = mgr(client).runners[d["id"]]
    call(client, lambda: runner.sell("CE", 1, 75, 100.0, datetime.now(IST).replace(tzinfo=None), reason="entry"))
    call(client, mgr(client)._refresh_cache_after_fill)
    row = next(x for x in client.get("/deployments").json() if x["id"] == d["id"])
    assert float(row["current_cash"]) == pytest.approx(500000 + 7500)


def test_pause_stops_the_strategy_even_if_saving_its_state_fails(client, monkeypatch):
    """Regression: a failing state dump used to leave the runner's task
    running — shown paused, still trading."""
    d = make_deployment(client, start="now")
    runner = mgr(client).runners[d["id"]]

    def boom():
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(runner.strategy, "get_persistable_state", boom, raising=False)
    monkeypatch.setattr(runner, "dump_state", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    task = runner._task
    assert client.post(f"/deployments/{d['id']}/pause").status_code == 200
    assert task.done() and runner._task is None and runner._queue is None


def test_shutdown_stops_every_runner_even_if_one_fails(client, monkeypatch):
    a = make_deployment(client, name="A", start="now")
    b = make_deployment(client, name="B", start="now")
    ra, rb = mgr(client).runners[a["id"]], mgr(client).runners[b["id"]]

    async def bad_stop():
        raise RuntimeError("boom")
    monkeypatch.setattr(ra, "stop", bad_stop)
    tasks = [rb._task]
    call(client, mgr(client).shutdown_all)
    assert mgr(client).runners == {} and all(t.done() for t in tasks)
    call(client, type(ra).stop, ra)    # the one whose stop was broken
