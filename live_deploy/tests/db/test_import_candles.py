"""custom_scripts/import_candles.py: user backups into the candle store —
1-minute only, symbols resolved, nothing duplicated."""

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

from app.db import queries
from tests.conftest import fill

spec = importlib.util.spec_from_file_location("import_candles", Path(__file__).parents[2] / "custom_scripts" / "import_candles.py")
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)


def write(path, header, rows):
    path.write_text("\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows]) + "\n")


def test_imports_one_minute_backups_and_refuses_other_intervals(pool_run, tmp_path, capsys):
    t0 = datetime(2026, 9, 1, 9, 15)
    write(tmp_path / "a.csv", ["instrument_token", "date", "open", "high", "low", "close", "volume"],
          [(111, (t0 + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), 1, 2, 0.5, 1.5, 10) for i in range(375)]
          + [(111, "2026-09-01 15:45:00", 1, 1, 1, 1, 0)])
    write(tmp_path / "five.csv", ["instrument_token", "date", "open", "high", "low", "close"],
          [(222, (t0 + timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M"), 1, 1, 1, 1) for i in range(75)])
    write(tmp_path / "sym.csv", ["tradingsymbol", "date", "open", "high", "low", "close"],
          [("NIFTY26SEP24000CE", (t0 + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M"), 5, 5, 5, 5) for i in range(10)]
          + [("NOSUCH26SEP1CE", t0.strftime("%Y-%m-%d %H:%M"), 1, 1, 1, 1), ("NOSUCH26SEP1CE", (t0 + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"), 1, 1, 1, 1)])

    async def go(pool):
        d = (await queries.create_deployment(pool, "D", "strangle_monthly_v2", "positional", 1e6, {}))["id"]
        await fill(pool, d, "NIFTY26SEP24000CE", 333, "sell", 75, 5, datetime(2026, 9, 1, 10, 0))
        await imp.import_csv(pool, [tmp_path], dry=False)
        await imp.import_csv(pool, [tmp_path / "a.csv"], dry=False)          # re-run: no duplicates
        counts = {r["instrument_token"]: r["n"] for r in await pool.fetch(
            "SELECT instrument_token, count(*) AS n FROM candles_1m GROUP BY 1")}
        assert counts == {111: 375, 333: 10}, "5-minute file refused, unknown symbol skipped, outside-session dropped"
        assert await pool.fetchval("SELECT DISTINCT source FROM candles_1m") == "import"
        assert (await queries.get_contract(pool, 333))["tradingsymbol"] == "NIFTY26SEP24000CE"
    pool_run(go)
    out = capsys.readouterr().out
    assert "5-minute candles — only 1-minute" in out and "unknown tradingsymbol" in out
