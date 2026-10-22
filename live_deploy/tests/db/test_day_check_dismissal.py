"""A dismissed flagged day (Repair case: Dismiss) stays dismissed when the
end-of-day check runs again and finds the same thing, and comes back when
it finds something different (app/repair/daycheck.py: run_day)."""

from datetime import date

from app.db import queries
from app.repair import daycheck

DAY = date(2026, 10, 14)


def test_a_rerun_keeps_a_dismissal_only_while_the_finding_is_the_same(pool_run, monkeypatch):
    async def go(pool):
        dep = await queries.create_deployment(pool, "Rechecked", "intraday_dtt_simple", "intraday", 500000,
                                              {"instrument_tokens": [256265]})
        found = {"status": "mismatch", "headline": "Traded differently from 13:31", "rules": [], "diff": {}, "replay": {}}

        async def check(pool_, d, day, book, kite):
            return dict(found)
        monkeypatch.setattr(daycheck, "check_deployment", check)
        run = lambda: daycheck.run_day(pool, DAY, None, None, only=[dep["id"]])  # noqa: E731
        dismissed = lambda: pool.fetchval("SELECT dismissed_at FROM day_checks WHERE deployment_id = $1 AND day = $2", dep["id"], DAY)  # noqa: E731

        await run()
        await pool.execute("UPDATE day_checks SET dismissed_at = now() WHERE deployment_id = $1", dep["id"])
        await run()
        assert await dismissed() is not None, "the same finding: still dismissed"
        found["headline"] = "Still holding 2 legs after 15:15"
        await run()
        assert await dismissed() is None, "a different finding comes back"
    pool_run(go)
