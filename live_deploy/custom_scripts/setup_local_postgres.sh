#!/usr/bin/env bash
# live_deploy — custom_scripts/setup_local_postgres.sh
#
# Step 1 of moving off a remote-hosted DB (Neon, etc.) onto a Postgres
# that runs ALONGSIDE this app's own server, on the same machine.
#
# Runs Postgres as a SIBLING Docker container (official `postgres`
# image), on its own Docker network, and connects the ALREADY-RUNNING
# `live-deploy` app container to that same network -- no need to
# recreate the app container, no need to touch host-level
# postgresql.conf/pg_hba.conf, no dependency on docker-compose (this
# repo is deployed via plain `docker run`, not compose -- see the
# Dockerfile's own header). The app container reaches the DB by
# CONTAINER NAME over that shared network (Docker's own embedded DNS),
# not by IP -- stable across restarts, unlike the bridge gateway IP.
#
# After this script, run (in this order):
#   1. custom_scripts/create_local_schema.py   -- builds every table
#   2. custom_scripts/copy_remote_data_to_local.sh -- copies existing rows
#   3. set LOCAL_DATABASE_URL (this script prints the exact value) on
#      the app container and restart it -- see app/config.py's own
#      LOCAL DB OVERRIDE comment for exactly how that's picked up.
#
# USAGE:
#   ./custom_scripts/setup_local_postgres.sh
#
# All of the below are overridable via environment variables; sane
# defaults are used otherwise.
set -euo pipefail

APP_CONTAINER="${APP_CONTAINER:-live-deploy}"
DB_CONTAINER="${DB_CONTAINER:-live-deploy-db}"
DB_NETWORK="${DB_NETWORK:-live_deploy_net}"
DB_VOLUME="${DB_VOLUME:-live_deploy_db_data}"
DB_USER="${DB_USER:-liveuser}"
DB_NAME="${DB_NAME:-live_deploy}"
DB_IMAGE="${DB_IMAGE:-postgres:16}"

# DB_PASSWORD: use whatever's given, or generate one and print it ONCE
# -- never hardcoded in this script, never written anywhere but stdout,
# your own responsibility to save it (into the LOCAL_DATABASE_URL you
# set on the app container in step 3 above).
if [ -z "${DB_PASSWORD:-}" ]; then
  DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  GENERATED_PASSWORD=1
else
  GENERATED_PASSWORD=0
fi

echo "== live_deploy: setting up a local Postgres container =="
echo "  app container : $APP_CONTAINER"
echo "  db container  : $DB_CONTAINER"
echo "  network       : $DB_NETWORK"
echo "  db name/user  : $DB_NAME / $DB_USER"
echo

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed / not on PATH." >&2
  exit 1
fi

# Network -- idempotent, `docker network create` fails loudly if it
# already exists, so check first rather than relying on `|| true`
# (which would also swallow a genuine failure).
if docker network inspect "$DB_NETWORK" >/dev/null 2>&1; then
  echo "-- network '$DB_NETWORK' already exists, reusing it"
else
  echo "-- creating network '$DB_NETWORK'"
  docker network create "$DB_NETWORK"
fi

# DB container -- idempotent: if it already exists (running or
# stopped), just (re)start it rather than erroring or recreating it
# (which would lose the volume's mount point registration, though not
# the volume's own data).
if docker container inspect "$DB_CONTAINER" >/dev/null 2>&1; then
  echo "-- container '$DB_CONTAINER' already exists, starting it (if not already running)"
  docker start "$DB_CONTAINER" >/dev/null
else
  echo "-- creating container '$DB_CONTAINER' (image $DB_IMAGE, volume $DB_VOLUME)"
  docker run -d \
    --name "$DB_CONTAINER" \
    --network "$DB_NETWORK" \
    --restart unless-stopped \
    -e POSTGRES_USER="$DB_USER" \
    -e POSTGRES_PASSWORD="$DB_PASSWORD" \
    -e POSTGRES_DB="$DB_NAME" \
    -v "$DB_VOLUME":/var/lib/postgresql/data \
    "$DB_IMAGE" >/dev/null
fi

# Connect the EXISTING app container to the same network -- idempotent:
# `docker network connect` errors if already connected, which is fine,
# not a real failure (the desired end state already holds).
if docker container inspect "$APP_CONTAINER" >/dev/null 2>&1; then
  if docker network connect "$DB_NETWORK" "$APP_CONTAINER" 2>/dev/null; then
    echo "-- connected '$APP_CONTAINER' to network '$DB_NETWORK'"
  else
    echo "-- '$APP_CONTAINER' is already on network '$DB_NETWORK' (or connect failed -- check above)"
  fi
else
  echo "WARNING: app container '$APP_CONTAINER' not found — connect it to"
  echo "  network '$DB_NETWORK' manually once it exists:"
  echo "    docker network connect $DB_NETWORK $APP_CONTAINER"
fi

echo
echo "-- waiting for Postgres to accept connections..."
for i in $(seq 1 30); do
  if docker exec "$DB_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    echo "-- Postgres is ready"
    break
  fi
  sleep 1
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Postgres did not become ready within 30s -- check 'docker logs $DB_CONTAINER'" >&2
    exit 1
  fi
done

LOCAL_DB_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_CONTAINER}:5432/${DB_NAME}"

echo
echo "================================================================"
echo "Local Postgres is up. Its connection string (usable FROM the"
echo "'$APP_CONTAINER' container, and from any other container on the"
echo "'$DB_NETWORK' network — NOT from the host machine directly,"
echo "since '$DB_CONTAINER' only resolves over that Docker network):"
echo
echo "  $LOCAL_DB_URL"
echo
if [ "$GENERATED_PASSWORD" -eq 1 ]; then
  echo "(password was auto-generated — save this string now, it is not"
  echo " stored anywhere else and won't be printed again)"
  echo
fi
echo "Next steps, in order:"
echo "  1. Build the schema. IMPORTANT: '$DB_CONTAINER' is a Docker"
echo "     container NAME — only resolvable via Docker's own embedded"
echo "     DNS from INSIDE a container on '$DB_NETWORK'. Running plain"
echo "     'python3 custom_scripts/create_local_schema.py' directly on"
echo "     this host WILL fail with a DNS resolution error — it has to"
echo "     run inside a container on that network. Easiest: reuse"
echo "     '$APP_CONTAINER' itself, which is already on that network and"
echo "     already has asyncpg + this app's migration code installed:"
echo "       docker exec $APP_CONTAINER python3 -c \""
echo "         import asyncio"
echo "         from app.db.pool import create_pool, close_pool"
echo "         from app.db.migrate import run_migrations"
echo "         async def main():"
echo "             pool = await create_pool('$LOCAL_DB_URL')"
echo "             try: print(await run_migrations(pool))"
echo "             finally: await close_pool(pool)"
echo "         asyncio.run(main())"
echo "       \""
echo "     (or, if you'd rather use the standalone script: a throwaway"
echo "     container with the repo mounted in works too —"
echo "       docker run --rm --network $DB_NETWORK \\"
echo "         -v \"\$(pwd)\":/app -w /app python:3.11-slim \\"
echo "         bash -c 'pip install -q asyncpg && python3 custom_scripts/create_local_schema.py --database-url \"$LOCAL_DB_URL\"')"
echo "  2. Copy existing data across:"
echo "       REMOTE_DATABASE_URL='<your current remote database_url>' \\"
echo "       LOCAL_DATABASE_URL='$LOCAL_DB_URL' \\"
echo "       DB_NETWORK='$DB_NETWORK' \\"
echo "       ./custom_scripts/copy_remote_data_to_local.sh"
echo "  3. Set LOCAL_DATABASE_URL='$LOCAL_DB_URL' as an env var on the"
echo "     '$APP_CONTAINER' container (docker run -e LOCAL_DATABASE_URL=...,"
echo "     or your redeploy script) and restart it — app/config.py picks"
echo "     this up automatically and overrides whatever database_url"
echo "     was otherwise configured, remote value or not."
echo "================================================================"
