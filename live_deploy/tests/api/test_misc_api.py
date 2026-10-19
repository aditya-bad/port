"""Everything outside deployments and /v2: health, notifications and
mute, strategies (enable, presets), tags, Kite login, instruments, the
portfolio reads, cycles and their charts, and the redeploy guard."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import ADMIN_PASSWORD, add_fill, make_deployment, sql

IST = ZoneInfo("Asia/Kolkata")
TODAY = datetime.now(IST).date()


def last_weekday(before):
    d = before - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


PAST = last_weekday(TODAY)


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), IST)


# ── Health ────────────────────────────────────────────────────────────

def test_health_reports_db_and_runners(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["database_connected"] is True
    assert h["running_deployments"] == 0 and h["not_running"] == []
    assert "needs_login" in h
    make_deployment(client, start="now")
    assert client.get("/health").json()["running_deployments"] == 1


def test_health_needs_a_session(app_client):
    assert app_client.get("/health").status_code == 401


# ── Notifications ─────────────────────────────────────────────────────

def test_vapid_key_is_null_when_push_isnt_configured(client):
    assert client.get("/notifications/vapid-public-key").json() == {"public_key": None}


def test_subscribe_is_an_upsert_and_unsubscribe_is_idempotent(client):
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert client.post("/notifications/subscribe", json=sub).status_code == 204
    assert client.post("/notifications/subscribe", json=sub).status_code == 204
    assert len(sql(client, "SELECT 1 FROM push_subscriptions")) == 1
    assert client.post("/notifications/unsubscribe", json={"endpoint": sub["endpoint"]}).status_code == 204
    assert client.post("/notifications/unsubscribe", json={"endpoint": sub["endpoint"]}).status_code == 204
    assert sql(client, "SELECT 1 FROM push_subscriptions") == []
    assert client.post("/notifications/subscribe", json={"endpoint": "x"}).status_code == 422


def test_mute_today_until_7am_tomorrow_and_unmute(client):
    assert client.get("/notifications/mute-status").json() == {"muted": False, "muted_until": None}
    r = client.post("/notifications/mute-today").json()
    until = datetime.fromisoformat(r["muted_until"]).astimezone(IST)
    assert r["muted"] is True and until.date() == TODAY + timedelta(days=1) and until.time() == time(7, 0)
    assert client.get("/notifications/mute-status").json()["muted"] is True
    assert client.post("/notifications/unmute").json() == {"muted": False, "muted_until": None}
    assert client.get("/notifications/mute-status").json()["muted"] is False


# ── Strategies ────────────────────────────────────────────────────────

def test_strategies_list_and_enable_toggle(client):
    names = {s["name"] for s in client.get("/strategies").json()}
    assert "strangle_monthly_v2" in names and "intraday_dtt_simple" in names
    assert client.put("/strategies/nope/enabled", json={"enabled": False}).status_code == 404
    assert client.put("/strategies/calendar_btst/enabled", json={"enabled": False}).json() == \
        {"strategy_name": "calendar_btst", "enabled": False}
    got = {s["name"]: s["enabled"] for s in client.get("/strategies").json()}
    assert got["calendar_btst"] is False, "the cached list refreshes straight away"


def test_presets_crud(client):
    base = "/strategies/intraday_dtt_simple/presets"
    assert client.get(base).json() == []
    r = client.post(base, json={"preset_name": " Tight ", "config": {"lots_per_trade": 2}})
    assert r.status_code == 200 and r.json()["preset_name"] == "Tight"
    pid = r.json()["id"]
    assert client.post(base, json={"preset_name": "Tight", "config": {}}).status_code == 409
    assert client.post(base, json={"preset_name": "  ", "config": {}}).status_code == 400
    assert [p["config"] for p in client.get(base).json()] == [{"lots_per_trade": 2}]
    assert client.delete(f"/strategies/strangle_monthly_v2/presets/{pid}").status_code == 404, "other strategy's preset"
    assert client.delete(f"{base}/{pid}").status_code == 200
    assert client.get(base).json() == []


# ── Tags ──────────────────────────────────────────────────────────────

def test_tags_crud_and_delete_strips_them_from_deployments(client):
    r = client.post("/tags", json={"name": "Live"})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert client.post("/tags", json={"name": "Live"}).status_code == 409
    assert client.post("/tags", json={"name": " "}).status_code == 400
    assert client.post("/tags", json={"name": "Excluded From Reports"}).status_code == 400
    d = make_deployment(client, tags=["Live"])
    assert client.get(f"/deployments/{d['id']}").json()["tags"] == ["Live"]
    assert client.delete(f"/tags/{tid}").status_code == 204
    assert client.delete(f"/tags/{tid}").status_code == 404
    assert client.get("/tags").json() == []
    assert client.get(f"/deployments/{d['id']}").json()["tags"] == []


# ── Kite login ────────────────────────────────────────────────────────

class FakeKiteConnect:
    calls = []

    def __init__(self, api_key):
        self.api_key = api_key

    def login_url(self):
        return f"https://kite.example/connect/login?api_key={self.api_key}"

    def generate_session(self, request_token, api_secret):
        FakeKiteConnect.calls.append((self.api_key, request_token, api_secret))
        if request_token == "bad":
            raise Exception("Token is invalid or has expired.")
        return {"access_token": f"access-{request_token}", "login_time": datetime.now(timezone.utc)}


@pytest.fixture
def kite(client, monkeypatch):
    import app.routers.kite_auth as kite_auth
    FakeKiteConnect.calls = []
    reconnects = []
    monkeypatch.setattr(kite_auth, "KiteConnect", FakeKiteConnect)
    monkeypatch.setattr(client.app.state.dispatcher, "reconnect", reconnects.append)
    return reconnects


def test_kite_login_url_uses_the_configured_key(client, kite):
    assert client.get("/kite/login-url").json()["login_url"].endswith("api_key=test-key")


def test_kite_manual_login_stores_the_token_and_reconnects(client, kite):
    r = client.post("/kite/manual-login", json={"request_token": "rt1"})
    assert r.status_code == 200 and set(r.json()) == {"kite_connected", "needs_login", "last_error"}
    assert FakeKiteConnect.calls == [("test-key", "rt1", "test-secret")]
    assert kite == ["access-rt1"]
    assert sql(client, "SELECT access_token FROM kite_sessions")[0]["access_token"] == "access-rt1"


def test_kite_manual_login_override_credentials_are_used_once_and_never_kept(client, kite):
    assert client.post("/kite/manual-login", json={"request_token": "r", "api_key": "k2"}).status_code == 400
    r = client.post("/kite/manual-login", json={"request_token": "rt2", "api_key": "k2", "api_secret": "s2"})
    assert r.status_code == 200
    assert FakeKiteConnect.calls == [("k2", "rt2", "s2")]
    assert client.app.state.kite_config["api_key"] == "test-key"
    rows = client.get("/auth/audit-log").json()
    assert "s2" not in str([x["request_body"] for x in rows]), "the typed secret isn't in the audit log"


def test_kite_manual_login_failure_is_a_400_and_changes_nothing(client, kite):
    r = client.post("/kite/manual-login", json={"request_token": "bad"})
    assert r.status_code == 400 and "expired" in r.text
    assert kite == [] and sql(client, "SELECT 1 FROM kite_sessions") == []


def test_kite_callback_success_and_failure_pages(client, kite):
    r = client.get("/kite/callback?status=success&request_token=rt3")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and kite == ["access-rt3"]
    assert client.get("/kite/callback?status=cancelled").status_code == 400
    assert client.get("/kite/callback?status=success&request_token=bad").status_code == 400


def test_kite_status(client):
    s = client.get("/kite/status").json()
    assert s["kite_connected"] is False


# ── Instruments ───────────────────────────────────────────────────────

def test_instruments_list_and_input_checks(client):
    got = client.get("/instruments").json()
    assert "subscribed" in got and "tick_mode" in got
    assert client.get("/instruments/search?q=N").status_code == 400
    assert client.get("/instruments/search?q=NIFTY").status_code == 400, "no Kite session: a clear 400"
    assert client.get("/instruments/quotes?tokens=a,b").status_code == 400
    assert client.get("/instruments/quotes?tokens=").status_code == 400
    assert client.get("/instruments/quotes?tokens=256265").status_code == 400


# ── Portfolio / aggregate ─────────────────────────────────────────────

def seed_two(client):
    a = make_deployment(client, name="A")
    b = make_deployment(client, name="B", strategy="intraday_dtt_adjusted")
    add_fill(client, a["id"], "CE", 1, "sell", 75, 100, at(PAST, "10:00"), reason="entry")
    add_fill(client, a["id"], "CE", 1, "buy", 75, 80, at(PAST, "11:00"), reason="exit")
    add_fill(client, b["id"], "PE", 2, "sell", 75, 50, at(TODAY, "09:20"), reason="entry")
    return a, b


def test_aggregate_positions_open_closed_all(client):
    a, b = seed_two(client)
    client.app.state.dispatcher.last_prices[2] = 40.0
    from tests.conftest import refresh
    refresh(client)
    [o] = client.get("/positions").json()
    assert o["deployment_name"] == "B" and o["symbol"] == "PE"
    closed = client.get("/positions?status=closed").json()
    assert [c["symbol"] for c in closed] == ["CE"]
    assert closed[0]["realized_pnl_net"] < float(closed[0]["realized_pnl"]) == pytest.approx(1500)
    everything = client.get("/positions?status=").json()
    assert len(everything) == 2
    live = next(p for p in everything if p["symbol"] == "PE")
    assert live["unrealized_pnl"] == pytest.approx(750)
    assert live["unrealized_pnl_net_slip"] <= live["unrealized_pnl_net"] < 750


def test_recent_trades_digest_report_leaderboard(client):
    a, b = seed_two(client)
    recent = client.get("/trades/recent?limit=10").json()
    assert len(recent) == 3
    dig = client.get("/portfolio/pnl-digest?period=day").json()
    assert sum(float(r["realized_pnl"]) for r in dig) == pytest.approx(1500)
    assert client.get("/portfolio/pnl-digest?period=decade").status_code == 422
    rep = client.get("/portfolio/pnl-report?period=all").json()
    assert rep
    assert client.get("/portfolio/pnl-report?period=day&offset=-1").status_code == 422
    assert client.get("/portfolio/pnl-report?period=fortnight").status_code == 422
    for p in ("day", "week", "month", "year"):
        assert client.get(f"/portfolio/pnl-report?period={p}&offset=1").status_code == 200, p
    board = {r["strategy_name"]: r for r in client.get("/portfolio/strategy-leaderboard").json()}
    assert float(board["intraday_dtt_simple"]["realized_pnl"]) == pytest.approx(1500)
    assert client.get("/portfolio/equity-curve").status_code == 200


def test_excluded_deployment_drops_out_of_reports(client):
    a, b = seed_two(client)
    client.patch(f"/deployments/{a['id']}", json={"include_in_reports": False})
    from tests.conftest import refresh
    refresh(client)
    dig = client.get("/portfolio/pnl-digest?period=day").json()
    assert sum(float(r["realized_pnl"]) for r in dig) == 0
    names = [r["strategy_name"] for r in client.get("/portfolio/strategy-leaderboard").json()]
    assert "intraday_dtt_simple" not in names


def test_active_periods_summary(client):
    a, b = seed_two(client)
    rows = {r["deployment_id"]: r for r in client.get("/portfolio/active-periods").json()}
    assert set(rows) == {a["id"], b["id"]}


# ── Cycles ────────────────────────────────────────────────────────────

def test_cycles_list_and_chart_with_recorded_underlying(client, monkeypatch):
    """Legs from the store; the index straight from Kite (never stored)."""
    import app.candles as C

    async def history(resolver, token, start, end, interval):
        assert token == 256265 and interval == "minute"
        return [{"date": start.replace(tzinfo=IST) + timedelta(minutes=i), "open": 25000 + 15 + i, "high": 25020 + i,
                 "low": 25010 + i, "close": 25016 + i} for i in range(int((end - start).total_seconds() // 60))]
    monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", history)
    d = make_deployment(client)
    token = int(client.get(f"/deployments/{d['id']}").json()["config"]["instrument_tokens"][0])
    add_fill(client, d["id"], "NIFTY25000CE", 11, "sell", 75, 100, at(PAST, "09:30"), reason="entry")
    add_fill(client, d["id"], "NIFTY25000PE", 12, "sell", 75, 100, at(PAST, "09:30"), reason="entry")
    add_fill(client, d["id"], "NIFTY25000CE", 11, "buy", 75, 70, at(PAST, "15:15"), reason="exit")
    add_fill(client, d["id"], "NIFTY25000PE", 12, "buy", 75, 90, at(PAST, "15:15"), reason="exit")
    [c] = client.get(f"/deployments/{d['id']}/cycles").json()
    assert c["status"] == "closed" and c["positions"] == 2 and c["fills"] == 4
    assert c["realized_pnl"] == pytest.approx(3000)
    ch = client.get(f"/deployments/{d['id']}/cycles/{c['key']}/chart").json()
    assert ch["underlying_source"] == "kite" and ch["underlying"]
    assert sql(client, "SELECT 1 FROM candles_1m WHERE instrument_token = 256265") == [], "the index is never stored"
    assert ch["cycle"]["mode"] == "intraday"
    assert {p["symbol"] for p in ch["positions"]} == {"NIFTY25000CE", "NIFTY25000PE"}
    assert ch["stats"]["realized_pnl"] == pytest.approx(3000)
    assert ch["stats"]["underlying"]["open"] == pytest.approx(25000 + 15, abs=20)
    assert client.get(f"/deployments/{d['id']}/cycles/nope/chart").status_code == 404


def test_cycle_chart_without_candles_or_kite_explains_why(client):
    d = make_deployment(client)
    add_fill(client, d["id"], "X", 11, "sell", 75, 100, at(PAST, "09:30"), reason="entry")
    add_fill(client, d["id"], "X", 11, "buy", 75, 70, at(PAST, "10:30"), reason="exit")
    [c] = client.get(f"/deployments/{d['id']}/cycles").json()
    ch = client.get(f"/deployments/{d['id']}/cycles/{c['key']}/chart").json()
    assert ch["underlying"] == [] and "logged in" in ch["underlying_error"]
    assert ch["positions"][0]["price_source"] == "none"


def test_cycles_mark_eod_rollouts(client):
    d = make_deployment(client, name="S", strategy="strangle_monthly_v2", mode="positional")
    add_fill(client, d["id"], "CE1", 1, "sell", 75, 100, at(PAST, "10:00"), reason="entry")
    add_fill(client, d["id"], "PE1", 2, "sell", 75, 100, at(PAST, "10:00"), reason="entry")
    add_fill(client, d["id"], "PE1", 2, "buy", 75, 150, at(PAST, "15:13"), reason="eod_rollout")
    add_fill(client, d["id"], "PE2", 3, "sell", 75, 100, at(PAST, "15:13"), reason="eod_rollout")
    [c] = client.get(f"/deployments/{d['id']}/cycles").json()
    assert c["status"] == "open" and c["rollouts"] == 1 and c["adjustments"] >= 1


# ── Admin ─────────────────────────────────────────────────────────────

def test_redeploy_is_guarded(client, tmp_path, monkeypatch):
    import app.routers.admin as admin
    trig = tmp_path / "control" / "redeploy.trigger"
    monkeypatch.setattr(admin, "REDEPLOY_TRIGGER_PATH", trig)
    assert client.post("/admin/redeploy", json={"password": "x", "confirm": "REDEPLOY"}).status_code == 401
    assert client.post("/admin/redeploy", json={"password": ADMIN_PASSWORD, "confirm": "redeploy"}).status_code == 400
    assert client.post("/admin/redeploy", json={"password": ADMIN_PASSWORD, "confirm": "REDEPLOY"}).status_code == 503
    assert not trig.exists()
    trig.parent.mkdir()
    r = client.post("/admin/redeploy", json={"password": ADMIN_PASSWORD, "confirm": "REDEPLOY"})
    assert r.status_code == 200 and trig.exists()
    rows = client.get("/auth/audit-log").json()
    assert ADMIN_PASSWORD not in str([x["request_body"] for x in rows])


def test_expired_legs_chart_from_stored_candles_without_kite(client, monkeypatch):
    """A contract long expired (Kite has nothing) still draws its own
    price line from the candle store; Kite is never asked for it."""
    import app.candles as C
    asked = []

    async def history(resolver, token, start, end, interval):     # Kite logged in, nothing to give
        asked.append(token)
        return []
    monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", history)
    d = make_deployment(client)
    sym = "NIFTY26O0625000CE"                       # expired 6 Oct
    day = last_weekday(TODAY - timedelta(days=14))
    add_fill(client, d["id"], sym, 4242, "sell", 75, 100, at(day, "10:00"), reason="entry")
    add_fill(client, d["id"], sym, 4242, "buy", 75, 80, at(day, "11:00"), reason="exit")
    rows = [(4242, at(day, "09:15") + timedelta(minutes=i), 100 - i / 10, 101, 99, 100 - i / 10, None) for i in range(375)]
    from app.db import queries
    pool = client.app.state.db_pool
    client.portal.call(lambda: queries.upsert_candles(pool, rows, "import"))
    [c] = client.get(f"/deployments/{d['id']}/cycles").json()
    ch = client.get(f"/deployments/{d['id']}/cycles/{c['key']}/chart").json()
    [leg] = ch["positions"]
    assert leg["price_source"] == "stored"
    assert 4242 not in asked, "an expired contract is never requested from Kite"
    assert asked, "the (listed) underlying was asked for"


def test_candles_api_rollup_and_coverage(client):
    from app.db import queries
    pool = client.app.state.db_pool
    day = PAST
    client.portal.call(lambda: queries.upsert_contracts(pool, [{"instrument_token": 5151, "tradingsymbol": "NIFTY26DEC25100CE",
                                                                "instrument_type": "CE"}]))
    rows = [(5151, at(day, "09:15") + timedelta(minutes=i), 100, 101, 99, 100 + i, None) for i in range(375)]
    client.portal.call(lambda: queries.upsert_candles(pool, rows, "ticks"))
    r = client.get("/candles/5151", params={"start": at(day, "09:15").isoformat(), "end": at(day, "15:30").isoformat(),
                                            "interval": "15minute", "fetch": "false"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "stored" and len(body["candles"]) == 25 and body["candles"][0]["c"] == 114
    assert client.get("/candles/5151", params={"start": at(day, "09:15").isoformat(), "end": at(day, "15:30").isoformat(),
                                               "interval": "7minute"}).status_code == 400
    d = make_deployment(client)
    add_fill(client, d["id"], "NIFTY26O2725000CE", 6161, "sell", 75, 100, at(day, "10:00"))
    cov = client.get("/candles", params={"day": day.isoformat()}).json()
    by = {i["instrument_token"]: i for i in cov["instruments"]}
    assert by[5151]["minutes"] == 375 and by[5151]["sources"] == ["ticks"] and not by[5151]["held"]
    assert by[6161]["held"] and by[6161]["minutes"] == 0


def test_held_contracts_are_recorded_live(client):
    d = make_deployment(client, start="now")
    add_fill(client, d["id"], "NIFTY26O2725000CE", 7171, "sell", 75, 100, at(TODAY, "09:30"))
    mgr = client.app.state.deployment_manager
    client.portal.call(mgr.runners[d["id"]]._reload_positions)
    assert mgr.watched_tokens() == {7171}, "held option contracts only: the index is never stored"


def test_backfill_status_route_is_not_taken_for_an_instrument_token(client):
    """Regression: GET /candles/backfill was matched by /candles/{instrument_token} (422)."""
    r = client.get("/candles/backfill")
    assert r.status_code == 200 and set(r.json()) >= {"running", "last", "next_at", "stored"}


def _wait_backfill(client):
    import time as _t
    for _ in range(100):
        st = client.get("/candles/backfill").json()
        if st["running"] is None:
            return st
        _t.sleep(0.05)
    raise AssertionError("backfill never finished")


def _start_backfill(client, day):
    """POST a backfill once the app's own scheduled run (which catches up a
    missed day at start-up) isn't holding the slot; asserts it was ours."""
    _wait_backfill(client)
    r = client.post("/candles/backfill", json={"day": day.isoformat()})
    assert r.status_code == 202, r.text
    return r


def _running(client, n):
    """The run's progress once it has counted its contracts."""
    import time as _t
    for _ in range(100):
        st = client.get("/candles/backfill").json()["running"]
        if st and st["total"] == n:
            return st
        _t.sleep(0.05)
    raise AssertionError("the run never counted its contracts")


def test_backfill_runs_in_the_background_and_reports_progress(client, monkeypatch):
    import asyncio
    import app.candles as C
    asked, gate = [], {"open": False}

    async def history(resolver, token, start, end, interval):
        asked.append(token)
        while not gate["open"]:                       # hold the run so its progress can be seen
            await asyncio.sleep(0.01)
        return [{"date": start.replace(tzinfo=IST) + timedelta(minutes=i), "open": 1, "high": 1, "low": 1, "close": 1}
                for i in range(375)]
    monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", history)
    a, b = make_deployment(client, name="A"), make_deployment(client, name="B")
    for d in (a, b):                                    # two deployments, one contract
        add_fill(client, d["id"], "NIFTY26DEC25000CE", 8181, "sell", 75, 100, at(PAST, "10:00"))
    add_fill(client, a["id"], "NIFTY26DEC25000PE", 8182, "sell", 75, 100, at(PAST, "10:00"))
    r = _start_backfill(client, PAST)
    assert r.json()["running"]["day"] == PAST.isoformat(), "marked running from the moment it starts"
    running = _running(client, 2)
    assert running["day"] == PAST.isoformat()
    h = client.get("/health").json()["candle_backfill"]
    assert h is not None and h["total"] == 2, "the header learns of it from /health"
    assert client.post("/candles/backfill", json={}).status_code == 409, "one run at a time"
    gate["open"] = True
    st = _wait_backfill(client)
    assert asked.count(8181) == 1 and asked.count(8182) == 1
    last = st["last"]
    assert last["day"] == PAST.isoformat() and last["instruments"] == 2 and last["fetched"] == 2
    assert last["rows"] == 750 and last["complete"] is True and last["error"] is None
    assert st["stored"]["contracts"] == 2 and st["stored"]["minutes"] == 750
    assert client.get("/health").json()["candle_backfill"] is None
    # again: nothing left to fetch
    _start_backfill(client, PAST)
    assert _wait_backfill(client)["last"]["fetched"] == 0


def test_backfill_without_kite_is_reported_as_incomplete(client, monkeypatch):
    import app.candles as C

    async def no_session(*a):
        raise C.NoKiteSession("no session")
    monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", no_session)
    d = make_deployment(client)
    add_fill(client, d["id"], "NIFTY26DEC25000CE", 8191, "sell", 75, 100, at(PAST, "10:00"))
    _start_backfill(client, PAST)
    last = _wait_backfill(client)["last"]
    assert last["complete"] is False and "Kite" in last["error"]
