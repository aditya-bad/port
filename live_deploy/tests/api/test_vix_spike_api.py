"""A VIX spike: GET /v2/vix/spike names what holds sold premium, and the
push goes once a day (app/vix.py)."""

from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app import vix as V
from tests.conftest import add_fill, make_deployment

IST = ZoneInfo("Asia/Kolkata")


def test_spike_names_the_exposed_and_pushes_once(client, monkeypatch):
    async def history(resolver, token, start, end, interval):
        out, d = [], start.date()
        while d <= end.date():
            if d.weekday() < 5:
                out.append({"date": datetime.combine(d, time(0)), "open": 14, "high": 14.6, "low": 13.8, "close": 14.5})
            d += timedelta(days=1)
        return out
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", history)
    svc = client.app.state.vix
    monkeypatch.setattr(svc, "token", 264969)
    svc._days.clear()
    svc.dispatcher.last_prices[264969] = 16.4
    seller, buyer = make_deployment(client, name="spike seller"), make_deployment(client, name="spike buyer")
    t0 = datetime.now(IST) - timedelta(minutes=30)
    add_fill(client, seller["id"], "NIFTY26DEC25000CE", 9901, "sell", 75, 120.0, t0, "entry", {"trigger": "entry", "action": "open"})
    add_fill(client, seller["id"], "NIFTY26DEC25000PE", 9902, "sell", 75, 110.0, t0, "entry", {"trigger": "entry", "action": "open"})
    add_fill(client, buyer["id"], "NIFTY26DEC25100CE", 9903, "buy", 75, 90.0, t0, "entry", {"trigger": "entry", "action": "open"})
    # in market hours, a +13% VIX
    monkeypatch.setattr(V, "datetime", type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: datetime(2026, 10, 21, 11, 0, tzinfo=IST))}))
    body = client.get("/v2/vix/spike?fresh=true").json()
    assert body["spiking"] is True and body["prev_close"] == 14.5 and body["change_pct"] == 13.1
    names = [e["name"] for e in body["exposed"]]
    assert "spike seller" in names and "spike buyer" not in names, "a buyer gains as VIX rises"
    mine = next(e for e in body["exposed"] if e["name"] == "spike seller")
    assert mine["legs"] == 2 and mine["premium"] == round((120 + 110) * 75, 2)
    # pushed once a day
    from app import notifications
    sent = []

    async def fake(pool, config, title, text):
        sent.append(title)
    monkeypatch.setattr(notifications, "send_push_for_all", fake)
    monkeypatch.setattr(notifications, "is_push_configured", lambda c: True)
    pool, now = client.app.state.db_pool, datetime(2026, 10, 21, 11, 0, tzinfo=IST)
    assert client.portal.call(lambda: V.push_spike_once(pool, {}, body, now)) is True
    assert client.portal.call(lambda: V.push_spike_once(pool, {}, body, now + timedelta(minutes=30))) is False
    assert client.portal.call(lambda: V.push_spike_once(pool, {}, body, now + timedelta(days=1))) is True, "the next day: again"
    assert sent == ["VIX up 13% today: 14.50 → 16.40"] * 2
    svc.dispatcher.last_prices.pop(264969, None)
