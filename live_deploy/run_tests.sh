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
if ! "$PY" -c "import pytest, pytest_cov, pytest_timeout, httpx" 2>/dev/null; then
  echo "Missing test tools for $PY — run: $PY -m pip install -r requirements-dev.txt"
  FAILED+=("python: test dependencies not installed")
else
  if [ "$QUICK" = 1 ]; then
    bold "Python: unit tests"
    "$PY" -m pytest tests/unit -p no:cacheprovider || FAILED+=("python unit tests")
    SKIPPED+=("database, API and strategy tests (--quick)")
  else
    bold "Python: unit, database, API and strategy simulations"
    "$PY" -m pytest --require-db -p no:cacheprovider --cov --cov-report=term:skip-covered --cov-report=html \
      ${MIN_COVERAGE:+--cov-fail-under=$MIN_COVERAGE} || FAILED+=("python tests")
  fi
fi

# ── JavaScript (Linck's pure modules) ────────────────────────────────
if command -v node >/dev/null; then
  bold "JavaScript: Linck modules"
  node --test --test-force-exit tests/js/*.test.mjs || FAILED+=("javascript tests")
else
  FAILED+=("javascript tests: node is not installed")
fi

# ── Browser ───────────────────────────────────────────────────────────
if [ "$E2E" = 1 ]; then
  bold "Browser: sign-in, flows, every page"
  tests/e2e/run.sh || FAILED+=("browser tests")
else
  SKIPPED+=("browser tests (run with --e2e)")
fi

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
