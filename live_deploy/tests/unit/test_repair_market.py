"""The repair's replayed market (app/repair/market.py): four ticks a
minute with CE and PE on opposite extremes, prices carried over a minute
with no trade, Kite answered at the replay's moment, history that never
reaches the future, and everything patched put back."""

from datetime import date, datetime, time, timedelta

import pytest

from app.repair import market as M

DAY = date(2026, 10, 21)
NIFTY, CE, PE = 256265, 111, 222


def at(hhmm, day=DAY):
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time())


def book_with(rows):
    """rows: {token: {"HH:MM": (o, h, l, c)}}"""
    def source(token, start, end):
        return [{"date": at(k).replace(tzinfo=M.IST), "open": o, "high": h, "low": l, "close": c, "volume": 10, "oi": 500}
                for k, (o, h, l, c) in sorted(rows.get(token, {}).items())]
    b = M.MinuteBook(DAY, source)
    b.note(CE, "NIFTY26OCT2725000CE")
    b.note(PE, "NIFTY26OCT2725000PE")
    b.note(NIFTY, "NIFTY 50")
    return b


ROWS = {NIFTY: {"12:00": (100, 110, 90, 105), "12:01": (105, 106, 104, 104), "12:03": (104, 109, 101, 108)},
        CE: {"12:00": (50, 60, 41, 55), "12:01": (55, 56, 54, 54)},
        PE: {"12:00": (40, 48, 31, 36)}}


def test_ce_is_at_its_high_and_pe_at_its_low_when_the_index_is_at_its_high():
    b = book_with(ROWS)
    m = at("12:00")
    assert [b.at(NIFTY, m, p) for p in M.UP_FIRST] == [100, 110, 90, 105]
    assert [b.at(CE, m, p) for p in M.UP_FIRST] == [50, 60, 41, 55]
    assert [b.at(PE, m, p) for p in M.UP_FIRST] == [40, 31, 48, 36]     # opposite extremes
    assert M.moves_against("INDIA VIX") and not M.moves_against("NIFTY 50")


def test_a_minute_with_no_trade_keeps_the_last_close_and_its_older_trade_time():
    b = book_with(ROWS)
    px, traded = b.price(PE, at("12:02"), "up", at("12:02") + timedelta(seconds=20))
    assert px == 36 and traded == at("12:00") + timedelta(seconds=59)
    px, traded = b.price(CE, at("12:00"), "up", at("12:00") + timedelta(seconds=20))
    assert px == 60 and traded == at("12:00") + timedelta(seconds=20)
    assert b.price(CE, at("11:59"), "open", at("11:59")) is None          # before its first trade


def test_natural_order_reads_green_as_down_first_and_red_as_up_first():
    assert M.natural_order({"open": 100, "close": 105}) == "down_first"
    assert M.natural_order({"open": 100, "close": 95}) == "up_first"
    assert M.natural_order({"open": 100, "close": 100}) == "down_first"
    assert M.natural_order(None) == "down_first"


def test_the_clock_walks_four_ticks_a_minute():
    c = M.ReplayClock(DAY)
    c.set(at("12:00"), M.UP_FIRST, 0)
    seen = []
    for step in range(4):
        c.set(at("12:00"), M.UP_FIRST, step)
        seen.append((c.phase, c.now.second))
    assert seen == [("open", 0), ("up", 20), ("down", 40), ("close", 59)]
    assert c.played == M.UP_FIRST and c.now_ist().tzinfo is not None


class Real:
    def __init__(self):
        self.asked = []

    def instruments(self, exchange=None):
        return [{"instrument_token": CE, "tradingsymbol": "NIFTY26OCT2725000CE", "exchange": "NFO", "instrument_type": "CE"},
                {"instrument_token": PE, "tradingsymbol": "NIFTY26OCT2725000PE", "exchange": "NFO", "instrument_type": "PE"}]

    def historical_data(self, token, frm, to, interval, **kw):
        self.asked.append((token, frm, to, interval))
        d = datetime.combine(DAY - timedelta(days=1), time(15, 25))
        return [{"date": d, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]


def kite_at(hhmm, step, order=M.UP_FIRST):
    b = book_with(ROWS)
    c = M.ReplayClock(DAY)
    c.set(at(hhmm), order, step)
    return M.ReplayKite(Real(), b, c), c


def test_kite_ltp_and_quote_answer_at_the_replays_moment():
    k, c = kite_at("12:00", 1)       # the up tick
    got = k.ltp(["NFO:NIFTY26OCT2725000CE", "NFO:NIFTY26OCT2725000PE", "NSE:NIFTY 50", "NFO:NOPE"])
    assert {key: v["last_price"] for key, v in got.items()} == {
        "NFO:NIFTY26OCT2725000CE": 60, "NFO:NIFTY26OCT2725000PE": 31, "NSE:NIFTY 50": 110}
    q = k.quote([str(CE)])[str(CE)]
    assert q["instrument_token"] == CE and q["last_price"] == 60 and q["oi"] == 500
    assert q["depth"]["buy"][0]["price"] < 60 < q["depth"]["sell"][0]["price"]


def test_history_stops_at_now_with_the_forming_candle_partial():
    k, c = kite_at("12:00", 1)       # 12:00:20, after the open and the high
    today = k.historical_data(NIFTY, at("09:15"), at("15:30"), "minute")
    assert [x["date"].strftime("%H:%M") for x in today] == ["12:00"]
    assert today[0]["high"] == 110 and today[0]["low"] == 100 and today[0]["close"] == 110   # the low isn't played yet
    k, c = kite_at("12:03", 3)
    five = k.historical_data(NIFTY, at("09:15"), at("15:30"), "5minute")
    assert len(five) == 1 and five[0]["date"].strftime("%H:%M") == "12:00"
    assert (five[0]["open"], five[0]["high"], five[0]["low"], five[0]["close"]) == (100, 110, 90, 108)
    day = k.historical_data(NIFTY, at("09:15") - timedelta(days=5), at("15:30"), "day")
    assert day[0]["close"] == 1.5 and day[-1]["close"] == 108          # earlier days from Kite, today built here
    # Kite is never asked for anything after midnight before the replay day
    assert all(to < datetime.combine(DAY, time(0)) for _, _, to, _ in k.real.asked)


def test_dispatcher_prices_only_what_is_subscribed():
    b = book_with(ROWS)
    c = M.ReplayClock(DAY)
    c.set(at("12:00"), M.DOWN_FIRST, 1)   # the down tick
    d = M.ReplayDispatcher(b, c)
    d.publish()
    assert d.last_prices == {}
    d.add_instruments([{"instrument_token": PE, "symbol": "NIFTY26OCT2725000PE"}])
    assert d.last_prices == {PE: 48}
    saved = d.save()
    c.set(at("12:00"), M.DOWN_FIRST, 3)
    d.publish()
    assert d.last_prices[PE] == 36
    d.load(saved)
    assert d.last_prices[PE] == 48


def test_replaying_patches_kite_and_the_clock_and_puts_them_back():
    import app.clock as clock
    import app.options.resolver as R
    from app import vix
    k, c = kite_at("12:00", 0)
    before = (R.get_kite_connect, clock.now_ist, vix.state.get("token"))
    with M.replaying(k, c):
        assert R.get_kite_connect(None) is k
        assert clock.now_ist() == at("12:00").replace(tzinfo=M.IST)
        assert vix.state["token"] == M.VIX_TOKEN
    assert (R.get_kite_connect, clock.now_ist, vix.state.get("token")) == before


def test_a_failing_source_falls_back_to_stored_candles_and_says_so():
    def boom(token, start, end):
        raise RuntimeError("instrument expired")
    b = M.MinuteBook(DAY, boom, stored={CE: [{"date": at("12:00"), "open": 1, "high": 2, "low": 1, "close": 2}]})
    assert b.candle(CE, at("12:00"))["close"] == 2 and b.origin[CE] == "stored" and "expired" in b.errors[CE]
    assert b.load(PE) == {} and b.origin[PE] == "none"


def test_kite_gate_retries_a_rate_limit_then_gives_up():
    calls = []

    class K:
        def historical_data(self, *a, **kw):
            calls.append(kw)
            if len(calls) < 3:
                raise Exception("Too many requests")
            return ["ok"]
    g = M.KiteGate(K(), min_gap=0, attempts=4)
    import app.repair.market as mm
    real_sleep = mm._time.sleep
    mm._time.sleep = lambda s: None
    try:
        assert g.historical_data(1, None, None, "minute") == ["ok"] and len(calls) == 3
        calls.clear()
        g2 = M.KiteGate(type("K2", (), {"historical_data": lambda self, *a, **k: (_ for _ in ()).throw(Exception("Too many requests"))})(), min_gap=0, attempts=2)
        with pytest.raises(Exception, match="Too many"):
            g2.historical_data(1)
    finally:
        mm._time.sleep = real_sleep
