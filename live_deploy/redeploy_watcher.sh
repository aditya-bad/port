#!/usr/bin/env bash
# live_deploy — redeploy_watcher.sh: polls for the trigger file the
# running app container writes (POST /admin/redeploy, from Account ->
# Admin Options -> "Redeploy latest version" in the UI — see
# app/routers/admin.py's own REDEPLOY_TRIGGER_PATH comment) and runs
# redeploy.sh for real when it appears.
#
# MUST run directly ON THE HOST — never inside the app's own container.
# This is the entire point of the design, not a preference: the running
# container has no `git`/`docker` CLI and no access to the host's
# Docker daemon, and even granting both of those, redeploy.sh's own
# `docker stop live-deploy` tears down that container's WHOLE PID
# namespace at once — every process in it, including whatever tried to
# run this redeploy from inside it — before `docker build`/`docker run`
# ever executed. Running this loop on the host instead means the thing
# performing the redeploy is never at risk of being killed BY the
# redeploy it's performing.
#
# One-time setup (see RUN_GUIDE.md's "Redeploy from the UI" section for
# the full walkthrough): install this as a systemd service using
# redeploy-watcher.service (same directory) so it survives reboots and
# restarts itself if it ever crashes — a bare `nohup ./redeploy_watcher.sh &`
# gives you neither. Do NOT run this by hand in a terminal you'll close.
#
# Usage: ./redeploy_watcher.sh   (invoked by the systemd unit, not directly)

set -uo pipefail   # deliberately NOT -e: one bad redeploy.sh run should log and keep watching, not kill the watcher itself
cd "$(dirname "$0")"

TRIGGER="control/redeploy.trigger"
LOG="logs/redeploy_watcher.log"
POLL_SECONDS=3

mkdir -p control logs
echo "$(date -Is) redeploy_watcher started (pid $$), watching $TRIGGER every ${POLL_SECONDS}s" >> "$LOG"

while true; do
  if [ -f "$TRIGGER" ]; then
    echo "$(date -Is) trigger detected -- removing it and running redeploy.sh" >> "$LOG"
    rm -f "$TRIGGER"
    if ./redeploy.sh >> "$LOG" 2>&1; then
      echo "$(date -Is) redeploy.sh finished successfully" >> "$LOG"
    else
      echo "$(date -Is) redeploy.sh exited non-zero (see output above) -- the OLD container may already be stopped; check 'docker ps' and this log by hand" >> "$LOG"
    fi
  fi
  sleep "$POLL_SECONDS"
done
