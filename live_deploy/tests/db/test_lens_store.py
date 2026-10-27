"""LinckLens's daily prices in the database (app/lens_store.py) and how
app/lens.py reads them: each instrument's year once, then only the days
since, at the 17:00 sync; nothing asked of Kite in between."""

import asyncio
import time
from datetime import date, datetime, timedelta

from app import lens, lens_store as S

IST = S.IST
WED = date(2026, 10, 14)


def at(d, hh, mm=0):
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


def daily(start, end, f=lambda i: 100.0 + i):
    out, d, i = [], start, 0
    while d <= end:
        if d.weekday() < 5:
            c = f(i)
            out.append({"date": datetime(d.year, d.month, d.day), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 10})
            i += 1
        d += timedelta(days=1)
    return out


def kite(asked, fail=()):
    async def call(what, *args):
        if what == "historical_data":
            asked.append((args[0], args[1], args[2]))
            if args[0] in fail:
                raise RuntimeError("refused")
            await asyncio.sleep(0)
            return daily(args[1], args[2])
        if what == "holdings":
            return [{"tradingsymbol": "INFY", "exchange": "NSE", "instrument_token": 408065, "quantity": 10,
                     "average_price": 90.0, "last_price": 120.0, "close_price": 118.0}]
        return [] if what in ("mf_holdings", "mf_sips", "mf_instruments", "instruments") else {}
    return call


def test_which_days_to_ask_kite_for():
    assert S.last_complete_day(at(WED, 15, 44)) == WED - timedelta(days=1), "the day isn't final before 15:45"
    assert S.last_complete_day(at(WED, 17, 0)) == WED
    assert S.last_complete_day(at(date(2026, 10, 18), 12)) == date(2026, 10, 16), "Sunday: Friday"
    first = S.to_read(None, None, at(WED, 11))
    assert first == (WED - timedelta(days=S.FIRST_READ_DAYS), WED), "the first time: the year"
    assert S.to_read(WED - timedelta(days=1), date(2025, 10, 1), at(WED, 11)) is None, "during the day: nothing new yet"
    assert S.to_read(WED - timedelta(days=1), date(2025, 10, 1), at(WED, 17)) == (WED, WED), "after the close: today"
    assert S.to_read(date(2026, 10, 9), date(2025, 10, 1), at(WED, 17)) == (date(2026, 10, 10), WED), "days missed: from the last read"
    assert S.to_read(WED, date(2025, 10, 1), at(WED, 18)) is None
    assert S.to_read(WED, date(2025, 10, 1), at(WED, 18), since=date(2024, 1, 2)) == (date(2024, 1, 2), WED), \
        "an older first trade: back to it"


def test_the_year_once_then_only_the_days_since(pool_run):
    async def go(pool):
        asked, now = [], [at(WED, 11)]
        L = lens.Lens(None, call=kite(asked), pool=pool, now=lambda: now[0])
        first = await L.history()
        assert first["state"] == "loading" and first["total"] == 2
        await L.hist["task"]
        ready = await L.history()
        assert ready["state"] == "ready" and ready["error"] is None
        assert ready["insights"]["shares"]["INFY"]["ret"]["1Y"] is not None and ready["insights"]["curve"]["covered"] == 1
        assert sorted(a[0] for a in asked) == [lens.NIFTY, 408065] and asked[0][1] == WED - timedelta(days=S.FIRST_READ_DAYS)
        kept = await S.kept(pool, [408065])
        assert kept[408065]["fetched_through"] == WED - timedelta(days=1), "read before the close: today isn't final"
        n = await pool.fetchval("SELECT count(*) FROM lens_daily WHERE instrument_token = 408065")
        assert n > 250
        # the rest of the day: nothing asked of Kite, however often it's opened
        for _ in range(3):
            assert (await L.history())["state"] == "ready"
        assert len(asked) == 2
        # 17:00: today, one call each
        now[0] = at(WED, 17, 5)
        r = await L.topup()
        assert r["asked"] == 2 and r["failed"] == {} and [a[1] for a in asked[2:]] == [WED, WED]
        assert (await S.kept(pool, [408065]))[408065]["fetched_through"] == WED
        assert (await L.history())["state"] == "ready" and len(asked) == 4
        # a day missed (the app was off Thursday and Friday): from the last read
        now[0] = at(date(2026, 10, 19), 17, 1)
        await L.topup()
        assert asked[-1][1:] == (date(2026, 10, 15), date(2026, 10, 19))
        # Saturday: the week since Monday once, then nothing more over the weekend
        now[0] = at(date(2026, 10, 24), 12)
        await L.topup()
        assert asked[-1][1:] == (date(2026, 10, 20), date(2026, 10, 24))
        n = len(asked)
        now[0] = at(date(2026, 10, 25), 18)
        await L.topup()
        assert len(asked) == n, "Sunday: Friday is already in"
    pool_run(go)


def test_a_share_kite_refuses_is_named_and_tried_again_later(pool_run, monkeypatch):
    async def go(pool):
        asked = []
        L = lens.Lens(None, call=kite(asked, fail=(408065,)), pool=pool, now=lambda: at(WED, 11))
        await L.history()
        await L.hist["task"]
        out = await L.history()
        assert out["state"] == "ready" and out["error"] == "Prices couldn't be read for INFY (RuntimeError: refused)"
        assert out["insights"]["shares"]["INFY"]["days"] == 0 and out["insights"]["curve"] is None
        n = len(asked)
        await L.history()
        assert len(asked) == n, "not asked again at once"
        monkeypatch.setattr(time, "monotonic", lambda: 1e12)        # ten minutes on
        assert (await L.history())["state"] == "loading"
        await L.hist["task"]
        assert len(asked) == n + 1 and asked[-1][0] == 408065
    pool_run(go)


def test_without_nifty_the_year_says_why(pool_run):
    async def go(pool):
        L = lens.Lens(None, call=kite([], fail=(lens.NIFTY,)), pool=pool, now=lambda: at(WED, 11))
        await L.history()
        await L.hist["task"]
        return await L.history()
    out = pool_run(go)
    assert out["state"] == "error" and out["error"] == "NIFTY 50 prices: RuntimeError: refused"


def test_the_17_00_sync_runs_lenss_after_a_complete_option_backfill(pool_run):
    from app.deployments.manager import DeploymentManager as M

    class Candles:
        def __init__(self, pool, complete):
            self.pool, self.complete = pool, complete

        async def backfill_day(self, day):
            return {"complete": self.complete}

    async def go(pool):
        ran = []

        async def after():
            ran.append(1)
        assert await M.candle_backfill_once(Candles(pool, False), at(WED, 17, 1), None, after) is None and ran == [], \
            "the options didn't finish: not yet"
        assert await M.candle_backfill_once(Candles(pool, True), at(WED, 17, 2), None, after) == WED and ran == [1]
        assert await M.candle_backfill_once(Candles(pool, True), at(WED, 17, 3), WED, after) == WED and ran == [1], "once a day"

        async def broken():
            raise RuntimeError("Kite down")
        assert await M.candle_backfill_once(Candles(pool, True), at(WED + timedelta(days=1), 17, 1), WED, broken) == WED + timedelta(days=1), \
            "LinckLens failing doesn't undo the options"
    pool_run(go)
