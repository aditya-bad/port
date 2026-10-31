"""Variations (app/variations.py): a deployment run beside its primary with
other settings. Each is a deployment of its own; totals count the primary,
a variation's alerts are the critical ones only, the evening day check
skips it, there are at most 10 a primary, and Make primary swaps the two."""

from datetime import date

import pytest

from app import alert_types, variations as V
from app.repair import daycheck
from tests.conftest import default_config, make_deployment

STRAT = "intraday_dtt_simple"


def mgr(client):
    return client.app.state.deployment_manager


def add(client, dep_id, label, run="paused", **extra):
    cfg = default_config(STRAT)
    cfg.update(extra.pop("cfg", {}))
    return client.post(f"/v2/deployments/{dep_id}/variations", json={"label": label, "config": cfg, "run": run, **extra})


def test_a_variation_is_a_deployment_of_its_own_beside_its_primary(client):
    p = make_deployment(client, name="Straddle P", capital=400000)
    r = add(client, p["id"], "wider stop", cfg={"per_leg_stop_loss_pct": 0.6})
    assert r.status_code == 201, r.text
    v = client.get(f"/deployments/{r.json()['id']}").json()
    assert v["deployment_name"] == "Straddle P · wider stop" and v["strategy_name"] == STRAT
    assert v["variation_of"] == p["id"] and v["variation_label"] == "wider stop"
    assert v["config"]["per_leg_stop_loss_pct"] == 0.6 and v["status"] == "paused"
    assert v["initial_capital"] == 400000, "the primary's capital unless given"
    assert v["include_in_reports"] is False, "totals count the primary"
    assert v["alerts_off"] == list(alert_types.TYPES), "the critical alerts only"
    # its own capital; created running
    r2 = add(client, p["id"], "small", run="now", initial_capital=150000)
    v2 = client.get(f"/deployments/{r2.json()['id']}").json()
    assert v2["initial_capital"] == 150000 and v2["status"] == "active" and r2.json()["id"] in mgr(client).runners
    # a variation of a variation hangs off the primary
    r3 = add(client, v2["id"], "tight")
    assert client.get(f"/deployments/{r3.json()['id']}").json()["variation_of"] == p["id"]
    listing = client.get(f"/v2/deployments/{v2['id']}/variations").json()
    assert listing["primary"]["id"] == p["id"] and [x["label"] for x in listing["variations"]] == ["wider stop", "small", "tight"]
    assert listing["max"] == 10 and listing["running"] == 3
    assert client.get(f"/deployments/{p['id']}").json()["variation_of"] is None


def test_refusals_say_why(client):
    p = make_deployment(client, name="Straddle Q")
    assert add(client, p["id"], " ").status_code == 409
    add(client, p["id"], "a")
    r = add(client, p["id"], "a")
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert add(client, "00000000-0000-0000-0000-000000000000", "x").status_code == 404
    assert add(client, p["id"], "neg", initial_capital=-5).status_code == 409


def test_at_most_ten_not_stopped(client, monkeypatch):
    monkeypatch.setattr(V, "MAX_PER_PRIMARY", 2)
    p = make_deployment(client, name="Straddle R")
    ids = [add(client, p["id"], f"v{i}").json()["id"] for i in range(2)]
    r = add(client, p["id"], "v2")
    assert r.status_code == 409 and "already has 2 variations" in r.json()["detail"]
    client.post(f"/deployments/{ids[0]}/stop")
    assert add(client, p["id"], "v2").status_code == 201, "a stopped one frees a place"


def test_a_variation_that_cant_start_leaves_nothing_behind(client):
    p = make_deployment(client, name="Straddle S")
    r = add(client, p["id"], "broken", run="now", cfg={"instrument_tokens": []})
    assert r.status_code == 400
    assert not [d for d in client.get("/deployments").json() if d["deployment_name"] == "Straddle S · broken"]


def test_its_alerts_are_the_critical_ones_only(client, pushes, monkeypatch):
    from tests.api.test_runner import runner_of
    p = make_deployment(client, name="Straddle T", start="now")
    v = add(client, p["id"], "loud", run="now").json()
    for d in (p, v):
        monkeypatch.setattr(runner_of(client, d["id"]), "push_config", {"vapid": "test"})
        client.portal.call(runner_of(client, d["id"]).notify_execution, "entry", "Sold CE+PE")
    assert [t for t, _ in pushes] == ["Straddle T — Entry"], "the variation's entry goes to its log"
    # a variation that isn't running still alerts
    monkeypatch.setattr(mgr(client), "push_config", {"vapid": "test"})
    from app import ops_health
    from datetime import datetime
    from zoneinfo import ZoneInfo
    down = ops_health.Problem(f"dep:{v['id']}:down", "not_running", "Straddle T · loud isn't running", "x", deployment_id=v["id"])
    client.portal.call(client.app.state.ops._sync, datetime(2026, 10, 14, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata")), [down])
    assert "Straddle T · loud isn't running" in [t for t, _ in pushes]


def test_make_primary_swaps_the_two(client):
    p = make_deployment(client, name="Straddle U")
    client.patch(f"/deployments/{p['id']}", json={"alerts_off": ["drawdown"]})
    a = add(client, p["id"], "a").json()["id"]
    b = add(client, p["id"], "b").json()["id"]
    r = client.post(f"/v2/deployments/{b}/make-primary")
    assert r.status_code == 200
    get = lambda i: client.get(f"/deployments/{i}").json()  # noqa: E731
    nb, np_, na = get(b), get(p["id"]), get(a)
    assert nb["variation_of"] is None and nb["include_in_reports"] is True and nb["alerts_off"] == ["drawdown"]
    assert np_["variation_of"] == b and np_["variation_label"] == "Original" and np_["include_in_reports"] is False
    assert np_["alerts_off"] == list(alert_types.TYPES)
    assert na["variation_of"] == b, "the others follow the new primary"
    assert client.post(f"/v2/deployments/{b}/make-primary").status_code == 409, "already the primary"


def test_continuing_the_cycle_is_refused_until_it_exists(client):
    p = make_deployment(client, name="Straddle V")
    r = add(client, p["id"], "cont", start="continue")
    assert r.status_code == 409 and "start fresh" in r.json()["detail"]
    assert not [d for d in client.get("/deployments").json() if d["deployment_name"] == "Straddle V · cont"]


def test_the_evening_check_skips_variations_and_checks_one_when_asked(client, monkeypatch):
    p = make_deployment(client, name="Straddle W")
    v = add(client, p["id"], "w").json()["id"]
    seen = []

    async def check(pool, d, day, book, kite):
        seen.append(str(d["id"]))
        return {"status": "ok", "headline": "ok", "rules": [], "diff": {}, "replay": {}}
    monkeypatch.setattr(daycheck, "check_deployment", check)
    pool = client.app.state.db_pool
    client.portal.call(lambda: daycheck.run_day(pool, date(2026, 10, 14), None, None))
    assert p["id"] in seen and v not in seen
    seen.clear()
    client.portal.call(lambda: daycheck.run_day(pool, date(2026, 10, 14), None, None, only=[v]))
    assert seen == [v]


def test_a_variation_survives_a_restart_as_itself(client):
    p = make_deployment(client, name="Straddle X", start="now")
    v = add(client, p["id"], "x", run="now").json()["id"]
    m = mgr(client)
    client.portal.call(m.shutdown_all)
    m.runners.clear()
    client.portal.call(m.load_active_on_startup)
    assert v in m.runners and p["id"] in m.runners
    d = client.get(f"/deployments/{v}").json()
    assert d["variation_of"] == p["id"] and d["alerts_off"] == list(alert_types.TYPES) and d["include_in_reports"] is False
