#!/usr/bin/env bash
# live_deploy — custom_scripts/copy_remote_data_to_local.sh
#
# Step 3 of moving off a remote-hosted DB onto one running alongside
# this app's own server. Copies every ROW from the remote database into
# the local one — schema/table structure is NOT touched here; run
# custom_scripts/create_local_schema.py against the local DB FIRST (this
# script assumes every table already exists there with matching
# columns, and will fail loudly if it doesn't).
#
# Uses `pg_dump --data-only | psql`, via a THROWAWAY `postgres:16`
# container rather than needing pg_dump/psql installed on the host —
# borrows the exact same client tools bundled in the image
# setup_local_postgres.sh already pulled. Runs on the SAME Docker
# network as the local DB container so it can resolve it by name; still
# has normal internet egress too, needed to reach the remote (Neon,
# etc.) database.
#
# --disable-triggers: skips foreign-key trigger checks during the
# data-only load, so rows can be inserted in whatever order pg_dump
# happens to emit them without failing on a not-yet-inserted parent row
# — safe here because we're loading into a schema whose constraints are
# already known-correct (just created fresh by create_local_schema.py),
# not validating untrusted data. Requires connecting as the table
# owner/a superuser on the LOCAL side — true by default here, since
# DB_USER is the same role that initialized this Postgres instance in
# setup_local_postgres.sh (Postgres makes the image's POSTGRES_USER a
# superuser of a freshly-initialized cluster automatically).
#
# --exclude-table=schema_migrations: CONFIRMED NECESSARY BY ACTUALLY
# RUNNING THIS, not assumed -- without it, this fails with "duplicate
# key value violates unique constraint schema_migrations_pkey", because
# create_local_schema.py (step 2, run before this) already populated
# that table itself by applying every migration file locally. That
# table is migration-APPLICATION history, not application data --
# copying the remote's rows over is never correct here, both sides
# should already agree on it since both run the same migration files.
#
# USAGE:
#   REMOTE_DATABASE_URL='postgresql://user:pass@ep-xxxx.neon.tech/dbname?sslmode=require' \
#   LOCAL_DATABASE_URL='postgresql://liveuser:pw@live-deploy-db:5432/live_deploy' \
#   ./custom_scripts/copy_remote_data_to_local.sh
#
# (both values are printed by setup_local_postgres.sh / already known
# from your current config.json's database_url for the remote one)
set -euo pipefail

: "${REMOTE_DATABASE_URL:?Set REMOTE_DATABASE_URL to your current (remote) database_url}"
: "${LOCAL_DATABASE_URL:?Set LOCAL_DATABASE_URL to the local DB's connection string (see setup_local_postgres.sh's output)}"
DB_NETWORK="${DB_NETWORK:-live_deploy_net}"
DB_IMAGE="${DB_IMAGE:-postgres:16}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed / not on PATH." >&2
  exit 1
fi

echo "== live_deploy: copying data from remote -> local =="
echo "  remote : ${REMOTE_DATABASE_URL%%@*}@***  (host/creds hidden)"
echo "  local  : ${LOCAL_DATABASE_URL%%@*}@***"
echo "  network: $DB_NETWORK"
echo
echo "This copies DATA ONLY — the local database's tables must already"
echo "exist (run create_local_schema.py first if you haven't)."
echo

# AUTO_CONFIRM=1 skips the interactive prompt -- set by
# migrate_to_local_db.sh when running this as one step of the full
# automated pipeline; direct/manual invocation still prompts by default.
if [ "${AUTO_CONFIRM:-}" != "1" ]; then
  read -r -p "Continue? [y/N] " confirm
  if [ "${confirm,,}" != "y" ]; then
    echo "Aborted."
    exit 1
  fi
fi

echo "-- running pg_dump (remote, data-only) piped into psql (local)..."
docker run --rm \
  --network "$DB_NETWORK" \
  -e REMOTE_DATABASE_URL="$REMOTE_DATABASE_URL" \
  -e LOCAL_DATABASE_URL="$LOCAL_DATABASE_URL" \
  "$DB_IMAGE" \
  bash -c '
    set -euo pipefail
    pg_dump --data-only --no-owner --no-privileges --disable-triggers \
      --exclude-table=schema_migrations \
      --dbname="$REMOTE_DATABASE_URL" \
      | psql --set ON_ERROR_STOP=on --dbname="$LOCAL_DATABASE_URL"
  '

echo
echo "-- done. Sanity-check row counts on both sides, e.g.:"
echo "     docker exec live-deploy-db psql -U \"\$DB_USER\" -d \"\$DB_NAME\" -c 'SELECT count(*) FROM deployments;'"
echo "   against the same query on your remote DB, for a few key tables"
echo "   (deployments, positions, position_lots) before cutting over."
