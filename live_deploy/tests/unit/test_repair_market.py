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


# ── legs Kite has nothing for: by name on their own exchange, else by model ──

SENSEX, SX_CE, SX_PE, MASTER_CE = 265, 701, 702, 9901
SX_EXPIRY = date(2026, 10, 29)


class FakeGate:
    def __init__(self, answers, masters=None):
        self.answers, self.masters, self.calls = answers, masters or {}, []

    def historical_data(self, token, start, end, interval, **kw):
        self.calls.append((token, kw.get("oi", False)))
        got = self.answers.get((token, kw.get("oi", False)))
        if isinstance(got, Exception):
            raise got
        return got

    def instruments(self, exchange=None):
        self.calls.append(("instruments", exchange))
        return self.masters.get(exchange, [])


def test_an_empty_answer_with_oi_is_asked_again_without_it():
    rows = [{"date": at("12:00"), "open": 1, "high": 1, "low": 1, "close": 1}]
    gate = FakeGate({(5, True): [], (5, False): rows, (6, True): Exception("no oi"), (6, False): rows,
                     (7, True): rows})
    fetch = M.kite_minute_source(gate)
    assert fetch(5, at("09:15"), at("15:30")) == rows
    assert fetch(6, at("09:15"), at("15:30")) == rows
    assert fetch(7, at("09:15"), at("15:30")) == rows
    assert gate.calls == [(5, True), (5, False), (6, True), (6, False), (7, True)]
    assert M.kite_minute_source(FakeGate({}))(8, at("09:15"), at("15:30")) == []


def test_kite_lookup_finds_a_contract_on_its_own_exchange():
    gate = FakeGate({}, {"BFO": [{"instrument_token": MASTER_CE, "tradingsymbol": "SENSEX26N0577700CE"}],
                         "NFO": [{"instrument_token": 42, "tradingsymbol": "NIFTY26OCT25000CE", "exchange": "NFO"}]})
    look = M.kite_lookup(gate)
    assert look("SENSEX26N0577700CE") == {"instrument_token": MASTER_CE, "tradingsymbol": "SENSEX26N0577700CE", "exchange": "BFO"}
    assert look("NIFTY26OCT25000CE")["instrument_token"] == 42
    assert look("BANKEX26NOV66300CE") is None and look("SENSEX") is None
    assert [c for c in gate.calls] == [("instruments", "BFO"), ("instruments", "NFO")]   # each master once


def test_a_leg_kite_has_nothing_for_by_its_token_is_fetched_by_its_name():
    """The app's token for a BFO leg answers nothing; the master's token
    for the same tradingsymbol has the day."""
    def source(token, start, end):
        return [{"date": at("12:00"), "open": 80, "high": 90, "low": 70, "close": 85}] if token == MASTER_CE else []
    b = M.MinuteBook(DAY, source, lookup=lambda s: {"instrument_token": MASTER_CE, "tradingsymbol": s, "exchange": "BFO"})
    b.note(SX_CE, "SENSEX26N0577700CE")
    assert b.at(SX_CE, at("12:00"), "close") == 85 and b.origin[SX_CE] == "kite"


def sensex_index(level=80000.0):
    return {"12:00": (level, level + 200, level - 200, level), "12:01": (level, level + 50, level - 50, level + 20)}


def model_book(anchor_source=None, lookup=None, symbol_ce="SENSEX26O2981000CE", symbol_pe="SENSEX26O2979000PE"):
    idx = sensex_index()

    def source(token, start, end):
        if token == SENSEX:
            if start.date() < DAY:   # an earlier day's index, for an anchor then
                return (anchor_source or {}).get(("idx", start.date()), [])
            return [{"date": at(k), "open": o, "high": h, "low": l, "close": c} for k, (o, h, l, c) in idx.items()]
        if start.date() < DAY:
            return (anchor_source or {}).get((token, "before"), [])
        return []
    b = M.MinuteBook(DAY, source, lookup=lookup)
    b.note(SENSEX, "SENSEX")
    b.note(SX_CE, symbol_ce)
    b.note(SX_PE, symbol_pe)
    return b


def test_a_leg_with_no_trade_all_day_is_priced_by_model_from_its_fill():
    from app.geek.optionmath import fwd_price, years_to
    b = model_book()
    t0 = at("11:00")
    true_vol = 0.14
    # the fill, made when SENSEX stood where it opens at noon (no candle at 11:00 -> its first open)
    p_ce = fwd_price("CE", 80000, 81000, years_to(SX_EXPIRY, t0.replace(tzinfo=M.IST)), true_vol)
    p_pe = fwd_price("PE", 80000, 79000, years_to(SX_EXPIRY, t0.replace(tzinfo=M.IST)), true_vol)
    b.anchor(SX_CE, p_ce, t0.replace(tzinfo=M.IST), "the fill at 21 Oct 11:00")
    b.anchor(SX_PE, p_pe, t0, "the fill at 21 Oct 11:00")
    ce, pe = b.load(SX_CE), b.load(SX_PE)
    assert b.origin[SX_CE] == b.origin[SX_PE] == "model"
    assert abs(b.models[SX_CE]["iv"] - true_vol) < 0.002 and b.models[SX_CE]["index"] == "SENSEX"
    assert b.models[SX_CE]["anchor"] == "the fill at 21 Oct 11:00" and b.models[SX_CE]["level"] == 80000
    c, p = ce[at("12:00")], pe[at("12:00")]
    assert c["low"] < c["open"] < c["high"] and p["low"] < p["open"] < p["high"]
    # opposite extremes: at SENSEX's high the CE is at its high and the PE at its low
    assert b.at(SX_CE, at("12:00"), "up") == c["high"] and b.at(SX_PE, at("12:00"), "up") == p["low"]
    noon = fwd_price("CE", 80000, 81000, years_to(SX_EXPIRY, at("12:00").replace(second=30, tzinfo=M.IST)), true_vol)
    assert 0 < p_ce - noon < 3 and abs(c["open"] - noon) < 0.1    # an hour's decay, nothing else
    assert all(round(v / 0.05, 6) % 1 == 0 for v in c.values() if isinstance(v, float))
    assert c["volume"] == 0 and set(ce) == {at("12:00"), at("12:01")}


def test_with_no_fill_the_model_starts_from_its_last_trade_in_the_days_before():
    """A new leg (an adjustment strike) has no fill: its last trade in the
    week before is where the model starts, with SENSEX at that moment."""
    from app.geek.optionmath import fwd_price, years_to
    prev = date(2026, 10, 20)
    t = datetime.combine(prev, time(15, 10))
    p = fwd_price("CE", 79500, 81000, years_to(SX_EXPIRY, (t + timedelta(seconds=59)).replace(tzinfo=M.IST)), 0.16)
    anchors = {(SX_CE, "before"): [{"date": t - timedelta(days=1), "open": 9, "high": 9, "low": 9, "close": 9},
                                   {"date": t, "open": p, "high": p, "low": p, "close": p}],
               ("idx", prev): [{"date": t, "open": 79500, "high": 79500, "low": 79500, "close": 79500}]}
    b = model_book(anchors)
    assert b.load(SX_CE) and b.origin[SX_CE] == "model"
    assert abs(b.models[SX_CE]["iv"] - 0.16) < 0.003 and b.models[SX_CE]["anchor"] == "its last trade, 20 Oct 15:10"


def test_the_latest_known_price_wins_and_an_unusable_one_is_skipped():
    b = model_book()
    b.anchor(SX_CE, 300.0, at("10:00"), "older")
    b.anchor(SX_CE, 250.0, at("11:30"), "newer")
    b.anchor(SX_CE, 0, at("11:45"), "zero")                      # ignored on the way in
    b.load(SX_CE)
    assert [a[2] for a in b.anchors[SX_CE]] == ["older", "newer"]
    assert b.models[SX_CE]["anchor"] == "newer"
    # a PE priced under its intrinsic value has no implied vol: the older usable one is used
    b2 = model_book(symbol_pe="SENSEX26O2981000PE")
    b2.anchor(SX_PE, 2000.0, at("10:00"), "usable")
    b2.anchor(SX_PE, 100.0, at("11:30"), "under intrinsic")    # 1,000 in the money
    b2.load(SX_PE)
    assert b2.models[SX_PE]["anchor"] == "usable"


def test_no_known_price_or_an_unmodelable_contract_stays_without_prices():
    b = model_book(lookup=lambda s: None)
    assert b.load(SX_CE) == {} and b.origin[SX_CE] == "none"
    assert b.tried[SX_CE] == f"token {SX_CE} — not in Kite's instrument list"
    # expired by its own symbol, a stock option, a future: never modelled
    for sym in ("SENSEX26O1481000CE", "RELIANCE26OCT3000CE", "SENSEX26NOVFUT"):
        bb = model_book(symbol_ce=sym)
        bb.anchor(SX_CE, 100.0, at("11:00"), "fill")
        assert bb.load(SX_CE) == {} and bb.origin[SX_CE] == "none", sym


def test_the_master_expiry_is_used_over_the_symbols_month_end():
    """A monthly symbol only names the month; the master has the day."""
    seen = {}
    b = model_book(lookup=lambda s: {"instrument_token": SX_CE, "tradingsymbol": s, "exchange": "BFO",
                                     "expiry": date(2026, 11, 26)}, symbol_ce="SENSEX26NOV81000CE")
    b.anchor(SX_CE, 900.0, at("11:00"), "fill")
    b.load(SX_CE)
    seen["master"] = b.models[SX_CE]["iv"]
    b2 = model_book(symbol_ce="SENSEX26NOV81000CE")
    b2.anchor(SX_CE, 900.0, at("11:00"), "fill")
    b2.load(SX_CE)
    seen["month_end"] = b2.models[SX_CE]["iv"]
    assert seen["master"] > seen["month_end"]   # less time to expiry -> more vol for the same price
