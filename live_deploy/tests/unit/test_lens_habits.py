"""How you trade, from your tradebook (app/lens_habits.py): each finding
on trades made up so its figures can be checked by hand."""

from datetime import date, datetime, timedelta

import pytest

from app import lens_habits as H
from app import lens_trades as T

TODAY = date(2026, 10, 30)


def t(sym, d, kind, qty, px, tid, seg="EQ", at=None, order=None, ex="NSE"):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return {"symbol": sym, "isin": f"IN{sym}", "trade_date": d, "exchange": ex, "segment": seg, "series": "EQ" if seg == "EQ" else None,
            "trade_type": kind, "auction": False, "quantity": float(qty), "price": float(px), "trade_id": str(tid),
            "order_id": order or str(tid), "executed_at": at or datetime.combine(d, datetime.min.time())}


def run(trades, holdings=None, prices=None, nifty=None):
    a = T.analyse(trades, holdings or {}, TODAY)
    return H.analyse(trades, a, TODAY, prices, nifty), a


def candles(start, closes, spread=0.0):
    """Daily candles from `start`, every calendar day (enough for these tests)."""
    d = date.fromisoformat(start)
    return [{"date": d + timedelta(days=i), "open": c, "high": c * (1 + spread), "low": c * (1 - spread), "close": c, "volume": 1}
            for i, c in enumerate(closes)]


# ── costs and tax ─────────────────────────────────────────────────────

def test_delivery_charges_and_tax_by_year():
    tr = [t("AAA", "2025-05-02", "buy", 100, 1000, 1), t("AAA", "2025-08-01", "sell", 100, 1200, 2)]
    h, _ = run(tr)
    y = next(x for x in h["costs"]["years"] if x["fy"] == "2025-26")
    # STT 0.1% both sides on 2,20,000; stamp 0.015% on the buy; one DP charge
    assert y["stt"] == 220.0 and y["stamp"] == 15.0 and y["dp"] == H.DP_PER_SALE and y["brokerage"] == 0
    ex = 220000 * H.EXCHANGE["NSE"]
    assert y["exchange"] == round(ex, 2) and y["gst"] == round(0.18 * (ex + 220000 * H.SEBI), 2)
    # short term (91 days): 20,000 at 20%
    assert y["tax"]["short_tax"] == 4000.0 and y["tax"]["long_tax"] == 0
    assert y["booked"] == 20000.0 and h["costs"]["kept"] == round(20000 - h["costs"]["charges"] - 4000, 2)


def test_long_term_exemption_and_losses_set_off():
    tr = [t("LT", "2023-04-03", "buy", 100, 1000, 1), t("LT", "2025-06-02", "sell", 100, 3000, 2),     # +2,00,000 long term
          t("ST", "2025-05-02", "buy", 100, 1000, 3), t("ST", "2025-07-01", "sell", 100, 500, 4)]      # −50,000 short term
    h, _ = run(tr)
    x = next(y for y in h["costs"]["years"] if y["fy"] == "2025-26")["tax"]
    # the short-term loss comes off the long-term gain, then 1.25 lakh is free
    assert x["long_net"] == 150000.0 and x["exempt_used"] == 125000.0
    assert x["long_tax"] == round(25000 * 0.125, 2) and x["short_tax"] == 0


def test_old_rates_before_23_july_2024():
    tr = [t("OLD", "2024-01-02", "buy", 10, 100, 1), t("OLD", "2024-05-02", "sell", 10, 200, 2)]
    h, _ = run(tr)
    assert next(y for y in h["costs"]["years"] if y["fy"] == "2024-25")["tax"]["short_tax"] == 150.0   # 15%


def test_same_day_trades_are_charged_and_shown_as_intraday():
    tr = [t("DAY", "2026-03-02", "buy", 100, 500, 1), t("DAY", "2026-03-02", "sell", 100, 510, 2)]
    h, _ = run(tr)
    y = next(x for x in h["costs"]["years"] if x["fy"] == "2025-26")
    assert y["brokerage"] == 30.3          # 0.03% a side: ₹15 and ₹15.30, under the ₹20 cap
    assert y["stt"] == round(51000 * 0.00025, 2) and y["dp"] == 0, "nothing left the demat"
    assert h["intraday"]["count"] == 1 and h["intraday"]["pnl"] == 1000.0
    assert y["tax"] is None, "business income: not estimated"


def test_fund_purchases_carry_only_stamp_duty():
    tr = [t("FUND", "2026-01-05", "buy", 100, 100, 1, seg="MF")]
    h, _ = run(tr)
    y = h["costs"]["years"][0]
    assert y["stamp"] == 0.5 and y["charges"] == 0.5


# ── holding, averaging, round trips ──────────────────────────────────

def test_holding_period_buckets():
    tr = []
    for i in range(5):      # five quick losers, five long winners
        tr += [t(f"Q{i}", "2025-01-02", "buy", 10, 100, f"q{i}a"), t(f"Q{i}", "2025-01-12", "sell", 10, 90, f"q{i}b"),
               t(f"L{i}", "2024-01-02", "buy", 10, 100, f"l{i}a"), t(f"L{i}", "2025-03-02", "sell", 10, 150, f"l{i}b")]
    h, _ = run(tr)
    b = {x["label"]: x for x in h["holding"]["buckets"]}
    assert b["Under a month"]["return_pct"] == -10.0 and b["Under a month"]["sales"] == 5 and b["Under a month"]["pnl"] == -500.0
    assert b["Over a year"]["return_pct"] == 50.0
    assert (h["holding"]["best"], h["holding"]["worst"]) == ("Over a year", "Under a month")
    s = next(f for f in h["summary"] if f["key"] == "holding")
    assert "under a month −10.0%" in s["text"] and "+50.0%" in s["text"]


def test_averaging_down_against_not():
    tr = []
    for i in range(3):      # bought more 20% lower, then sold at a loss
        tr += [t(f"AD{i}", "2025-01-02", "buy", 10, 100, f"a{i}"), t(f"AD{i}", "2025-02-03", "buy", 10, 80, f"b{i}"),
               t(f"AD{i}", "2025-06-02", "sell", 20, 70, f"c{i}")]
    for i in range(3):      # never added, sold up
        tr += [t(f"NO{i}", "2025-01-02", "buy", 10, 100, f"d{i}"), t(f"NO{i}", "2025-06-02", "sell", 10, 110, f"e{i}")]
    h, _ = run(tr)
    av = h["averaging"]
    assert av["enough"] and av["added_summary"]["count"] == 3 and av["never"] == 3
    assert av["added"][0]["adds"][0] == {"day": "2025-02-03", "price": 80.0, "avg": 100.0, "below_pct": 20.0}
    assert av["added_summary"]["result"] == 3 * (20 * 70 - 1800)      # −400 each
    assert av["added_summary"]["return_pct"] == round(-1200 / 5400 * 100, 1) and av["never_summary"]["return_pct"] == 10.0
    f = next(x for x in h["summary"] if x["key"] == "averaging")
    assert f["title"] == "Averaging down hasn’t paid" and "AD0" in f["text"]


def test_round_trips_bought_back_dearer():
    tr = [t("RT", "2025-01-02", "buy", 10, 100, 1), t("RT", "2025-02-03", "sell", 10, 120, 2), t("RT", "2025-02-20", "buy", 10, 130, 3),
          t("RT", "2025-05-02", "sell", 10, 125, 4), t("RT", "2025-05-12", "buy", 10, 140, 5),
          t("RT", "2025-07-01", "sell", 10, 150, 6), t("RT", "2025-07-05", "buy", 5, 160, 7), t("RT", "2025-09-01", "buy", 5, 90, 8)]
    h, _ = run(tr)
    r = h["round_trips"]
    assert [x["effect"] for x in r["trips"]] == [-150.0, -100.0, -50.0], "the buy 62 days later isn't a round trip"
    assert r["effect"] == -300.0 and r["dearer"] == 3
    assert next(x for x in h["summary"] if x["key"] == "round_trips")["rupees"] == 300.0


# ── spread, funds, orders ────────────────────────────────────────────

def test_spread_and_concentration():
    tr = [t("BIG", "2025-01-02", "buy", 10, 800, 1), t("SM", "2025-01-03", "buy", 10, 200, 2), t("BIG", "2025-03-03", "sell", 10, 900, 3)]
    h, _ = run(tr)
    m = {x["month"]: x for x in h["spread"]["months"]}
    assert m["2025-01"]["holdings"] == 2 and m["2025-01"]["largest"] == "BIG" and m["2025-01"]["largest_pct"] == 80.0
    assert m["2025-03"]["holdings"] == 1 and m["2025-03"]["largest"] == "SM"
    fy = h["spread"]["years"][0]
    assert fy["fy"] == "2024-25" and fy["sold"] == 9000.0
    assert next(x for x in h["summary"] if x["key"] == "concentration")["text"].startswith("In 2025-01 BIG was 80%")


def test_a_sip_its_missed_months_and_a_lump_sum():
    tr = [t("FLEXI", f"2026-{m:02d}-05", "buy", 10, 500, f"f{m}", seg="MF") for m in (1, 2, 3, 5, 6, 7, 8, 9)]
    tr.append(t("FLEXI", "2026-06-20", "buy", 100, 500, "lump", seg="MF"))
    h, _ = run(tr)
    f = h["funds"]["funds"][0]
    assert f["sip"] and f["usual"] == 5000.0 and f["missed"] == ["2026-04"], "October is the current month"
    assert f["lump_sums"] == [{"month": "2026-06", "amount": 55000.0}] and not f["stopped"]
    assert "1 month without its usual ₹5,000" in next(x for x in h["summary"] if x["key"] == "sip_gaps")["text"]


def test_order_times_and_split_orders():
    at = lambda d, hh, mm: datetime.combine(date.fromisoformat(d), datetime.min.time()).replace(hour=hh, minute=mm)  # noqa: E731
    tr = [t("X", "2026-01-05", "buy", 5, 100, 1, at=at("2026-01-05", 9, 16), order="o1"),
          t("X", "2026-01-05", "buy", 5, 100, 2, at=at("2026-01-05", 9, 16), order="o1"),
          t("X", "2026-02-05", "sell", 10, 110, 3, at=at("2026-02-05", 15, 10), order="o2")]
    h, _ = run(tr)
    o = h["orders"]
    assert [s["buys"] + s["sells"] for s in o["slots"]] == [2, 0, 0, 1] and o["open_share"] == 67.0
    assert o["orders"] == 2 and o["split_orders"] == 1 and o["most_parts"] == 2


# ── with prices ──────────────────────────────────────────────────────

def test_after_the_sale_winners_kept_rising():
    tr, prices = [], {}
    for i in range(5):
        tr += [t(f"W{i}", "2025-01-01", "buy", 10, 100, f"w{i}a"), t(f"W{i}", "2025-03-01", "sell", 10, 120, f"w{i}b")]
        prices[f"INW{i}"] = candles("2024-12-01", [100] * 90 + [120 + n * 0.5 for n in range(500)])   # 120 on 1 Mar, then rising
    nifty = candles("2024-12-01", [20000] * 600)
    h, _ = run(tr, prices=prices, nifty=nifty)
    a = h["after_sale"]
    s = a["sales"][0]
    assert s["price"] == 120.0 and s["m3"]["move_pct"] == round(91 * 0.5 / 120 * 100, 1) and s["m3"]["missed"] == round(91 * 0.5 * 10, 2)
    assert s["m3"]["nifty_pct"] == 0.0
    assert a["winners"]["kept_rising"] == 5 and a["winners"]["missed_3m"] == 5 * 455.0
    f = next(x for x in h["summary"] if x["key"] == "sold_early")
    assert f["rupees"] == 2275.0 and "5 of 5" in f["text"]


def test_a_sale_too_recent_has_no_later_price():
    tr = [t("NEW", "2026-09-01", "buy", 10, 100, 1), t("NEW", "2026-10-20", "sell", 10, 110, 2)]
    h, _ = run(tr, prices={"INNEW": candles("2025-08-01", [100] * 456)}, nifty=candles("2025-08-01", [1] * 456))
    s = h["after_sale"]["sales"][0]
    assert s["m1"] is None and s["m3"] is None and s["m12"] is None


def test_buying_after_a_run_up():
    tr, prices = [], {}
    for i in range(5):       # bought after +20% in a month, since down 10%
        tr.append(t(f"R{i}", "2025-03-01", "buy", 10, 120, f"r{i}"))
        prices[f"INR{i}"] = candles("2025-01-01", [100] * 39 + [100 + (n + 1) for n in range(20)] + [120] + [108] * 700)
    for i in range(5):       # bought flat, since up 10%
        tr.append(t(f"F{i}", "2025-03-01", "buy", 10, 100, f"f{i}"))
        prices[f"INF{i}"] = candles("2025-01-01", [100] * 60 + [110] * 700)
    held = {f"IN{s}": {"qty": 10, "ltp": px, "value": 10 * px, "symbol": s} for s, px in
            [*((f"R{i}", 108) for i in range(5)), *((f"F{i}", 110) for i in range(5))]}
    a = T.analyse(tr, held, TODAY)
    h = H.analyse(tr, a, TODAY, prices, candles("2025-01-01", [1] * 700))
    b = h["buy_timing"]
    assert b["run_up"]["buys"] == 5 and b["run_up"]["return_pct"] == -10.0 and b["flat"]["return_pct"] == 10.0
    assert b["run_up"]["result"] == -600.0
    f = next(x for x in h["summary"] if x["key"] == "chasing")
    assert "−10.0%" in f["text"] and "+10.0%" in f["text"] and f["rupees"] == 1200.0


def test_closed_trades_against_nifty():
    tr = []
    for i in range(5):
        tr += [t(f"N{i}", "2025-01-01", "buy", 10, 100, f"n{i}a"), t(f"N{i}", "2025-04-01", "sell", 10, 105, f"n{i}b")]
    nifty = candles("2025-01-01", [100] * 90 + [110] * 10)        # Nifty +10% by 1 Apr
    h, _ = run(tr, prices={}, nifty=nifty)
    v = h["vs_nifty"]
    assert v["trades"] == 5 and v["pnl"] == 250.0 and v["nifty"] == 500.0 and v["vs"] == -250.0 and v["beat"] == 0
    assert next(x for x in h["summary"] if x["key"] == "vs_nifty")["tone"] == "bad"


def test_where_in_the_day_you_buy():
    tr = [t("HI", f"2025-02-{d:02d}", "buy", 1, 109, f"h{d}") for d in range(1, 11)]
    prices = {"INHI": candles("2025-01-01", [100] * 60, spread=0.1)}   # each day 90–110, closing 100
    h, _ = run(tr, prices=prices, nifty=candles("2025-01-01", [1] * 60))
    d = h["in_day"]
    assert d["buys"] == 10 and d["buy_median_pct"] == 95.0 and d["vs_close"] == 90.0
    assert next(x for x in h["summary"] if x["key"] == "in_day")["rupees"] == 90.0


def test_prices_adjusted_since_a_trade_are_read_in_their_own_terms():
    """A 1:10 split after the buy: Kite's series is in post-split terms
    throughout (≈10), the tradebook's sale is at the old price (≈100)."""
    tr = [t("SPL", "2025-01-01", "buy", 10, 100, 1), t("SPL", "2025-02-01", "sell", 10, 101, 2)]
    prices = {"INSPL": candles("2024-12-01", [10.0] * 63 + [11.0] * 400, spread=0.02)}
    h, _ = run(tr, prices=prices, nifty=candles("2024-12-01", [1] * 462))
    s = h["after_sale"]["sales"][0]
    assert s["m3"]["move_pct"] == 10.0 and s["m3"]["missed"] == 101.0, "10.0 → 11.0, on the ₹1,010 sold"
    assert h["in_day"]["buys"] == 0 and h["in_day"]["left_out"] == 2, "a price of 100 isn't in a day of 9.8–10.2"


def test_without_prices_only_the_trade_findings():
    tr = [t("A", "2025-01-02", "buy", 10, 100, 1)]
    h, _ = run(tr)
    assert h["priced"] is False and "after_sale" not in h
    assert all(f["key"] not in ("sold_early", "chasing", "vs_nifty", "in_day") for f in h["summary"])


def test_the_summary_is_biggest_first_and_says_nothing_without_evidence():
    h, _ = run([t("A", "2025-01-02", "buy", 10, 100, 1), t("A", "2025-01-10", "sell", 10, 101, 2)])
    assert [f["key"] for f in h["summary"]] == ["costs"], "one sale: nothing else to say"
    tr = [t("RT", "2025-01-02", "buy", 10, 100, 1)]
    for i in range(3):
        tr += [t("RT", f"2025-0{2 + i * 2}-03", "sell", 10, 100, f"s{i}"), t("RT", f"2025-0{2 + i * 2}-10", "buy", 10, 200, f"b{i}")]
    h, _ = run(tr)
    rs = [f["rupees"] for f in h["summary"]]
    assert rs == sorted(rs, reverse=True) and h["summary"][0]["key"] == "round_trips"


@pytest.mark.parametrize("v,s", [(1234567, "₹12,34,567"), (-950, "−₹950"), (100000, "₹1,00,000")])
def test_rupees_in_indian_grouping(v, s):
    assert H._inr(v) == s
