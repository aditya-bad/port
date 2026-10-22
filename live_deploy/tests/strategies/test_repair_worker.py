"""custom_scripts/repair_worker.py: takes the Repair page's jobs off the
queue — a plan (rolled back) and then its apply (committed as planned) —
with Kite faked; a job it can't do is failed with the reason."""

import argparse
import importlib.util
from pathlib import Path

from app.db import queries
from app.repair import daycheck as D
from tests.repair_market import candles_source, flat
from tests.sim import Sim
from tests.strategies.helpers import assert_book_consistent, cfg

SCRIPT = Path(__file__).resolve().parents[2] / "custom_scripts" / "repair_worker.py"


def load_script():
    spec = importlib.util.spec_from_file_location("repair_worker", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_kite(monkeypatch, sim):
    minutes = candles_source(sim, flat(25000))

    class FakeKiteConnect:
        def __init__(self, api_key):
            pass

        def set_access_token(self, t):
            pass

        def instruments(self, exchange=None):
            return sim.kite.instruments(exchange)

        def quote(self, keys):
            return {}

        def ltp(self, keys):
            return {}

        def historical_data(self, token, start, end, interval, **kw):
            if interval == "minute" and start.date() == sim.market.today:
                return minutes(token, start, end)
            return []
    import kiteconnect
    monkeypatch.setattr(kiteconnect, "KiteConnect", FakeKiteConnect)


def test_the_worker_plans_then_applies_a_flagged_day(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        await sim.walk("09:15", "12:15", every_min=5)
        from app.repair.market import MinuteBook
        await D.run_day(pool, day, MinuteBook(day, candles_source(sim, flat(25000))), sim.kite)
        await queries.set_kite_session(pool, "tok")
        fake_kite(monkeypatch, sim)
        mod = load_script()
        plan_id = await pool.fetchval(
            "INSERT INTO repair_jobs (deployment_id, day, kind) VALUES ($1, $2, 'plan') RETURNING id", sim.dep_id, day)
        assert await mod.main(argparse.Namespace(idle=0, poll=0.01)) == 0
        plan = await pool.fetchrow("SELECT * FROM repair_jobs WHERE id = $1", plan_id)
        assert plan["status"] == "done", plan["error"]
        res = plan["result"]
        assert res["from"] == "15:00" and res["can_apply"] and len(res["fills"]) == 2
        assert len(res["series"]["t"]) == 375
        hb = await queries.get_app_scratch(pool, "repair_worker")
        assert hb and hb["pid"]

        apply_id = await pool.fetchval(
            "INSERT INTO repair_jobs (deployment_id, day, kind, expected, author) VALUES ($1, $2, 'apply', $3, 'me') RETURNING id",
            sim.dep_id, day, res["fills_key"])
        assert await mod.main(argparse.Namespace(idle=0, poll=0.01)) == 0
        done = await pool.fetchrow("SELECT * FROM repair_jobs WHERE id = $1", apply_id)
        assert done["status"] == "done", done["error"]
        assert done["result"]["applied"]["summary"]["fills"] == 2
        assert await sim.open_positions() == []
        await assert_book_consistent(pool, sim.dep_id)
        note = await pool.fetchrow("SELECT author, body FROM journal_entries WHERE deployment_id = $1", sim.dep_id)
        assert note["author"] == "me" and "repaired from 15:00" in note["body"]

        again = await pool.fetchval(
            "INSERT INTO repair_jobs (deployment_id, day, kind, expected) VALUES ($1, $2, 'apply', $3) RETURNING id",
            sim.dep_id, day, res["fills_key"])
        await mod.main(argparse.Namespace(idle=0, poll=0.01))
        failed = await pool.fetchrow("SELECT * FROM repair_jobs WHERE id = $1", again)
        assert failed["status"] == "failed" and "already applied" in failed["error"]
    pool_run(go)


def test_without_a_kite_session_jobs_fail_saying_so(pool_run, monkeypatch):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        jid = await pool.fetchval(
            "INSERT INTO repair_jobs (deployment_id, day, kind) VALUES ($1, $2, 'plan') RETURNING id", sim.dep_id, sim.market.today)
        await load_script().main(argparse.Namespace(idle=0, poll=0.01))
        r = await pool.fetchrow("SELECT * FROM repair_jobs WHERE id = $1", jid)
        assert r["status"] == "failed" and "No Kite session" in r["error"]
    pool_run(go)
