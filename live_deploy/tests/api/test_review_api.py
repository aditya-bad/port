"""GET /v2/review through real fills, and the Saturday push sent once
(the arithmetic: tests/unit/test_review.py). Weeks in 2025, which no other
test trades in."""

from datetime import datetime, timedelta

from tests.conftest import add_fill, make_deployment

T = datetime.fromisoformat
IST_WEEK = "2025-03-10"   # a Monday


def _trade(client, dep_id, sym, tok, sell, buy, opened, closed, trigger="exit"):
    add_fill(client, dep_id, sym, tok, "sell", 75, sell, T(opened), "entry", {"trigger": "entry", "action": "open"})
    add_fill(client, dep_id, sym, tok, "buy", 75, buy, T(closed), trigger, {"trigger": trigger, "action": "close"})


def test_a_week_in_review(client, monkeypatch):
    d = make_deployment(client, name="review dtt")
    # the week before: one +750
    _trade(client, d["id"], "NIFTY25MAR22000CE", 9301, 100, 90, "2025-03-04T10:00:00+05:30", "2025-03-04T11:00:00+05:30")
    # the week: +1,500 Mon, −750 Wed, and three stop-outs in a day
    _trade(client, d["id"], "NIFTY25MAR22100CE", 9302, 100, 80, "2025-03-10T10:00:00+05:30", "2025-03-10T11:00:00+05:30")
    _trade(client, d["id"], "NIFTY25MAR22200CE", 9303, 100, 110, "2025-03-12T10:00:00+05:30", "2025-03-12T11:00:00+05:30")
    for i in range(3):
        _trade(client, d["id"], f"NIFTY25MAR2230{i}PE", 9310 + i, 100, 101, f"2025-03-13T1{i}:00:00+05:30", f"2025-03-13T1{i}:30:00+05:30", "leg_stop")
    client.post("/v2/journal", json={"day": "2025-03-12", "deployment_id": d["id"], "body": "RBI day, stayed small", "tags": ["event day"]})
    # India VIX from Kite (faked): 14 last Friday, up a point a day this week
    from datetime import time as _t

    from app import vix as V

    async def history(resolver, token, start, end, interval):
        assert interval == "day"
        out, d, v = [], start.date(), {}
        while d <= end.date():
            if d.weekday() < 5:
                close = 14.0 + max(0, (d - T("2025-03-07T00:00:00+05:30").date()).days - 2) if d >= T("2025-03-10T00:00:00+05:30").date() else 14.0
                out.append({"date": datetime.combine(d, _t(0)), "open": close - 0.5, "high": close + 0.3, "low": close - 0.6, "close": close})
            d += timedelta(days=1)
        return out
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", history)
    svc = client.app.state.vix
    monkeypatch.setattr(svc, "token", 264969)
    svc._days.clear()
    r = client.get(f"/v2/review?week=2025-03-12")   # any day of the week
    assert r.status_code == 200, r.text
    w = r.json()
    assert (w["week"], w["week_end"], w["complete"]) == (IST_WEEK, "2025-03-14", True)
    mine = next(x for x in w["deployments"] if x["deployment_id"] == d["id"])
    assert (mine["pnl"], mine["trades"], mine["wins"], mine["name"]) == (525.0, 5, 1, "review dtt")
    assert mine["usual"]["pnl"] == 750.0 and mine["weeks_known"] == 1
    assert mine["best"]["pnl"] == 1500.0 and mine["worst"]["pnl"] == -750.0
    assert mine["pnl_net"] < mine["pnl"], "after charges too"
    days = {x["day"]: x for x in w["days"]}
    assert list(days)[:5] == ["2025-03-10", "2025-03-11", "2025-03-12", "2025-03-13", "2025-03-14"]
    assert days["2025-03-10"]["pnl"] >= 1500 and days["2025-03-13"]["closes"] >= 3
    stops = [x for x in w["rules"] if x["deployment_id"] == d["id"]]
    assert [(x["trigger"], x["n"], x["usual"]) for x in stops] == [("leg_stop", 3, 0), ("entry", 5, 1.0)], \
        "a rule that never fired before, then one 5× its usual"
    assert any(n["body"] == "RBI day, stayed small" for n in w["notes"])
    assert w["prev"] is not None and w["next"] == "2025-03-17"
    assert round(sum(x["pnl"] for x in w["days"]), 2) == w["totals"]["pnl"], "the total is what the day bars add up to"
    assert client.get("/v2/review?week=not-a-day").status_code == 422
    v = w["vix"]
    assert (v["open"], v["close"], v["change"]) == (14.0, 19.0, 5.0) and [d["change"] for d in v["days"]] == [1.0] * 5, "a point a day, Mon–Fri"
    assert mine["side"] == "sold", "it sold its options"
    assert w["by_side"]["sold"]["n"] >= 1 and w["vix_error"] is None


def test_the_saturday_push_goes_once(client):
    from app import notifications, strategy_health
    sent = []

    async def fake(pool, config, title, body):
        sent.append((title, body))
    pool, real = client.app.state.db_pool, notifications.send_push_for_all
    notifications.send_push_for_all = fake
    try:
        sat = T("2025-03-15T09:05:00+05:30")
        assert client.portal.call(lambda: strategy_health.push_weekly(pool, {}, sat - timedelta(hours=1), {})) is False, "not before 09:00"
        assert client.portal.call(lambda: strategy_health.push_weekly(pool, {}, sat - timedelta(days=1), {})) is False, "Saturdays only"
        assert client.portal.call(lambda: strategy_health.push_weekly(pool, {}, sat, {})) is True
        assert client.portal.call(lambda: strategy_health.push_weekly(pool, {}, sat + timedelta(hours=3), {})) is False, "once a week"
        assert len(sent) == 1 and sent[0][0] == "Your week, 10–14 Mar"
    finally:
        notifications.send_push_for_all = real
