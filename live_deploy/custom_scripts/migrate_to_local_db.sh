#!/usr/bin/env bash
# live_deploy — custom_scripts/migrate_to_local_db.sh
#
# THE ONE-SHOT VERSION of moving off a remote-hosted DB (Neon, etc.)
# onto one running alongside this app's own server. Runs
# setup_local_postgres.sh, create_local_schema.py, and
# copy_remote_data_to_local.sh back to back, non-interactively, then
# rewrites config.json's own database_url in place to point at the new
# local database. Read those three scripts' own headers for exactly
# what each step does and why — this is just the glue that chains them
# with no prompts in between and no values to re-type between steps.
#
# Run this ON THE HOST (same machine `docker run`/your redeploy script
# runs from) — config.json lives on the host filesystem and is bind-
# mounted read-only INTO the app container (see the Dockerfile's own
# header), so this script edits the host's copy, which the container
# picks up on its NEXT restart, not immediately (a read-only mount
# can't be written to from inside the container either way).
#
# WHAT THIS DOES NOT NEED FROM YOU: the remote database_url to migrate
# FROM — it reads that straight out of config.json itself, whatever's
# there right now, so you don't have to go find and re-paste it.
#
# USAGE:
#   cd live_deploy
#   ./custom_scripts/migrate_to_local_db.sh
#
# Every setting below is overridable via environment variable; sane
# defaults otherwise. Set DB_PASSWORD yourself if you want a specific
# one — otherwise a random one is generated once, used for both the new
# Postgres container and the config.json entry, and printed at the end
# (nowhere else — save it somewhere, e.g. a password manager).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONFIG_PATH="${CONFIG_PATH:-./config.json}"
APP_CONTAINER="${APP_CONTAINER:-live-deploy}"
DB_CONTAINER="${DB_CONTAINER:-live-deploy-db}"
DB_NETWORK="${DB_NETWORK:-live_deploy_net}"
DB_USER="${DB_USER:-liveuser}"
DB_NAME="${DB_NAME:-live_deploy}"
export APP_CONTAINER DB_CONTAINER DB_NETWORK DB_USER DB_NAME

if [ ! -f "$CONFIG_PATH" ]; then
  echo "ERROR: config.json not found at '$CONFIG_PATH'." >&2
  echo "  Run this from the live_deploy directory, or set CONFIG_PATH=" >&2
  echo "  to point at wherever your real config.json actually lives." >&2
  exit 1
fi

# Same password used for BOTH the new Postgres container (step 1) and
# the config.json entry written at the end — generated ONCE, here, up
# front, rather than letting setup_local_postgres.sh generate its own
# independently (which this script would then have no way to recover
# for the final config.json write without parsing its output).
if [ -z "${DB_PASSWORD:-}" ]; then
  DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  GENERATED_PASSWORD=1
else
  GENERATED_PASSWORD=0
fi
export DB_PASSWORD

REMOTE_DATABASE_URL="$(python3 -c "
import json
with open('$CONFIG_PATH') as f:
    print(json.load(f).get('database_url', ''))
")"
if [ -z "$REMOTE_DATABASE_URL" ]; then
  echo "ERROR: no database_url found in '$CONFIG_PATH' — nothing to migrate from." >&2
  exit 1
fi

LOCAL_DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_CONTAINER}:5432/${DB_NAME}"

echo "================================================================"
echo " live_deploy: fully automated local-DB migration"
echo "================================================================"
echo "  config file          : $CONFIG_PATH"
echo "  migrating FROM       : ${REMOTE_DATABASE_URL%%@*}@*** (current config.json)"
echo "  migrating TO         : ${LOCAL_DATABASE_URL%%@*}@*** ($DB_CONTAINER on $DB_NETWORK)"
echo "================================================================"
echo

echo ">>> Step 1/4 — setting up local Postgres container"
"$SCRIPT_DIR/setup_local_postgres.sh"
echo

echo ">>> Step 2/4 — building schema on the local database"
python3 "$SCRIPT_DIR/create_local_schema.py" --database-url "$LOCAL_DATABASE_URL"
echo

echo ">>> Step 3/4 — copying existing data across"
REMOTE_DATABASE_URL="$REMOTE_DATABASE_URL" \
LOCAL_DATABASE_URL="$LOCAL_DATABASE_URL" \
DB_NETWORK="$DB_NETWORK" \
AUTO_CONFIRM=1 \
  "$SCRIPT_DIR/copy_remote_data_to_local.sh"
echo

echo ">>> Step 4/4 — updating $CONFIG_PATH's database_url"
BACKUP_PATH="${CONFIG_PATH}.bak.$(date +%Y%m%dT%H%M%S)"
cp "$CONFIG_PATH" "$BACKUP_PATH"
python3 -c "
import json

path = '$CONFIG_PATH'
with open(path) as f:
    cfg = json.load(f)
cfg['database_url'] = '$LOCAL_DATABASE_URL'
with open(path, 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
"
echo "  backed up the previous config to: $BACKUP_PATH"
echo "  $CONFIG_PATH's database_url now points at the local database."
echo

echo "================================================================"
echo " Migration complete."
echo "================================================================"
echo "  Local database_url: $LOCAL_DATABASE_URL"
if [ "$GENERATED_PASSWORD" -eq 1 ]; then
  echo
  echo "  (password was auto-generated — save it somewhere else too,"
  echo "   e.g. a password manager; it is only stored in config.json"
  echo "   and this terminal's own scrollback from here on)"
fi
echo
echo "  RESTART THE APP CONTAINER to actually pick this up —"
echo "  config.json is bind-mounted read-only, so editing the host"
echo "  file alone (what this script just did) is not enough on its"
echo "  own; the container has to re-read it on startup:"
echo
echo "    docker restart $APP_CONTAINER"
echo
echo "  Sanity-check a few row counts (deployments, positions,"
echo "  position_lots) against the remote DB before you fully trust"
echo "  this and stop paying for/using the remote one."
echo "================================================================"
