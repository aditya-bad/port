"""India VIX from Kite on demand (app/vix.py): looked up by name, history
fetched when asked in as few requests as Kite allows, finished days kept
in memory and never asked for again, today's refreshed after a minute,
and nothing stored."""

import asyncio
from datetime import date, datetime, time, timedelta

import pytest

from app import vix as V

IST = V.IST
TOKEN = 264969
NOW = datetime(2026, 10, 14, 11, 0, tzinfo=IST)   # a Wednesday, mid-session


class FakeDispatcher:
    def __init__(self):
        self.access_token, self.added = "t", []
        self.last_prices, self.last_price_times = {}, {}

    def add_instruments(self, tokens):
        self.added.extend(tokens)


@pytest.fixture
def kite(monkeypatch):
    calls = []
    holidays = {date(2026, 10, 2)}

    async def instruments(resolver, exchange, force=False):
        assert exchange == "NSE"
        return {"data": [{"tradingsymbol": "NIFTY 50", "instrument_token": 256265},
                         {"tradingsymbol": "INDIA VIX", "instrument_token": TOKEN}]}

    async def history(resolver, token, start, end, interval):
        calls.append((token, start.date(), end.date(), interval))
        out, d = [], start.date()
        while d <= end.date():
            if d.weekday() < 5 and d not in holidays:
                if interval == "day":
                    out.append({"date": datetime.combine(d, time(0)), "open": 14, "high": 15, "low": 13.5, "close": 14.5})
                else:
                    for i in range(0, 375, 15):
                        t = datetime.combine(d, time(9, 15)) + timedelta(minutes=i)
                        if t.replace(tzinfo=IST) <= NOW:
                            out.append({"date": t, "open": 14, "high": 14.2, "low": 13.9, "close": 14 + i / 1000})
            d += timedelta(days=1)
        return out
    monkeypatch.setattr(V.OptionsResolver, "_ensure_instruments", instruments)
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", history)
    monkeypatch.setitem(V.state, "token", None)
    return calls


def test_the_pure_parts():
    assert round(V.expected_move_pct(15), 3) == 0.785, "VIX 15: about 0.79% a day"
    assert round(V.expected_move_pct(15, 30), 2) == 4.30
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(0, 400, 1)]
    assert len(V.windows(days, "day")) == 1, "a year of days: one request"
    assert len(V.windows(days, "15minute")) == 3, "15-minute: 180 days a request"
    assert len(V.windows(days, "minute")) == 14


def test_a_year_in_three_requests_then_from_memory(kite):
    svc = V.VixService(None, FakeDispatcher())
    year = asyncio.run(svc.vix(NOW.date() - timedelta(days=365), NOW.date(), "15minute", NOW))
    assert svc.token == TOKEN and svc.dispatcher.added == [{"symbol": "INDIA VIX", "instrument_token": TOKEN}]
    assert len(kite) == 3 and len(year) > 250 * 20, f"{len(kite)} requests"
    assert year[-1]["t"] <= NOW and year == sorted(year, key=lambda r: r["t"])
    n = len(kite)
    again = asyncio.run(svc.vix(NOW.date() - timedelta(days=100), NOW.date() - timedelta(days=1), "15minute", NOW))
    assert len(kite) == n and again, "finished days come from memory"
    assert asyncio.run(svc.vix(date(2026, 10, 2), date(2026, 10, 2), "15minute", NOW)) == [] and len(kite) == n, \
        "a holiday is remembered as empty"
    # today: from memory for a minute, then asked again
    asyncio.run(svc.vix(NOW.date(), NOW.date(), "15minute", NOW))
    assert len(kite) == n
    key = (TOKEN, "15minute", NOW.date())
    svc._days[key] = (svc._days[key][0] - 120, svc._days[key][1])
    asyncio.run(svc.vix(NOW.date(), NOW.date(), "15minute", NOW))
    assert len(kite) == n + 1 and kite[-1][1] == NOW.date() == kite[-1][2], "only today"


def test_an_index_s_days(kite):
    svc = V.VixService(None, FakeDispatcher())
    days = asyncio.run(svc.candles(256265, date(2026, 9, 1), date(2026, 10, 13), "day", NOW))
    assert len(kite) == 1 and [d["t"].date() for d in days][-1] == date(2026, 10, 13)
    assert all(d["t"].tzinfo is not None for d in days)


def test_a_missing_instrument_is_reported_not_guessed(kite, monkeypatch):
    async def none_listed(resolver, exchange, force=False):
        return {"data": [{"tradingsymbol": "NIFTY 50", "instrument_token": 256265}]}
    monkeypatch.setattr(V.OptionsResolver, "_ensure_instruments", none_listed)
    svc = V.VixService(None, FakeDispatcher())
    assert asyncio.run(svc.vix(NOW.date(), NOW.date(), "day", NOW)) == []
    assert svc.token is None and "isn't in Kite's NSE instrument list" in svc.last_error and kite == []


def test_nothing_is_stored():
    from pathlib import Path
    sql = " ".join(p.read_text() for p in sorted(Path("app/db/migrations").glob("*.sql")))
    assert "DROP TABLE IF EXISTS vix_1m" in sql and "DROP TABLE IF EXISTS index_1d" in sql
