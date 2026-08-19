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
  NIFTY and SENSEX from Kite and computes SuperTrend(7,3) through the
  actual strategy code (imported, not reimplemented) as a PRE-FLIGHT
  SANITY CHECK — confirms a Kite session exists and the numbers agree
  with a chart reading you provide — saves everything fetched to
  `custom_scripts/data/*.json` (gitignored) for the record, and — only
  once that validation passes, or with `--force` — registers 4
  deployments (`pivot_supertrend_options` + its inverse, each for
  NIFTY and SENSEX). Registers them with NO seed in their config at
  all: every `pivot_supertrend*` strategy now self-seeds live from
  Kite's own REST API the instant its `on_start` runs (see the main
  README's Step 80), so this script's own fetch above is validation
  only — it's never passed into the deployment. See the script's own
  `--help` / module docstring for the full flag list.

- **`generate_vapid_keys.py`** — the other exception to "touches the
  database": doesn't touch it at all, and needs no app server either.
  Generates the one-time VAPID keypair mobile push notifications
  (Step 85) need and prints exactly what to paste into `config.json`
  (or the equivalent env vars). Run it ONCE ever per deployment —
  regenerating later breaks push for everyone already subscribed until
  they re-enable it, since every subscription is tied to the specific
  keypair active when they tapped "Enable notifications." See its own
  module docstring for the full byte-format rationale.

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
