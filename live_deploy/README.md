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

Step 3 (**not built yet** — "once infra is ready, I'll tell you the
strategies"): actual strategy decision logic. `app/deployments/
strategy_base.py` is the interface a strategy will implement; `app/
strategies/` is where they'll live. Until then, deployments exist, hold
positions, and can be traded via `runner.buy()`/`runner.sell()` calls
(which is exactly what a strategy will call), but nothing decides *when*
to call them yet.

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
  "access_token": "your_daily_access_token",
  "tick_mode": "full",
  "database_url": "postgresql://user:password@ep-xxxxx.neon.tech/dbname?sslmode=require"
}
```

The `access_token` expires daily — refresh it each session. `tick_mode`
is one of Kite's three tick verbosity levels: `"ltp"`, `"quote"`, or
`"full"` (default — includes market depth). `database_url` is your Neon
connection string, exactly as Neon gives it to you (it already includes
`sslmode=require`, which asyncpg honors automatically).

**Schema setup is automatic.** On every startup, the app applies any
`app/db/migrations/*.sql` file not yet recorded as applied (tracked in a
`schema_migrations` table) — point this at a brand-new, empty Neon
database and the schema builds itself on first boot. No manual `psql`
step, no Alembic. Safe to leave running on every restart — already-applied
migrations are skipped.

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

### `GET /health`

```json
{
  "status": "ok",
  "database_connected": true,
  "running_deployments": 3,
  "kite_connected": true,
  "subscribed_tokens": [{"instrument_token": 256265, "symbol": "NIFTY 50"}],
  "tick_mode": "full",
  "ticks_received": 148213,
  "last_tick_at": "2026-08-11T09:42:03.512+00:00",
  "reconnect_count": 0,
  "last_error": null,
  "downstream_subscribers": 3
}
```

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

`config` is free-form JSONB — whatever a strategy needs. The one key the
infra itself reads is `instrument_tokens`: the runner filters the shared
tick stream down to just those tokens for this deployment. (Every token
a deployment trades must already be in the dispatcher's `tokens.json` —
that list is loaded once at startup; adding a new token currently means
adding it to `tokens.json` and restarting.)

**One strategy, multiple deployments:** `strategy_name` is just a label.
Create two deployments with the same `strategy_name`, different
`deployment_name`s, different `config`s (or the same config, doesn't
matter) — they get separate rows everywhere, separate cash, separate
positions, and a DB-level constraint (a partial unique index on
`positions(deployment_id, instrument_token) WHERE status='open'`)
guarantees they can never collide even if both trade the identical
instrument at the identical time.

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

## Folder layout

```
live_deploy/
├── config.example.json        # copy -> config.json (gitignored)
├── tokens.json                 # committed — which instruments to subscribe to
├── requirements.txt
└── app/
    ├── main.py                  # FastAPI app, startup/shutdown wiring
    ├── config.py                  # config.json / tokens.json loading
    ├── broadcaster.py              # TickBroadcaster (step 1)
    ├── dispatcher.py                # LiveDataDispatcher (step 1) + last_prices cache
    ├── db/
    │   ├── pool.py                   # asyncpg pool + JSONB codec
    │   ├── migrate.py                 # migration runner
    │   ├── queries.py                  # every DB read/write
    │   └── migrations/0001_init.sql     # schema
    ├── deployments/
    │   ├── schemas.py                   # Pydantic request/response models
    │   ├── strategy_base.py              # interface future strategies implement
    │   ├── runner.py                      # DeploymentRunner — one per deployment
    │   └── manager.py                      # DeploymentManager — lifecycle orchestration
    ├── routers/
    │   ├── health.py
    │   └── deployments.py
    └── strategies/                          # empty — step 3, not built yet
```

## Relationship to the rest of the repo

Fully isolated from the main `port` repo's Nifty 50 backtest pipeline,
from `generic/`, and from `tg_int_st_pp/`. Shares no code, no data, no
config with any of them. This is a live/real-time service, not a
backtest — it doesn't read or write anything under `data/`. Its own
persistence lives in whatever Neon database you point `database_url` at,
entirely separate from this repo's filesystem-based data.
