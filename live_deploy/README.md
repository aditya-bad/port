# live_deploy

Standalone folder — does **not** import anything from the rest of the
`port` repo (not `tg_int_st_pp`, not `generic`, not the main pipeline).
Own config template, own requirements, own FastAPI app.

## What's here (Step 1: the live data dispatcher)

A FastAPI service that owns **exactly one** Kite Connect WebSocket
(`KiteTicker`) connection for the whole deployment, and fans every tick
out to however many downstream consumers are connected — a browser tab,
another backend service, a future strategy running in-process, five of
each at once. No matter how many consumers connect, Kite only ever sees
one client. Consumers never open their own Kite session.

```
                    ┌─────────────────────────┐
   Kite Connect  ───▶  LiveDataDispatcher      │
   (1 WebSocket)    │  (owns the ONE upstream  │
                    │   Kite connection)        │
                    └───────────┬─────────────┘
                                │ broadcast()
                    ┌───────────▼─────────────┐
                    │     TickBroadcaster       │
                    │  (fan-out to N queues)    │
                    └──┬─────────┬─────────┬───┘
                       │         │         │
                  ws client  ws client  future
                    #1         #2      in-process
                                        strategy
```

## What's here (Step 2: persistent, resumable paper-trading deployments)

A **deployment** is one strategy + one config, running with its own
isolated cash, positions, and trade history — persisted to Postgres
(Neon) so it survives the server being turned off overnight. One
strategy can have many deployments (same `strategy_name`, different
`deployment_name`/`config`/capital) and they never share state or
overlap, even if they trade the exact same instrument.

```
POST /deployments  ──▶  DeploymentManager ──▶  DeploymentRunner (1 per deployment)
                              │                        │
                    on startup: reload every            subscribes to the SAME
                    'active' deployment + its            TickBroadcaster as
                    open positions from Postgres          everyone else — no
                    and resume it automatically            extra Kite connection
                              │                        │
                              ▼                        ▼
                         Postgres (Neon): deployments, positions,
                         position_lots, deployment_events, deployment_snapshots
```

Server turned off at night → every fill was already durably committed
before the process stopped → next morning's `uvicorn` restart calls
`DeploymentManager.load_active_on_startup()`, which reloads every
deployment still marked `active` together with its current open
positions straight from the DB, and resumes it. No replay of missed
ticks — a live paper-trading engine reacts to the *current* live tick
stream once it's back up, the same way it would if you'd just deployed
it fresh with an already-open position.

## What's here (Step 3: onboarding — Kite login, strategy registry, UI)

Three things needed before this is actually usable day-to-day, none of
which are strategy logic:

1. **Daily Kite re-login, without a restart.** Kite's `access_token`
   expires every day, and can only be reissued through a login flow a
   human completes in a browser — that part can't be automated. What's
   automated is everything *around* it: a "Login with Kite" button opens
   Kite's login page in a popup; after the human logs in, Kite redirects
   to `GET /kite/callback` on this service, which exchanges the
   `request_token` for a fresh `access_token`, persists it to Postgres,
   and **hot-swaps the live dispatcher's connection** — no process
   restart, every downstream consumer (WS clients, running deployments)
   completely undisturbed.
2. **A strategy registry**, so "show me the list of strategies, let me
   deploy one" has something to list. `app/strategies/registry.py`'s
   `@register_strategy(...)` decorator is how a strategy announces
   itself; `GET /strategies` and the UI read from it.
3. **A single-page UI** (`static/index.html`) tying it together:
   connection status + login button, the strategy list with a deploy
   form, the deployment list with pause/resume/stop and a
   positions/trades/report drill-down, and manual instrument
   subscription. Served at `/` by the same FastAPI app — no separate
   frontend process.

Deploying an unregistered `strategy_name` is still allowed — you can
set up a deployment's name/capital/tokens/config before its strategy
code exists, it just won't trade until a matching `@register_strategy`
exists, and every API response flags this clearly via
`strategy_registered: false` so it's never silently misleading.

## What's here (Step 4: pivot points + SuperTrend(7,3) — live)

The first real strategy, `app/strategies/pivot_supertrend.py` —
ports the exact rules already backtested and validated in
`tg_int_st_pp/strategy_pivot_supertrend.py` (long above resistance with
SuperTrend green, short below support with SuperTrend red, exit on a
SuperTrend flip or a force-exit time, both entries and exits at the
*next* candle's open, only 1 open position at a time) to live streaming
ticks. Re-implemented here rather than imported — this repo's convention
is every top-level folder stays standalone.

**What's genuinely new vs. the backtest** (a batch file vs. a live tick
stream are different problems):

- **Ticks → 5-min candles.** `CandleAggregator` buckets incoming ticks
  by their `exchange_timestamp` (only present in Kite's `"full"` tick
  mode — this strategy needs `tick_mode: "full"` in `config.json`) into
  5-minute OHLC candles, floored to the same `:00/:05/:10…` boundaries
  Kite's own candles use, emitting a candle exactly once, the moment it
  closes.
- **SuperTrend computed incrementally**, one candle at a time (carrying
  forward just the previous ATR/bands/trend), instead of one batch pass
  over a pre-loaded array. **Proven identical to the batch math**, not
  just "should be the same": a test replays the same synthetic candle
  sequence through both implementations and asserts bit-for-bit
  identical trend/ATR/band output at every single step, across all 3 ATR
  smoothing methods.
- **Seeding.** A live deployment starts with no history at all — pivots
  need the *previous day's* H/L/C, and SuperTrend's ATR needs `period`
  candles of warmup. Everything below is optional; omitting all of it
  means a genuine cold start (no entries until ATR warms up from live
  ticks, ~35 minutes, AND a full trading day has been observed for
  pivots — the live equivalent of the backtest's "day 1 excluded"):

  | Config key | What it's for | Accuracy |
  |---|---|---|
  | `prev_day_ohlc: {high, low, close}` | Correct pivots from minute one | Exact — 3 numbers off any chart |
  | `seed_candles: [{date, open, high, low, close}, ...]` | **Recommended.** SuperTrend state, run through the exact same algorithm as live ticks — no approximation | Exact, given enough candles (7+ for ATR, 20-30+ for the trend/bands to have "settled") |
  | `supertrend_seed: {trend, value, atr, as_of_candle}` | Fallback for when you only have what your chart currently shows (the ST line + its color + a separately-added ATR(7) reading) | Approximate — only the *active* band is known this way; the inactive one is derived from `as_of_candle`'s own H/L. Not valid with `atr_smoothing: "sma"` (needs a rolling TR window, not one ATR number) — that combination is rejected with a clear warning and cold-starts instead of silently producing wrong numbers |

- **Position sizing is a genuinely new dimension.** The backtest reported
  raw index points per trade with no capital model — the NIFTY 50 index
  itself isn't a tradeable instrument. This paper-trading engine tracks
  real cash, so an entry is sized as `floor(cash / price)` "units" of
  the index price, as if it were directly tradeable — a deliberate
  simplification consistent with how the strategy was originally
  designed (point-based), not a claim you can literally buy the index.
  `capital_per_trade` caps this to a fixed amount instead of using all
  available cash; there's no averaging — exactly one lot in, one lot out,
  matching the backtest.

**Deploy example** (seeded — most accurate):

```bash
curl -X POST localhost:8000/deployments -H 'Content-Type: application/json' -d '{
  "deployment_name": "pivot_st_live_1",
  "strategy_name": "pivot_supertrend",
  "mode": "intraday",
  "initial_capital": 500000,
  "config": {
    "instrument_tokens": [256265],
    "symbol": "NIFTY 50",
    "pivot_type": "classic",
    "atr_smoothing": "wilder",
    "force_exit_time": "15:00",
    "prev_day_ohlc": {"high": 24850, "low": 24650, "close": 24800},
    "seed_candles": [
      {"date": "2026-08-11 09:15:00", "open": 24700, "high": 24720, "low": 24690, "close": 24710}
    ]
  }
}'
```

`instrument_tokens` (plural — matches the key every other deployment's
config uses for the same reason: `DeploymentRunner` filters the shared
tick stream by this key, and `DeploymentManager` reads it for dynamic
dispatcher subscription) must be a **one-element** list — this strategy
only ever trades a single instrument.

For a `"positional"`-style deployment that lets winners run past one
day instead of force-flattening at a fixed time, set
`"force_exit_time": null` — SuperTrend flips become the *only* exit
trigger. (`mode` itself is just a label at the infra level, same as
every other deployment — see "Mode: intraday vs positional" below;
`force_exit_time` is what actually controls this.)

## Setup

```bash
cd live_deploy
pip install -r requirements.txt
```

```bash
cp config.example.json config.json
```

```json
{
  "api_key": "your_kite_api_key",
  "api_secret": "your_kite_api_secret",
  "tick_mode": "full",
  "database_url": "postgresql://user:password@ep-xxxxx.neon.tech/dbname?sslmode=require"
}
```

`access_token` is **deliberately not in this template anymore** — it
expires daily and is now obtained via the UI's "Login with Kite" flow
(see below), which stores it in Postgres. It's still accepted as an
optional field here purely as a one-time bootstrap if the database has
no session yet; leave it out entirely and just log in through the UI on
first run. `tick_mode` is one of Kite's three tick verbosity levels:
`"ltp"`, `"quote"`, or `"full"` (default — includes market depth).
`database_url` is your Neon connection string, exactly as Neon gives it
to you (it already includes `sslmode=require`, which asyncpg honors
automatically).

**Schema setup is automatic.** On every startup, the app applies any
`app/db/migrations/*.sql` file not yet recorded as applied (tracked in a
`schema_migrations` table) — point this at a brand-new, empty Neon
database and the schema builds itself on first boot. No manual `psql`
step, no Alembic. Safe to leave running on every restart — already-applied
migrations are skipped.

### One-time manual step: register the Kite redirect URL

Kite's login flow redirects the browser to a URL **you configure once**
in the [Kite Developer Console](https://developers.kite.trade/apps),
under the app's "Redirect URL" setting — this can't be done from here,
Kite doesn't expose it via API. Set it to:

```
http://<your-host>:8000/kite/callback
```

(`http://localhost:8000/kite/callback` for local dev; your real domain
once this is deployed somewhere reachable.) If this doesn't match
exactly, Kite will refuse the redirect after login and the flow breaks
at the very last step — this is the single most common reason "Login
with Kite" would appear to do nothing.

`tokens.json` (already committed, not a secret) lists which instrument
tokens the dispatcher subscribes to on Kite's behalf:

```json
[
  {"symbol": "NIFTY 50", "instrument_token": 256265}
]
```

Add more entries here as needed — every token in this file gets
subscribed with the same `tick_mode` when the dispatcher connects.

## Usage

```bash
uvicorn app.main:app --reload --port 8000
```

(Run from inside `live_deploy/` — the app uses relative imports, so
`uvicorn app.main:app` resolves the package correctly. Running
`python app/main.py` directly does not; use `python -m app.main` if you
need a direct-execution fallback instead of uvicorn's CLI.)

Open **`http://localhost:8000/`** for the UI. On first run (or any
morning after the server was off overnight) it'll show "Not connected —
login required" — click **Login with Kite**, complete the login in the
popup, and the status flips to connected within a couple seconds with no
restart. Everything below is also reachable directly as an API if you'd
rather script it.

### `GET /health`

```json
{
  "status": "ok",
  "database_connected": true,
  "running_deployments": 3,
  "kite_connected": true,
  "needs_login": false,
  "subscribed_tokens": [{"instrument_token": 256265, "symbol": "NIFTY 50", "static": true}],
  "tick_mode": "full",
  "ticks_received": 148213,
  "last_tick_at": "2026-08-11T09:42:03.512+00:00",
  "reconnect_count": 0,
  "last_error": null,
  "downstream_subscribers": 3
}
```

`needs_login: true` means no Kite session exists at all yet (fresh
deploy, or the daily token was never refreshed) — distinct from
`kite_connected: false` with `needs_login: false`, which means a session
exists but the connection is currently down (network hiccup,
mid-reconnect) and should recover on its own. The UI uses this
distinction to decide whether to show "Login with Kite" or a
"reconnecting…" state.

### Kite login flow

| Method & path | What it does |
|---|---|
| `GET /kite/login-url` | Returns the URL to send the user to (the UI opens this in a popup) |
| `GET /kite/callback` | Kite redirects here after login — exchanges `request_token` for a fresh `access_token`, persists it, hot-swaps the dispatcher. Returns an HTML confirmation page, not JSON — the browser lands here directly |
| `GET /kite/status` | `{kite_connected, needs_login, last_error}` — a narrower view of the same info in `/health` |

### `GET /strategies`

```json
[{"name": "pivot_supertrend", "description": "Pivot points + SuperTrend(7,3) intraday",
  "default_config": {"instrument_tokens": [256265], "pivot_type": "classic"}}]
```

Backed by `app/strategies/registry.py` — a strategy module registers by
calling `@register_strategy(...)` and being imported (see that file's
docstring, and `app/strategies/__init__.py`'s import list; `pivot_supertrend`
is the first one). The UI's deploy form pre-fills `config` from
`default_config` when one is given.

### `WS /ws/ticks`

Connect and receive every tick batch Kite pushes for the subscribed
tokens, as JSON, in real time. Connecting here never opens a new Kite
session — you're subscribing to the broadcaster, not to Kite.

```python
import asyncio, websockets, json

async def main():
    async with websockets.connect("ws://localhost:8000/ws/ticks") as ws:
        async for message in ws:
            print(json.loads(message))

asyncio.run(main())
```

### Deployments API

| Method & path | What it does |
|---|---|
| `POST /deployments` | Create + immediately start a deployment |
| `GET /deployments?status=active` | List deployments, optionally filtered by status |
| `GET /deployments/{id}` | Full deployment detail (config, cash, status) |
| `GET /deployments/{id}/positions?status=open` | Current positions, with live `current_price` + `unrealized_pnl` computed from the dispatcher's last-tick cache |
| `GET /deployments/{id}/trades?offset=&limit=` | Paginated fill history (every lot) |
| `GET /deployments/{id}/events?offset=&limit=` | Audit log: fills, pause/resume/stop, strategy errors |
| `GET /deployments/{id}/report` | Aggregate stats: realized P&L, win rate, avg win/loss, open/closed counts |
| `POST /deployments/{id}/pause` | Halt trading, keep positions as-is, stop reacting to ticks |
| `POST /deployments/{id}/resume` | Resume a paused deployment |
| `POST /deployments/{id}/stop?force_close=false` | Terminal. Refuses if positions are open unless `force_close=true`, which flattens every open position at the dispatcher's last known tick price first |

**Create example:**

```bash
curl -X POST localhost:8000/deployments -H 'Content-Type: application/json' -d '{
  "deployment_name": "pivot_st_conservative",
  "strategy_name": "pivot_supertrend",
  "mode": "intraday",
  "initial_capital": 500000,
  "config": {"instrument_tokens": [256265], "pivot_type": "classic"}
}'
```

The response (and every `GET` on a deployment) includes
`strategy_registered: bool` — `false` means `strategy_name` doesn't
match anything in the registry yet. The deployment is still created and
still shows up everywhere; it just won't trade until a matching
`@register_strategy` exists and the deployment restarts (pause+resume
also re-attaches against the current registry state).

`config` is free-form JSONB — whatever a strategy needs. The one key the
infra itself reads is `instrument_tokens`: the runner filters the shared
tick stream down to just those tokens for this deployment. **A token
doesn't need to already be in `tokens.json`** — if it's new, creating the
deployment dynamically subscribes it on the already-live Kite connection,
no restart (see "Dynamic instrument subscription" below).

**One strategy, multiple deployments:** `strategy_name` is just a label.
Create two deployments with the same `strategy_name`, different
`deployment_name`s, different `config`s (or the same config, doesn't
matter) — they get separate rows everywhere, separate cash, separate
positions, and a DB-level constraint (a partial unique index on
`positions(deployment_id, instrument_token) WHERE status='open'`)
guarantees they can never collide even if both trade the identical
instrument at the identical time.

### Dynamic instrument subscription

`tokens.json` is a **permanent baseline**, loaded once at startup — never
auto-removed, editable only by hand-editing the file and restarting.
Anything needed beyond that baseline is subscribed **at runtime, on the
already-live Kite connection, no restart required**, two ways:

1. **Automatic, via deployments.** `POST /deployments` reads the new
   deployment's `config.instrument_tokens` and subscribes any that
   aren't already covered. `POST /deployments/{id}/stop` releases that
   deployment's claim on its tokens when it's done. Tokens are
   **reference-counted**, not just added/removed 1:1 — if two
   deployments both trade NIFTY BANK, stopping one leaves it subscribed
   for the other; only the last deployment relying on a token actually
   triggers an unsubscribe. Pausing does *not* release tokens — it's
   meant to be a lightweight, reversible halt, not a teardown.

2. **Manually, via the API** — for subscribing ahead of a deployment, or
   just watching a token's ticks over `/ws/ticks` with nothing deployed
   against it at all:

   | Method & path | What it does |
   |---|---|
   | `GET /instruments` | List everything currently subscribed, flagged `static` (from `tokens.json`) vs dynamic |
   | `POST /instruments` | `[{"instrument_token": 260105, "symbol": "NIFTY BANK"}]` — subscribe, ref-counted the same as a deployment's claim |
   | `DELETE /instruments/{token}` | Release one manual claim. A token stays subscribed as long as *any* claim on it remains; `tokens.json` entries can never be removed this way |

**Why this needed a real fix, not just a new method.** `kiteconnect`'s
`KiteTicker.subscribe()`/`unsubscribe()`/`set_mode()` write straight to
the WebSocket (`self.ws.sendMessage(...)`) with no thread-safety of
their own — confirmed by reading the installed library's source, not
assumed. `KiteTicker.connect(threaded=True)` runs Kite's connection on
Twisted's reactor, in its own background thread; calling those methods
directly from FastAPI's asyncio thread while that reactor thread is
concurrently reading/writing the same socket is a genuine data race on
the transport, not a style nitpick — it just wouldn't necessarily show up
in casual testing. Every dynamic subscribe/unsubscribe in
`LiveDataDispatcher.add_instruments()`/`release_instruments()` is
marshaled onto the reactor's own thread via Twisted's documented
mechanism for exactly this, `reactor.callFromThread(...)` — the mirror
image of the existing tick-ingestion bridge, which already went the
other way (Kite's thread → asyncio loop) via
`asyncio.run_coroutine_threadsafe`.

### Mode: intraday vs positional

`mode` is currently a label/reporting field — it does **not** trigger any
automatic end-of-day flattening at the infra level. That's a deliberate
choice: forcing all `intraday` deployments closed at some fixed time
would preempt whatever exit timing a future strategy defines for itself
(the backtest engine's own `force_exit_time` config is exactly this kind
of strategy-level decision). If you want a blanket "never hold overnight"
guarantee independent of strategy logic, say so and it's a small addition
to `DeploymentManager` — flagging it here as a decision made, not an
oversight.

## Architecture notes

- **`app/broadcaster.py`** — `TickBroadcaster`: pure async fan-out, one
  bounded `asyncio.Queue` per subscriber, zero Kite/network dependency.
  Fully testable in isolation.
- **`app/dispatcher.py`** — `LiveDataDispatcher`: owns the single
  `KiteTicker`. `KiteTicker.connect(threaded=True)` runs Kite's own I/O
  loop in a background thread — its `on_ticks`/`on_connect`/`on_close`
  callbacks all fire from *that* thread, not from FastAPI's asyncio
  event loop. The dispatcher's only real job beyond owning the
  connection is bridging that thread-based callback into the event loop
  thread-safely, via `asyncio.run_coroutine_threadsafe` — everything
  downstream of that bridge point sees an ordinary async `broadcast()`
  call, never a raw thread handoff.
- **Slow-consumer protection** — each subscriber's queue is bounded
  (`max_queue_size=1000`). If one downstream consumer falls behind, the
  broadcaster drops that consumer's *oldest* queued batch to make room
  for the newest one, rather than blocking the whole broadcast (and
  therefore every *other* subscriber) on one lagging client.
- **Reconnection** — `KiteTicker`'s built-in auto-reconnect is left on
  (its defaults: up to 50 attempts, 60s max delay); `on_reconnect` /
  `on_noreconnect` are wired to update `/health`'s `reconnect_count` and
  `kite_connected` fields rather than crash the service.
- **`kite_ticker_cls` constructor parameter** on `LiveDataDispatcher` —
  exists purely so tests can inject a fake `KiteTicker` instead of the
  real one. Not used in normal operation (defaults to the real
  `kiteconnect.KiteTicker`).
- **`app/db/migrate.py`** — numbered `.sql` files in `app/db/migrations/`,
  applied in order, tracked in a `schema_migrations` table. No Alembic —
  deliberately, for a schema this size. Idempotent, runs on every startup.
- **`app/db/queries.py`** — every DB write goes through `record_fill()`,
  and it's the one place position/lot/cash state changes, all inside a
  single transaction (`SELECT ... FOR UPDATE` locks the deployment row
  and the open-position row first, so concurrent fills against the same
  deployment can't race). Position/lot semantics deliberately mirror
  `backtest.py`'s `Position`/`Lot` classes: a same-direction fill ADDS a
  lot (quantity-weighted averaging); an opposite-direction fill must
  close the ENTIRE open position — qty must exactly match. No partial
  exits, no reversal in one fill. Nothing has asked for that yet, and
  this keeps a strategy ported from the backtest engine's buy()/sell()
  calling convention unchanged. Loosening it later only means relaxing
  the one qty-equality check — the schema already supports partial lots.
- **Cash accounting is a simplification, not a margin model** — a `sell`
  (whether closing a long or opening a short) credits the full notional
  as cash; a `buy` (closing a short or opening a long) debits it. There's
  no margin/collateral concept, so a deployment's cash briefly looks
  "high" right after opening a short — it nets out correctly across the
  full open→close round trip via `realized_pnl`, but this is not how a
  real broker margins a short position. Flagged here since it's a real
  simplification a strategy author should know about, not a bug.
- **`InsufficientCash` / `ClosingQtyMismatch`** — clean, catchable
  exceptions raised from an explicit check inside `record_fill()`'s
  transaction (after locking the deployment row). The DB's own
  `current_cash >= 0` CHECK constraint still exists underneath as a
  last-resort safety net, but callers should never actually hit it.
- **`DeploymentRunner` re-reads from the DB after every fill** rather
  than replicating the averaging/closing math in memory — Postgres is
  the single source of truth, which is exactly what makes resuming after
  a restart correct by construction rather than by careful bookkeeping.
- **One open position per `(deployment_id, instrument_token)`** is
  enforced by a partial unique index at the database level (see
  `app/db/migrations/0001_init.sql`), not just in application code — the
  actual guarantee behind "each deployment's own positions never overlap."

## Verified without live Kite credentials or a real Neon database

No Kite credentials, and no Neon URL, exist in this environment. Two
things were used to verify this thoroughly rather than by inspection:

1. The dispatcher's `kite_ticker_cls` injection point, substituting a
   fake `KiteTicker` that replicates the real one's thread-based
   callback behavior (same as step 1).
2. **A real local PostgreSQL 16 instance** (available in this sandbox) —
   the schema, migrations, and every query in `app/db/queries.py` were
   run against actual Postgres, not mocked. Neon *is* Postgres, so this
   is a faithful test of the SQL itself, not just the Python around it.

Verified end-to-end:

- **Schema**: the partial unique index genuinely rejects a second open
  position for the same `(deployment, instrument)` pair, and genuinely
  allows a new one once the first is closed
- **Migration runner**: applies `0001_init.sql` on a fresh DB, is a
  true no-op on a second run against the same DB (idempotent)
- **`record_fill`**: opening, averaging (quantity-weighted avg price
  checked against hand-computed expected values), full close with
  correct realized P&L, `InsufficientCash` and `ClosingQtyMismatch`
  both correctly reject the fill *and* leave cash/positions completely
  untouched (transaction rollback verified, not assumed)
- **Isolation**: two deployments of the identical `strategy_name`,
  trading the identical instrument, at the same time — separate
  position rows, separate cash balances, zero cross-contamination
- **Full FastAPI + DB integration** (`httpx.AsyncClient` against the
  real app, one shared event loop so the asyncpg pool and direct
  `runner.buy()/sell()` calls coexist correctly): deployment create →
  409 on duplicate name → trade → pause (runner removed, DB state
  intact) → resume (runner recreated, picks the position back up) →
  stop refused with an open position → stop with `force_close=true`
  correctly flattens at the dispatcher's last known tick price
- **The actual point of this build**: a **second, completely fresh
  FastAPI app instance** (all `app.*` modules re-imported from scratch,
  simulating the server being killed and restarted) pointed at the
  *same* database — on startup, it resumed exactly the one deployment
  still marked `active` (not the one that had been stopped), with its
  full trade history intact and correctly showing zero open positions,
  and immediately accepted a new trade with no replay step of any kind

This confirms the persistence and lifecycle mechanics are correct
against real Postgres semantics. It has not been run against Neon
specifically, or against a real Kite WebSocket — those require your
`config.json`.

**Dynamic instrument subscription** was verified two ways:

1. **The thread-safety fix itself, against the real Twisted reactor** —
   not the fake ticker. Started `reactor.run()` in a background thread
   exactly as `KiteTicker.connect(threaded=True)` does in production,
   scheduled a probe function via the dispatcher's actual default
   scheduler (`_default_ticker_thread_scheduler()`, which resolves to
   `reactor.callFromThread`), and confirmed it executed on the
   *reactor's* thread, not the calling thread — proving the marshaling
   is real, not just plumbing that looks right.
2. **Ref-counting logic and full deployment-lifecycle wiring** (fake
   ticker, real Postgres): a static (`tokens.json`) token is a complete
   no-op for both `add_instruments`/`release_instruments`; a genuinely
   new token gets a live `subscribe`+`set_mode` call on the
   already-connected fake ticker; a second claim on the same token
   bumps the refcount with *no* duplicate wire call; releasing one of
   two claims leaves it subscribed; releasing the last one genuinely
   unsubscribes. End-to-end through the real API: two deployments
   created with the identical new `instrument_token` triggered exactly
   one `subscribe` call between them; stopping the first left the token
   subscribed (the second deployment still needed it); stopping the
   second actually unsubscribed it. The manual `POST`/`DELETE
   /instruments` endpoints were exercised the same way, including
   confirming a static token survives a manual `DELETE`.

**The Kite onboarding + registry layer** (fake `KiteConnect`/`KiteTicker`,
real local Postgres) was verified end-to-end, including a real bug this
testing caught before it shipped: the fake `generate_session()` returned
`login_time` as a plain string, and `/kite/callback` crashed trying to
insert it into a `TIMESTAMPTZ` column — because the real `kiteconnect`
library only parses that field into a `datetime` under a fragile
string-length condition, and the callback handler had implicitly trusted
that it always would. Fixed by parsing defensively in `/kite/callback`
itself rather than trusting the third-party library's behavior. Verified:

- **Cold start, no Kite session anywhere** (no DB row, no `config.json`
  token): `/health` correctly shows `needs_login: true`; every other
  endpoint (deployments, etc.) works normally regardless
- `GET /kite/login-url` returns a real Kite login URL with the
  configured `api_key`
- `GET /kite/callback` with a simulated successful redirect: exchanges
  the token, persists it, **hot-swaps the dispatcher's connection with
  no restart** — confirmed by checking the live dispatcher object
  directly, not just the HTTP response — and `/health` reflects
  `kite_connected: true` on the very next poll
- A failed-login callback (`status=failure`) is rejected with a clean
  400, not silently accepted
- **A second, completely fresh app instance, with `config.json`
  deliberately holding no `access_token` at all**, connects to Kite
  automatically on startup — proving the DB, not the file, is what
  carried the session across the restart
- **Strategy registry**: a test strategy registered via
  `@register_strategy` is listed by `GET /strategies`; deploying it
  attaches a real, live instance to the runner, and a broadcasted tick
  demonstrably reaches its `on_tick()`; deploying an *unregistered*
  `strategy_name` is allowed (not rejected) but comes back flagged
  `strategy_registered: false`, and its runner's `strategy` stays `None`
- `GET /` serves the UI's `index.html`, confirmed not shadowed by the
  API routes registered before it (Starlette route-matching order
  verified directly, separately from the app test)
- The UI's embedded JavaScript was extracted and checked with
  `node --check` — syntactically valid, not just "looked right"
- Full regression pass: every test from the previous two build steps
  re-run against the changed `LiveDataDispatcher` constructor
  (`access_token` → `initial_access_token`, `start()` → `bind_loop()`)
  — zero regressions

**`pivot_supertrend`** was verified at three levels:

1. **Math**: pivot formulas re-checked against the same hand-computed
   values used in `tg_int_st_pp`'s own tests. The critical one —
   `SuperTrendState` (incremental) vs. a fresh batch reference
   implementation, fed the identical 145-candle synthetic sequence used
   throughout this whole project's testing — produced **bit-for-bit
   identical trend and ATR values at every single step, across all 3
   ATR smoothing methods**. `CandleAggregator` correctly buckets
   multi-tick sequences into OHLC candles on the same `:00/:05/:10…`
   boundaries Kite itself uses (confirmed a tick at `09:17:32` floors to
   the `09:15` bucket, not `09:20`). The `sma` + `supertrend_seed`
   rejection was confirmed to actually fall back to a not-ready cold
   start rather than silently seeding with wrong numbers.
2. **Live integration, seeded**: deployed through the real API with
   `prev_day_ohlc` + `seed_candles` derived from a synthetic "day 1",
   then fed a full "day 2" (rally, crash, drift) as realistic
   open+close tick pairs through the actual dispatcher →
   broadcaster → `DeploymentRunner` → strategy → `runner.buy()`/
   `sell()` → Postgres pipeline — no shortcuts, no direct DB writes
   from the test. Produced a long entry, a SuperTrend-flip exit, a
   same-day short re-entry, and a force-exit at 15:00, with real
   `realized_pnl` in the DB-backed report. **The exact entry/exit
   timestamps and prices matched what the original backtest produced
   on this same synthetic data** — a strong independent cross-check
   that the live port didn't drift from the validated strategy.
3. **Live integration, cold start**: deployed with zero seed data at
   all — confirmed **zero trades during day 1** (no pivots exist yet,
   matching the backtest's "day 1 excluded" behavior translated to
   live operation), then confirmed entries correctly fire on day 2 once
   pivots were self-derived from day 1's live-observed OHLC and ATR
   self-warmed from live ticks.

Not yet run against a real Kite tick stream — the tick-shape assumptions
(`exchange_timestamp`, `last_price` in `"full"` mode) are taken directly
from `kiteconnect`'s own source and documented tick-structure example,
not guessed.

## Folder layout

```
live_deploy/
├── config.example.json        # copy -> config.json (gitignored). access_token now optional.
├── tokens.json                 # committed — which instruments to subscribe to
├── requirements.txt
├── static/
│   └── index.html               # the UI — served at "/" by the FastAPI app itself
└── app/
    ├── main.py                  # FastAPI app, startup/shutdown wiring, static mount
    ├── config.py                  # config.json / tokens.json loading
    ├── broadcaster.py              # TickBroadcaster (step 1)
    ├── dispatcher.py                # LiveDataDispatcher — connection + hot-swap (steps 1 & 3)
    ├── db/
    │   ├── pool.py                   # asyncpg pool + JSONB codec
    │   ├── migrate.py                 # migration runner
    │   ├── queries.py                  # every DB read/write, incl. kite_sessions
    │   └── migrations/
    │       ├── 0001_init.sql            # deployments/positions/lots/events/snapshots
    │       └── 0002_kite_sessions.sql    # single-row table for the daily access_token
    ├── deployments/
    │   ├── schemas.py                   # Pydantic request/response models
    │   ├── strategy_base.py              # interface future strategies implement
    │   ├── runner.py                      # DeploymentRunner — one per deployment
    │   └── manager.py                      # DeploymentManager — lifecycle + registry wiring
    ├── routers/
    │   ├── health.py
    │   ├── deployments.py
    │   ├── instruments.py                   # manual subscribe/unsubscribe control
    │   ├── kite_auth.py                      # login-url / callback / status
    │   └── strategies.py                      # GET /strategies
    └── strategies/
        ├── __init__.py                         # import list — triggers registration
        ├── registry.py                          # @register_strategy
        └── pivot_supertrend.py                   # step 4 — ports tg_int_st_pp's backtested rules to live ticks
```

## Relationship to the rest of the repo

Fully isolated from the main `port` repo's Nifty 50 backtest pipeline,
from `generic/`, and from `tg_int_st_pp/`. Shares no code, no data, no
config with any of them. This is a live/real-time service, not a
backtest — it doesn't read or write anything under `data/`. Its own
persistence lives in whatever Neon database you point `database_url` at,
entirely separate from this repo's filesystem-based data.
