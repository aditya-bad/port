"""Geek mode for intraday_dtt_simple (app/geek/dtt_straddle.py): the
rules read the same prices and lines the strategy exits on, the next
move says what it will do, and every straddle view adds up to the book,
on the simulated market."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.db import queries
from app.geek import dtt_straddle as G
from tests.sim import Sim
from tests.strategies.helpers import cfg
from tests.strategies.test_all_strategies_smoke import market, trading_days

S = "intraday_dtt_simple"
IST = ZoneInfo("Asia/Kolkata")


def reading(**over):
    r = {"entry_time": time(10, 0), "force_exit": time(15, 0), "profit_pct": 0.10, "stop_pct": 0.40,
         "entered_today": True, "spot": 25000.0,
         "ce": {"symbol": "NIFTY26OCT25000CE", "entry": 100.0, "now": 110.0},
         "pe": {"symbol": "NIFTY26OCT25000PE", "entry": 100.0, "now": 85.0}}
    r.update(over)
    return r


def at(hh, mm):
    return datetime(2026, 10, 20, hh, mm, tzinfo=IST)


def rule(rs, key):
    return next(r for r in rs if r["key"] == key)


# ── the rules, on readings we choose ────────────────────────────────────

def test_rules_in_the_order_the_strategy_checks_them():
    rs = G.rules(reading(), at(11, 0), True)
    assert [r["key"] for r in rs] == ["entry", "force_exit", "stop_ce", "stop_pe", "profit_target"]
    assert all(0 <= r["fill"] <= 1 for r in rs)


def test_lines_are_the_strategys_own():
    st = G.straddle_state(reading())
    assert st["legs"]["CE"]["stop"] == 140.0 and st["legs"]["PE"]["stop"] == 140.0
    assert st["legs"]["CE"]["to_stop"] == 30.0 and st["legs"]["PE"]["to_stop"] == 55.0
    c = st["combined"]
    assert (c["entry"], c["now"], c["target"], c["to_target"], c["decay_pct"]) == (200.0, 195.0, 180.0, 15.0, 2.5)
    assert G.straddle_state(reading(ce=None, pe=None)) is None
    one = G.straddle_state(reading(pe=None))
    assert one["combined"] is None and set(one["legs"]) == {"CE"}, "one leg: no combined target (the strategy skips it too)"


def test_stop_and_target_gauges():
    rs = G.rules(reading(ce={"symbol": "X", "entry": 100.0, "now": 136.0}), at(11, 0), True)
    ce = rule(rs, "stop_ce")
    assert ce["fill"] == 0.9 and ce["state"] == "armed" and ce["reading"] == "136.00 · stop 140.00 · 4.00 to go"
    assert rule(rs, "stop_pe")["fill"] == 0 and rule(rs, "stop_pe")["state"] == "ok"
    near = rule(G.rules(reading(ce={"symbol": "X", "entry": 100.0, "now": 92.0}, pe={"symbol": "Y", "entry": 100.0, "now": 90.0}), at(11, 0), True), "profit_target")
    assert near["fill"] == 0.9 and near["state"] == "armed" and "2.00 to go" in near["reading"]
    flat = G.rules(reading(ce=None, pe=None), at(11, 0), True)
    assert {rule(flat, k)["state"] for k in ("stop_ce", "stop_pe", "profit_target")} == {"idle"}


def test_entry_rule_and_next_move_agree():
    def both(r, t):
        return rule(G.rules(r, t, True), "entry"), G.next_move(r, t, True, {"text": "x"}, None)["text"]
    flat = dict(ce=None, pe=None, entered_today=False)
    e, txt = both(reading(**flat), at(9, 40))
    assert e["state"] == "ok" and e["reading"] == "in 20m" and txt == "Sells the ATM straddle at 10:00, in 20m"
    e, txt = both(reading(**flat), at(9, 55))
    assert e["state"] == "armed"
    e, txt = both(reading(), at(11, 0))
    assert e["state"] == "done" and txt.startswith("Holding the straddle (NIFTY26OCT25000…): books at combined 180.0 (now 195.0)")
    assert "stops if CE ≥ 140.0 or PE ≥ 140.0, else squares off at 15:00" in txt
    e, txt = both(reading(ce=None, pe=None), at(13, 0))
    assert txt == "Done for today. Next straddle tomorrow at 10:00"
    txt = G.next_move(reading(ce=None, pe=None), at(13, 0), True, {"text": "x"},
                      {"reason": "leg_spike_stop", "t": "2026-10-20T11:42:00+05:30"})["text"]
    assert txt == "Done for today: exited by Leg stop at 11:42. Next straddle tomorrow at 10:00"
    assert G.next_move(reading(), at(11, 0), False, {"text": "Paused"}, None)["text"] == "Paused"


# ── on the simulated market ─────────────────────────────────────────────

async def simulate(pool, monkeypatch, days=4):
    sim = await Sim.create(pool, monkeypatch, S, cfg(S), spot=25000)
    for i, d in enumerate(trading_days(sim.market.today, days)):
        await sim.walk(f"{d} 09:15", f"{d} 15:29", every_min=1, spot_fn=market(i + 1))
    return sim


async def ctx(sim, running=True, **params):
    dep = dict(await queries.get_deployment(sim.pool, sim.dep_id))
    return G.Ctx(pool=sim.pool, dep=dep, runner=sim.runner if running else None,
                 now=sim.market.now.replace(tzinfo=IST), params=params)


def test_engine_reads_the_straddle_the_strategy_holds(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, S, cfg(S), spot=25000)
        d = trading_days(sim.market.today, 1)[0]
        await sim.walk(f"{d} 09:15", f"{d} 10:20", spot_fn=market(1))
        s = sim.strategy
        eng = await G.engine(await ctx(sim))
        if s.ce_token is not None:
            st = eng["straddle"]["state"]
            assert st["legs"]["CE"]["entry"] == s.ce_entry_price and st["legs"]["PE"]["entry"] == s.pe_entry_price
            assert st["legs"]["CE"]["stop"] == round(s.ce_entry_price * (1 + s.per_leg_stop_loss_pct), 2)
            assert st["combined"]["target"] == round((s.ce_entry_price + s.pe_entry_price) * (1 - s.combined_premium_profit_pct), 2)
            assert st["legs"]["CE"]["now"] == sim.dispatcher.last_prices.get(s.ce_token)
            assert eng["next"]["text"].startswith("Holding the straddle")
        else:
            assert eng["next"]["text"].startswith("Done for today")
        assert [r["key"] for r in eng["rules"]] == ["entry", "force_exit", "stop_ce", "stop_pe", "profit_target"]
        assert rule(eng["rules"], "entry")["state"] == "done"
        off = await G.engine(await ctx(sim, running=False))
        assert off["running"] is False and off["next"]["text"].startswith(("Paused", "Not running", "Stopped"))
    pool_run(go)


def test_straddles_add_up_to_the_book(pool_run, monkeypatch):
    async def go(pool):
        sim = await simulate(pool, monkeypatch)
        body = await G.straddles(await ctx(sim))
        pos = await sim.all_positions()
        rows = body["straddles"]
        assert len(rows) == len(pos) // 2 >= 3, "one CE and one PE per straddle"
        days = [r["day"] for r in rows]
        assert len(days) == len(set(days)), "one straddle a day"
        closed = [p for p in pos if p["status"] == "closed"]
        assert body["summary"]["pnl"] == round(sum(float(p["realized_pnl"]) for p in closed), 2)
        for r in rows:
            assert r["ce"]["symbol"].endswith("CE") and r["pe"]["symbol"].endswith("PE")
            assert r["ce"]["symbol"][:-2] == r["pe"]["symbol"][:-2], "one strike"
            assert r["combined_entry"] == round(r["ce"]["entry"] + r["pe"]["entry"], 2)
            if not r["open"]:
                assert r["reason"] in G.EXITS
                assert r["held_min"] > 0 and r["exit_t"] > r["entry_t"]
                if r["reason"] == "profit_target_decay":
                    assert r["decay_pct"] >= 10 - 0.01, "booked at the target"
                if r["reason"] == "leg_spike_stop":
                    assert r["stop_leg"] in ("CE", "PE", "both")
                    leg = r[r["stop_leg"].lower()] if r["stop_leg"] != "both" else r["ce"]
                    assert leg["exit"] >= round(leg["entry"] * 1.4, 2) - 0.01, "the leg that stopped was at its line"
                if r["reason"] == "force_exit":
                    assert r["exit_t"][11:16] >= "15:00"
        n = len([r for r in rows if not r["open"]])
        assert sum(g["n"] for g in body["by_exit"].values()) == n
        assert sum(g["n"] for g in body["by_weekday"]) == n and sum(g["n"] for g in body["by_dte"]) == n
        assert sum(g["n"] for g in body["by_exit_hour"]) == n
        assert sum(body["stop_leg"].values()) == body["by_exit"].get("leg_spike_stop", {}).get("n", 0)
    pool_run(go)


def test_one_straddle_end_to_end(pool_run, monkeypatch):
    async def go(pool):
        sim = await simulate(pool, monkeypatch, days=2)
        rows = await G.straddles_of(await ctx(sim))
        newest = await G.straddle(await ctx(sim))
        assert newest["straddle"]["id"] == rows[-1]["id"] and newest["no"] == newest["n"] == len(rows)
        assert newest["prev"] == rows[-2]["id"] and newest["next"] is None
        first = await G.straddle(await ctx(sim, s=rows[0]["pe"]["id"]))
        assert first["no"] == 1, "either leg's id finds its straddle"
        s = first["straddle"]
        assert first["lines"]["target"] == round(s["combined_entry"] * 0.9, 2)
        assert first["lines"]["stops"] == {"CE": round(s["ce"]["entry"] * 1.4, 2), "PE": round(s["pe"]["entry"] * 1.4, 2)}
        assert (await G.straddle(await ctx(sim, s="nope")))["straddle"] is None
    pool_run(go)
