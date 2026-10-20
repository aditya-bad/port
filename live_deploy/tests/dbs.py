"""Test databases for parallel runs.

Every pytest-xdist worker and every browser-test server gets its own
database next to TEST_DATABASE_URL (t_test -> t_test_gw0, t_test_e2e1,
…), so they never truncate each other's data. They are created on first
use; the test role needs CREATEDB (or create them once by hand).

    python tests/dbs.py t_test_gw0 t_test_e2e0     # create if missing
"""

import asyncio
import os
import sys
from urllib.parse import urlparse, urlunparse

BASE = os.environ.get("TEST_DATABASE_URL", "postgresql://t:t@localhost/t_test")


def name_of(url: str) -> str:
    return urlparse(url).path.lstrip("/")


def url_for(suffix: str, base: str = BASE) -> str:
    """BASE with `_suffix` appended to the database name ('' -> BASE)."""
    if not suffix:
        return base
    u = urlparse(base)
    return urlunparse(u._replace(path=f"/{name_of(base)}_{suffix}"))


async def ensure(url: str, base: str = BASE) -> None:
    """Create the database at `url` (connecting through `base`) if missing."""
    import asyncpg
    name = name_of(url)
    if "test" not in name:
        raise SystemExit(f"refusing to create {name!r}: test database names must contain 'test'")
    conn = await asyncpg.connect(base)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _main(suffixes):
    for s in suffixes:
        await ensure(url_for(s))


if __name__ == "__main__":
    try:
        asyncio.run(_main(sys.argv[1:]))
    except Exception as e:   # unreachable, no CREATEDB
        print(f"could not create test databases: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
