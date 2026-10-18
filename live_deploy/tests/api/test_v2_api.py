"""The /v2 API the Linck UI is built on: the trade book, today's session
lanes, the strategy catalog, per-user preferences, close-everything,
create-with-start-mode and restart."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.strategies.registry import list_strategies
from tests.conftest import add_fill, default_config, make_deployment, refresh, sql

IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime.now(IST).date()


def at(day: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), IST)


def trade(client, dep_id, sym, tok, entry, exit_, opened, minutes=30, side="sell", reason="exit"):
    other = "buy" if side == "sell" else "sell"
    add_fill(client, dep_id, sym, tok, side, 75, entry, opened, reason="entry")
    if exit_ is not None:
        add_fill(client, dep_id, sym, tok, other, 75, exit_, opened + timedelta(minutes=minutes), reason=reason)


# ── Book ──────────────────────────────────────────────────────────────

def test_book_is_empty_without_trades(client):
    assert client.get("/v2/book").json() == {"trades": [], "daily": []}


def test_book_groups_legs_into_one_trade_and_days_into_daily_rows(client):
    d = make_deployment(client)
    day1 = TODAY - timedelta(days=3)
    # a strangle: two legs out together, closed together — one trade
    trade(client, d["id"], "CE", 1, 100, 80, at(day1, "10:00"))
    trade(client, d["id"], "PE", 2, 100, 110, at(day1, "10:00"))
    # a second, separate trade the next day, still open
    trade(client, d["id"], "CE2", 3, 90, None, at(day1 + timedelta(days=1), "10:00"))
    b = client.get("/v2/book").json()
    assert len(b["trades"]) == 1, "the open trade isn't in the book yet"
    t = b["trades"][0]
    assert t["deployment_id"] == d["id"] and t["legs"] == 2 and t["adjustments"] == 0
    assert t["pnl"] == pytest.approx(1500 - 750)
    assert t["pnl_net_slip"] <= t["pnl_net"] < t["pnl"]
    assert t["exit_reason"] == "exit" and t["wins"] == 1
    [row] = b["daily"]
    assert row["day"] == day1.isoformat() and row["pnl"] == pytest.approx(750) and row["closes"] == 2


def test_book_counts_a_later_open_as_an_adjustment(client):
    d = make_deployment(client)
    day1 = TODAY - timedelta(days=2)
    add_fill(client, d["id"], "CE", 1, "sell", 75, 100, at(day1, "10:00"), reason="entry")
    add_fill(client, d["id"], "PE", 2, "sell", 75, 100, at(day1, "10:00"), reason="entry")
    add_fill(client, d["id"], "PE", 2, "buy", 75, 40, at(day1, "12:00"), reason="adjust")
    add_fill(client, d["id"], "PE3", 3, "sell", 75, 90, at(day1, "12:00"), reason="adjust")
    for sym, tok in (("CE", 1), ("PE3", 3)):
        add_fill(client, d["id"], sym, tok, "buy", 75, 50, at(day1, "15:00"), reason="checkpoint")
    [t] = client.get("/v2/book").json()["trades"]
    assert t["legs"] == 3 and t["adjustments"] == 1 and t["exit_reason"] == "checkpoint"


def test_book_since_filters_by_close_time(client):
    d = make_deployment(client)
    trade(client, d["id"], "OLD", 1, 100, 90, at(TODAY - timedelta(days=10), "10:00"))
    trade(client, d["id"], "NEW", 2, 100, 90, at(TODAY - timedelta(days=1), "10:00"))
    since = at(TODAY - timedelta(days=5), "00:00").isoformat()
    b = client.get("/v2/book", params={"since": since}).json()
    assert len(b["trades"]) == 1 and len(b["daily"]) == 1
    assert len(client.get("/v2/book").json()["trades"]) == 2


def test_book_worst_point_comes_from_snapshots(client):
    d = make_deployment(client)
    t0 = at(TODAY - timedelta(days=1), "10:00")
    trade(client, d["id"], "CE", 1, 100, 90, t0, minutes=60)
    for minutes, v in ((5, 500000), (20, 497000), (40, 499000)):
        sql(client, "INSERT INTO deployment_snapshots (deployment_id, snapshot_at, cash, open_positions_value, total_value, realized_pnl_cumulative) "
                    "VALUES ($1::uuid, $2, 0, 0, $3, 0)", d["id"], t0 + timedelta(minutes=minutes), v)
    refresh(client)
    [t] = client.get("/v2/book").json()["trades"]
    assert t["worst"] == pytest.approx(-3000)


def test_book_includes_every_deployment(client):
    a = make_deployment(client, name="A")
    b = make_deployment(client, name="B", strategy="intraday_dtt_adjusted")
    for dep in (a, b):
        trade(client, dep["id"], "X", 1, 100, 90, at(TODAY - timedelta(days=1), "10:00"))
    ids = {t["deployment_id"] for t in client.get("/v2/book").json()["trades"]}
    assert ids == {a["id"], b["id"]}


# ── Session ───────────────────────────────────────────────────────────

def test_session_has_a_lane_per_deployment(client):
    a = make_deployment(client, name="A")
    s = client.get("/v2/session").json()
    assert s["date"] == TODAY.isoformat() and isinstance(s["open_at"], int) and isinstance(s["now"], int)
    assert set(s["lanes"]) == {a["id"]}
    lane = s["lanes"][a["id"]]
    assert lane["equity_open"] == {"gross": 0, "net": 0, "net_slip": 0}
    assert lane["series"] == []
    assert [e["kind"] for e in lane["events"]] == ["pause"], "created paused"


def test_session_marks_entries_exits_adjustments_and_rollouts(client):
    d = make_deployment(client)
    add_fill(client, d["id"], "CE", 1, "sell", 75, 100, at(TODAY, "09:30"), reason="entry")
    add_fill(client, d["id"], "PE", 2, "sell", 75, 100, at(TODAY, "09:30"), reason="entry")
    add_fill(client, d["id"], "PE", 2, "buy", 75, 60, at(TODAY, "11:00"), reason="adjust")
    add_fill(client, d["id"], "PE2", 3, "sell", 75, 95, at(TODAY, "11:00"), reason="adjust")
    add_fill(client, d["id"], "CE", 1, "buy", 75, 120, at(TODAY, "15:13"), reason="eod_rollout")
    add_fill(client, d["id"], "CE2", 4, "sell", 75, 90, at(TODAY, "15:13"), reason="eod_rollout")
    add_fill(client, d["id"], "PE2", 3, "buy", 75, 80, at(TODAY, "15:20"), reason="exit")
    kinds = [e["kind"] for e in client.get("/v2/session").json()["lanes"][d["id"]]["events"] if e["kind"] != "pause"]
    assert kinds == ["fill", "adjust", "rollout", "exit"]


def test_session_equity_open_counts_what_closed_before_today(client):
    d = make_deployment(client)
    trade(client, d["id"], "CE", 1, 100, 80, at(TODAY - timedelta(days=1), "10:00"))
    eo = client.get("/v2/session").json()["lanes"][d["id"]]["equity_open"]
    assert eo["gross"] == pytest.approx(1500)
    assert eo["net_slip"] <= eo["net"] < eo["gross"]


def test_session_shows_errors_and_pauses(client):
    d = make_deployment(client, start="now")
    assert client.post(f"/deployments/{d['id']}/pause").status_code == 200
    kinds = [e["kind"] for e in client.get("/v2/session").json()["lanes"][d["id"]]["events"]]
    assert "pause" in kinds


# ── Strategies ────────────────────────────────────────────────────────

def test_strategies_catalog_covers_the_registry_and_follows_enabled(client):
    cat = client.get("/v2/strategies").json()
    assert {s["name"] for s in cat} == {s["name"] for s in list_strategies()}
    for s in cat:
        assert s["enabled"] is True and s.get("label"), s["name"]
    assert client.put("/strategies/calendar_btst/enabled", json={"enabled": False}).status_code == 200
    cat = {s["name"]: s for s in client.get("/v2/strategies").json()}
    assert cat["calendar_btst"]["enabled"] is False


def test_disabled_strategy_cannot_be_deployed(client):
    client.put("/strategies/intraday_dtt_simple/enabled", json={"enabled": False})
    r = client.post("/v2/deployments", json={"deployment_name": "X", "strategy_name": "intraday_dtt_simple",
                                             "mode": "intraday", "initial_capital": 100000,
                                             "config": default_config("intraday_dtt_simple")})
    assert r.status_code == 400 and "disabled" in r.text
    client.put("/strategies/intraday_dtt_simple/enabled", json={"enabled": True})


# ── Preferences ───────────────────────────────────────────────────────

def test_preferences_round_trip_merge_and_clean(client):
    assert client.get("/v2/preferences").json() == {}
    r = client.put("/v2/preferences", json={"theme": "dark", "basis": "net", "density": 2})
    assert r.json() == {"theme": "dark", "basis": "net", "density": 2}
    r = client.put("/v2/preferences", json={"basis": None, "nested": {"x": 1}, "long": "x" * 200, "k" * 41: 1})
    assert r.json() == {"theme": "dark", "density": 2}, "None removes; non-scalars and oversized values dropped"
    assert client.get("/v2/preferences").json() == {"theme": "dark", "density": 2}


def test_preferences_are_per_user(client):
    client.put("/v2/preferences", json={"theme": "dark"})
    r = client.post("/auth/users", json={"username": "riya", "password": "riya-pass-123"}); assert r.status_code < 300, r.text
    r = client.post("/auth/logout"); assert r.status_code < 300, r.text
    r = client.post("/auth/login", json={"username": "riya", "password": "riya-pass-123"}); assert r.status_code < 300, r.text
    assert client.get("/v2/preferences").json() == {}


def test_preferences_need_a_session_user(app_client):
    from tests.conftest import ADMIN_PASSWORD
    h = {"X-API-Key": ADMIN_PASSWORD}
    assert app_client.get("/v2/preferences", headers=h).json() == {}
    assert app_client.put("/v2/preferences", json={"a": 1}, headers=h).status_code == 400


def test_preferences_cap_the_number_of_keys(client):
    r = client.put("/v2/preferences", json={f"k{i}": i for i in range(200)})
    assert len(r.json()) == 64


# ── Close everything ──────────────────────────────────────────────────

def test_close_everything_needs_the_typed_confirmation(client):
    d = make_deployment(client)
    trade(client, d["id"], "CE", 1, 100, None, at(TODAY, "09:30"))
    assert client.post("/v2/close-everything", json={"confirm": "close"}).status_code == 400
    assert client.post("/v2/close-everything", json={}).status_code == 422
    assert len(client.get(f"/deployments/{d['id']}/positions").json()) == 1


def test_close_everything_closes_shorts_before_longs_and_pauses_running(client):
    run = make_deployment(client, name="Running", start="now")
    parked = make_deployment(client, name="Parked")
    add_fill(client, run["id"], "WING", 9, "buy", 75, 10, at(TODAY, "09:30"))
    add_fill(client, run["id"], "SHORT", 1, "sell", 75, 100, at(TODAY, "09:30"))
    add_fill(client, parked["id"], "P", 2, "sell", 75, 50, at(TODAY, "09:30"))
    client.app.state.dispatcher.last_prices[1] = 80.0      # live price for one leg only
    r = client.post("/v2/close-everything", json={"confirm": " close all "})
    assert r.status_code == 200, r.text
    assert r.json() == {"positions_closed": 3, "deployments_closed": 2, "deployments_paused": 1, "errors": []}
    for x in (run, parked):
        assert client.get(f"/deployments/{x['id']}/positions").json() == []
        assert client.get(f"/deployments/{x['id']}").json()["status"] == "paused"
    assert run["id"] not in client.app.state.deployment_manager.runners
    closed = {p["symbol"]: p for p in client.get(f"/deployments/{run['id']}/positions?status=closed").json()}
    assert float(closed["SHORT"]["realized_pnl"]) == pytest.approx(1500), "closed at the live price"
    assert float(closed["WING"]["realized_pnl"]) == pytest.approx(0), "no price: closed at entry"
    assert closed["SHORT"]["closed_at"] <= closed["WING"]["closed_at"], "shorts first"


def test_close_everything_skips_stopped_deployments(client):
    d = make_deployment(client)
    client.post(f"/deployments/{d['id']}/stop")
    r = client.post("/v2/close-everything", json={"confirm": "CLOSE ALL"}).json()
    assert r["positions_closed"] == 0 and r["deployments_paused"] == 0
    assert client.get(f"/deployments/{d['id']}").json()["status"] == "stopped"


# ── Create ────────────────────────────────────────────────────────────

def body(name="W", strategy="intraday_dtt_simple", **over):
    return {"deployment_name": name, "strategy_name": strategy, "mode": "intraday",
            "initial_capital": 300000, "config": default_config(strategy), **over}


def test_create_paused_does_not_run(client):
    r = client.post("/v2/deployments", json=body())
    assert r.status_code == 201 and r.json()["status"] == "paused" and r.json()["strategy_registered"] is True
    assert r.json()["id"] not in client.app.state.deployment_manager.runners


def test_create_now_runs(client):
    r = client.post("/v2/deployments", json=body(start="now"))
    assert r.status_code == 201 and r.json()["status"] == "active"
    assert r.json()["id"] in client.app.state.deployment_manager.runners
    assert any(x["id"] == r.json()["id"] for x in client.get("/deployments").json()), "list refreshed"


def test_create_with_tags(client):
    assert client.post("/tags", json={"name": "Experiment"}).status_code == 201
    assert client.post("/v2/deployments", json=body(tags=["Nope"])).status_code == 400
    r = client.post("/v2/deployments", json=body(tags=["Experiment"]))
    assert r.status_code == 201
    assert client.get(f"/deployments/{r.json()['id']}").json()["tags"] == ["Experiment"]


def test_create_validation(client):
    assert client.post("/v2/deployments", json=body()).status_code == 201
    assert client.post("/v2/deployments", json=body()).status_code == 409
    assert client.post("/v2/deployments", json=body(name="B", start="later")).status_code == 422
    assert client.post("/v2/deployments", json=body(name="C", initial_capital=0)).status_code == 422
    bad = body(name="D", start="now", config={**default_config("intraday_dtt_simple"), "instrument_tokens": []})
    r = client.post("/v2/deployments", json=bad)
    assert r.status_code == 400 and "instrument_tokens" in r.text
    names = [x["deployment_name"] for x in client.get("/deployments").json()]
    assert "D" not in names, "a deployment that can't start isn't left behind"


@pytest.mark.parametrize("strategy", sorted(s["name"] for s in list_strategies()))
def test_every_strategy_can_be_created_from_its_defaults(client, strategy):
    mode = next(s for s in list_strategies() if s["name"] == strategy).get("mode") or "intraday"
    r = client.post("/v2/deployments", json=body(name=strategy, strategy=strategy, mode=mode))
    assert r.status_code == 201, r.text


# ── Restart ───────────────────────────────────────────────────────────

def _kill_runner(client, dep_id):
    """The runner dies; the watchdog's next pass notices."""
    mgr = client.app.state.deployment_manager

    async def go():
        runner = mgr.runners.pop(dep_id)
        await runner.stop()
        await mgr.check_health_once(datetime.now(IST).replace(tzinfo=None))
    client.portal.call(go)


def test_restart_brings_back_a_dead_runner_and_clears_health(client):
    d = make_deployment(client, start="now")
    _kill_runner(client, d["id"])
    h = client.get("/health").json()
    assert [x["id"] for x in h["not_running"]] == [d["id"]]
    r = client.post(f"/v2/deployments/{d['id']}/restart")
    assert r.status_code == 200 and r.json() == {"ok": True, "running": True}
    assert d["id"] in client.app.state.deployment_manager.runners
    assert client.get("/health").json()["not_running"] == []
    events = [e["event_type"] for e in client.get(f"/deployments/{d['id']}/events").json()]
    assert "restarted" in events


def test_restart_of_a_running_deployment_is_a_no_op(client):
    d = make_deployment(client, start="now")
    assert client.post(f"/v2/deployments/{d['id']}/restart").status_code == 200
    assert d["id"] in client.app.state.deployment_manager.runners


def test_restart_refuses_paused_and_unknown(client):
    d = make_deployment(client)
    assert client.post(f"/v2/deployments/{d['id']}/restart").status_code == 409
    assert client.post("/v2/deployments/00000000-0000-0000-0000-000000000000/restart").status_code == 404


def test_restart_that_still_fails_reports_why(client):
    d = make_deployment(client, start="now")
    _kill_runner(client, d["id"])
    sql(client, "UPDATE deployments SET config = config || '{\"instrument_tokens\": []}'::jsonb WHERE id = $1::uuid", d["id"])
    r = client.post(f"/v2/deployments/{d['id']}/restart")
    assert r.status_code == 409 and "instrument_tokens" in r.text
    client.portal.call(lambda: client.app.state.deployment_manager.check_health_once(datetime.now(IST).replace(tzinfo=None)))
    [nr] = client.get("/health").json()["not_running"]
    assert "instrument_tokens" in nr["reason"]
