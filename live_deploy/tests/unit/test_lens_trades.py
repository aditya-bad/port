"""Your trades from Console tradebook files (app/lens_trades.py): reading
the files, lots first in first out, short and long term by financial
year, money-weighted returns, months in and out, and whether the trades
add up to what Kite holds."""

from datetime import date, datetime, timedelta

import pytest

from app import lens_trades as T

HEAD = "symbol\tisin\ttrade_date\texchange\tsegment\tseries\ttrade_type\tauction\tquantity\tprice\ttrade_id\torder_id\torder_execution_time\t\n"


def t(sym, d, kind, qty, px, tid, isin=None, seg="EQ", ex="NSE"):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return {"symbol": sym, "isin": isin or f"IN{sym}", "trade_date": d, "exchange": ex, "segment": seg, "series": "EQ" if seg == "EQ" else None,
            "trade_type": kind, "auction": False, "quantity": qty, "price": px, "trade_id": tid, "order_id": tid,
            "executed_at": datetime.combine(d, datetime.min.time())}


def test_a_console_file_as_given():
    text = (HEAD
            + "NIFTYBEES\tINF204KB14I2\t2026-04-02\tNSE\tEQ\tEQ\tbuy\tFALSE\t4\t252.850006\t400299040\t1200000002303827\t2026-04-02 9:18:00\t\n"
            + "BANDHAN SMALL CAP FUND DIRECT PLAN-GROWTH\tINF194KB1AL4\t2026-04-01\tBSE\tMF\t\tbuy\tFALSE\t56.215\t46.249\t2597645039\t2597645039\t2026-04-01 0:00:00\t\n")
    rows, bad = T.parse(text, "tradebook.csv")
    assert bad == [] and len(rows) == 2
    a, m = rows
    assert (a["symbol"], a["isin"], a["trade_date"], a["quantity"], a["price"], a["trade_type"]) == \
        ("NIFTYBEES", "INF204KB14I2", date(2026, 4, 2), 4.0, 252.850006, "buy")
    assert a["executed_at"] == datetime(2026, 4, 2, 9, 18) and a["auction"] is False and a["source"] == "tradebook.csv"
    assert (T.kind_of(a), T.kind_of(m)) == ("equity", "mf") and m["quantity"] == 56.215


def test_commas_bom_and_bad_rows_are_named():
    text = "﻿" + HEAD.replace("\t", ",") + "INFY,INE009A01021,2026-01-05,NSE,EQ,EQ,buy,false,10,1500,1,1,2026-01-05 10:00:00\n" \
        + "INFY,INE009A01021,05-13-2026,NSE,EQ,EQ,buy,false,10,1500,2,2,\n" \
        + "INFY,INE009A01021,2026-01-06,NSE,EQ,EQ,hold,false,10,1500,3,3,\n" \
        + "INFY,INE009A01021,2026-01-06,NSE,EQ,EQ,buy,false,ten,1500,4,4,\n" \
        + ",,,,,,,,,,,,\n"
    rows, bad = T.parse(text)
    assert len(rows) == 1 and rows[0]["executed_at"] == datetime(2026, 1, 5, 10, 0)
    assert bad == ["line 3: bad trade_date", "line 4: trade_type 'hold'", "line 5: quantity or price isn't a number"]
    assert T.parse("hello\nworld")[1][-1].startswith("no tradebook rows found")


def test_lots_first_in_first_out_and_the_term_of_each_sale():
    trades = [t("A", "2024-01-10", "buy", 10, 100, "1"), t("A", "2025-03-01", "buy", 10, 150, "2"),
              t("A", "2025-02-01", "sell", 5, 120, "3"),          # held 388 days: long
              t("A", "2025-06-01", "sell", 10, 200, "4")]
    x = T.lots(trades)["INA"]
    assert [(c["qty"], c["buy"], c["sell"], c["term"]) for c in x["closed"]] == [
        (5, 100, 120, "long"), (5, 100, 200, "long"), (5, 150, 200, "short")]
    assert x["open"] == [{"day": date(2025, 3, 1), "qty": 5, "price": 150}]
    assert sum(c["pnl"] for c in x["closed"]) == 5 * 20 + 5 * 100 + 5 * 50
    assert T.term(365, "equity") == "short" and T.term(366, "equity") == "long", "more than 12 months"


def test_a_sale_the_trades_never_bought_is_counted_not_guessed():
    x = T.lots([t("B", "2025-01-01", "buy", 10, 50, "1"), t("B", "2025-06-01", "sell", 15, 60, "2")])["INB"]
    assert x["oversold"] == 5 and [c["qty"] for c in x["closed"]] == [10] and x["open"] == []


def test_financial_years():
    assert T.fy(date(2026, 3, 31)) == "2025-26" and T.fy(date(2026, 4, 1)) == "2026-27"


def test_xirr():
    assert T.xirr([(date(2025, 1, 1), -100), (date(2026, 1, 1), 110)]) == pytest.approx(10, abs=0.01)
    r = T.xirr([(date(2025, 1, 1), -100), (date(2025, 7, 1), -100), (date(2026, 1, 1), 220)])
    assert 12 < r < 14, "money in later counts for less of the year"
    assert T.xirr([(date(2025, 1, 1), -100), (date(2025, 1, 3), 120)]) is None, "under a week: no yearly rate"
    assert T.xirr([(date(2025, 1, 1), -100)]) is None


def test_the_whole_picture_against_what_kite_holds():
    today = date(2026, 10, 28)
    trades = [t("A", "2025-01-10", "buy", 10, 100, "1"), t("A", "2025-08-01", "sell", 4, 130, "2"),
              t("B", "2026-02-01", "buy", 20, 50, "3"), t("B", "2026-09-01", "sell", 20, 40, "4"),
              t("C", "2025-11-15", "buy", 5, 200, "5"),                                      # turns long term 16 Nov 2026
              t("F", "2025-04-10", "buy", 100.5, 20, "6", seg="MF", ex="BSE"),
              t("OPT", "2026-03-01", "buy", 75, 10, "7", seg="FO", ex="NFO")]
    holdings = {"INA": {"qty": 10, "ltp": 140, "symbol": "A"}, "INC": {"qty": 5, "ltp": 260, "symbol": "C"},
                "INF": {"qty": 100.5, "ltp": 25, "symbol": "F", "kind": "mf"}, "INZ": {"qty": 3, "ltp": 10, "symbol": "Z"}}
    a = T.analyse(trades, holdings, today)
    assert a["count"] == 7 and a["other"] == {"count": 1, "segments": ["FO"]}, "F&O kept, not analysed"
    eq, mf = a["totals"]["equity"], a["totals"]["mf"]
    assert eq["realized"] == 4 * 30 - 20 * 10 and eq["open"] == 2
    assert eq["value"] == 6 * 140 + 5 * 260 and eq["unrealized"] == 6 * 40 + 5 * 60
    assert mf["value"] == pytest.approx(100.5 * 25) and mf["xirr"] > 0
    assert [(y["fy"], y["short"], y["long"]) for y in a["by_year"]] == [("2025-26", 120.0, 0.0), ("2026-27", -200.0, 0.0)]
    soon = a["long_term_soon"]
    assert [(s["symbol"], s["long_term_on"], s["in_days"]) for s in soon] == [("C", "2026-11-16", 19)]
    rec = {r["symbol"]: r for r in a["reconcile"]}
    assert rec["A"]["trades_qty"] == 6 and rec["A"]["held_qty"] == 10 and "more than the trades bought" in rec["A"]["why"]
    assert rec["Z"]["why"] == "held, with no trades in the files imported" and "C" not in rec and "F" not in rec
    b = a["behaviour"]
    assert (b["sales"], b["wins"], b["losses"], b["win_rate"]) == (2, 1, 1, 50.0)
    assert b["avg_days_winners"] == 203 and b["avg_days_losers"] == 212
    months = {m["month"]: m for m in a["months"]}
    assert months["2025-01"]["bought"] == 1000 and months["2026-09"]["sold"] == 800 and a["months"][-1]["net_in"] == \
        1000 + 1000 + 1000 + 2010 - 520 - 800
    c = next(h for h in a["holdings"] if h["symbol"] == "C")
    assert c["lots"] == [{"day": "2025-11-15", "qty": 5, "price": 200, "days": 347, "term": "short",
                          "long_term_on": "2026-11-16", "gain": 300.0}]


def test_your_shares_against_the_same_money_in_nifty():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(200) if (date(2026, 1, 1) + timedelta(days=i)).weekday() < 5]
    nifty = [(d, 20000 * (1 + 0.001 * i)) for i, d in enumerate(days)]
    stock = [(d, 100 * (1 + 0.002 * i)) for i, d in enumerate(days)]
    trades = [t("A", days[0], "buy", 10, 100, "1", isin="X"), t("A", days[50], "sell", 5, stock[50][1], "2", isin="X"),
              t("B", days[10], "buy", 1, 50, "3", isin="Y"), t("F", days[5], "buy", 10, 10, "4", seg="MF")]
    c = T.value_curve(trades, {"X": stock}, nifty)
    assert c["left_out"] == ["B"], "no prices: left out, and named"
    assert (c["value"][0], c["invested"][0], c["nifty"][0]) == (1000.0, 1000.0, 1000.0), "the same money, the same day"
    assert c["now"]["value"] == pytest.approx(5 * stock[-1][1], abs=0.01)
    units = 1000 / nifty[0][1] - 5 * stock[50][1] / nifty[50][1]
    assert c["now"]["nifty"] == pytest.approx(units * nifty[-1][1], abs=0.01)
    assert c["now"]["vs_nifty"] == pytest.approx(c["now"]["value"] - c["now"]["nifty"], abs=0.01) and c["now"]["vs_nifty"] > 0
    assert T.value_curve([t("F", days[5], "buy", 10, 10, "4", seg="MF")], {}, nifty) is None, "funds: no line"


def test_debt_funds_have_no_long_term_and_fund_units_match_to_three_decimals():
    today = date(2026, 10, 28)
    trades = [t("LIQ", "2025-01-02", "buy", 10.001, 4000, "1", seg="MF", ex="BSE"),
              t("LIQ", "2026-02-02", "sell", 5, 4300, "2", seg="MF", ex="BSE"),           # 396 days: still not long term
              t("LIQ", "2025-11-20", "buy", 10.0, 4200, "3", seg="MF", ex="BSE"),
              t("EQF", "2025-11-20", "buy", 3.333, 300, "4", seg="MF", ex="BSE"),
              t("EQF", "2025-11-21", "buy", 3.333, 300, "5", seg="MF", ex="BSE"),
              t("EQF", "2025-11-22", "buy", 3.334, 300, "6", seg="MF", ex="BSE")]
    held = {"INLIQ": {"qty": 15.001, "ltp": 4400, "kind": "mf", "asset": "mf_liquid"},
            "INEQF": {"qty": 10.0, "ltp": 330, "kind": "mf", "asset": "mf_equity"}}
    a = T.analyse(trades, held, today)
    assert [(y["fy"], y["slab"], y["short"], y["long"]) for y in a["by_year"]] == [("2025-26", 1500.0, 0.0, 0.0)]
    liq = next(h for h in a["holdings"] if h["symbol"] == "LIQ")
    assert {l["term"] for l in liq["lots"]} == {"slab"} and all(l["long_term_on"] is None for l in liq["lots"])
    assert [s["symbol"] for s in a["long_term_soon"]] == ["EQF"] * 3, "an equity fund turns long term; a liquid fund never does"
    assert a["reconcile"] == [], "units within a hundredth: the same holding"
    assert T.term(400, "mf", "mf_debt", date(2023, 3, 1)) == "long", "bought before April 2023: the old rule"


def test_under_a_year_the_return_over_the_time_held_not_a_yearly_rate():
    today = date(2026, 10, 28)
    a = T.analyse([t("NEW", "2026-08-29", "buy", 10, 100, "1"), t("OLD", "2024-06-03", "buy", 10, 100, "2"),
                   t("GONE", "2026-01-05", "buy", 10, 100, "3"), t("GONE", "2026-03-05", "sell", 10, 130, "4")],
                  {"INNEW": {"qty": 10, "ltp": 130}, "INOLD": {"qty": 10, "ltp": 130}}, today)
    by = {h["symbol"]: h for h in a["holdings"]}
    assert by["NEW"]["xirr"] is None and by["NEW"]["return_pct"] == 30.0 and by["NEW"]["days"] == 60, \
        "+30% in two months: said as that, not as ~380% a year"
    assert by["OLD"]["xirr"] == pytest.approx((1.3 ** (365 / 877) - 1) * 100, abs=0.05) and by["OLD"]["return_pct"] == 30.0
    assert by["GONE"]["xirr"] is None and by["GONE"]["return_pct"] == 30.0 and by["GONE"]["days"] == 59, "sold out: first buy to last sale"
    assert a["totals"]["all"]["xirr"] is not None, "the whole still has its yearly rate"
