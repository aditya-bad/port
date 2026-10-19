# Testing

One command tells you whether a change is safe to deploy:

```bash
./run_tests.sh          # Python + JS: about 5 minutes
./run_tests.sh --e2e    # plus the browser tests: about 3 more minutes
```

It ends with **SAFE TO DEPLOY** (exit 0) or **DO NOT DEPLOY** (exit 1),
followed by the list of what failed. Deploy only on SAFE TO DEPLOY. Run
`--e2e` before deploying any change to `static/`.

## One-time setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
sudo -u postgres createuser -P t           # password: t
sudo -u postgres createdb -O t t_test
# browser tests only:
(cd tests/e2e && npm install && npx playwright install chromium)
```

The tests use their own database, `TEST_DATABASE_URL` (default
`postgresql://t:t@localhost/t_test`). Its name must contain `test`
because the suite drops and recreates its schema. Your real database is
never touched:

- `config.json` is ignored.
- `DATABASE_URL` and `LOCAL_DATABASE_URL` are both pointed at the test
  database.
- No Kite session exists, so nothing reaches Zerodha.
- Push notifications are captured instead of sent.

## What's covered

| Layer | Folder | What it proves |
|---|---|---|
| Unit | `tests/unit` | Pure logic: charges, slippage, the options resolver (expiries, ATM, strike by premium, liquidity, the Kite rate limit), the tick dispatcher (subscriptions, reconnects, disconnect alerts), cache, config, the v2 builders, the strangle EOD rules, exit safety. |
| Database | `tests/db` | Migrations; the fill engine (every rupee: P&L, cash, charges, slippage, averaging, refusals); every report query; push sending. |
| API | `tests/api` | The real app over HTTP: sign-in, sessions, the middleware's public/private rules, the audit log, every v1 and `/v2` endpoint, the manager (startup resume, watchdog, snapshots, marks, recovery) and the runner (which ticks reach a strategy, errors, notifications). |
| Strategies | `tests/strategies` | Each strategy run through simulated market days with the real runner, resolver and fill engine. Every registered strategy must trade, survive a restart and a missed square-off, and keep a consistent book. DTT and the strangle also have scenario tests (stops, targets, rolls, EOD, checkpoints, expiry). |
| JS | `tests/js` | Linck's pure modules: number and date formatting (IST, ₹), the P&L basis switch, performance metrics, routing, Kite token parsing. |
| Browser | `tests/e2e` | Linck and classic in Chromium against a seeded app: sign-in with Connect Kite, pause and undo, closing legs, the deploy wizard, switching back to classic. Every page is also checked at phone, laptop and desktop widths for script errors, sideways scroll, "undefined/NaN" and overlapping text. |

**Crash recovery** (`tests/strategies/test_crash_recovery.py`) holds
every strategy to two rules:
- Killed every few minutes (no clean shutdown, no final save) and
  restarted from the database, it makes exactly the same trades as an
  uninterrupted run, fill for fill.
- Killed between two fills of one action (half a straddle sold), it
  resumes safely: the book stays consistent, intraday is flat at the
  close, and nothing is left open and forgotten.

A strategy that keeps a new piece of state must add it to
`get_persistable_state` (saved whenever it changes), or rebuild it
from its open positions in `on_start`. These tests fail otherwise.

New strategies are picked up automatically by
`tests/strategies/test_all_strategies_smoke.py` and the API's
"create from defaults" test.

## Running parts

```bash
.venv/bin/python -m pytest tests/unit                      # no database needed
.venv/bin/python -m pytest tests/api/test_v2_api.py -x     # one file, stop at first failure
.venv/bin/python -m pytest -k strangle                     # by name
node --test --test-force-exit tests/js/*.test.mjs
tests/e2e/run.sh
```

Without a test database, the database and API tests are skipped when
you run pytest directly. `run_tests.sh` passes `--require-db`, which
turns those skips into failures, so the gate can't pass by accident.
Coverage is written to `htmlcov/index.html`. Set `MIN_COVERAGE=80` to
also fail below a percentage.

## When something breaks

Every bug found gets a test that fails without the fix. Put the test
next to the closest existing one:

- a strategy decision → `tests/strategies`, using `tests/sim.py`'s
  `Sim`. It drives the real runner minute by minute at IST times.
- an endpoint → `tests/api`, using the `client` fixture (signed in as
  admin) and `make_deployment` / `add_fill` from `tests/conftest.py`.
- a query or the fill engine → `tests/db`, using `pool_run`.
- a pure function → `tests/unit`.
- something only visible in the browser → `tests/e2e`.
