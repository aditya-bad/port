#!/usr/bin/env python3
"""
live_deploy — custom_scripts/clean_deployment_names.py

Strips a fixed set of words out of every existing deployment's
deployment_name (NOT strategy_name — that's the fixed registry key
like "pivot_supertrend"; deployment_name is the free-text label chosen
at deploy time, e.g. "DTT Straddle Intraday Nifty Simple", which is
what this actually touches). Requested directly: "DTT" and "Intraday"
should come out wherever they appear, e.g.

    "DTT Straddle Intraday Nifty Simple"  ->  "Straddle Nifty Simple"

Matching is whole-word and case-insensitive (so "dtt"/"Dtt"/"DTT" all
match) — edit WORDS_TO_STRIP below to add/remove words for a future
run; nothing else about the script needs to change for that.

Standalone, deliberately — runs OUTSIDE Docker/the app process
entirely, straight against the database:

    cd live_deploy
    python3 custom_scripts/clean_deployment_names.py            # applies the renames
    python3 custom_scripts/clean_deployment_names.py --dry-run  # preview only, touches nothing

(or `./custom_scripts/clean_deployment_names.py` directly, if it's
executable — chmod +x it once if your checkout didn't preserve that.)

Reuses this app's own config loader (app/config.py) and query layer
(app/db/queries.py) for the actual DB access, not a hand-rolled
connection/SQL string — same DATABASE_URL resolution (env var first,
config.json fallback) the real app uses, and the exact same
update_deployment_fields() write path a real PATCH /deployments/{id}
request goes through, so a renamed deployment is indistinguishable
from one renamed through the UI. Does NOT import app.main / start the
dispatcher, Kite session, or any background loop — just the two
lightweight, dependency-free modules that actually do DB work.

Every planned rename is printed BEFORE anything is written, so the
output is a complete audit trail even for a non-interactive/logged
run — there's no interactive confirmation prompt to get in the way of
"just run it and it works" usage, but nothing is silent either. A
name collision (two deployments landing on the same stripped name,
blocked by deployments' own UNIQUE constraint) is reported clearly and
skipped, not left to crash the whole run.
"""

import asyncio
import re
import sys
from pathlib import Path

LIVE_DEPLOY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIVE_DEPLOY_DIR))   # so `import app.*` below works regardless of cwd

from app.config import load_config          # noqa: E402
from app.db import queries                  # noqa: E402

import asyncpg                               # noqa: E402

WORDS_TO_STRIP = ["DTT", "Intraday"]

_STRIP_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in WORDS_TO_STRIP) + r")\b", re.IGNORECASE)


def clean_name(name: str) -> str:
    """Remove every whole-word match of WORDS_TO_STRIP, then collapse
    the whitespace that removal leaves behind ("DTT Straddle Intraday
    Nifty Simple" -> " Straddle  Nifty Simple" -> "Straddle Nifty
    Simple")."""
    stripped = _STRIP_RE.sub("", name)
    return re.sub(r"\s+", " ", stripped).strip()


async def main(dry_run: bool) -> int:
    cfg = load_config()
    pool = await asyncpg.create_pool(cfg["database_url"])
    try:
        rows = await queries.list_deployments(pool)
        planned = []
        for row in rows:
            old_name = row["deployment_name"]
            new_name = clean_name(old_name)
            if new_name != old_name:
                planned.append((row["id"], old_name, new_name))

        if not planned:
            print("Nothing to rename — no deployment_name contains any of "
                  f"{WORDS_TO_STRIP}.")
            return 0

        print(f"{len(planned)} deployment(s) to rename:")
        for _id, old_name, new_name in planned:
            print(f"  {old_name!r}  ->  {new_name!r}")

        if dry_run:
            print("\n--dry-run: nothing written. Re-run without --dry-run to apply.")
            return 0

        print()
        applied, skipped = 0, 0
        for dep_id, old_name, new_name in planned:
            if not new_name:
                print(f"  SKIPPED {old_name!r}: stripping leaves an empty name, which "
                      f"isn't a valid deployment_name — rename it manually instead.")
                skipped += 1
                continue
            collision = await queries.get_deployment_by_name(pool, new_name)
            if collision is not None and collision["id"] != dep_id:
                print(f"  SKIPPED {old_name!r} -> {new_name!r}: a deployment named "
                      f"{new_name!r} already exists (id={collision['id']}) — resolve "
                      f"the naming collision manually.")
                skipped += 1
                continue
            await queries.update_deployment_fields(pool, dep_id, deployment_name=new_name)
            print(f"  OK {old_name!r} -> {new_name!r}")
            applied += 1

        print(f"\nDone: {applied} renamed, {skipped} skipped.")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv[1:]
    exit_code = asyncio.run(main(dry_run))
    sys.exit(exit_code)
