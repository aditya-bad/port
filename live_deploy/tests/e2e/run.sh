#!/usr/bin/env bash
# Browser tests: seeds the test database, starts the app on $E2E_PORT,
# runs every *.e2e.mjs, stops the app. Needs node, `npm install` here,
# and a Chromium for Playwright (`npx playwright install chromium`, or
# E2E_CHROMIUM=/path/to/chrome).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-python3}"
export E2E_PORT="${E2E_PORT:-8765}"
LOG="$HERE/server.log"

[ -d "$HERE/node_modules/playwright" ] || (cd "$HERE" && npm install --silent --no-audit --no-fund) || {
  echo "e2e: could not install playwright (npm install in tests/e2e)"; exit 2; }

"$PY" "$HERE/serve.py" > "$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null' EXIT
for _ in $(seq 1 60); do
  grep -q "E2E READY" "$LOG" 2>/dev/null && break
  kill -0 $PID 2>/dev/null || { echo "e2e: server failed to start:"; tail -30 "$LOG"; exit 2; }
  sleep 0.5
done
grep -q "E2E READY" "$LOG" || { echo "e2e: server not ready after 30s"; tail -30 "$LOG"; exit 2; }

cd "$HERE" && node --test --test-force-exit --test-concurrency=1 ${E2E_SPECS:-./*.e2e.mjs}
