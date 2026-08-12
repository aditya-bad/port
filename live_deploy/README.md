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
curl -X POST localhost:8000/deployments \
  -H 'Content-Type: application/json' -H "X-API-Key: $APP_AUTH_SECRET" -d '{
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

## What's here (Step 5: options utils)

Before wiring up an actual options strategy, `app/options/` gives it
everything needed to turn a plain-English leg description into a
concrete, tradeable contract — reusing the **exact same Kite session**
the dispatcher's WebSocket already holds (built from the dispatcher's
publicly-exposed `api_key`/`access_token`, see `app/options/client.py`),
no separate login.

This package is **resolution-only** — it never places a trade itself. A
strategy resolves an `OptionLeg` and then calls the same
`runner.buy()`/`runner.sell()` every other strategy already uses:

```python
from ..options import OptionsResolver

resolver = OptionsResolver(runner.dispatcher)

# "THIS_WEEK ATM CE"
leg = await resolver.get_atm_leg("NIFTY", "THIS_WEEK", "CE")

# "NEXT_WEEK ATM-10 CE"  (10 strike-steps below the ATM strike)
leg = await resolver.get_leg_by_offset("NIFTY", "NEXT_WEEK", "CE", -10)

# "THIS_WEEK CE with price closest to 40"
leg = await resolver.get_leg_by_premium("NIFTY", "THIS_WEEK", "CE", 40)

await runner.buy(leg.tradingsymbol, leg.instrument_token, leg.lot_size, leg.last_price)
```

Every method is `async` — resolving anything here can require an
instrument-master fetch or a live quote, both blocking HTTP calls in
`kiteconnect` (it's built on `requests`, not `httpx`/`aiohttp`) — so they
always run off the event loop via `asyncio.to_thread`, never blocking
every other deployment's tick processing on the same loop.

**Expiry selectors** (`resolve_expiry` and every leg method that takes
one): `"THIS_WEEK"`, `"NEXT_WEEK"`, `"THIS_MONTH"`, `"NEXT_MONTH"`, an
`int` (`0` = nearest upcoming expiry = `THIS_WEEK`, `1` = next, ...), a
`date`, or an ISO date string. `THIS_MONTH`/`NEXT_MONTH` resolve to the
*last* expiry listed within that calendar month — for NIFTY-style
underlyings that's the same contract the monthly series has always been
(the last weekly of the month); for a stock with no weeklies at all,
it's simply that month's one listed expiry.

**Strike/leg resolution:**

| Method | What it answers |
|---|---|
| `list_strikes` / `get_strike_step` | Real listed strikes and the actual gap between them — derived from the live instrument master, not a hardcoded per-underlying constant (strike spacing has changed over time, e.g. NIFTY has used both 50 and 100) |
| `get_atm_strike` / `get_atm_leg` | Nearest **listed** strike to the current spot price |
| `get_leg_by_offset(..., offset_steps)` | ATM ± N *strike-steps* (not rupees) — `-10` is "ATM-10" in chain jargon |
| `get_otm_leg` / `get_itm_leg(..., steps)` | OTM/ITM, **direction-aware per option type** so callers never have to think about it: a CE is OTM *above* spot and ITM *below*; a PE is the mirror image. Explicitly tested both ways for both types — this is the one detail in options code that's easy to get backwards |
| `get_leg_by_premium(..., target_price)` | The leg whose current LTP is closest to a target premium, searched over a bounded window around ATM (`strike_window`, default 15 steps each side) rather than the whole chain — correct for realistic targets and far cheaper than scanning 150+ strikes |
| `get_max_oi_strike(..., option_type=None)` | Highest-open-interest leg — a common support/resistance signal (max CE OI ≈ resistance, max PE OI ≈ support); returns `(leg, oi)` |
| `list_option_chain(underlying, expiry_selector)` | The whole chain for one expiry as `{strike: {"CE": leg, "PE": leg}}` |

**Futures, pricing, and misc:** `get_futures_leg`/`get_futures_price`
(defaults to `THIS_MONTH`), `get_ltp`/`get_quote` (take either an
`OptionLeg` or a raw `"EXCHANGE:TRADINGSYMBOL"` string), `get_spot_price`
(prefers the dispatcher's **live tick cache** — zero REST calls if the
underlying happens to already be subscribed — falling back to a REST
`ltp()` call only on a cache miss), `get_lot_size`, `round_to_lot`
(nearest whole-lot quantity, minimum 1 lot), `list_underlyings`.

**Index spot symbol mapping.** A handful of indices' options are listed
under a `name` that doesn't match their own spot tradingsymbol (e.g.
NIFTY's options are named `"NIFTY"` but the spot index trades as
`"NIFTY 50"`). `INDEX_SPOT_SYMBOL` in `resolver.py` maps the ones
actually listed on Kite today (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY,
SENSEX, BANKEX); anything not in that table is assumed to be a stock,
where the options `name` and the spot tradingsymbol are identical. If
Kite lists a new index this table doesn't know about, `get_spot_price`
for it falls back to `(NSE, underlying)`, which will resolve to the
wrong symbol — add an entry here if that ever comes up.

**Instrument master caching.** The full NFO instrument list (tens of
thousands of rows) is fetched once per calendar day and shared
**process-wide**, not per-deployment or per-resolver-instance — multiple
options strategies running at once don't each pay for their own fetch.
An `asyncio.Lock` per exchange prevents a redundant concurrent refetch if
two resolvers race on a cold cache at the same moment.

`get_kite_connect(dispatcher)` (`app/options/client.py`) caches the REST
client keyed by `(api_key, access_token)` — so a daily re-login
(`dispatcher.reconnect()`, same as the WebSocket hot-swap) transparently
produces a fresh REST client on the next call too, with nothing to
manually invalidate. Calling any options util before the first-ever Kite
login raises a clean `NoKiteSession`, not a crash.

## What's here (Step 6: pivot + SuperTrend, but selling options)

`app/strategies/pivot_supertrend_options.py` — the exact same signal
engine as `pivot_supertrend` (pivots, SuperTrend(7,3), candle
aggregation, seeding — literally imported from that module, not
reimplemented, so both strategies share one tested source of truth for
the numerically-sensitive parts), but instead of going long/short the
underlying, it **sells options**:

- **Long signal** (5-min close above R1/R2/R3, ST green) → **SELL
  THIS_WEEK ATM PE**
- **Short signal** (5-min close below S1/S2/S3, ST red) → **SELL
  THIS_WEEK ATM CE**
- **Exit** (ST flip, or force-exit time) → **BUY BACK** whichever leg is
  open

This is live paper-trading only — there's no backtested version of this
variant in `tg_int_st_pp`, since options weren't part of that engine at
all. It's registered separately as `"pivot_supertrend_options"` (the
original `"pivot_supertrend"` is untouched and still trades the
underlying) rather than a config flag on one class, since the execution
side — leg resolution, dynamic per-trade option-token subscription,
sell-to-open/buy-to-close instead of buy-to-open/sell-to-close — is
different enough to deserve its own file.

**Why always SELL, never BUY, regardless of signal direction:** this
strategy is always writing premium — a long signal just picks *which*
leg to write (the PE, since a put seller profits as long as NIFTY stays
above the strike, a defined-premium way to express "bullish") — never
buys options outright. That maps directly onto the existing paper ledger
with zero schema or query changes: `record_fill` already treats
"sell first, buy later" as a short position with `realized_pnl =
qty * (sell_price - buy_price)` — exactly premium collected minus
premium paid to close. Same simplification the rest of live_deploy
already makes for the underlying: **no margin model** — selling a leg
always succeeds cash-wise (premium is a pure credit), buying back to
close is still cash-checked for real like any other buy.

**Deploy example:**

```bash
curl -X POST localhost:8000/deployments \
  -H 'Content-Type: application/json' -H "X-API-Key: $APP_AUTH_SECRET" -d '{
  "deployment_name": "pst_options_live_1",
  "strategy_name": "pivot_supertrend_options",
  "mode": "intraday",
  "initial_capital": 500000,
  "config": {
    "instrument_tokens": [256265],
    "symbol": "NIFTY 50",
    "options_underlying": "NIFTY",
    "expiry_selector": "THIS_WEEK",
    "lots_per_trade": 1,
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

Config keys not already covered by `pivot_supertrend`'s seeding options
(identical here — `prev_day_ohlc`/`seed_candles`/`supertrend_seed`, see
Step 4 above):

| Key | Meaning |
|---|---|
| `instrument_tokens` | The **underlying's** token (e.g. NIFTY 50's `256265`) — used ONLY to generate the pivot/SuperTrend signal from its own tick stream. The options actually traded are resolved dynamically and are never this token. |
| `options_underlying` | **Required.** The options chain's own `name`, e.g. `"NIFTY"` — NOT the spot tradingsymbol `"NIFTY 50"` (see Step 5's `INDEX_SPOT_SYMBOL` note). |
| `expiry_selector` | `"THIS_WEEK"` (default) — any selector `OptionsResolver` accepts. |
| `lots_per_trade` | `1` (default) — options only trade in whole lots; each entry sells this many lots of whatever `options_underlying`'s current lot size is. There's no `capital_per_trade` equivalent here — selling a leg doesn't consume capital the way buying the underlying does, so sizing is naturally lot-based instead. |

**Execution details worth knowing:**

- Entry/exit timing is identical to `pivot_supertrend` — signals are
  detected on a candle's close and executed at the *next* candle's open
  — but the **price** used for the fill comes from the option's own live
  LTP (`OptionsResolver.get_ltp`) at that moment, not from the
  underlying's OHLC — the underlying candle only decides *when* to act.
- A fresh ATM leg is resolved on every single entry (a new week almost
  certainly means a different strike, sometimes a different expiry
  contract entirely) — nothing about which option gets traded is fixed
  at deploy time.
- The strategy dynamically subscribes the dispatcher to whichever
  option leg is currently open (so its live ticks feed
  `dispatcher.last_prices` for mark-to-market / force-close-on-stop) and
  releases that subscription the moment the leg closes — including on
  pause (re-subscribed automatically by `on_start` on resume if the
  position is still open) and on a genuine stop, so nothing leaks.
- If resolving or pricing a leg fails (e.g. no Kite session yet, a
  transient API error), the entry is skipped with a logged warning
  rather than crashing the deployment — the next entry signal gets a
  fresh attempt.

## What's here (Step 7: application-level authentication)

This is a single-user personal tool, not a multi-tenant service — so
instead of a user/password table, ONE shared secret
(`app_auth_secret` in `config.json`) protects the entire service: every
router, `/ws/ticks`, and the UI at `/`.

**Implemented as ASGI middleware** (`app/auth.py`'s `AuthMiddleware`),
not per-route `Depends()`, on purpose: middleware fails **closed** —
anything not explicitly allowlisted needs auth, including any router
added later and forgotten about — a `Depends()`-based check fails
**open** (unprotected until someone remembers to add the dependency to
the new router). "Protect everything by default" only actually holds
with the fail-closed shape.

**Exactly two paths are allowlisted**, both for the same underlying
reason — neither can carry our own auth on the request that reaches it:

- **`GET /kite/callback`** — Kite's own servers redirect the user's
  browser here after a successful login; Kite doesn't and can't attach
  our session cookie or API key to that redirect. Still safe without our
  auth layer: it only does anything with a `request_token` that Kite
  itself validates server-side during the token exchange — hitting this
  URL without a real token from an actual Kite login is a clean failure,
  not a way in.
- **`POST /auth/login`** — the login endpoint itself obviously can't
  require being already logged in to reach it.

Everything else, **including `/health`**, is protected — low-sensitivity
today, but "protect everything by default" means not carving out silent
exceptions; if a future monitoring setup needs it open, that's a
deliberate change to make explicitly, not something that should already
be true by omission.

**Two ways to authenticate, either is accepted:**

1. **A session cookie**, for the browser UI. `POST /auth/login` with
   `{"password": "..."}`; on match (checked with `secrets.compare_digest`,
   not `==`, to avoid a timing side-channel on the comparison itself),
   sets a signed `httponly`, `samesite=lax` cookie via Starlette's own
   `SessionMiddleware` — no new package needed for the signing
   (`itsdangerous`, which it depends on, is now in `requirements.txt`).
   `POST /auth/logout` clears it; there's a **Logout** button in the
   main UI's header once logged in.
2. **An `X-API-Key` header**, for scripted/curl use — compared against
   `app_auth_secret` the same `compare_digest` way. See the updated curl
   examples above.

**The `Secure` cookie flag is a per-request decision, not a fixed one**
— `HostAwareSessionMiddleware` runs TWO `SessionMiddleware` instances
(both signing with the identical secret, so a cookie either one issues
is valid to the other) and picks between them based on whether the
request's `Host` header looks like localhost. Plain Starlette
`SessionMiddleware` only supports a fixed `https_only` flag set once at
construction, which can't express "Secure in production, plain http on
localhost during dev" for the same running process.

**`/ws/ticks` and the browser UI need no extra work** — a same-origin
browser WebSocket connection carries the session cookie automatically,
so the UI's own tick view just works once logged in. A script connecting
directly (see the updated example above) can't always set a custom
header on the WS handshake, so it passes the API key as a query param
instead (`?api_key=...`) — the **one** deliberate exception to "never
put the key in a URL" (query params on GET requests get logged by most
reverse proxies; everywhere else uses the header or cookie).

**The login page** (`static/login.html`) is served *inline* by
`AuthMiddleware` itself for any unauthenticated `GET /` — not a redirect
to a separate `/login.html` URL, which would itself need to be on the
allowlist. This keeps the allowlist at exactly the two paths above while
still making the login form reachable.

## What's here (Step 8: intraday_dtt_simple — a short straddle)

`app/strategies/intraday_dtt_simple.py` — a plain intraday short
straddle. Live paper-trading only, no backtested version. No pivots, no
SuperTrend, no candle aggregation — this one is pure time-of-day +
live-premium threshold logic:

- **Entry** (once per day, at `entry_time`, default 10:00): resolve
  THIS_WEEK ATM strike from the live spot price, **sell** the ATM CE and
  **sell** the ATM PE at that same strike — same lot count both legs.
- **Exit** — checked continuously once both legs are open, in priority
  order:
  1. **Profit target**: combined premium (CE + PE) has decayed
     `decay_pct` (default 10%) from the combined *entry* premium → exit
     both legs.
  2. **Stop loss**: *either* leg's own premium has risen `spike_pct`
     (default 40%) from *its own* entry premium → exit **both** legs,
     even though only one leg breached.
  3. **Time stop**: if neither fired, force-exit both legs at
     `force_exit_time` (default 15:00) — required here, not optional
     the way it is for `pivot_supertrend`, since the hard exit is one of
     this strategy's three defining rules.
- **Exactly one entry per day.** Once exited for any of the 3 reasons,
  no same-day re-entry — it waits for the next day's `entry_time`.

**How continuous exit monitoring works without REST polling.** Once
sold, both legs' `instrument_token`s are dynamically subscribed on the
dispatcher (same mechanism `pivot_supertrend_options` uses for
mark-to-market), so their live ticks continuously update
`dispatcher.last_prices`. Every subsequent *underlying* tick then checks
both legs' current prices via a plain in-memory dict lookup — REST
(`OptionsResolver.get_ltp`) is only ever called once per leg, at the
entry instant, to establish the entry price. No polling interval to
tune, no rate-limit risk from checking on every tick.

**"Late start" / catch-up entry** (config: `catch_up_late_entry`,
default `true`): if this strategy instance's *very first* observed tick
already shows a time-of-day past `entry_time` — deployed, or resumed,
after 10:00 with no entry yet today — this flag decides what happens:
`true` enters immediately on that first tick at the current spot price,
same as any other entry; `false` skips entry for the rest of *that day
only* — the next day's `entry_time` behaves completely normally, since
the flag only ever gates a fresh start/resume's very first tick, never a
normal day-to-day crossing. Deploying *before* `entry_time` (e.g. at
9:30 for a 10:00 entry) is never "late" — it just waits for 10:00 like
any other day, regardless of this flag.

**Deploy example:**

```bash
curl -X POST localhost:8000/deployments \
  -H 'Content-Type: application/json' -H "X-API-Key: $APP_AUTH_SECRET" -d '{
  "deployment_name": "dtt_simple_live_1",
  "strategy_name": "intraday_dtt_simple",
  "mode": "intraday",
  "initial_capital": 500000,
  "config": {
    "instrument_tokens": [256265],
    "symbol": "NIFTY 50",
    "options_underlying": "NIFTY",
    "expiry_selector": "THIS_WEEK",
    "entry_time": "10:00",
    "force_exit_time": "15:00",
    "decay_pct": 0.10,
    "spike_pct": 0.40,
    "lots_per_trade": 1,
    "catch_up_late_entry": true
  }
}'
```

`options_underlying` is required (the options chain's own `name`, e.g.
`"NIFTY"` — not the spot tradingsymbol `"NIFTY 50"`, see Step 5's
`INDEX_SPOT_SYMBOL` note). Same no-margin-model simplification as
`pivot_supertrend_options`: both SELL fills credit premium, `record_fill`
already treats sell-first/buy-later as a short position with
`realized_pnl = qty*(sell_price - buy_price)` per leg.

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
  "database_url": "postgresql://user:password@ep-xxxxx.neon.tech/dbname?sslmode=require",
  "app_auth_secret": "pick-a-real-password-here"
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

`app_auth_secret` is **unrelated to Kite entirely** — it's this app's
own front door (see "Step 7" below), required, no default. Pick a real
password; it doubles as both the UI login password and the value you
pass as `X-API-Key` for scripted access. The service refuses to start
without it.

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

**Protected like everything else** (see "Step 7" below) — the browser
UI's own tick view needs no extra work (its session cookie is sent
automatically, same-origin), but a script connecting directly, like the
example below, can't always set a custom header on a WebSocket handshake
— pass the API key as a query param instead, the one deliberate
exception to "never put the key in a URL":

```python
import asyncio, websockets, json

APP_AUTH_SECRET = "..."   # same value as config.json's app_auth_secret

async def main():
    url = f"ws://localhost:8000/ws/ticks?api_key={APP_AUTH_SECRET}"
    async with websockets.connect(url) as ws:
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
curl -X POST localhost:8000/deployments \
  -H 'Content-Type: application/json' -H "X-API-Key: $APP_AUTH_SECRET" -d '{
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

**`app/options/` (options utils)** was verified with a synthetic NFO
instrument chain — a realistic multi-expiry, multi-strike NIFTY chain
(weeklies + monthly futures) plus a stock (`RELIANCE`) with a
monthly-only chain and a different lot size, so the strategies-agnostic
logic couldn't accidentally special-case "just NIFTY". All expiries were
computed relative to `date.today()` (not hardcoded dates), so the test
stays valid regardless of what day it's actually run:

- **Expiry resolution**: `THIS_WEEK`/`NEXT_WEEK` correctly pick the 1st/
  2nd upcoming expiry; int offsets `0`/`1` match them exactly;
  `THIS_MONTH`/`NEXT_MONTH` correctly resolve to the last expiry within
  the target calendar month; explicit `date` and ISO-string selectors
  work; an unknown selector, an unlisted explicit date, and an unknown
  underlying are all rejected with a clean `ValueError`, not a crash
- **Strike step**: derived from the real listed strikes (not hardcoded)
  and matched the synthetic chain's actual ₹50 spacing
- **ATM resolution**: snapped correctly to the nearest *listed* strike,
  including a case where the raw spot price wasn't itself a multiple of
  the strike step
- **`get_leg_by_offset`**: "NEXT_WEEK ATM-10 CE" landed on exactly
  `ATM - 10 × step`, on the correct (next week) expiry
- **OTM/ITM directionality — checked explicitly, both option types**:
  CE OTM landed *above* spot, CE ITM *below*; PE OTM landed *below*
  spot, PE ITM *above* — the exact mirror-image relationship the
  implementation claims, not just "it returned something"
- **`get_leg_by_premium`** ("THIS_WEEK CE closest to premium 40"): its
  answer was checked against an **independent brute-force scan** — the
  test fetched quotes for the same strike window directly from the fake
  broker itself (bypassing the resolver's own code entirely) and
  confirmed the resolver picked the genuine arg-min, not merely *a*
  plausible-looking leg
- **`get_max_oi_strike`**: a single strike was deliberately given an
  outlier OI value 10 strikes away from ATM; the resolver found exactly
  that strike, proving it actually scans by OI rather than defaulting to
  ATM
- **`get_spot_price`**: verified both paths — a live-tick-cache hit made
  **zero** REST calls (asserted via a call counter on the fake
  `ltp()`), and a cache miss fell back to exactly one REST call,
  correctly resolving `"NIFTY"` → `"NSE:NIFTY 50"` via
  `INDEX_SPOT_SYMBOL`
- **REST client reuse**: two calls with the same `access_token` reused
  the identical `KiteConnect` instance; changing the token (simulating a
  daily re-login) produced a genuinely new client, not a stale one
- **Futures, lot size, `round_to_lot`, `list_option_chain`,
  `list_underlyings`**, and calling any util before a Kite login
  (`NoKiteSession`, not a crash) were all exercised too
- **Full existing regression suite re-run** (DB layer, full deployment
  lifecycle, dynamic subscription, onboarding/UI, pivot_supertrend math
  + live) against the dispatcher's credential-exposure change —
  zero regressions

Not yet run against real Kite instrument/quote data — the instrument-row
and `quote()`/`ltp()` response shapes are taken directly from
`kiteconnect`'s own source (`_parse_instruments`, `quote()`, `ltp()`),
not guessed.

**`pivot_supertrend_options`** was verified with the SAME synthetic
day1/day2 tick sequence used for `pivot_supertrend`'s own live
integration test, fed through the real API/dispatcher/broadcaster/
runner pipeline, plus a synthetic NFO options chain (fake `ltp()`
pricing legs relative to whatever the live underlying price actually is
at that instant, read the same way `get_spot_price`'s live-cache path
does):

- **Refactor safety first**: extracting `apply_seed_to_state` out of
  `PivotSupertrendStrategy` into a shared function (so this strategy
  could reuse it without duplicating it) was verified to be a pure
  extraction, not a behavior change — `pivot_supertrend`'s own math and
  live integration tests were re-run **unchanged** afterward and
  produced byte-identical trade timestamps/prices/P&L to before the
  refactor.
- **Same signal timing as pivot_supertrend**, different execution: fed
  the identical seeded day1 + live day2 sequence and got the same two
  entry/exit cycles at the same candle timestamps as the underlying
  version — direct proof the shared signal engine really is shared, not
  a look-alike reimplementation.
- **Correct leg per signal, checked on the actual trade records, not
  just logs**: the long signal's entry/exit pair was a PE (same
  tradingsymbol both times — sold then bought back, not some other
  contract); the short signal's pair was a CE.
- **Sell-to-open / buy-to-close, never the reverse** — asserted on
  every one of the 4 recorded fills' `action` field directly.
- **Resolved ATM strike tracks the live underlying price** at each
  entry instant (checked against the known signal-candle price from the
  seeded sequence).
- **Dynamic subscription lifecycle**: the option leg's token is
  subscribed on entry and unsubscribed the moment it closes — confirmed
  by reading `dispatcher.status` directly after each cycle, not
  inferred from log lines.
- **Resume-safety**: deployed, let it enter a position (sell a PE) but
  stopped short of the exit, then simulated a full process restart
  (fresh `app.*` module reimport, same DB) — the still-open option
  position survived intact, and — the actual point — `on_start()`
  correctly **re-subscribed the dispatcher to that specific option's
  token** on resume, confirmed directly against `dispatcher.status`.
- Full existing regression suite re-run one more time after these
  changes (DB layer, full deployment lifecycle, dynamic subscription,
  onboarding/UI, pivot_supertrend math + live, options resolver) —
  zero regressions.

Not yet run against a real Kite tick stream or real option premiums —
same caveat as everywhere else in this README: the tick/quote shapes are
taken from `kiteconnect`'s own source, not guessed, but this hasn't been
pointed at a live market.

**Application-level auth (`app/auth.py` + `app/routers/auth.py`)** was
verified with Starlette's `TestClient` — the one client that can drive
both plain HTTP *and* WebSocket connections against the same running app
with a real shared cookie jar (simulating an actual browser), against
the real local Postgres instance:

- **Every protected route 401s with no cookie and no `X-API-Key`** —
  checked directly across `/health`, `/deployments`, `/instruments`,
  `/strategies`, `/kite/login-url`, `/kite/status`, and `/auth/logout`
  in one pass, then confirmed each one passes again once authenticated —
  exactly the before/after check the spec asked for.
- **`GET /kite/callback` stays reachable with ZERO auth** — the one
  deliberate exception, tested explicitly (not just "not on the 401
  list") since it's the single easiest thing to accidentally lock down
  along with everything else: it correctly returns Kite's own
  status=failure page (400), never our 401.
- **`GET /` unauthenticated serves the login page's actual markup**, not
  a peek at the real UI (asserted the real UI's own markup is *absent*
  from that response, not just that *some* HTML came back) — and after a
  correct login, the exact same URL serves the real UI instead, with the
  login form gone.
- **Wrong password**: rejected with 401, and confirmed it grants
  **nothing** — a follow-up request with no other credentials still
  401s, not silently authenticated.
- **X-API-Key**: wrong value rejected, correct value accepted, checked
  against the identical routes as the cookie path.
- **A forged/tampered session cookie value is rejected** — manually
  injected a garbage cookie value (never issued by `/auth/login`) and
  confirmed it does NOT authorize anything, proving the signature check
  is actually doing something, not just present.
- **WebSocket `/ws/ticks`**: an unauthenticated connection is rejected
  (raises on connect, never accepts); a genuine session cookie's value —
  the same one the browser UI already holds after logging in — is
  accepted with no extra work; a wrong `?api_key=` query value is
  rejected; the correct one is accepted — covering the documented
  script-client path.
- **`Secure` cookie flag is genuinely per-request**, not just
  configured-and-hoped: logging in against a `localhost` host produces a
  cookie with **no** `Secure` flag (so plain-http local dev keeps
  working); logging in against a non-localhost host produces one WITH
  the `Secure` flag — checked directly on the raw `Set-Cookie` response
  header in both cases, on two separate app instances.
- **Logout genuinely clears the session** — after `POST /auth/logout`,
  the same cookie no longer authorizes anything, and `GET /` shows the
  login page again.
- **Full existing regression suite re-run** (DB layer, full deployment
  lifecycle, dynamic subscription, onboarding/UI, pivot_supertrend math
  + live, pivot_supertrend_options live, options resolver) — all updated
  to carry `app_auth_secret` in their config and an `X-API-Key` header
  on every request, all still pass — zero regressions from adding a
  required, fail-closed auth layer in front of the entire service.

One quirk surfaced *by* this testing, not a bug in the app: Starlette's
`TestClient.websocket_connect()` hardcodes its WebSocket request host to
`testserver` regardless of the client's configured `base_url` — a real
browser's WS connection is always same-origin with the page, so this
mismatch can't happen outside a test harness, but it did mean the
"browser-style" WS cookie check above passes the already-obtained signed
cookie value explicitly rather than relying on `TestClient`'s cookie jar
to cross-attach it across that host mismatch on its own.

Not yet run against a real reverse proxy / real TLS termination, or with
a real browser exercising the login/logout UI by hand — the behavior
above is verified at the HTTP/WebSocket protocol level, which is what
actually determines whether it's secure, but a manual click-through is
still worth doing before relying on this for anything with real money
behind it.

**`intraday_dtt_simple`** was verified end-to-end through the real
API/dispatcher/broadcaster/runner/`OptionsResolver`/Postgres pipeline,
with a synthetic NFO chain and a fake underlying tick feed:

- **Entry timing**: no position opens before `entry_time`; the straddle
  sells at 10:00 with the correct ATM strike, correct CE+PE
  tradingsymbols, correct qty (`lots_per_trade × lot_size`) and correct
  entry prices on both legs, both recorded with `side: "short"`.
- **Profit-target decay**: combined premium pushed down 31.6% (past the
  10% default threshold) via simulated option ticks — both legs
  correctly exited with `reason=profit_target_decay`, at the exact
  prices the simulated ticks carried.
- **Single-leg spike stop, and NOT mistaken for decay**: one leg pushed
  up 46% (past the 40% threshold) while the *combined* premium
  simultaneously **increased** (no decay at all) — confirmed the exit
  fired on `leg_spike_stop`, not `profit_target_decay`, proving the two
  checks are independently correct, not one masking the other.
- **Hard time stop**: mild moves that trip neither threshold, followed
  by a tick at/after 15:00 — both legs correctly force-exited with
  `reason=force_exit`.
- **No same-day re-entry**: fed more ticks the same day after an exit —
  confirmed no new position opens.
- **`catch_up_late_entry`, both settings**: a deployment whose first-ever
  observed tick is already at 11:30 (well past 10:00) enters immediately
  when `true`; skips entry for the rest of that day when `false` — and,
  critically, a **fresh day's normal 10:00 crossing on that SAME still-
  running instance is confirmed unaffected** by `false` from the day
  before, proving the flag only ever gates a fresh start's very first
  tick, not ordinary day-to-day operation.
- **Resume-safety**: entered a straddle, simulated a full process
  restart (fresh `app.*` reimport, same DB) — both legs' positions
  survived, `on_start` correctly re-subscribed **both** legs' tokens
  (checked directly against `dispatcher.status`), and — the actual
  point — a decay exit fed *after* the restart still fired correctly,
  proving the resumed instance reconstructed each leg's `avg_entry_price`
  from the DB correctly, not just "noticed a position exists."
- Full existing regression suite (auth, DB layer, deployment lifecycle,
  dynamic subscription, onboarding/UI, pivot_supertrend math + live,
  pivot_supertrend_options live, options resolver) re-run — zero
  regressions from adding a third strategy to the registry.

Not yet run against a real Kite tick stream or real option premiums —
same caveat as every other strategy in this README.

## Folder layout

```
live_deploy/
├── config.example.json        # copy -> config.json (gitignored). access_token now optional.
├── tokens.json                 # committed — which instruments to subscribe to
├── requirements.txt
├── static/
│   ├── index.html                # the UI — served at "/" by the FastAPI app itself
│   └── login.html                 # step 7 — served inline by AuthMiddleware, unauthenticated "/"
└── app/
    ├── main.py                  # FastAPI app, startup/shutdown wiring, static mount, middleware order
    ├── config.py                  # config.json / tokens.json loading
    ├── auth.py                     # step 7 — AuthMiddleware + HostAwareSessionMiddleware
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
    │   ├── strategies.py                      # GET /strategies
    │   └── auth.py                             # step 7 — POST /auth/login, /auth/logout
    ├── strategies/
    │   ├── __init__.py                         # import list — triggers registration
    │   ├── registry.py                          # @register_strategy
    │   ├── pivot_supertrend.py                   # step 4 — ports tg_int_st_pp's backtested rules to live ticks
    │   ├── pivot_supertrend_options.py            # step 6 — same signal engine, sells options instead
    │   └── intraday_dtt_simple.py                 # step 8 — short straddle, decay/spike/time exits
    └── options/
        ├── __init__.py                          # step 5 — public exports
        ├── models.py                             # OptionLeg
        ├── client.py                              # get_kite_connect() — reuses the dispatcher's session
        └── resolver.py                             # OptionsResolver — expiry/strike/leg/pricing utils
```

## Relationship to the rest of the repo

Fully isolated from the main `port` repo's Nifty 50 backtest pipeline,
from `generic/`, and from `tg_int_st_pp/`. Shares no code, no data, no
config with any of them. This is a live/real-time service, not a
backtest — it doesn't read or write anything under `data/`. Its own
persistence lives in whatever Neon database you point `database_url` at,
entirely separate from this repo's filesystem-based data.
