"""Repairing a day the end-of-day check flagged (app/repair/dayrepair.py):
from the minute the app and its strategy parted, plan (rolled back),
apply (only as planned), undo (the app's own fills back)."""

from datetime import datetime, time, timedelta

import pytest

from app.repair import daycheck as D
from app.repair import dayrepair as R
from app.repair import engine
from app.repair.market import IST, MinuteBook
from tests.conftest import fill
from tests.repair_market import candles_source, flat
from tests.sim import Sim
from tests.strategies.helpers import assert_book_consistent, cfg


async def book_state(pool, dep_id):
    pos = sorted((str(r["id"]), r["symbol"], r["status"], float(r["qty"]), float(r["avg_entry_price"]), float(r["realized_pnl"]))
                 for r in await pool.fetch("SELECT * FROM positions WHERE deployment_id = $1", dep_id))
    lots = sorted(str(r["id"]) for r in await pool.fetch("SELECT id FROM position_lots WHERE deployment_id = $1", dep_id))
    cash = round(float(await pool.fetchval("SELECT current_cash FROM deployments WHERE id = $1", dep_id)), 2)
    return pos, lots, cash


async def checked(pool, sim, path=flat(25000)):
    day = sim.market.today
    book = MinuteBook(day, candles_source(sim, path))
    await D.run_day(pool, day, book, sim.kite)
    return day, book


def test_a_stalled_day_is_planned_from_where_it_parted_applied_and_undone(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        await D.capture_states(pool, sim.market.today, "open")
        await sim.walk("09:15", "12:15", every_min=5)
        day, book = await checked(pool, sim)
        row = dict(await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id))
        assert row["status"] == "rule" and R.divergence(row) == "15:00"
        before = await book_state(pool, sim.dep_id)
        plan = await R.run(pool, sim.dep_id, day, book, sim.kite, {})
        assert plan["from"] == "15:00" and plan["complete"] and plan["can_apply"]
        assert [f["reason"] for f in plan["fills"]] == ["force_exit", "force_exit"]
        assert plan["replaced"] == [] and plan["end"]["after"] == [] and len(plan["end"]["app"]) == 2
        assert "the morning replayed to 15:00" in plan["start"]
        s = plan["series"]
        assert len(s["t"]) == 375 and s["index"]["label"] == "NIFTY 50" and len(s["index"]["ohlc"]) == 375
        assert len(s["legs"]) == 2 and all(len(v) == 375 for v in s["legs"].values())
        assert await book_state(pool, sim.dep_id) == before, "a plan writes nothing"

        res = await R.run(pool, sim.dep_id, day, book, sim.kite, {}, commit=True, expected=plan["fills_key"], author="me")
        assert await sim.open_positions() == []
        rep = await pool.fetchrow("SELECT * FROM stall_repairs WHERE id = $1::uuid", res["applied"]["id"])
        assert rep["summary"]["kind"] == "day" and rep["window_from"].astimezone(IST).strftime("%H:%M") == "15:00"
        await assert_book_consistent(pool, sim.dep_id)
        with pytest.raises(engine.PlanChanged, match="already applied"):
            await R.run(pool, sim.dep_id, day, book, sim.kite, {}, commit=True, expected=plan["fills_key"])

        await engine.undo(pool, res["applied"]["id"])
        assert await book_state(pool, sim.dep_id) == before, "undone exactly"
    pool_run(go)


def test_trades_the_app_made_that_its_strategy_wouldnt_are_replaced_and_come_back_on_undo(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        await sim.walk("09:15", "15:29", every_min=5)
        # the app "did" a trade at 13:00 the strategy wouldn't have (a bad tick, say)
        stray = datetime.combine(day, time(13, 0), IST)
        await fill(pool, sim.dep_id, "NIFTY26STRAY", 777001, "sell", 75, 40.0, stray, "spurious")
        day, book = await checked(pool, sim)
        row = dict(await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id))
        assert row["status"] == "rule" or row["status"] == "mismatch"
        assert R.divergence(row) == "13:00"
        before = await book_state(pool, sim.dep_id)
        plan = await R.run(pool, sim.dep_id, day, book, sim.kite, {})
        assert plan["from"] == "13:00"
        assert {f["symbol"] for f in plan["replaced"]} >= {"NIFTY26STRAY"}
        assert all(f["minute"] >= "13:00" for f in plan["replaced"])
        res = await R.run(pool, sim.dep_id, day, book, sim.kite, {}, commit=True, expected=plan["fills_key"])
        assert not await pool.fetchval("SELECT count(*) FROM positions WHERE deployment_id = $1 AND symbol = 'NIFTY26STRAY'", sim.dep_id)
        morning = await pool.fetch("SELECT * FROM position_lots WHERE deployment_id = $1 AND executed_at < $2", sim.dep_id, stray)
        assert morning and all("repair" not in (l["metadata"] or {}) for l in morning), "the app's own fills before it stay"
        await assert_book_consistent(pool, sim.dep_id)
        await engine.undo(pool, res["applied"]["id"])
        assert await book_state(pool, sim.dep_id) == before
    pool_run(go)


def test_a_minute_that_reads_two_ways_waits_for_your_choice_then_carries_on(pool_run, monkeypatch):
    path = lambda m: (25000, 25600, 24400, 25000) if f"{m:%H:%M}" == "13:05" else (25000, 25004, 24996, 25000)

    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "strangle_monthly_v2", cfg("strangle_monthly_v2"), mode="positional", spot=25000)
        await D.capture_states(pool, sim.market.today, "open")
        await sim.walk("09:15", "15:29", every_min=5)
        day, book = await checked(pool, sim, path)
        plan = await R.run(pool, sim.dep_id, day, book, sim.kite, {})
        assert not plan["complete"] and plan["ambiguity"]["minute"] == "13:05" and not plan["can_apply"]
        paths = plan["ambiguity"]["paths"]
        assert set(paths) == {"up_first", "down_first"}
        sells = {k: {f["symbol"] for f in v["fills"] if f["action"] == "sell"} for k, v in paths.items()}
        assert sells["up_first"] != sells["down_first"]
        assert plan["ambiguity"]["candles"][0]["label"] == "NIFTY 50"
        chosen = await R.run(pool, sim.dep_id, day, book, sim.kite, {"13:05": "up_first"})
        assert chosen["complete"] and chosen["can_apply"]
        assert {f["symbol"] for f in chosen["fills"] if f["action"] == "sell"} >= sells["up_first"]
        assert all(f.get("meta") is not None for f in chosen["fills"])
        res = await R.run(pool, sim.dep_id, day, book, sim.kite, {"13:05": "up_first"}, commit=True, expected=chosen["fills_key"])
        assert res["applied"]["summary"]["fills"] == len(chosen["fills"])
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_a_day_it_has_traded_after_cant_be_repaired(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        await D.capture_states(pool, sim.market.today, "open")
        await sim.walk("09:15", "12:15", every_min=5)
        day, book = await checked(pool, sim)
        nxt = datetime.combine(day + timedelta(days=1), time(10, 0), IST)
        await fill(pool, sim.dep_id, "NIFTY26NEXT", 777002, "sell", 75, 40.0, nxt, "entry")
        plan = await R.run(pool, sim.dep_id, day, book, sim.kite, {})
        assert not plan["can_apply"] and "traded 1 time(s) since that day" in plan["why_not"][0]
        with pytest.raises(engine.PlanChanged, match="traded 1 time"):
            await R.run(pool, sim.dep_id, day, book, sim.kite, {}, commit=True, expected=plan["fills_key"])
    pool_run(go)


def test_a_plan_that_no_longer_comes_out_the_same_isnt_applied(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        await D.capture_states(pool, sim.market.today, "open")
        await sim.walk("09:15", "12:15", every_min=5)
        day, book = await checked(pool, sim)
        with pytest.raises(engine.PlanChanged, match="differently from the plan"):
            await R.run(pool, sim.dep_id, day, book, sim.kite, {}, commit=True, expected="not-the-plan")
        assert len(await sim.open_positions()) == 2
    pool_run(go)


def test_divergence_is_the_earliest_difference():
    assert R.divergence({"diff": {"missed": [{"minute": "15:00"}], "extra": [{"minute": "13:02"}]},
                         "replay": {"auto": [{"minute": "13:05"}]}}) == "13:02"
    assert R.divergence({"diff": {"missed": [], "extra": []}, "replay": {"auto": [{"minute": "11:40"}]}}) == "11:40"
    assert R.divergence({"diff": {}, "replay": {}}) is None


def test_a_friday_planned_on_saturday_and_applied_on_sunday_repairs_friday_and_not_after_monday_trades(pool_run, monkeypatch):
    from datetime import date

    from app.repair import market as M
    friday = date(2026, 10, 16)
    assert friday.weekday() == 4

    def on(day):
        class At(datetime):   # the repair's own sense of "now" (Kite history after the day, ltp)
            @classmethod
            def now(cls, tz=None):
                return datetime.combine(day, time(11, 0), IST).astimezone(tz) if tz else datetime.combine(day, time(11, 0))
        monkeypatch.setattr(M, "datetime", At)

    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"), today=friday)
        await D.capture_states(pool, friday, "open")
        await sim.walk("09:15", "12:15", every_min=5)     # stalled on Friday
        day, book = await checked(pool, sim)
        on(date(2026, 10, 17))                             # Saturday: plan it
        plan = await R.run(pool, sim.dep_id, friday, book, sim.kite, {})
        assert plan["can_apply"] and plan["from"] == "15:00", plan["why_not"]
        on(date(2026, 10, 18))                             # Sunday: apply it
        res = await R.run(pool, sim.dep_id, friday, book, sim.kite, {}, commit=True, expected=plan["fills_key"])
        assert res["applied"]
        lots = await pool.fetch("SELECT executed_at FROM position_lots WHERE deployment_id = $1", sim.dep_id)
        assert {l["executed_at"].astimezone(IST).date() for l in lots} == {friday}, "the repair's fills are Friday's"
        assert await sim.open_positions() == []
        await assert_book_consistent(pool, sim.dep_id)
        # had it waited until Monday's trading, Friday could no longer be repaired
        await engine.undo(pool, res["applied"]["id"])
        on(date(2026, 10, 19))
        await fill(pool, sim.dep_id, "NIFTY26MON", 777003, "sell", 75, 40.0, datetime.combine(date(2026, 10, 19), time(9, 20), IST), "entry")
        late = await R.run(pool, sim.dep_id, friday, book, sim.kite, {})
        assert not late["can_apply"] and "traded 1 time(s) since that day" in late["why_not"][0]
    pool_run(go)


def test_a_repair_from_after_the_close_takes_out_the_trades_made_after_it(pool_run, monkeypatch):
    """DTT BankNifty Strangle, 23 Oct: the app rolled a leg at 15:36, after
    the close, at prices no order could get. The replay stops at 15:30, so
    the check flags the roll; the repair starts there with no minute left
    to replay, takes the two trades out and leaves the leg it rolled away."""
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "strangle_monthly_v2", cfg("strangle_monthly_v2"), mode="positional", spot=25000)
        await D.capture_states(pool, sim.market.today, "open")
        await sim.walk("09:15", "15:29", every_min=5)
        legs = await sim.open_positions()
        ce = next(p for p in legs if p["symbol"].endswith("CE"))
        held = sorted(p["symbol"] for p in legs)
        before = await book_state(pool, sim.dep_id)
        late = datetime.combine(sim.market.today, time(15, 36), IST)
        await fill(pool, sim.dep_id, ce["symbol"], int(ce["instrument_token"]), "buy", float(ce["qty"]), 224.0, late, "eod_gap_check")
        await fill(pool, sim.dep_id, "NIFTY_LATE_CE", 999003, "sell", float(ce["qty"]), 290.6, late, "eod_gap_check")
        day, book = await checked(pool, sim)
        row = dict(await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id))
        assert row["status"] == "mismatch" and R.divergence(row) == "15:36"
        assert {f["minute"] for f in row["diff"]["extra"]} == {"15:36"} and not row["diff"]["missed"]
        plan = await R.run(pool, sim.dep_id, day, book, sim.kite, {})
        assert plan["complete"] and plan["can_apply"], plan["why_not"]
        assert plan["fills"] == [] and [f["minute"] for f in plan["replaced"]] == ["15:36", "15:36"]
        assert sorted(p["symbol"] for p in plan["end"]["after"]) == held, "the strategy's own legs, as at the close"
        await R.run(pool, sim.dep_id, day, book, sim.kite, {}, commit=True, expected=plan["fills_key"])
        assert sorted(p["symbol"] for p in await sim.open_positions()) == held
        after = await book_state(pool, sim.dep_id)
        assert (after[0], after[2]) == (before[0], before[2]), "positions and cash as before the late trades"
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)
