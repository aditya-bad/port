"""custom_scripts/day_check.py end to end: the Kite session from the
database, a run row reported into, every deployment's day checked and
stored — with Kite faked; and a failure recorded on the run."""

import argparse
import importlib.util
from pathlib import Path

from app.db import queries
from app.repair import daycheck as D
from tests.repair_market import candles_source, flat
from tests.sim import Sim
from tests.strategies.helpers import cfg

SCRIPT = Path(__file__).resolve().parents[2] / "custom_scripts" / "day_check.py"


def load_script():
    spec = importlib.util.spec_from_file_location("day_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def args(day, run=None, **kw):
    return argparse.Namespace(date=str(day), deployment=None, run=run, verbose=False, **kw)


def test_the_worker_checks_every_deployment_and_reports_into_its_run(pool_run, monkeypatch, capsys):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        day = sim.market.today
        await D.capture_states(pool, day, "open")
        await sim.walk("09:15", "15:29", every_min=5)
        stalled = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"), name="Stalled")
        await stalled.walk("09:15", "12:15", every_min=5)
        await queries.set_kite_session(pool, "tok")
        minutes = candles_source(sim, flat(25000))

        class FakeKiteConnect:
            def __init__(self, api_key):
                pass

            def set_access_token(self, t):
                assert t == "tok"

            def instruments(self, exchange=None):
                return sim.kite.instruments(exchange)

            def quote(self, keys):
                return {}

            def ltp(self, keys):
                return {}

            def historical_data(self, token, start, end, interval, **kw):
                if interval == "minute" and start.date() == day:
                    return minutes(token, start, end)
                return []

        import kiteconnect
        monkeypatch.setattr(kiteconnect, "KiteConnect", FakeKiteConnect)
        run = await pool.fetchval("INSERT INTO day_check_runs (day) VALUES ($1) RETURNING id", day)
        assert await load_script().main(args(day, run)) == 0
        out = capsys.readouterr().out
        assert "[1/2]" in out and "Day check" in out
        r = await pool.fetchrow("SELECT * FROM day_check_runs WHERE id = $1", run)
        assert r["status"] == "done" and r["finished_at"] is not None
        got = {x["deployment_id"]: x["status"] for x in await pool.fetch("SELECT * FROM day_checks WHERE run_id = $1", run)}
        assert got == {sim.dep_id: "ok", stalled.dep_id: "rule"}
    pool_run(go)


def test_without_a_kite_session_the_run_is_marked_failed(pool_run, monkeypatch, capsys):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        assert await load_script().main(args(sim.market.today)) == 1
        r = await pool.fetchrow("SELECT * FROM day_check_runs ORDER BY id DESC LIMIT 1")
        assert r["status"] == "failed" and "No Kite session" in r["error"] and r["trigger"] == "manual"
    pool_run(go)
