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

- **`fix_strangle_instrument_tokens.py`** — another exception to "never
  needs the app server," for two separate reasons: it needs a live
  Kite session (fetches NSE/BSE's real instrument masters to resolve
  each affected deployment's correct spot `instrument_token` — not
  guessed, not hardcoded, BANKEX in particular isn't in `tokens.json`
  at all) AND, after fixing the DB, calls the app's own API
  (`http://localhost:8000`) to Pause then Resume every deployment it
  touched, since a running deployment doesn't re-read its own config
  live. Run it via `docker exec live-deploy python3
  custom_scripts/fix_strangle_instrument_tokens.py` — needs to run
  INSIDE the container for both the local DB (Docker-network-only
  hostname) and the running app server (`localhost:8000`) to actually
  be reachable. Fixes a real bug: a `strangle_monthly_v2` deployment
  with an empty/missing `config.instrument_tokens` receives zero ticks
  ever (see that strategy's own `on_start`) and sits "active" with 0
  positions forever, completely silently — this finds every such
  deployment, resolves the right token, and fixes it end to end.
  `--dry-run` previews with zero writes.

- **`remove_todays_trades.py`** — "undo a whole calendar day, everywhere,
  correctly": deletes every trade dated `--date` (required, no default —
  a destructive op shouldn't have a "today" convenience default) across
  EVERY deployment, intraday and positional alike, and un-does everything
  that trade touched, not just the position rows. Built for a real
  incident: `force_exit_time` silently failed to fire for several
  intraday deployments and the eventual cleanup flatten used a stale/
  mistimed LTP as the exit price — the day's booked P&L was provably
  wrong, and the fix was "delete the whole day, we're OK losing it."
  Three things a bare `DELETE FROM positions` would get wrong, all
  handled here: (1) **cash** — sums `record_fill`'s own exact
  `-(qty*price)`/`+(qty*price)` formula over every `position_lots` row
  dated `--date` and reverses that exact total, correct regardless of
  whether those lots were opens, adds, or closes; (2) **persisted
  strategy state** (`deployment_state` — cycle_id/entered_ever/etc.,
  NOT re-derived from `positions` on its own) — cleared per affected
  deployment so `on_start`/`_resume_from_db` reconstructs fresh from
  whatever real history is left; (3) **live in-memory runner state** —
  same reasoning as every other script here: Pause+Resume via the app's
  own API afterward for every deployment it touched, hands-off. Also
  clears that day's `deployment_events` and `deployment_snapshots` for
  touched deployments so the Activity tab/equity curve don't keep
  showing values for trades that no longer exist. Dates are compared in
  IST (`AT TIME ZONE 'Asia/Kolkata'`), matching every other day-boundary
  concept in this app.

  ONE case it deliberately does NOT auto-handle: a position opened on an
  EARLIER day that also picked up a lot on `--date` (e.g. a same-day
  adjustment on an older multi-day position) — reconstructing that
  position's pre-`--date` qty/avg_entry_price would mean replaying its
  remaining older lots with the same averaging math `record_fill` itself
  uses, and this script doesn't guess at that. It detects the case,
  per-deployment, and ABORTS ONLY that deployment (every other
  deployment in the run is untouched by one abort) — printed clearly,
  left for manual handling. `--dry-run` previews the full plan (every
  deployment's cash before/after, position/lot counts, and which ones
  would abort and why) with zero writes.

  Verified end-to-end against a real local Postgres in the sandbox with
  4 seeded cases (fully same-day open+close, same-day still-open,
  the mixed-day abort case, and an untouched control with no trades
  that day) and the app's pause/resume API calls mocked: both
  same-day cases end with `current_cash` restored to EXACTLY their
  `initial_capital` and zero remaining positions; the mixed-day
  deployment and the untouched control both come out completely
  byte-for-byte unchanged.

- **`recreate_deployment_clean.py`** — "delete and re-register, same
  config": force-stops, deletes, then recreates one or more deployments
  by exact `deployment_name`, capturing the old row's
  strategy_name/mode/initial_capital/config/notes from the database
  itself right before deleting so the new one can never accidentally
  drift from what was actually running. Built for a real incident: a
  deployment that placed bogus trades off a stale subscribe-time tick
  (see `app/deployments/runner.py`'s `_is_stale_tick`) — flattening
  alone still leaves those bogus closed positions counted forever in
  `win_rate_pct`/`total_realized_pnl` (no reason-based filtering
  anywhere in those queries), so this instead deletes the deployment
  outright (cascades away every position/event/snapshot row under the
  old id) and creates a genuinely new one — new id, `entered_ever=False`,
  zero trade history, identical config. Goes through the running app's
  own API for every step (stop with `force_close=true`, delete, create),
  same reasoning as `fix_strangle_instrument_tokens.py`'s pause/resume —
  these have real in-process side effects a raw DB write would skip.
  `--dry-run` prints the captured config for every matched name with
  zero writes.

- **`clean_deployment_names.py`** — strips a fixed list of words
  (`DTT`, `Intraday` today — edit `WORDS_TO_STRIP` at the top of the
  file to change the list) out of every existing deployment's
  `deployment_name`, whole-word and case-insensitive, collapsing the
  whitespace left behind. E.g. `"DTT Straddle Intraday Nifty Simple"`
  → `"Straddle Nifty Simple"`. A rename that would collide with an
  existing `deployment_name` is reported and skipped, not applied —
  the rest of the batch still runs.

- **`register_supertrend_options_strategies.py`** — the one exception
  to "never needs the app server": actually REGISTERING a deployment
  (`--register`) needs a real running app server to pick it up and
  start a live runner — a bare database insert would just leave an
  orphaned row with nothing trading it. Registers 4 deployments
  (`pivot_supertrend_options` + its inverse, each for NIFTY and
  SENSEX) with NO seed in their config at all: every `pivot_supertrend*`
  strategy self-seeds live from Kite's own REST API the instant its
  `on_start` runs (see the main README's Step 80), so nothing needs
  fetching or validating up front any more — this script's only job is
  building the 4 configs and POSTing them. Used to ALSO fetch today's
  candles and pre-validate SuperTrend against a chart reading before
  registering (a leftover from when it was the seed source); dropped
  entirely (Step 89) once that made it pure duplicate work — needs no
  Kite session, no database, nothing but `app/config.py` at all in its
  default dry-run mode, which just prints the 4 deployments' exact
  config with nothing created. Capital: 250000 for each
  `pivot_supertrend_options`, 100000 for each `..._inverse` (Step 90).
  The inverse pair also gets an immediate follow-up
  `PATCH .../include_in_reports=false` right after each one registers
  — `POST /deployments`' own `DeploymentCreate` schema has no
  `include_in_reports` field at all (only `PATCH` does), so this can't
  be set in the same create call; the script does it as a second
  request instead of skipping it outright. See the script's own
  `--help` / module docstring for the full flag list.

- **`validate_supertrend_pivots.py`** — a diagnostic, not a mutation:
  independently re-derives SuperTrend + pivot values for a
  `pivot_supertrend*` deployment straight from Kite's REST API (the
  EXACT SAME `fetch_seed_from_kite`/`SuperTrendState`/`compute_pivots`/
  `supertrend_status_fields` code the strategy and
  `GET /deployments/{id}/strategy-status` itself both run — imported,
  not reimplemented) and compares it against what that live endpoint is
  CURRENTLY reporting for `ST_PV_NIFTY`/`ST_PV_SENSEX` (or any
  `--deployment` you name). Computes TWO candidate answers — pivots as
  of before vs. after today's 15:45 IST post-market checkpoint, since
  which one is correct depends on whether that's already run today, not
  something knowable in advance — and reports which one (if either) the
  live API matches. Every intermediate number (candle count, exact
  `prev_day_ohlc` used, raw `SuperTrendState` fields, both candidates'
  full field lists, the API's own raw response) is printed either way,
  by design: this is meant to be copy-pasteable as-is into a bug report
  and be enough on its own to diagnose from, not just a pass/fail.
  Needs the app server running (it hits the real endpoint, not the
  database directly) plus a real Kite session. See the script's own
  `--help` / module docstring for the full flag list.

- **`clone_straddle_strategies_banknifty_sensex.py`** — clones every
  existing NIFTY deployment of `intraday_dtt_simple`,
  `intraday_dtt_advanced`, and `intraday_dtt_adjusted` into a BANKNIFTY
  version and a SENSEX version each, fetched straight from the
  database (so "same params" always means whatever that NIFTY
  deployment is ACTUALLY running with right now, never a guessed/stale
  hardcoded copy) — only `instrument_tokens`/`symbol`/
  `options_underlying`/`deployment_name` swapped for the new
  underlying. Also the one exception (alongside
  `register_supertrend_options_strategies.py`) needing the app server
  for `--register`, same reasoning. BANKNIFTY gets ONE live check
  before being included at all: NSE discontinued its weekly options,
  so `expiry_selector="THIS_WEEK"` for it now mechanically resolves to
  "the nearest listed (monthly) expiry" — already true generically,
  with zero BANKNIFTY-specific code anywhere (see the script's own
  module docstring for exactly why `OptionsResolver`'s own expiry
  resolution already handles this with no branch needed). If that
  check ever comes back negative, BANKNIFTY clones are skipped
  entirely for that run (SENSEX still proceeds) rather than either
  guessing or bolting on special-case handling.

- **`generate_vapid_keys.py`** — the other exception to "touches the
  database": doesn't touch it at all, and needs no app server either.
  Generates the one-time VAPID keypair mobile push notifications
  (Step 85) need and prints exactly what to paste into `config.json`
  (or the equivalent env vars). Run it ONCE ever per deployment —
  regenerating later breaks push for everyone already subscribed until
  they re-enable it, since every subscription is tied to the specific
  keypair active when they tapped "Enable notifications." See its own
  module docstring for the full byte-format rationale.

### Moving off a remote-hosted database onto a local one

**`migrate_to_local_db.sh`** — the one-shot version. Run this, from the
host (same machine your `docker run`/redeploy script runs from, since
it needs to edit the host's own `config.json` — see its own header):

```bash
cd live_deploy
./custom_scripts/migrate_to_local_db.sh
```

It reads the CURRENT `database_url` straight out of `config.json` (you
don't re-type it), then runs all four steps below non-interactively:
spins up a local Postgres container, builds its schema, copies every
existing row across, and rewrites `config.json`'s own `database_url` to
point at the new local database (backing up the previous `config.json`
first, timestamped, alongside it). Prints the new connection string
(and the auto-generated password, if it generated one) at the end — the
one and only place that's shown, so save it somewhere. The very last
thing it tells you is to restart the app container — `config.json` is
bind-mounted read-only into it (see the Dockerfile's own header), so
editing the host's copy alone isn't picked up until the container
actually restarts.

Every setting (container/network names, `DB_USER`, `DB_PASSWORD`,
`CONFIG_PATH`, ...) is overridable via environment variable — see the
script's own header for the full list and defaults.

**The four steps it runs, if you'd rather do this by hand / one at a
time** (a different shape from everything else in this README — three
are Docker orchestration shell scripts and one calls the app's own
migration runner directly, not `queries.py`):

1. **`setup_local_postgres.sh`** — runs Postgres as a sibling Docker
   container (official `postgres` image, own named volume, own Docker
   network) and connects the already-running `live-deploy` app
   container to that same network, by container name (not IP — stable
   across restarts). No docker-compose dependency (this repo deploys
   via plain `docker run`), no host-level `postgresql.conf`/`pg_hba.conf`
   editing. Prints the resulting local `database_url` and the exact
   next-step commands at the end.
2. **`create_local_schema.py`** — builds every table on the fresh local
   database by calling the app's own `run_migrations()` (`app/db/
   migrate.py`) standalone — the exact same idempotent migration runner
   the app itself calls on every startup, just invoked once here
   without needing the whole FastAPI app or a Kite session running.
   Safe to re-run; does nothing if already up to date.
3. **`copy_remote_data_to_local.sh`** — copies every existing ROW from
   the remote database into the local one via `pg_dump --data-only |
   psql`, run inside a throwaway `postgres:16` container (borrows its
   bundled client tools rather than needing `pg_dump`/`psql` installed
   on the host) on the same Docker network so it can resolve the local
   DB by name. MUST run after step 2, not before — assumes every table
   already exists with matching columns. Excludes `schema_migrations`
   deliberately (confirmed necessary by actually running this, not
   assumed — without it, the load fails on a duplicate-key conflict
   against what step 2 already inserted there itself; that table is
   migration-application history, not real application data, and both
   sides should already agree on it since both run the same migration
   files). Prompts for confirmation before touching anything unless
   `AUTO_CONFIRM=1` is set (which `migrate_to_local_db.sh` sets
   automatically — direct/manual invocation still prompts by default).
4. Manually: update `config.json`'s own `database_url` to the new local
   connection string, then restart the app container.

There's also a code-level safety net independent of all of the above —
`app/config.py`'s `LOCAL_DATABASE_URL` env var: if set, it
unconditionally overrides whatever `database_url`/`DATABASE_URL` would
otherwise resolve to, remote value included, even if `config.json`
itself never gets updated (a stale file, a deploy script that
regenerates it from an old template). `migrate_to_local_db.sh` updates
`config.json` directly instead of relying on this, but the env var is
still there as a second line of defense — set it too if you want belt
and suspenders. Unset it to go back to normal resolution at any time.

There used to be a `resync_supertrend_state.py` here — a standalone
script to manually correct a `pivot_supertrend*` deployment's SuperTrend
state after a WebSocket tick gap drifted it from reality (see
`register_supertrend_options_strategies.py`'s own docstring above for
what a tick gap does to a recursive indicator like SuperTrend). It's
gone now because the fix moved INTO the strategy itself instead: every
`pivot_supertrend`/`pivot_supertrend_options`/
`pivot_supertrend_options_inverse` deployment now auto-seeds itself
live from Kite's REST API on every `on_start` (cold deploy, resume,
mid-day restart — no config-provided `seed_candles`/`prev_day_ohlc`
needed at all, though still honored as a fallback), AND self-corrects
its live in-memory state once a day at the post-market checkpoint
without needing a restart. See `app/strategies/pivot_supertrend.py`'s
`fetch_seed_from_kite`/`supertrend_from_seed_candles` and
`StrategyBase.on_post_market_checkpoint` for the mechanism. The
script's one remaining use case — "force a resync right now, without
waiting" — is now just Pause then Resume on the deployment (from the
UI or `POST /deployments/{id}/pause` + `/resume`), which triggers the
exact same live re-seed `on_start` always does.
