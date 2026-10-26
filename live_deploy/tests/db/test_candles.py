"""The candle store (app/candles.py): one 1-minute row per instrument
per minute whatever the source, rollups for every interval, and Kite
asked only for what's missing — once, one request at a time, never for
an expired contract."""

import asyncio
from datetime import date, datetime, time, timedelta

import pytest

from app import candles as C
from app.db import queries
from tests.conftest import fill

IST = C.IST
DAY = date(2026, 10, 14)          # a Wednesday


def at(d, hh, mm):
    return datetime.combine(d, time(hh, mm), IST)


class FakeKite:
    """Stands in for OptionsResolver.get_historical_candles: 1-minute
    candles for every session minute asked for, unless told otherwise."""

    def install(self, monkeypatch):
        fake = self

        async def history(resolver, token, start, end, interval):
            assert interval == "minute", "only 1-minute history is ever asked for"
            fake.calls.append((token, start, end))
            fake.in_flight += 1
            fake.max_in_flight = max(fake.max_in_flight, fake.in_flight)
            await asyncio.sleep(fake.delay)
            fake.in_flight -= 1
            if token in fake.empty:
                return []
            out, t = [], start
            while t <= end:
                if t.weekday() < 5 and time(9, 15) <= t.time() < time(15, 30):
                    p = 100 + (t.minute % 7)
                    out.append({"date": t.replace(tzinfo=IST), "open": p, "high": p + 1, "low": p - 1, "close": p, "volume": 10})
                t += timedelta(minutes=1)
            return out
        monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", history)
        return self


def _mk(monkeypatch, empty=(), delay=0.0):
    k = object.__new__(FakeKite)
    k.calls, k.empty, k.delay, k.in_flight, k.max_in_flight = [], set(empty), delay, 0, 0
    return k.install(monkeypatch)


def service(pool):
    return C.CandleService(pool, type("D", (), {"access_token": "t", "api_key": "k"})())


async def as_options(pool, *tokens):
    """Register test tokens as listed option contracts (only options are stored)."""
    await queries.upsert_contracts(pool, [{"instrument_token": t, "tradingsymbol": f"NIFTY26NOV{20000 + t}CE",
                                           "expiry": date(2026, 11, 24), "instrument_type": "CE"} for t in tokens])


@pytest.fixture
def frozen_today(monkeypatch):
    """Make 'now' a later day so DAY is fully in the past."""
    class Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 20, 18, 0, tzinfo=IST).astimezone(tz) if tz else datetime(2026, 10, 20, 18, 0)
    monkeypatch.setattr(C, "datetime", Now)


# ── Storage: one row per instrument per minute ────────────────────────

def test_sources_replace_worse_never_better(pool_run):
    async def go(pool):
        m = at(DAY, 10, 0)
        await queries.upsert_candles(pool, [(1, m, 100, 101, 99, 100, None)], "ticks")
        await queries.upsert_candles(pool, [(1, m, 100, 103, 98, 102, None)], "ticks")
        [r] = await queries.list_candles(pool, 1, m, m + timedelta(minutes=1))
        assert (r["h"], r["l"], r["c"], r["source"]) == (103, 98, 102, "ticks"), "ticks merge within a minute"
        await queries.upsert_candles(pool, [(1, m, 100, 110, 90, 105, 50)], "import")
        await queries.upsert_candles(pool, [(1, m, 1, 1, 1, 1, None)], "ticks")
        [r] = await queries.list_candles(pool, 1, m, m + timedelta(minutes=1))
        assert (r["h"], r["source"]) == (110, "import"), "ticks never overwrite better data"
        await queries.upsert_candles(pool, [(1, m, 100, 104, 96, 101, 70)], "kite")
        await queries.upsert_candles(pool, [(1, m, 5, 5, 5, 5, None)], "import")
        [r] = await queries.list_candles(pool, 1, m, m + timedelta(minutes=1))
        assert (r["h"], r["volume"], r["source"]) == (104, 70, "kite"), "Kite wins over everything"
        n = await pool.fetchval("SELECT count(*) FROM candles_1m")
        assert n == 1, "still exactly one row for that instrument and minute"
    pool_run(go)


def test_rollup_anchors_at_the_open_like_kite():
    rows = [{"minute": at(DAY, 9, 15) + timedelta(minutes=i), "o": i, "h": i + 1, "l": i - 1, "c": i} for i in range(375)]
    five = C.rollup(rows, "5minute")
    assert len(five) == 75 and five[0]["o"] == 0 and five[0]["c"] == 4 and five[0]["h"] == 5
    hourly = C.rollup(rows, "60minute")
    assert [datetime.fromtimestamp(c["t"] / 1000, IST).strftime("%H:%M") for c in hourly] == \
        ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
    [day] = C.rollup(rows, "day")
    assert (day["o"], day["c"], day["h"], day["l"]) == (0, 374, 375, -1)


@pytest.mark.parametrize("sym,expiry,strike,typ", [
    ("NIFTY26O2025000CE", date(2026, 10, 20), 25000, "CE"),
    ("BANKNIFTY26O0655100PE", date(2026, 10, 6), 55100, "PE"),
    ("NIFTY2610625000CE", date(2026, 1, 6), 25000, "CE"),
    ("NIFTY26NOV22400PE", date(2026, 11, 30), 22400, "PE"),
    ("NIFTY26DECFUT", date(2026, 12, 31), None, "FUT"),
])
def test_parse_contract(sym, expiry, strike, typ):
    p = C.parse_contract(sym)
    assert p["expiry"] == expiry and p["strike"] == strike and p["instrument_type"] == typ


def test_parse_contract_rejects_non_derivatives():
    assert C.parse_contract("NIFTY 50") is None and C.parse_contract("") is None


# ── Filling from Kite ─────────────────────────────────────────────────

def test_fetches_only_what_is_missing_and_never_twice(pool_run, monkeypatch, frozen_today):
    k = _mk(monkeypatch)

    async def go(pool):
        svc = service(pool)
        await as_options(pool, 7)
        # the first half of the day was recorded from ticks
        await queries.upsert_candles(pool, [(7, at(DAY, 9, 15) + timedelta(minutes=i), 1, 1, 1, 1, None) for i in range(375)], "ticks")
        candles, src, err = await svc.series(7, at(DAY, 9, 15), at(DAY, 15, 30), "5minute")
        assert k.calls == [] and src == "stored" and len(candles) == 75, "a covered day costs no Kite call"
        candles, src, _ = await svc.series(7, at(DAY, 9, 15), at(DAY + timedelta(days=1), 15, 30), "minute")
        assert len(k.calls) == 1 and k.calls[0][1].date() == DAY + timedelta(days=1), "only the missing day"
        assert len(candles) == 750 and src == "stored"
        await svc.series(7, at(DAY, 9, 15), at(DAY + timedelta(days=1), 15, 30), "minute")
        assert len(k.calls) == 1, "now covered: no second request"
    pool_run(go)


def test_concurrent_requests_share_one_fetch_and_kite_sees_one_at_a_time(pool_run, monkeypatch, frozen_today):
    k = _mk(monkeypatch, delay=0.05)

    async def go(pool):
        svc = service(pool)
        await as_options(pool, 5, 6, 8, 9)
        w = (at(DAY, 9, 15), at(DAY, 15, 30))
        await asyncio.gather(*(svc.ensure(5, *w) for _ in range(4)), *(svc.ensure(t, *w) for t in (6, 8, 9)))
        assert sorted(c[0] for c in k.calls) == [5, 6, 8, 9], "one request per instrument, however many asked"
        assert k.max_in_flight == 1, "never two Kite history requests at once"
    pool_run(go)


def test_expired_contracts_are_never_requested(pool_run, monkeypatch, frozen_today):
    k = _mk(monkeypatch)

    async def go(pool):
        svc = service(pool)
        _, src, err = await svc.series(11, at(DAY, 9, 15), at(DAY, 15, 30), symbol="NIFTY26O1625000CE")   # expired 16 Oct
        assert k.calls == [] and src is None and "No price history" in err
        await svc.series(12, at(DAY, 9, 15), at(DAY, 15, 30), symbol="NIFTY26O2725000CE")                # still listed
        assert [c[0] for c in k.calls] == [12]
    pool_run(go)


def test_an_instrument_kite_has_nothing_for_is_not_asked_again(pool_run, monkeypatch, frozen_today):
    k = _mk(monkeypatch, empty={13})

    async def go(pool):
        svc = service(pool)
        await as_options(pool, 13)
        for _ in range(3):
            await svc.series(13, at(DAY, 9, 15), at(DAY, 15, 30))
        assert len(k.calls) == 1
    pool_run(go)


def test_long_windows_are_split_into_kite_sized_requests(pool_run, monkeypatch, frozen_today):
    k = _mk(monkeypatch)

    async def go(pool):
        await as_options(pool, 14)
        await service(pool).ensure(14, at(date(2026, 8, 3), 9, 15), at(DAY, 15, 30))
        assert len(k.calls) >= 3
        for _, s, e in k.calls:
            assert (e - s).days <= C.MAX_DAYS_PER_REQUEST
    pool_run(go)


def test_no_kite_session_reports_and_keeps_what_is_stored(pool_run, monkeypatch, frozen_today):
    async def no_session(*a):
        raise C.NoKiteSession("no session")
    monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", no_session)

    async def go(pool):
        await as_options(pool, 15, 16)
        await queries.upsert_candles(pool, [(15, at(DAY, 10, 0), 1, 1, 1, 1, None)], "ticks")
        candles, src, err = await service(pool).series(15, at(DAY, 9, 15), at(DAY, 15, 30))
        assert len(candles) == 1 and src == "stored_partial" and err is None
        candles, src, err = await service(pool).series(16, at(DAY, 9, 15), at(DAY, 15, 30))
        assert candles == [] and "logged in" in err
    pool_run(go)


# ── After the close ───────────────────────────────────────────────────

async def alone(pool):
    """The backfill fetches what every deployment held that day: other
    tests' deployments (the strategy simulations trade the same days in
    this worker's database) would be fetched too."""
    await pool.execute("DELETE FROM deployments")


def test_backfill_fetches_each_held_instrument_once(pool_run, monkeypatch, frozen_today):
    k = _mk(monkeypatch)

    async def go(pool):
        await alone(pool)
        intra = (await queries.create_deployment(pool, "I", "intraday_dtt_simple", "intraday", 500000, {}))["id"]
        pos = (await queries.create_deployment(pool, "P", "strangle_monthly_v2", "positional", 500000, {}))["id"]
        sym = "NIFTY26O2723000CE"
        await fill(pool, intra, sym, 900, "sell", 75, 100, at(DAY, 10, 0))
        await fill(pool, intra, sym, 900, "buy", 75, 90, at(DAY, 11, 0))
        await fill(pool, pos, sym, 900, "sell", 75, 100, at(DAY - timedelta(days=2), 10, 0))   # still open
        await fill(pool, pos, "NIFTY26O2722000PE", 901, "sell", 75, 50, at(DAY, 9, 30))
        await fill(pool, pos, "NIFTY26O2721000PE", 902, "sell", 75, 50, at(DAY - timedelta(days=5), 9, 30))
        await fill(pool, pos, "NIFTY26O2721000PE", 902, "buy", 75, 40, at(DAY - timedelta(days=4), 9, 30))  # not held today
        r = await service(pool).backfill_day(DAY)
        assert sorted(c[0] for c in k.calls) == [900, 901], "each held option contract once"
        assert r["instruments"] == 2 and r["rows"] == 2 * 375
        c = await queries.get_contract(pool, 900)
        assert c["tradingsymbol"] == sym and c["expiry"] == date(2026, 10, 27) and float(c["strike"]) == 23000
        again = await service(pool).backfill_day(DAY)
        assert again["fetched"] == [], "a re-run fetches nothing"
        # stored once, readable at any interval
        assert await pool.fetchval("SELECT count(*) FROM candles_1m WHERE instrument_token = 900") == 375
    pool_run(go)



# ── Indices and stocks: Kite's, never stored ──────────────────────────

def test_indices_come_from_kite_at_the_chart_interval_and_are_never_stored(pool_run, monkeypatch, frozen_today):
    asked = []

    async def history(resolver, token, start, end, interval):
        asked.append((token, interval))
        return [{"date": start.replace(tzinfo=IST) + timedelta(minutes=15 * i), "open": 1, "high": 2, "low": 0, "close": 1}
                for i in range(25)]
    monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", history)

    async def go(pool):
        svc = service(pool)
        w = (at(DAY, 9, 15), at(DAY, 15, 30))
        candles, src, err = await svc.series(256265, *w, "15minute")
        assert src == "kite" and len(candles) == 25 and err is None
        assert asked == [(256265, "15minute")], "Kite's own interval: no 1-minute fetch, no rollup"
        await svc.series(256265, *w, "15minute")
        assert len(asked) == 1, "a finished window is served from memory"
        assert await svc.ensure(256265, *w) == 0, "the store refuses a non-option"
        assert await pool.fetchval("SELECT count(*) FROM candles_1m") == 0, "nothing stored"
    pool_run(go)


def test_index_without_kite_says_so(pool_run, monkeypatch, frozen_today):
    async def no_session(*a):
        raise C.NoKiteSession("no session")
    monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", no_session)

    async def go(pool):
        candles, src, err = await service(pool).series(256265, at(DAY, 9, 15), at(DAY, 15, 30), "5minute")
        assert candles == [] and src is None and "logged in" in err
    pool_run(go)


def test_backfill_skips_held_futures_and_stocks(pool_run, monkeypatch, frozen_today):
    k = _mk(monkeypatch)

    async def go(pool):
        await alone(pool)
        d = (await queries.create_deployment(pool, "F", "calendar_btst", "positional", 1e7, {}))["id"]
        await fill(pool, d, "NIFTY26NOVFUT", 990, "buy", 75, 25000, at(DAY, 10, 0))
        await fill(pool, d, "NIFTY26NOV25000PE", 991, "sell", 75, 100, at(DAY, 10, 0))
        await service(pool).backfill_day(DAY)
        assert [c[0] for c in k.calls] == [991]
    pool_run(go)


def test_migration_0022_removes_stored_index_candles_and_keeps_options(pool_run):
    from pathlib import Path
    sql = (Path(C.__file__).parent / "db" / "migrations" / "0022_candles_options_only.sql").read_text()

    async def go(pool):
        await alone(pool)
        d = (await queries.create_deployment(pool, "D", "strangle_monthly_v2", "positional", 1e6, {}))["id"]
        await fill(pool, d, "NIFTY26NOV25000CE", 31, "sell", 75, 100, at(DAY, 10, 0))
        await queries.upsert_contracts(pool, [{"instrument_token": 32, "tradingsymbol": "NIFTY26DEC24000PE", "instrument_type": "PE"},
                                              {"instrument_token": 33, "tradingsymbol": "NIFTY26DECFUT", "instrument_type": "FUT"}])
        m = at(DAY, 10, 0)
        for tok in (31, 32, 33, 256265):
            await queries.upsert_candles(pool, [(tok, m, 1, 1, 1, 1, None)], "ticks")
        await pool.execute(sql)
        left = sorted(r["instrument_token"] for r in await pool.fetch("SELECT instrument_token FROM candles_1m"))
        assert left == [31, 32], "options kept (from a position or the contracts table); index and future gone"
        assert [r["instrument_token"] for r in await pool.fetch("SELECT instrument_token FROM contracts")] == [32]
    pool_run(go)



def test_status_never_shows_not_running_beside_the_previous_result(pool_run, monkeypatch):
    """A run that finishes while the status is being read: the status must
    either still show it running, or show its own result, never "nothing
    running" beside the run before it (a reader would take that stale
    result for the new run's)."""
    async def go(pool):
        svc = service(pool)
        await queries.set_app_scratch(pool, "candle_backfill_last", {"day": "2026-10-19", "fetched": 2, "complete": True})
        svc.progress = svc._waiting(date(2026, 10, 19))
        real = queries.get_app_scratch

        async def finishes_meanwhile(pool_, key):
            old = await real(pool_, key)
            # the run saves its result, then stops showing as running
            await queries.set_app_scratch(pool, "candle_backfill_last", {"day": "2026-10-19", "fetched": 0, "complete": True})
            svc.progress = None
            return old
        monkeypatch.setattr(queries, "get_app_scratch", finishes_meanwhile)
        st = await svc.backfill_status()
        assert st["running"] is not None or st["last"]["fetched"] == 0, st
        monkeypatch.setattr(queries, "get_app_scratch", real)
        st = await svc.backfill_status()
        assert st["running"] is None and st["last"]["fetched"] == 0, "and next time, the new result"
    pool_run(go)


def test_next_run_is_today_at_1545_until_done_then_the_next_weekday(pool_run):
    async def go(pool):
        svc = service(pool)
        assert C.BACKFILL_AT == time(15, 45)
        wed = datetime(2026, 10, 14, 11, 0, tzinfo=IST)
        assert (await svc.backfill_status(at=wed))["next_at"] == "2026-10-14T15:45:00+05:30"
        await queries.set_app_scratch(pool, "candle_backfill_last", {"day": "2026-10-14", "complete": True})
        assert (await svc.backfill_status(at=wed))["next_at"] == "2026-10-15T15:45:00+05:30"
        await queries.set_app_scratch(pool, "candle_backfill_last", {"day": "2026-10-14", "complete": False})
        assert (await svc.backfill_status(at=wed))["next_at"] == "2026-10-14T15:45:00+05:30", "incomplete: still due"
        fri_done = datetime(2026, 10, 16, 18, 0, tzinfo=IST)
        await queries.set_app_scratch(pool, "candle_backfill_last", {"day": "2026-10-16", "complete": True})
        assert (await svc.backfill_status(at=fri_done))["next_at"] == "2026-10-19T15:45:00+05:30", "skips the weekend"
        sat = datetime(2026, 10, 17, 10, 0, tzinfo=IST)
        assert (await svc.backfill_status(at=sat))["next_at"] == "2026-10-19T15:45:00+05:30"
    pool_run(go)


def test_two_backfills_never_run_at_once(pool_run, monkeypatch, frozen_today):
    k = _mk(monkeypatch, delay=0.02)

    async def go(pool):
        await alone(pool)
        d = (await queries.create_deployment(pool, "D", "strangle_monthly_v2", "positional", 1e6, {}))["id"]
        await fill(pool, d, "NIFTY26O2725000CE", 41, "sell", 75, 100, at(DAY, 10, 0))
        await fill(pool, d, "NIFTY26O2725000PE", 42, "sell", 75, 100, at(DAY, 10, 0))
        svc = service(pool)
        r1, r2 = await asyncio.gather(svc.backfill_day(DAY), svc.backfill_day(DAY))
        assert sorted(c[0] for c in k.calls) == [41, 42], "the second run found everything already fetched"
        assert sorted([len(r1["fetched"]), len(r2["fetched"])]) == [0, 2]
        assert svc.progress is None
    pool_run(go)


def test_result_is_saved_before_the_run_stops_showing_as_running(pool_run, monkeypatch, frozen_today):
    """Regression: progress was cleared before the result was saved, so a
    status read in between saw neither a running nor a finished run."""
    _mk(monkeypatch)
    seen = []
    real = queries.set_app_scratch

    async def go(pool):
        svc = service(pool)

        async def spy(pool_, key, value):
            seen.append(svc.progress is not None)       # still showing as running when the result lands
            return await real(pool_, key, value)
        monkeypatch.setattr(queries, "set_app_scratch", spy)
        await alone(pool)
        d = (await queries.create_deployment(pool, "D", "strangle_monthly_v2", "positional", 1e6, {}))["id"]
        await fill(pool, d, "NIFTY26O2725000CE", 51, "sell", 75, 100, at(DAY, 10, 0))
        await svc.backfill_day(DAY)
        assert seen == [True]
        assert svc.progress is None
    pool_run(go)


def test_start_backfill_shows_as_running_immediately(pool_run, monkeypatch, frozen_today):
    _mk(monkeypatch)

    async def go(pool):
        svc = service(pool)
        task = svc.start_backfill(DAY)
        st = await svc.backfill_status()
        assert st["running"] is not None and st["running"]["day"] == DAY.isoformat()
        await task
        assert (await svc.backfill_status())["running"] is None
    pool_run(go)


def test_backfill_keeps_the_traded_indexs_current_and_next_month_futures_with_their_history(pool_run, monkeypatch, frozen_today):
    """Futures expire like options (Kite drops their history): the current
    and next-month future of every traded index is kept, back KEEP_DAYS on
    the first run, only the new day after that; a stopped deployment's
    index isn't."""
    from app import futures as F
    k = _mk(monkeypatch)
    rows = {"NIFTY": [{"instrument_token": 7001, "tradingsymbol": "NIFTY26OCTFUT", "exchange": "NFO", "name": "NIFTY", "expiry": date(2026, 10, 27)},
                      {"instrument_token": 7002, "tradingsymbol": "NIFTY26NOVFUT", "exchange": "NFO", "name": "NIFTY", "expiry": date(2026, 11, 24)}],
            "SENSEX": [{"instrument_token": 7101, "tradingsymbol": "SENSEX26OCTFUT", "exchange": "BFO", "name": "SENSEX", "expiry": date(2026, 10, 29)}]}
    asked = []

    async def listed(dispatcher, underlying, on, n=2):
        asked.append(underlying)
        return rows.get(underlying, [])
    monkeypatch.setattr(F, "listed", listed)

    async def go(pool):
        await queries.create_deployment(pool, "N", "strangle_monthly_v2", "positional", 1e6, {"instrument": "NIFTY", "instrument_tokens": [256265]})
        s = await queries.create_deployment(pool, "S", "intraday_dtt_simple", "intraday", 1e6, {"instrument_tokens": [265]})
        await queries.set_status(pool, s["id"], "stopped")
        r = await service(pool).backfill_day(DAY)
        assert asked == ["NIFTY"], "only indices a running deployment trades"
        assert sorted({c[0] for c in k.calls}) == [7001, 7002]
        first = min(c[1] for c in k.calls if c[0] == 7002)
        assert (DAY - first.date()).days >= F.KEEP_DAYS - 3, "history back KEEP_DAYS (to the first trading day)"
        assert r["instruments"] == 2 and r["rows"] > 2 * 375 * 30
        c = await queries.get_contract(pool, 7002)
        assert c["instrument_type"] == "FUT" and c["expiry"] == date(2026, 11, 24) and c["underlying"] == "NIFTY"
        k.calls.clear()
        again = await service(pool).backfill_day(DAY)
        assert again["fetched"] == [] and k.calls == [], "a re-run fetches nothing"
        # stored futures read back as stored, like options
        rows_, src, _ = await service(pool).series(7002, at(DAY, 9, 15), at(DAY, 15, 30), "5minute", fetch=False)
        assert src == "stored" and len(rows_) == 75
        known = await F.known(pool, "NIFTY")
        assert [f["tradingsymbol"] for f in known] == ["NIFTY26OCTFUT", "NIFTY26NOVFUT"]
        assert F.pick(known, date(2026, 11, 24), DAY)["tradingsymbol"] == "NIFTY26NOVFUT"    # a November option: November's future
        assert F.pick(known, date(2026, 10, 20), DAY)["tradingsymbol"] == "NIFTY26OCTFUT"    # a weekly: the current month
        assert F.pick(known, None, DAY)["tradingsymbol"] == "NIFTY26OCTFUT"
        assert F.pick(known, date(2026, 12, 29), DAY) is None, "no future that far out: ignored"
        assert F.pick(known, None, date(2026, 11, 30)) is None
        # a monthly symbol only gives its month (expiry read as the month's last day): still that month's future
        assert F.pick(known, date(2026, 11, 30), DAY)["tradingsymbol"] == "NIFTY26NOVFUT"
    pool_run(go)


def test_underlying_of_a_deployment():
    from app import futures as F
    assert F.underlying_of({"instrument": "nifty"}) == "NIFTY"
    assert F.underlying_of({"options_underlying": "SENSEX", "instrument_tokens": [256265]}) == "SENSEX"
    assert F.underlying_of({"instrument_tokens": [260105]}) == "BANKNIFTY"
    assert F.underlying_of({"instrument_tokens": [123]}) is None and F.underlying_of({}) is None



def test_pick_the_same_expiry_else_the_months_future_else_none():
    from app import futures as F
    fs = [{"tradingsymbol": "OCT", "expiry": date(2026, 10, 27)}, {"tradingsymbol": "NOV", "expiry": date(2026, 11, 24)},
          {"tradingsymbol": "NOVW", "expiry": date(2026, 11, 10)}, {"tradingsymbol": "DEC", "expiry": date(2026, 12, 29)}]
    on = date(2026, 10, 21)
    assert F.pick(fs, date(2026, 11, 10), on)["tradingsymbol"] == "NOVW", "a future with the leg's own expiry"
    assert F.pick(fs, date(2026, 11, 24), on)["tradingsymbol"] == "NOV"
    assert F.pick(fs, date(2026, 11, 17), on)["tradingsymbol"] == "NOV", "else the monthly future of its month (its last expiry)"
    assert F.pick(fs, date(2026, 10, 20), on)["tradingsymbol"] == "OCT", "a weekly takes its month's future"
    assert F.pick(fs, date(2027, 12, 28), on) is None, "next year's December: no future, ignored"
    assert F.pick(fs, None, on)["tradingsymbol"] == "OCT", "no leg: the nearest live"


def test_backfill_also_keeps_the_future_of_every_held_legs_month(pool_run, monkeypatch, frozen_today):
    """A held December leg gets December's future stored (beyond the
    current and next month); a leg with no future listed that far out
    gets none."""
    from app import futures as F
    k = _mk(monkeypatch)
    rows = [{"instrument_token": 7001 + i, "tradingsymbol": f"NIFTY26{m}FUT", "exchange": "NFO", "name": "NIFTY", "expiry": e}
            for i, (m, e) in enumerate([("OCT", date(2026, 10, 27)), ("NOV", date(2026, 11, 24)), ("DEC", date(2026, 12, 29)), ("JAN", date(2027, 1, 26))])]

    async def listed(dispatcher, underlying, on, n=None):
        return rows if underlying == "NIFTY" else []
    monkeypatch.setattr(F, "listed", listed)

    async def go(pool):
        d = (await queries.create_deployment(pool, "N", "strangle_monthly_v2", "positional", 1e6, {"instrument": "NIFTY"}))["id"]
        await fill(pool, d, "NIFTY26DEC25000CE", 801, "sell", 75, 100, at(DAY, 10, 0))
        await fill(pool, d, "NIFTY27DEC25000PE", 802, "sell", 75, 100, at(DAY, 10, 0))     # next year's December: no future
        await service(pool).backfill_day(DAY)
        futs = sorted({c[0] for c in k.calls} - {801, 802})
        assert futs == [7001, 7002, 7003], "October and November, plus December for the December leg; not January"
        assert (await queries.get_contract(pool, 7003))["instrument_type"] == "FUT"
    pool_run(go)


# ── What a contract is ──────────────────────────────────────────────────

def test_a_monthly_contract_is_stored_with_kites_expiry_or_none_never_the_months_last_day(pool_run, monkeypatch):
    """NIFTY26OCT22550CE: the symbol says October, not the day. Kite says
    27 Oct; the month's last day (31 Oct) was stored as fact and read back
    as an expiry by the end-of-day check."""
    from app.options import resolver as R

    async def go(pool):
        svc = service(pool)
        monkeypatch.setitem(R._INSTRUMENT_CACHE, "NFO", {"date": DAY, "by_name": {}, "data": [
            {"instrument_token": 801, "tradingsymbol": "NIFTY26OCT22550CE", "expiry": date(2026, 10, 27)}]})
        await svc.remember_contracts([(801, "NIFTY26OCT22550CE"), (802, "NIFTY26OCT22600CE"), (803, "NIFTY26O2025000CE")])
        got = {r["instrument_token"]: r["expiry"] for r in await pool.fetch("SELECT instrument_token, expiry FROM contracts")}
        assert got == {801: date(2026, 10, 27), 802: None, 803: date(2026, 10, 20)}, \
            "Kite's word; none when Kite isn't loaded; a weekly symbol's own day"
    pool_run(go)


def test_the_migration_clears_monthly_expiries_guessed_from_the_symbol(pool_run):
    from pathlib import Path
    sql = (Path(C.__file__).parent / "db" / "migrations" / "0039_monthly_expiry_guesses.sql").read_text()

    async def go(pool):
        rows = [(901, "NIFTY26OCT22550CE", date(2026, 10, 31), "CE"),     # the guess
                (902, "NIFTY26NOV22550PE", date(2026, 11, 24), "PE"),     # Kite's own
                (903, "NIFTY26O2025000CE", date(2026, 10, 20), "CE"),     # a weekly symbol's day
                (904, "NIFTY26OCTFUT", date(2026, 10, 27), "FUT")]
        for tok, sym, exp, typ in rows:
            await pool.execute("INSERT INTO contracts (instrument_token, tradingsymbol, underlying, expiry, instrument_type) "
                               "VALUES ($1, $2, 'NIFTY', $3, $4)", tok, sym, exp, typ)
        await pool.execute(sql)
        got = {r["instrument_token"]: r["expiry"] for r in await pool.fetch("SELECT instrument_token, expiry FROM contracts")}
        assert got == {901: None, 902: date(2026, 11, 24), 903: date(2026, 10, 20), 904: date(2026, 10, 27)}
    pool_run(go)
