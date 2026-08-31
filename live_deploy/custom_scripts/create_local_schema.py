#!/usr/bin/env python3
"""
live_deploy — custom_scripts/create_local_schema.py

Step 2 of moving off a remote-hosted DB onto one running alongside this
app's own server (step 1: setup_local_postgres.sh). Builds every table
this app needs on a FRESH, empty database — by calling the exact same
`run_migrations()` the app itself calls automatically on every startup
(see app/db/migrate.py's own docstring: "point this at a fresh
database and the schema builds itself on first boot"). This script
just does that once, standalone, against whichever database URL you
point it at, without needing the whole FastAPI app (or a Kite session)
running.

Idempotent, same as the app's own startup — safe to run again against
a database that already has some or all migrations applied; it only
applies whatever's still missing, and does nothing at all if it's
already fully up to date.

IMPORTANT if your --database-url's HOST is a Docker container name
(e.g. `live-deploy-db`, from setup_local_postgres.sh): that's only
resolvable via Docker's own embedded DNS from INSIDE a container on the
same Docker network — running this directly on the bare host WILL fail
with `socket.gaierror: Temporary failure in name resolution` (confirmed
by actually hitting this, not a theoretical warning). Run it inside a
container on that network instead — either `docker exec` into the
already-running app container (it already has asyncpg + this app's own
migration code, see migrate_to_local_db.sh's own Step 2 for the exact
invocation), or a throwaway container with this repo mounted in (see
setup_local_postgres.sh's own printed next-steps for that form). A
--database-url whose host is a real, host-resolvable hostname or IP
(a port-published Postgres, a remote DB like Neon) has no such
restriction and this script can run directly on the host against it
exactly as the USAGE below shows.

USAGE:
    cd live_deploy
    python3 custom_scripts/create_local_schema.py --database-url postgresql://user:pass@host:5432/dbname
    # or, if DATABASE_URL is already set in the environment:
    python3 custom_scripts/create_local_schema.py
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.migrate import run_migrations
from app.db.pool import close_pool, create_pool


async def main(database_url: str) -> None:
    print(f"Connecting to {database_url.split('@')[-1] if '@' in database_url else database_url} ...")
    pool = await create_pool(database_url)
    try:
        applied = await run_migrations(pool)
        if applied:
            print(f"Applied {len(applied)} migration(s): {applied}")
        else:
            print("Schema already up to date — nothing to apply.")
    finally:
        await close_pool(pool)
    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database-url", default=os.environ.get("DATABASE_URL"),
        help="Target Postgres connection string. Defaults to $DATABASE_URL if not given.",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("No database URL given — pass --database-url or set DATABASE_URL.")
    asyncio.run(main(args.database_url))
