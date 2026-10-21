"""GET /v2/vix/exposure: what open option legs gain or lose if VIX rises
a point now (app/vix.exposure)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.geek.optionmath import implied_vol, vega, years_to
from tests.conftest import add_fill, default_config, make_deployment

IST = ZoneInfo("Asia/Kolkata")


def test_open_legs_per_vix_point(client):
    cfg = {**default_config("intraday_dtt_simple"), "instrument_tokens": [256265]}
    seller, buyer = make_deployment(client, name="vega seller", config=cfg), make_deployment(client, name="vega buyer", config=cfg)
    t0 = datetime.now(IST) - timedelta(hours=1)
    expiry = (datetime.now(IST) + timedelta(days=9)).date().isoformat()
    meta = {"trigger": "entry", "action": "open", "expiry": expiry}
    add_fill(client, seller["id"], "NIFTY26DEC25000CE", 9801, "sell", 75, 200.0, t0, "entry", meta)
    add_fill(client, seller["id"], "NIFTY26DEC25000PE", 9802, "sell", 75, 190.0, t0, "entry", meta)
    add_fill(client, buyer["id"], "NIFTY26DEC25200CE", 9803, "buy", 75, 120.0, t0, "entry", meta)
    add_fill(client, buyer["id"], "NIFTY26DEC25300CE", 9804, "buy", 75, 90.0, t0, "entry", {"trigger": "entry", "action": "open"})   # no expiry known
    d = client.app.state.dispatcher
    d.last_prices.update({256265: 25000.0, 9801: 210.0, 9802: 185.0, 9803: 118.0, 9804: 88.0})
    try:
        body = client.get("/v2/vix/exposure").json()
    finally:
        for k in (256265, 9801, 9802, 9803, 9804):
            d.last_prices.pop(k, None)
    by = {x["name"]: x for x in body["deployments"]}
    now = datetime.now(IST)
    t = years_to(date.fromisoformat(expiry), now)

    def per_point(kind, k, px, qty, sign):
        iv = implied_vol(kind, px, 25000.0, k, t)
        return vega(25000.0, k, t, iv) / 100 * qty * sign
    want_seller = per_point("CE", 25000, 210.0, 75, -1) + per_point("PE", 25000, 185.0, 75, -1)
    want_buyer = per_point("CE", 25200, 118.0, 75, 1)
    assert by["vega seller"]["legs"] == 2 and abs(by["vega seller"]["per_point"] - want_seller) < 5
    assert by["vega seller"]["per_point"] < 0, "a seller loses as VIX rises"
    assert by["vega buyer"]["legs"] == 1 and by["vega buyer"]["per_point"] > 0 and abs(by["vega buyer"]["per_point"] - want_buyer) < 5
    assert body["missing"] >= 1, "the leg with no expiry known is counted, not guessed"
    assert body["deployments"][0]["per_point"] <= body["deployments"][-1]["per_point"], "most exposed first"
