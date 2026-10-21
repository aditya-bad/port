"""--probe (app/repair/probe.py): what Kite answers for every
deployment's index and open legs, printed raw, errors and all."""

from datetime import date, datetime

from app.db import queries
from app.repair.probe import probe

DAY = date(2026, 10, 21)


class Gate:
    def __init__(self):
        self.kite = self

    def profile(self):
        return {"user_id": "AB1234", "user_name": "Trader"}

    def call(self, fn, *a, **k):
        return fn(*a, **k)

    def historical_data(self, token, start, end, interval, **kw):
        if token == 274441:
            return []
        if token == 285049349:
            raise Exception("Insufficient permission for that call.")
        if token == 999:
            return [{"date": datetime(2026, 10, 21, 9, 15), "close": 61.5}, {"date": datetime(2026, 10, 21, 15, 29), "close": 70.0}]
        return []


def test_probe_prints_what_kite_answers_for_each_index_and_leg(pool_run):
    async def go(pool):
        dep = await queries.create_deployment(pool, "DTT Bankex Strangle", "strangle_monthly_v2", "positional", 150000,
                                              {"instrument_tokens": [274441]})
        await queries.set_status(pool, dep["id"], "active")
        await pool.execute(
            "INSERT INTO positions (deployment_id, symbol, instrument_token, side, qty, avg_entry_price, opened_at) "
            "VALUES ($1, 'BANKEX26NOV66300CE', 285049349, 'short', 30, 266.35, now())", dep["id"])
        lines = []
        lookup = lambda s: {"instrument_token": 999, "tradingsymbol": s, "exchange": "BFO"}
        rep = await probe(pool, Gate(), DAY, lookup, out=lines.append)
        text = "\n".join(lines)
        assert "KITE PROBE · Wed 21 Oct 2026" in text and "Kite session: AB1234 (Trader)" in text
        assert "BANKEX  (token 274441)" in text and "today, with OI:    0 candles" in text
        assert "master: BFO:BANKEX26NOV66300CE token 999  ← DIFFERS from the app's token" in text
        assert "ERROR Exception: Insufficient permission for that call." in text
        assert "today, master token: 2 candles, 21 Oct 09:15 close 61.5 … 21 Oct 15:29 close 70.0" in text
        assert {r["token"] for r in rep} >= {274441, 285049349}
    pool_run(go)
