#!/usr/bin/env bash
# Deploy gate. Runs every test layer and ends with one verdict:
#   SAFE TO DEPLOY      every layer passed
#   DO NOT DEPLOY       something failed (listed above the verdict)
#
#   ./run_tests.sh            Python (unit, database, API, strategy sims) + JS
#   ./run_tests.sh --e2e      ... plus the browser tests (needs Playwright + Chromium)
#   ./run_tests.sh --quick    Python unit tests and JS only (no database)
#
# Needs a Postgres test database whose name contains "test":
#   TEST_DATABASE_URL (default postgresql://t:t@localhost/t_test)
#   e.g.  sudo -u postgres createuser -P t && sudo -u postgres createdb -O t t_test
# Runs in parallel: Python on PY_WORKERS workers (default: one per CPU) and,
# with --e2e, the browser tests on E2E_SHARDS servers (default 2) at the
# same time. Each worker/server has its own database beside the test one
# (t_test_gw0…, t_test_e2e0…), so the role needs CREATEDB:
#   sudo -u postgres psql -c 'ALTER ROLE t CREATEDB'
# Without it the Python tests run on one worker. PY_WORKERS=0 forces that.
# Python: $PYTHON, else .venv/bin/python, else python3, with requirements-dev.txt installed.
# Coverage report: htmlcov/index.html. Set MIN_COVERAGE to fail below a percentage.

set -u
cd "$(dirname "$0")"

E2E=0 QUICK=0
for a in "$@"; do
  case "$a" in
    --e2e) E2E=1 ;;
    --quick) QUICK=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
else PY=python3; fi
export PYTHON="$PY"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
FAILED=() SKIPPED=()

# ── Python ────────────────────────────────────────────────────────────
# The Python and browser layers run side by side (their logs are shown
# when each finishes); JavaScript runs meanwhile.
LOGS="$(mktemp -d)"; trap 'rm -rf "$LOGS"' EXIT
START=$(date +%s)
PY_PID="" E2E_PID=""
if ! "$PY" -c "import pytest, pytest_cov, pytest_timeout, httpx, xdist" 2>/dev/null; then
  echo "Missing test tools for $PY — run: $PY -m pip install -r requirements-dev.txt"
  FAILED+=("python: test dependencies not installed")
elif [ "$QUICK" = 1 ]; then
  bold "Python: unit tests"
  "$PY" -m pytest tests/unit -p no:cacheprovider -n "${PY_WORKERS:-auto}" || FAILED+=("python unit tests")
  SKIPPED+=("database, API and strategy tests (--quick)")
else
  W="${PY_WORKERS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}"
  if [ "$W" != 0 ]; then
    dbs=(); for ((i = 0; i < W; i++)); do dbs+=("gw$i"); done
    "$PY" tests/dbs.py "${dbs[@]}" 2>"$LOGS/dbs" || {
      echo "note: $(cat "$LOGS/dbs") — running Python tests on one worker"; W=0; }
  fi
  echo "Python: unit, database, API and strategy simulations ($([ "$W" = 0 ] && echo 1 || echo "$W") workers)…"
  "$PY" -m pytest --require-db -p no:cacheprovider -n "$W" --cov --cov-report=term:skip-covered --cov-report=html \
    ${MIN_COVERAGE:+--cov-fail-under=$MIN_COVERAGE} > "$LOGS/py" 2>&1 &
  PY_PID=$!
fi

if [ "$E2E" = 1 ]; then
  echo "Browser: sign-in, flows, every page (${E2E_SHARDS:-2} servers)…"
  tests/e2e/run.sh > "$LOGS/e2e" 2>&1 &
  E2E_PID=$!
else
  SKIPPED+=("browser tests (run with --e2e)")
fi

# ── JavaScript (Linck's pure modules) ────────────────────────────────
if command -v node >/dev/null; then
  bold "JavaScript: Linck modules"
  node --test --test-force-exit tests/js/*.test.mjs || FAILED+=("javascript tests")
else
  FAILED+=("javascript tests: node is not installed")
fi

if [ -n "$PY_PID" ]; then
  wait "$PY_PID"; rc=$?
  bold "Python: unit, database, API and strategy simulations"
  cat "$LOGS/py"
  [ $rc = 0 ] || FAILED+=("python tests")
fi
if [ -n "$E2E_PID" ]; then
  wait "$E2E_PID"; rc=$?
  bold "Browser: sign-in, flows, every page"
  cat "$LOGS/e2e"
  [ $rc = 0 ] || FAILED+=("browser tests")
fi
echo "took $(( $(date +%s) - START ))s"

# ── Verdict ───────────────────────────────────────────────────────────
echo
for s in "${SKIPPED[@]}"; do echo "skipped: $s"; done
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '\033[1;32mSAFE TO DEPLOY\033[0m — every test layer passed.\n'
  exit 0
fi
for f in "${FAILED[@]}"; do printf '\033[31mfailed:\033[0m %s\n' "$f"; done
printf '\033[1;31mDO NOT DEPLOY\033[0m — fix the failures above first.\n'
exit 1
