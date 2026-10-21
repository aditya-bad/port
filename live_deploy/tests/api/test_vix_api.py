"""GET /v2/vix: India VIX candles from what's stored (app/vix.py)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import queries

IST = ZoneInfo("Asia/Kolkata")


def test_vix_candles_and_bad_requests(client):
    pool = client.app.state.db_pool
    t0 = datetime(2025, 6, 2, 9, 15, tzinfo=IST)
    rows = [(t0 + timedelta(minutes=i), 13 + i / 100, 13.1 + i / 100, 12.9 + i / 100, 13 + i / 100) for i in range(30)]
    client.portal.call(lambda: queries.upsert_vix(pool, rows, "kite"))
    r = client.get("/v2/vix", params={"start": "2025-06-02T00:00:00+05:30", "end": "2025-06-03T00:00:00+05:30", "interval": "15minute"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["symbol"] == "INDIA VIX" and len(body["candles"]) == 2
    a, b = body["candles"]
    assert a["o"] == 13.0 and round(a["c"], 2) == 13.14 and round(b["h"], 2) == 13.39
    assert set(body["backfill"]) == {"through", "running", "error"}
    assert body["latest_stored"]["price"] is not None
    assert client.get("/v2/vix", params={"interval": "7minute"}).status_code == 422
    assert client.get("/v2/vix", params={"start": "2020-01-01T00:00:00+05:30", "end": "2025-01-01T00:00:00+05:30"}).status_code == 422
