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


def test_the_day_is_one_request_and_only_an_error_retries_without_oi():
    """An empty answer is the same with or without OI: asking twice doubled
    the requests for every untraded strike (the BANKEX scan crawled)."""
    rows = [{"date": at("12:00"), "open": 1, "high": 1, "low": 1, "close": 1}]
    gate = FakeGate({(5, True): [], (5, False): rows, (6, True): Exception("no oi"), (6, False): rows,
                     (7, True): rows})
    fetch = M.kite_minute_source(gate)
    assert fetch(5, at("09:15"), at("15:30")) == []
    assert fetch(6, at("09:15"), at("15:30")) == rows
    assert fetch(7, at("09:15"), at("15:30")) == rows
    assert gate.calls == [(5, True), (6, True), (6, False), (7, True)]
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
    # expired by its own symbol, a stock option: never modelled
    for sym in ("SENSEX26O1481000CE", "RELIANCE26OCT3000CE"):
        bb = model_book(symbol_ce=sym)
        bb.anchor(SX_CE, 100.0, at("11:00"), "fill")
        assert bb.load(SX_CE) == {} and bb.origin[SX_CE] == "none", sym
    # a future isn't modelled: it takes Kite's last trade (here, none)
    bb = model_book(symbol_ce="SENSEX26NOVFUT")
    bb.anchor(SX_CE, 80100.0, at("11:00"), "fill")
    assert bb.load(SX_CE) == {} and bb.origin[SX_CE] == "none" and SX_CE not in bb.models


def test_with_nothing_to_model_it_on_a_contract_stands_at_its_last_trade():
    """What Kite's ltp() answers for a contract that hasn't traded today."""
    prev = date(2026, 10, 20)
    t = datetime.combine(prev, time(14, 0))
    anchors = {(SX_CE, "before"): [{"date": t, "open": 3010, "high": 3010, "low": 3010, "close": 3010}]}
    b = model_book(anchors, symbol_ce="RELIANCE26OCT3000CE")
    assert b.load(SX_CE) == {} and b.origin[SX_CE] == "stale"
    assert b.price(SX_CE, at("12:00"), "close", at("12:00")) == (3010, t + timedelta(seconds=59))


def bankex_future_book(fut_today, fut_before, ltp=None, fut_after=None):
    """BANKEX at 12:00 today; its November future traded `fut_today`
    (HH:MM -> close) today and `fut_before` ((date, HH:MM) -> close) before."""
    BANKEX, FUT = 274441, 8801

    def source(token, start, end):
        if token == BANKEX:
            return [{"date": at("12:00"), "open": 61700, "high": 61800, "low": 61650, "close": 61726}] if start.date() == DAY else []
        if token != FUT:
            return []
        if start.date() == DAY:
            return [{"date": at(k), "open": c, "high": c, "low": c, "close": c} for k, c in fut_today.items()]
        if start.date() > DAY:
            return [{"date": datetime.combine(d, datetime.strptime(k, "%H:%M").time()), "open": c, "high": c, "low": c, "close": c}
                    for (d, k), c in (fut_after or {}).items()]
        return [{"date": datetime.combine(d, datetime.strptime(k, "%H:%M").time()), "open": c, "high": c, "low": c, "close": c}
                for (d, k), c in fut_before.items() if start.date() <= d < DAY]

    class Real:
        def instruments(self, exchange=None):
            return [{"instrument_token": FUT, "tradingsymbol": "BANKEX26NOVFUT", "exchange": "BFO", "name": "BANKEX",
                     "instrument_type": "FUT", "strike": 0.0, "expiry": date(2026, 11, 26)}] if exchange == "BFO" else []

    def history(token, start, end, interval):
        """Kite's daily candles: each day's last close."""
        assert interval == "day"
        if token != FUT:
            return []
        days = {}
        for (d, k), c in sorted({**fut_before, **(fut_after or {})}.items()):
            if start <= d <= end:
                days[d] = c
        return [{"date": datetime.combine(d, time(0)), "open": c, "high": c, "low": c, "close": c} for d, c in days.items()]

    b = M.MinuteBook(DAY, source, lookup=M.kite_lookup(Real()), ltp=ltp, history=history)
    clock = M.ReplayClock(DAY)
    clock.set(at("12:00"), M.UP_FIRST, 3)
    return b, M.ReplayKite(Real(), b, clock), FUT


@pytest.mark.parametrize("today, before, want, at_", [
    # no trade today: its last trade yesterday
    ({}, {(date(2026, 10, 20), "15:20"): 61800}, 61800, datetime(2026, 10, 20, 15, 20, 59)),
    # first trade today only at 13:00, after the replay's 12:00: still yesterday's
    ({"13:00": 62000}, {(date(2026, 10, 20), "15:20"): 61800}, 61800, datetime(2026, 10, 20, 15, 20, 59)),
    # nothing in the week before: Kite's daily candles for 60 days (one light request), its close that day
    ({}, {(date(2026, 9, 25), "11:05"): 60100}, 60100, datetime(2026, 9, 25, 15, 29, 59)),
])
def test_kite_ltp_answers_a_bfo_future_with_its_last_trade_on_kite(today, before, want, at_):
    """The scan's crash: the strangle asks ltp('BFO:BANKEX26NOVFUT') for
    its ATM, and a future with no trade yet was left out of the answer
    (KeyError in the resolver). Kite's own ltp() gives its last trade."""
    b, k, fut = bankex_future_book(today, before)
    got = k.ltp("BFO:BANKEX26NOVFUT")
    assert got["BFO:BANKEX26NOVFUT"]["last_price"] == want
    assert b.price(fut, at("12:00"), "close", at("12:00")) == (want, at_)
    assert fut not in b.models


def test_once_it_trades_today_the_real_trade_is_the_price():
    b, k, fut = bankex_future_book({"11:30": 62000}, {(date(2026, 10, 20), "15:20"): 61800})
    assert k.ltp("BFO:BANKEX26NOVFUT")["BFO:BANKEX26NOVFUT"]["last_price"] == 62000


def test_an_index_with_no_candle_yet_is_not_given_a_previous_days_price():
    b = book_with({NIFTY: {"12:05": (100, 100, 100, 100)}})
    assert b.price(NIFTY, at("12:00"), "close", at("12:00")) is None


def test_a_window_that_hasnt_happened_yet_is_refused_with_how_to_fix_it():
    from app.repair import engine
    w = engine.Window(DAY, time(12, 16), time(15, 30))
    morning = datetime(2026, 10, 21, 8, 40, tzinfo=M.IST)
    assert "hasn't happened yet" in engine.window_problem(w, morning) and "--date" in engine.window_problem(w, morning)
    assert "--to 13:59" in engine.window_problem(w, datetime(2026, 10, 21, 14, 0, tzinfo=M.IST))
    assert engine.window_problem(w, datetime(2026, 10, 21, 16, 0, tzinfo=M.IST)) is None
    assert engine.window_problem(w, datetime(2026, 10, 22, 9, 0, tzinfo=M.IST)) is None
    sat = engine.Window(date(2026, 10, 24), time(12, 16), time(15, 30))
    assert "weekend" in engine.window_problem(sat, datetime(2026, 10, 26, 9, 0, tzinfo=M.IST))



def test_a_future_with_no_trade_in_60_days_answers_with_kites_own_ltp():
    """Your BANKEX26NOVFUT: nothing in Kite's history for the day or the 60
    days before, so the replay asks Kite's ltp() — which is still what the
    app saw that day, since it hasn't traded after it either."""
    asked = []
    b, k, fut = bankex_future_book({}, {}, ltp=lambda key: asked.append(key) or 62150.0)
    got = k.ltp("BFO:BANKEX26NOVFUT")
    assert got["BFO:BANKEX26NOVFUT"]["last_price"] == 62150.0
    assert asked == ["BFO:BANKEX26NOVFUT"] and b.ltp_used[fut] == "BFO:BANKEX26NOVFUT"


def test_kites_ltp_isnt_used_when_the_contract_traded_after_the_day_or_later_in_it():
    calls = []
    # traded the day after: today's ltp() isn't the day's price -> no price, as before
    b, k, fut = bankex_future_book({}, {}, ltp=lambda key: calls.append(key) or 63000.0,
                                   fut_after={(date(2026, 10, 22), "10:00"): 63000})
    assert k.ltp("BFO:BANKEX26NOVFUT") == {} and calls == []
    # its first trade is later in the day: today's ltp() would be that trade
    b, k, fut = bankex_future_book({"13:00": 62000}, {}, ltp=lambda key: calls.append(key) or 62000.0)
    assert k.ltp("BFO:BANKEX26NOVFUT") == {} and calls == []
    # Kite's ltp() says 0 (never traded at all): no price rather than a zero
    b, k, fut = bankex_future_book({}, {}, ltp=lambda key: 0)
    assert k.ltp("BFO:BANKEX26NOVFUT") == {}


def test_kite_ltp_helper_reads_the_last_price():
    class K:
        def ltp(self, keys):
            return {"BFO:BANKEX26NOVFUT": {"instrument_token": 1, "last_price": 62150.5}}
    gate = M.KiteGate(K(), min_gap=0)
    assert M.kite_ltp(gate)("BFO:BANKEX26NOVFUT") == 62150.5
    assert M.kite_ltp(gate)("BFO:SENSEX26NOVFUT") is None


def test_the_resolvers_own_get_ltp_no_longer_raises_for_it_inside_the_replay():
    """The exact call in your traceback: resolver.get_ltp('BFO:BANKEX26NOVFUT')."""
    import asyncio
    from app.options.resolver import OptionsResolver
    b, k, fut = bankex_future_book({}, {}, ltp=lambda key: 62150.0)

    class Disp:
        access_token, api_key = "replay", "replay"
    with M.replaying(k, k.clock):
        px = asyncio.run(OptionsResolver(Disp()).get_ltp("BFO:BANKEX26NOVFUT"))
    assert px == 62150.0



# ── Kite's quote first: a contract whose last trade was before the day ──

def quoted_book(quotes, day_rows=None):
    """A book whose source counts its history requests; quote() answers
    from `quotes` {"EXCH:SYM": (last_price, last_trade_time)}."""
    calls, asked = [], []

    def source(token, start, end):
        calls.append(token)
        return (day_rows or {}).get(token, []) if start.date() == DAY else []

    def quote(keys):
        asked.append(list(keys))
        return {k: {"last_price": quotes[k][0], "last_trade_time": quotes[k][1]} for k in keys if k in quotes}

    class Real:
        def instruments(self, exchange=None):
            return [{"instrument_token": 900 + i, "tradingsymbol": k.split(":")[1], "exchange": "BFO"}
                    for i, k in enumerate(sorted(quotes))] if exchange == "BFO" else []

    b = M.MinuteBook(DAY, source, quote=quote)
    clock = M.ReplayClock(DAY)
    clock.set(at("12:00"), M.UP_FIRST, 3)
    return b, M.ReplayKite(Real(), b, clock), calls, asked


def test_a_strike_ladder_is_one_quote_and_no_history_for_contracts_that_didnt_trade_that_day():
    epoch = datetime(1970, 1, 1, 5, 30)    # Kite's "never traded"
    quotes = {f"BFO:BANKEX26NOV6{k}00CE": (100.0 + k, epoch) for k in range(10, 20)}
    quotes["BFO:BANKEX26NOV61300CE"] = (88.0, datetime(2026, 10, 20, 14, 2))   # last traded the day before
    b, k, calls, asked = quoted_book(quotes)
    got = k.ltp(sorted(quotes))
    assert len(asked) == 1 and len(asked[0]) == 10, "one quote for the whole ladder"
    assert calls == [], "no history request for any of them"
    assert got["BFO:BANKEX26NOV61300CE"]["last_price"] == 88.0
    assert got["BFO:BANKEX26NOV61000CE"]["last_price"] == 110.0
    tok = got["BFO:BANKEX26NOV61000CE"]["instrument_token"]
    assert b.origin[tok] == "quote" and tok in b.never_traded
    t13 = got["BFO:BANKEX26NOV61300CE"]["instrument_token"]
    assert t13 not in b.never_traded and b.price(t13, at("12:00"), "close", at("12:00")) == (88.0, datetime(2026, 10, 20, 14, 2))
    k.ltp(sorted(quotes))
    assert len(asked) == 1, "quoted once per run"


def test_a_contract_that_traded_on_the_day_gets_its_real_candles():
    rows = {900: [{"date": at("11:00"), "open": 50, "high": 55, "low": 48, "close": 52}]}
    b, k, calls, asked = quoted_book({"BFO:SENSEX26NOV77700CE": (60.0, datetime(2026, 10, 21, 15, 20))}, rows)
    got = k.ltp("BFO:SENSEX26NOV77700CE")
    assert got["BFO:SENSEX26NOV77700CE"]["last_price"] == 52, "the day's candle, not today's quote"
    assert calls == [900] and b.origin[900] == "kite"


def test_a_quote_of_zero_falls_through_to_history():
    b, k, calls, asked = quoted_book({"BFO:SENSEX26NOV99900CE": (0.0, None)})
    assert k.ltp("BFO:SENSEX26NOV99900CE") == {}
    assert calls, "asked Kite's history instead"
