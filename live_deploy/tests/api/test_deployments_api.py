"""The deployments API (v1): create, read, edit, lifecycle (pause,
resume, stop, flatten, remove trades, delete), bulk actions, and every
per-deployment read endpoint, with real fills behind them."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import ADMIN_PASSWORD, add_fill, default_config, make_deployment

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime.now(IST).replace(second=0, microsecond=0)


def body(name="D1", strategy="intraday_dtt_simple", **over):
    return {"deployment_name": name, "strategy_name": strategy, "mode": "intraday",
            "initial_capital": 500000, "config": default_config(strategy), **over}


def short_trade(client, dep_id, sym="NIFTYCE", tok=101, entry=100.0, exit_=80.0, opened=None, closed=True):
    opened = opened or NOW - timedelta(hours=2)
    add_fill(client, dep_id, sym, tok, "sell", 75, entry, opened, reason="entry", metadata={"leg_role": "original"})
    if closed:
        add_fill(client, dep_id, sym, tok, "buy", 75, exit_, opened + timedelta(minutes=30), reason="exit")


# ── Create / read / edit ──────────────────────────────────────────────

def test_create_list_get(client):
    r = client.post("/deployments", json=body())
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["deployment_name"] == "D1" and d["status"] == "active" and float(d["current_cash"]) == 500000
    assert [x["id"] for x in client.get("/deployments").json()] == [d["id"]]
    assert client.get(f"/deployments/{d['id']}").json()["strategy_name"] == "intraday_dtt_simple"


def test_create_validation(client):
    assert client.post("/deployments", json=body()).status_code == 201
    assert client.post("/deployments", json=body()).status_code == 409, "duplicate name"
    assert client.post("/deployments", json=body(name="Z", initial_capital=-5)).status_code == 422
    assert client.post("/deployments", json={"deployment_name": "x"}).status_code == 422


def test_unknown_deployment_is_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    for path in ["", "/positions", "/trades", "/events", "/report", "/snapshots", "/pnl-digest", "/cycles"]:
        assert client.get(f"/deployments/{missing}{path}").status_code == 404, path
    assert client.post(f"/deployments/{missing}/pause").status_code == 404


def test_patch_rename_notes_flags_and_config_only_when_paused(client):
    d = make_deployment(client, start="now")
    r = client.patch(f"/deployments/{d['id']}", json={"deployment_name": "Renamed", "notes": "why",
                                                     "include_in_reports": False, "notifications_enabled": False})
    assert r.status_code == 200
    got = r.json()
    assert got["deployment_name"] == "Renamed" and got["notes"] == "why"
    assert got["include_in_reports"] is False and got["notifications_enabled"] is False
    assert client.patch(f"/deployments/{d['id']}", json={"config": {"lots_per_trade": 2}}).status_code == 409
    client.post(f"/deployments/{d['id']}/pause")
    r = client.patch(f"/deployments/{d['id']}", json={"config": {**d.get("config", {}), "lots_per_trade": 2}})
    assert r.status_code == 200 and r.json()["config"]["lots_per_trade"] == 2


def test_rename_to_an_existing_name_is_refused(client):
    a = make_deployment(client, name="A")
    make_deployment(client, name="B")
    assert client.patch(f"/deployments/{a['id']}", json={"deployment_name": "B"}).status_code == 409


# ── Lifecycle ─────────────────────────────────────────────────────────

def test_pause_resume_toggle_status_and_the_runner(client):
    d = make_deployment(client, start="now")
    mgr = client.app.state.deployment_manager
    assert d["id"] in mgr.runners
    assert client.post(f"/deployments/{d['id']}/pause").status_code == 200
    assert client.get(f"/deployments/{d['id']}").json()["status"] == "paused" and d["id"] not in mgr.runners
    assert client.post(f"/deployments/{d['id']}/pause").status_code == 409, "can't pause twice"
    assert client.post(f"/deployments/{d['id']}/resume").status_code == 200
    assert client.get(f"/deployments/{d['id']}").json()["status"] == "active" and d["id"] in mgr.runners


def test_resume_that_fails_to_start_rolls_back_to_paused(client):
    d = make_deployment(client, strategy="intraday_dtt_adjusted")
    bad = {**default_config("intraday_dtt_adjusted"), "instrument_tokens": []}
    assert client.patch(f"/deployments/{d['id']}", json={"config": bad}).status_code == 200
    r = client.post(f"/deployments/{d['id']}/resume")
    assert r.status_code in (400, 409)
    assert client.get(f"/deployments/{d['id']}").json()["status"] == "paused"


def test_stop_with_open_positions_needs_force_close(client):
    d = make_deployment(client)
    short_trade(client, d["id"], closed=False)
    assert client.post(f"/deployments/{d['id']}/stop").status_code == 409
    assert client.post(f"/deployments/{d['id']}/stop?force_close=true").status_code == 200
    got = client.get(f"/deployments/{d['id']}").json()
    assert got["status"] == "stopped"
    assert client.get(f"/deployments/{d['id']}/positions?status=open").json() == []
    assert client.post(f"/deployments/{d['id']}/resume").status_code in (400, 409), "stopped is final"


def test_flatten_closes_everything_and_keeps_an_active_deployment_running(client):
    d = make_deployment(client, start="now")
    short_trade(client, d["id"], sym="A", tok=1, closed=False)
    short_trade(client, d["id"], sym="B", tok=2, closed=False)
    r = client.post(f"/deployments/{d['id']}/flatten")
    assert r.status_code == 200
    assert client.get(f"/deployments/{d['id']}/positions?status=open").json() == []
    assert client.get(f"/deployments/{d['id']}").json()["status"] == "active"
    assert d["id"] in client.app.state.deployment_manager.runners, "runner restarted from the flat book"


def test_flatten_all_closes_every_deployment_and_pauses_them(client):
    a = make_deployment(client, name="A", start="now")
    b = make_deployment(client, name="B", start="now")
    short_trade(client, a["id"], closed=False)
    short_trade(client, b["id"], closed=False)
    assert client.post("/deployments/flatten-all").status_code == 200
    for x in (a, b):
        assert client.get(f"/deployments/{x['id']}/positions?status=open").json() == []
        assert client.get(f"/deployments/{x['id']}").json()["status"] == "paused"


def test_remove_trades_erases_the_open_cycle(client):
    d = make_deployment(client)
    short_trade(client, d["id"], sym="OLD", tok=1, opened=NOW - timedelta(days=2))
    short_trade(client, d["id"], sym="NEW", tok=2, closed=False)
    r = client.post(f"/deployments/{d['id']}/remove-trades")
    assert r.status_code == 200
    syms = [p["symbol"] for p in client.get(f"/deployments/{d['id']}/positions?status=all").json()]
    assert syms == ["OLD"]


def test_delete_only_after_stop(client):
    d = make_deployment(client)
    assert client.post(f"/deployments/{d['id']}/delete").status_code == 409
    client.post(f"/deployments/{d['id']}/stop?force_close=true")
    assert client.post(f"/deployments/{d['id']}/delete").status_code == 200
    assert client.get(f"/deployments/{d['id']}").status_code == 404


@pytest.mark.parametrize("pw,phrase,code", [("wrong", "DELETE ALL", 401), (ADMIN_PASSWORD, "delete all", 400)])
def test_clear_all_is_guarded(client, pw, phrase, code):
    make_deployment(client)
    assert client.post("/deployments/clear-all", json={"password": pw, "confirm": phrase}).status_code == code
    assert len(client.get("/deployments").json()) == 1


def test_clear_all_with_password_and_phrase(client):
    make_deployment(client, name="A")
    make_deployment(client, name="B")
    r = client.post("/deployments/clear-all", json={"password": ADMIN_PASSWORD, "confirm": "DELETE ALL"})
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert client.get("/deployments").json() == []


# ── Read endpoints with data ──────────────────────────────────────────

def test_positions_trades_events_report_with_real_fills(client):
    d = make_deployment(client)
    short_trade(client, d["id"], sym="A", tok=1, entry=100, exit_=80)
    short_trade(client, d["id"], sym="B", tok=2, closed=False)
    pos = client.get(f"/deployments/{d['id']}/positions?status=all").json()
    assert {p["symbol"]: p["status"] for p in pos} == {"A": "closed", "B": "open"}
    closed = next(p for p in pos if p["symbol"] == "A")
    assert float(closed["realized_pnl"]) == pytest.approx(1500)
    assert float(closed["realized_pnl_net"]) < 1500
    lots = client.get(f"/deployments/{d['id']}/trades?limit=10").json()
    assert len(lots["lots"]) == 3
    rep = client.get(f"/deployments/{d['id']}/report").json()
    assert rep["closed_positions"] == 1 and rep["open_positions"] == 1
    assert client.get(f"/deployments/{d['id']}/events").status_code == 200
    dep = client.get(f"/deployments/{d['id']}").json()
    assert float(dep["realized_pnl"]) == pytest.approx(1500)


def test_pnl_digest_snapshots_histogram_status(client):
    d = make_deployment(client, strategy="intraday_dtt_adjusted")
    short_trade(client, d["id"])
    dig = client.get(f"/deployments/{d['id']}/pnl-digest?period=day").json()
    assert dig and float(dig[0]["realized_pnl"]) == pytest.approx(1500)
    assert client.get(f"/deployments/{d['id']}/pnl-digest?period=decade").status_code == 400
    assert client.get(f"/deployments/{d['id']}/snapshots").status_code == 200
    hist = client.get(f"/deployments/{d['id']}/adjustment-histogram").json()
    assert hist["supported"] is True and hist["group_by"] == "day"
    st = client.get(f"/deployments/{d['id']}/strategy-status").json()
    assert "source" in st and "fields" in st


def test_list_reflects_fills_without_waiting_for_the_cache(client):
    d = make_deployment(client, start="now")
    short_trade(client, d["id"])
    row = next(x for x in client.get("/deployments").json() if x["id"] == d["id"])
    assert float(row["realized_pnl"]) == pytest.approx(1500)
