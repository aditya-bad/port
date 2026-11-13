"""GET /v2/vix: India VIX from Kite, on demand (app/vix.py)."""

from datetime import datetime, time, timedelta

import pytest

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


def test_track_compares_with_a_shortcut_underlying(client, monkeypatch):
    """GET /v2/track (Tools › Compare with underlying): NIFTY found by
    name in Kite's list, an ETF against it, daily candles from Kite."""
    from app.v2 import track
    closes = {256265: [24000, 24240, 24000], 2707457: [270, 272.7, 270.3]}

    async def history(resolver, token, start, end, interval):
        d0 = datetime.combine(start.date(), time(0))
        return [{"date": d0 + timedelta(days=i), "open": x, "high": x, "low": x, "close": x, "volume": 100}
                for i, x in enumerate(closes[token])]

    async def instruments(resolver, exchange, force=False):
        return {"data": [{"tradingsymbol": "NIFTY 50", "instrument_token": 256265}]}
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", history)
    monkeypatch.setattr(track.OptionsResolver, "_ensure_instruments", instruments)
    client.app.state.vix._days.clear()
    r = client.get("/v2/track", params={"base": "NIFTY", "with": "2707457", "start": "2025-06-02", "end": "2025-06-04"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["base_token"] == 256265 and b["base_info"]["symbol"] == "NIFTY 50"
    assert b["base"]["pct"] == [0, 1, 0] and b["others"][0]["pct"] == [0, 1, 0.1111]
    assert b["others"][0]["stats"]["diff"] == 0.1111 and b["others"][0]["stats"]["traded"] == pytest.approx(27100, abs=1)
    bad = [{"base": "NIFTY"}, {"base": "XYZ", "with": "1"}, {"base": "NIFTY", "with": "a"}, {"base": "NIFTY", "with": ",".join(map(str, range(1, 10)))},
           {"base": "NIFTY", "with": "1", "interval": "5minute", "start": "2025-06-02", "end": "2025-06-20"},
           {"base": "NIFTY", "with": "1", "start": "2025-06-05", "end": "2025-06-01"}]
    for p in bad:
        assert client.get("/v2/track", params=p).status_code == 422, p

    async def no_session(resolver, *a, **k):
        raise NoKiteSession("no")
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", no_session)
    client.app.state.vix._days.clear()
    r = client.get("/v2/track", params={"base": "256265", "with": "1", "start": "2025-07-01", "end": "2025-07-02"})
    assert r.status_code == 409 and "Kite isn’t connected" in r.json()["detail"]
