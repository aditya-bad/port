"""LinckLens (app/lens.py): Kite's holdings, as the Lens page shows them."""

import asyncio
from datetime import date

import pytest

from app import lens

EQ = [  # kite.holdings(), trimmed to what's read
    {"tradingsymbol": "INFY", "exchange": "NSE", "isin": "INE009A01021", "quantity": 10, "t1_quantity": 2,
     "collateral_quantity": 0, "average_price": 1400.0, "last_price": 1500.0, "close_price": 1480.0},
    {"tradingsymbol": "ITC", "exchange": "NSE", "isin": "INE154A01025", "quantity": 100, "t1_quantity": 0,
     "collateral_quantity": 50, "average_price": 450.0, "last_price": 420.0, "close_price": 0},
]
MF = [  # kite.mf_holdings()
    {"fund": "PARAG PARIKH FLEXI CAP FUND - DIRECT PLAN", "folio": "123/45", "tradingsymbol": "INF879O01027",
     "quantity": 250.5, "average_price": 60.0, "last_price": 80.0, "last_price_date": date(2026, 10, 26)},
]


def test_each_holding_and_the_totals():
    out = lens.build(EQ, MF)
    infy, itc = out["equity"][1], out["equity"][0]           # largest first: ITC ₹42,000, INFY ₹18,000
    assert [r["symbol"] for r in out["equity"]] == ["ITC", "INFY"]
    assert infy["qty"] == 12 and infy["t1_qty"] == 2, "settled plus bought and not yet delivered"
    assert (infy["invested"], infy["value"], infy["pnl"]) == (16800.0, 18000.0, 1200.0)
    assert infy["pnl_pct"] == pytest.approx(7.14, abs=0.01)
    assert infy["day_change"] == 240.0 and infy["day_change_pct"] == pytest.approx(1.35, abs=0.01)
    assert itc["pnl"] == -3000.0 and itc["pledged_qty"] == 50
    assert itc["day_change"] is None and itc["prev_close"] is None, "no previous close: no day change, not a fake one"
    [f] = out["mf"]
    assert (f["units"], f["invested"], f["value"], f["pnl"]) == (250.5, 15030.0, 20040.0, 5010.0)
    assert f["nav_date"] == "2026-10-26" and f["folio"] == "123/45"
    t = out["totals"]
    assert t["equity"] == {"count": 2, "invested": 61800.0, "value": 60000.0, "pnl": -1800.0, "pnl_pct": -2.91,
                           "day_change": 240.0, "day_change_pct": round(240 / (60000 - 240) * 100, 2)}
    assert t["mf"]["pnl"] == 5010.0 and "day_change" not in t["mf"], "a fund's NAV is a day's, not today's move"
    assert (t["all"]["invested"], t["all"]["value"], t["all"]["pnl"]) == (76830.0, 80040.0, 3210.0)


def test_nothing_held():
    out = lens.build([], [])
    assert out["equity"] == [] and out["mf"] == []
    assert out["totals"]["all"] == {"count": 0, "invested": 0.0, "value": 0.0, "pnl": 0.0, "pnl_pct": None}
    assert out["totals"]["equity"]["day_change"] is None


def test_read_once_a_minute_unless_asked_fresh():
    asked = []

    async def call(what):
        asked.append(what)
        return EQ if what == "holdings" else MF

    async def go():
        L = lens.Lens(None, call=call)
        a = await L.holdings()
        b = await L.holdings()
        c = await L.holdings(fresh=True)
        return a, b, c
    a, b, c = asyncio.run(go())
    assert a is b and asked == ["holdings", "mf_holdings", "holdings", "mf_holdings"]
    assert c["totals"] == a["totals"] and c["as_of"]
