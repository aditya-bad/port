"""LinckLens Inspector (app/lens_inspect.py): your own money against what
"invested" shows, the yearly tax-free long-term gain, and your figures
with and without some holdings."""

from datetime import date, datetime, timedelta

import pytest

from app import lens_inspect as I
from tests.unit.test_lens_trades import t

TODAY = date(2026, 10, 28)


def test_reused_profit_is_not_your_own_money():
    """₹1 lakh in, sold for ₹1.2 lakh, all of it into another share: what
    you hold cost ₹1.2 lakh, but only ₹1 lakh of it was ever your money."""
    trades = [t("A", "2025-01-06", "buy", 100, 1000, "1"),
              t("A", "2025-08-01", "sell", 100, 1200, "2"),
              t("B", "2025-08-01", "buy", 600, 200, "3")]
    o = I.own_money(trades, {"INB": {"ltp": 250}}, TODAY)
    assert (o["own"], o["reused"], o["cost_now"], o["profit_in_holdings"], o["booked"]) == (100000, 120000, 120000, 20000, 20000)
    assert o["value"] == 150000 and o["gain"] == 50000 and o["gain_pct"] == 50.0 and o["pot"] == 0
    assert o["times_turned"] == 2.2, "₹2.2 lakh bought on ₹1 lakh of your own"
    assert o["xirr"] == pytest.approx(((150000 / 100000) ** (365 / (TODAY - date(2025, 1, 6)).days) - 1) * 100, abs=0.05)


def test_a_sale_pays_for_a_later_buy_first_and_new_money_only_tops_it_up():
    trades = [t("A", "2025-01-06", "buy", 10, 1000, "1"),          # 10,000 of yours
              t("A", "2025-03-03", "sell", 10, 900, "2"),          # 9,000 back in the pot (a loss of 1,000)
              t("B", "2025-03-10", "buy", 20, 600, "3"),           # 12,000: 9,000 from the pot, 3,000 new
              t("B", "2025-06-02", "sell", 5, 800, "4")]           # 4,000 left in the pot
    o = I.own_money(trades, {"INB": {"ltp": 700}}, TODAY)
    assert (o["own"], o["reused"], o["pot"]) == (13000, 9000, 4000)
    assert o["booked"] == -1000 + 5 * 200 and o["cost_now"] == 15 * 600
    assert o["profit_in_holdings"] == o["cost_now"] - (o["own"] - o["pot"]) == o["booked"], "held at cost = own money in it + booked profit"
    assert o["value"] == 15 * 700 and o["gain"] == 15 * 700 + 4000 - 13000
    assert [(y["fy"], y["fresh"], y["reused"], y["booked"]) for y in o["years"]] == [("2024-25", 13000.0, 9000.0, -1000.0), ("2025-26", 0.0, 0.0, 1000.0)]
    assert [m["month"] for m in o["months"]] == ["2025-01", "2025-03", "2025-06"] and o["months"][-1]["pot"] == 4000


def test_selling_and_buying_back_for_the_tax_free_gain_raises_invested_not_your_own_money():
    trades = [t("F", "2024-04-02", "buy", 1000, 100, "1", seg="MF", ex="BSE"),
              t("F", "2025-06-02", "sell", 1000, 220, "2", seg="MF", ex="BSE"),
              t("F", "2025-06-03", "buy", 1000, 221, "3", seg="MF", ex="BSE")]
    o = I.own_money(trades, {"INF": {"ltp": 250}}, TODAY)
    assert o["own"] == 100000 + 1000, "the rebuy cost ₹1,000 more than the sale gave: that much is new"
    assert o["cost_now"] == 221000 and o["booked"] == 120000 and o["value"] == 250000
    assert o["harvests"] == [{"symbol": "F", "sold_on": "2025-06-02", "bought_on": "2025-06-03", "qty": 1000, "bought_qty": 1000,
                              "booked": 120000.0, "kind": "gain"}]


def test_a_sale_the_trades_never_bought_counts_at_no_gain():
    o = I.own_money([t("X", "2025-01-01", "buy", 10, 100, "1"), t("X", "2025-02-01", "sell", 15, 120, "2")], {}, TODAY)
    assert o["unmatched"] == 600 and o["booked"] == 200, "the 5 a bonus gave are counted at no gain, not guessed"


def test_the_tax_free_room_this_year_and_the_lots_that_use_it_oldest_first():
    trades = [t("A", "2024-05-02", "buy", 100, 100, "1"), t("A", "2025-02-03", "buy", 100, 180, "2"),
              t("A", "2025-12-01", "buy", 100, 300, "3"),                                         # not long term yet
              t("B", "2024-06-03", "buy", 10, 1000, "4"),
              t("B", "2026-05-04", "sell", 5, 3000, "5"),                                         # 10,000 long-term this year
              t("L", "2024-06-03", "buy", 10, 1000, "6", seg="MF", ex="BSE"),                     # a liquid fund: not covered
              t("G", "2024-06-03", "buy", 100, 50, "7")]                                          # a gold ETF: not covered
    held = {"INA": {"ltp": 400, "asset": "stock"}, "INB": {"ltp": 3000, "asset": "stock"},
            "INL": {"ltp": 1100, "asset": "mf_liquid"}, "ING": {"ltp": 90, "asset": "gold"}}
    x = I.tax_free(trades, held, TODAY)
    assert (x["fy"], x["exemption"], x["booked"], x["room"]) == ("2026-27", 125000, 10000.0, 115000.0)
    assert x["days_left"] == (date(2027, 3, 31) - TODAY).days
    assert [(a["symbol"], a["qty"], a["gain"]) for a in x["available"]] == [("A", 200, 100 * 300 + 100 * 220), ("B", 5, 5 * 2000)], \
        "long-term lots only, oldest first; the liquid fund and the gold ETF aren't covered"
    assert [p["symbol"] for p in x["plan"]] == ["B", "A"], "most gain for each rupee sold first: B 2000/3000, A 52000/80000"
    assert x["plan"][1] == {"symbol": "A", "isin": "INA", "qty": 200, "sale": 80000, "gain": 52000, "maybe": False}
    assert x["plan"][0]["qty"] == 5 and x["plan_gain"] == 62000.0
    assert I.exemption("2023-24") == 100000 and I.exemption("2024-25") == 125000


def test_a_plan_stops_at_the_room_left():
    trades = [t("A", "2024-05-02", "buy", 1000, 100, "1")]
    x = I.tax_free(trades, {"INA": {"ltp": 300, "asset": "stock"}}, TODAY)
    assert x["plan"] == [{"symbol": "A", "isin": "INA", "qty": 625, "sale": 187500, "gain": 125000, "maybe": False}], \
        "₹200 a share: 625 shares fill the ₹1.25 lakh"


def candles(start, n, f):
    out, d, i = [], start, 0
    while i < n:
        if d.weekday() < 5:
            c = f(i)
            out.append({"date": datetime(d.year, d.month, d.day), "open": c, "high": c, "low": c, "close": c})
            i += 1
        d += timedelta(days=1)
    return out


def test_with_and_without_and_what_each_holding_did():
    start = TODAY - timedelta(days=380)
    nifty = candles(start, 280, lambda i: 20000 * (1 + 0.0005 * i))
    up = candles(start, 280, lambda i: 100 * (1 + 0.002 * i))
    down = candles(start, 280, lambda i: 100 * (1 - 0.001 * i))
    h = {"equity": [{"symbol": "UP", "isin": "INUP", "token": 1, "qty": 10, "ltp": up[-1]["close"], "value": 10 * up[-1]["close"], "pnl": 0, "weight": 50},
                    {"symbol": "DOWN", "isin": "INDOWN", "token": 2, "qty": 10, "ltp": down[-1]["close"], "value": 10 * down[-1]["close"], "pnl": 0, "weight": 50}],
         "mf": []}
    cs = {1: up, 2: down, 256265: nifty}
    trades = [t("UP", start, "buy", 10, 100, "1"), t("DOWN", start, "buy", 10, 100, "2")]
    c = I.compare(trades, h, cs, nifty, TODAY, {"INDOWN"})
    w, wo = c["with"], c["without_them"]
    assert wo["now"]["count"] == 1 and wo["now"]["top"] == "UP" and wo["now"]["effective"] == 1.0
    assert wo["year"]["ret"] > w["year"]["ret"] and wo["trades"]["xirr"] > w["trades"]["xirr"]
    assert w["year"]["nifty"] == wo["year"]["nifty"], "Nifty is Nifty either way"
    rows = {r["symbol"]: r for r in I.contributions(trades, h, cs, nifty, TODAY)}
    assert rows["DOWN"]["xirr_change"] < 0 < rows["UP"]["xirr_change"], "DOWN held you back; UP carried you"
    assert rows["DOWN"]["year_change"] < 0 < rows["UP"]["year_change"]
    assert list(rows) == ["DOWN", "UP"], "what held you back first"
    h["mf"] = [{"fund": "NO TRADES FUND", "isin": "INNT", "units": 1, "nav": 10, "value": 10, "weight": 0.1}]
    nt = next(r for r in I.contributions(trades, h, cs, nifty, TODAY) if r["isin"] == "INNT")
    assert nt["xirr_change"] is None and nt["xirr_without"] is None, "held with no trades: nothing to take out, not +0.0"
