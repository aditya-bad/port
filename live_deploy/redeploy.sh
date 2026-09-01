#!/usr/bin/env bash
# live_deploy — redeploy.sh: pull the latest code, rebuild the Docker
# image, and restart the container, in one step. Run this ON THE
# DEPLOYMENT BOX itself (not in CI, not locally against a remote host)
# — it operates on whatever's checked out in this directory and
# whatever Docker daemon is reachable from here.
#
# Usage:
#   ./redeploy.sh              # git pull, then rebuild + restart
#   ./redeploy.sh --no-pull    # skip git pull — rebuild + restart
#                                 against whatever's already checked
#                                 out (useful right after you've hand-
#                                 edited something on the box, or
#                                 already pulled a moment ago)
#
# `-p 127.0.0.1:8000:8000` below assumes the Tailscale Serve setup from
# RUN_GUIDE.md (the app only needs to be reachable from this box's own
# loopback interface; `tailscale serve` is the actual tailnet-facing
# entry point). If you're instead binding straight to the tailnet
# interface (the earlier, simpler setup, no Tailscale Serve), change
# that one line to `-p $(tailscale ip -4):8000:8000`.
#
# This script is also what redeploy_watcher.sh calls when someone uses
# the app's own Account -> Admin Options -> "Redeploy latest version"
# button (Step 112) -- see that script's own header comment, and
# app/routers/admin.py's REDEPLOY_TRIGGER_PATH comment, for the full
# reasoning on why the running container can't just do this itself.

set -euo pipefail

# Always operate on THIS script's own directory, regardless of where
# it's invoked from — same reasoning as run.py's own docstring on why
# location-independence beats "assume the caller cd'd here first".
cd "$(dirname "$0")"

pull=true
for arg in "$@"; do
  case "$arg" in
    --no-pull) pull=false ;;
    *) echo "Unknown option: $arg (only --no-pull is supported)" >&2; exit 1 ;;
  esac
done

if $pull; then
  echo "==> git pull"
  git pull
else
  echo "==> Skipping git pull (--no-pull)"
fi

echo "==> Stopping and removing the existing container (if any)"
# `|| true` on both: a first-ever deploy has no existing container to
# stop/remove, and that's not an error worth aborting the script over
# (set -e would otherwise treat docker's "no such container" as fatal).
docker stop live-deploy > /dev/null 2>&1 || true
docker rm live-deploy > /dev/null 2>&1 || true

echo "==> Building image"
docker build -t live-deploy .

echo "==> Starting container"
# control/ (read-write, NOT :ro) is Step 112's redeploy-from-the-UI
# trigger handoff -- the running container writes control/redeploy.trigger
# when someone uses Account -> Admin Options -> Redeploy, and
# redeploy_watcher.sh (running on THIS host, not in any container --
# see its own header comment for exactly why) notices the file and
# re-runs this exact script. Created automatically by `docker run`'s
# own bind-mount behavior if it doesn't exist yet.
mkdir -p control
docker run -d --name live-deploy --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v "$(pwd)/config.json:/app/config.json:ro" \
  -v "$(pwd)/tokens.json:/app/tokens.json:ro" \
  -v "$(pwd)/control:/app/control" \
  live-deploy

# Re-attach to the local-Postgres Docker network, if one was ever set
# up (custom_scripts/setup_local_postgres.sh) -- CONFIRMED NECESSARY,
# not theoretical: docker run above creates a brand-new container with
# NO network membership beyond the default bridge, so a database_url
# in config.json pointing at a container NAME (e.g. live-deploy-db,
# only resolvable via Docker's own embedded DNS from another container
# on the SAME user-defined network) would otherwise silently stop
# resolving on every single redeploy, even though config.json itself
# still has the right value. `|| true`: a deployment that never set up
# a local DB (still on a remote one) has no such network to connect
# to, and that's not an error worth aborting the script over.
if docker network inspect live_deploy_net >/dev/null 2>&1; then
  echo "==> Reattaching to live_deploy_net (local Postgres)"
  docker network connect live_deploy_net live-deploy || true
fi

echo "==> Done:"
docker ps --filter "name=live-deploy"
