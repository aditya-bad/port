from datetime import datetime, timezone

from tests.conftest import fill


def test_migrations_create_core_tables(pool_run):
    async def go(pool):
        rows = await pool.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        return {r["tablename"] for r in rows}
    tables = pool_run(go)
    for t in ("deployments", "positions", "position_lots", "deployment_events", "position_marks",
              "candles_1m", "contracts", "user_preferences", "schema_migrations"):
        assert t in tables
