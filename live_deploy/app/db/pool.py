"""
live_deploy — DB connection pool.

Neon (like most hosted Postgres) requires TLS; a Neon connection string
already includes `sslmode=require` in its query params, and asyncpg
honors that automatically when it's present in the DSN — no extra ssl=
argument needed here.
"""

import json

import asyncpg


async def _init_connection(conn: asyncpg.Connection) -> None:
    """
    asyncpg doesn't auto-convert Python dicts <-> JSONB — without this,
    every query touching a JSONB column would need manual json.dumps()/
    json.loads() at every call site. Registering the codec once per
    connection makes JSONB columns behave like plain dicts everywhere
    else in this codebase.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


async def create_pool(database_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=database_url, min_size=1, max_size=10, init=_init_connection,
    )


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()
