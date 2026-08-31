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

Three scripts, a different shape from everything above (two are Docker
orchestration shell scripts, not standalone Python against `queries.py`)
— run in this exact order, once, to move this app from a remote-hosted
Postgres (Neon, etc.) onto one running alongside its own server:

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
   files).

After all three: set `LOCAL_DATABASE_URL` (the connection string step 1
printed) as an environment variable on the `live-deploy` container and
restart it. See `app/config.py`'s own `LOCAL DB OVERRIDE` comment —
this env var unconditionally overrides whatever `database_url`/
`DATABASE_URL` would otherwise resolve to, remote value included, so
there's no need to hunt down and edit every place the old connection
string might still be sitting around (a stale `config.json`, a leftover
`DATABASE_URL` in a deploy script). Unset it to go back to normal
resolution at any time.

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
