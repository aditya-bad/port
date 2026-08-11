"""
live_deploy — lightweight SQL migration runner.

No Alembic — just numbered .sql files in app/db/migrations/, applied in
order, tracked in a schema_migrations table. Idempotent: safe to call on
every startup, only ever applies files not yet recorded as applied. This
is what "you can create all schemas and tables" turns into in practice —
point this at a fresh Neon database and the schema builds itself on
first boot.
"""

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger("live_deploy.db.migrate")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(pool: asyncpg.Pool) -> list[int]:
    """Apply any migration files not yet recorded, in filename order.

    Returns the list of version numbers newly applied (empty if the
    schema was already up to date).
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        applied = {
            r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")
        }

        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        newly_applied: list[int] = []
        for f in files:
            version = int(f.stem.split("_")[0])
            if version in applied:
                continue
            sql = f.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", version
                )
            newly_applied.append(version)
            logger.info("Applied migration %04d (%s)", version, f.name)

        return newly_applied
