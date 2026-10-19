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
        await fill(pool, d, "NIFTY26SEP25000PE", 111, "sell", 75, 5, datetime(2026, 9, 1, 10, 0))   # so token 111 is a known option
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


def test_all_trades_backfills_every_listed_contract_once(pool_run, monkeypatch, capsys):
    from datetime import date, timezone
    import app.candles as C
    asked = []

    async def history(resolver, token, start, end, interval):
        asked.append((token, start.date(), end.date()))
        out, t = [], start
        while t <= end:
            if t.weekday() < 5 and (9, 15) <= (t.hour, t.minute) < (15, 30):
                out.append({"date": t.replace(tzinfo=C.IST), "open": 1, "high": 1, "low": 1, "close": 1})
            t += timedelta(minutes=1)
        return out
    monkeypatch.setattr(C.OptionsResolver, "get_historical_candles", history)

    async def go(pool):
        await queries.set_kite_session(pool, "tok", datetime.now(timezone.utc))
        a = (await queries.create_deployment(pool, "A", "strangle_monthly_v2", "positional", 1e6, {"instrument_tokens": [256265]}))["id"]
        b = (await queries.create_deployment(pool, "B", "intraday_dtt_simple", "intraday", 1e6, {"instrument_tokens": [256265]}))["id"]
        live = "NIFTY26NOV25000CE"                       # monthly, listed
        await fill(pool, a, live, 501, "sell", 75, 100, datetime(2026, 10, 12, 10, 0))
        await fill(pool, a, live, 501, "buy", 75, 90, datetime(2026, 10, 14, 11, 0))
        await fill(pool, b, live, 501, "sell", 75, 100, datetime(2026, 10, 13, 10, 0))   # same contract, other deployment
        await fill(pool, b, live, 501, "buy", 75, 95, datetime(2026, 10, 13, 12, 0))
        await fill(pool, b, "NIFTY26O0625000PE", 502, "sell", 75, 50, datetime(2026, 10, 1, 10, 0))   # expired 6 Oct
        await fill(pool, b, "NIFTY26O0625000PE", 502, "buy", 75, 40, datetime(2026, 10, 1, 11, 0))
        await imp.import_all_trades(pool, {"api_key": "k"}, date(2026, 10, 19), dry=False)
        tokens = sorted({t for t, *_ in asked})
        assert tokens == [501], "the listed contract once; the expired one and the index never"
        n501 = await pool.fetchval("SELECT count(*) FROM candles_1m WHERE instrument_token = 501")
        assert n501 == 3 * 375, "12–14 Oct, its whole holding span, one row a minute"
        asked.clear()
        await imp.import_all_trades(pool, {"api_key": "k"}, date(2026, 10, 19), dry=False)
        assert asked == [], "a re-run finds everything stored and asks Kite for nothing"
    pool_run(go)
    out = capsys.readouterr().out
    assert "skip NIFTY26O0625000PE" in out



def test_csv_import_refuses_indices(pool_run, tmp_path, capsys):
    t0 = datetime(2026, 9, 1, 9, 15)
    write(tmp_path / "nifty.csv", ["instrument_token", "date", "open", "high", "low", "close"],
          [(256265, (t0 + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M"), 1, 1, 1, 1) for i in range(30)])

    async def go(pool):
        await imp.import_csv(pool, [tmp_path], dry=False)
        assert await pool.fetchval("SELECT count(*) FROM candles_1m") == 0
    pool_run(go)
    assert "not an option contract" in capsys.readouterr().out
