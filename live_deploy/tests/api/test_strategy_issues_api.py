"""What a deployment reports about itself (strategy_issues, app/deployments/
health.py) inside the running app: the health monitor alerts on them —
critical ones every minute until they clear, warnings once, notes never —
closes ones past their expiry, and a dismiss on either side closes both;
/v2/strategy-issues lists them for the Repair page, and a day a
deployment reported about has a Repair case even if it was never checked."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import make_deployment, sql

IST = ZoneInfo("Asia/Kolkata")
WED = date(2026, 10, 14)


def at(hhmm, day=WED):
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), IST)


def check(client, when):
    return client.portal.call(client.app.state.ops.check_once, when)


@pytest.fixture
def push_on(client, monkeypatch):
    monkeypatch.setattr(client.app.state.deployment_manager, "push_config", {"vapid": "test"})
    return client


def report(client, dep_id, key, kind, severity="warning", title="Something happened", day=WED, expires_at=None):
    return sql(client, "INSERT INTO strategy_issues (deployment_id, key, kind, severity, title, detail, day, expires_at) "
                       "VALUES ($1::uuid, $2, $3, $4, $5, 'what and why', $6, $7) RETURNING id",
               dep_id, key, kind, severity, title, day, expires_at)[0]["id"]


def ops_keys(client):
    return [x["key"] for x in client.get("/v2/ops").json()["open"]]


def mine(pushes, word):
    return [t for t, _ in pushes if word in t]


def test_a_critical_report_alerts_every_minute_a_warning_once_a_note_never(push_on, pushes):
    client = push_on
    d = make_deployment(client, name="Strangle")
    crit = report(client, d["id"], "crash", "crash", "critical", "The strategy keeps failing")
    warn = report(client, d["id"], "entry:2026-10-14", "entry_delayed", "warning", "The 09:20 entry hasn't gone through yet")
    report(client, d["id"], "feed_gap:x", "feed_gap", "info", "No prices from 11:00 to 11:20")
    for m in range(3):
        check(client, at(f"10:0{m}"))
    assert len(mine(pushes, "keeps failing")) == 3
    assert len(mine(pushes, "entry hasn't gone through")) == 1
    assert mine(pushes, "No prices") == []
    keys = ops_keys(client)
    assert f"strat:{crit}" in keys and f"strat:{warn}" in keys and len([k for k in keys if k.startswith("strat:")]) == 2
    x = next(i for i in client.get("/v2/ops").json()["open"] if i["key"] == f"strat:{crit}")
    assert x["title"] == "Strangle: The strategy keeps failing" and x["deployment_id"] == d["id"]


def test_a_report_that_clears_closes_its_alert(push_on, pushes):
    client = push_on
    d = make_deployment(client, name="Calendar")
    iid = report(client, d["id"], "crash", "crash", "critical", "The strategy keeps failing")
    check(client, at("10:00"))
    assert f"strat:{iid}" in ops_keys(client)
    sql(client, "UPDATE strategy_issues SET resolved_at = now(), resolved_by = 'cleared' WHERE id = $1", iid)
    check(client, at("10:01"))
    assert f"strat:{iid}" not in ops_keys(client)


def test_a_report_past_its_expiry_is_closed_before_it_alerts(push_on, pushes):
    client = push_on
    d = make_deployment(client, name="Pivot")
    iid = report(client, d["id"], "bad_ticks", "bad_ticks", expires_at=datetime.now(IST) - timedelta(minutes=1))
    check(client, at("10:00"))
    assert f"strat:{iid}" not in ops_keys(client) and mine(pushes, "Pivot") == []
    [row] = sql(client, "SELECT resolved_by FROM strategy_issues WHERE id = $1", iid)
    assert row["resolved_by"] == "expired"


def test_dismissing_the_alert_closes_the_report(push_on, pushes):
    client = push_on
    d = make_deployment(client, name="Weekly")
    iid = report(client, d["id"], "legs_mismatch", "legs_mismatch", "critical")
    check(client, at("10:00"))
    oid = next(i["id"] for i in client.get("/v2/ops").json()["open"] if i["key"] == f"strat:{iid}")
    assert client.post(f"/v2/ops/issues/{oid}/dismiss").status_code == 200
    [row] = sql(client, "SELECT resolved_by FROM strategy_issues WHERE id = $1", iid)
    assert row["resolved_by"] == "dismissed"
    check(client, at("10:01"))
    assert f"strat:{iid}" not in ops_keys(client), "doesn't come straight back"


def test_the_list_and_dismissing_a_report_closes_its_alert(push_on, pushes):
    client = push_on
    d = make_deployment(client, name="DTT")
    other = make_deployment(client, name="Other")
    iid = report(client, d["id"], "fill_failed:X", "fill_failed", "critical", "A fill failed")
    report(client, other["id"], "bad_ticks", "bad_ticks")
    check(client, at("10:00"))

    got = client.get("/v2/strategy-issues").json()["issues"]
    assert {x["name"] for x in got} == {"DTT", "Other"}
    assert got[0]["severity"] == "critical", "worst first"
    one = client.get(f"/v2/strategy-issues?deployment={d['id']}").json()["issues"]
    assert [x["id"] for x in one] == [iid]
    assert one[0]["day"] == WED.isoformat() and one[0]["source"] == "runner" and one[0]["resolved_at"] is None

    assert client.post(f"/v2/strategy-issues/{iid}/dismiss").json() == {"id": iid, "dismissed": True}
    assert client.post(f"/v2/strategy-issues/{iid}/dismiss").status_code == 404
    assert f"strat:{iid}" not in ops_keys(client)
    assert iid not in [x["id"] for x in client.get("/v2/strategy-issues").json()["issues"]]
    closed = next(x for x in client.get("/v2/strategy-issues?all=true").json()["issues"] if x["id"] == iid)
    assert closed["resolved_by"] == "dismissed"


def test_a_day_a_deployment_reported_about_has_a_repair_case(client):
    d = make_deployment(client, name="Calendar")
    D = WED.isoformat()
    assert client.get(f"/v2/repair-cases/{D}/{d['id']}").status_code == 404
    report(client, d["id"], "entry:2026-10-14", "entry_skipped", "warning", "The 09:20 entry was skipped")
    report(client, d["id"], "other-day", "missed_trade", day=WED + timedelta(days=1))
    c = client.get(f"/v2/repair-cases/{D}/{d['id']}").json()
    assert c["check"] is None and c["applied"] is None
    assert [x["title"] for x in c["reported"]] == ["The 09:20 entry was skipped"], "only that day's"
