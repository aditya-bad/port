# custom_scripts/

One-off maintenance/admin scripts against a real live_deploy database —
things that don't belong as an app feature (no UI, no API endpoint,
run rarely and deliberately by hand), but still need to touch the same
data the app does.

## Running one

Every script here is standalone: it runs **outside Docker, outside the
app process entirely** — it never imports `app.main` (so it never
starts the dispatcher, a Kite session, or any background loop), just
the two lightweight modules that actually do the work:

- `app/config.py` — `load_config()`, the exact same `DATABASE_URL`
  resolution (environment variable first, `config.json` fallback) the
  real app uses.
- `app/db/queries.py` — the same query functions the API routers call,
  so a script's write goes through the exact same path (COALESCE
  semantics, `updated_at` bump, etc.) a real request would.

That's the whole reason these can run as plain `python3 file.py` from
anywhere — no server needs to be up, no container needs to be running,
just a reachable database and this repo's own `requirements.txt`
already installed.

```bash
cd live_deploy
python3 custom_scripts/<script>.py            # see the script's own --help/docstring for flags
```

(or `./custom_scripts/<script>.py` directly, if it's executable —
`chmod +x` it once if your checkout didn't preserve that bit.)

Each script prints exactly what it's about to change before writing
anything — that printed list is a complete audit trail on its own,
even piped to a log file, with no interactive prompt to get in the way
of a non-interactive run. Most support `--dry-run` to preview with
zero writes.

## Scripts

- **`clean_deployment_names.py`** — strips a fixed list of words
  (`DTT`, `Intraday` today — edit `WORDS_TO_STRIP` at the top of the
  file to change the list) out of every existing deployment's
  `deployment_name`, whole-word and case-insensitive, collapsing the
  whitespace left behind. E.g. `"DTT Straddle Intraday Nifty Simple"`
  → `"Straddle Nifty Simple"`. A rename that would collide with an
  existing `deployment_name` is reported and skipped, not applied —
  the rest of the batch still runs.

- **`register_supertrend_options_strategies.py`** — the one exception
  to "never needs the app server": fetching real Kite historical data
  and validating it is standalone same as everything else here, but
  actually REGISTERING a deployment (`--register`) needs a real
  running app server to pick it up and start a live runner — a bare
  database insert would just leave an orphaned row with nothing
  trading it. Fetches today's real 5-min candles + daily OHLC for
  NIFTY and SENSEX from Kite, computes SuperTrend(7,3) through the
  actual strategy code (imported, not reimplemented), checks it
  against a chart reading you provide, saves everything fetched to
  `custom_scripts/data/*.json` (gitignored), and — only once that
  validation passes, or with `--force` — registers 4 deployments
  (`pivot_supertrend_options` + its inverse, each for NIFTY and
  SENSEX) fully seeded so pivots/SuperTrend are correct from minute
  one instead of needing a cold-start warmup day. See the script's own
  `--help` / module docstring for the full flag list.

- **`resync_supertrend_state.py`** — corrects an ALREADY-REGISTERED
  deployment's persisted SuperTrend state after it's drifted from
  reality, which can happen because `CandleAggregator` has no built-in
  way to notice a missing tick window (a WebSocket reconnect can
  silently skip whole 5-min candles — SuperTrend is recursive, so one
  skipped candle permanently shifts every value after it away from what
  a real chart shows). Fetches gap-free 5-min candles straight from
  Kite's REST `historical_data` (not the WS tick stream) for the last
  `--lookback-days` (default 7) through right now, replays them through
  a fresh `SuperTrendState`, and overwrites just the
  `supertrend`/`prev_trend` fields in `deployment_state` — pivots and
  `prev_day_ohlc` are untouched (they come from a single daily OHLC
  read, never exposed to this failure mode). Defaults to all 4 standard
  `ST_PV_*` names; `--deployment-name` scopes to one, `--dry-run`
  previews without writing. **Does not affect a currently-running
  deployment's in-memory state** — the deployment must be paused then
  resumed (or the app redeployed) for the corrected state to actually
  take effect; the script prints this reminder itself whenever it
  resyncs a deployment it finds `active`.
