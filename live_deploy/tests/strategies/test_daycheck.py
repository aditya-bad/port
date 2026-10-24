"""The end-of-day check (app/repair/daycheck.py) on simulated days: the
book rewound to the open, the strategy replayed over the whole session,
its trades matched to the app's, the rules of a day's end."""

from datetime import date, datetime, time, timedelta

import pytest

from app.repair import daycheck as D
from app.repair.market import IST, MinuteBook
from tests.repair_market import candles_source, flat
from tests.sim import Sim
from tests.strategies.helpers import assert_book_consistent, cfg


async def dep_row(pool, sim):
    return dict(await pool.fetchrow("SELECT * FROM deployments WHERE id = $1", sim.dep_id))


async def snapshot(pool, dep_id):
    pos = sorted((r["symbol"], r["status"], float(r["qty"]), float(r["avg_entry_price"])) for r in await pool.fetch(
        "SELECT * FROM positions WHERE deployment_id = $1", dep_id))
    cash = float(await pool.fetchval("SELECT current_cash FROM deployments WHERE id = $1", dep_id))
    lots = await pool.fetchval("SELECT count(*) FROM position_lots WHERE deployment_id = $1", dep_id)
    return pos, round(cash, 2), lots


def test_a_healthy_day_replays_the_same_and_is_ok(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        day = sim.market.today
        assert await D.capture_states(pool, day, "open") == 1
        await sim.walk("09:15", "15:29", every_min=1)
        assert await sim.open_positions() == [], "the force exit at 15:00 closed it"
        before = await snapshot(pool, sim.dep_id)
        book = MinuteBook(day, candles_source(sim, flat(25000)))
        r = await D.check_deployment(pool, await dep_row(pool, sim), day, book, sim.kite)
        assert r["status"] == "ok", r
        assert len(r["diff"]["matched"]) == 4 and not r["diff"]["missed"] and not r["diff"]["extra"]
        assert r["diff"]["end"]["same"] and r["replay"]["start"].startswith("captured at the open")
        assert await snapshot(pool, sim.dep_id) == before, "nothing written"
        await assert_book_consistent(pool, sim.dep_id)
    pool_run(go)


def test_a_stalled_day_breaks_the_cutoff_rule_and_shows_the_missed_exits(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        await sim.walk("09:15", "12:15", every_min=5)        # ticks stopped at 12:15
        assert len(await sim.open_positions()) == 2
        book = MinuteBook(day, candles_source(sim, flat(25000)))
        r = await D.check_deployment(pool, await dep_row(pool, sim), day, book, sim.kite)
        assert r["status"] == "rule"
        assert r["rules"][0]["rule"] == "flat_after_cutoff" and "15:00 cut-off" in r["rules"][0]["title"]
        assert "and the replay found 2 trade(s) the strategy would have made that the app didn't" in r["headline"]
        assert {f["reason"] for f in r["diff"]["missed"]} == {"force_exit"} or len(r["diff"]["missed"]) == 2
        assert not r["diff"]["end"]["same"] and r["diff"]["end"]["replay"] == []
    pool_run(go)


def test_without_a_start_of_day_state_only_the_rules_are_checked(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        day = sim.market.today
        await sim.walk("09:15", "12:15", every_min=5)
        book = MinuteBook(day, candles_source(sim, flat(25000)))
        r = await D.check_deployment(pool, await dep_row(pool, sim), day, book, sim.kite)
        assert r["status"] == "rule", "a broken rule is flagged without the replay"
        sim2 = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"), name="Healthy")
        await sim2.walk("09:15", "15:29", every_min=5)
        r2 = await D.check_deployment(pool, await dep_row(pool, sim2), day, book, sim2.kite)
        assert r2["status"] == "cant_check" and "No start-of-day state" in r2["headline"]
    pool_run(go)


def test_the_close_of_the_day_before_starts_the_day_when_the_open_wasnt_captured(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        day = sim.market.today
        await D.capture_states(pool, day - timedelta(days=1), "close")
        await sim.walk("09:15", "15:29", every_min=5)
        book = MinuteBook(day, candles_source(sim, flat(25000)))
        r = await D.check_deployment(pool, await dep_row(pool, sim), day, book, sim.kite)
        assert r["status"] == "ok" and r["replay"]["start"].startswith("captured after the close of")
    pool_run(go)


def test_rewind_rebuilds_the_book_at_the_open_inside_a_rolled_back_transaction(pool_run, monkeypatch):
    """A positional strangle entered the day before and adjusted today:
    at the open it held the two original legs, at the entry prices, and
    the cash it had then."""
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "strangle_monthly_v2", cfg("strangle_monthly_v2"), mode="positional", spot=25000)
        day = sim.market.today
        await sim.walk("09:15", "12:15", every_min=5)
        at_open = await snapshot(pool, sim.dep_id)
        # fake "today": one leg closed and a new one opened after 12:30
        legs = await sim.open_positions()
        from tests.conftest import fill
        later = datetime.combine(day, time(13, 0), IST)
        ce = next(p for p in legs if p["symbol"].endswith("CE"))
        await fill(pool, sim.dep_id, ce["symbol"], int(ce["instrument_token"]), "buy", float(ce["qty"]), 5.0, later, "roll")
        await fill(pool, sim.dep_id, "NIFTY_NEW_CE", 999001, "sell", float(ce["qty"]), 50.0, later, "roll")
        async with pool.acquire() as conn:
            tr = conn.transaction()
            await tr.start()
            await D.rewind(conn, sim.dep_id, datetime.combine(day, time(12, 30), IST), {"k": 1})
            pos = sorted((r["symbol"], r["status"], float(r["qty"]), float(r["avg_entry_price"])) for r in await conn.fetch(
                "SELECT * FROM positions WHERE deployment_id = $1", sim.dep_id))
            cash = round(float(await conn.fetchval("SELECT current_cash FROM deployments WHERE id = $1", sim.dep_id)), 2)
            state = await conn.fetchval("SELECT state FROM deployment_state WHERE deployment_id = $1", sim.dep_id)
            await tr.rollback()
        assert (pos, cash) == at_open[:2] and state == {"k": 1}
        assert (await snapshot(pool, sim.dep_id))[2] == at_open[2] + 2, "rolled back"
    pool_run(go)


# ── Pure parts ──────────────────────────────────────────────────────────

DAY = date(2026, 10, 21)


def fl(hhmm, action, symbol, qty=75, price=100.0, reason=""):
    t = datetime.combine(DAY, datetime.strptime(hhmm, "%H:%M").time(), IST)
    return {"at": t, "minute": hhmm, "action": action, "symbol": symbol, "qty": qty, "price": price, "reason": reason}


def test_compare_matches_within_three_minutes_on_action_contract_and_qty():
    app = [fl("10:00", "sell", "CE"), fl("10:00", "sell", "PE"), fl("13:04", "buy", "CE"), fl("14:00", "sell", "X")]
    rep = [fl("10:01", "sell", "CE", price=98), fl("10:00", "sell", "PE"), fl("13:10", "buy", "CE"), fl("15:00", "buy", "PE")]
    d = D.compare(app, rep, [{"symbol": "PE", "side": "short", "qty": 75}], [], DAY)
    assert [m["app"]["symbol"] for m in d["matched"]] == ["PE", "CE"] or len(d["matched"]) == 2
    assert {(m["minutes_apart"], m["price_diff"]) for m in d["matched"]} == {(0, 0.0), (1, 2.0)}
    assert [(f["minute"], f["symbol"]) for f in d["missed"]] == [("13:10", "CE"), ("15:00", "PE")]
    assert [(f["minute"], f["symbol"]) for f in d["extra"]] == [("13:04", "CE"), ("14:00", "X")]
    assert d["end"]["same"] is False and d["end"]["app"] == [{"symbol": "PE", "side": "short", "qty": 75.0}]


def test_rules_of_a_days_end():
    dtt = {"mode": "intraday", "config": {"force_exit_time": "15:00"}}
    btst = {"mode": "intraday", "config": {"entry_time": "15:20", "exit_time": "09:20"}}
    strangle = {"mode": "positional", "config": {}}
    held = [{"symbol": "NIFTY26O2125000CE", "token": 1, "side": "short", "qty": 75}]
    assert [r["rule"] for r in D.rules(dtt, held, [], DAY)] == ["flat_after_cutoff", "held_past_expiry"]
    assert D.rules(btst, [{"symbol": "NIFTY26O2825000CE", "token": 2, "side": "short", "qty": 75}], [], DAY) == [], \
        "BTST holds overnight by design: no cut-off to break"
    assert D.rules(strangle, [{"symbol": "NIFTY26NOV25000CE", "token": 3, "side": "short", "qty": 75}], [], DAY) == []
    assert [r["rule"] for r in D.rules(strangle, held, [], DAY)] == ["held_past_expiry"]
    assert D.rules(strangle, held, [], DAY, {1: date(2026, 10, 28)}) == [], "the contracts table's expiry wins"
    late = D.rules(dtt, [], [fl("15:07", "buy", "CE", reason="square_off_sweep")], DAY)
    assert [r["rule"] for r in late] == ["closed_late"] and late[0]["severity"] == "warning"


def test_verdicts():
    same = {"missed": [], "extra": [], "end": {"same": True}}
    diff = {"missed": [fl("13:05", "buy", "CE")], "extra": [], "end": {"same": False}}

    class O:
        auto = []
    amb = O()
    amb.auto = [{"minute": "13:05"}]
    assert D.verdict([], same, O(), True)[0] == "ok"
    assert D.verdict([], diff, O(), True) == ("mismatch", "The replay found 1 trade(s) the strategy would have made that the app didn't")
    s, h = D.verdict([], diff, amb, True)
    assert s == "ambiguous" and "13:05" in h
    warn = [{"severity": "warning", "title": "1 fill(s) after its 15:00 cut-off"}]
    assert D.verdict(warn, same, O(), True) == ("rule", "1 fill(s) after its 15:00 cut-off")
    assert D.verdict([], None, None, False, "No start-of-day state.")[0] == "cant_check"


def strangle_day(path_fn):
    """The strangle's day as the app saw it (a flat 25,000 all day) against
    Kite's candles, which follow `path_fn(minute)`."""
    async def setup(pool, monkeypatch):
        sim = await Sim.create(pool, monkeypatch, "strangle_monthly_v2", cfg("strangle_monthly_v2"), mode="positional", spot=25000)
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        await sim.walk("09:15", "15:29", every_min=5)
        book = MinuteBook(day, candles_source(sim, path_fn))
        return sim, day, book
    return setup


def test_a_steady_move_the_app_never_acted_on_is_an_unambiguous_mismatch(pool_run, monkeypatch):
    def path(m):
        k = max(0, min(30, (m.hour * 60 + m.minute) - (13 * 60)))     # 13:00–13:30: +20 a minute, tight candles
        s = 25000 + 20 * k
        return (s, s + 2, s - 2, s)

    async def go(pool):
        sim, day, book = await strangle_day(path)(pool, monkeypatch)
        r = await D.check_deployment(pool, dict(await pool.fetchrow("SELECT * FROM deployments WHERE id = $1", sim.dep_id)),
                                     day, book, sim.kite)
        assert r["status"] == "mismatch", r["headline"]
        assert r["diff"]["missed"] and not r["replay"]["auto"]
        assert all(f["reason"] == "continuous_50pct_trigger" for f in r["diff"]["missed"])
        assert not r["diff"]["end"]["same"]
    pool_run(go)


def test_a_difference_hanging_on_a_minute_read_two_ways_is_ambiguous(pool_run, monkeypatch):
    path = lambda m: (25000, 25600, 24400, 25000) if f"{m:%H:%M}" == "13:05" else (25000, 25004, 24996, 25000)

    async def go(pool):
        sim, day, book = await strangle_day(path)(pool, monkeypatch)
        r = await D.check_deployment(pool, dict(await pool.fetchrow("SELECT * FROM deployments WHERE id = $1", sim.dep_id)),
                                     day, book, sim.kite)
        assert r["status"] == "ambiguous" and "13:05" in r["headline"]
        assert [a["minute"] for a in r["replay"]["auto"]] == ["13:05"]
    pool_run(go)


def test_run_day_stores_one_result_per_deployment_and_replaces_it_on_a_rerun(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        await sim.walk("09:15", "15:29", every_min=5)
        book = MinuteBook(day, candles_source(sim, flat(25000)))
        run = await pool.fetchval("INSERT INTO day_check_runs (day, trigger) VALUES ($1, 'manual') RETURNING id", day)
        lines = []
        res = await D.run_day(pool, day, book, sim.kite, run_id=run, say=lines.append)
        assert [r["status"] for r in res] == ["ok"] and lines[0].startswith("[1/1]")
        row = await pool.fetchrow("SELECT * FROM day_checks WHERE deployment_id = $1", sim.dep_id)
        assert row["status"] == "ok" and row["run_id"] == run and row["diff"]["end"]["same"] is True
        assert (await pool.fetchval("SELECT progress FROM day_check_runs WHERE id = $1", run))["done"] == 1
        await D.run_day(pool, day, book, sim.kite)
        assert await pool.fetchval("SELECT count(*) FROM day_checks") == 1
    pool_run(go)


def test_the_startup_recheck_leaves_other_strategies_and_checks_run_after_the_fix_alone(pool_run):
    from app.db import queries

    async def go(pool):
        strangle = await queries.create_deployment(pool, "S", "strangle_monthly_v2", "positional", 500000, cfg("strangle_monthly_v2"))
        pivot = await queries.create_deployment(pool, "P", "pivot_supertrend_options", "intraday", 500000, cfg("pivot_supertrend_options"))
        day = date(2026, 10, 23)
        stored = {"fills": [{"minute": "10:20", "action": "sell", "symbol": "X", "qty": 75.0, "price": 1.0, "reason": "entry"}]}
        diff = {"matched": [], "missed": stored["fills"], "extra": [], "end": {"app": [], "replay": [], "same": True}}
        for dep, at in ((strangle, "2026-10-23 16:00+05:30"), (pivot, "2026-10-26 16:00+05:30")):
            await pool.execute(
                "INSERT INTO day_checks (day, deployment_id, status, headline, rules, diff, replay, checked_at) "
                "VALUES ($1, $2, 'mismatch', 'h', '[]'::jsonb, $3, $4, $5::timestamptz)",
                day, dep["id"], diff, stored, datetime.fromisoformat(at))
        await pool.execute("INSERT INTO app_marks (name, at) VALUES ('fills_stamped_at_tick', $1) ON CONFLICT (name) DO UPDATE SET at = $1",
                           datetime(2026, 10, 25, 10, 0, tzinfo=IST))
        assert await D.recheck_candle_stamped(pool) == []
        assert {r["status"] for r in await pool.fetch("SELECT status FROM day_checks")} == {"mismatch"}
        await pool.execute("DELETE FROM app_marks")
        assert await D.recheck_candle_stamped(pool) == [], "no mark (the fix not live): nothing to recheck"
    pool_run(go)
