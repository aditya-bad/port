"""GET /v2/vix: India VIX from Kite, on demand (app/vix.py)."""

from datetime import datetime, time, timedelta

from app import vix as V
from app.options import NoKiteSession


def test_vix_candles_and_bad_requests(client, monkeypatch):
    async def history(resolver, token, start, end, interval):
        assert interval == "15minute"
        d0 = datetime.combine(start.date(), time(9, 15))
        return [{"date": d0 + timedelta(minutes=15 * i), "open": 13 + i / 100, "high": 13.2, "low": 12.9, "close": 13.1} for i in range(3)]
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", history)
    svc = client.app.state.vix
    monkeypatch.setattr(svc, "token", 264969)
    svc._days.clear()
    r = client.get("/v2/vix", params={"start": "2025-06-02T00:00:00+05:30", "end": "2025-06-02T23:00:00+05:30", "interval": "15minute"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "INDIA VIX" and len(body["candles"]) == 3 and body["candles"][0]["o"] == 13.0
    assert client.get("/v2/vix", params={"interval": "7minute"}).status_code == 422
    assert client.get("/v2/vix", params={"start": "2020-01-01T00:00:00+05:30", "end": "2025-01-01T00:00:00+05:30"}).status_code == 422


def test_without_kite_it_says_so(client, monkeypatch):
    async def no_session(resolver, *a, **k):
        raise NoKiteSession("no")
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", no_session)
    svc = client.app.state.vix
    monkeypatch.setattr(svc, "token", 264969)
    svc._days.clear()
    body = client.get("/v2/vix", params={"start": "2025-06-03T00:00:00+05:30", "end": "2025-06-03T23:00:00+05:30"}).json()
    assert body["candles"] == [] and "Kite isn't logged in" in body["error"]
