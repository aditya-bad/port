"""LinckLens (app/lens.py): Kite's account data, and what can be said from
it: what each holding is, how it's spread, what needs a look, and a year
of prices (returns, 52-week range, volatility, beta, the shares against
Nifty)."""

import asyncio
import math
from datetime import date, datetime, timedelta

import pytest

from app import lens

TODAY = date(2026, 10, 27)

EQ = [  # kite.holdings(), trimmed to what's read
    {"tradingsymbol": "INFY", "exchange": "NSE", "isin": "INE009A01021", "instrument_token": 408065, "quantity": 10, "t1_quantity": 2,
     "collateral_quantity": 0, "average_price": 1400.0, "last_price": 1500.0, "close_price": 1480.0},
    {"tradingsymbol": "ITC", "exchange": "NSE", "isin": "INE154A01025", "instrument_token": 424961, "quantity": 100, "t1_quantity": 0,
     "collateral_quantity": 50, "average_price": 450.0, "last_price": 420.0, "close_price": 0},
]
MF = [  # kite.mf_holdings()
    {"fund": "PARAG PARIKH FLEXI CAP FUND - DIRECT PLAN", "folio": "123/45", "tradingsymbol": "INF879O01027",
     "quantity": 250.5, "average_price": 60.0, "last_price": 80.0, "last_price_date": date(2026, 10, 26)},
]


# ── The holdings ─────────────────────────────────────────────────────────

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
    assert f["nav_date"] == "2026-10-26" and f["folio"] == "123/45" and f["plan"] == "direct", "Direct, from its name"
    t = out["totals"]
    assert t["equity"]["invested"] == 61800.0 and t["equity"]["value"] == 60000.0 and t["equity"]["pnl"] == -1800.0
    assert t["equity"]["day_change"] == 240.0 and (t["equity"]["advancing"], t["equity"]["declining"]) == (1, 0)
    assert t["mf"]["pnl"] == 5010.0 and "day_change" not in t["mf"], "a fund's NAV is a day's, not today's move"
    assert (t["all"]["invested"], t["all"]["value"], t["all"]["pnl"]) == (76830.0, 80040.0, 3210.0)
    assert sum(r["weight"] for r in out["equity"] + out["mf"]) == pytest.approx(100, abs=0.02)


def test_nothing_held():
    out = lens.build([], [])
    assert out["equity"] == [] and out["mf"] == [] and out["allocation"] == [] and out["attention"] == []
    assert out["totals"]["all"] == {"count": 0, "invested": 0.0, "value": 0.0, "pnl": 0.0, "pnl_pct": None}
    assert out["concentration"] == {"count": 0, "top": None, "top5": None, "effective": None}


@pytest.mark.parametrize("symbol,name,kind", [
    ("SGBMAR29", "SGB 2.50% MAR 2029", "gold"),
    ("GOLDBEES", "NIPPON INDIA ETF GOLD BEES", "gold"),
    ("SILVERBEES", "NIPPON INDIA SILVER ETF", "silver"),
    ("LIQUIDBEES", "NIPPON INDIA ETF LIQUID BEES", "debt"),
    ("EBBETF0430", "BHARAT BOND ETF APRIL 2030", "debt"),
    ("NIFTYBEES", "NIPPON INDIA ETF NIFTY 50 BEES", "etf"),
    ("MON100", "MOTILAL OSWAL NASDAQ 100 ETF", "etf"),
    ("EMBASSY", "EMBASSY OFFICE PARKS REIT", "reit"),
    ("INFY", "INFOSYS", "stock"),
    ("GOLDIAM", "GOLDIAM INTERNATIONAL", "stock"),        # a company with "gold" in its name is a stock
])
def test_what_a_listed_holding_is(symbol, name, kind):
    assert lens.asset_of_share(symbol, name) == kind


@pytest.mark.parametrize("scheme,name,kind", [
    ("equity", "PARAG PARIKH FLEXI CAP FUND", "mf_equity"),
    ("elss", "AXIS LONG TERM EQUITY FUND", "mf_elss"),
    ("equity", "MIRAE ASSET ELSS TAX SAVER FUND", "mf_elss"),
    ("index", "UTI NIFTY 50 INDEX FUND", "mf_index"),
    ("debt", "HDFC SHORT TERM DEBT FUND", "mf_debt"),
    ("liquid", "SBI LIQUID FUND", "mf_liquid"),
    ("balanced", "ICICI PRU BALANCED ADVANTAGE FUND", "mf_hybrid"),
    ("fof", "PPFAS CONSERVATIVE HYBRID FOF", "mf_fof"),
    (None, "SOME NEW FUND", "mf_other"),
])
def test_what_a_fund_is(scheme, name, kind):
    assert lens.asset_of_fund(scheme, name) == kind


def test_names_fund_details_sips_and_the_account():
    meta = {"INF879O01027": {"amc": "PPFAS_MF", "scheme_type": "equity", "plan": "direct", "dividend_type": "growth", "name": "PARAG PARIKH"},
            "INF090I01239": {"amc": "FRANKLIN", "scheme_type": "equity", "plan": "regular", "dividend_type": "payout"}}
    mf = MF + [{"fund": "FRANKLIN INDIA FLEXI CAP FUND", "tradingsymbol": "INF090I01239", "quantity": 100, "average_price": 900, "last_price": 1000}]
    sips = [
        {"sip_id": "1", "fund": "PARAG PARIKH", "tradingsymbol": "INF879O01027", "status": "ACTIVE", "frequency": "monthly",
         "instalment_amount": 10000, "instalment_day": 5, "completed_instalments": 12, "next_instalment": "2026-11-05"},
        {"sip_id": "2", "fund": "FRANKLIN", "tradingsymbol": "INF090I01239", "status": "ACTIVE", "frequency": "weekly", "instalment_amount": 1200},
        {"sip_id": "3", "fund": "OLD", "tradingsymbol": "X", "status": "PAUSED", "frequency": "monthly", "instalment_amount": 5000},
    ]
    margins = {"equity": {"net": 52000.5, "available": {"cash": 40000, "live_balance": 41000, "opening_balance": 40000, "collateral": 12000},
                          "utilised": {"debits": 1000, "payout": 0}}}
    out = lens.build(EQ, mf, names={408065: "INFOSYS"}, mf_meta=meta, sips=sips, margins=margins)
    assert next(r for r in out["equity"] if r["symbol"] == "INFY")["name"] == "INFOSYS"
    ff = next(f for f in out["mf"] if f["isin"] == "INF090I01239")
    assert (ff["amc"], ff["scheme_type"], ff["plan"], ff["option"], ff["asset"]) == ("FRANKLIN", "equity", "regular", "payout", "mf_equity")
    assert [s["status"] for s in out["sips"]] == ["ACTIVE", "ACTIVE", "PAUSED"]
    assert out["sips"][0]["monthly"] == 10000 and out["sips"][0]["next"] == "2026-11-05"
    assert out["sip_monthly"] == pytest.approx(10000 + 1200 * 52 / 12, abs=0.01), "weekly at 52/12 a month; paused counts nothing"
    assert out["account"] == {"net": 52000.5, "cash": 41000.0, "opening": 40000.0, "collateral": 12000.0, "used": 1000.0,
                              "payout": 0.0, "commodity_net": None}
    keys = [a["key"] for a in out["attention"]]
    assert "regular_plans" in keys and "idcw" in keys and "sips_paused" in keys and "pledged" in keys and "t1" in keys
    reg = next(a for a in out["attention"] if a["key"] == "regular_plans")
    assert reg["tone"] == "warn" and reg["title"] == "1 fund is a Regular plan, ₹1,00,000" and reg["items"] == ["FRANKLIN INDIA FLEXI CAP FUND"]


def test_how_its_spread():
    eq = [{"tradingsymbol": s, "instrument_token": i, "quantity": q, "average_price": 1, "last_price": 1, "close_price": 1}
          for i, (s, q) in enumerate([("INFY", 500), ("GOLDBEES", 200), ("NIFTYBEES", 200), ("SGBMAR29", 100)])]
    out = lens.build(eq, [])
    assert [(a["key"], a["weight"]) for a in out["allocation"]] == [("stock", 50.0), ("gold", 30.0), ("etf", 20.0)]
    c = out["concentration"]
    assert c["top"] == {"name": "INFY", "weight": 50.0} and c["top5"] == 100.0
    assert c["effective"] == pytest.approx(1 / (0.5 ** 2 + 0.2 ** 2 + 0.2 ** 2 + 0.1 ** 2), abs=0.05)
    heavy = [a for a in out["attention"] if a["key"].startswith("heavy:")]
    assert [a["items"] for a in heavy] == [["INFY"]], "one company over a fifth; ETFs and gold aren't one company"


def test_what_needs_a_look_from_the_holdings_alone():
    eq = ([{"tradingsymbol": "DOWN", "instrument_token": 1, "quantity": 10, "average_price": 100, "last_price": 60, "close_price": 61}]
          + [{"tradingsymbol": f"S{i}", "instrument_token": 10 + i, "quantity": 1, "average_price": 1, "last_price": 1, "close_price": 1} for i in range(4)]
          + [{"tradingsymbol": "BIG", "instrument_token": 2, "quantity": 100, "average_price": 10, "last_price": 10, "close_price": 10,
              "discrepancy": True}])
    out = lens.build(eq, [], margins={"equity": {"net": 0, "available": {"cash": 250000}, "utilised": {}}})
    by = {a["key"]: a for a in out["attention"]}
    assert by["deep_loss"]["items"] == ["DOWN -40%"]
    assert by["small"]["title"].startswith("4 holdings are under 0.5% each")
    assert by["discrepancy"]["items"] == ["BIG"]
    assert "idle_cash" in by and by["idle_cash"]["title"] == "₹2,50,000 cash in the account", "Indian digit grouping"
    tones = [a["tone"] for a in out["attention"]]
    assert tones == sorted(tones, key=lambda t: {"warn": 0, "good": 1, "info": 2}[t]), "what to act on first"


# ── A year of prices ─────────────────────────────────────────────────────

def candles(f, days=380, end=TODAY):
    """Weekday candles ending `end`: close f(i) on the i-th weekday from
    the start, high/low 1% either side."""
    out, d, i = [], end - timedelta(days=days), 0
    while d <= end:
        if d.weekday() < 5:
            c = f(i)
            out.append({"date": datetime(d.year, d.month, d.day), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c})
            i += 1
        d += timedelta(days=1)
    return out


def test_a_shares_year():
    up = candles(lambda i: 100 * 1.001 ** i)
    last = up[-1]["close"]
    s = lens.share_stats(up, last, TODAY)
    start_1y = next(c["close"] for c in reversed(up) if c["date"].date() <= TODAY - timedelta(days=365))
    assert s["ret"]["1Y"] == pytest.approx((last / start_1y - 1) * 100, abs=0.01)
    assert s["ret"]["1W"] == pytest.approx((last / next(c["close"] for c in reversed(up) if c["date"].date() <= TODAY - timedelta(days=7)) - 1) * 100, abs=0.01)
    assert s["hi52"] == pytest.approx(last * 1.01, abs=0.01) and s["off_high"] == pytest.approx(-0.99, abs=0.01)
    assert s["range_pos"] > 95, "a steady climb sits at the top of its range"
    assert s["vol"] == pytest.approx(0.0, abs=0.01), "the same move every day: no volatility"
    assert len(s["spark"]) == len(s["spark_days"]) == 60 and s["spark"][-1] == pytest.approx(last, abs=0.01)
    assert s["spark_days"][-1] == TODAY.isoformat() and s["spark_days"] == sorted(s["spark_days"])
    live = lens.share_stats(up, last * 1.02, TODAY)
    assert live["spark"][-1] == pytest.approx(last * 1.02, abs=0.01) and live["spark_days"].count(TODAY.isoformat()) == 1, \
        "the price now takes today's candle's place, not a second point"
    # a share listed three months ago: no 6-month or 1-year return, not a made-up one
    young = lens.share_stats(candles(lambda i: 50.0, days=90), 50.0, TODAY)
    assert young["ret"]["6M"] is None and young["ret"]["1Y"] is None and young["ret"]["1M"] == 0.0
    assert lens.share_stats([], 10.0, TODAY) == {"ret": {}, "spark": [], "days": 0}


def test_volatility_and_beta_against_nifty():
    wig = lambda i: 0.01 * math.sin(i * 1.7)                         # noqa: E731
    nifty = candles(lambda i: 20000 * math.exp(sum(wig(j) for j in range(i + 1))))
    double = candles(lambda i: 100 * math.exp(sum(2 * wig(j) for j in range(i + 1))))
    n = [x for x in lens._closes(nifty) if x[0] >= TODAY - timedelta(days=365)]
    s = lens.share_stats(double, double[-1]["close"], TODAY, n)
    assert s["beta"] == pytest.approx(2.0, abs=0.01), "moves twice Nifty's every day"
    sn = lens.share_stats(nifty, nifty[-1]["close"], TODAY, n)
    assert s["vol"] == pytest.approx(2 * sn["vol"], rel=0.02)


def test_the_shares_together_against_nifty():
    nifty = candles(lambda i: 20000 + 20 * i)
    a = candles(lambda i: 100 + i)              # rises
    b = candles(lambda i: 200 - 0.2 * i)        # slips
    newer = candles(lambda i: 50.0, days=60)    # listed two months ago
    eq = [{"symbol": "A", "token": 1, "qty": 10, "value": 0}, {"symbol": "B", "token": 2, "qty": 5, "value": 0},
          {"symbol": "NEW", "token": 3, "qty": 1, "value": 0}]
    c = lens.portfolio_curve(eq, {1: a, 2: b, 3: newer}, nifty, TODAY)
    assert c["covered"] == 2 and c["left_out"] == ["NEW"], "a newer listing would jump the line: named, not in it"
    assert c["portfolio"][0] == 100 and c["nifty"][0] == 100 and len(c["days"]) == len(c["portfolio"]) == len(c["nifty"])
    first = lens._at(lens._closes(a), date.fromisoformat(c["days"][0])) * 10 + lens._at(lens._closes(b), date.fromisoformat(c["days"][0])) * 5
    assert c["value"][0] == pytest.approx(first, abs=0.01)
    one_y = next(p for p in c["periods"] if p["k"] == "1Y")
    assert one_y["portfolio"] == pytest.approx((c["value"][-1] / c["value"][0] - 1) * 100, abs=0.01)
    one_m = next(p for p in c["periods"] if p["k"] == "1M")
    i = c["days"].index(one_m["from"])   # the chart's window starts on the same day
    assert date.fromisoformat(one_m["from"]) <= TODAY - timedelta(days=30) < date.fromisoformat(c["days"][i + 1])
    assert one_m["portfolio"] == pytest.approx((c["value"][-1] / c["value"][i] - 1) * 100, abs=0.01)
    assert one_y["nifty"] > 0 and c["drawdown"]["pct"] <= 0
    assert lens.portfolio_curve(eq, {}, nifty, TODAY) is None, "no prices for any share: no line"


def test_drawdown():
    days = [TODAY - timedelta(days=4 - i) for i in range(5)]
    dd = lens._drawdown([100, 120, 90, 110, 130], days)
    assert dd == {"pct": -25.0, "from": days[1].isoformat(), "to": days[2].isoformat()}


def test_what_needs_a_look_with_the_year():
    nifty = candles(lambda i: 20000 * 1.0008 ** i)                     # about +20% a year
    flat_low = candles(lambda i: 100 - 0.1 * i)                        # sliding to its low
    at_high = candles(lambda i: 100 + 0.2 * i)
    wild = candles(lambda i: 100 * (1 + 0.06 * math.sin(i)))
    flat = [{**c, "open": 1000, "high": 1000, "low": 1000, "close": 1000} for c in candles(lambda i: 1000)]   # a liquid ETF
    eq = [{"tradingsymbol": s, "instrument_token": t, "quantity": q, "average_price": 1, "last_price": c[-1]["close"], "close_price": c[-2]["close"]}
          for s, t, q, c in [("LOW", 1, 10, flat_low), ("HIGH", 2, 10, at_high), ("WILD", 3, 10, wild), ("LIQUIDBEES", 4, 1, flat)]]
    h = lens.build(eq, [])
    ins = lens.insights(h, {1: flat_low, 2: at_high, 3: wild, 4: flat}, nifty, TODAY)
    by = {a["key"]: a for a in ins["attention"]}
    assert by["near_low"]["items"][0].startswith("LOW ")
    assert by["near_high"]["items"] == ["HIGH"] and by["near_high"]["tone"] == "good", "a price that never moves isn't near a high"
    assert any(x.startswith("LOW ") for x in by["lagging"]["items"]), "trails Nifty's year by 20 points or more"
    assert by["volatile"]["items"][0].startswith("WILD ")
    assert ins["curve"]["covered"] == 4 and ins["nifty"]["ret"]["1Y"] > 15


# ── Reading it from Kite ─────────────────────────────────────────────────

def fake_kite(fail=(), prices=None, asked=None):
    prices = prices or {}

    async def call(what, *args):
        if asked is not None:
            asked.append((what, *args[:1]))
        if what in fail or (what == "historical_data" and args[0] in fail):
            raise RuntimeError(f"{what} refused")
        if what == "holdings":
            return EQ
        if what == "mf_holdings":
            return MF
        if what == "instruments":
            return [{"instrument_token": 408065, "name": "INFOSYS"}]
        if what == "mf_instruments":
            return [{"tradingsymbol": "INF879O01027", "scheme_type": "equity", "plan": "direct", "dividend_type": "growth"}]
        if what == "mf_sips":
            return []
        if what == "margins":
            return {"equity": {"net": 1000, "available": {"cash": 1000}, "utilised": {}}}
        if what == "historical_data":
            await asyncio.sleep(0)
            return prices.get(args[0], candles(lambda i: 100.0 + i))
    return call


def test_read_once_a_minute_unless_asked_fresh_and_the_lists_once_a_day():
    asked = []

    async def go():
        L = lens.Lens(None, call=fake_kite(asked=asked), today=lambda: TODAY)
        a = await L.holdings()
        b = await L.holdings()
        c = await L.holdings(fresh=True)
        return a, b, c
    a, b, c = asyncio.run(go())
    assert a is b and [x[0] for x in asked].count("holdings") == 2
    assert [x[0] for x in asked].count("instruments") == 1 and [x[0] for x in asked].count("mf_instruments") == 1, "the lists: once a day"
    assert a["equity"][1]["name"] == "INFOSYS" and a["missing"] == [] and c["as_of"]


def test_what_kite_wont_give_is_named_not_fatal():
    async def go():
        return await lens.Lens(None, call=fake_kite(fail=("mf_sips", "margins", "instruments")), today=lambda: TODAY).holdings()
    out = asyncio.run(go())
    assert out["sips"] == [] and out["account"] is None and out["equity"]
    assert [m.split(":")[0] for m in out["missing"]] == ["NSE names", "SIPs", "account funds"]


def test_the_year_is_fetched_in_the_background_and_answered_when_in():
    async def go():
        L = lens.Lens(None, call=fake_kite(fail=(424961,)), today=lambda: TODAY)   # ITC's prices refused
        first = await L.history()
        await L.hist["task"]
        second = await L.history()
        return first, second, L
    first, second, L = asyncio.run(go())
    assert first["state"] == "loading" and first["total"] == 3 and first["done"] < 3
    assert second["state"] == "ready" and second["done"] == 3
    ins = second["insights"]
    assert ins["shares"]["INFY"]["ret"]["1Y"] is not None and ins["shares"]["ITC"] == {"ret": {}, "spark": [], "days": 0}
    assert ins["curve"]["left_out"] == ["ITC"] and ins["curve"]["covered"] == 1


def test_without_nifty_the_year_says_why():
    async def go():
        L = lens.Lens(None, call=fake_kite(fail=(lens.NIFTY,)), today=lambda: TODAY)
        await L.history()
        await L.hist["task"]
        return await L.history()
    out = asyncio.run(go())
    assert out["state"] == "ready" and out["insights"]["curve"] is None and out["insights"]["nifty"] is None
    assert out["error"].startswith("NIFTY 50 prices: RuntimeError")


def test_the_browser_tests_see_what_the_server_sends():
    """tests/e2e/lens.fixture.json is the made-up account read through this
    module; rerun tests/e2e/lens_fixture.py after changing what it answers."""
    import asyncio
    import importlib.util
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "e2e" / "lens_fixture.py"
    spec = importlib.util.spec_from_file_location("lens_fixture", path)
    fx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fx)
    made = json.loads(json.dumps(asyncio.run(fx.make()), ensure_ascii=False))
    assert made == json.loads(fx.OUT.read_text()), "stale: run python tests/e2e/lens_fixture.py"
