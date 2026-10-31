"""Kinds of alert a deployment turns off (app/alert_types.py): set by
PATCH /deployments/{id}, and every place that raises an alert leaves out
the kinds turned off, while the critical ones still alert."""

import re
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app import alert_types, ops_health
from tests.conftest import add_fill, make_deployment

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[2]


def test_the_browser_has_the_same_kinds():
    js = (ROOT / "static/linck/js/data/alert_types.js").read_text()
    keys = re.findall(r"^    \['([a-z_]+)', '", js.split("ALWAYS_ON")[0], re.M)
    assert keys == list(alert_types.TYPES), "data/alert_types.js and app/alert_types.py list the same kinds, in order"
    assert len(re.findall(r"^  \['[^']+', '[^']+'\],$", js.split("ALWAYS_ON")[1], re.M)) == len(alert_types.ALWAYS_ON)


def test_alerts_off_is_set_cleaned_and_kept(client):
    d = make_deployment(client, name="alerts kept")
    assert client.get(f"/deployments/{d['id']}").json()["alerts_off"] == []
    r = client.patch(f"/deployments/{d['id']}", json={"alerts_off": ["drawdown", "trades", "drawdown"]})
    assert r.status_code == 200 and r.json()["alerts_off"] == ["trades", "drawdown"], "each once, in the catalogue's order"
    assert next(x for x in client.get("/deployments").json() if x["id"] == d["id"])["alerts_off"] == ["trades", "drawdown"]
    # another change leaves it alone; [] turns them all back on
    client.patch(f"/deployments/{d['id']}", json={"notes": "x"})
    assert client.get(f"/deployments/{d['id']}").json()["alerts_off"] == ["trades", "drawdown"]
    assert client.patch(f"/deployments/{d['id']}", json={"alerts_off": []}).json()["alerts_off"] == []


def test_an_unknown_kind_is_refused(client):
    d = make_deployment(client, name="alerts unknown")
    r = client.patch(f"/deployments/{d['id']}", json={"alerts_off": ["drawdown", "not_running"]})
    assert r.status_code == 400 and "not_running" in r.json()["detail"], "the critical ones can't be turned off"
    assert client.get(f"/deployments/{d['id']}").json()["alerts_off"] == []


def test_entries_and_exits_off_is_silent_and_on_again_alerts(client, pushes, monkeypatch):
    from tests.api.test_runner import runner_of
    d = make_deployment(client, name="Quiet straddle", start="now")
    r = runner_of(client, d["id"])
    monkeypatch.setattr(r, "push_config", {"vapid": "test"})
    client.patch(f"/deployments/{d['id']}", json={"alerts_off": ["trades"]})
    client.portal.call(r.notify_execution, "entry", "Sold CE+PE")
    assert pushes == []
    assert "execution_entry" not in [e["event_type"] for e in client.get(f"/deployments/{d['id']}/events").json()], "no toast"
    client.patch(f"/deployments/{d['id']}", json={"alerts_off": ["drawdown"]})   # read fresh: no restart
    client.portal.call(r.notify_execution, "exit", "Closed")
    assert pushes == [("Quiet straddle — Exit", "Closed")]


def test_a_health_finding_of_a_kind_turned_off_isnt_pushed(client):
    from app import notifications, strategy_health
    d = make_deployment(client, name="health quiet")
    pool = client.app.state.db_pool
    found = {d["id"]: [
        {"rule": "drawdown", "key": "drawdown:x", "title": "Deepest drawdown yet", "body": "b1", "name": "health quiet"},
        {"rule": "win_rate", "key": "win_rate:x", "title": "Winning less often", "body": "b2", "name": "health quiet"},
    ]}
    client.patch(f"/deployments/{d['id']}", json={"alerts_off": ["drawdown"]})
    sent = []

    async def fake(pool, config, title, text):
        sent.append(title)
    real, notifications.send_push_for_all = notifications.send_push_for_all, fake
    try:
        assert client.portal.call(lambda: strategy_health.push_new(pool, {}, found)) == 1
    finally:
        notifications.send_push_for_all = real
    assert sent == ["health quiet: winning less often"]
    # turned back on later, it's still news: never marked as sent
    client.patch(f"/deployments/{d['id']}", json={"alerts_off": []})
    notifications.send_push_for_all = fake
    try:
        assert client.portal.call(lambda: strategy_health.push_new(pool, {}, found)) == 1
    finally:
        notifications.send_push_for_all = real
    assert sent[-1] == "health quiet: deepest drawdown yet"


def test_a_vix_spike_leaves_out_deployments_that_turned_it_off(client, monkeypatch):
    from app import notifications, vix as V
    a, b = make_deployment(client, name="vix wants"), make_deployment(client, name="vix muted")
    spike = {"vix": 16.4, "prev_close": 14.5, "change_pct": 13.1, "spiking": True, "per_point": -900.0,
             "exposed": [{"id": a["id"], "name": "vix wants"}, {"id": b["id"], "name": "vix muted"}]}
    sent = []

    async def fake(pool, config, title, text):
        sent.append(text)
    monkeypatch.setattr(notifications, "send_push_for_all", fake)
    monkeypatch.setattr(notifications, "is_push_configured", lambda c: True)
    pool, now = client.app.state.db_pool, datetime(2026, 10, 22, 11, 0, tzinfo=IST)
    client.patch(f"/deployments/{b['id']}", json={"alerts_off": ["vix"]})
    assert client.portal.call(lambda: V.push_spike_once(pool, {}, spike, now)) is True
    assert sent == ["1 deployment hold sold premium that loses as VIX rises: vix wants."], "the muted one isn't named, nor counted per point"
    # every exposed one turned it off: no push, and the day isn't used up
    client.patch(f"/deployments/{a['id']}", json={"alerts_off": ["vix"]})
    later = now + timedelta(days=1)
    assert client.portal.call(lambda: V.push_spike_once(pool, {}, spike, later)) is False
    client.patch(f"/deployments/{a['id']}", json={"alerts_off": []})
    assert client.portal.call(lambda: V.push_spike_once(pool, {}, spike, later)) is True


def test_ops_warnings_turned_off_stay_issues_but_dont_alert(client, pushes, monkeypatch):
    mgr = client.app.state.deployment_manager
    monkeypatch.setattr(mgr, "push_config", {"vapid": "test"})
    d = make_deployment(client, name="ops quiet")
    pool = client.app.state.db_pool
    now = datetime(2026, 10, 14, 11, 0, tzinfo=IST)
    stale = ops_health.Problem(f"dep:{d['id']}:stale-legs", "stale_legs", "ops quiet: 1 leg(s) haven't traded today", "x",
                               severity="warning", deployment_id=d["id"], repeat=False)
    warn = ops_health.Problem(f"strat:w{d['id']}", "strategy:entry_failed", "ops quiet: couldn't enter", "y",
                              severity="warning", deployment_id=d["id"], repeat=False)
    crit = ops_health.Problem(f"dep:{d['id']}:down", "not_running", "ops quiet isn't running", "z",
                              deployment_id=d["id"])
    client.patch(f"/deployments/{d['id']}", json={"alerts_off": ["stale_legs", "strategy_warning"]})
    client.portal.call(client.app.state.ops._sync, now, [stale, warn, crit])
    assert [t for t, _ in pushes] == ["ops quiet isn't running"], "the critical one always alerts"
    open_keys = [x["key"] for x in client.get("/v2/ops").json()["open"]]
    assert stale.key in open_keys and warn.key in open_keys, "still issues, on the Repair page"
    # cleared without ever alerting: no "Fixed" either
    client.portal.call(client.app.state.ops._sync, now + timedelta(minutes=1), [crit])
    assert not [t for t, _ in pushes if t.startswith("Fixed")]


@pytest.mark.parametrize("kind,severity,want", [
    ("stale_legs", "warning", "stale_legs"), ("strategy:entry_failed", "warning", "strategy_warning"),
    ("strategy:crash", "critical", None), ("not_running", "critical", None), ("silent", "critical", None),
    ("strategy_error", "critical", None), ("end_of_day", "critical", None), ("kite_login", "critical", None),
])
def test_which_issues_can_be_turned_off(kind, severity, want):
    assert alert_types.of_problem(kind, severity) == want
