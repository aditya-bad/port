"""Geek mode for strangle_monthly_v2 (app/geek/strangle_v2.py), on the
simulated market: the live engine readings must agree with what the
strategy then actually does, and every history view must add up to the
book."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import queries
from app.geek import strangle_v2 as G
from tests.sim import Sim, find_spot, leg_prices, side_sum
from tests.strategies.helpers import cfg

S = "strangle_monthly_v2"
IST = ZoneInfo("Asia/Kolkata")


async def start(pool, monkeypatch, **over):
    sim = await Sim.create(pool, monkeypatch, S, cfg(S, **over), mode="positional")
    await sim.tick("10:00", spot=25000)
    await sim.tick("10:01", spot=25000)    # the new legs' first prices
    return sim


async def ctx(sim, running=True):
    dep = dict(await queries.get_deployment(sim.pool, sim.dep_id))
    return G.Ctx(pool=sim.pool, dep=dep, runner=sim.runner if running else None,
                 now=sim.market.now.replace(tzinfo=IST))


def rule(eng, key):
    return next(r for r in eng["rules"] if r["key"] == key)


def test_engine_lists_the_rules_in_the_order_the_strategy_checks_them(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        eng = await G.engine(await ctx(sim))
        assert eng["running"] is True
        assert [r["key"] for r in eng["rules"]] == [
            "contract_expiry_backstop", "checkpoint_target", "convergence_stop", "eod_gap_check",
            "continuous_50pct_trigger"]
        assert rule(eng, "checkpoint_target")["target"] == 0.005 * 500000
        assert rule(eng, "contract_expiry_backstop")["reading"] == "41 days left"   # 14 Oct -> 24 Nov
        assert len(eng["legs"]) == 2 and all(l["ltp"] is not None for l in eng["legs"])
        k = eng["strikes"]
        assert k["upper_be"] > k["ce"] > 25000 > k["pe"] > k["lower_be"]
        assert eng["next"]["text"].startswith("Nothing due")
    pool_run(go)


def test_engine_checkpoint_reading_is_the_cycle_profit(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        await sim.tick("11:00", spot=25100)
        prices = await leg_prices(sim)
        open_pnl = sum((float(p["avg_entry_price"]) - prices[p["symbol"]]) * float(p["qty"])
                       for p in await sim.open_positions())
        eng = await G.engine(await ctx(sim))
        assert abs(rule(eng, "checkpoint_target")["value"] - open_pnl) < 0.01
    pool_run(go)


def test_engine_checkpoint_in_loss_is_behind_not_a_negative_percentage(pool_run, monkeypatch):
    """A cycle in loss read "-357% there" in green, as if on track."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        await sim.tick("11:00", spot=25150)
        eng = await G.engine(await ctx(sim))
        ck = rule(eng, "checkpoint_target")
        assert ck["value"] < 0, "the move put the short strangle in loss"
        assert ck["state"] == "behind" and ck["pct"] is None
        assert "% there" not in eng["next"]["text"]
        assert "short of the checkpoint" in eng["next"]["text"]
    pool_run(go)


def test_checkpoint_progress_words():
    assert G._checkpoint_progress(-2675, 750) == "cycle is −₹2,675, ₹3,425 short of the checkpoint"
    assert G._checkpoint_progress(375, 750) == "checkpoint 50% there"
    assert G._checkpoint_progress(0, 750) == "checkpoint 0% there"
    assert G._checkpoint_progress(100, 0) == "no checkpoint set"


def test_engine_forecasts_the_eod_roll_and_the_strategy_then_makes_it(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        mid = await find_spot(sim, lambda p: 0.58 <= side_sum(p, "PE") / side_sum(p, "CE") <= 0.75, step=10)
        await sim.tick("14:00", spot=mid)
        eng = await G.engine(await ctx(sim))
        eod = rule(eng, "eod_gap_check")
        assert eod["state"] == "armed" and eod["reading"].endswith("pts short")
        assert rule(eng, "continuous_50pct_trigger")["state"] == "idle"
        assert eng["next"]["text"].startswith("At 15:13 the EOD check will roll the PE side up")
        assert eng["next"]["at"] == "15:13"
        await sim.tick("15:13", spot=mid)
        assert {f["reason"] for f in await sim.fills() if f["action"] == "buy"} <= {"eod_gap_check", "eod_rollout"}
        eng = await G.engine(await ctx(sim))
        assert eng["rules"] == [] and eng["next"]["text"] == "Waiting for a live price on every leg"
        await sim.tick("15:14", spot=mid)
        eng = await G.engine(await ctx(sim))
        assert rule(eng, "eod_gap_check")["state"] == "done"
    pool_run(go)


def test_engine_continuous_reading_matches_the_leg_prices(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        await sim.tick("11:00", spot=25150)
        p = await leg_prices(sim)
        ce, pe = side_sum(p, "CE"), side_sum(p, "PE")
        small, big = min(ce, pe), max(ce, pe)
        eng = await G.engine(await ctx(sim))
        r = rule(eng, "continuous_50pct_trigger")
        assert abs(r["ratio"] - small / big) < 1e-3
        assert f"can lose {small - 0.5 * big:.1f} more" in r["reading"]
    pool_run(go)


def test_engine_without_a_running_strategy_shows_the_legs_and_says_why(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        await queries.set_status(pool, sim.dep_id, "paused")
        eng = await G.engine(await ctx(sim, running=False))
        assert eng["running"] is False and eng["rules"] == []
        assert len(eng["legs"]) == 2
        assert eng["next"]["text"].startswith("Paused")
    pool_run(go)


def test_decisions_explain_trades_and_checks(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.48 * side_sum(p, "CE"), step=25)
        await sim.tick("11:00", spot=up)
        await sim.tick("15:13", spot=up)
        d = await G.decisions(await ctx(sim))
        rows = list(reversed(d["rows"]))     # oldest first
        assert [r["kind"] for r in rows] == ["entry", "roll", "check"]
        assert rows[0]["summary"].startswith("Sold strangle: CE ") and "long-days 5% of capital" in rows[0]["why"]
        assert rows[1]["summary"].startswith("Rolled PE in: closed PE ") and "sold PE " in rows[1]["summary"]
        assert rows[1]["why"].startswith("PE/CE 0.") and "≤ 0.50" in rows[1]["why"]
        assert rows[2]["rule"] == "eod_gap_check" and rows[2]["outcome"] == "no_action"
        assert all(r["cycle_id"] == 1 for r in rows)
        assert d["counts"] == {"acted": 2, "no_action": 1, "blocked": 0}
    pool_run(go)


def test_cycles_split_by_checkpoint_and_add_up_to_the_book(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        sim.market.vol = 0.13
        await sim.tick("11:00", spot=25000)
        c = await G.cycles(await ctx(sim))
        rows = sorted(c["rows"], key=lambda r: r["cycle_id"])
        assert [(r["cycle_id"], r["status"], r["ended_by"]) for r in rows] == [
            (1, "closed", "checkpoint_target"), (2, "open", "open")]
        realized = sum(float(p["realized_pnl"]) for p in await sim.all_positions()
                       if p["status"] == "closed" and int(p["metadata"]["cycle_id"]) == 1)
        assert abs(rows[0]["pnl"] - realized) < 0.01 and rows[0]["pnl"] >= c["checkpoint_target"] * 0.9
        assert c["summary"]["ended_by"] == {"checkpoint_target": 1}
        assert c["summary"]["median_days_to_checkpoint"] is not None
    pool_run(go)


def test_cycle_worst_dip_and_best_peak_come_from_the_price_marks(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        legs = await sim.open_positions()
        base = legs[0]["opened_at"]
        # Marks: both legs up 20 (a loss), then down 30 (a profit).
        marks = []
        for p in legs:
            e = float(p["avg_entry_price"])
            marks += [(p["id"], sim.dep_id, base + timedelta(minutes=5), e + 20),
                      (p["id"], sim.dep_id, base + timedelta(minutes=10), e - 30)]
        await queries.record_position_marks(pool, marks)
        await sim.tick("10:20", spot=25000)
        c = await G.cycles(await ctx(sim))
        row = c["rows"][0]
        qty = float(legs[0]["qty"])
        assert abs(row["mae"] - (-20 * qty * 2)) < 0.01
        assert abs(row["mfe"] - (30 * qty * 2)) < 0.01
    pool_run(go)


def test_adjustment_is_scored_against_holding_the_closed_leg(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        old_pe = next(p for p in await sim.open_positions() if p["symbol"].endswith("PE"))
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.48 * side_sum(p, "CE"), step=25)
        await sim.tick("11:00", spot=up)
        await sim.tick("12:00", spot=up)
        c = await ctx(sim)
        a = await G.adjustments(c)
        assert len(a["rows"]) == 1
        row = a["rows"][0]
        assert row["rule"] == "continuous" and row["ratio_before"] <= 0.5
        assert 0.75 <= row["ratio_after"] <= 1.0, row["ratio_after"]
        assert row["if_held"] is None and row["note"], "no stored candles for the closed leg yet"
        # Store a candle for the closed leg at "now": the roll gets scored.
        tok = int(old_pe["instrument_token"])
        held_px = 50.0
        minute = sim.market.now.replace(tzinfo=IST, second=0) - timedelta(minutes=1)
        await queries.upsert_candles(pool, [(tok, minute, held_px, held_px, held_px, held_px, None)], "kite")
        a = await G.adjustments(await ctx(sim))
        row = a["rows"][0]
        qty = float(old_pe["qty"])
        assert abs(row["if_held"] - (float(old_pe["avg_entry_price"]) - held_px) * qty) < 0.01
        assert row["roll_paid"] == round(row["roll_pnl"] - row["if_held"], 2), "new leg still open: no costs yet"
        assert a["by_rule"]["continuous"]["rolls"] == 1 and a["summary"]["scored"] == 1
    pool_run(go)


def test_entries_show_the_expiry_rule_and_the_premium_target(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        e = await G.entries(await ctx(sim))
        row = e["rows"][0]
        assert row["trigger"] == "initial_entry" and row["rotation"] == "NEXT_MONTH"
        assert row["long_days"] is True and row["capital_pct"] == 0.05
        assert abs(row["target"] - 500000 * 0.05 / 75 / 2) < 0.1
        assert [l["opt"] for l in row["legs"]] == ["CE", "PE"]
        assert row["contract_expiry"] == "2026-11-24"
    pool_run(go)


def test_premium_sold_and_side_series(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        legs = await sim.open_positions()
        sold = sum(float(p["avg_entry_price"]) * float(p["qty"]) for p in legs)
        base = legs[0]["opened_at"]
        await queries.record_position_marks(pool, [
            (p["id"], sim.dep_id, base + timedelta(minutes=5), float(p["avg_entry_price"]) - 1) for p in legs])
        await sim.tick("10:20", spot=25000)
        pr = await G.premium(await ctx(sim))
        assert abs(pr["premium_sold"] - sold) < 0.01
        assert pr["open_cycle"] == 1 and len(pr["series"]) == 1
        pt = pr["series"][0]
        assert 0 < pt["ratio"] <= 1 and pt["ce"] > 0 and pt["pe"] > 0
    pool_run(go)


def test_settings_mark_changes_from_default_and_count_what_they_did(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch, eod_gap_floor=0.7)
        await sim.tick("15:13", spot=25000)
        st = await G.settings(await ctx(sim))
        by = {r["key"]: r for r in st["rows"]}
        assert by["eod_gap_floor"]["changed"] is True and by["eod_gap_floor"]["default"] == 0.8
        assert by["eod_gap_floor"]["value"] == 0.7
        assert by["adjustment_trigger_ratio"]["changed"] is False
        assert by["eod_gap_floor"]["effect"] == "rolled 0 time(s) · nothing to do 1 time(s)"
        assert by["long_days_target_percentage"]["effect"] == "used on 1 entry"
        assert "instrument_tokens" not in by
    pool_run(go)


def test_a_rolled_away_legs_missing_prices_are_filled_from_kite_once(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        old_pe = next(p for p in await sim.open_positions() if p["symbol"].endswith("PE"))
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.48 * side_sum(p, "CE"), step=25)
        await sim.tick("11:00", spot=up)
        await sim.tick("12:00", spot=up)
        calls = []

        class FakeCandles:
            async def ensure(self, token, start, end, symbol=None):
                calls.append((token, symbol))
                await queries.upsert_candles(pool, [(token, end - timedelta(minutes=2), 40, 40, 40, 40, None)], "kite")
                return 1
        c = await ctx(sim)
        c.candles = FakeCandles()
        row = (await G.adjustments(c))["rows"][0]
        assert calls == [(int(old_pe["instrument_token"]), old_pe["symbol"])]
        assert abs(row["if_held"] - (float(old_pe["avg_entry_price"]) - 40) * float(old_pe["qty"])) < 0.01
        c = await ctx(sim)
        c.candles = FakeCandles()
        await G.adjustments(c)
        assert len(calls) == 1, "stored: not fetched again"

        class NoKite:
            async def ensure(self, *a, **k):
                raise RuntimeError("no Kite session")
        await pool.execute("DELETE FROM candles_1m")
        c = await ctx(sim)
        c.candles = NoKite()
        row = (await G.adjustments(c))["rows"][0]
        assert row["if_held"] is None and row["note"]
    pool_run(go)


def test_engine_says_the_eod_check_is_due_once_its_time_has_passed(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        mid = await find_spot(sim, lambda p: 0.58 <= side_sum(p, "PE") / side_sum(p, "CE") <= 0.75, step=10)
        await sim.tick("14:00", spot=mid)
        c = await ctx(sim)
        c.now = c.now.replace(hour=15, minute=14)     # past 15:13, no tick since
        eng = await G.engine(c)
        assert eng["next"]["text"].startswith("The EOD check is due: it rolls the PE side up on the next tick")
        assert eng["next"]["at"] is None
    pool_run(go)


def test_engine_says_when_the_market_is_closed(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        mid = await find_spot(sim, lambda p: 0.58 <= side_sum(p, "PE") / side_sum(p, "CE") <= 0.75, step=10)
        await sim.tick("14:00", spot=mid)
        c = await ctx(sim)
        c.now = c.now.replace(hour=20, minute=0)
        eng = await G.engine(c)
        assert eng["next"]["text"].startswith("Market closed. At 15:13 next session the EOD check rolls the PE side up")
    pool_run(go)


def test_a_cycle_never_in_the_red_has_no_worst_dip(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        legs = await sim.open_positions()
        base = legs[0]["opened_at"]
        await queries.record_position_marks(pool, [
            (p["id"], sim.dep_id, base + timedelta(minutes=5), float(p["avg_entry_price"]) - 5) for p in legs])
        await sim.tick("10:20", spot=25000)
        row = (await G.cycles(await ctx(sim)))["rows"][0]
        assert row["mae"] == 0 and row["mfe"] > 0
    pool_run(go)


def test_side_series_pairs_marks_written_seconds_apart(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        legs = await sim.open_positions()
        base = legs[0]["opened_at"].replace(second=0, microsecond=0) + timedelta(minutes=5)
        await queries.record_position_marks(pool, [
            (p["id"], sim.dep_id, base + timedelta(seconds=i * 3), float(p["avg_entry_price"])) for i, p in enumerate(legs)])
        await sim.tick("10:20", spot=25000)
        assert len((await G.premium(await ctx(sim)))["series"]) == 1
    pool_run(go)


# ── one cycle, end to end ───────────────────────────────────────────────

def test_cycle_page_has_everything_about_one_cycle(pool_run, monkeypatch):
    """Entry, legs, the roll scored, every action and check in order, the
    P&L path, the side balance and the money: all for that cycle only."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        legs = await sim.open_positions()
        base = legs[0]["opened_at"]
        await queries.record_position_marks(pool, [
            (p["id"], sim.dep_id, base + timedelta(minutes=5), float(p["avg_entry_price"]) - 2) for p in legs])
        up = await find_spot(sim, lambda p: side_sum(p, "PE") <= 0.48 * side_sum(p, "CE"), step=25)
        await sim.tick("11:00", spot=up)
        await sim.tick("15:13", spot=up)
        c = await ctx(sim)
        c.params = {"c": "1"}
        cy = await G.cycle(c)
        assert cy["found"] and cy["cycle_id"] == 1 and cy["ids"] == [1]
        assert cy["prev"] is None and cy["next"] is None
        assert cy["row"]["status"] == "open" and cy["row"]["rolls"] == 1
        # every leg: 2 entered, the rolled-away PE closed by the continuous rule, the new PE open
        assert len(cy["legs"]) == 3
        closed = [l for l in cy["legs"] if l["status"] == "closed"]
        assert len(closed) == 1 and closed[0]["opt"] == "PE" and closed[0]["closed_by"] == "continuous_50pct_trigger"
        assert closed[0]["pnl"] is not None
        # the timeline, oldest first: entry, roll, then the EOD check that did nothing
        assert [(x["kind"], x["outcome"]) for x in cy["timeline"]] == [("entry", "acted"), ("roll", "acted"), ("check", "no_action")]
        assert cy["timeline"] == sorted(cy["timeline"], key=lambda x: x["t"])
        assert cy["entry"]["cycle_id"] == 1 and [l["opt"] for l in cy["entry"]["legs"]] == ["CE", "PE"]
        assert len(cy["rolls"]) == 1 and cy["rolls"][0]["side"] == "PE" and cy["rolls"][0]["direction"] == "in"
        assert cy["path"] and all(set(p) == {"t", "pnl"} for p in cy["path"])
        assert cy["path_kind"] == "marks" and cy["path"][0]["pnl"] == 0 and cy["path_gaps"] == []
        assert cy["side"] and 0 < cy["side"][0]["ratio"] <= 1
        lots = await queries.list_deployment_lots(pool, sim.dep_id)
        sold = sum(float(l["price"]) * float(l["qty"]) for l in lots if l["position_side"] == "short" and l["action"] == "sell")
        assert abs(cy["money"]["premium_sold"] - sold) < 0.01
        assert cy["money"]["open"] is not None
        assert cy["rules"] == {"trigger": 0.5, "floor": 0.8, "band": [0.8, 0.95]}
        # every cycle's row and journey, to set this one against the others
        assert [r["cycle_id"] for r in cy["cycles"]] == [1] and cy["cycles"][0]["path"]
    pool_run(go)


def test_cycle_page_defaults_to_the_newest_and_knows_its_neighbours(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        sim.market.vol = 0.13
        await sim.tick("11:00", spot=25000)       # cycle 1 hits its checkpoint, cycle 2 starts
        cy = await G.cycle(await ctx(sim))
        assert cy["cycle_id"] == 2 and cy["ids"] == [1, 2] and cy["prev"] == 1 and cy["next"] is None
        c = await ctx(sim)
        c.params = {"c": "1"}
        one = await G.cycle(c)
        assert one["row"]["ended_by"] == "checkpoint_target" and one["next"] == 2
        assert one["timeline"][-1]["kind"] == "exit" and "≥" in one["timeline"][-1]["why"]
        assert all(l["status"] == "closed" for l in one["legs"])
        assert one["money"]["open"] is None
        assert one["money"]["net"] == round(one["money"]["gross"] - one["money"]["charges"] - one["money"]["slippage"], 2)
        for bad in ("9", "x"):
            c = await ctx(sim)
            c.params = {"c": bad}
            assert (await G.cycle(c))["found"] is False
    pool_run(go)


def test_cycles_rows_carry_their_pnl_path_and_rolls_their_direction(pool_run, monkeypatch):
    async def go(pool):
        sim = await start(pool, monkeypatch)
        legs = await sim.open_positions()
        base = legs[0]["opened_at"]
        await queries.record_position_marks(pool, [
            (p["id"], sim.dep_id, base + timedelta(minutes=m), float(p["avg_entry_price"]) - m / 5)
            for p in legs for m in (5, 10, 15)])
        await sim.tick("10:30", spot=25000)
        row = (await G.cycles(await ctx(sim)))["rows"][0]
        assert row["path_kind"] == "marks" and row["path_gaps"] == []
        assert len(row["path"]) == 4 and row["path"][0] == [0, 0], "it opened at 0, then the 3 marks"
        hrs = [p[0] for p in row["path"]]
        assert hrs == sorted(hrs) and hrs[1] > 0
        assert row["path"][-1][1] > row["path"][1][1], "premium decayed: P&L rose"
    pool_run(go)


def test_cycles_from_before_the_marks_still_have_a_journey(pool_run, monkeypatch):
    """Old cycles have no 5-minute marks: their path comes from the stored
    1-minute candles, or, with none, from what each leg booked."""
    async def go(pool):
        sim = await start(pool, monkeypatch)
        sim.market.vol = 0.13
        await sim.tick("11:00", spot=25000)       # cycle 1 hits its checkpoint, cycle 2 starts
        rows = {r["cycle_id"]: r for r in (await G.cycles(await ctx(sim)))["rows"]}
        one = rows[1]
        assert one["path_kind"] == "booked" and one["path_gaps"] == []
        assert one["path"][0] == [0, 0] and len(one["path"]) >= 2, "drawn, not dropped"
        assert one["path"][-1][1] == one["realized_pnl"], "ends at its result"
        assert one["mae"] is None and one["mfe"] is None, "no prices: no dip or peak to claim"

        # now candles for cycle 1's legs, one every minute, decaying
        prs = [p for p in await queries.list_positions(pool, sim.dep_id) if (p["metadata"] or {}).get("cycle_id") == 1]
        t0 = min(p["opened_at"] for p in prs)
        rows_c = []
        for p in prs:
            entry = float(p["avg_entry_price"])
            for m in range(0, 60):
                rows_c.append((int(p["instrument_token"]), t0 + timedelta(minutes=m), entry, entry, entry, entry - m / 20, None))
        await queries.upsert_candles(pool, rows_c, "kite")
        c = await ctx(sim)
        one = {r["cycle_id"]: r for r in (await G.cycles(c))["rows"]}[1]
        assert one["path_kind"] == "candles"
        assert len(one["path"]) > 3 and one["path"][0] == [0, 0]
        assert one["mae"] is not None and one["mfe"] > 0, "candles show the move"
        c.params = {"c": "1"}
        cy = await G.cycle(c)
        assert cy["path_kind"] == "candles" and len(cy["path"]) > 3
        assert len(cy["side"]) >= 2 and all(0 < x["ratio"] <= 1 for x in cy["side"]), "side balance from the candles too"
    pool_run(go)


def test_leg_prices_use_candles_only_before_the_marks():
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=IST)
    leg = {"id": "a", "instrument_token": 7, "opened_at": t0, "closed_at": None}
    candles = {7: [(t0 + timedelta(minutes=m), 100.0 - m) for m in (0, 15, 30, 90, 180)]}
    marks = {"a": [(t0 + timedelta(minutes=120), 50.0)]}
    prices, used = G._leg_prices([leg], marks, candles)
    assert used and [t for t, _ in prices["a"]] == [t0 + timedelta(minutes=m) for m in (0, 15, 30, 90, 120)]
    # marks from the start: candles aren't needed
    marks = {"a": [(t0 + timedelta(minutes=5), 99.0)]}
    prices, used = G._leg_prices([leg], marks, candles)
    assert not used and prices["a"] == marks["a"]
    # a closed leg takes no candle after its close
    closed = {**leg, "closed_at": t0 + timedelta(minutes=20)}
    prices, _ = G._leg_prices([closed], {}, candles)
    assert [t for t, _ in prices["a"]] == [t0, t0 + timedelta(minutes=15)]


def test_booked_path_steps_at_each_close():
    t0 = datetime(2026, 9, 1, 10, 0, tzinfo=IST)
    legs = [{"closed_at": t0 + timedelta(days=2), "realized_pnl": -300},
            {"closed_at": t0 + timedelta(days=1), "realized_pnl": 500},
            {"closed_at": None, "realized_pnl": 0}]
    assert G._booked_path(legs, t0, None) == [(t0, 0.0), (t0 + timedelta(days=1), 500.0), (t0 + timedelta(days=2), 200.0)]
    assert G._booked_path(legs, t0, t0 + timedelta(days=2)) == [(t0, 0.0), (t0 + timedelta(days=1), 500.0)], \
        "the close at the end is the result, added by the caller"


def test_gaps_are_market_hours_without_prices_not_nights_or_weekends():
    d = lambda *a: datetime(*a, tzinfo=IST)
    fri_close, mon_open = d(2026, 10, 16, 15, 25), d(2026, 10, 19, 9, 20)
    assert G._market_minutes(fri_close, mon_open) == 10
    assert G._gaps([d(2026, 10, 19, 15, 25), d(2026, 10, 20, 9, 20)]) == [], "overnight"
    assert G._gaps([fri_close, mon_open]) == [], "weekend"
    assert G._gaps([d(2026, 10, 19, 10, 0), d(2026, 10, 19, 10, 50), d(2026, 10, 20, 11, 0)]) == [2], \
        "Kite offline from Monday 10:50 to Tuesday 11:00"
    assert G._market_minutes(d(2026, 10, 19, 9, 0), d(2026, 10, 19, 16, 0)) == 375


def test_thin_keeps_the_ends():
    pts = list(range(1000))
    t = G._thin(pts, 50)
    assert len(t) == 50 and t[0] == 0 and t[-1] == 999
    assert G._thin([1, 2, 3], 50) == [1, 2, 3]
