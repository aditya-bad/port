"""Repairing a tick stall (app/repair): the real strategy replayed on
1-minute candles through the real runner and fill engine, its plan rolled
back, applied, and undone."""

from datetime import date, datetime, time, timedelta

import pytest

from app.repair import engine
from app.repair.market import MinuteBook
from tests.repair_market import candles_source, flat, ist_minute
from tests.sim import Sim
from tests.strategies.helpers import assert_book_consistent, cfg


async def stalled_dtt(pool, monkeypatch, **over):
    """intraday_dtt_simple entered at 10:00, ticks stopping after 12:15."""
    sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple", **over))
    await sim.walk("09:15", "12:15", every_min=5)
    assert len(await sim.open_positions()) == 2
    return sim


def window(sim, start="12:16", end="15:30"):
    h, m = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    return engine.Window(sim.market.today, time(h, m), time(eh, em))


async def table_counts(pool, dep_id):
    out = {}
    for t in ("positions", "position_lots", "deployment_events", "strategy_decisions", "journal_entries"):
        out[t] = await pool.fetchval(f"SELECT count(*) FROM {t} WHERE deployment_id = $1", dep_id)
    out["cash"] = await pool.fetchval("SELECT current_cash FROM deployments WHERE id = $1", dep_id)
    out["state"] = await pool.fetchval("SELECT state::text FROM deployment_state WHERE deployment_id = $1", dep_id)
    return out


def test_plan_squares_off_intraday_legs_at_the_force_exit_minute_and_rolls_back(pool_run, monkeypatch):
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        before = await table_counts(pool, sim.dep_id)
        book = MinuteBook(sim.market.today, candles_source(sim, flat(25000)))
        out = await engine.plan(pool, sim.dep_id, window(sim), book, sim.kite, {})
        assert out.complete
        assert await table_counts(pool, sim.dep_id) == before        # a plan writes nothing
        assert {f["reason"] for f in out.fills} == {"force_exit"}
        assert {f["minute"] for f in out.fills} == {"15:00"}
        assert all(f["action"] == "buy" and f["phase"] == "open" for f in out.fills)
        # each leg closed at its own 15:00 candle's open
        for f in out.fills:
            assert f["price"] == round(book.candle(f["token"], f_minute(sim, "15:00"))["open"], 2)
        assert out.end_positions == []
        assert len(out.curve) == len(window(sim).minutes)
        s = engine.summarize(out, 500000)
        assert s["fills"] == 2 and s["open_after"] == 0 and s["reasons"] == {"force_exit": 2}
        assert s["booked"] == pytest.approx(sum(f["realized"] for f in out.fills))
    pool_run(go)


def f_minute(sim, hhmm):
    from datetime import datetime
    return datetime.combine(sim.market.today, datetime.strptime(hhmm, "%H:%M").time())


def test_apply_writes_tagged_fills_once_and_undo_restores_everything(pool_run, monkeypatch):
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        before = await table_counts(pool, sim.dep_id)
        open_before = sorted((p["symbol"], float(p["qty"])) for p in await sim.open_positions())
        book = MinuteBook(sim.market.today, candles_source(sim, flat(25000)))
        w = window(sim)
        planned = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {})
        res = await engine.apply(pool, sim.dep_id, w, book, sim.kite, planned, author="test")
        assert engine.exact(res["fills"]) == engine.exact(planned.fills)
        assert await sim.open_positions() == []
        lots = await pool.fetch(
            "SELECT * FROM position_lots WHERE deployment_id = $1 AND metadata ? 'repair' ORDER BY executed_at", sim.dep_id)
        assert len(lots) == 2
        for l in lots:
            r = l["metadata"]["repair"]
            assert r["source"] == "replay-1m" and r["from"] == "12:16" and r["minute"] == "15:00"
            assert l["executed_at"].astimezone(engine.IST).strftime("%H:%M") == "15:00"
        await assert_book_consistent(pool, sim.dep_id)
        note = await pool.fetchrow("SELECT * FROM journal_entries WHERE deployment_id = $1", sim.dep_id)
        assert "Kite down" in note["tags"] and "12:16" in note["body"]
        assert await pool.fetchval(
            "SELECT count(*) FROM deployment_events WHERE deployment_id = $1 AND event_type = 'stall_repair'", sim.dep_id) == 1

        # twice is refused, and the listing shows it repaired
        with pytest.raises(engine.AlreadyRepaired):
            await engine.apply(pool, sim.dep_id, w, book, sim.kite, planned)
        row = next(c for c in await engine.candidates(pool, w) if c["id"] == sim.dep_id)
        assert row["repaired"] is not None and row["legs"] == []

        await engine.undo(pool, res["id"])
        after = await table_counts(pool, sim.dep_id)
        assert {k: v for k, v in after.items() if k != "deployment_events"} == \
               {k: v for k, v in before.items() if k != "deployment_events"}
        # only the "undone" note is left in Activity
        kinds = [r["event_type"] for r in await pool.fetch(
            "SELECT event_type FROM deployment_events WHERE deployment_id = $1 ORDER BY created_at", sim.dep_id)]
        assert after["deployment_events"] == before["deployment_events"] + 1, kinds
        assert sorted((p["symbol"], float(p["qty"])) for p in await sim.open_positions()) == open_before
        await assert_book_consistent(pool, sim.dep_id)
        row = next(c for c in await engine.candidates(pool, w) if c["id"] == sim.dep_id)
        assert row["repaired"] is None
        # and it can be repaired again
        planned2 = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {})
        assert engine.exact(planned2.fills) == engine.exact(planned.fills)
    pool_run(go)


def test_apply_refuses_a_plan_made_before_the_deployment_changed(pool_run, monkeypatch):
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        book = MinuteBook(sim.market.today, candles_source(sim, flat(25000)))
        w = window(sim)
        planned = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {})
        await sim.tick("12:20")    # something traded / moved since the plan
        await sim.tick("15:00")
        with pytest.raises(engine.PlanChanged):
            await engine.apply(pool, sim.dep_id, w, book, sim.kite, planned)
    pool_run(go)


def test_undo_is_refused_once_the_deployment_traded_after_the_window(pool_run, monkeypatch):
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        book = MinuteBook(sim.market.today, candles_source(sim, flat(25000)))
        w = window(sim, end="14:00")
        planned = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {})
        assert planned.fills == []                 # nothing due before 15:00
        res = await engine.apply(pool, sim.dep_id, w, book, sim.kite, planned)
        await sim.restart()
        await sim.tick("15:00")                    # live trading after the window
        with pytest.raises(engine.PlanChanged, match="traded"):
            await engine.undo(pool, res["id"])
    pool_run(go)


def test_a_spike_inside_a_minute_triggers_the_stop_at_the_candles_extreme(pool_run, monkeypatch):
    """The candle's high counts (adjust with the spikes): a CE spike within
    one minute that closes back down still trips the per-leg stop, at the
    CE's high, and the PE leg is closed at its low at the same moment."""
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        spike = lambda m: (25000, 25450, 24996, 25000) if f"{m:%H:%M}" == "13:05" else (25000, 25004, 24996, 25000)
        book = MinuteBook(sim.market.today, candles_source(sim, spike))
        out = await engine.plan(pool, sim.dep_id, window(sim), book, sim.kite, {})
        assert out.complete
        stops = [f for f in out.fills if f["minute"] == "13:05"]
        assert stops and {f["reason"] for f in stops} == {"leg_spike_stop"}
        assert {f["phase"] for f in stops} == {"up"}
        m = f_minute(sim, "13:05")
        for f in stops:
            c = book.candle(f["token"], m)
            want = c["high"] if f["symbol"].endswith("CE") else c["low"]
            assert f["price"] == round(want, 2)
        assert not [f for f in out.fills if f["minute"] > "13:05"]    # no re-entry
        assert engine.summarize(out)["spikes"] == len(stops)
    pool_run(go)


def both_ways(at="13:05"):
    """One minute that runs 450 points up AND 450 down: either leg's stop
    could have fired first."""
    return lambda m: (25000, 25450, 24550, 25000) if f"{m:%H:%M}" == at else (25000, 25004, 24996, 25000)


def test_a_minute_where_the_order_decides_the_trade_stops_and_shows_both_paths(pool_run, monkeypatch):
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        before = await table_counts(pool, sim.dep_id)
        book = MinuteBook(sim.market.today, candles_source(sim, both_ways()))
        out = await engine.plan(pool, sim.dep_id, window(sim), book, sim.kite, {})
        assert not out.complete and out.ambiguity.minute == "13:05"
        a = out.ambiguity
        assert set(a.paths) == {"up_first", "down_first"} and a.natural == "down_first"
        up, down = a.paths["up_first"], a.paths["down_first"]
        assert {f["phase"] for f in up["fills"]} == {"up"} and {f["phase"] for f in down["fills"]} == {"down"}
        assert {f["reason"] for f in up["fills"] + down["fills"]} == {"leg_spike_stop"}
        assert up["pnl"] != down["pnl"]
        assert a.candles[0]["high"] == 25450 and a.candles[0]["low"] == 24550    # the index first
        assert {c["label"][-2:] for c in a.candles[2:]} == {"CE", "PE"}
        assert await table_counts(pool, sim.dep_id) == before

        # each choice is followed from there on
        for order, phase in (("up_first", "up"), ("down_first", "down")):
            done = await engine.plan(pool, sim.dep_id, window(sim), book, sim.kite, {"13:05": order})
            assert done.complete and done.choices == {"13:05": order}
            assert [f["phase"] for f in done.fills] == [phase, phase]
            assert engine.summarize(done)["pnl"] == a.paths[order]["pnl"]
    pool_run(go)


def test_a_scan_settles_ambiguous_minutes_by_the_usual_reading_and_lists_them(pool_run, monkeypatch):
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        book = MinuteBook(sim.market.today, candles_source(sim, both_ways()))
        out = await engine.plan(pool, sim.dep_id, window(sim), book, sim.kite, {}, auto=True)
        assert out.complete
        assert [a["minute"] for a in out.auto] == ["13:05"] and out.auto[0]["picked"] == "down_first"
        assert {f["phase"] for f in out.fills} == {"down"}
    pool_run(go)


def test_the_same_trade_at_the_same_moment_either_way_is_not_ambiguous(pool_run, monkeypatch):
    """Only the up-move matters (the stop is on the CE): both orders take it
    at the CE's high, so there's nothing to choose."""
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        book = MinuteBook(sim.market.today, candles_source(
            sim, lambda m: (25000, 25450, 24990, 25000) if f"{m:%H:%M}" == "13:05" else (25000, 25004, 24996, 25000)))
        out = await engine.plan(pool, sim.dep_id, window(sim), book, sim.kite, {})
        assert out.complete and out.auto == []
    pool_run(go)


import math  # noqa: E402

from app.strategies.registry import list_strategies  # noqa: E402

POSITIONAL = {"strangle_monthly_v2", "calendar_btst", "trend_credit_spread", "weekly_ema_st_spread"}


def wave(t):
    m = (t.hour * 60 + t.minute) - 555
    return 25000 + 250 * math.sin(m / 60.0)


def wave_candles(m):
    from datetime import timedelta
    o, c = wave(m), wave(m + timedelta(minutes=1))
    return (o, max(o, c) + 6, min(o, c) - 6, c)


@pytest.mark.parametrize("strategy", sorted(s["name"] for s in list_strategies()))
def test_every_strategy_replays_applies_and_keeps_a_consistent_book(strategy, pool_run, monkeypatch):
    async def go(pool):
        mode = "positional" if strategy in POSITIONAL else "intraday"
        sim = await Sim.create(pool, monkeypatch, strategy, cfg(strategy), mode=mode, spot=25000)
        await sim.walk("09:15", "12:15", every_min=1, spot_fn=wave)
        w = window(sim)
        book = MinuteBook(sim.market.today, candles_source(sim, wave_candles))
        planned = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {}, auto=True)
        assert planned.complete and planned.problems == [], planned.problems
        choices = {a["minute"]: a["picked"] for a in planned.auto}
        planned = await engine.plan(pool, sim.dep_id, w, book, sim.kite, choices)
        assert planned.complete
        res = await engine.apply(pool, sim.dep_id, w, book, sim.kite, planned)
        assert engine.exact(res["fills"]) == engine.exact(planned.fills)
        await assert_book_consistent(pool, sim.dep_id)
        if mode == "intraday" and sim.runner.config.get("force_exit_time"):
            assert await sim.open_positions() == [], "an intraday deployment must be flat after a repaired close"
        assert all(f["at"][:10] == str(sim.market.today) and "12:16" <= f["minute"] < "15:30" for f in res["fills"])
    pool_run(go)


def test_strangle_two_way_minute_adjusts_differently_each_way_and_the_choice_carries_on(pool_run, monkeypatch):
    """The strangle case: a minute that swings 600 points both ways trips
    the 50% adjustment on BOTH sides, into different strikes depending on
    which way went first. That's for the trader to choose; once chosen the
    replay carries on from that path."""
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "strangle_monthly_v2", cfg("strangle_monthly_v2"),
                               mode="positional", spot=25000)
        await sim.walk("09:15", "12:15", every_min=5)
        assert len(await sim.open_positions()) == 2
        path = lambda m: (25000, 25600, 24400, 25000) if f"{m:%H:%M}" == "13:05" else (25000, 25004, 24996, 25000)
        book = MinuteBook(sim.market.today, candles_source(sim, path))
        w = window(sim)
        out = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {})
        assert out.ambiguity and out.ambiguity.minute == "13:05"
        sells = {k: {f["symbol"] for f in v["fills"] if f["action"] == "sell"} for k, v in out.ambiguity.paths.items()}
        assert sells["up_first"] != sells["down_first"]
        assert all(f["reason"] == "continuous_50pct_trigger" for v in out.ambiguity.paths.values() for f in v["fills"])
        done = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {"13:05": "up_first"})
        assert done.complete
        assert {f["symbol"] for f in done.fills if f["action"] == "sell" and f["minute"] == "13:05"} == sells["up_first"]
        held = {p["symbol"] for p in done.end_positions}
        assert held and held <= {f["symbol"] for f in done.fills if f["action"] == "sell"}
        res = await engine.apply(pool, sim.dep_id, w, book, sim.kite, done)
        await assert_book_consistent(pool, sim.dep_id)
        assert {p["symbol"] for p in await sim.open_positions()} == held
        # the new legs' candles are stored, as for anything the app held
        for p in await sim.open_positions():
            n = await pool.fetchval("SELECT count(*) FROM candles_1m WHERE instrument_token = $1", int(p["instrument_token"]))
            assert n == len(book.load(int(p["instrument_token"])))
            assert await pool.fetchval("SELECT tradingsymbol FROM contracts WHERE instrument_token = $1",
                                       int(p["instrument_token"])) == p["symbol"]
        # the strategy resumes from the repaired book after a restart
        await sim.restart()
        assert {l["symbol"] for side in sim.strategy.legs.values() for l in side} == held
        await engine.undo(pool, res["id"])
        assert len(await sim.open_positions()) == 2
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_what_a_repair_touched_is_marked_everywhere_and_unmarked_on_undo(pool_run, monkeypatch):
    """After a repair: the trade (v2 book), the straddle (Geek), and the
    replay's episode, its repaired events and legs are marked; an undo
    takes every mark away with the fills."""
    from app.geek import common, dtt_straddle, replay as RP
    from app.v2 import book as B
    from app.db import queries

    async def marks(pool, sim):
        trades = [t for t in await B.list_trades(pool) if t["deployment_id"] == str(sim.dep_id)]
        dep = dict(await queries.get_deployment(pool, sim.dep_id))
        ctx = common.Ctx(pool=pool, dep=dep, now=None)
        straddles = await dtt_straddle.straddles_of(ctx)
        rp = await RP.replay(common.Ctx(pool=pool, dep=dep, now=None))
        return trades, straddles, rp

    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        book = MinuteBook(sim.market.today, candles_source(sim, flat(25000)))
        w = window(sim)
        planned = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {})
        res = await engine.apply(pool, sim.dep_id, w, book, sim.kite, planned)
        trades, straddles, rp = await marks(pool, sim)
        assert [t["repaired"] for t in trades] == [True]
        assert [s["repaired"] for s in straddles] == [True]
        assert rp["episodes"][-1]["repaired"] is True
        ev = {e["kind"]: e["repaired"] for e in rp["events"]}
        assert ev == {"entry": False, "exit": True}, "the live entry isn't marked, the repaired exit is"
        assert all(l["repaired"] for l in rp["legs"])
        await engine.undo(pool, res["id"])
        trades, straddles, rp = await marks(pool, sim)
        assert trades == [] and [s["repaired"] for s in straddles] == [False]
        assert not rp["episodes"][-1]["repaired"] and not any(e["repaired"] for e in rp["events"])
    pool_run(go)


def test_legs_with_no_prices_make_the_plan_untrustworthy_and_it_cant_be_applied(pool_run, monkeypatch):
    """The Bankex case: Kite had nothing for the held legs (one empty, one
    an error) and the app stored none. The strategy saw nothing, so its
    "nothing to do" means nothing: the plan names the blind legs with
    Kite's reason, and apply refuses it."""
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        legs = await sim.open_positions()
        ce = next(int(p["instrument_token"]) for p in legs if p["symbol"].endswith("CE"))
        pe = next(int(p["instrument_token"]) for p in legs if p["symbol"].endswith("PE"))
        good = candles_source(sim, flat(25000))

        def source(token, start, end):
            if token == ce:
                return []
            if token == pe:
                raise Exception("Invalid `token`")
            return good(token, start, end)
        book = MinuteBook(sim.market.today, source)
        monkeypatch.setattr(MinuteBook, "anchor", lambda *a: None)   # and no earlier price to model them from
        w = window(sim)
        out = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {})
        assert out.complete and sorted(s[-2:] for s in out.blind) == ["CE", "PE"]
        notes = " ".join(out.notes)
        assert "Kite returned no 1-minute candles" in notes and "Kite said “Exception: Invalid `token`”" in notes
        assert f"(asked for token {ce})" in notes and "no earlier price to model it from" in notes
        assert out.index_name == "NIFTY 50"
        assert engine.summarize(out)["blind"] == out.blind
        with pytest.raises(engine.PlanChanged, match="no prices"):
            await engine.apply(pool, sim.dep_id, w, book, sim.kite, out)
        assert await engine.live_repair(pool, sim.dep_id, w.day) is None
    pool_run(go)


def test_legs_kite_never_traded_are_priced_by_model_from_their_fills_and_the_plan_applies(pool_run, monkeypatch):
    """The Bankex case with its fills: Kite has no trade in the held legs
    all day, so each is priced off the index from what it filled at. The
    strategy sees them move, the plan says they're estimated, and it
    applies like any other."""
    async def go(pool):
        sim = await stalled_dtt(pool, monkeypatch)
        legs = await sim.open_positions()
        toks = {int(p["instrument_token"]) for p in legs}
        # the index runs 300 points up through the afternoon: the short CE's stop has to fire
        good = candles_source(sim, lambda m: (25000 + max(0, (m.hour * 60 + m.minute) - 13 * 60) * 3,) * 4)
        book = MinuteBook(sim.market.today, lambda t, a, b: [] if t in toks else good(t, a, b))
        w = window(sim)
        out = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {}, auto=True)
        assert out.complete and not out.blind
        assert sorted(s[-2:] for s in out.estimated) == ["CE", "PE"]
        assert all(book.origin[t] == "model" for t in toks)
        notes = " ".join(out.notes)
        assert "priced by model — Black-76 off NIFTY 50" in notes and "from the fill at" in notes
        assert engine.summarize(out)["estimated"] == out.estimated
        # the model moved with the index: the CE rose, the PE fell
        ce = next(int(p["instrument_token"]) for p in legs if p["symbol"].endswith("CE"))
        pe = next(int(p["instrument_token"]) for p in legs if p["symbol"].endswith("PE"))
        early, late = ist_minute(sim, "12:30"), ist_minute(sim, "14:30")
        assert book.at(ce, late, "close") > book.at(ce, early, "close")
        assert book.at(pe, late, "close") < book.at(pe, early, "close")
        assert out.fills, "a 300-point run should move the strategy"
        rep = await engine.apply(pool, sim.dep_id, w, book, sim.kite, out)
        assert rep and await engine.live_repair(pool, sim.dep_id, w.day) is not None
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_a_sensex_strangle_where_nothing_on_bfo_trades_still_replays_and_adjusts(pool_run, monkeypatch):
    """The BFO case end to end: Kite has no trade today in any SENSEX
    contract (legs, new strikes, the future the strangle prices its ATM
    on), only their last trades the day before. Options are priced off
    the index, the future at its last trade, the 3% swing adjusts into new strikes, and it applies.
    (The future's own ltp is in test_repair_market.)"""
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "strangle_monthly_v2",
                               cfg("strangle_monthly_v2", instrument="SENSEX", instrument_tokens=[265]),
                               mode="positional", underlying="SENSEX", spot=82000)
        await sim.walk("09:15", "12:15", every_min=5)
        assert len(await sim.open_positions()) == 2
        today = sim.market.today
        prev = datetime.combine(today - timedelta(days=1), time(15, 29))
        path = lambda m: (82000, 84460, 79540, 82000) if f"{m:%H:%M}" == "13:05" else (82000, 82008, 81992, 82000)
        good = candles_source(sim, lambda m: (82000,) * 4 if m.date() < today else path(m))
        asked = set()

        def source(token, start, end):
            if token == 265:
                return good(token, start, end)
            asked.add(token)
            return good(token, prev, prev + timedelta(minutes=1)) if start.date() < today else []
        book = MinuteBook(today, source)
        w = window(sim)
        out = await engine.plan(pool, sim.dep_id, w, book, sim.kite, {"13:05": "up_first"}, auto=True)
        assert out.complete and not out.blind and not out.problems
        held = {int(p["instrument_token"]) for p in await sim.open_positions()}
        assert all(book.origin[t] == "model" for t in held)
        # a future isn't modelled: it's at its last trade on Kite
        assert all(book.origin[t] == "stale" and book.before[t] for t, s in book.symbols.items() if s.endswith("FUT"))
        new = {f["token"] for f in out.fills if f["action"] == "sell"}
        assert new and new - held and all(book.origin[t] == "model" for t in new)
        notes = " ".join(out.notes)
        assert "Black-76 off SENSEX" in notes
        await engine.apply(pool, sim.dep_id, w, book, sim.kite, out)
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)
