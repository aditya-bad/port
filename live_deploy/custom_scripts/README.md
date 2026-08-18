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
