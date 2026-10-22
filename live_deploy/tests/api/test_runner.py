"""DeploymentRunner inside the running app: which ticks reach the
strategy, what happens when the strategy raises, execution
notifications, and how a close is priced."""

import asyncio
from datetime import datetime, time, timedelta

import pytest

from tests.conftest import make_deployment, sql


def runner_of(client, dep_id):
    return client.app.state.deployment_manager.runners[dep_id]


def tick(token, at, price=100.0):
    return {"instrument_token": token, "last_price": price, "exchange_timestamp": at}


# ── Stale ticks ───────────────────────────────────────────────────────

def test_stale_tick_rules(client):
    d = make_deployment(client, start="now")
    r = runner_of(client, d["id"])
    tok = next(iter(r.tokens))
    now = datetime.now()
    r._MARKET_OPEN_TIME, r._MARKET_CLOSE_TIME = time(0, 0), time(23, 59, 59)
    assert not r._is_stale_tick(tick(tok, now))
    assert not r._is_stale_tick({"instrument_token": tok}), "ltp-mode ticks carry no time: trusted"
    assert r._is_stale_tick(tick(tok, r._created_at_local - timedelta(seconds=1)))
    assert "creation" in r._stale_tick_reason(tick(tok, r._created_at_local - timedelta(seconds=1)))
    r._created_at_local = now - timedelta(hours=1)
    assert r._is_stale_tick(tick(tok, now - timedelta(minutes=6)))
    assert "wall clock" in r._stale_tick_reason(tick(tok, now - timedelta(minutes=6)))
    # a timely tick at a moment the market isn't trading
    hhmm = (now - timedelta(minutes=1)).time()
    r._MARKET_OPEN_TIME, r._MARKET_CLOSE_TIME = time(0, 0), hhmm
    late = tick(tok, now)
    if now.time() > hhmm:            # (not the minute after midnight)
        assert r._is_stale_tick(late)
        assert "trading hours" in r._stale_tick_reason(late)


def _run_ticks(client, r, ticks, wait=0.3):
    async def go():
        await client.app.state.broadcaster.broadcast(ticks)
        await asyncio.sleep(wait)
    client.portal.call(go)


def test_only_fresh_ticks_for_its_own_tokens_reach_the_strategy(client, monkeypatch):
    d = make_deployment(client, start="now")
    r = runner_of(client, d["id"])
    r._MARKET_OPEN_TIME, r._MARKET_CLOSE_TIME = time(0, 0), time(23, 59, 59)
    seen = []

    async def on_tick(runner, t):
        seen.append(t["last_price"])
    monkeypatch.setattr(r.strategy, "on_tick", on_tick)
    tok = next(iter(r.tokens))
    now = datetime.now()
    # (prices an index makes: a jump of several % in a second is held as a
    # spike until confirmed — app/deployments/health.py: TickFilter)
    _run_ticks(client, r, [tick(tok, now, 100.0), tick(999999, now, 2.0),
                           tick(tok, now - timedelta(minutes=10), 99.0), tick(tok, now, 100.5)])
    assert seen == [100.0, 100.5]


def test_a_strategy_that_raises_keeps_running_and_records_an_error(client, monkeypatch):
    d = make_deployment(client, start="now")
    r = runner_of(client, d["id"])
    r._MARKET_OPEN_TIME, r._MARKET_CLOSE_TIME = time(0, 0), time(23, 59, 59)
    calls = []

    async def on_tick(runner, t):
        calls.append(1)
        raise RuntimeError("bug in strategy")
    monkeypatch.setattr(r.strategy, "on_tick", on_tick)
    tok = next(iter(r.tokens))
    _run_ticks(client, r, [tick(tok, datetime.now())])
    _run_ticks(client, r, [tick(tok, datetime.now())])
    assert len(calls) == 2, "the runner survives and keeps feeding ticks"
    assert not r._task.done()
    events = [e["event_type"] for e in client.get(f"/deployments/{d['id']}/events").json()]
    assert events.count("strategy_error") == 1, "the same error is recorded once, not on every tick"
    issues = client.get(f"/v2/strategy-issues?deployment={d['id']}").json()["issues"]
    assert [x["kind"] for x in issues] == ["crash"], "failing twice in a row is reported"


# ── Execution notifications ───────────────────────────────────────────

def test_notify_execution_records_pushes_and_respects_the_toggle(client, pushes, monkeypatch):
    d = make_deployment(client, name="Straddle", start="now")
    r = runner_of(client, d["id"])
    monkeypatch.setattr(r, "push_config", {"vapid": "test"})
    client.portal.call(r.notify_execution, "entry", "Sold CE+PE")
    assert pushes == [("Straddle — Entry", "Sold CE+PE")]
    events = [e["event_type"] for e in client.get(f"/deployments/{d['id']}/events").json()]
    assert "execution_entry" in events
    client.patch(f"/deployments/{d['id']}", json={"notifications_enabled": False})
    client.portal.call(r.notify_execution, "exit", "Closed")
    assert len(pushes) == 1, "a muted deployment is silent"
    with pytest.raises(ValueError):
        client.portal.call(r.notify_execution, "adjust", "x")


def test_a_push_failure_never_reaches_the_strategy(client, monkeypatch):
    import app.notifications as N
    d = make_deployment(client, start="now")
    r = runner_of(client, d["id"])
    monkeypatch.setattr(r, "push_config", {"vapid": "test"})

    async def boom(*a):
        raise RuntimeError("push service down")
    monkeypatch.setattr(N, "send_push_for_all", boom)
    client.portal.call(r.notify_execution, "exit", "Closed")   # no raise


def test_strategy_errors_are_broadcast_live(client):
    d = make_deployment(client, start="now")
    r = runner_of(client, d["id"])

    async def go():
        q = await client.app.state.event_broadcaster.subscribe()
        try:
            await r.record_strategy_error("leg missing", {"leg": "CE"})
            return await asyncio.wait_for(q.get(), 2)
        finally:
            await client.app.state.event_broadcaster.unsubscribe(q)
    ev = client.portal.call(go)
    assert ev["event_type"] == "strategy_error" and ev["message"] == "leg missing"
    assert ev["deployment_id"] == d["id"] and ev["metadata"] == {"leg": "CE"}


def test_record_decision_stores_tick_time_as_ist_and_never_raises(client, monkeypatch):
    from datetime import datetime
    from app.db import queries
    d = make_deployment(client, start="now")
    r = runner_of(client, d["id"])
    client.portal.call(r.record_decision, datetime(2026, 10, 14, 15, 13), "eod_gap_check", "no_action", "fine", {"x": 1})
    rows = client.portal.call(queries.list_decisions, client.app.state.db_pool, r.deployment_id)
    assert rows[0]["decided_at"].isoformat().endswith("09:43:00+00:00"), "15:13 IST"

    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(queries, "record_decision", boom)
    client.portal.call(r.record_decision, datetime(2026, 10, 14, 15, 14), "x", "blocked", "never raises")


# ── Exit pricing ──────────────────────────────────────────────────────

def test_exit_price_live_then_quote_then_entry(client, monkeypatch):
    d = make_deployment(client, start="now")
    r = runner_of(client, d["id"])
    pos = {"instrument_token": 77, "symbol": "X", "avg_entry_price": 120.0}
    client.app.state.dispatcher.last_prices[77] = 95.0
    assert client.portal.call(r._exit_price, pos) == (95.0, "live")
    del client.app.state.dispatcher.last_prices[77]
    assert client.portal.call(r._exit_price, pos) == (120.0, "entry"), "no Kite: closes at entry"
    from app import options

    async def get_quotes(self, toks):
        return {"NFO:X": {"instrument_token": 77, "last_price": 88.0}}
    monkeypatch.setattr(options.OptionsResolver, "get_quotes", get_quotes)
    assert client.portal.call(r._exit_price, pos) == (88.0, "quote")


def test_unregistered_strategy_observes_without_trading(client):
    r = client.post("/v2/deployments", json={"deployment_name": "Ghost", "strategy_name": "not_written_yet",
                                             "mode": "intraday", "initial_capital": 100000,
                                             "config": {"instrument_tokens": [256265]}, "start": "now"})
    assert r.status_code == 201 and r.json()["strategy_registered"] is False
    runner = runner_of(client, r.json()["id"])
    assert runner.strategy is None
    _run_ticks(client, runner, [tick(256265, datetime.now())])
    assert sql(client, "SELECT 1 FROM position_lots") == []
