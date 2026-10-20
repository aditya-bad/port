"""Geek mode for pivot_supertrend_options (app/geek/supertrend_options.py):
the rules read the same SuperTrend line and pivot levels the strategy
trades on, the next move says what the strategy will do, and every trade
view adds up to the book, on the simulated market."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.db import queries
from app.geek import supertrend_options as G
from tests.sim import Sim
from tests.strategies.helpers import cfg
from tests.strategies.test_all_strategies_smoke import market, trading_days

S = "pivot_supertrend_options"
IST = ZoneInfo("Asia/Kolkata")
PIV = {"S3": 24700.0, "S2": 24800.0, "S1": 24900.0, "P": 25000.0, "R1": 25100.0, "R2": 25200.0, "R3": 25300.0}


def reading(**over):
    r = {"source": "live", "st": {"trend": "up", "upper": 25400.0, "lower": 24950.0, "atr": 40.0, "multiplier": 3},
         "pivots": dict(PIV), "trades_today": 1, "max_trades": 3, "close": 25050.0, "close_is_live": True,
         "open_symbol": None, "force_exit": time(15, 0), "market_open": time(9, 15)}
    r.update(over)
    return r


def at(hh, mm):
    return datetime(2026, 10, 20, hh, mm, tzinfo=IST)


def rule(rs, key):
    return next(r for r in rs if r["key"] == key)


# ── the rules, on readings we choose ────────────────────────────────────

def test_rules_in_the_order_the_strategy_checks_them():
    rs = G.rules(reading(), at(11, 0), holding=False)
    assert [r["key"] for r in rs] == ["st_flip", "force_exit", "pivot_break", "max_trades"]
    assert all(0 <= r["fill"] <= 1 for r in rs)


def test_the_line_is_the_lower_band_going_up_and_the_upper_going_down():
    assert G.line_of({"trend": "up", "lower": 1.0, "upper": 2.0}) == 1.0
    assert G.line_of({"trend": "down", "lower": 1.0, "upper": 2.0}) == 2.0
    assert G.line_of({"trend": None, "lower": 1.0, "upper": 2.0}) is None and G.line_of(None) is None


def test_entry_level_is_r1_going_up_and_s1_going_down():
    assert G.trigger_level("up", PIV) == ("R1", 25100.0)
    assert G.trigger_level("down", PIV) == ("S1", 24900.0)
    assert G.trigger_level(None, PIV) is None and G.trigger_level("up", None) is None


def test_bracket_places_the_close_on_the_ladder():
    assert G.bracket(25050, PIV) == "P–R1"
    assert G.bracket(25100, PIV) == "R1–R2"
    assert G.bracket(24600, PIV) == "below S3" and G.bracket(25350, PIV) == "above R3"
    assert G.bracket(None, PIV) is None and G.bracket(25000, None) is None


def test_flip_gauge_fills_as_the_close_nears_the_line():
    far = rule(G.rules(reading(close=25070.0), at(11, 0), holding=True), "st_flip")    # 120 pts above, band 120
    near = rule(G.rules(reading(close=24960.0), at(11, 0), holding=True), "st_flip")   # 10 pts above
    assert far["fill"] == 0 and far["state"] == "ok" and "120 pts above" in far["reading"]
    assert near["fill"] > 0.9 and near["state"] == "armed"
    through = rule(G.rules(reading(close=24940.0), at(11, 0), holding=True), "st_flip")
    assert through["fill"] == 1 and "flips on this candle" in through["reading"]
    assert rule(G.rules(reading(close=24960.0), at(11, 0), holding=False), "st_flip")["state"] == "idle", "nothing to close"
    down = rule(G.rules(reading(st={"trend": "down", "upper": 25100.0, "lower": 24600.0, "atr": 40.0, "multiplier": 3},
                                close=25050.0), at(11, 0), holding=True), "st_flip")
    assert "50 pts below" in down["reading"]


def test_entry_rule_and_next_move_agree():
    eng = lambda r, t=at(11, 0), h=None: (rule(G.rules(r, t, h is not None), "pivot_break"),  # noqa: E731
                                          G.next_move(r, t, h, True, {"text": "x"})["text"])
    r, txt = eng(reading(close=25050.0))
    assert r["state"] == "ok" and "50 pts to go · sells a PE" in r["reading"]
    assert txt == "Sells an ATM PE on a 5-minute close above R1 25,100, 50 pts away"
    r, txt = eng(reading(close=25120.0))
    assert r["state"] == "armed" and r["fill"] == 1
    assert txt.startswith("Close is past R1 25,100: sells an ATM PE")
    down = reading(st={"trend": "down", "upper": 25100.0, "lower": 24600.0, "atr": 40.0, "multiplier": 3}, close=24950.0)
    r, txt = eng(down)
    assert "S1 24,900.00 · 50 pts to go · sells a CE" == r["reading"]
    assert txt.endswith("below S1 24,900, 50 pts away")
    r, txt = eng(reading(trades_today=3))
    assert r["state"] == "off" and txt == "Done for today: all 3 entries used"
    r, txt = eng(reading(), at(15, 5))
    assert r["state"] == "done" and txt == "Done for today: no new entries after 15:00"
    r, txt = eng(reading(), h={"symbol": "NIFTY26OCT25000PE"})
    assert r["state"] == "idle" and txt == "Holding NIFTY26OCT25000PE: bought back on a SuperTrend flip (line 24,950, 100 pts away) or at 15:00"
    r, txt = eng(reading(pivots=None))
    assert r["reading"] == "No pivot levels yet" and txt.startswith("Waiting for pivot levels")


def test_cap_and_square_off_rules():
    rs = G.rules(reading(trades_today=2), at(14, 50), holding=True)
    assert rule(rs, "max_trades")["reading"] == "2 of 3 used" and rule(rs, "max_trades")["state"] == "ok"
    fe = rule(rs, "force_exit")
    assert fe["reading"] == "in 10m" and fe["state"] == "armed"
    assert rule(G.rules(reading(), at(15, 1), holding=False), "force_exit")["state"] == "done"
    nocap = rule(G.rules(reading(max_trades=None, trades_today=5), at(11, 0), holding=False), "max_trades")
    assert nocap["state"] == "off" and nocap["reading"] == "5 today · no cap"


# ── on the simulated market ─────────────────────────────────────────────

async def simulate(pool, monkeypatch, days=3):
    sim = await Sim.create(pool, monkeypatch, S, cfg(S), spot=25000)
    for i, d in enumerate(trading_days(sim.market.today, days)):
        await sim.walk(f"{d} 09:15", f"{d} 15:29", every_min=1, spot_fn=market(i + 1))
    return sim


async def ctx(sim, running=True, **params):
    dep = dict(await queries.get_deployment(sim.pool, sim.dep_id))
    return G.Ctx(pool=sim.pool, dep=dep, runner=sim.runner if running else None,
                 now=sim.market.now.replace(tzinfo=IST), params=params)


def test_engine_reads_what_the_strategy_trades_on(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, S, cfg(S), spot=25000)
        d = trading_days(sim.market.today, 2)
        await sim.walk(f"{d[0]} 09:15", f"{d[0]} 15:29", spot_fn=market(1))
        await sim.walk(f"{d[1]} 09:15", f"{d[1]} 11:00", spot_fn=market(2))
        s = sim.strategy
        eng = await G.engine(await ctx(sim))
        st = eng["supertrend"]
        assert eng["running"] and st["source"] == "live" and st["trend"] == s.st.trend
        assert st["line"] == (s.st.final_lower if s.st.trend == "up" else s.st.final_upper)
        assert [x["key"] for x in st["ladder"]] == list(G.LADDER)
        assert [x["value"] for x in st["ladder"]] == sorted(x["value"] for x in st["ladder"])
        assert st["trades_today"] == s.trades_today and st["max_trades"] == 3
        assert [r["key"] for r in eng["rules"]] == ["st_flip", "force_exit", "pivot_break", "max_trades"]
        holding = [l for l in eng["legs"] if l["side"] == "short"]
        assert len(holding) == (1 if s.active_leg_symbol else 0)
        assert eng["next"]["text"].startswith("Holding" if holding else ("Sells", "Close is past", "Done"))
        # paused: the same readings from the state it saved
        await sim.runner.dump_state()
        off = await G.engine(await ctx(sim, running=False))
        assert off["supertrend"]["source"] == "persisted" and off["supertrend"]["line"] == st["line"]
        assert off["running"] is False and off["next"]["text"].startswith(("Paused", "Not running", "Stopped"))
    pool_run(go)


def test_signals_add_up_to_the_book(pool_run, monkeypatch):
    async def go(pool):
        sim = await simulate(pool, monkeypatch)
        body = await G.signals(await ctx(sim))
        pos = await sim.all_positions()
        closed = [p for p in pos if p["status"] == "closed"]
        tr = body["trades"]
        assert len(tr) == len(pos) >= 3 and body["summary"]["closed"] == len(closed)
        assert round(sum(t["pnl"] for t in tr if not t["open"]), 2) == round(sum(float(p["realized_pnl"]) for p in closed), 2)
        assert body["summary"]["pnl"] == round(sum(float(p["realized_pnl"]) for p in closed), 2)
        for t in tr:
            assert t["level"] in G.R_KEYS + G.S_KEYS
            assert (t["opt"], t["side"]) in (("PE", "long"), ("CE", "short"))
            assert (t["level"] in G.R_KEYS) == (t["side"] == "long"), "a PE is sold on an R break, a CE on an S break"
            assert t["past_level"] > 0, "the close was beyond the level it broke"
            assert t["from_st"] > 0, "the close was on the trend's side of the SuperTrend line"
            if not t["open"]:
                assert t["exit_reason"] in ("st_flip", "force_exit") and t["hold_min"] > 0
                assert t["kept_pct"] == round((t["entry_px"] - t["exit_px"]) / t["entry_px"] * 100, 1)
        n = len(closed)
        assert sum(g["n"] for g in body["by_side"].values()) == n
        assert sum(g["n"] for g in body["by_level"].values()) == n
        assert sum(g["n"] for g in body["by_exit"].values()) == n
        assert sum(h["n"] for h in body["by_hour"]) == n and sum(h["n"] for h in body["hold"]) == n
        assert sum(d["trades"] for d in body["days"]) == len(tr)
        assert round(sum(d["pnl"] for d in body["days"]), 2) == body["summary"]["pnl"]
        decs = await queries.list_decisions(pool, sim.dep_id, limit=5000)
        assert sum(d["cap_hits"] for d in body["days"]) == sum(1 for x in decs if x["rule"] == "max_trades")
        per_day = {}
        for t in tr:
            per_day[t["entry_t"][:10]] = per_day.get(t["entry_t"][:10], 0) + 1
        assert max(per_day.values()) <= 3, "never more than the daily cap"
    pool_run(go)


def test_one_trade_end_to_end(pool_run, monkeypatch):
    async def go(pool):
        sim = await simulate(pool, monkeypatch, days=2)
        rows = await G.trade_rows(await ctx(sim))
        newest = await G.trade(await ctx(sim))
        assert newest["trade"]["id"] == rows[-1]["id"] and newest["no"] == newest["n"] == len(rows)
        assert newest["next"] is None and newest["prev"] == rows[-2]["id"]
        first = await G.trade(await ctx(sim, p=rows[0]["id"]))
        assert first["no"] == 1 and first["prev"] is None and first["next"] == rows[1]["id"]
        t = first["trade"]
        assert t["pivots"] and set(G.LADDER) <= set(t["pivots"]), "the levels it was taken on"
        assert all(d["t"] >= (datetime.fromisoformat(t["entry_t"]).isoformat()[:10]) for d in first["decisions"])
        assert (await G.trade(await ctx(sim, p="nope")))["trade"] is None
    pool_run(go)
