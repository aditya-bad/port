"""Your tradebook files in LinckLens (app/lens.py, app/lens_trades.py):
stored once however often they're imported, read against what Kite
holds, and the shares day by day against the same money in Nifty, from
prices read once."""

from datetime import date, datetime, timedelta

from app import lens
from tests.db.test_lens_store import at, daily

HEAD = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time\n"
JAN = HEAD + ("INFY,INE009A01021,2025-01-06,NSE,EQ,EQ,buy,false,10,1500,101,9101,2025-01-06 10:00:00\n"
              "TCS,INE467B01029,2025-02-03,NSE,EQ,EQ,buy,false,5,4000,102,9102,2025-02-03 10:00:00\n"
              "PARAG PARIKH FLEXI CAP FUND - DIRECT PLAN,INF879O01027,2025-01-10,BSE,MF,,buy,false,100.5,70,7001,7001,2025-01-10 0:00:00\n")
LATER = HEAD + ("TCS,INE467B01029,2025-02-03,NSE,EQ,EQ,buy,false,5,4000,102,9102,2025-02-03 10:00:00\n"     # in both files
                "TCS,INE467B01029,2026-06-01,NSE,EQ,EQ,sell,false,5,3500,103,9103,2026-06-01 11:00:00\n"
                "INFY,INE009A01021,2026-03-02,NSE,EQ,EQ,buy,false,4,1400,104,9104,2026-03-02 09:30:00\n"
                "INFY,INE009A01021,not a day,NSE,EQ,EQ,buy,false,4,1400,105,9105,\n")
WED = date(2026, 10, 14)


def kite(asked):
    async def call(what, *args):
        asked.append((what, *args[:3]))
        if what == "holdings":
            return [{"tradingsymbol": "INFY", "exchange": "NSE", "isin": "INE009A01021", "instrument_token": 408065,
                     "quantity": 16, "average_price": 1470.0, "last_price": 1600.0, "close_price": 1590.0}]
        if what == "mf_holdings":
            return [{"tradingsymbol": "INF879O01027", "fund": "PARAG PARIKH FLEXI CAP FUND - DIRECT PLAN", "quantity": 100.5,
                     "average_price": 70.0, "last_price": 90.0}]
        if what == "instruments":
            return [{"instrument_token": 408065, "tradingsymbol": "INFY", "name": "INFOSYS"},
                    {"instrument_token": 2953217, "tradingsymbol": "TCS", "name": "TATA CONSULTANCY"}]
        if what == "historical_data":
            return daily(args[1], args[2])
        return [] if what in ("mf_sips", "mf_instruments") else {}
    return call


def test_import_store_once_report_and_remove(pool_run):
    async def go(pool):
        asked, now = [], [at(WED, 11)]
        L = lens.Lens(None, call=kite(asked), pool=pool, now=lambda: now[0])
        assert (await L.trades_report())["analysis"] is None, "nothing imported yet"
        r1 = await L.import_trades([{"name": "2025.csv", "text": JAN}])
        r2 = await L.import_trades([{"name": "later.csv", "text": LATER}, {"name": "2025.csv", "text": JAN}])
        assert [(x["rows"], x["added"], x["skipped"]) for x in r1 + r2] == [(3, 3, 0), (3, 2, 1), (3, 0, 3)]
        assert r2[0]["problems"] == ["line 5: bad trade_date"] and r2[0]["first_day"] == "2025-02-03"
        assert await pool.fetchval("SELECT count(*) FROM lens_trades") == 5

        rep = await L.trades_report()
        assert [i["filename"] for i in rep["imports"]] == ["2025.csv", "later.csv", "2025.csv"] and rep["holdings_error"] is None
        a = rep["analysis"]
        assert a["count"] == 5 and a["totals"]["equity"]["realized"] == -2500.0
        infy = next(p for p in a["holdings"] if p["symbol"] == "INFY")
        assert infy["open_qty"] == 14 and infy["held_qty"] == 16 and infy["price"] == 1600.0
        assert [r["symbol"] for r in a["reconcile"]] == ["INFY"], "two INFY the files don't show"
        assert a["totals"]["mf"]["value"] == 100.5 * 90.0
        # the shares day by day: prices read once, back to the first trade
        assert rep["curve"]["state"] == "loading"
        await L.hist["trades_task"]
        hist = [x for x in asked if x[0] == "historical_data"]
        assert sorted(x[1] for x in hist) == [lens.NIFTY, 408065, 2953217]
        assert {x[1]: x[2] for x in hist}[lens.NIFTY] == date(2025, 1, 6), "Nifty from the first share bought"
        assert {x[1]: x[2] for x in hist}[408065] == date(2025, 1, 6) - timedelta(days=lens.HABIT_BEFORE_DAYS), \
            "a share from a year before its first buy: where it stood when bought"
        rep = await L.trades_report()
        c = rep["curve"]
        assert c["state"] == "ready" and c["days"][0] == "2025-01-06" and c["left_out"] == [] and c["unknown"] == []
        assert c["invested"][0] == 15000 and c["now"]["invested"] == 15000 + 20000 + 5600 - 17500
        # how you trade: the findings from prices join once they're in
        hb = rep["habits"]
        assert hb["priced"] and [s["symbol"] for s in hb["after_sale"]["sales"]] == ["TCS"]
        assert hb["after_sale"]["sales"][0]["m3"] is not None and hb["costs"]["booked"] == -2500.0
        # the next evening: INFY and Nifty read again for the day, and TCS
        # too: sold in June, it's followed for a year after
        n = len([x for x in asked if x[0] == "historical_data"])
        now[0] = at(WED + timedelta(days=1), 17, 30)
        assert (await L.trades_report())["curve"]["state"] == "loading"
        await L.hist["trades_task"]
        again = [x[1] for x in asked if x[0] == "historical_data"][n:]
        assert sorted(again) == [lens.NIFTY, 408065, 2953217]
        # removing the later file takes its trades
        later = next(i["id"] for i in rep["imports"] if i["filename"] == "later.csv")
        assert await L.remove_import(later) and not await L.remove_import(later)
        assert await pool.fetchval("SELECT count(*) FROM lens_trades") == 3
    pool_run(go)


def test_without_kite_the_trades_still_say_what_they_can(pool_run):
    from app.options import NoKiteSession

    async def no_kite(what, *args):
        raise NoKiteSession("no access token")

    async def go(pool):
        L = lens.Lens(None, call=no_kite, pool=pool, now=lambda: at(WED, 11))
        await L.import_trades([{"name": "2025.csv", "text": JAN}])
        rep = await L.trades_report()
        a = rep["analysis"]
        assert rep["holdings_error"].startswith("NoKiteSession") and a["totals"]["equity"]["bought"] == 35000
        assert a["holdings"][0]["value"] is None and a["totals"]["equity"]["unpriced"] == ["INFY", "TCS"]
        assert rep["curve"]["state"] == "loading", "it tries Kite for the prices"
        assert rep["habits"]["priced"] is False and rep["habits"]["costs"]["years"], "what the trades alone say, at once"
        await L.hist["trades_task"]
        rep = await L.trades_report()
        assert rep["curve"] == {"state": "error", "error": "Kite gave no NIFTY 50 prices back to your first trade"}, \
            "it says so, and doesn't ask again for ten minutes"
        assert "trades_task" in L.hist and L.hist["trades_task"].done()
    pool_run(go)


def test_inspector_reads_your_own_money_the_tax_free_room_and_what_if(pool_run):
    async def go(pool):
        asked = []
        L = lens.Lens(None, call=kite(asked), pool=pool, now=lambda: at(WED, 11))
        await L.import_trades([{"name": "2025.csv", "text": JAN}, {"name": "later.csv", "text": LATER}])
        await L.history()
        await L.hist["task"]
        n = len(asked)
        x = await L.inspect()
        assert len(asked) == n, "nothing more asked of Kite: the holdings are kept, the prices stored"
        o = x["own"]
        # INFY 15,000, the fund 7,035, TCS 20,000 and INFY again 5,600 (March, before any sale): all your own;
        # TCS's sale in June gave 17,500 that nothing bought with since
        assert (o["own"], o["pot"], o["booked"]) == (15000 + 7035 + 20000 + 5600, 17500, -2500)
        assert o["profit_in_holdings"] == -2500, "a loss booked: what you hold at cost is that much below what went in"
        assert x["tax"]["fy"] == "2026-27" and x["tax"]["exemption"] == 125000
        assert [h["symbol"] for h in x["holdings"]] == ["INFY", "PARAG PARIKH FLEXI CAP FUND - DIRECT PLAN"]
        assert {c["symbol"] for c in x["contributions"]} == {"INFY", "TCS", "PARAG PARIKH FLEXI CAP FUND - DIRECT PLAN"}
        w = await L.whatif({"INE467B01029"})                         # TCS, never bought
        assert w["without"] == ["INE467B01029"] and w["with"]["trades"]["booked"] == -2500 and w["without_them"]["trades"]["booked"] == 0
        assert w["with"]["now"]["count"] == 2 and w["without_them"]["now"]["count"] == 2, "TCS isn't held: today's holdings the same"
    pool_run(go)
