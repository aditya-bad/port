"""OptionsResolver against the simulated option chain (tests/sim.py):
expiry selectors, ATM (spot / future / put-call parity), legs by offset,
OTM/ITM, by premium (with exclusions and the liquidity tie-break), max
OI, futures, lots, search, and Kite's rate limit."""

import asyncio
import time
from datetime import date

import pytest
from kiteconnect.exceptions import KiteException

import app.options.resolver as R
from app.options import OptionsResolver, options_exchange_for
from tests.sim import FakeDispatcher, Market, install

DAY = date(2026, 10, 14)          # a Wednesday
W1, W2 = date(2026, 10, 20), date(2026, 10, 27)   # weekly Tuesdays; the 27th is October's monthly


@pytest.fixture
def market(monkeypatch):
    m = Market(DAY)
    kite = install(monkeypatch, m)
    return m, kite


def res(market, **kw):
    return OptionsResolver(FakeDispatcher(market[0]), **kw)


def run(coro):
    return asyncio.run(coro)


# ── Expiries ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("sel,want", [
    ("THIS_WEEK", W1), ("NEXT_WEEK", W2), ("this_week", W1), (0, W1), (1, W2),
    ("THIS_MONTH", W2), ("NEXT_MONTH", date(2026, 11, 24)), (W2, W2), ("2026-10-27", W2),
])
def test_expiry_selectors(market, sel, want):
    assert run(res(market).resolve_expiry("NIFTY", sel)) == want


@pytest.mark.parametrize("sel,err", [
    ("SOMEDAY", ValueError), (99, ValueError), (-1, ValueError), (date(2026, 10, 21), ValueError),
    ("2026-10-21", ValueError), (True, TypeError), (1.5, TypeError),
])
def test_bad_expiry_selectors(market, sel, err):
    with pytest.raises(err):
        run(res(market).resolve_expiry("NIFTY", sel))


def test_unknown_underlying_and_bse_exchange(market):
    with pytest.raises(ValueError, match="No option expiries"):
        run(res(market).resolve_expiry("SENSEX", "THIS_WEEK"))           # SENSEX trades on BFO
    assert options_exchange_for("sensex") == "BFO" and options_exchange_for("NIFTY") == "NFO"
    assert options_exchange_for("RELIANCE") == "NFO"
    assert run(res(market, exchange="BFO").resolve_expiry("SENSEX", "THIS_WEEK")) == W1


def test_instrument_master_is_fetched_once_per_exchange_per_day(market):
    _, kite = market
    r = res(market)
    run(r.list_expiries("NIFTY"))
    run(r.list_expiries("BANKNIFTY"))
    assert kite.calls.count("instruments NFO") == 1


# ── ATM ───────────────────────────────────────────────────────────────

def test_atm_from_spot_parity_and_future(market):
    assert run(res(market, atm_reference_mode="spot").get_atm_strike("NIFTY", W1)) == 25000
    assert run(res(market).get_atm_strike("NIFTY", W1)) == 25000, "put-call parity on a weekly"
    # month-end expiry has a listed future: carry pushes ATM up a strike
    assert run(res(market).get_atm_strike("NIFTY", W2)) == 25050
    assert run(res(market).get_atm_strike("NIFTY", W1, spot_price=25130)) == 25150, "a given spot wins"


def test_bad_reference_mode():
    with pytest.raises(ValueError):
        OptionsResolver(None, atm_reference_mode="mid")


def test_strike_step_and_chain(market):
    r = res(market)
    assert run(r.get_strike_step("NIFTY", W1)) == 50
    assert run(r.get_strike_step("BANKNIFTY", W1)) == 100
    chain = run(r.list_option_chain("NIFTY", "THIS_WEEK"))
    assert len(chain) == 121 and all(v["CE"] and v["PE"] for v in chain.values())
    assert list(chain) == sorted(chain)


# ── Legs ──────────────────────────────────────────────────────────────

def test_offset_otm_itm(market):
    r = res(market, atm_reference_mode="spot")
    assert run(r.get_leg_by_offset("NIFTY", "THIS_WEEK", "CE", -2)).strike == 24900
    assert run(r.get_otm_leg("NIFTY", "THIS_WEEK", "CE", 3)).strike == 25150
    assert run(r.get_otm_leg("NIFTY", "THIS_WEEK", "PE", 3)).strike == 24850
    assert run(r.get_itm_leg("NIFTY", "THIS_WEEK", "CE", 1)).strike == 24950
    assert run(r.get_itm_leg("NIFTY", "THIS_WEEK", "pe", 1)).strike == 25050
    assert run(r.get_atm_leg("NIFTY", "THIS_WEEK", "CE")).strike == 25000
    with pytest.raises(ValueError):
        run(r.get_otm_leg("NIFTY", "THIS_WEEK", "CE", -1))
    with pytest.raises(ValueError, match="No CE leg"):
        run(r.get_leg("NIFTY", "THIS_WEEK", 25025, "CE"))
    leg = run(r.get_leg("NIFTY", "THIS_WEEK", 25000, "CE"))
    assert leg.key == f"NFO:{leg.tradingsymbol}" and leg.lot_size == 75 and leg.expiry == W1


def test_leg_by_premium_picks_the_closest_and_respects_exclusions(market):
    m, _ = market
    r = res(market, atm_reference_mode="spot")
    leg = run(r.get_leg_by_premium("NIFTY", "THIS_WEEK", "CE", 40))
    chain = run(r.list_option_chain("NIFTY", "THIS_WEEK"))
    prices = {k: m.price_of(m.by_token[v["CE"].instrument_token]) for k, v in chain.items()}
    best = min(abs(p - 40) for k, p in prices.items() if abs(k - 25000) <= 15 * 50 + 25)
    assert abs(leg.last_price - 40) == pytest.approx(best)
    other = run(r.get_leg_by_premium("NIFTY", "THIS_WEEK", "CE", 40, exclude_strikes=[leg.strike]))
    assert other.strike != leg.strike


def test_leg_by_premium_window_bounds_the_search(market):
    r = res(market, atm_reference_mode="spot")
    deep = run(r.get_leg_by_premium("NIFTY", "THIS_WEEK", "PE", 0.05, strike_window=2))
    assert abs(deep.strike - 25000) <= 100


def test_liquidity_tie_break_prefers_higher_oi_within_tolerance(market, monkeypatch):
    m, kite = market
    r = res(market, atm_reference_mode="spot")
    plain = run(r.get_leg_by_premium("NIFTY", "THIS_WEEK", "CE", 60))
    neighbour = plain.strike + 50
    orig = kite.quote

    def quote(keys):
        out = orig(keys)
        for k, q in out.items():
            q["oi"] = 9_000_000 if m.by_key[k]["strike"] == neighbour else 1000
        return out
    monkeypatch.setattr(kite, "quote", quote)
    tight = run(r.get_leg_by_premium("NIFTY", "THIS_WEEK", "CE", 60, liquidity_tolerance_pct=0.001))
    assert tight.strike == plain.strike, "nothing else within a 0.1% band"
    loose = run(r.get_leg_by_premium("NIFTY", "THIS_WEEK", "CE", 60, liquidity_tolerance_pct=0.6))
    assert loose.strike == neighbour, "the liquid strike wins inside the band"
    # the log-only guard never changes the pick
    assert run(r.get_leg_by_premium("NIFTY", "THIS_WEEK", "CE", 60, check_liquidity=True,
                                    min_oi=10**9)).strike == plain.strike


def test_liquidity_rules():
    ok = {"oi": 1000, "depth": {"buy": [{"price": 99}], "sell": [{"price": 101}]}}
    assert OptionsResolver._leg_liquidity_ok(ok, 500, 0.15) == (True, "")
    assert not OptionsResolver._leg_liquidity_ok({**ok, "oi": 10}, 500, 0.15)[0]
    assert "spread" in OptionsResolver._leg_liquidity_ok(
        {"oi": 1000, "depth": {"buy": [{"price": 50}], "sell": [{"price": 100}]}}, 500, 0.15)[1]
    assert "no bid/ask" in OptionsResolver._leg_liquidity_ok({"oi": 1000}, 500, 0.15)[1]


def test_max_oi(market, monkeypatch):
    m, kite = market
    orig = kite.quote

    def quote(keys):
        out = orig(keys)
        for k, q in out.items():
            row = m.by_key[k]
            q["oi"] = 7_777_777 if (row["strike"], row["instrument_type"]) == (24500.0, "PE") else 100
        return out
    monkeypatch.setattr(kite, "quote", quote)
    leg, oi = run(res(market).get_max_oi_strike("NIFTY", "THIS_WEEK"))
    assert (leg.strike, leg.option_type, oi) == (24500, "PE", 7_777_777)
    leg, _ = run(res(market).get_max_oi_strike("NIFTY", "THIS_WEEK", "CE", strike_window=3))
    assert abs(leg.strike - 25000) <= 150


# ── Futures, spot, lots, search ───────────────────────────────────────

def test_futures_spot_and_lots(market):
    m, kite = market
    r = res(market)
    fut = run(r.get_futures_leg("NIFTY"))
    assert fut.option_type == "FUT" and fut.expiry == W2
    assert run(r.get_futures_price("NIFTY")) > 25000
    assert run(r.get_spot_price("NIFTY")) == 25000
    d = FakeDispatcher(m)
    d.last_prices[256265] = 25111.0
    calls = len(kite.calls)
    assert run(OptionsResolver(d).get_spot_price("NIFTY")) == 25111.0, "live tick, no REST call"
    assert len(kite.calls) == calls
    assert run(r.get_lot_size("NIFTY")) == 75
    assert run(r.round_to_lot("NIFTY", 100)) == 75
    assert run(r.round_to_lot("NIFTY", 10)) == 75, "never below one lot"
    assert run(r.round_to_lot("NIFTY", 190)) == 225
    with pytest.raises(ValueError):
        run(r.get_lot_size("NOPE"))


def test_ltp_many_is_one_call(market):
    _, kite = market
    r = res(market)
    a = run(r.get_leg("NIFTY", "THIS_WEEK", 25000, "CE"))
    b = run(r.get_leg("NIFTY", "THIS_WEEK", 25000, "PE"))
    before = len(kite.calls)
    prices = run(r.get_ltp_many([a, b]))
    assert set(prices) == {a.key, b.key} and len(kite.calls) == before + 1
    assert run(r.get_ltp_many([])) == {}


def test_search(market):
    r = res(market)
    out = run(r.search_instruments("NIFTY26OCT2025000", exchanges=["NFO"], limit=5))
    assert out and len(out) <= 5 and all("25000" in x["tradingsymbol"] for x in out)
    assert run(r.search_instruments("   ")) == []


# ── Kite rate limit ───────────────────────────────────────────────────

def test_429_is_retried_other_errors_are_not(monkeypatch):
    r = OptionsResolver(None)
    monkeypatch.setattr(R, "_KITE_CALL_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(r, "_KITE_RETRY_BASE_DELAY", 0.001)
    n = {"c": 0}

    def flaky():
        n["c"] += 1
        if n["c"] < 3:
            raise KiteException("Too many requests", code=429)
        return "ok"
    assert run(r._kite_call(flaky)) == "ok" and n["c"] == 3

    def bad_token():
        n["c"] += 1
        raise KiteException("Invalid token", code=403)
    n["c"] = 0
    with pytest.raises(KiteException):
        run(r._kite_call(bad_token))
    assert n["c"] == 1

    def always_429():
        raise KiteException("Too many requests", code=429)
    with pytest.raises(KiteException):
        run(r._kite_call(always_429))


def test_calls_are_spaced_process_wide(monkeypatch):
    monkeypatch.setattr(R, "_KITE_CALL_MIN_INTERVAL", 0.05)
    monkeypatch.setattr(R, "_kite_call_next_allowed_at", 0.0)
    monkeypatch.setattr(R, "_kite_call_lock", asyncio.Lock())

    async def go():
        a, b = OptionsResolver(None), OptionsResolver(None)   # two deployments, one limit
        t0 = time.monotonic()
        await asyncio.gather(*(x._kite_call(lambda: 1) for x in (a, b, a, b)))
        return time.monotonic() - t0
    assert run(go()) >= 0.14


def test_live_price_is_a_recent_trade_else_the_bid_ask_else_nothing():
    """What a trade could be done at now (strangle_monthly_v2's LIVE
    PRICES): a last trade only if it's recent; a thin contract's last
    trade can be days old (SENSEX26NOV70800PE, 30 Oct: 21 Oct 09:34)."""
    from datetime import datetime
    from app.options.resolver import live_price
    now = datetime(2026, 10, 30, 10, 0)
    book = {"buy": [{"price": 280.0}], "sell": [{"price": 286.0}]}
    assert live_price({"last_price": 125.9, "last_trade_time": datetime(2026, 10, 30, 9, 58), "depth": book}, now) == (125.9, "trade")
    assert live_price({"last_price": 125.9, "last_trade_time": datetime(2026, 10, 21, 9, 34), "depth": book}, now) == (283.0, "mid")
    assert live_price({"last_price": 125.9, "last_trade_time": datetime(2026, 10, 30, 9, 40), "depth": book}, now) == (283.0, "mid")
    assert live_price({"last_price": 125.9, "last_trade_time": datetime(2026, 10, 30, 9, 40),
                       "depth": {"buy": [], "sell": [{"price": 286.0}]}}, now) == (125.9, "old_trade")
    assert live_price({"last_price": 125.9, "last_trade_time": datetime(2026, 10, 21, 9, 34),
                       "depth": {"buy": [], "sell": []}}, now) == (None, "none")
    assert live_price({"last_price": 125.9, "last_trade_time": datetime(2026, 10, 30, 9, 10), "depth": {}}, now) == (None, "none"), \
        "a trade before the open is yesterday's"
