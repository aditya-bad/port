"""GET /v2/deployments/{id}/underlying: a strangle's legs against NIFTY
and its stored November future, without a Kite session (prices from the
live tick cache and the stored candles)."""

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db import queries
from tests.conftest import add_fill, default_config, make_deployment

IST = ZoneInfo("Asia/Kolkata")


def test_legs_against_spot_and_future(client, monkeypatch):
    pool = client.app.state.db_pool
    cfg = {**default_config("strangle_monthly_v2"), "instrument": "NIFTY", "instrument_tokens": [256265]}
    d = make_deployment(client, name="und strangle", strategy="strangle_monthly_v2", mode="positional", config=cfg)
    today = datetime.now(IST).date()
    exp = today + timedelta(days=34)
    meta = {"contract_expiry": exp.isoformat()}
    add_fill(client, d["id"], "NIFTY26DEC25600CE", 5101, "sell", 75, 150, datetime.now(IST) - timedelta(days=2), "entry", {**meta, "strike": 25600})
    add_fill(client, d["id"], "NIFTY26DEC24600PE", 5102, "sell", 75, 140, datetime.now(IST) - timedelta(days=2), "entry", {**meta, "strike": 24600})
    client.portal.call(lambda: queries.upsert_contracts(pool, [
        {"instrument_token": 6001, "tradingsymbol": "NIFTYNEARFUT", "underlying": "NIFTY", "expiry": today + timedelta(days=6), "instrument_type": "FUT"},
        {"instrument_token": 6002, "tradingsymbol": "NIFTYFARFUT", "underlying": "NIFTY", "expiry": exp, "instrument_type": "FUT"}]))
    # the far future's stored candles: yesterday's close and today's
    y = datetime.combine(today - timedelta(days=1), datetime.min.time(), IST).replace(hour=15, minute=29)
    t = datetime.combine(today, datetime.min.time(), IST).replace(hour=9, minute=15)
    client.portal.call(lambda: queries.upsert_candles(pool, [(6002, y, 25190, 25195, 25185, 25190, 1), (6002, t, 25200, 25220, 25195, 25210, 1)], "kite"))
    disp = client.app.state.dispatcher
    monkeypatch.setitem(disp.last_prices, 256265, 25100.0)
    monkeypatch.setitem(disp.last_prices, 5101, 120.0)
    monkeypatch.setitem(disp.last_prices, 5102, 110.0)
    r = client.get(f"/v2/deployments/{d['id']}/underlying").json()
    assert r["available"] and r["underlying"] == "NIFTY" and r["index"] == "NIFTY 50"
    assert r["future"]["symbol"] == "NIFTYFARFUT", "the future of the legs' own month"
    assert r["future"]["price"] == 25210 and r["future"]["source"] == "stored" and r["future"]["as_of"]
    assert r["future"]["prev_close"] == 25190 and r["future"]["change_today"] == 20
    assert r["basis"]["pts"] == 110 and r["basis"]["dte"] == 34 and r["basis"]["annual_pct"] > 0
    ce = next(l for l in r["legs"] if l["kind"] == "CE")
    assert ce["from_spot"] == 500 and ce["from_fut"] == 390 and ce["iv"] and ce["delta"] < 0
    assert r["net"]["legs_valued"] == 2 and len(r["net"]["scenarios"]) == 8
    # the live tick, when there is one, beats the stored candle
    monkeypatch.setitem(disp.last_prices, 6002, 25230.0)
    r2 = client.get(f"/v2/deployments/{d['id']}/underlying").json()
    assert r2["future"]["price"] == 25230 and r2["future"]["source"] == "live"


def test_without_a_known_index_or_future_it_says_so(client):
    base = default_config("intraday_dtt_simple")
    d = make_deployment(client, name="und none", config={**base, "options_underlying": "RELIANCE", "instrument_tokens": [999]})
    r = client.get(f"/v2/deployments/{d['id']}/underlying").json()
    assert r == {"available": False, "why": "This deployment's index isn't one with listed futures."}
    d2 = make_deployment(client, name="und nofut", config={**base, "options_underlying": "BANKNIFTY", "instrument_tokens": [260105]})
    r2 = client.get(f"/v2/deployments/{d2['id']}/underlying").json()
    assert r2["available"] and r2["future"] is None and "Kite login" in r2["why"]
    assert client.get("/v2/deployments/00000000-0000-0000-0000-000000000000/underlying").status_code == 404
