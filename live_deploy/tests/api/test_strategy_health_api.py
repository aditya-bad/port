"""GET /v2/strategy-health through real fills, and each finding pushed
once (app/strategy_health.py; the rules: tests/unit/test_strategy_health.py)."""

from datetime import datetime, timedelta
from uuid import UUID

from tests.conftest import add_fill, make_deployment

T = datetime.fromisoformat


def _trades(client, dep_id, pnls, start="2026-08-03"):
    """One short option a market day, closing at entry − pnl/75."""
    d = T(f"{start}T10:00:00+05:30")
    for i, p in enumerate(pnls):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        sym, tok = f"NIFTY26DEC{24000 + i * 50}CE", 9100 + i
        add_fill(client, dep_id, sym, tok, "sell", 75, 100.0, d, "entry", {"trigger": "entry", "action": "open"})
        add_fill(client, dep_id, sym, tok, "buy", 75, 100.0 - p / 75, d + timedelta(hours=2), "exit", {"trigger": "exit", "action": "close"})
        d += timedelta(days=1)


def test_a_deployment_winning_less_often_is_flagged_once(client):
    d = make_deployment(client, name="health drifting")
    steady = make_deployment(client, name="health steady")
    base = [1500, -750, 1500, 1500, -750] * 4
    _trades(client, d["id"], base + [1500, -750, -750, -750, -750, -750, -750, -750, 1500, -750])
    _trades(client, steady["id"], base + base[:10])
    body = client.get("/v2/strategy-health?fresh=true").json()
    found = body["deployments"].get(d["id"], [])
    assert [f["rule"] for f in found] == ["win_rate"], found
    f = found[0]
    assert f["name"] == "health drifting" and f["deployment_id"] == d["id"]
    assert f["body"] == "Won 20% of the last 10 trades against 60% before."
    assert steady["id"] not in body["deployments"]
    assert client.get("/v2/strategy-health").json()["checked_at"] == body["checked_at"], "cached until the next check"

    # pushed once, however often the check runs
    from app import notifications, strategy_health
    sent = []

    async def fake_push(pool, config, title, text):
        sent.append((title, text))
    pool = client.app.state.db_pool
    real = notifications.send_push_for_all
    notifications.send_push_for_all = fake_push
    try:
        only = {d["id"]: found}
        assert client.portal.call(lambda: strategy_health.push_new(pool, {}, only)) == 1
        assert client.portal.call(lambda: strategy_health.push_new(pool, {}, only)) == 0
        assert sent == [("health drifting: winning less often", f["body"])]
        # notifications off for a deployment: nothing pushed
        other = {d["id"]: [{**f, "key": "win_rate:other"}]}
        client.portal.call(lambda: pool.execute("UPDATE deployments SET notifications_enabled = false WHERE id = $1", UUID(d["id"])))
        assert client.portal.call(lambda: strategy_health.push_new(pool, {}, other)) == 0
    finally:
        notifications.send_push_for_all = real
