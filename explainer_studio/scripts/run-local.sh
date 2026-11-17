#!/usr/bin/env bash
# Start Explainer Studio on this machine: http://127.0.0.1:${STUDIO_PORT:-47391}
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export EV_HOME="${EV_HOME:-$HOME/.cache/explainer-video}"
export STUDIO_PORT="${STUDIO_PORT:-47391}"
cd "$HERE"
exec "$EV_HOME/venv/bin/uvicorn" app.main:app --host "${STUDIO_HOST:-127.0.0.1}" --port "$STUDIO_PORT"
