#!/usr/bin/env bash
# Browser tests. Starts E2E_SHARDS copies of the app (default 2), each on
# its own port and its own seeded database (<test db>_e2e0, _e2e1, …), and
# runs the *.e2e.mjs specs across them: each server takes the next spec
# from one queue, heaviest first, so they finish together. Needs node,
# `npm install` here, and a Chromium for Playwright
# (`npx playwright install chromium`, or E2E_CHROMIUM=/path/to/chrome).
#
#   E2E_SHARDS=1 tests/e2e/run.sh            one server, specs one by one
#   E2E_SPECS="./geek.e2e.mjs" tests/e2e/run.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-python3}"
PORT0="${E2E_PORT:-8765}"
SHARDS="${E2E_SHARDS:-2}"

[ -d "$HERE/node_modules/playwright" ] || (cd "$HERE" && npm install --silent --no-audit --no-fund) || {
  echo "e2e: could not install playwright (npm install in tests/e2e)"; exit 2; }

# Heaviest first (measured); specs not listed go last.
ORDER="pages geek history legs login brand flow charts"
if [ -n "${E2E_SPECS:-}" ]; then SPECS=($E2E_SPECS)
else
  SPECS=()
  for n in $ORDER; do [ -f "$HERE/$n.e2e.mjs" ] && SPECS+=("./$n.e2e.mjs"); done
  for f in "$HERE"/*.e2e.mjs; do
    b="./$(basename "$f")"; [[ " ${SPECS[*]} " == *" $b "* ]] || SPECS+=("$b")
  done
fi
[ "$SHARDS" -gt "${#SPECS[@]}" ] && SHARDS=${#SPECS[@]}

suffixes=(); for ((i = 0; i < SHARDS; i++)); do suffixes+=("e2e$i"); done
(cd "$ROOT" && "$PY" tests/dbs.py "${suffixes[@]}") || {
  echo "e2e: could not create the test databases (the test role needs CREATEDB)"; exit 2; }
BASE_DB="${TEST_DATABASE_URL:-postgresql://t:t@localhost/t_test}"

PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; rm -f "$HERE"/.queue*; }
trap cleanup EXIT

# Start every server, then wait until all are ready.
for ((i = 0; i < SHARDS; i++)); do
  db="$(cd "$ROOT" && "$PY" -c "from tests.dbs import url_for; print(url_for('e2e$i', '$BASE_DB'))")"
  TEST_DATABASE_URL="$db" E2E_PORT=$((PORT0 + i)) E2E_IDS="$HERE/.ids-$i.json" \
    "$PY" "$HERE/serve.py" > "$HERE/server-$i.log" 2>&1 &
  PIDS+=($!)
done
for ((i = 0; i < SHARDS; i++)); do
  for _ in $(seq 1 120); do
    grep -q "E2E READY" "$HERE/server-$i.log" 2>/dev/null && break
    kill -0 "${PIDS[$i]}" 2>/dev/null || { echo "e2e: server $i failed to start:"; tail -30 "$HERE/server-$i.log"; exit 2; }
    sleep 0.5
  done
  grep -q "E2E READY" "$HERE/server-$i.log" || { echo "e2e: server $i not ready after 60s"; tail -30 "$HERE/server-$i.log"; exit 2; }
done

# The queue: one spec per line; workers take the next under a lock.
Q="$HERE/.queue"; printf '%s\n' "${SPECS[@]}" > "$Q"; : > "$Q.lock"
next() { flock "$Q.lock" sh -c 'head -n1 "$1"; sed -i 1d "$1"' _ "$Q"; }
worker() {
  local i=$1 rc=0 spec
  while spec="$(next)"; [ -n "$spec" ]; do
    echo "── $spec (server $i)"
    (cd "$HERE" && E2E_IDS="$HERE/.ids-$i.json" node --test --test-force-exit --test-concurrency=1 "$spec") || rc=1
  done
  return $rc
}
WPIDS=()
for ((i = 0; i < SHARDS; i++)); do worker "$i" > "$HERE/shard-$i.log" 2>&1 & WPIDS+=($!); done
RC=0
for ((i = 0; i < SHARDS; i++)); do
  wait "${WPIDS[$i]}" || RC=1
  cat "$HERE/shard-$i.log"
done
exit $RC
