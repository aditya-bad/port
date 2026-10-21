"""custom_scripts/repair_stall.py end to end: the config, the Kite
session from the database, the app's stored candles as a fallback, the
start minute asked for, then the CLI — with Kite faked."""

import argparse
import builtins
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

from app.db import queries
from app.repair.market import IST
from tests.repair_market import candles_source
from tests.sim import Sim
from tests.strategies.helpers import cfg

SCRIPT = Path(__file__).resolve().parents[2] / "custom_scripts" / "repair_stall.py"


def load_script():
    spec = importlib.util.spec_from_file_location("repair_stall", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_script_repairs_a_stalled_deployment(pool_run, monkeypatch, capsys):
    async def go(pool):
        sim = await Sim.create(pool, monkeypatch, "intraday_dtt_simple", cfg("intraday_dtt_simple"))
        await sim.walk("09:15", "12:15", every_min=5)
        await queries.set_kite_session(pool, "tok")
        legs = await sim.open_positions()
        ce = next(p for p in legs if p["symbol"].endswith("CE"))
        # Kite has no history for the CE (say it expired): the app's own stored candle is used
        stored_at = datetime.combine(sim.market.today, datetime.min.time()).replace(hour=15, tzinfo=IST)
        await pool.execute(
            "INSERT INTO candles_1m (instrument_token, minute, open, high, low, close, volume, source) "
            "VALUES ($1, $2, 123.45, 124, 123, 123.5, 10, 'test')", int(ce["instrument_token"]), stored_at)
        minutes = candles_source(sim, lambda m: (25000, 25004, 24996, 25000))

        class FakeKiteConnect:
            def __init__(self, api_key):
                self.api_key = api_key

            def set_access_token(self, t):
                assert t == "tok"

            def instruments(self, exchange=None):
                return sim.kite.instruments(exchange)

            def historical_data(self, token, start, end, interval, **kw):
                if interval == "minute" and start.date() == sim.market.today:
                    if int(token) == int(ce["instrument_token"]):
                        raise Exception("invalid token")
                    return minutes(token, start, end)
                return sim.kite.historical_data(token, start, end, interval)

        import kiteconnect
        monkeypatch.setattr(kiteconnect, "KiteConnect", FakeKiteConnect)
        # start minute, the deployment, apply, yes, and yes the app is stopped (it can't be paused), quit
        answers = iter(["12:16", "1", "a", "y", "y", "q"])
        monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
        mod = load_script()
        args = argparse.Namespace(probe=False, date=str(sim.market.today), start=None, end="15:30", api="http://127.0.0.1:9",
                                  no_api=True, author="tester")
        await mod.main(args)
        out = capsys.readouterr().out
        assert "Applied: 2 trade(s)" in out
        lots = await pool.fetch(
            "SELECT * FROM position_lots WHERE deployment_id = $1 AND metadata ? 'repair' ORDER BY executed_at", sim.dep_id)
        prices = {l["metadata"]["repair"]["minute"]: float(l["price"]) for l in lots if l["position_id"] == ce["id"]}
        assert prices == {"15:00": 123.45}       # the stored candle's open at the force-exit minute
        assert "from the app's own store" in out
        note = await pool.fetchrow("SELECT author FROM journal_entries WHERE deployment_id = $1", sim.dep_id)
        assert note["author"] == "tester"
    pool_run(go)
