"""Geek mode for intraday_dtt_adjusted and intraday_dtt_advanced
(app/geek/dtt_adjusted.py): the rules read what the strategy acts on,
the advanced one shows rolls where the adjusted one shows a used-up
budget, and every view adds up to the book, on the simulated market."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.db import queries
from app.geek import dtt_adjusted as G
from tests.sim import Sim
from tests.strategies.helpers import cfg
from tests.strategies.test_all_strategies_smoke import market, trading_days

IST = ZoneInfo("Asia/Kolkata")
BOTH = ["intraday_dtt_adjusted", "intraday_dtt_advanced"]


def reading(**over):
    r = {"entry_time": time(10, 0), "force_exit": time(15, 0), "profit_pct": 0.10, "trigger_ratio": 0.5, "size_pct": 0.25,
         "max_adjustments": 2, "be_mult": 1.0, "rolling": False, "spot": 25000.0,
         "legs": {"CE": [{"symbol": "N25000CE", "role": "original", "strike": 25000, "entry": 100.0, "now": 140.0}],
                  "PE": [{"symbol": "N25000PE", "role": "original", "strike": 25000, "entry": 100.0, "now": 84.0}]},
         "adjusted_side": None, "adjustments_used": 0, "entered_today": True, "combined_entry": 200.0,
         "breakeven": [24800.0, 25200.0], "realized_today": 0.0, "target": 1500.0}
    r.update(over)
    return r


def at(hh, mm):
    return datetime(2026, 10, 20, hh, mm, tzinfo=IST)


def rule(rs, key):
    return next(r for r in rs if r["key"] == key)


# ── the rules, on readings we choose ────────────────────────────────────

def test_rules_in_the_strategys_order():
    r = reading()
    rs = G.rules(r, G.day_state(r), at(11, 0), 300.0)
    assert [x["key"] for x in rs] == ["entry", "force_exit", "breakeven", "profit_target", "adjustment", "reversal", "cap"]
    assert all(0 <= x["fill"] <= 1 for x in rs)


def test_side_comparison_before_and_after_a_side_is_adjusted():
    st = G.day_state(reading())
    assert (st["smaller"], st["bigger"], st["ratio"]) == ("PE", "CE", 0.6)
    two = reading(adjusted_side="PE", legs={
        "CE": [{"symbol": "C", "role": "original", "strike": 25000, "entry": 100.0, "now": 160.0}],
        "PE": [{"symbol": "P", "role": "original", "strike": 25000, "entry": 100.0, "now": 50.0},
               {"symbol": "P2", "role": "adjustment_1", "strike": 24900, "entry": 40.0, "now": 30.0}]})
    st = G.day_state(two)
    assert st["smaller"] == "PE" and st["totals"]["PE"] == 80.0 and st["ratio"] == 0.5, "the adjusted side is summed against the anchor"
    assert st["legs_open"] == {"CE": 1, "PE": 2}


def test_adjustment_reversal_and_cap_rules():
    r = reading()
    rs = G.rules(r, G.day_state(r), at(11, 0), 300.0)
    a = rule(rs, "adjustment")
    assert a["fill"] == 0.8 and a["state"] == "ok" and "PE 84.0 is 60% of CE" in a["reading"]
    assert rule(rs, "reversal")["state"] == "idle" and rule(rs, "cap")["reading"] == "0 of 2 used"
    used = reading(adjustments_used=2)
    rs = G.rules(used, G.day_state(used), at(11, 0), 300.0)
    assert rule(rs, "adjustment")["chip"] == "Cap used" and rule(rs, "cap")["chip"] == "Used up"
    # the advanced one rolls at the cap instead
    roll = reading(rolling=True, adjustments_used=5, adjusted_side="PE", legs={
        "CE": [{"symbol": "C", "role": "original", "strike": 25000, "entry": 100.0, "now": 160.0}],
        "PE": [{"symbol": "P", "role": "original", "strike": 25000, "entry": 100.0, "now": 40.0},
               {"symbol": "P2", "role": "adjustment_4", "strike": 24900, "entry": 40.0, "now": 20.0},
               {"symbol": "P3", "role": "adjustment_5", "strike": 24800, "entry": 40.0, "now": 20.0}]})
    rs = G.rules(roll, G.day_state(roll), at(11, 0), 300.0)
    assert rule(rs, "adjustment")["state"] != "off" and "rolls the cheapest leg" in rule(rs, "adjustment")["desc"]
    assert rule(rs, "cap")["chip"] == "At the cap: rolls" and "5 sold today" in rule(rs, "cap")["reading"]
    rev = rule(rs, "reversal")
    assert rev["state"] == "ok" and rev["chip"] == "2 extra legs" and "50% of the anchor" in rev["reading"]


def test_breakeven_and_target_and_next_move():
    r = reading(spot=25170.0)
    st = G.day_state(r)
    rs = G.rules(r, st, at(11, 0), 1400.0)
    be = rule(rs, "breakeven")
    assert be["fill"] == 0.85 and be["state"] == "armed" and "30 pts to the upper edge" in be["reading"]
    pt = rule(rs, "profit_target")
    assert round(pt["fill"], 3) == 0.933 and pt["reading"] == "day 1,400 of 1,500"
    txt = G.next_move(r, st, at(11, 0), True, {"text": "x"}, 1400.0)["text"]
    assert txt.startswith("Holding 2 legs: books everything at a day P&L of 1,500 (now 1,400)")
    assert "adjusts PE if it falls to 50% of CE (now 60%)" in txt and "flattens if the index leaves 24,800–25,200" in txt
    flat = reading(legs={"CE": [], "PE": []}, entered_today=False)
    assert G.next_move(flat, G.day_state(flat), at(9, 40), True, {"text": "x"}, None)["text"] == "Sells the ATM straddle at 10:00, in 20m"
    assert G.rules(flat, G.day_state(flat), at(9, 40), None)[0]["state"] == "ok"


# ── on the simulated market ─────────────────────────────────────────────

async def simulate(pool, monkeypatch, strategy, days=4):
    sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), spot=25000)
    for i, d in enumerate(trading_days(sim.market.today, days)):
        await sim.walk(f"{d} 09:15", f"{d} 15:29", every_min=1, spot_fn=market(i + 1))
    return sim


async def ctx(sim, running=True, **params):
    dep = dict(await queries.get_deployment(sim.pool, sim.dep_id))
    return G.Ctx(pool=sim.pool, dep=dep, runner=sim.runner if running else None,
                 now=sim.market.now.replace(tzinfo=IST), params=params)


@pytest.mark.parametrize("strategy", BOTH)
def test_sessions_and_moves_add_up_to_the_book(strategy, pool_run, monkeypatch):
    async def go(pool):
        sim = await simulate(pool, monkeypatch, strategy)
        c = await ctx(sim)
        body = await G.sessions(c)
        pos = await sim.all_positions()
        fills = await sim.fills()
        rows = body["sessions"]
        assert len(rows) >= 3 and len({r["day"] for r in rows}) == len(rows), "one session a day"
        assert sum(len(r["legs"]) for r in rows) == len([p for p in pos if p["side"] == "short"])
        closed = [p for p in pos if p["status"] == "closed"]
        assert body["summary"]["pnl"] == round(sum(float(p["realized_pnl"]) for p in closed), 2)
        trig = lambda t: sum(1 for f in fills if (f["metadata"] or {}).get("trigger") == t)  # noqa: E731
        assert body["summary"]["adjustments"] == trig("adjustment")
        assert body["summary"]["rolls"] == trig("roll_open") and body["summary"]["unwinds"] == trig("reversal_unwind")
        assert body["summary"]["adjustments"] + body["summary"]["rolls"] > 0, "the market made it adjust"
        if strategy == "intraday_dtt_adjusted":
            assert body["summary"]["rolls"] == 0
        for r in rows:
            orig = [l for l in r["legs"] if l["role"] == "original"]
            assert sorted(l["side"] for l in orig) == ["CE", "PE"] and orig[0]["strike"] == orig[1]["strike"]
            assert r["combined_entry"] == round(sum(l["entry"] for l in orig), 2)
            assert r["breakeven"][0] < r["spot"] < r["breakeven"][1]
            assert r["max_legs"] <= 2 + 2, "never more than the original two plus the cap on one side"
            assert all(l["side"] == r["adjusted_side"] for l in r["legs"] if l["role"] != "original"), "only one side gets extra legs"
            if not r["open"]:
                assert r["reason"] in G.FLATTEN
                assert r["pnl"] == round(r["pnl_original"] + (r["pnl_extra"] or 0), 2)
        n = len([r for r in rows if not r["open"]])
        assert sum(g["n"] for g in body["by_exit"].values()) == n == sum(g["n"] for g in body["by_moves"])
        m = await G.moves(c)
        sold = [x for x in m["rows"] if x["kind"] in ("adjustment", "roll")]
        assert len(sold) == trig("adjustment") + trig("roll_open")
        assert all(x["ratio"] is not None and x["ratio"] <= 0.5 + 1e-6 for x in sold if x["kind"] == "adjustment"), "sold at the trigger"
        if m["cumulative"]:
            assert m["cumulative"][-1]["total"] == round(sum(x["pnl"] for x in sold if x["pnl"] is not None), 2)
        assert sum(g["n"] for g in m["by_kind"].values()) == len(m["rows"])
    pool_run(go)


@pytest.mark.parametrize("strategy", BOTH)
def test_engine_and_one_session(strategy, pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), spot=25000)
        d = trading_days(sim.market.today, 2)
        await sim.walk(f"{d[0]} 09:15", f"{d[0]} 15:29", spot_fn=market(1))
        await sim.walk(f"{d[1]} 09:15", f"{d[1]} 11:30", spot_fn=market(2))
        s = sim.strategy
        eng = await G.engine(await ctx(sim))
        a = eng["adjusted"]
        assert {k: len(v) for k, v in a["legs"].items()} == {k: len(v) for k, v in s.legs.items()}
        assert a["adjusted_side"] == s.adjusted_side and a["rolling"] == (strategy == "intraday_dtt_advanced")
        if s.breakeven_lower is not None:
            assert a["breakeven"] == [round(s.breakeven_lower, 2), round(s.breakeven_upper, 2)]
        keys = [r["key"] for r in eng["rules"]]
        assert keys == ["entry", "force_exit", "breakeven", "profit_target", "adjustment", "reversal", "cap"]
        assert eng["next"]["text"].startswith(("Holding", "Done for today"))
        one = await G.session(await ctx(sim, d=d[0].isoformat()))
        assert one["session"]["day"] == d[0].isoformat() and one["no"] == 1 and one["next"] == d[1].isoformat()
        assert (await G.session(await ctx(sim, d="1999-01-01")))["session"] is None
        off = await G.engine(await ctx(sim, running=False))
        assert off["running"] is False and off["next"]["text"].startswith(("Paused", "Not running", "Stopped"))
    pool_run(go)
