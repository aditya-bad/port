"""India VIX (app/vix.py): looked up by name, backfilled once from Kite
(a year back, or to the first trade), holidays remembered, live ticks
folded into minutes, and stamped on every fill."""

import asyncio
from datetime import date, datetime, time, timedelta

from app import vix as V
from app.db import queries

IST = V.IST
TOKEN = 264969


class FakeDispatcher:
    def __init__(self):
        self.access_token, self.added = "t", []
        self.last_prices, self.last_price_times = {}, {}

    def add_instruments(self, tokens):
        self.added.extend(tokens)


def fake_kite(monkeypatch, holidays=(), listed=True):
    calls = []

    async def instruments(resolver, exchange, force=False):
        assert exchange == "NSE"
        rows = [{"tradingsymbol": "NIFTY 50", "instrument_token": 256265, "name": "NIFTY 50"}]
        if listed:
            rows.append({"tradingsymbol": "INDIA VIX", "instrument_token": TOKEN, "name": "INDIA VIX"})
        return {"data": rows}

    async def history(resolver, token, start, end, interval):
        assert token == TOKEN and interval == "minute"
        calls.append((start, end))
        out, d = [], start.date()
        while d <= end.date():
            if d.weekday() < 5 and d not in holidays:
                t = datetime.combine(d, time(9, 15))
                for i in range(375):
                    v = 14 + (d.day % 5) + i / 1000
                    out.append({"date": t + timedelta(minutes=i), "open": v, "high": v + 0.1, "low": v - 0.1, "close": v})
            d += timedelta(days=1)
        return out
    monkeypatch.setattr(V.OptionsResolver, "_ensure_instruments", instruments)
    monkeypatch.setattr(V.OptionsResolver, "get_historical_candles", history)
    return calls


def test_the_pure_parts():
    assert round(V.expected_move_pct(15), 3) == 0.785, "VIX 15: about 0.79% a day"
    assert round(V.expected_move_pct(15, 30), 2) == 4.30
    mon = date(2026, 10, 12)
    days = V.missing_days(mon, mon + timedelta(days=6), {mon: 375, mon + timedelta(days=1): 120}, {mon + timedelta(days=2)})
    assert days == [mon + timedelta(days=1), mon + timedelta(days=3), mon + timedelta(days=4)], "weekdays not stored and not holidays"
    run = [date(2026, 1, 1) + timedelta(days=i) for i in range(0, 70, 7)]
    assert [len(c) for c in V.chunks(run)] == [5, 5], "at most 30 calendar days a request"
    m = datetime(2026, 10, 12, 10, 0, tzinfo=IST)
    b, done = V.fold(None, 14.0, m)
    b, done = V.fold(b, 14.5, m)
    b, done = V.fold(b, 13.9, m)
    assert done is None and b == [m, 14.0, 14.5, 13.9, 13.9]
    b2, done = V.fold(b, 15.0, m + timedelta(minutes=1))
    assert done == [m, 14.0, 14.5, 13.9, 13.9] and b2[1] == 15.0


def test_backfill_once_with_holidays_remembered(pool_run, monkeypatch):
    holiday = date(2026, 10, 2)
    calls = fake_kite(monkeypatch, holidays={holiday})

    async def go(pool):
        await pool.execute("DELETE FROM vix_1m; DELETE FROM vix_no_data")
        d = FakeDispatcher()
        svc = V.VixService(pool, d)
        now = datetime(2026, 10, 14, 11, 0, tzinfo=IST)     # before the close: through yesterday
        r = await svc.backfill(now)
        assert svc.token == TOKEN and V.state["token"] == TOKEN
        assert d.added == [{"symbol": "INDIA VIX", "instrument_token": TOKEN}], "subscribed like any index"
        assert r["error"] is None and r["rows"] > 250 * 300
        assert len(calls) <= 14, f"a year in 30-day requests, not one a day ({len(calls)})"
        counts = await queries.vix_day_counts(pool, datetime(2025, 1, 1, tzinfo=IST), datetime(2027, 1, 1, tzinfo=IST))
        assert counts.get(date(2026, 10, 13)) == 375 and date(2026, 10, 14) not in counts, "today waits for the close"
        assert holiday in await queries.vix_no_data_days(pool)
        # again: nothing to ask for
        n = len(calls)
        r = await svc.backfill(now)
        assert len(calls) == n and r["fetched_days"] == 0
        # after the close, just today
        r = await svc.backfill(datetime(2026, 10, 14, 15, 50, tzinfo=IST))
        assert len(calls) == n + 1 and r["fetched_days"] == 1
        assert await queries.vix_at(pool, datetime(2026, 10, 13, 10, 0, tzinfo=IST)) is not None
        # rolled up for charts
        day = await svc.series(datetime(2026, 10, 13, tzinfo=IST), datetime(2026, 10, 14, tzinfo=IST), "day")
        assert len(day) == 1 and day[0]["o"] < day[0]["h"]
        five = await svc.series(datetime(2026, 10, 13, tzinfo=IST), datetime(2026, 10, 14, tzinfo=IST), "5minute")
        assert len(five) == 75
    pool_run(go)


def test_a_missing_instrument_is_reported_not_guessed(pool_run, monkeypatch):
    fake_kite(monkeypatch, listed=False)

    async def go(pool):
        V.state["token"] = None
        svc = V.VixService(pool, FakeDispatcher())
        r = await svc.backfill(datetime(2026, 10, 14, 11, 0, tzinfo=IST))
        assert svc.token is None and r["rows"] == 0 and "isn't in Kite's NSE instrument list" in r["error"]
    pool_run(go)


def test_live_ticks_never_replace_kites_minutes(pool_run):
    async def go(pool):
        m = datetime(2026, 9, 1, 10, 0, tzinfo=IST)
        await pool.execute("DELETE FROM vix_1m WHERE minute >= $1 AND minute < $1 + interval '1 day'", m)
        await queries.upsert_vix(pool, [(m, 14, 14.5, 13.8, 14.2)], "ticks")
        await queries.upsert_vix(pool, [(m, 14.1, 14.9, 13.9, 14.0)], "ticks")      # widened, moved on
        r = (await queries.vix_rows(pool, m, m + timedelta(minutes=1)))[0]
        assert (r["o"], r["h"], r["l"], r["c"]) == (14, 14.9, 13.8, 14.0)
        await queries.upsert_vix(pool, [(m, 15, 15.2, 14.9, 15.1)], "kite")          # Kite's replaces it
        await queries.upsert_vix(pool, [(m, 1, 99, 1, 1)], "ticks")                  # and ticks never undo that
        r = (await queries.vix_rows(pool, m, m + timedelta(minutes=1)))[0]
        assert (r["o"], r["h"], r["l"], r["c"]) == (15, 15.2, 14.9, 15.1)
    pool_run(go)
