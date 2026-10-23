# 02 · The system as built today

Everything here is in `live_deploy/`. Paths below are relative to it.

## Runtime shape

- **One Python 3.11 process**: FastAPI + uvicorn (`run.py` → `app.main`),
  asyncpg to Postgres. One Docker container (`Dockerfile`, `TZ=Asia/Kolkata`
  is set on purpose: an earlier UTC container fired every trade 5.5 h late).
- **One Kite WebSocket** for the whole service (`app/dispatcher.py`,
  `LiveDataDispatcher`). KiteTicker runs in its own thread; ticks cross into
  asyncio with `run_coroutine_threadsafe`; every subscribe/unsubscribe/close
  goes back onto the ticker thread (`_schedule_on_ticker_thread`) because
  kiteconnect isn't thread-safe. Daily Kite login hot-swaps the token without
  a restart.
- **Fan-out** (`app/broadcaster.py`): one bounded queue (1,000 batches) per
  subscriber; when full it drops the *oldest*. Every runner and every open
  screen subscribes. **This is the P0 problem**: a slow consumer works through
  old ticks first (see `09-open-items.md`).
- **DeploymentRunner** (`app/deployments/runner.py`), one per deployment:
  filters ticks to its own instruments, guards them (`TickFilter` in
  `app/deployments/health.py`: non-positive, out of order, duplicate, spike
  held until confirmed; `_is_stale_tick`: older than 5 min or outside
  09:15–15:40), calls the strategy, records fills. `_fill` is the single fill
  path; it stamps `market_context` on each fill (logging only), captures the
  order book price for slippage, and hands the fill to shadow mode.
- **DeploymentManager** (`app/deployments/manager.py`): lifecycle
  (create/pause/resume/stop), resumes every `active` deployment on startup,
  ref-counted dynamic Kite subscriptions, and loops: `snapshot_loop` (equity
  snapshots, 5 min), `marks_loop` (open-leg price marks: 1 min intraday, 5 min
  positional), `candles_loop` (1-minute candles from ticks),
  `candle_backfill_loop` (15:45 Kite history backfill), `watchdog_loop`
  (restart dead runners), `post_market_dump_loop` (state checkpoint).
- **Ops health** (`app/ops_health.py`): every minute turns problems into
  `ops_issues` (kite:login, kite:stall, db, dep:*:down/silent/errors/
  stale-legs, export, daycheck, eod). An open issue alerts every minute (push +
  in-app) until it clears; "Mark as holiday" mutes the day. Also records each
  deployment's state at the open and after the close (`deployment_state_days`)
  and launches the end-of-day check after the 15:45 export.
- **Tick stall self-heal**: if ticks flowed today and then none arrive for
  90 s in market hours, the dispatcher reconnects with the same login (added
  after a real silent stall on 21 Oct at 12:15 IST).

## Strategy contract (`app/deployments/strategy_base.py`)

`on_start(runner)`, `on_tick(runner, tick)`, `on_stop(runner)`,
`get_persistable_state()` (saved whenever it changes; crash recovery depends
on it), `decide(...)` (decision log, `strategy_decisions`, logging only),
`report(...)` (self-reported issues, `strategy_issues`), `signal_context`.
Strategies call `runner.buy()/sell()`; only the runner writes the DB.
Strategies use `app/options/resolver.py` (`OptionsResolver`) for expiries,
ATM (off the future, not spot), strike by premium (with a ±5% liquidity
tie-break by OI), LTP and quotes. **Every Kite REST call goes through one
process-wide throttle of ≥0.35 s per call** with 429 back-off.

Registry: `app/strategies/registry.py` (`@register_strategy`), imported in
`app/strategies/__init__.py`.

## Money

- `app/db/queries.py::record_fill` is the one place positions, lots and cash
  change, in one transaction with row locks. Same-direction fills add a lot;
  an opposite fill must close the whole position (no partial exits). One open
  position per (deployment, instrument) is a DB constraint.
- Cash is a simplification, not a margin model.
- Charges: `app/charges.py`, a line-for-line port of
  `static/js/calculators.js` (verified to the paisa against Zerodha's own
  calculator). Stored per position (`estimated_charges`).
- Slippage: `app/slippage.py`, the fill walked against the 5-level book
  (`position_lots.book_price`, `positions.estimated_slippage`). Fills stay at
  LTP; slippage is recorded alongside.
- **Three P&L bases everywhere**: Gross, After charges, After charges +
  slippage (`pnl`, `pnl_net`, `pnl_net_slip`).
- Prices for open positions: `app/prices.py`: live price, else the last
  recorded mark (so holidays and outages don't show ₹0).

## Data (Postgres, migrations `app/db/migrations/0001…0037`)

Auto-applied on startup (`app/db/migrate.py`, `schema_migrations` table; no
Alembic). Main tables:

| Area | Tables |
|---|---|
| Core | `deployments`, `positions`, `position_lots`, `deployment_events`, `deployment_snapshots`, `deployment_state`, `deployment_state_days` |
| Prices | `candles_1m` (renamed from `index_candles` in 0021; options and futures only, 1 row per contract per minute), `contracts`, `position_marks` |
| Users | `users`, `audit_log`, `user_preferences`, `push_subscriptions`, `alert_dismissals`, `kite_sessions`, `notification_holiday` |
| Config | `strategy_settings`, `strategy_presets`, `tag_catalog`, scratch tables |
| Analysis | `strategy_decisions`, `journal_entries`, `strategy_health_sent` |
| Trust | `ops_issues`, `strategy_issues`, `day_checks`, `day_check_runs`, `repair_jobs`, `repair_drafts`, `stall_repairs`, `shadow_checks`, `freeze_limits` |

Candles: Kite drops option/future history at expiry, so the app stores
options and futures (1-minute, no duplicates; 5/15/60 min rolled up on read).
Indices, stocks and India VIX are **not** stored; Kite keeps their history, so
they are fetched on demand and cached in memory.

## API

- v1 routers in `app/routers/` (deployments, aggregate, cycles, candles,
  instruments, kite_auth, auth, notifications, journal, geek, admin…), used by
  both UIs.
- `/v2` (`app/v2/router.py`) is additive and was written for Linck: book,
  session lanes, strategies catalog, preferences, alerts dismissals,
  close-everything (shorts before longs), create deployment (paused or
  started), restart, ops, strategy-issues, day-checks, repair cases (plan,
  push, undo, dismiss), review, VIX, underlying/futures, shadow.
- Live updates to browsers are **SSE** (`/sse/ticks`, `/sse/events`); the app
  moved off WebSocket in August because screens only listen.
- Auth: session cookies (sliding 2 h idle), `X-API-Key` = `app_auth_secret`
  for scripts. Multi-user with an admin.

## UI

- **Linck** (`static/linck/`): plain ES modules, no build step. See
  `static/linck/README.md` for extension points (themes, preferences, pages,
  tabs, attention rules). Fonts: Funnel Display, Funnel Sans, Martian Mono
  (self-hosted). Themes Paper (default), Slate, Night. Preferences are saved
  per user (`/v2/preferences`).
- Pages: Desk, Deployments, Deployment (tabs Now, Performance, Cycles,
  Trades & replay, Settings), Geek mode (`#/d/:id/geek/:view`), Leaderboard,
  Compare, Reports, Strategies + Strategy page (`#/s/:name`) + deploy wizard,
  Review, Journal, Repair (+ case page), Tools, Account (sectioned).
- **Classic** UI at `/` still exists (Bazaar Ledger style); Linck is default.
  The installed PWA always opens Linck.
- Android: `android/` is a Kotlin/Compose client (Phase 1) that has **never
  been compiled** (no SDK in the sandbox). Not maintained since early Oct.
- Brand: `brand/` (the mark is a terminal prompt that printed a candle). The
  header shows the word "Linck", not the mark.

## Tests and the gate

`./run_tests.sh --e2e` runs Python (unit, db, api, strategy simulations, fault
matrix, crash recovery), JS unit tests and Playwright browser tests in
parallel, and prints **SAFE TO DEPLOY** or **DO NOT DEPLOY**. Details in
`TESTING.md`. In this sandbox use `PYTHON=.venv/bin/python E2E_SHARDS=1
./run_tests.sh --e2e` (two browser shards flake under load here). About 930+
Python tests at the last count.

Key permanent tests:
- `tests/strategies/test_crash_recovery.py`: every strategy killed every few
  minutes makes the same trades as an uninterrupted run.
- `tests/strategies/test_faults.py`: 9 strategies × 12 production failures.
- `tests/strategies/test_decisions_logging_only.py`, `test_conditions_geek.py`:
  recording more data never changes an order.
- `tests/e2e/charts-hover.e2e.mjs`: every Geek chart responds to hover.
- `tests/strategies/test_all_strategies_smoke.py`: new strategies are picked
  up automatically.

Benchmarks (`tests/bench/`) are run by name, not in the gate.

## Deployment (owner only)

The owner's box: Docker container `live-deploy`, local Postgres container on
network `live_deploy_net`, Tailscale Serve in front (`-p 127.0.0.1:8000:8000`).
`./redeploy.sh` = git pull, stop, rm, build, run. A UI button can trigger it
via `redeploy_watcher.sh` on the host. See `13-runbook.md`.
