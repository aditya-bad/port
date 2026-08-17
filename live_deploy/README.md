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
  1. **Stop loss**: *either* leg's own premium has risen `spike_pct`
     (default 40%) from *its own* entry premium → exit **both** legs,
     even though only one leg breached. Checked first — a sharp
     one-sided move that spikes one leg while the other leg's decay
     drags the *combined* premium past `decay_pct` too, on the same
     tick, is real one-sided directional exposure, not calm two-sided
     decay, and the risk stop wins that tie.
  2. **Profit target**: combined premium (CE + PE) has decayed
     `decay_pct` (default 10%) from the combined *entry* premium → exit
     both legs.
  3. **Time stop**: if neither fired, force-exit both legs at
     `force_exit_time` (default 15:00) — required here, not optional
     the way it is for `pivot_supertrend`, since the hard exit is one of
     this strategy's three defining rules.
- **Exactly one entry per day.** Once exited for any of the 3 reasons,
  no same-day re-entry — it waits for the next day's `entry_time`.
- **Never skips a trading day, including the resolved contract's own
  expiry day** (config: `switch_to_next_week_on_expiry`, default
  `false`): selling options that expire that same afternoon is a
  fast-decay, sharp-gamma scenario, so this decides *which* contract
  gets traded, not *whether* to trade. `false` sells the same-day-expiry
  contract as resolved (opted into, same-day gamma and all); `true`
  re-resolves `NEXT_WEEK` instead, for that one entry only —
  `expiry_selector` itself is never touched, so every other day still
  resolves however it's configured to. Checked against the *actual
  resolved* expiry date (`expiry == ts.date()`, right after
  `resolve_expiry()` — before strike/leg resolution, before pricing,
  before subscribing anything), not a hardcoded weekday, since the
  weekly expiry day has changed before and isn't guaranteed to stay put.

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
    "catch_up_late_entry": true,
    "switch_to_next_week_on_expiry": false
  }
}'
```

`options_underlying` is required (the options chain's own `name`, e.g.
`"NIFTY"` — not the spot tradingsymbol `"NIFTY 50"`, see Step 5's
`INDEX_SPOT_SYMBOL` note). Same no-margin-model simplification as
`pivot_supertrend_options`: both SELL fills credit premium, `record_fill`
already treats sell-first/buy-later as a short position with
`realized_pnl = qty*(sell_price - buy_price)` per leg.

## What's here (Step 9: pivot_supertrend_options_inverse)

`app/strategies/pivot_supertrend_options_inverse.py` — deliberately the
mirror image of `pivot_supertrend_options`. Live paper-trading only, no
backtested version. No pivot levels at all — this one only cares about
SuperTrend flips:

- **Entry = every SuperTrend flip** (exactly where the original strategy
  used to *exit*): flip to red → **buy** THIS_WEEK ATM **PE**; flip to
  green → **buy** THIS_WEEK ATM **CE**.
- **Exit = purely time-based**: hold for `hold_candles` complete 5-min
  candles after entry (config, default `1`), then sell to close at the
  next candle's open — no more SuperTrend dependency on the way out,
  since the flip already *was* the entry trigger. A `force_exit_time`
  safety net (default 15:00, nullable to disable, same as
  `pivot_supertrend`) still applies in case a late-day flip's hold
  period would otherwise run past close.
- **Buying, not selling** — standard long-option mechanics (buy to
  open, sell to close), the opposite fill direction from
  `pivot_supertrend_options`.
- **Re-arms after every exit** — a flip can happen several times in a
  session; this isn't a one-trade-a-day strategy. Only one open position
  at a time — a flip that occurs *while already holding* one is simply
  missed, not queued for later.

**`hold_candles` timing**: entry executes at the open of the candle
right after the flip is detected (same "decide on close, act on next
open" convention every strategy in this family uses). From there,
`hold_candles` counts full candle-close events *including the entry
candle's own close* — `hold_candles: 1` exits at the very next candle's
open after entry; `hold_candles: 2` exits one candle further out.

**Resume-safety for the hold counter** is the interesting part here: the
candle count isn't stored anywhere durable, so it's reconstructed on
resume from the entry candle's own timestamp (stashed in the opening
fill's metadata) compared against whatever candle is actually observed
first, live, after resuming. If that reconstruction finds the hold
period has already fully elapsed during the pause, it exits immediately
rather than waiting around further — a deliberate one-directional
asymmetry (a resume can make this exit up to one candle *earlier* than
an uninterrupted run would have at the exact threshold candle, but never
later, since over-holding an options position is the worse failure
mode).

**Deploy example:**

```bash
curl -X POST localhost:8000/deployments \
  -H 'Content-Type: application/json' -H "X-API-Key: $APP_AUTH_SECRET" -d '{
  "deployment_name": "psoi_live_1",
  "strategy_name": "pivot_supertrend_options_inverse",
  "mode": "intraday",
  "initial_capital": 500000,
  "config": {
    "instrument_tokens": [256265],
    "symbol": "NIFTY 50",
    "options_underlying": "NIFTY",
    "expiry_selector": "THIS_WEEK",
    "atr_smoothing": "wilder",
    "hold_candles": 1,
    "force_exit_time": "15:00",
    "lots_per_trade": 1
  }
}'
```

No `prev_day_ohlc` or `pivot_type` config keys here — this strategy
never computes pivots, so seeding is SuperTrend-only
(`seed_candles`/`supertrend_seed`, identical meaning to
`pivot_supertrend`).

## What's here (Step 10: intraday_dtt_adjusted — dynamic rebalancing)

`app/strategies/intraday_dtt_adjusted.py` — `intraday_dtt_simple`'s ATM
straddle, but with a dynamic rebalancing layer instead of a fixed
40%/10% per-leg stop. Identical entry (10:00 ATM CE+PE, same
expiry-day exclusion, same "once per day" rule — literally reused via
a new shared `resolve_atm_straddle_legs()` function in
`intraday_dtt_simple.py`, not reimplemented) and identical 3:00 PM hard
exit. Everything between entry and 3pm is a real redesign — variable-
length legs per side (up to 3 on one side while the other stays at 1),
a running realized-P&L total, and five checks in a fixed priority order
every tick:

1. **Force-exit (3pm)** — closes everything, always wins.
2. **Break-even fallback** — `entry_spot ∓ combined_entry_premium`,
   checked against the live UNDERLYING price (not any option's
   premium). The true worst-case exit; closes everything.
3. **Profit target** (`decay_pct`, default 10%) — NOT replaced by the
   rebalancing layer, kept running the whole time. Compares
   `realized_pnl_today` (every leg closed earlier today via reversal-
   unwind) + unrealized P&L of every currently-open leg against
   `decay_pct × combined_entry_premium` — the ORIGINAL 2-leg entry
   premium only, deliberately NOT growing as adjustment legs add more
   premium (confirmed explicitly before writing any code — the
   alternative reading was real and is called out in the module
   docstring). Closes everything on hit.
4. **Adjustment trigger** — symmetric, either side can be "bigger":
   `smaller_side_total <= adjustment_trigger_ratio × bigger_side_current`
   (default ratio 0.5, validated strictly between 0 and 1 at deployment
   creation — see below). Sells one more leg of the smaller side's
   option type at whichever strike's live premium is closest to
   `adjustment_size_pct × bigger_side_current` (default 25%) — via a
   new `OptionsResolver.get_leg_by_premium(..., exclude_strikes=...)`
   parameter (see Step 5) that keeps it off any strike already held on
   that side. Side identity is STICKY once the first adjustment fires;
   `max_adjustments` (default 2) is a LIFETIME cap for the day, not a
   concurrently-open count — it doesn't reset even if every adjustment
   leg later gets fully unwound.
5. **Reversal / unwind** — once the adjusted side has more than 1 leg:
   `smaller_side_total >= bigger_side_current` closes exactly the
   single CHEAPEST leg on that side (original leg included, competing
   on equal footing), re-evaluated fresh each time — ties break toward
   the EARLIEST-OPENED leg.

`adjustment_trigger_ratio` is rejected at deployment creation (HTTP 400)
unless it's strictly between 0 and 1. The adjustment trigger (step 4,
`<= ratio`) and the reversal trigger (step 5, `>=`) are only guaranteed
mutually exclusive — never both true on the same tick — when
`ratio < 1.0`; the tick-handling code relies on that guarantee to return
immediately after acting on the adjustment trigger, without also
checking reversal-unwind that same tick. A ratio `>= 1.0` would let the
two overlap and silently skip a reversal-unwind that should have fired.
`ratio <= 0` is separately nonsensical (never fires, or compares against
a premium that can't be zero or negative).

**Deploy example:**

```bash
curl -X POST localhost:8000/deployments \
  -H 'Content-Type: application/json' -H "X-API-Key: $APP_AUTH_SECRET" -d '{
  "deployment_name": "dtt_adjusted_live_1",
  "strategy_name": "intraday_dtt_adjusted",
  "mode": "intraday",
  "initial_capital": 1000000,
  "config": {
    "instrument_tokens": [256265],
    "symbol": "NIFTY 50",
    "options_underlying": "NIFTY",
    "expiry_selector": "THIS_WEEK",
    "entry_time": "10:00",
    "force_exit_time": "15:00",
    "decay_pct": 0.10,
    "adjustment_trigger_ratio": 0.5,
    "adjustment_size_pct": 0.25,
    "max_adjustments": 2,
    "adjustment_strike_window": 40,
    "lots_per_trade": 1,
    "catch_up_late_entry": true,
    "switch_to_next_week_on_expiry": false
  }
}'
```

**Resume-safety** here is the hard part: on restart, `on_start()`
reattaches every open leg (role — `"original"` or `"adjustment_N"` —
read back from that leg's own stored fill metadata), and if any leg is
open, that leg's own `opened_at` date IS "today" for reconciliation —
no need to wait for a live tick. `runner.list_closed_positions()` (a
new sanctioned DB-access method on `DeploymentRunner`, alongside
`buy()`/`sell()`/`open_positions` — see Step 2) is then used to
reconstruct `realized_pnl_today` (summed from every position closed
*earlier the same day*) and `adjustments_used` (the highest adjustment
index seen across open AND closed-today legs — open-only would
under-count a leg that was already unwound before the restart). Getting
either of these wrong is a real correctness bug, not a cosmetic one —
see the module docstring and the "Verified" section below for how this
was tested: not by peeking at internal state, but by choosing post-
restart price moves whose own unrealized P&L is deliberately too small
to explain a result on its own, so the outcome is only correct if the
reconstructed value was genuinely carried forward.

## What's here (Step 11: intraday_dtt_advanced — rolling adjustments)

`app/strategies/intraday_dtt_advanced.py` — `intraday_dtt_adjusted`, but
as an actual **Python subclass** (`IntradayDTTAdvancedStrategy(IntradayDTTAdjustedStrategy)`),
not a fork of that ~760-line file. Entry, the profit-target check, the
break-even check, and reversal-unwind are all inherited unchanged. Two
real behavioral differences:

1. **Adjustments roll instead of permanently stopping.**
   `max_adjustments` here caps how many adjustment legs may be
   *concurrently* open on the adjusted side (never more than
   `1 + max_adjustments` legs total) — not a lifetime total the way it
   is in `intraday_dtt_adjusted`. Once at that cap, a further trigger
   **rolls**: closes the single cheapest currently-open leg on that side
   (original leg competes on equal footing, exactly as it already does
   for ordinary reversal-unwind — nothing here treats it as
   permanently reserved), then immediately reopens one new leg sized off
   the *current* bigger-side premium at the moment of the roll. No
   lifetime ceiling on how many times this can happen in a day.
2. **`breakeven_multiplier`** (default `1.0`, matching
   `intraday_dtt_adjusted` exactly): `entry_spot ± breakeven_multiplier
   × combined_entry_premium`, instead of a fixed 1.0× band.

**The seams that make this a clean subclass rather than a fork** live
in `intraday_dtt_adjusted.py` itself: `self.breakeven_multiplier`
(defaulted to a no-op `1.0`, not part of that strategy's own documented
config) is what both break-even computation sites already multiply by,
so no override is needed there at all; and `_handle_adjustment_trigger`
is a new extension point — `_maybe_manage` no longer inlines the
lifetime-cap check itself, it just calls this method once the trigger
condition is confirmed true. `intraday_dtt_advanced` overrides only
that one method; its roll implementation is literally
`_unwind_one(..., reason="roll_close")` followed by
`_adjust(..., reason="roll_open")` — both already-tested base-class
methods, reused directly, with a `reason` parameter added to each so a
roll's two fills are distinguishable from an ordinary adjustment or
reversal-unwind in the trade history.

**Resume-safety is simpler here than in the adjust version**: since the
concurrent cap only ever needs "how many non-original legs are open on
the adjusted side *right now*" (`len(self.legs[side]) - 1`), and that's
already exactly what leg reattachment from `runner.open_positions`
rebuilds, no extra reconstruction step is needed for it — unlike
`intraday_dtt_adjusted`'s lifetime `adjustments_used` counter, which
does need closed-today history (a since-unwound leg still counts toward
a lifetime total, but not toward a live count). `_resume_from_db` is
inherited unchanged and needs no override.

**Deploy example:**

```bash
curl -X POST localhost:8000/deployments \
  -H 'Content-Type: application/json' -H "X-API-Key: $APP_AUTH_SECRET" -d '{
  "deployment_name": "dtt_advanced_live_1",
  "strategy_name": "intraday_dtt_advanced",
  "mode": "intraday",
  "initial_capital": 1000000,
  "config": {
    "instrument_tokens": [256265],
    "symbol": "NIFTY 50",
    "options_underlying": "NIFTY",
    "expiry_selector": "THIS_WEEK",
    "entry_time": "10:00",
    "force_exit_time": "15:00",
    "decay_pct": 0.10,
    "adjustment_trigger_ratio": 0.5,
    "adjustment_size_pct": 0.25,
    "max_adjustments": 2,
    "adjustment_strike_window": 40,
    "breakeven_multiplier": 1.0,
    "lots_per_trade": 1,
    "catch_up_late_entry": true,
    "switch_to_next_week_on_expiry": false
  }
}'
```

Note `max_adjustments` means something structurally different here
(concurrent cap) than in `intraday_dtt_adjusted` (lifetime cap) — same
config key name, deliberately, since it plays the same role, but the
semantics genuinely differ between the two strategies; called out
explicitly in both modules' docstrings rather than left to be noticed
by diffing the two files.

## What's here (Step 12: strangle_monthly_v2 — a monthly checkpoint-cycling strangle)

`app/strategies/strangle_monthly_v2.py` — the most complex strategy in
this family: a **monthly** (not intraday) short strangle on
NIFTY/BANKNIFTY (NSE) or SENSEX/BANKEX (BSE), locked to one specific
option **contract** for its entire life, that keeps re-entering itself
every time a capital-based checkpoint fires, rebalances continuously
AND on a fixed daily schedule, and can converge from a strangle into a
straddle with three selectable behaviors for what happens next — one of
which reuses `intraday_dtt_adjusted`'s own adjustment machinery
directly. No backtested version exists; live paper-trading only.

1. **Contract lock, not just a signal state.** Rotation (day 1-15 of
   the month → that month's own contract; day 16-end → next month's) is
   applied ONLY at the moment of a fresh entry — initial, or the very
   next entry after a checkpoint flatten. Once locked in,
   `self.contract_expiry` (an actual `date`) is used for every roll,
   every EOD adjustment, and every hedge placement for that position's
   entire life, even if the calendar crosses day 16 while it's still
   open. `force_close_at_contract_expiry` (default `true`, visible in
   config specifically so it can be turned off deliberately) force-
   closes a position that's still open at its own contract's expiry.
2. **Checkpoint-triggered re-entry.** `checkpoint_pct` (default 0.5% of
   `runner.initial_capital` — FIXED for the deployment's entire
   lifetime, checked every tick — not `monthly_target_pct`, which is
   informational only, logged so progress against it is visible but
   never itself a hard stop) closing the whole position and
   IMMEDIATELY selling a fresh CE+PE pair, independently RE-APPLYING
   the rotation rule against today's date — a checkpoint that fires on
   day 20 can re-enter in next month's contract even though the
   position it just closed was this month's. The bar itself does NOT
   rise as the account compounds — REVISED from an earlier version of
   this file, which used the current, compounding `runner.cash` here;
   see "Fixes applied after initial build" below.
3. **Two independent rebalancing mechanisms, generalized over 1 or 2
   legs per side**: a continuous 50%-of-bigger-side trigger that always
   REPLACES the cheapest leg on the decayed side in place (never grows
   leg count), and a fixed-time (default 15:13, deliberately just
   before NSE/BSE's 15:15 closing-auction transition) daily 80% check
   that GROWS a side from 1→2 legs on its first breach, then REPLACES
   the cheapest of the (at most two) extra legs on every breach after
   that — the side's longest-held ("protected") leg is never a
   replacement candidate for this daily check specifically, tracked via
   an explicit open-order sequence stamp rather than list position.
4. **Convergence** (`convergence_mode`): once repeated rolls bring both
   strikes to the same strike, `"fixed_stop"` (default) snapshots the
   combined premium and stops `convergence_stop_pct` (default 10%)
   above it, never recalculated; `"trailing_stop"` recalculates that
   stop continuously off the CURRENT combined premium, trailing down as
   the position decays favorably; `"active_management"` hands post-
   convergence rebalancing entirely to `intraday_dtt_adjusted`'s own
   methods (bound onto this instance, not reimplemented — see the
   module docstring's "ACTIVE-MANAGEMENT DELEGATION"). The checkpoint
   check keeps running, unmodified, in every mode, before AND after
   convergence — but the daily 80% check and the continuous 50% trigger
   do NOT: under `fixed_stop`/`trailing_stop` specifically, BOTH freeze
   entirely the instant convergence is detected, leaving the position at
   exactly the 2 legs it converged with until the stop fires (or
   checkpoint/contract-expiry closes it out); `active_management` is the
   one mode where both keep running post-convergence exactly as before
   (Section 6 unmodified, Section 5 replaced by the delegation). See
   "Fixes applied after initial build" below for why the freeze exists.
5. **Optional protective-leg hedging** (`enable_hedging`, default
   `false` — new mechanics, never combined with the rest of this
   strategy's logic before, so it's kept in its own clearly-separated
   section of the code and tested in isolation first): a long leg per
   short, sized at ~10% of the short's premium for BANKNIFTY/BANKEX or
   a flat `hedge_flat_premium` (default ₹4) for NIFTY/SENSEX, rolling
   in lockstep with its short to maintain a fixed POINT distance (not
   premium). Every roll/replace executes in REVERSED order from entry's
   own CE-then-PE ordering: close old short → close old protective →
   open new protective → open new short, so short exposure never exists
   without its hedge already in place.

**Position sizing** follows the spec's own worked example exactly:
`(capital × strike_selection_capital_pct) / lot_size / 2` — for
NIFTY (lot_size 50) at ₹1,20,000 capital that's exactly **36**; for
BANKNIFTY (lot_size 25), **72**. "Capital" here is
`runner.initial_capital` (a new fixed-for-the-deployment's-lifetime
accessor added to `DeploymentRunner` alongside the existing
compounding `runner.cash`) — and, as of the fix described below,
quantity is fixed too: `qty = lots_per_trade * lot_size`, for every
leg this deployment ever opens (entry, rolls, EOD-accumulation legs,
hedges alike), for its entire lifetime. `runner.cash` is read by
NEITHER the sizing formula NOR the checkpoint target — see "Fixes
applied after initial build" below.

**Trade-reason logging** extends the same one-fill-per-action pattern
already established for `intraday_dtt_adjusted`/`intraday_dtt_advanced`
(`runner.sell()`/`buy()`'s own `metadata` parameter) with a richer,
consistently-shaped payload on every single fill: `trigger` (which of
the five priority-ordered paths caused it), `action`, `leg`, `strike`,
`trigger_values` (the actual numbers that made the condition true —
enough to independently re-verify it without cross-referencing other
log lines), `target_basis` (target premium / selected strike / fill
premium), and `resulting_state` (both sides' leg counts/strikes/roles
immediately after that fill). One flagged, documented exception: fills
placed by the `active_management` delegation path carry
`intraday_dtt_adjusted`'s own (simpler) metadata shape instead, since
those `runner.sell()` calls are reused completely unmodified — see the
module docstring's own "KNOWN LIMITATION" note.

**Deploy example:**

```bash
curl -X POST localhost:8000/deployments \
  -H 'Content-Type: application/json' -H "X-API-Key: $APP_AUTH_SECRET" -d '{
  "deployment_name": "strangle_monthly_v2_live_1",
  "strategy_name": "strangle_monthly_v2",
  "mode": "positional",
  "initial_capital": 120000,
  "config": {
    "instrument_tokens": [256265],
    "symbol": "NIFTY 50",
    "instrument": "NIFTY",
    "strike_selection_capital_pct": 0.03,
    "monthly_target_pct": 0.02,
    "checkpoint_pct": 0.005,
    "entry_time": "10:00",
    "enter_immediately_on_deploy": false,
    "enable_hedging": false,
    "adjustment_trigger_ratio": 0.5,
    "adjustment_band_min": 0.80,
    "adjustment_band_max": 0.95,
    "eod_check_time": "15:13",
    "eod_gap_floor": 0.80,
    "convergence_mode": "fixed_stop",
    "convergence_stop_pct": 0.10,
    "max_adjustments": 2,
    "force_close_at_contract_expiry": true
  }
}'
```

For SENSEX/BANKEX, set `"instrument"` accordingly and
`"instrument_tokens"`/`"symbol"` to the SENSEX spot token added to
`tokens.json` (see Setup, below) — the resolver is then constructed
with `exchange="BFO"` automatically; no other config changes needed.

**What this session flagged rather than silently assumed** (see the
module docstring's own Section 9 and the "Verified" section below for
the full detail): whether `kite.instruments("BFO")` really returns
SENSEX/BANKEX rows with the expected shape, and whether the underlying
Kite Connect account actually has BSE F&O market data permissions, are
both **external facts this sandboxed environment cannot check** —
confirm both independently before relying on SENSEX/BANKEX in
production. Everything else about BFO support (spot routing, dynamic
lot-size/strike-step derivation, the resolver's `exchange` plumbing)
was verified by reading the actual resolver code, not assumed.

**Fixes applied after initial build** — two correctness fixes to
already-built behavior, requested after the strategy first shipped:

1. **Post-convergence freeze for `fixed_stop`/`trailing_stop`.**
   Originally, the daily 80% check (Section 6) and the continuous 50%
   trigger (Section 5) kept running, unmodified, in EVERY
   `convergence_mode`, even after convergence — correct for
   `active_management` (which explicitly wants EOD to keep running and
   replaces Section 5 with its own delegation), but wrong for
   `fixed_stop`/`trailing_stop`: a post-convergence Section 5 roll could
   silently turn a converged straddle back into a strangle, and a
   post-convergence Section 6 leg-add injects that leg's own premium
   into `combined_now`, corrupting the stop calculation with an
   artifact that has nothing to do with real market movement (converge
   at 600, stop at 660, drift to a harmless 630 — then an EOD-added leg
   worth ~82.5 pushes `combined_now` to 712.5, tripping the stop on zero
   real loss). Fixed: for `fixed_stop`/`trailing_stop` specifically,
   BOTH Section 5 and Section 6 go fully dormant the instant
   `self.converged` becomes `True` — the position sits at exactly the 2
   legs it converged with until the stop fires or checkpoint/
   contract-expiry closes it out. `active_management` is unaffected.
2. **`initial_capital` only, never `runner.cash`, for sizing AND the
   checkpoint target.** Originally, `qty_multiplier =
   round(runner.cash / runner.initial_capital)` let quantity silently
   grow as checkpoints compounded the account, and the checkpoint
   target itself (`checkpoint_pct * runner.cash`) rose along with it —
   both now read `runner.initial_capital` exclusively.
   `qty_multiplier` is gone entirely; quantity is simply `lots_per_trade
   * lot_size`, identical for every leg this deployment ever opens.
   `checkpoint_pct * runner.initial_capital` is the fixed bar for the
   deployment's whole lifetime, unmoved by realized P&L or cash growth.
   Scaling to more size is now a deliberate `lots_per_trade` config
   change, never automatic behavior. `runner.cash` is still logged
   (`capital_now` in Section 12's trigger_values) as a genuinely useful
   record of what cash happened to be at that moment — it just no
   longer feeds either calculation.

Both fixes are verified end-to-end (see "Verified" below): a dedicated
scenario reproduces the exact converge-600/stop-660/drift-630 worked
example, feeding numbers that would have tripped a leg-add or a roll
under the old behavior and confirming neither happens; and a checkpoint
fired twice, on cycles with deliberately different realized-P&L/cash
history, confirming the logged `checkpoint_target` is identical both
times even though `capital_now` demonstrably isn't.

## What's here (Step 13: UI redesign — a real multi-view app)

The original UI was one HTML file with three sections stacked on a
single scrolling page, and deployment detail was an inline expand-in-
place panel — fine for one strategy, doesn't scale to seven strategies
with many deployments each and the richer trade metadata
`strangle_monthly_v2` (and, going forward, every other strategy) writes.
This is a genuine redesign: four real views with real navigation between
them, not more content stacked onto the existing page.

**Navigation** — a persistent left sidebar, deliberately kept to three
items (this is a small personal tool, not enterprise software):
**Dashboard**, **Strategy Catalog**, **Deployed Strategies**. Strategy
Detail is reached by clicking into a deployment row, not a fourth
sidebar destination — it's a drill-down (`#/deployments/{id}`), not a
peer view. Routing is a small hash-based router in `index.html`'s own
inline `<script>` (`#/dashboard`, `#/catalog`, `#/deployments`,
`#/deployments/{id}`) — still no frontend framework, same vanilla-JS
style as before, just enough for the browser's back/forward buttons and
page refresh to behave like real navigation.

`static/index.html`'s JS (498 lines before this step, and still growing)
is now split into `static/js/{api,dashboard,catalog,deployments,detail}.js`
— `api.js` owns every fetch wrapper and the formatting/badge helpers
every other file shares (so `fmtMoney`, P&L coloring, and date formatting
can never quietly drift between views); the other four each own exactly
one view's rendering.

1. **Dashboard** — the cross-strategy birds-eye view, nothing here is
   per-deployment. Aggregate P&L (realized + unrealized, summed across
   active/paused deployments, with a small per-deployment breakdown),
   deployment status counts, a consolidated open-positions table across
   every deployment, and a recent-activity feed (latest 20 fills across
   everything, newest first). The positions table and activity feed are
   backed by two new endpoints (`GET /positions`, `GET /trades/recent`
   — see below) rather than N+1 client-side fetching across every
   deployment. The old "Subscribed Instruments" section (no natural home
   among the 4 real views, and not worth a 4th sidebar item for how
   rarely it's touched) lives at the bottom of this page instead of
   being dropped.
2. **Strategy Catalog** — the existing card list, upgraded with a count
   of how many currently-active deployments are running each strategy
   (derived from the same `/deployments` list already needed elsewhere —
   no new endpoint). Deploy modal unchanged.
3. **Deployed Strategies** — a real filterable table now (status +
   strategy-type filters — necessary, not polish, once there are many
   deployments across seven strategies), with running P&L (realized AND
   unrealized) pulled directly into each row so "is this currently
   winning or losing" doesn't need a click-through. Pause/Resume/Stop
   stay available right from the row; clicking the row itself (not a
   button) navigates to Strategy Detail.
4. **Strategy Detail** — header (name, strategy, status, action buttons)
   + 4 tabs:
   - **Config** — the deployment's actual running config as a readable
     key/value table, not a raw JSON dump (nested values still shown as
     compact JSON within their own cell).
   - **Positions** — unchanged from before.
   - **Trades** — the tab the trade-reason logging work was actually
     for. The visible table stays scannable (time/action/symbol/price/
     reason) — `trigger_values`/`target_basis`/`resulting_state` are
     NEVER crammed into columns. Click a row to expand it and see the
     full metadata: `trigger_values`/`target_basis`/`resulting_state`
     get their own labeled blocks when a strategy's metadata has them
     (`strangle_monthly_v2`'s own Section 12 schema, and — since Step
     14 — every other strategy's too); everything else in that fill's
     metadata renders verbatim in an "other metadata" block, so nothing
     is ever silently dropped or renamed regardless of which strategy
     wrote it. Each reason also gets a small colored
     **trigger-type badge** (keyword-matched — `stop`/`force_exit`/
     `spike`/`backstop` → red, `profit_target`/`checkpoint`/`decay` →
     green, `roll`/`adjust`/`eod`/`gap`/`unwind`/`converg`/`flip` →
     amber, `entry` → blue), so a long trade list can be scanned for
     every stop-loss or every checkpoint at a glance. The vocabulary
     genuinely differs strategy to strategy; the CATEGORY-to-color
     mapping doesn't need to (and isn't meant to) match byte-for-byte
     across strategies, only stay internally consistent.
   - **Stats** — the old Report tab's content (realized P&L, win rate,
     open/closed counts, avg win/loss) plus three genuinely new things:
     a **trigger breakdown** (count of trades per `reason` — if a
     strategy is expected to hit checkpoints regularly and this shows
     zero, that's an immediate, visible signal without reading a single
     log line), **average holding period** (from closed positions' own
     `opened_at`/`closed_at`, no new backend needed), and an **equity
     curve** — a single inline SVG `<polyline>` over that deployment's
     recorded snapshots (see "Equity-curve snapshots" above), deliberately
     not a charting library. Fewer than 2 snapshots shows an explicit
     "not enough data yet" state, never a fabricated flat line.

**Two real bugs caught and fixed during this step's own testing** (both
found via a full headless-browser pass, not just the unit-style HTTP
tests — see "Verified" below):
1. The hash router's view-resolution originally couldn't distinguish
   `#/deployments` (the list) from `#/deployments/{id}` (the drill-down)
   — `"deployments"` matched the valid-views list either way, so
   `Detail.load()` was silently never called for any real deployment
   link; only navigating there DIRECTLY via JS (bypassing the router
   entirely, exactly what the first, HTTP-only test pass had done)
   masked it. Fixed by checking for a param specifically before falling
   back to the bare view-name match.
2. `list_lots`'s SQL gained a `JOIN` onto `positions` for the new
   `symbol` column — logically correct, but it changed Postgres's query
   plan enough to perturb row order for two fills sharing the exact same
   `executed_at` (a roll's close-then-open pair, timestamped identically
   by the strategy that placed them) — previously stable in practice,
   though never actually guaranteed by the old query either. Fixed by
   using a correlated subquery for `symbol` instead of a `JOIN`, which
   leaves the primary scan (on `position_lots` alone, filtered +
   ordered) structurally unchanged from before this column was added.

## What's here (Step 14: trade-reason logging retrofit)

`strangle_monthly_v2` (Step 12) was the first strategy to carry the
full five-field metadata schema on every fill — `trigger` (the specific
rule that fired), `action` (open/close + which position), `trigger_values`
(the actual numbers that made the condition true at that moment — enough
to independently recompute whether it was genuinely true, without
re-running the strategy or trusting the code was right), `target_basis`
(only where a strike/premium selection actually happened — what was
targeted, what was selected, what it filled at), and `resulting_state`
(a compact snapshot of the position immediately after this fill). This
step brings the other six strategies up to the same standard:
`pivot_supertrend`, `pivot_supertrend_options`,
`pivot_supertrend_options_inverse`, `intraday_dtt_simple`,
`intraday_dtt_adjusted`, `intraday_dtt_advanced`.

**Shared helper, not six copies**: `app/strategies/trade_meta.py`'s
`build_trade_meta(trigger, action, trigger_values=None,
resulting_state=None, target_basis=None, **extra)` assembles the common
dict shape exactly once — `target_basis` is genuinely OMITTED (not even
as `{}`) unless a caller passes one, since it doesn't apply everywhere
(see below). It's a plain module-level function, not a mixin or base-
class method — deliberately, since `_adjust`/`_unwind_one`/`_flatten_all`
in `intraday_dtt_adjusted.py` are ALSO reused, unmodified, by
`strangle_monthly_v2`'s `active_management` convergence mode via
unbound-method binding onto a `StrangleMonthlyV2Strategy` instance (see
that module's "ACTIVE-MANAGEMENT DELEGATION" section) — a method that
only exists on `IntradayDTTAdjustedStrategy` would `AttributeError` the
moment `self` turns out to be that other class. Every metadata-building
call in the retrofit works from a plain function of the values already
in scope, never assuming anything about `self`'s actual type. The one
piece of PER-STRATEGY shared state (`_legs_snapshot(legs)`, the compact
`{"CE": [...], "PE": [...]}` `resulting_state` snapshot,
`intraday_dtt_adjusted`'s analogue of `strangle_monthly_v2`'s own
`_snapshot_state`) is the same kind of plain function for the same
reason.

**Adapted per strategy, not forced to a uniform shape** — exactly what
the schema's own five fields mean differs where the underlying strategy
genuinely differs:
- `pivot_supertrend` trades the underlying directly, not options —
  `target_basis` is omitted entirely on every fill (not forced to exist
  with nothing meaningful in it). Triggers: `pivot_break_long` /
  `pivot_break_short` (entries — `trigger_values` carries the close
  price, the trend, WHICH specific pivot level broke and its value, and
  the full R/S level set for context), `st_flip` / `force_exit` (exits
  — `trigger_values` carries the trend before/after and the close that
  caused the flip, or the candle time vs. the cutoff).
- `pivot_supertrend_options` — the identical signal engine as
  `pivot_supertrend` (imported, not duplicated), but selling an ATM leg
  instead of the underlying — `target_basis` is `{"selection_basis":
  "ATM", "selected_strike", "fill_premium"}` on every entry (an ATM pick
  has no PREMIUM target the way `intraday_dtt_adjusted`'s adjustment
  legs do — just a strike rule and what it filled at).
- `pivot_supertrend_options_inverse` — same ATM `target_basis` shape;
  triggers are `st_flip_entry_ce`/`st_flip_entry_pe` (the flip itself IS
  the entry here, mirror image of the two strategies above) and
  `hold_expired`/`force_exit`. The RESUME-CRITICAL `entry_candle_date`
  metadata key (read back by `on_start` to reconstruct the hold counter
  after a restart — see that module's own docstring) is preserved
  verbatim, merged in via `build_trade_meta`'s `**extra`, never renamed.
- `intraday_dtt_simple` — continuous tick-check, not candle-deferred
  (unlike the three above, no detection/execution timing split — every
  trigger's numbers are read directly out of local scope at the call
  site). Triggers: `entry_time_reached`, `profit_target_decay`,
  `leg_spike_stop`, `force_exit`. `target_basis` is the same ATM shape.
- `intraday_dtt_adjusted` — the most complex case: `target_basis` on an
  `_adjust` fill is a genuine PREMIUM target (`{"target_premium",
  "selected_strike", "fill_premium"}`, since `_adjust` really does aim
  at `adjustment_size_pct * bigger_now`), distinct from the ATM-only
  shape above. `resulting_state` is a running PER-FILL snapshot — each
  fill shows the book exactly as it stood immediately after THAT fill,
  not after the whole multi-leg event finishes (a 3-leg flatten's first
  close still shows 2 legs remaining; only the last shows fully flat).
  All five RESUME-CRITICAL metadata keys (`leg_role`, `leg`, `strike`,
  `exchange`, `entry_spot`) are preserved verbatim. `_adjust`/
  `_unwind_one` compute their own `trigger_values` INTERNALLY, branching
  on the `reason` they were called with: the ordinary adjustment/
  reversal condition (`smaller_total`/`bigger_now`/the trigger ratio)
  for `reason="adjustment"`/`"reversal_unwind"`, versus the concurrent-
  cap condition (`concurrent_legs_before_roll[_open]`) for
  `reason="roll_open"`/`"roll_close"` — a roll's trigger_values never
  claim the ordinary condition fired, since it usually hasn't.
- `intraday_dtt_advanced` — a SUBCLASS that overrides only
  `_handle_adjustment_trigger`, reusing `_adjust`/`_unwind_one` directly
  with `reason="roll_open"`/`"roll_close"`. This retrofit added NO code
  to this file at all: because the base class computes `trigger_values`
  from parameters already in its own signature (`side`, `bigger_now`,
  `prices` — never a NEW parameter the subclass would also need to
  pass), every one of this file's existing call sites picked up the full
  schema automatically the moment the base class was retrofitted.

**Resume-safety discipline carried through unchanged**: every metadata
key any strategy's own `on_start()` reads back to reconstruct in-memory
state after a restart (see each module's own docstring) was verified
still present, unrenamed, after the retrofit — the retrofit is
ADDITIVE only. The DB `reason` column (and every existing test's
assertions against it, and `static/js/api.js`'s `triggerBadge()`
keyword classifier, which is keyed off `reason`, not `trigger`) was
never changed either — `trigger` in metadata is sometimes MORE specific
than `reason` (`pivot_supertrend`'s `reason="entry"` fills carry
`trigger="pivot_break_long"`/`"pivot_break_short"`), never a competing
source of truth for the same fill.

## What's here (Step 15: instrument browser, manual Kite login, credential hardening)

Three additions, genuinely separate concerns, described together here
because they landed in one pass.

**Instrument browser** — `GET /instruments` (existing, unchanged) only
ever showed what was ALREADY subscribed, and `POST /instruments` only
ever accepted a raw numeric `instrument_token` — there was no way to
find one by symbol/name at all; you had to already know it. New `GET
/instruments/search?q=...` searches Kite's instrument master by
symbol/name substring, case-insensitive, matched against BOTH
`tradingsymbol` (finds a specific contract like
`NIFTY26AUG24700CE`) AND the underlying `name` (finds it by searching
just `"NIFTY"` too) — across NSE/NFO/BSE/BFO by default (the same four
exchanges `strangle_monthly_v2`'s own `SUPPORTED_INSTRUMENTS` already
trades on). Implemented as `OptionsResolver.search_instruments()`,
reusing the EXACT SAME per-exchange instrument-master cache
(`_ensure_instruments`, refreshed once per calendar day) every other
resolver method already relies on — no separate fetch, no separate
cache, no re-fetching Kite's multi-thousand-row dump on every keystroke.
Results are capped (`limit`, default 30) since a common substring like
`"NIFTY"` would otherwise mean every listed NIFTY option contract.
New **Instruments** page in the sidebar (a real 5th nav item, not
tucked into Dashboard) — a debounced search box, a results table with a
per-row **Subscribe** button, and the currently-subscribed list with
**Unsubscribe** — both actions wired to the ALREADY-EXISTING `POST
/instruments` / `DELETE /instruments/{token}` endpoints, which needed no
changes at all. Dashboard's own small "Subscribed Instruments" widget
(and its raw-token-entry modal) is untouched — this is a new, better
front door for the same underlying subscription mechanism, not a
replacement for the old one.

**Manual Kite login** — an ALTERNATIVE to the popup/redirect flow, not
a replacement (that flow, `GET /kite/login-url` + `GET /kite/callback`,
works completely unchanged). For someone who already completed Kite's
own login in a separate tab/window and has the `request_token` from the
resulting redirect URL's query string, without wanting to go through
this app's own popup again. New `POST /kite/manual-login` — JSON in,
JSON out (matching how the rest of this SPA already does every other
write — deploy, subscribe/unsubscribe — unlike `/kite/callback`'s HTML
response, which exists for a raw browser redirect Kite itself controls,
not a `fetch()` caller). Both endpoints now share one
`_complete_kite_login()` helper (generate_session + persist to
`kite_sessions` + `dispatcher.reconnect()`) so there is exactly one
implementation of "what a successful Kite login does to this process's
state," not two that could drift apart. **Verified** to produce the
IDENTICAL end state as the redirect flow (checked via `/kite/status`
after each, independently, fresh app/DB each time — not one riding on
the other's success).

`api_key`/`api_secret` are OPTIONAL on the manual-login form: omitted
(the common case), it reuses this app's already-configured credentials;
provided, they're used for that ONE `generate_session` call only.
**This is where it connects to credential hardening below, made
explicit in the implementation, not two disconnected features**: the
override is a request-scoped Pydantic field and two local variables,
never written to `config.json`, never assigned onto
`app.state.kite_config` (which would leak it into every SUBSEQUENT
request), never logged. The resulting `access_token` is still persisted
to the DB exactly as it already is for every other login path — that
part was already correct and is unchanged; it's SPECIFICALLY the
typed-in `api_key`/`api_secret` that must never outlive the one
request, and does not. **Verified**: `config.json`'s own on-disk bytes
are byte-for-byte identical before and after an override request, do
not contain either override value anywhere, and neither override value
appears in captured log output at any level for that request.

**Credential hardening** — see `RUN_GUIDE.md`'s own "Credential
hardening" section for the full writeup (what this does and honestly
does NOT solve, why a custom `config.json` encryption scheme was
deliberately not built). In short: `app/config.py`'s `load_config()`
now checks `KITE_API_KEY`/`KITE_API_SECRET`/`DATABASE_URL`/
`APP_AUTH_SECRET` environment variables FIRST for each of
`api_key`/`api_secret`/`database_url`/`app_auth_secret`, falling back to
`config.json` per-key for whichever aren't set that way — additive,
`config.json` alone still works completely unchanged for local dev.
**Verified**: with all four env vars set and `config.json` genuinely
absent from disk, the REAL app (full FastAPI lifespan — migrations,
dispatcher, deployment manager, not `load_config()` in isolation) boots
successfully and serves `/health`, with the env-sourced
`app_auth_secret` confirmably live (correct value authorizes, wrong
value still 401s). A partial mix (some keys from `config.json`, others
from env vars) merges correctly per-key, not all-or-nothing — also
verified. The "config not found" error message now names both ways to
supply whatever's still missing, narrowing to only the genuinely-absent
keys rather than always listing all four.

Verified via 3 new dedicated integration test scripts, run against the
real API/DB pipeline, on top of the full existing regression suite
(zero regressions): one spot-checking search results across NSE and NFO
specifically (confirming NIFTY 50's `instrument_token` matches the exact
`256265` already used everywhere else in this codebase — `tokens.json`,
`INDEX_SPOT_SYMBOL`, every `pivot_supertrend` test), one covering the
redirect-vs-manual-login parity plus the override's disk/log audit, and
one covering the env-var startup path end to end.

## What's here (Step 16: UI design pass, then a second pass — "Bazaar Ledger")

Two visual redesigns, neither a functional change — every id/class the
JS reads or writes, every endpoint, every data shape has stayed
untouched across both; only `static/index.html`'s CSS/markup,
`static/login.html`, and ~13 inline `style="color:var(--x)"` references
in `static/js/*.js` moved. The first pass replaced the original generic
dark-admin-template look (`#0f1117` ground, `#4f8cff` blue accent, a
system-font stack, one repeated bordered-box treatment for every
surface) with a quiet dark "ledger at night" system — warm graphite,
Libre Caslon Text headings, a verdigris accent, soft rule-top cards.
Shown to the user, who wanted the visual *language* of a different
reference design entirely (thick ink borders, flat offset "stamped"
shadows, blocky bordered nav buttons, alternating-tint card grids — a
workbook/ledger-form aesthetic, not a soft glass-panel one) while
explicitly asking for a genuinely different, surprising palette rather
than that reference's own forest-green-on-cream. **The current design
is the second pass** — same structural language the user pointed to,
a palette built for this instead of reused from either source.

**Color — "Bazaar Ledger."** 10 named tokens (`static/index.html`'s
`:root`): a warm rice-paper ground and deep-indigo border ink (not
black, not navy) standing in for a ledger page and its ruling, with a
marigold/saffron accent — the literal color of festoons and trading-
floor energy on muhurat-trading morning, when Indian exchanges open at
dawn for a symbolic first trade — deliberately not the blue, teal, or
graphite this app has already tried. Semantic colors pull from the same
everyday Indian color vocabulary rather than generic Tailwind
red/green: sindoor red for loss, jade for gain, turmeric/ochre for
paused. The accent is the only *cool*-leaning hue in a mostly warm
palette (marigold itself is warm, but sits apart from the red/green/
ochre trio by being the one clearly "clickable" color) — kept out of
the loss/gain/paused hue family on purpose, same reasoning as the first
pass, so "this is a button" and "this position needs attention" never
share a color.

**Type** — self-hosted (`static/fonts/*.woff2`, no CDN dependency,
consistent with this project's standalone-server philosophy; both
`latin` and `latin-ext` unicode-range subsets fetched per weight
specifically because `latin` alone does NOT cover ₹ U+20B9, which
`fmtMoney()` renders in every money value this app shows). Single
family this pass — IBM Plex Sans, weights 400 through 700, tight
letter-spacing on headlines — carrying both display and body roles
(the reference's own Inter-everywhere approach honored directly); IBM
Plex Mono unchanged for anything tabular. Libre Caslon Text (the first
pass's serif display face) is no longer used and its font files were
removed — this system has no serif anywhere, matching the reference.

**Layout** — the reference's own bordered-frame-plus-offset-shadow
language, applied to this app's existing structure rather than a
literal copy of its DOM: cards, the table wrap, and modals all carry a
3px `--line` border; modals get the heaviest offset shadow in the app
so "floating above everything" is unmistakable; stat-cards keep a
lighter 2px-no-shadow treatment so they read as flatter KPI boxes, not
identical to a full "frame." Sidebar nav items became individually
bordered, uppercase, bold buttons (solid accent fill when active)
instead of the first pass's plain underlined-link list. The Strategy
Catalog — previously one narrow column of cards — is now a responsive
grid with alternating tints across every third card, mirroring the
reference's own catalog treatment; nothing else needed a DOM change,
only `#catalogList`'s own CSS.

**Signature** — the expandable trade row keeps its role as the one
deliberately bold interaction, reframed again for this system: instead
of the first pass's soft dashed "torn ticket," it's now a stamped
audit slip — a heavy top+bottom rule frames the reveal, and each
`trigger_values`/`target_basis`/`resulting_state` block becomes its own
small bordered card nested inside, the same frame-within-a-frame
language the rest of the app now uses at a smaller scale. The expand
marker switched from a circle to a hollow/filled square (`□`/`■`),
matching the blockier overall vocabulary. Trigger badges stay smaller
and quieter than `.tag` chips by design, so they don't compete with the
row's own reveal.

Single light theme by deliberate choice (the reference itself has no
theme toggle either) — colors are still painted explicitly rather than
left to rely on browser/OS defaults, satisfying the "design both themes
unless deliberately committing to one visual world" rule from the same
place either way.

**Verified**: the full existing regression suite passes unchanged (zero
backend files touched, both passes — this is a `static/` diff only);
every `getElementById` target and every CSS class name the JS reads
(`.pos`/`.neg`, `.tag-{status}`, `.trig-{stop,profit,adjust,entry,other}`,
`.trigger-badge`, `.open`) re-cross-checked present in the rewritten
stylesheet; no orphaned references to any prior palette's token names
anywhere in `static/`; every JS file re-syntax-checked. Every view
(Dashboard, Strategy Catalog, Deployed Strategies, Detail's 4 tabs,
Instruments, the login page, the deploy/instrument/manual-login modals)
re-screenshotted against the same seeded deployment data used for the
first pass and checked by inspection.

## What's here (Step 17: live ticker bar — NIFTY/SENSEX/BANK NIFTY + IST clock)

A sticky bar at the top of every view (`static/index.html`, sits outside
the `.view` containers so it persists across navigation) showing three
index prices and a live IST clock, both genuinely live, not polled:

- **Prices** connect directly to the already-existing `/ws/ticks`
  WebSocket broadcast — the same one-upstream-Kite-connection-fans-out-
  to-many-clients stream any downstream tick consumer would use (see
  `app/main.py`'s `ws_ticks` handler and `TickBroadcaster`). No new
  backend endpoint, no polling loop: a tick lands in the DOM the instant
  Kite sends it, at zero extra load on the dispatcher beyond one more
  broadcaster subscriber. Each price shows an up/down arrow and % change
  against `ohlc.close` (the previous day's close, present in `full`/
  `quote` tick mode) when available — degrades to price-only if not
  (e.g. `tick_mode: "ltp"`, which carries no OHLC at all).
- **NIFTY BANK** (`instrument_token` 260105) was added to `tokens.json`
  alongside the two indices already subscribed there — needed so its
  ticks flow by default rather than only when some deployment or manual
  subscription happens to require it.
- **The clock** is pure client-side (`Intl.DateTimeFormat` pinned to
  `Asia/Kolkata`, updated every second) — India has one fixed UTC+5:30
  offset with no DST, so no timezone library or backend involvement is
  needed for it to always be correct.

**Verified end-to-end, not just visually**: a real Playwright browser
was driven against the actual FastAPI app (real ASGI stack, fake Kite
underneath) running as a real `uvicorn` server, with ticks injected
through the exact same `FakeKiteTicker` instance the live dispatcher
owns — proving the full `/ws/ticks` → browser path, not a mock of the
frontend. Confirmed: the bar shows a "connecting…" placeholder before
any tick; after injecting ticks for all three tokens (two priced above
their `ohlc.close`, one below), the DOM updated with the exact prices,
the up-arrow/down-arrow direction correct for each, and percentages
correct; the clock string matched `Asia/Kolkata` wall-clock time
including seconds and was re-checked ~2 seconds later to confirm it had
genuinely advanced, not just rendered once. Full existing regression
suite re-run afterward, unaffected (this is additive markup/CSS/JS plus
one more `tokens.json` entry, no existing endpoint or schema touched).

### Follow-up: ticker bar REST fallback for closed-market hours

Found immediately after shipping the above: Kite sends **no ticks at
all** outside market hours — a live, correctly-authenticated Kite
session simply has nothing to say when nothing's trading — so the
ticker bar's "connecting…" placeholder had no way to ever resolve
itself in that state. It wasn't broken; it was accurately reflecting
"zero ticks received," which just isn't useful when that's the normal
state for most of the day.

Fixed with a one-shot REST fallback, not a second polling system: new
`GET /instruments/quotes?tokens=...` (`app/routers/instruments.py`)
calls Kite's `quote()` REST endpoint via the same `get_kite_connect()` /
`asyncio.to_thread(...)` pattern `OptionsResolver` already uses
elsewhere for `ltp()`/`quote()` calls (`KiteConnect`'s REST client is
synchronous under the hood). If the frontend hasn't received a single
real tick within 8 seconds of the WebSocket connecting, it calls this
endpoint ONCE and renders the last-known price/change from it instead
of leaving the placeholder up indefinitely. If a real tick arrives
later (market opens while the tab is left open), it silently overwrites
the REST-sourced value — `renderTickerPrices()` doesn't care where a
price came from, only whether it's live.

The one thing that mattered most here: a REST-fetched snapshot must
never look identical to a genuinely live, tick-driven price — showing
a stale number as if it were current is exactly the kind of quiet
mislabeling this whole app's design is built to avoid elsewhere (the
trigger badges, the pos/neg coloring). Every fallback-sourced price
gets a small, explicit "closed" label next to it; it disappears the
instant a real tick supersedes it.

**Verified end-to-end**: a real Playwright browser against the actual
running app, with the Kite WebSocket never sending a single tick for
the whole test (an authentic "market closed" simulation, not a mock) —
confirmed the bar correctly shows "connecting…" immediately after
login, then correctly calls the new endpoint after the 8s window and
renders the exact fetched prices with correct up/down arrows/
percentages AND the "closed" label on all three. The backend endpoint
was also checked in isolation first (direct HTTP calls, correct 200
with correct values, and correct 400s for a non-integer or empty
`tokens` param) before testing it through the browser, to separate
"does the backend work" from "does the frontend's timing/fallback logic
work." Full regression suite re-run afterward, unaffected.

### Follow-up: structured Deploy config form, with an Advanced/raw-JSON toggle

The Deploy modal's config field was a bare JSON textarea from day one —
fine for a developer, not for actually operating this thing day to day.
Replaced with real boxes/dropdowns generated from each strategy's own
registered `default_config` (`static/js/catalog.js`), with an "Advanced"
checkbox that swaps in the original raw-JSON textarea as a genuine mode
switch, not a read-only mirror.

Deliberately NOT a hand-maintained schema per strategy (7 strategies,
each with its own config shape, would mean 7 schemas to keep in sync
with the actual strategy code forever). Instead, the form is generated
straight from whatever `default_config` the strategy registered itself
with (`app/strategies/registry.py`), so it can never drift out of sync
with what a strategy actually accepts — widget chosen from the value's
own shape: booleans and known enum strings (`pivot_type`,
`atr_smoothing`, `expiry_selector`, `convergence_mode` — verified
against each strategy's own docstring/validation code, not guessed) get
dropdowns; `instrument_tokens` gets a comma-separated box parsed back
into a real array; `"HH:MM"`-shaped strings get a real time picker;
everything else gets a plain text/number box. Config keys registered as
`null` (`capital_per_trade`, `prev_day_ohlc`, `seed_candles`,
`supertrend_seed` — genuinely advanced initialization knobs almost no
deploy needs) are deliberately left OUT of the simple form — shown
instead as a small note naming them, since a form field can't represent
"leave this at its true default" any more clearly than just not showing
a box for it — but they still round-trip untouched into the submitted
config, and Advanced mode can set them directly.

Switching to Advanced seeds the JSON textarea from whatever's currently
in the boxes (not the strategy's original defaults), so nothing typed
gets lost; switching back parses the JSON and re-renders the boxes from
it, staying on the JSON view (with an explanatory alert) if it doesn't
parse, rather than silently discarding an in-progress edit.

**Verified end-to-end** with a real browser against the real app: opened
the Deploy modal for `pivot_supertrend` and confirmed every field
widget's actual type (dropdown/time-picker/checkbox/text, matching the
value's shape) and every prefilled default; confirmed the four
null-valued advanced keys are correctly excluded from the boxes and
named in the note; edited `instrument_tokens`/`pivot_type`/
`force_exit_time` via the boxes, toggled to Advanced and confirmed the
JSON exactly reflected those edits (plus the untouched null-valued
keys); edited further directly in JSON mode, toggled back, and confirmed
the boxes re-rendered correctly — including a previously-null field
that gained its own real box once given a real value; then actually
submitted through the simple form and fetched the resulting deployment
back via the real API, confirming its persisted `config` matched every
box edit, every JSON-mode edit, AND every untouched default, exactly —
nothing dropped or mangled across the whole box↔JSON↔submit round trip.
Full regression suite re-run afterward, unaffected (this is a
`static/js/catalog.js` + one `static/index.html` markup change only, no
backend endpoint or schema touched).

## What's here (Step 19: admin enable/disable, and confirming time/threshold fields are configurable)

Two requests landed together; the second turned out to already be
covered by Step 18's own structured Deploy form, verified rather than
assumed.

### Admin Options tab: enable/disable strategies in the Catalog

New `strategy_settings` table (`app/db/migrations/0003_strategy_settings.sql`,
`strategy_name TEXT PRIMARY KEY, enabled BOOLEAN DEFAULT true`) layered
on top of `app.strategies.registry`'s existing in-memory registration —
registration itself stays pure Python/import-time as before; this is
only the "should this show up / be deployable" flag on top of that,
persisted so it survives a restart. Every currently-registered strategy
gets a settings row (`enabled=true`) on every boot
(`queries.ensure_strategy_settings`, called from `main.py`'s startup) —
existing rows, including anything an admin already disabled, are left
untouched (`ON CONFLICT DO NOTHING`, not an upsert).

`GET /strategies` now returns every registered strategy annotated with
`enabled`; new `PUT /strategies/{name}/enabled` sets it (404 for a name
nothing has ever registered — toggling a strategy that doesn't exist
isn't a meaningful action). Enforced where it actually matters, not just
hidden in the UI: `POST /deployments` now 400s for a registered-but-
disabled strategy, checked BEFORE `DeploymentManager.create_deployment`
runs. Deliberately does NOT block an *unregistered* strategy_name —
that's a different, still-intentionally-allowed case (see
`DeploymentManager.create_deployment`'s own docstring: a deployment can
exist before matching code does) — `is_strategy_enabled()` defaults to
`True` for any name with no settings row, so this distinction falls out
naturally rather than needing a separate check.

Strategy Catalog gained a `Browse`/`Admin Options` tab pair (reusing the
Detail page's own bordered-tab pattern). Browse now filters to
`enabled !== false` — a disabled strategy simply isn't offered for a
*new* deployment, shown-but-greyed-out was considered and rejected as
adding a UI state (partially-clickable card) that doesn't map to
anything the backend actually allows. Admin Options lists every
registered strategy with a live count of its active deployments and an
Enable/Disable button. Disabling only affects *new* deployments —
anything already running under that strategy is completely unaffected,
called out explicitly in the tab's own footer note so it's never
ambiguous with pausing/stopping a specific deployment (a separate,
per-deployment action elsewhere in the app).

**Verified end-to-end**: direct API checks first — default `enabled:
true` for every strategy, `PUT .../enabled` persists and is reflected
back by `GET /strategies`, a disabled strategy's `POST /deployments`
correctly 400s with a clear message, an *unregistered* strategy_name
remains allowed (confirming the enabled check didn't regress that
existing behavior), re-enabling lifts the block, and a nonexistent
strategy_name 404s. Then the real UI end to end: disabled a strategy via
the Admin Options button, confirmed it disappeared from Browse,
re-enabled it via the UI, confirmed it reappeared. Full regression suite
(including the full-restart integration test, since `main.py`'s startup
sequence changed) re-run afterward, unaffected.

### Time/threshold config fields — already covered, verified rather than rebuilt

Checked whether `entry_time`/`force_exit_time`/`eod_check_time` and the
"EOD 80%" threshold (`strangle_monthly_v2`'s `eod_gap_floor`, default
`0.80` — matching the actual code, not a guess: `eod_check_time` is the
time that check runs, `eod_gap_floor` is the 80% ratio it checks
against) are genuinely read from each strategy's own `cfg.get(...)` (not
hardcoded) across every strategy that has them — pivot_supertrend family
(`force_exit_time`), the DTT family (`entry_time`/`force_exit_time`,
`intraday_dtt_advanced` inheriting the same handling from
`IntradayDTTAdjustedStrategy`), and `strangle_monthly_v2`
(`entry_time`/`eod_check_time`/`eod_gap_floor`). All confirmed genuinely
config-driven, all already present in each strategy's registered
`default_config` — which means Step 18's structured Deploy form (built
generically from `default_config`, not a hand-maintained per-strategy
schema) already exposes every one of them as a real editable field: a
time picker for the `"HH:MM"`-shaped ones, a plain number box for
`eod_gap_floor`. Nothing needed building — **verified** with a real
browser instead: opened the Deploy form for `intraday_dtt_simple` and
confirmed `entry_time`/`force_exit_time` are genuine `<input
type="time">` fields with the correct 10:00/15:00 defaults, editable;
opened it for `strangle_monthly_v2` and confirmed `eod_check_time`
(time picker, default 15:13), `eod_gap_floor` (number box, default
0.80), and `entry_time` (10:00) are all genuinely present and editable
together, matching the code's own defaults exactly.

## What's here (Step 20: fixed a shutdown hang on Ctrl+C)

Reported directly: `uvicorn` printing `Waiting for background tasks to
complete. (CTRL+C to force quit)` and sitting there forever, needing a
second Ctrl+C, which then dumped a pile of `asyncio.exceptions.
CancelledError` traceback noise instead of exiting cleanly.

**Root cause, found by actually tracing it, not guessed**: `/ws/ticks`
(`app/main.py`) was a send-only loop —
`while True: ticks = await queue.get(); await websocket.send_json(...)`
— with no way to notice the client disconnected (a send-only loop only
finds out on its *next* send, which never happens if no ticks are
flowing) and, critically, no way to notice the *server* wants to shut
down either. The Step 17 ticker bar keeps exactly this kind of
connection open essentially all the time now, and outside market hours
zero ticks ever flow (see Step 17's own follow-up) — so the handler
sits blocked in `queue.get()` indefinitely, Uvicorn's graceful shutdown
waits forever for that connection to finish on its own, and a forced
second Ctrl+C cancels it mid-`await`, which isn't a `WebSocketDisconnect`
so it surfaced as an unhandled `CancelledError` instead of a clean exit.
This bug existed before the ticker bar; nothing exercised a long-lived
idle `/ws/ticks` connection often enough to hit it until then.

**Fixed at the root**, not patched around: a new `app.state.
shutdown_event` (`asyncio.Event`) is set as the very first line of the
`shutdown()` handler, before any other teardown. `/ws/ticks` now races
three things concurrently — forwarding ticks, an explicit
`websocket.receive()` loop (the actual way to detect a client-initiated
close, which the old code never did either), and that shutdown event —
and cleanly closes and unsubscribes the instant any one of them fires.
This fixes two real bugs with one change: the reported shutdown hang,
and a previously-unnoticed companion bug where a client closing their
browser tab while idle left a zombie broadcaster subscription forever
(nothing ever detected the disconnect to clean it up).

**Verified concretely, not just "looks right"**: a real Uvicorn server
with a genuine idle WebSocket client connected to `/ws/ticks` (via the
`websockets` library, not a mock) and zero ticks ever sent — the exact
reported scenario. Confirmed server shutdown now completes in ~0.3
seconds with that connection still open (previously this reliably hung
past a 5-10s bound in every earlier test in this session that happened
to leave a `/ws/ticks` connection open during teardown — re-read after
the fact as the same bug surfacing quietly, not unrelated test flakiness
as it was assumed to be at the time), and that the connection was
genuinely closed *by the server*, not just abandoned. Separately
confirmed a client-side disconnect while idle now correctly drops the
broadcaster's subscriber count. Full regression suite re-run afterward
(unrelated pre-existing flakiness in `test_db_layer.py` unaffected, as
established earlier in this project).

## What's here (Step 21: Admin Options polish + a "Clear All" danger zone)

Three requests landed together, in the same message.

### Admin Options table → cards

The Admin Options tab (Step 19) originally listed strategies in a dense
multi-column table. Reported back as "very bad" with a screenshot showing
severe word-by-word wrapping. A first attempt (`table-layout: fixed` plus
explicit `<colgroup>` widths) was tried and rejected as insufficient — the
real problem wasn't column widths, it was a fundamental mismatch between a
table and strategies' long free-text descriptions (400+ characters for
some). Fixed properly by dropping the table entirely and reusing the same
`.card` layout Browse already uses (name, tag, description, action button,
one card per strategy) rather than continuing to tune a data shape that
doesn't fit the content.

### Time picker color

Native `<input type="time">` elements were rendering Chromium's default
blue, clashing with the Bazaar Ledger palette. Fixed with a single real
CSS property — `accent-color: var(--accent)` on `:root` — which themes
native form-control chrome (checkboxes, radios, and a time input's clock
icon/segment highlight) app-wide. Verified via
`getComputedStyle(el).accentColor`.

### Clear All Deployments

A destructive "start fresh" action for the Admin Options danger zone.
Scope and confirmation mechanism were both explicitly decided up front
(asked, not assumed, given the action is irreversible): scope is
**deployments only** — not a full factory reset — and confirmation is
**app password + a typed confirmation phrase**, both required.

`POST /deployments/clear-all` (`app/routers/deployments.py`, deliberately
last in the file under a "DANGER ZONE" banner) takes `{password, confirm}`,
checks the password with `secrets.compare_digest` against the same
`app_auth_secret` used for login, requires `confirm == "DELETE ALL"`
exactly, then calls `DeploymentManager.shutdown_all()` (stops every
in-memory runner task first, so nothing holds a reference to a row that's
about to disappear) and a single `DELETE FROM deployments` — cascading at
the DB level via the `ON DELETE CASCADE` foreign keys already on
`positions`, `position_lots`, `deployment_events`, and
`deployment_snapshots` (`0001_init.sql`), so one delete wipes every
deployment and everything under it in one transaction. Deliberately
narrow: the Kite login session, subscribed instruments, and Admin Options
enable/disable state are untouched — this clears deployments, not the
whole app.

New `Catalog.openClearAllModal()`/`submitClearAll()` (`static/js/
catalog.js`) and a `#clearAllModal` (`static/index.html`) — password
field, a "type DELETE ALL to confirm" field (client-side check as a fast
fail, not the real security boundary — both gates are re-checked
server-side regardless), and a modal message area reporting the exact
outcome (`✓ Cleared N deployment(s)` or the server's own error text).

**Verified end-to-end** with a real server, real Postgres, and a real
browser: seeded two deployments with a genuine open position, trade,
event, and equity snapshot; disabled a strategy and manually subscribed
an instrument (to check the scope boundary); completed a real Kite login
so `kite_sessions` had a genuine row to check. Wrong password alone → 401,
wrong confirm phrase alone → 400, either leaves everything untouched.
Correct password + phrase → 200 with the right `deleted` count; confirmed
`GET /deployments` now returns empty; confirmed directly against Postgres
(not just the API) that `deployments`/`positions`/`position_lots`/
`deployment_events`/`deployment_snapshots` are all genuinely empty;
confirmed `DeploymentManager.runners` has no dangling in-memory tasks left
over. Confirmed the scope boundary itself: the disabled strategy is still
disabled, the manually-subscribed instrument is still subscribed, and the
`kite_sessions` row is still present — none of them touched by the wipe.
Then the same flow through the real modal in a real browser: wrong
password shows an error and leaves the modal open, a wrong-case confirm
phrase is caught client-side before it even reaches the API, and the
correct combination clears the deployment and refreshes both the Catalog
and Deployed Strategies views to reflect it.

## What's here (Step 22: an aggregate-read cache — pages that took 3-6s now load instantly)

Reported directly: `GET /deployments` and friends taking 3-6 seconds to
load on every single page view. Confirmed earlier (see the query-
parallelization investigation) that this isn't query complexity — a
deployment list and a positions list run very different numbers of
queries but took the same ~3s, pointing at a flat per-round-trip cost
against Neon that swamps every request about equally.

**The fix: cache the hot reads, refresh in the background.** New
`app/cache.py` (`AggregateCache`) holds four keys — `deployments`,
`positions_open`, `trades_recent`, `strategies` — the exact endpoints
behind Dashboard, Deployed Strategies, and the Catalog's active-
deployment counts. Each is populated once at startup (before the app
accepts any traffic — no cold-cache penalty on the very first real
request) and then refreshed on its own background loop for the life of
the process (6s for deployments/positions, 12s for trades, 20s for
strategies — infrequent enough not to hammer Neon, frequent enough that
unrealized P&L, which drifts continuously with live prices and isn't
tied to any single "event," never drifts far). `GET /deployments`,
`/positions`, `/trades/recent`, `/strategies` now just read memory —
each endpoint still accepts its full original query-param range, but
falls through to a live query for any parameter combination other than
the one the frontend actually sends (a status filter, a non-default
limit), so nothing about the API's contract narrows.

A cache with only a timer would still leave up to one full interval of
staleness right after something actually changes, which would read as
"my click didn't do anything" — so every mutating endpoint
(create/pause/resume/stop/clear-all a deployment, enable/disable a
strategy) calls `cache.refresh_now(key)` right after its own write,
before returning to the caller. That covers every HTTP-driven change,
but not the one that matters most: a strategy's own trade, which never
goes through any HTTP endpoint at all — `DeploymentRunner.buy()`/
`sell()` write straight to Postgres. Wired a fire-and-forget hook
through instead: `DeploymentManager` now optionally holds a `cache`
reference and passes a callback to every runner it starts; after each
fill, the runner calls it, and the manager schedules a background
refresh (`asyncio.create_task`, never awaited inline) so a slow Neon
round trip can never add latency to the strategy's own tick-processing
loop. Found this gap the direct way: an existing regression test
asserting the list and single-deployment views agree on `realized_pnl`
right after a runner-level fill (bypassing the API entirely, same as a
real strategy) started failing once the list became cached — a genuine
consistency gap, not a stale test, fixed by wiring the hook rather than
loosening the assertion.

**A second, unrelated bug found the same way**: chasing that same test
failure down turned up an actual pre-existing bug, unmasked rather than
caused by the cache. `DeploymentManager.create_deployment()` writes the
deployment row to Postgres, *then* starts its runner — if the strategy's
own `on_start()` rejects the config (pivot_supertrend requiring exactly
one `instrument_token`, say), that exception was propagating up to a
clean 400 response, but the row was already committed and never rolled
back: a caller told "this failed" had an orphaned `active`-status
deployment sitting in the DB with no live runner, discoverable only by
retrying the same `deployment_name` and getting a confusing 409 for a
deployment that supposedly never got created. This was always a bug —
it just used to be invisible, because the old uncached `GET
/deployments` would still find the orphaned row on its next live query,
making a rejected deploy look like it "eventually" succeeded. The cache
removed that accidental cover. Fixed with a real rollback: `
create_deployment()` now deletes the row if starting its runner fails,
and re-raises the original exception so the caller still gets the
strategy's own error message.

**Frontend**: since reads are now memory-speed, Dashboard/Catalog/
Deployed Strategies auto-poll every 6s while active (paused instantly on
`document.visibilitychange` to hidden, and stopped entirely — not just
paused — the moment you navigate to Detail or Instruments, so nothing
re-pays the slow round trip this cache exists to avoid on a page that
isn't cache-backed). A small "Updated Xs ago" label sits next to each
view's Refresh button — a client-side timestamp of the view's own last
successful load, ticked every second, not a real server-reported cache
age (the honest thing that's cheap to provide without adding a header
to every response).

**Verified concretely**: first `GET /deployments` immediately after boot
in the single millisecond range (was 3-6s), five repeated calls all
sub-5ms. Every mutation (create/pause/resume/stop/clear-all, strategy
enable/disable) reflected on the very next read with zero wait. A write
made *directly against Postgres*, bypassing every endpoint, confirmed
correctly invisible until the background loop's interval elapsed, then
correctly visible afterward — proving the loop itself works
independently of any mutation hook, not just that the hooks work. The
fill-triggered refresh verified the same way at the runner level (not
through any HTTP call). The rollback fix verified with a genuinely
rejected deploy: 400 returned, no row in `GET /deployments`, no row
directly in Postgres either, and a retry with the same
`deployment_name` succeeding cleanly. Real-browser check of the
freshness label ticking and auto-refresh starting/stopping correctly
per view. Shutdown stays clean (~0.2-0.3s) — `cache.stop()` cancels its
background loops before the DB pool closes, same shutdown-hang class
already fixed once for `/ws/ticks` in Step 20. Full regression suite
re-run afterward (one unrelated failure traced to test-DB pollution
from an earlier scratch test run sharing the same local Postgres — not
an app regression — cleared and re-confirmed).

## What's here (Step 23: `/health` was the one endpoint still paying a live Neon round trip)

Reported: every API blazing fast (<30ms, post-Step-22 caching) except
`GET /health`, still 700-800ms. Root cause found immediately —
`/health` ran a live `SELECT 1` against Neon on every single call,
unlike every other hot read cached in Step 22. Made worse by the
frontend polling it every 5s (`pollHealth()`), so it was the one
remaining endpoint paying that flat per-round-trip cost repeatedly.

Fixed the same way as Step 22: `check_db_health()` pulled out into its
own function, registered as a fifth `AggregateCache` key (`db_health`,
15s interval — a DB outage still surfaces within one interval, the
right trade for a status indicator that gates nothing). `GET /health`
now reads `cache.get("db_health")` instead of querying live.

**Verified**: 5 repeated `GET /health` calls all in the low single-
digit milliseconds (was 700-800ms), `database_connected` still
correctly reflects real DB state.

## What's here (Step 24: a 7th strategy — `calendar_btst`)

A new strategy, `app/strategies/calendar_btst.py`: an ATM calendar
spread held overnight (Buy-Today-Sell-Tomorrow). Sells an ATM CE+PE
straddle in THIS_WEEK's expiry and buys an ATM CE+PE straddle at the
SAME strike in NEXT_WEEK's expiry — 4 legs, one entry, resolved from a
single spot-price read so the strike can never drift between the two
expiries' leg lookups. Entered near end-of-day (`entry_time`, default
15:20) and unwound in the first few minutes of the next trading day
(`exit_time`, default 09:20) — both configurable, per the request.
"Next day" is tick-driven, not calendar math (same pattern every other
day-boundary check in this package uses): it's just the first tick
whose date differs from the entry day, so a weekend or holiday is
skipped automatically without any holiday-calendar logic. Has
its own expiry-day entry guard, config `switch_to_next_week_on_expiry`
(default False) — see Step 52 for the full rationale, since this one
got a materially different fix than the DTT family's own version of
the same flag: Step 46 left this strategy's guard as a plain skip on
purpose (a genuinely different trade shape, so no reason to assume the
DTT fix applied unchanged); Step 52 revisited that and gave it the
"never skip" treatment too, but shifting BOTH legs one week — not just
the short one — since this is a calendar SPREAD, and shifting only one
leg would collapse its defining one-week gap between SHORT and LONG.

Resume-safety reconstructs open legs from the DB by SIDE (short →
buy back on exit, long → sell off on exit), not by which expiry each
leg was originally resolved from — exit only ever needs to know which
direction to close a leg, never its expiry.

**A real bug found and fixed via the test itself, not assumed away**:
the first version correctly resumed a mid-cycle position, but a second
BTST cycle later the same day silently failed to enter. Root cause —
resume set `entered_today = True` (correct, an entry genuinely already
happened) but left `self.today` at `None`, so the next tick's day-
boundary check took the "very first tick ever" branch instead of a
genuine day-change, and the stale `entered_today` flag never got reset
for the new day. Fixed by seeding `self.today = entry_day` on resume so
the following day-boundary crossing is detected correctly.

**Verified** with a real synthetic 2-expiry options chain (THIS_WEEK +
NEXT_WEEK instrument rows) through the real dispatcher → broadcaster →
DeploymentRunner → strategy → OptionsResolver → Postgres pipeline:
entry fires exactly at `entry_time` with all 4 legs at the same strike,
shorts genuinely resolve to THIS_WEEK contracts and longs to NEXT_WEEK,
no action on a later same-day tick, still holding the next morning
before `exit_time`, a real pause+resume mid-cycle (forcing a genuine
on_stop/on_start DB reload, not simulated) correctly reconstructs all 4
legs, exit at `exit_time` closes everything with the right buy/sell
direction per leg, and a second cycle enters normally that same evening
— 8 entry fills + 4 exit fills total, exactly as expected.

## What's here (Step 25: edit a deployment's name and notes)

`PATCH /deployments/{id}` — new `notes TEXT` column (migration `0004_
deployment_notes.sql`) plus the existing `deployment_name`. Deliberately
narrow, and deliberately NOT config/mode/initial_capital: those are
either fixed identity or structural/financial fields a running strategy
and every P&L calculation already assume are stable for the
deployment's lifetime (e.g. several strategies size against
`initial_capital` as a FIXED reference value) — changing them
post-creation would silently corrupt state rather than do anything
useful. Stop and redeploy fresh for an actual strategy/capital/config
change; rename and notes are the two things safe to edit in place,
regardless of status. A rename checks for a collision against every
OTHER deployment (renaming to its own current name is a no-op success,
not a false 409) and refreshes the aggregate-read cache immediately so
the next `GET /deployments` already reflects it.

New "Edit" button in the Detail page header, next to Pause/Resume/Stop
— opens a small modal pre-filled with the current name/notes; saved
notes render right in the header (📝, preserving line breaks) so
"why did I deploy this" doesn't require opening a separate tab of
scratch notes. Deliberately only on the Detail page, not inline on the
Deployed Strategies list — renaming/annotating is a "look at this one
thing" action, not a lifecycle action like Pause/Resume/Stop that
benefits from being reachable without drilling in.

**Verified**: rename-only and notes-only PATCHes each leave the other
field untouched, a blank name is rejected, renaming to an
already-taken name 409s, renaming to its own current name succeeds,
PATCH on a nonexistent deployment 404s, and the cached list reflects a
rename immediately. Real-browser check of the actual Edit modal:
opens pre-filled with the live values, submits, and the header shows
the new name/notes after reload — caught and fixed a Playwright
selector quirk in the test itself along the way (`:not(.open)` +
`state="hidden"` don't combine reliably, the same class of issue hit
once before in this project — fixed by waiting on the plain selector's
hidden state instead), not an app bug.

## What's here (Step 26: clearer config param names)

Reported: some config params were bare `_pct` names that don't say
WHICH basis they measure — the exact kind of param a config-editing user
can misread by assuming it matches a sibling param's basis. Renamed the
ambiguous ones, scoped to the pattern actually reported (a param whose
name gives no clue whether it's per-leg or combined, premium or
capital) — left alone the params that already name their own concept
clearly in context (`monthly_target_pct` already says "monthly",
`strike_selection_capital_pct` already says "capital",
`convergence_stop_pct`/`adjustment_*` are already scoped by their own
documented section). Flagging this scoping as a judgment call, not
something confirmed against every param in every strategy file.

Renamed:
- `decay_pct` → `combined_premium_profit_pct` (intraday_dtt_simple,
  intraday_dtt_adjusted, intraday_dtt_advanced) — the profit target is a
  fraction of the COMBINED (CE+PE) entry premium, not a per-leg or
  capital-based figure.
- `spike_pct` → `per_leg_stop_loss_pct` (intraday_dtt_simple) — the stop
  loss is a fraction of EACH leg's OWN entry premium, checked
  independently per leg — the exact "wl is per leg, profit is of
  concluded premium" distinction that prompted this.
- `checkpoint_pct` → `checkpoint_profit_pct_of_capital`
  (strangle_monthly_v2) — a fraction of `initial_capital`, a genuinely
  DIFFERENT basis than the DTT family's premium-based profit params;
  named explicitly so a config-editing user comparing the two never
  assumes the same basis.

**Every rename keeps reading the OLD key name as a fallback**
(`cfg.get(new_name, cfg.get(old_name, default))`) — a deployment created
before this rename has its config already persisted in Postgres under
the old key, and this makes it keep behaving identically after an
upgrade with zero manual migration. A fresh deploy only ever sees the
new name (the only one in `default_config`, so it's the only one the
structured Deploy form ever renders).

**Verified two ways**: re-ran the full existing regression suite for
all 4 affected strategies UNCHANGED — those tests deploy using the OLD
key names, so passing them directly proves the backward-compatible
fallback works, not just that it compiles. Then a dedicated test
deploying each strategy with ONLY the NEW key name(s) set to a
distinctive non-default value, confirming it lands correctly on the
live strategy instance for all 4 strategies.

## What's here (Step 27: a richer Deployment Detail page)

Two additions, one of them a real gap fix.

**New "Activity" tab** — `GET /deployments/{id}/events` has existed
since the very first version of this API and has NEVER been called by
the frontend. It's the audit trail behind every pause/resume and every
fill, plus — this is the part that actually matters — `strategy_error`:
a strategy's own `on_tick` raising an exception (a bad resolver call, a
transient `NoKiteSession`, anything) is caught at the runner level and
recorded here instead of crashing the deployment. Which means a
strategy that's silently failing — still showing "active," never
actually trading — was previously invisible ANYWHERE in the UI; the
only way to find out was reading server logs directly. This tab is that
visibility: a warning banner counts recorded errors up front, each
event is tagged by type (color-coded — errors in loss-red, fills in
info-blue, pause/resume matching their existing status colors), and a
row with metadata expands on click, reusing the same JSON-block
renderer the Trades tab's trigger metadata already uses.

**Enhanced Stats tab**: total return (realized + unrealized against the
FIXED `initial_capital` reference, not the compounding cash balance —
same basis several strategies themselves size against), profit factor
and largest win/largest loss (from each closed position's own
`realized_pnl` — position-level, not per-lot, so a multi-lot close
isn't double-counted), max drawdown (largest peak-to-trough decline
across the equity-snapshot series already being fetched for the equity
chart, no extra request), and a deployed-since / last-activity line for
quick operational context ("is this thing actually doing anything").

**Verified** with real runner fills (one profitable closed position,
one losing one, one still open), a real strategy_error event, and
seeded equity snapshots with a known drawdown, through a real browser:
the Activity tab correctly shows paused/resumed/fill/strategy_error
events with correct counts, the error row expands to show its message,
and every new Stats metric renders the exact expected value (profit
factor 2.50 from a ₹5,000 win against a ₹2,000 loss, max drawdown
exactly ₹10,000 / 9.52% from the seeded snapshot sequence).

## What's here (Step 28: real user accounts, replacing the one shared password)

Pre-launch requirement: `app_auth_secret` doubling as the actual,
ongoing login credential doesn't scale past "one person, forever" — no
way to change it without editing config.json and restarting, no way to
tell who did what, no way to add a second person their own login. Three
additions, all schema in `app/db/migrations/0005_users_and_audit.sql`:

**Real user accounts** (`users` table). `app_auth_secret` is now only a
**first-boot bootstrap seed** — on the very first startup with an empty
`users` table, it becomes the initial `admin` user's starting password
(see `app/main.py`'s startup); every boot after that is a no-op there,
same idempotent-on-every-restart shape as the migration runner itself.
From then on, real login is username + password (bcrypt-hashed, never
stored or logged in plaintext — see the audit log point below). New
endpoints: `POST /auth/change-password`, `POST /auth/users` (create),
`GET /auth/users` (list — never returns `password_hash`), `GET
/auth/me`. `POST /auth/login` now takes `{username, password}`; a
wrong password and a nonexistent username return the identical 401
message, so a login attempt can't be used to enumerate valid usernames.
The X-API-Key path (`app_auth_secret` in an `X-API-Key` header, for
scripted/curl access) is completely unchanged — it authenticates the
*script*, not a person, and has no associated user in audit rows
(`user_id`/`username` both null).

**No RBAC — on purpose, but built to be added later without a
rearchitecture.** Every logged-in user can see every deployment and
manage every other user today; there's no per-user data scoping and no
permission check anywhere. What exists instead is `app/rbac.py`'s
`can(user, action) -> bool`, a single always-`True` choke point, plus a
real (if currently unused) `role` column on `users` (defaults
`'member'`). Adding real RBAC later means writing the actual rule in
`can()` and adding `if not rbac.can(user, "...")` checks at the call
sites already commented with exactly what they'd check — not touching
the schema or the session model again.

**Audit logging** (`audit_log` table, `app/auth.py`'s
`AuditLogMiddleware`). Every POST/PUT/PATCH/DELETE request the app
handles gets one row — implemented as ASGI middleware, not a per-router
dependency, for the identical fail-closed reasoning `AuthMiddleware`
itself already uses: a new router added later is audited automatically,
with nobody needing to remember to wire it in. Placement in the
middleware stack is deliberate: it sits between `AuthMiddleware`
(innermost) and `HostAwareSessionMiddleware` (outermost) so it can (a)
read the already-decoded session to attribute a request to a real user,
and (b) still observe the real final status code even for a request
`AuthMiddleware` itself rejects with a 401 — a rejected mutating
attempt is exactly the kind of thing an audit trail exists to catch,
not something to silently skip. Request bodies are captured via the
standard buffer-then-replay ASGI pattern (the real route handler still
sees a completely normal, once-only-readable body) and redacted before
being written — `password`, `new_password`, `old_password`,
`app_auth_secret`, `access_token`, `api_secret`, `request_token`,
`api_key`, at any nesting depth, all become `"[redacted]"`. GET requests
are never logged (read-only, nothing state-changing to audit). A new
Account → Audit Log tab in the UI reads it back via `GET
/auth/audit-log`.

**New UI**: an "Account" nav item — Profile (who am I, change my own
password), Users (create a new account, see everyone), Audit Log
(everything above, browsable). `login.html` gained a username field.

**Verified** end-to-end against a real server + real Postgres (no
mocks): the bootstrap admin logs in with `app_auth_secret` as its seed
password; wrong-password and unknown-username both 401 with the
identical message; change-password rejects the wrong current password
and accepts the right one, after which the old password stops working
and the new one works; a second user can be created, rejects a
duplicate username (409) and a too-short password (422), and logs in
independently; that second, non-admin user can read `/deployments` and
list users too, confirming "no RBAC yet, shared visibility" is real,
not just documented; `GET /auth/users` never leaks `password_hash`;
logout actually invalidates the session. On the audit log specifically:
no plaintext password ever appears in any stored row (checked by
searching every row for the literal password strings used in the test,
not just checking the field name); a create-user attempt shows up with
the right actor, path, and status for both its 200 success and its 409
duplicate-rejection retry; a rejected 401 login attempt is captured;
a mutating request `AuthMiddleware` itself blocks (401, before reaching
any route handler) is *still* captured; GET requests are confirmed
absent from the log; and a successful login's own row shows the
username that request just authenticated as (session mutations made
during the request are visible to the middleware reading the session
after the handler returns). The UI was verified through a real browser
too: username-field login, the full change-password round trip, create-user
end-to-end with the new user appearing in the table, the Audit Log tab
rendering with no plaintext password anywhere in the rendered page,
and logout returning to the login form.

**Flagged, not resolved — a product decision, not a bug**: user
accounts have no deactivate/delete UI yet (`is_active` exists on the
schema and is checked at login, but nothing sets it false) — creating
one is one-way for now. Reasonable for "a handful of trusted people I
personally invite," worth revisiting if that assumption changes.

## What's here (Step 29: mobile sidebar/status-bar overlap, found during real deployment)

Found on a real phone during the actual first deployment of this app,
not in a desktop browser's device emulator: on a narrow viewport, the
sidebar's "footer" (Kite-connection badge, running/ws-client count,
Re-login/Enter-manually/Logout buttons) didn't wrap onto its own row
below the nav items — it used `margin-left: auto` to hug the right edge
of whatever row still had space, which meant it could end up sharing a
row with a wrapped nav item (e.g. "Account") instead of cleanly
dropping below the whole nav block, producing a visually overlapping
mess.

Fix: `width: 100%` on the mobile `.sidebar-footer` — inside a wrapping
flex row, a 100%-wide item forces a line break before itself, which is
what actually guarantees "always starts its own fresh row," not the
`margin-left: auto` trick that only worked by coincidence on wider
screens. Also gave the footer its own `flex-wrap` so its contents
(badge, count, three buttons) wrap internally on very narrow phones
instead of overflowing, and switched its divider from a left border
(made sense stacked inline; didn't once it's its own row) to a top one.

Side effect, not a separate fix: the live-price ticker (previously
buried below the broken, taller-than-it-needed-to-be nav block — "the
live prices should be on top, why is it at the bottom" was the actual
complaint) is now visible without scrolling on a normal phone screen,
simply because the nav block above it shrank back down to its correct
height. Desktop is completely unaffected — this media query only
applies below 760px.

**Verified** via Playwright at a real 390×844 mobile viewport (an
iPhone-sized screen), reproducing the exact broken layout first,
confirming the fix visually resolves it, and confirming a 1440×900
desktop screenshot is pixel-identical before and after.

## What's here (Step 30: mobile nav is now a real collapsible menu)

Step 29 fixed the immediate overlap bug but kept the underlying
approach — try to fit a whole sidebar's worth of content (5 nav items +
a status badge + 3 buttons) into a wrapping row. That was always going
to keep needing touch-ups as content grows. This replaces the approach
itself: on mobile, the sidebar is now a slim top bar (brand + a
hamburger toggle) with the nav list and account/status block hidden
behind it as an actual dropdown menu, not a permanently-visible wrapped
block.

Mechanically: the nav items + `.sidebar-footer` are now wrapped in a
`.sidebar-nav-group` div. On desktop this div is `display: contents` —
functionally invisible to the flex layout, so its children behave
exactly as direct children of `.sidebar`, identical to before this
existed (confirmed via a pixel-identical desktop screenshot). Only the
mobile media query gives it real behavior: hidden by default, toggled
to a plain vertical block by a new `.mobile-menu-toggle` (☰) button —
itself `display: none` outside mobile, so desktop never even renders
it. Picking a destination closes the menu automatically (hooked into
the existing `router()`, not each nav item individually — a new nav
item added later gets this for free).

**Verified** via Playwright at a real 390×844 mobile viewport: the
closed state shows only the slim top bar with live prices immediately
visible underneath (no more scrolling past a nav block at all, closed
or open); the open state shows the full menu as a clean vertical list
with the active view highlighted; clicking a nav item both navigates
AND closes the menu back down (checked programmatically, not just
visually); and a 1440×900 desktop screenshot is pixel-identical to
before this change.

## What's here (Step 31: mobile menu is now a slide-in drawer, not a dropdown)

Requested directly: the dropdown from Step 30 pushed page content down
when opened — asked for a proper side drawer instead, the more
familiar mobile pattern.

`.sidebar-nav-group` is now `position: fixed`, off-screen by default
(`transform: translateX(-100%)`) and slid fully on-screen by `.open`
(`translateX(0)`) — an actual slide animation, not an instant
show/hide, and it now floats OVER the page (capped at `min(82vw,
320px)` wide) instead of pushing it down. A new dimmed backdrop
(`#mobileNavBackdrop`, `rgba(27,17,48,0.5)`, fading in/out alongside
the drawer) covers the rest of the screen while it's open and doubles
as the tap-outside-to-close target. Three more things now move in
lockstep with the drawer, all through one shared `_setMobileNavOpen()`
helper rather than being toggled separately in different places (the
exact kind of thing that drifts out of sync if left as 3-4 independent
toggles): the hamburger icon swaps to ✕ while open (a persistent,
always-reachable close affordance, not just tap-outside), and the page
behind it is scroll-locked (`body.mobile-nav-open { overflow: hidden;
}`) so you can't accidentally scroll the dimmed dashboard underneath
the open menu.

**Investigated during verification, turned out not to be a bug**:
initial screenshots seemed to show the hamburger/✕ button staying
visible ABOVE the dimmed backdrop rather than being dimmed with
everything else. Checked properly before "fixing" it — sampled the
actual pixel color at that exact spot before/after opening the drawer,
which showed it going from near-white to near-black, confirming the
backdrop WAS correctly darkening that area the whole time. The ✕ glyph
just stayed legible because it's rendered in dark ink to begin with, so
"dark symbol on a now-dark background" still reads fine — not a
stacking bug, a real (and arguably nice) property of a translucent
scrim. An unnecessary z-index "fix" was written, then reverted once
the pixel check disproved the theory behind it, rather than shipped
speculatively.

**Verified** via Playwright at a real 390×844 mobile viewport: the
drawer slides fully on-screen with the backdrop fading in; the icon
switches to ✕ and `<body>` gets scroll-locked while open (checked
programmatically); tapping the backdrop closes the drawer; picking a
nav item both navigates and closes it; and a 1440×900 desktop
screenshot is pixel-identical to before this change (`.sidebar-brand`
and `.sidebar` are untouched — only `.sidebar-nav-group`'s positioning
mechanism changed, and only inside the sub-760px media query).

## What's here (Step 32: sessions now actually expire and can be revoked)

Reported directly: logging in one day, the session was still active
the next, even across a full container rebuild. Two separate root
causes, both real:

1. **Session lifetime was never explicitly set** — Starlette's
   `SessionMiddleware` defaults to **14 days** when `max_age` isn't
   passed, which it never was. Now explicit: 24 hours
   (`HostAwareSessionMiddleware.SESSION_MAX_AGE_SECONDS`), chosen to
   mirror the daily rhythm this app already has (Kite's own
   `access_token` also expires once a day), not a new, unfamiliar one.
   Absolute from login time, not sliding on activity — Starlette sets
   `Max-Age` once at issue time, and "re-login once a day" is already
   the expected routine here.

2. **Sessions had no server-side revocation at all** — the actual
   reason surviving a rebuild "worked": the cookie is a self-contained
   signed token, never checked against anything live server-side. As
   long as the signing key (`app_auth_secret`) is unchanged, ANY
   previously-issued, unexpired cookie keeps validating forever,
   rebuild or not — and critically, before this, changing your
   password didn't invalidate any other already-issued session either.
   Fixed with a `session_version` column on `users` (migration 0006),
   embedded in the cookie's own payload at login. Every authenticated
   request now checks the cookie's embedded version against the user's
   CURRENT version — a mismatch rejects the session even though its
   signature is still perfectly valid and it hasn't naturally expired.
   Bumping that one integer instantly invalidates every session ever
   issued for that user. Read from a new cached key
   (`user_session_versions`, 10s periodic refresh, `refresh_now()`
   after every bump — the same mutation-triggered-refresh pattern every
   other cached key in `app/cache.py` already uses) rather than a live
   query per request — this check runs on literally every authenticated
   request the whole app serves, so it can't be allowed to add a Neon
   round trip to each one.

   Two triggers bump it: **changing your password** (the account owner
   stays logged in — their OWN session is re-stamped with the new
   version in the same response — but every OTHER session for that
   user, including a possibly-stolen one, is dead on its very next
   request), and a new **"Log out everywhere"** button (Account →
   Profile) for when you want every session gone, current device
   included, without also having to pick a new password to get there.

**One deliberate, expected side effect**: shipping this invalidates
every session that existed before it — anyone (including whoever's
testing this) needs to log in once more after deploying. After that,
everything works normally until the version is next bumped.

**Verified** against a real server + real Postgres: the cookie's
`Max-Age` is confirmed as `86400`, not the old 14-day default; a
second, independent login as the same user (simulating a second
device) is confirmed to keep working right up until the first device
changes its password, at which point the second device's session gets
a real 401 on its very next request while the first device's
re-stamped session keeps working with no re-login needed; "log out
everywhere" is confirmed to invalidate BOTH sessions, current device
included; authenticated request latency was measured before/after
(≈2ms average over 20 requests) to confirm the new check reads the
cache, not Postgres, per request; and the new UI button was clicked
through a real browser, correctly ending with a redirect back to the
login page.

## What's here (Step 33: sessions now slide on activity — 2h idle timeout, not a flat 24h one)

Asked for directly, after Step 32's fix: something closer to a
refresh-token model, "even more secure." Worth being precise about
what changed and why: this app doesn't use JWTs (it's Starlette's
signed session cookies, `itsdangerous` — no algorithm-confusion
surface, no separate key-rotation story to get right), and a literal
OAuth2-style access+refresh token pair was a disproportionate amount
of new complexity for what this app actually is — a single first-party
browser SPA on its own same-origin backend, not a multi-client system
that needs a token usable outside a cookie jar. Building one for real
would mean a second token type, a refresh endpoint, and 401-catch-
refresh-retry logic wrapped around every call in `api.js`, to end up
with roughly the same security property a shorter, sliding cookie
already gets you — and a cookie is httpOnly, meaning JS (and therefore
XSS) can't read it at all, which is actually a tighter spot to keep a
credential than a token sitting in reachable client-side storage.

What actually ships: `SESSION_MAX_AGE_SECONDS` dropped from Step 32's
flat 24h to **2 hours**, and it's now a genuine sliding idle timeout,
not a countdown from login. Mechanism confirmed directly from
Starlette's own installed source (not assumed): `SessionMiddleware`
only re-signs and re-issues `Set-Cookie` when the session dict is
mutated during a request (`session.modified`) — so
`AuthMiddleware._session_ok()` now writes a `last_seen` key on every
request that passes its checks, which is what makes Starlette reissue
the cookie with a fresh `Max-Age` AND a fresh `itsdangerous` signed
timestamp on every touch. Both layers refresh together: the
browser-visible `Max-Age` hint, and the server-side signature
timestamp `unsign(..., max_age=...)` actually verifies against — not
just a client-trusted expiry. The touch lives in the middleware's own
gate check, not in individual route handlers, so it fires on ANY
authenticated request, reads included — "activity" has to mean any
real use of the app, not just requests that happen to write something.
Session revocation (`session_version`, change-password, "log out
everywhere") is unchanged — all of it still works exactly as Step 32
built it, layered underneath this.

**Verified** against a real server + real Postgres, including decoding
the signed cookie directly with `itsdangerous.TimestampSigner` rather
than trusting the `Set-Cookie` header alone: `Max-Age` confirmed as
`7200` (2h), not the old `86400`; a pure read (`GET /auth/me`, which
never itself writes to `request.session`) confirmed to STILL reissue
`Set-Cookie` (proving the touch is middleware-level, not
handler-level); the reissued cookie's own embedded `itsdangerous`
timestamp confirmed strictly later than the original after a real
1.2s sleep between requests (proving an actual server-side refresh,
not just a header that looks right); the refreshed cookie itself
confirmed to still authenticate a follow-up request; and the full
Step 32 revocation suite re-run afterward to confirm none of it
regressed.

## What's here (Step 34: the Deploy modal is wider on desktop, laid out in a grid)

Reported directly: some strategies have a dozen-plus config fields,
and filling them one full-width row at a time in a narrow, fixed-width
box wasn't pleasant. Scoped to the Deploy modal specifically, not
every modal — Clear All, Edit Deployment, and the rest have few enough
fields that the existing 560px box is already fine, and widening those
too would just be unused whitespace. Left completely alone on mobile,
as asked — everything below is gated behind the existing 761px
breakpoint this app's mobile layout already uses, so narrow stays
narrow.

Two changes, both above that breakpoint only: the modal itself grows
to 900px (from 560px), and the deployment-name/mode/initial-capital
row plus the strategy's own config fields both become CSS grids —
`repeat(3, 1fr)` for the fixed top row, `repeat(auto-fit,
minmax(240px, 1fr))` for the config fields, so a 4-field strategy and
a 14-field one both just lay out in however many columns actually fit
without needing a per-strategy column count anywhere.

**Real bug found and fixed along the way, not assumed away**: the
grid rule for `#deployConfigFields` computed correctly in the
stylesheet but had zero visible effect — `catalog.js` was setting
`element.style.display = 'block'` directly whenever the modal opens
or the Advanced-JSON toggle switches back to the form view, and an
inline style always wins over any stylesheet rule regardless of
selector specificity. Caught by checking the actual computed style in
a real browser rather than trusting the screenshot alone, fixed by
switching those two call sites to `removeProperty('display')`, which
lets the stylesheet (grid above the breakpoint, block below it)
decide instead of a leftover inline override.

**Verified** against a real server + real browser: a 14-field
strategy's config now visibly lays out in a 3-column grid at
1440×900, confirmed via computed style (not just eyeballing the
screenshot) that `display: grid` actually took effect after the JS
fix; the same modal at a real 390×844 mobile viewport is pixel-
identical to before this change; the Advanced (raw JSON) toggle still
correctly hides/shows the right element in both directions after
switching off inline `display` overrides; and a real deployment was
submitted through the new grid-laid-out form and confirmed via the API
afterward to have saved with the correct config values — the layout
change didn't corrupt anything being read back out of the form.

## What's here (Step 35: "Flatten All" — a panic button, not a delete button)

First of a batch of in-app features (Telegram alerts parked for
separate setup). `POST /deployments/flatten-all` closes every open
position, across every deployment that isn't already `stopped`, at
the last known price — then pauses whichever were `active` so nothing
immediately re-enters on the next tick. Deliberately its own thing,
not a variant of an existing primitive: `pause()` alone leaves
positions open; `stop(force_close=True)` closes them but permanently
stops the deployment (`resume()` refuses a `stopped` one outright).
This sits between them — get out of every position right now, decide
what to do about the deployment itself later, same reversibility as a
manual pause, just with nothing left open when you get there. Works on
already-`paused` deployments too (pause never touches positions, so a
paused deployment can absolutely still have one open) — those get
flattened but stay paused, no unwanted status change. A `stopped`
deployment is skipped entirely; there's nothing left to flatten by
definition.

One deployment's failure (most likely a stale/missing live price it
can't recover a fallback price from) never aborts the rest —
`flatten_all()` keeps going and reports which one(s) failed, since the
entire point is "get out of everything," not "get out of everything
until the first problem."

Deliberately NOT gated behind clear-all's password + typed-confirmation
— unlike Clear All, this touches no history at all (every closed
position is a normal, fully-recorded fill; deployments still exist and
can be resumed afterward), so a plain `confirm()` before calling it is
proportionate to what it actually does.

**Verified** against a real server + real Postgres, seeding genuine
open positions directly (no live Kite ticks needed in this sandbox) to
exercise all three cases at once: an `active` deployment with an open
position (confirmed: position closed, deployment auto-paused, can be
resumed afterward, an event recorded with `reason: flatten_all`), an
already-`paused` deployment with an open position (confirmed: position
closed, status untouched, a `flattened` event recorded instead), and a
`stopped` deployment with none (confirmed: completely untouched, not
even counted as flattened). The full flow was also clicked through a
real browser: the button, the `confirm()` dialog with the expected
wording, and the summary `alert()` afterward.

## What's here (Step 36: named config presets in the Deploy modal)

Second of the in-app feature batch. Some strategies (`intraday_dtt_
adjusted`) have 14 config fields — deploying the same setup repeatedly
meant retyping every one of them each time. `strategy_presets`
(migration 0007) stores a named snapshot of just the config object
(never `deployment_name`/`mode`/`initial_capital` — those are
per-deployment metadata, not part of what a preset remembers), scoped
to `(strategy_name, preset_name)` rather than a globally unique name —
the same preset name can mean something different for two unrelated
strategies, and there's no reason to force distinct names across
strategies that have nothing to do with each other.

The Deploy modal gained a "Preset" row: a dropdown of saved presets for
whichever strategy the modal's currently open for, a "Save current"
button that snapshots exactly what `_readConfigFromFields()` would
submit, and a delete button once a real preset is selected. Selecting
a preset re-renders the simple form from its config (dropping out of
Advanced/raw-JSON mode first if that was open, so the loaded values are
actually visible) — the same `_renderConfigFields()` the strategy's own
`default_config` already uses, so a preset behaves exactly like a
different starting point for the same form, not a separate code path.
Not cached server-side (`app/cache.py`) — this is opened occasionally
from inside a modal, nowhere near the traffic the hot-read endpoints
that actually needed caching see.

**Verified** against a real server + real Postgres: config round-trips
through save/list exactly as submitted; a duplicate preset name for the
SAME strategy correctly 409s, the identical name for a DIFFERENT
strategy is correctly allowed (proving the scoping, not just the
uniqueness); a blank name 400s; deleting a real preset under the wrong
`strategy_name` in the URL 404s rather than silently deleting across
strategies; deleting an already-deleted preset 404s rather than
crashing; and unauthenticated access is rejected like every other
endpoint. The full UI flow was also clicked through a real browser:
saving auto-selects the new preset, changing a field then reloading the
preset correctly restores the saved value, the preset survives closing
and reopening the modal (proving it's real server-side storage, not
just in-memory for one modal session), and deleting removes it from
the dropdown.

## What's here (Step 37: export a deployment's trades as CSV)

Third of the in-app feature batch. Purely client-side, deliberately —
this is a records/backup convenience, not a data interchange format
anything else in this app reads back, so it doesn't need a backend
endpoint at all: `toCsv()`/`downloadCsv()` (new, in `api.js` alongside
the other shared formatting helpers) build the file from the exact
same JSON the Trades tab already fetches to render its table, then
trigger a real browser download via a `Blob` + a programmatic
`<a download>` click.

One thing worth being deliberate about: it exports the FULL trade
history (`Api.getTrades(id, 100000)`), not just the up-to-200 rows the
on-screen table caps itself at — a "back up my records" button that
silently drops older fills because the table view happens to paginate
would be a real, easy-to-miss bug. CSV cells are escaped per RFC 4180
(quoted whenever a field contains a comma, quote, or newline — nested
trigger metadata gets JSON-stringified into its own column, quotes
doubled correctly), rows joined with `\r\n`, and the file is written
with a UTF-8 BOM so Excel — still the most likely thing to open a
"export my trades" button's output — reliably detects UTF-8 instead of
guessing a legacy codepage and mangling anything non-ASCII, rather than
assuming that risk was already covered.

**Verified** against a real server + real Postgres + an actual
triggered browser download (not a mocked assumption that a click
"would" download something): seeded a genuine closed round-trip (a buy
then a matching sell, both with real trigger reasons), clicked Export
CSV in the Trades tab, captured Playwright's own `expect_download()`,
and confirmed the downloaded file's exact suggested filename (derived
from and sanitized from the deployment's own name), the exact header
row, exactly 2 data rows, and that both fills' action/price/reason
values round-tripped correctly into the file.

## What's here (Step 38: dark mode)

Fourth of the in-app feature batch. The whole UI (`static/index.html`
and `static/login.html`) was already built on a CSS custom-property
token system (`--bg`, `--paper`, `--ink`, `--accent`, etc., defined
once on `:root`) — dark mode is mostly a second set of values for
those same tokens, not a parallel stylesheet.

The dark palette isn't a generic slate-grey theme bolted on — it reuses
the light theme's own "stamped ledger" identity with ink and paper
swapped: the light theme's near-black ink (`#1B1130`) becomes the dark
theme's page ground, and the light theme's warm cream becomes the dark
theme's ink. The semantic colors (`--gain`/`--loss`/`--brass`/`--info`)
are brightened versions of their light-theme selves — legible as TEXT
on a dark ground — and their `-soft` badge-background counterparts go
dark-toned instead of pale, since a pale mint badge floating on a
near-black page would look like a rendering bug, not a design choice.
The signature offset "stamped" shadow motif (`--shadow`/`--shadow-sm`,
plus a new `--shadow-lg` extracted from a rule that had it hardcoded)
switches from a translucent-ink shadow to a translucent-black one, since
an ink-colored shadow is invisible against an ink-colored background.

Applied two ways, both driven by the same tokens: automatically via
`prefers-color-scheme: dark` for anyone who hasn't chosen explicitly,
and explicitly via a `[data-theme="dark"]` attribute on `<html>` once
the sidebar's new "Dark mode" toggle is clicked (persisted in
`localStorage`, with a `:not([data-theme="light"])` guard so an
explicit light choice still beats a dark OS preference). A tiny inline
`<script>` at the very top of `<head>` — in both `index.html` and
`login.html` — reads that `localStorage` value and stamps the
attribute before the stylesheet is even parsed, which is the only way
to avoid a flash of the wrong theme on first paint; `toggleTheme()`
(near the bottom of `index.html`'s own inline script) does the same
thing on click, plus keeps the button's own label (🌙 Dark mode / ☀
Light mode) truthful.

Audited for stragglers: grepped both files for hex/rgba color literals
declared *outside* the `:root` block — i.e. anywhere a dark-mode
override of the root tokens alone wouldn't reach — and found nine:
`.nav-item.active`, `.btn-primary` and its `:hover`, `.btn-danger:hover`,
table row dividers, `.tabs button.active`, and the modal's box-shadow.
All nine were converted to reference tokens (`--accent-hover`,
`--loss-hover`, `--row-line`, `--shadow-lg` — new; the rest already
existed) instead of literals, so nothing was left stuck at its
light-theme-only value. Two more hardcoded rgba rules — the modal
overlay backdrop and the mobile nav drawer's backdrop — were
deliberately left alone: both are dimming scrims drawn *over* whatever
content sits behind them, and a dark, ink-colored scrim reads correctly
as "dimmed" against page content in either theme, so there was nothing
theme-specific to fix there.

**Verified** against a real server + real Postgres + a real browser,
not just a code read of the CSS: confirmed via `getComputedStyle` that
the app defaults to light when the OS is light and no choice is saved,
follows `prefers-color-scheme: dark` automatically when the OS is dark
and no choice is saved, that clicking the toggle flips the whole page
instantly and flips the button's own label, that the choice persists
to `localStorage` and survives a full reload with no flash (checked
`data-theme` is already `"dark"` immediately, set by the pre-paint
script rather than by later JS), and that an explicit light choice
correctly overrides a dark OS preference. Then screenshotted every
major view in dark mode — Dashboard, Strategy Catalog, the widened
Deploy modal (with the Step 36 preset row), Deployed Strategies (with
the Step 35 Flatten-All button), a deployment's Trades tab with an
expanded trigger-metadata row, its Stats tab, Account, the mobile
slide-in drawer, and the pre-login screen — checking each by eye for
any element left unreadable (low-contrast text, a badge that didn't
adapt, a shadow that vanished). Finally, re-ran the existing Step 36
(config presets) and Step 37 (CSV export) Playwright suites against
the changed markup/CSS end to end — both passed unchanged, confirming
the token-recoloring didn't regress either feature's actual behavior.

## What's here (Step 39: a Portfolio view — whole-account rollups the Dashboard doesn't try to be)

Fifth and last of the in-app feature batch. The Dashboard already
answers "what's happening right now" (live P&L, open positions, recent
fills); Portfolio is deliberately scoped to the slower-moving questions
a per-deployment view — even the Dashboard's own combined one — can't
answer: how is combined equity trending over time, how much capital is
actually deployed vs sitting idle, and whether unrelated strategies are
unknowingly stacking exposure to the same underlying.

Two of Portfolio's three sections needed no new backend at all —
**capital utilization** (total capital vs idle cash across every
active/paused deployment, broken down by strategy) and **exposure by
symbol** (every open position across every deployment, grouped by
symbol — three unrelated strategies each independently long NIFTY 50
look completely fine in isolation, but that's real stacked exposure to
one underlying that's only visible once everything is combined) are
both computed client-side, purely from data the Dashboard already
fetches (`Api.listDeployments()`, `Api.getAllPositions('open')`) — no
N+1 problem to avoid here, unlike Step 4's aggregate positions/trades
endpoints, since these are just different groupings of the same already-
fetched rows, not per-deployment detail that would need its own request.

The **combined equity curve** is the one piece that genuinely needed
new backend work — summing time-series data server-side is exactly the
kind of aggregation Step 4's own docstring already argues for doing in
Postgres rather than the frontend. New `GET /portfolio/equity-curve`
(`app/routers/aggregate.py`) + `queries.list_portfolio_equity_curve`
bucket every deployment's `deployment_snapshots` rows into shared
`bucket_seconds`-wide windows (default 300s, matching
`DEFAULT_SNAPSHOT_INTERVAL_SECONDS`) and sum `total_value`/
`realized_pnl_cumulative` within each bucket — needed because
`snapshot_all_active()` calls `datetime.now()` once per deployment
inside its own loop, so two deployments' snapshots from the same
5-minute "tick" can differ by a few milliseconds and would otherwise
land in their own separate single-row buckets instead of summing
together. Deliberately not scoped to any particular deployment status:
a bucket reflects however many deployments actually had a runner (i.e.
were active) AT THAT POINT IN TIME — a since-paused deployment's older
snapshots still contribute to its own past buckets (history doesn't
retroactively change), it just stops contributing to new ones the
moment it's no longer active. Cached the same way as Step 4's two
endpoints (`app.state.cache`), but at a 30s interval instead of 6s —
the underlying data only gets new rows every 300s, so polling any
faster than a fraction of that would just re-serve identical rows.

Reused, not reimplemented: `renderEquityChart()` — Detail's own
per-deployment curve (Step 5) — moved from `detail.js` into `api.js`'s
shared-helpers section so Portfolio's combined curve could call the
exact same renderer instead of a second copy of the same SVG-polyline
logic. It already only needs `{snapshot_at, total_value}` points, so
Portfolio just maps its `bucket_at` field to `snapshot_at` before
calling it, rather than the shared function learning two field names
for the same concept.

**Verified** against a real server + real Postgres + a real browser:
seeded two deployments both independently long NIFTY 50 (one via the
normal fill path affecting real `cash`/position rows, so capital
utilization and exposure numbers are the real computed values, not
fixtures) plus synthetic `deployment_snapshots` rows deliberately
spaced a few seconds apart within the same 5-minute window (proving
same-tick rows from different deployments get summed into one bucket)
and a second, later bucket with only one deployment contributing
(proving a bucket a deployment didn't reach isn't wrongly zeroed or
dropped) — confirmed via the actual rendered page that the combined
curve shows exactly 2 buckets (not 3 raw snapshot rows), capital
utilization sums both deployments' capital/cash correctly, and the
exposure table collapses both deployments' NIFTY 50 positions into one
row with the correct combined net quantity. One real bug caught in my
own test, not the app: seeding via direct `queries.record_fill()` calls
bypasses the API mutation paths that call `cache.refresh_now()`
themselves, so the first assertion run raced the `positions_open`
cache's 6s background refresh and saw only one of the two seeded
fills — fixed by waiting a cache cycle before asserting, the same
staleness window a real user would see too, just not one worth
querying live for. Re-ran the Step 37 CSV export suite (exercises
`api.js`'s shared helpers) and a fresh check of Detail's own Stats tab
equity chart afterward to confirm moving `renderEquityChart()` didn't
regress the view it originally belonged to.

## What's here (Step 40: Strategy Comparison — overlay equity curves, indexed to % return)

First of two follow-up features requested after the original in-app
batch shipped, done as an in-app page rather than wired to Telegram
(which stays parked). A new "Compare" nav item: pick 2–6 deployments
from a checklist (every deployment, not just active/paused — unlike
Portfolio's live-only combined curve, "how did my old stopped strategy
do against this one" is exactly the question this view exists to
answer) and overlay their equity curves on one chart.

**Indexed to % return, not raw rupees** — the one design decision the
whole feature hinges on. Two deployments with ₹10,000 and ₹1,00,000
initial capital are not comparable on a raw total_value chart: a ₹300
move means completely different things at those two scales, and
whichever deployment happens to be bigger would visually dominate the
chart regardless of which one is actually performing better. Each
curve is indexed to its OWN first snapshot (0% at the start, not
initial_capital — a deployment's first snapshot may already reflect
whatever cash/position state existed by the time the snapshot loop
first ran), so the chart answers "which one grew faster," not "which
one started with more money." No new backend endpoint needed — this
reuses the existing per-deployment `GET /deployments/{id}/snapshots`
(Step 5) and `GET /deployments` (Step 1), fetched once per selected
deployment (bounded by the user's own 2–6 selection, not the N+1
problem Step 4's aggregate endpoints exist to avoid).

**Colors, done properly, not eyeballed:** loaded the `dataviz` skill
before writing any chart code specifically because this is the app's
first genuinely multi-series chart — every other chart here (Detail's
equity curve, Portfolio's combined curve) is single-series, where a
semantic up/down color is enough. A 6th series needs six named colors
that stay tellable apart, and the app's own semantic tokens
(`--gain`/`--loss`/`--brass`/`--info`) were the wrong tool for that
job even though they're already 4 of the 6 colors this app has:
reusing them as "series 3" would make them lie every other place
they're on screen (they carry FIXED meaning — profit/loss/paused/entry
— everywhere else). Ran the skill's own validated categorical
six-color set through its `validate_palette.js` script against this
app's actual light (`#FFFDF6`) and dark (`#1E1533`) chart surfaces
before adopting it as new `--chart-1` through `--chart-6` tokens
(fixed order, never cycled or reassigned per-chart — see the tokens'
own comment in `index.html`'s `:root`) — full CVD-safety and contrast
report for both themes is in the commit. The light-mode palette
carries a contrast WARN on 3 of the 6 colors against the cream
surface, which the skill's own rule says is legal only with "relief":
a legend (always present for 2+ series, colored swatch + name + %
value) and a full data table below the chart (every selected
deployment, even ones the chart itself skips) — both already planned
for other reasons, so the relief was free, not bolted on after the
warning.

**Export**, per the request that came with this feature: long/tidy-
format CSV (`Deployment, Strategy, Time, Total Value, Pct Return` — one
row per deployment per snapshot), not a wide table with one column per
deployment — deployments' snapshot timestamps don't line up exactly
(see the chart's own index-based, not wall-clock, X axis for the same
reason), so a wide layout would need interpolation or ragged blank
cells. Long format has no such problem and is the shape any of the
usual next steps (Excel, pandas) actually want. Reuses the exact
`toCsv`/`downloadCsv` helpers Step 37 built, unchanged.

**Verified** against a real server + real Postgres + a real browser:
seeded three deployments with deliberately different capital sizes and
trajectories — a ₹10,000 deployment that grew to ₹10,700 (+7%), a
₹1,00,000 deployment that DROPPED slightly in rupees but by a much
smaller relative amount (₹99,800, -0.2%), and a third with only ONE
snapshot — then confirmed on the actual rendered page that the small
deployment's bigger % return correctly beats the big deployment's
smaller one despite a much tinier absolute rupee move (the whole point
of indexing), that the single-snapshot deployment is correctly
excluded from the chart (needs 2+ points to draw a line) while still
appearing in the table underneath (the relief rule in practice, not
just in the CSS), that the legend's swatch colors are distinct per
series, that the CSV downloads with the right long-format shape and
row count, and that the 6-deployment cap actually disables further
checkboxes once hit rather than only suggesting a limit. Caught one
real bug in my own test, not the app: driving every checkbox through a
tight click-loop raced Playwright's own post-click stability check
against the picker's full re-render on every toggle (real users click
one box, see it settle, then click the next — never mid-render like a
scripted loop can) — fixed by exercising the first click for real and
the rest through the same `Compare.toggle()` the checkboxes themselves
call, not a special path. Also re-ran the Step 38 dark-mode suite
afterward, since this added a new categorical palette to the same
`:root`/`[data-theme="dark"]` blocks that suite already covers — still
passes unchanged.

## What's here (Step 41: Reports — a real single-period statement, not just a digest)

Second of two follow-up features. Started as a simpler "P&L digest"
(one table, every day/week's realized P&L in a list), then the user
pointed at a mature personal-finance app's own Reports page (period
navigation, stat cards with vs-previous-period deltas, category/holding
breakdown tables, collapsible sections) and asked for that page's
*feature set* — not its visual style — adapted here. What shipped is a
genuine rework, not a coat of paint on the original digest: a
single-period drill-down you navigate through like a statement, with
the original digest table demoted to a supporting "Recent Periods"
trend section underneath it.

**Deliberately mapped, not copied wholesale.** The reference app has
sections with no honest equivalent here — Net Worth (assets vs
liabilities) and Investment Holdings (a personal brokerage's Top
Holdings) don't correspond to anything in a paper-trading app; the
closest analogue to a running net-worth chart is already the Portfolio
view (Step 39), so Reports doesn't duplicate it. What DOES map cleanly:
its period-type + prev/next/latest navigation, its four-stat-card row
with a "vs previous period" delta, and its category-breakdown tables
became **By Strategy** and **By Deployment** — this app's own
equivalent of "which spending category" and "which holding," i.e.
which strategy and which specific deployment actually made or lost
money in the period you're looking at, not all-time. Its
draggable-and-persisted section reordering was deliberately left out —
real engineering cost (drag-and-drop + server-side layout persistence)
for a single-user app where section order rarely needs to change;
collapse/expand shipped instead (persisted per-section in
`localStorage`), which gets most of the decluttering value for a
fraction of the cost. Its CSV-export-per-section and "Open in Google
Sheets" links were narrowed to one Export CSV button for the trend
table specifically — the single-period breakdown above it is already
fully on screen, so a one-row CSV of it would be a file for no reason.

**Backend, all new:** `period_bounds(period, offset)`
(`app/routers/aggregate.py`) is pure-Python date math — no DB round
trip — computing the `[start, end)` window for "day/week/month, N
periods before now," in this app's own Asia/Kolkata timezone (matching
the ticker clock and Step 40's digest bucketing) so "today" means what
someone watching IST markets expects, not the server's UTC day
boundary. Week is Monday-start, matching Postgres's own
`date_trunc('week', ...)` convention already used by
`list_pnl_digest`, so the single-period view and the trend table never
disagree about where a week starts. Three new queries —
`pnl_summary_for_range`, `pnl_by_strategy_for_range`,
`pnl_by_deployment_for_range` — all take an exact `[start, end)` and
stay REALIZED-P&L-only, same reasoning as Step 40's digest (a live
unrealized number has no honest place in a report of a SETTLED past
period — an open position's paper P&L on a bygone day isn't a fact
anymore). New `GET /portfolio/pnl-report?period=&offset=` composes all
three plus the previous period's summary (for the delta) into one
payload; not cached, same reasoning as the digest endpoint (cheap
GROUP BY at this app's scale, no hot polling path to protect since
Reports isn't in `_AUTO_REFRESH_VIEWS`).

**Verified** against a real server + real Postgres + a real browser:
seeded two deployments on two different strategies with closed
positions spread across two different (real, backdated) days —
confirmed the exact realized P&L, the exact vs-previous-period delta,
and the exact By Strategy / By Deployment attribution all compute
correctly; stepped Prev into yesterday and confirmed the numbers
change to yesterday's (not today's) and Next re-disables only once
back at the present; switched period type to Weekly and confirmed both
seeded days correctly combine into one week's total; collapsed a
section, reloaded the full page, and confirmed it stayed collapsed
(persisted, not just in-memory); and downloaded the trend CSV and
confirmed its header/shape. Re-ran the Step 38 dark-mode suite
afterward since this added new CSS token usage to the same
`:root`/`[data-theme="dark"]` blocks — still passes unchanged.

## What's here (Step 42: in-app real-time alerts)

The non-Telegram version of "push me an alert" — surfaced instead of
Telegram alerts after discussing what Telegram would actually need
(bot token, chat_id, still no interactive-command story worth building
yet) and picking "the in-app version, zero external setup" as the
thing to build first. Telegram itself stays parked.

**Every deployment event — a fill, a pause/resume/stop, a strategy
error — now reaches the browser the instant it's recorded**, as a
toast in the top-right corner, on any view, not just a deployment's
own Activity tab. New `/ws/events` websocket, same shape as the
existing `/ws/ticks` (same accept/shutdown/disconnect race — see that
handler's own docstring for why the race exists at all) but carrying
event payloads instead of price ticks, fed by a **second, separate**
instance of the same fan-out class ticks already used.

That fan-out class used to be called `TickBroadcaster` — renamed to
`Broadcaster` rather than writing a near-identical `EventBroadcaster`
copy, since its subscribe/unsubscribe/backpressure mechanics never had
anything tick-specific about them to begin with. Ticks and events still
use **separate instances** (`app.state.broadcaster` vs
`app.state.event_broadcaster`) deliberately — `DeploymentRunner`
subscribes to the tick one specifically to feed its own strategy;
merging the two streams would hand every strategy event payloads it'd
wrongly try to process as ticks.

**Where events get broadcast from:** the app already had a
`deployment_events` table and `queries.record_event()` calls at exactly
six call sites (`DeploymentManager.pause/resume/stop/flatten_all`,
`DeploymentRunner`'s fill and strategy-error paths) — this is what the
existing Activity tab already reads. Rather than bolting a broadcast
call onto each of those six sites separately (easy to add a 7th event
type later and forget the broadcast half), both classes got a small
`_record_event()` helper that does the DB write AND the broadcast
together, and all six call sites now go through it. One helper per
class (not shared) since `DeploymentRunner` has no reference back to
its manager — same duplication shape the codebase already uses for
`on_fill`/cache-refresh hooks.

**Toast styling is STATUS color, not the Compare view's categorical
palette and not bare gain/loss** — a fill's own P&L direction isn't
knowable from the event alone (a sell can be a stop-loss or a
profit-take), so fills read as neutral/informational (`--info`) rather
than colored by an outcome the toast can't actually verify;
`strategy_error` is the one category that gets `--loss` (something
actually needs attention); pause/resume/stop/flatten are administrative
(`--brass`), not P&L-colored at all. Auto-dismisses after 8s, or close
manually — both paths tested.

**Optional browser push**, opted in via a new "Notifications" section
in Account → Profile: fires a real `Notification` (works even with the
tab backgrounded) but ONLY when the tab isn't currently focused —
firing one while you're looking at the tab would just double up what
the toast already shows. Off by default; `Notification.requestPermission()`
is only ever called from the toggle's own click handler, never
ambiently on page load, since browsers require that and would ignore
(or the browser would rightly distrust) a permission prompt fired any
other way.

**Verified** against a real server + real Postgres + a real browser:
triggered real pause/resume through the actual `POST /deployments/{id}/
pause` and `/resume` endpoints (not direct DB/manager calls) — the full
router → manager → `_record_event()` → broadcast → `/ws/events` →
browser-toast path, exactly what a real user action goes through —
and confirmed the toast appears with the correct deployment name and
event label, gets the right category, dismisses both manually and
automatically after ~8s, and that Account → Profile's Notifications
section renders. Category mapping for fill/error events (identical
`_record_event()` code path, just a different `event_type` string —
nothing meaningfully different to re-prove end-to-end) verified via
direct frontend injection. Screenshotted the toast stack in both light
and dark mode and on a real mobile viewport. Re-ran the Step 38
dark-mode suite afterward — still passes unchanged.

## What's here (Step 43: all-time strategy leaderboard)

Third of the fresh feature round (in-app alerts, this). A new "All-Time
Strategy Performance" section on the Portfolio page: one ranked row per
strategy_name — realized P&L, win rate, profit factor, positions
closed, and how many deployments ever ran it — answering "which
strategy has actually made the most money since I started," a standing
question neither Reports (always scoped to one period at a time) nor
Portfolio's own Capital Utilization section (deliberately live-only,
active+paused) tries to answer.

**Deliberately ALL-TIME and NOT live-scoped** — the one design decision
this feature turns on, and the opposite scoping choice from the section
directly above it on the same page. Capital Utilization excludes
stopped deployments because idle-vs-deployed capital is a "right now"
question a stopped deployment has no answer to anymore. The
leaderboard is the opposite: "which strategy made the most money" is a
historical question, and a strategy you've since stopped running is
very much part of that history — excluding it would make a
still-good-on-paper strategy quietly vanish from its own scoreboard the
moment you stop the deployment that proved it worked. New
`queries.list_strategy_leaderboard` joins `positions` (closed only) to
`deployments` with NO status filter and NO date bound at all, grouped
by `strategy_name` alone — two deployments running the same strategy
correctly collapse into one row (`deployments_count` says how many),
same principle Reports' By Strategy breakdown already established, just
without a period window.

**Profit factor computed client-side, not server-side** — the backend
ships raw `gross_win`/`gross_loss` sums, and the frontend divides them
using the EXACT SAME convention Detail's own Stats tab already computes
from raw closed-position P&Ls (gross win over absolute gross loss,
`∞` when there's been a loss-free win, `—` when there's been neither) —
reused rather than re-derived, so there's exactly one profit-factor
formula in the whole app, not two that could quietly drift apart.

New `GET /portfolio/strategy-leaderboard` — cached via
`app.state.cache` (20s, matching the existing `strategies` key), unlike
the Reports page's pnl-digest/pnl-report endpoints: Portfolio itself
IS in `_AUTO_REFRESH_VIEWS` (polled every 6s), so leaving this one
uncached would mean the same GROUP BY re-running on every single poll
for a number that doesn't meaningfully change that often.

**Verified** against a real server + real Postgres + a real browser:
seeded two deployments of the SAME strategy (proving they combine into
one row with `deployments_count=2`) plus a third deployment of a
DIFFERENT strategy that was then STOPPED via the real `POST
/deployments/{id}/stop` endpoint (proving a stopped deployment's
history still counts) — confirmed exactly 2 rows for 3 deployments,
the correct combined P&L/win-rate/profit-factor math for both
(including the all-losses case: profit factor correctly shows `0.00`,
not `∞` or a dash), and that Capital Utilization directly above it
correctly shows only 1 "live" strategy while the leaderboard below
shows both — the scoping difference working exactly as designed, on
the same page, side by side. Screenshotted in light and dark. Re-ran
the Step 38 dark-mode suite afterward — still passes unchanged.

## What's here (Step 44: killed the blind polling — event-driven refresh, no more flicker)

Reported directly: Dashboard/Catalog/Deployments/Portfolio were
re-fetching every 5-6 seconds even with the market closed and nothing
actually changing, AND every one of those refreshes visibly flickered
the whole view. Two real, separate bugs, both traced to their actual
root cause rather than patched around:

**The flicker's exact cause**: every view's `load()` unconditionally
reset its containers to a loading spinner FIRST, then fetched, then
rendered — correct for "I just navigated here" (there's nothing on
screen yet), but that same function was also what the 6s auto-refresh
timer called, so a view sitting open would blank to spinners and
repopulate every few seconds even when the underlying numbers hadn't
moved at all. Fixed with a `quiet` parameter: `Dashboard.load(true)`
(and the same on Catalog/Deployments/Portfolio) skips the spinner
reset entirely and goes straight from fetch to render — old content
stays on screen until new content is ready, then swaps directly, no
flash to blank.

**The polling's exact redundancy**: auditing every `cache.refresh_now()`
call site (grepped the whole backend) showed the app already refreshes
`deployments`/`positions_open`/`trades_recent`/`strategies` at the
EXACT moment of every mutation that could change them — deploy,
pause, resume, stop, flatten, every fill, the strategy-enabled toggle.
The 6s/12s/20s background poll intervals were pure redundancy on top
of that already-complete coverage, re-serving identical data to a
frontend that was ALSO polling on its own fixed timer independently —
polling squared, doing the same wasted work at both ends of the wire
for the exact same reason the user called out ("the market is closed,
why would positions update").

Two real gaps found and closed while auditing that coverage, both
things this whole fix would have been undermined by if left alone:
`portfolio_equity_curve` and `strategy_leaderboard` had NO mutation
hook at all, purely poll-driven — now `refresh_now("portfolio_equity_curve")`
fires once per snapshot round (`DeploymentManager.snapshot_all_active`)
and `refresh_now("strategy_leaderboard")` fires alongside the existing
fill-triggered refreshes. And creating a deployment had never recorded
an event AT ALL, not even to the Activity tab — a "created"
`deployment_events` row (and its own broadcast/toast) didn't exist
until now, closed here rather than shipped as a known gap, since the
whole point of this fix is that every real mutation is covered.

**The actual fix, now that the previous two steps' `/ws/events` channel
existed to build on**: the frontend no longer polls on a matching
timer at all — `/ws/events` (Step 42) firing is the PRIMARY trigger for
a quiet refresh of whichever view is currently open (debounced 500ms
so a burst of events, e.g. Flatten All closing five positions at once,
collapses into one refresh instead of five). The old 6s
`setInterval(reload, 6000)` is now a 90s **defensive backstop only** —
in case a websocket event is somehow missed (a dropped connection
mid-reconnect) — calling the same quiet `load(true)`, never the
spinner-resetting one. Backend poll intervals for the now-fully-
mutation-covered cache keys moved from 6-20s to a matching 90-120s
backstop, same reasoning: still there in case a `refresh_now()` call
site gets missed by a future change, no longer doing any of the real
work. `db_health` and `user_session_versions` were deliberately left
alone — a genuine periodic connectivity check and a security-sensitive
revocation backstop, respectively, neither one actually redundant.

**Verified** against a real server + real Postgres + a real browser,
not by reasoning about the code alone: instrumented a real
`MutationObserver` on Dashboard's and Deployments' own containers
*before* triggering a real mutation (deploying a strategy via the
actual `POST /deployments` endpoint), then confirmed the spinner
NEVER appeared at any point during the resulting live update while the
new deployment's data correctly showed up anyway — the flicker fix and
the live-update mechanism both proven true simultaneously, not just
asserted. Separately, sat idle on the Dashboard for 12 real seconds
with zero mutations happening and captured every outgoing request:
confirmed exactly zero requests to `/deployments`, `/positions`,
`/trades/recent`, `/strategies`, or `/portfolio/*` fired during that
window — the actual complaint, proven false now rather than just
reasoned about. Confirmed the new "created" event via a deployment's
own Activity tab. Re-ran the Step 38 dark-mode suite and the Step 42
alerts suite afterward (the latter needed one update: it now correctly
sees the new "created" toast fire before the pause/resume ones it was
already checking, which is the fix working as intended, not a
regression) — both pass.

## What's here (Step 45: deployment descriptions, set at deploy time)

Requested, then clarified mid-build: a free-text description per
deployment ("so I can know what is the purpose of that strategy") —
and specifically settable in the Deploy modal itself, alongside the
name, at deployment-creation time, not only afterward.

Most of the plumbing already existed and just wasn't wired to
creation: the `notes` column (migration `0004_deployment_notes.sql`),
`DeploymentUpdate`, the `PATCH /deployments/{id}` endpoint, and an
"Edit deployment" modal on the Detail page were all already there —
added at some earlier point for post-creation renaming, but with no
way to set notes at the moment a deployment is actually created,
which is when "why this one" is easiest to write down, not something
worth reconstructing from memory later via a separate modal.

Closed the actual gap: `DeploymentCreate` gained an optional `notes`
field, `queries.create_deployment` now inserts it, and
`DeploymentManager.create_deployment` passes `payload.notes` through
— no migration needed, the column was already there.
`POST /deployments` itself needed no change; it already forwards the
full payload object to the manager.

On the frontend: the Deploy modal gained a "Description" textarea
next to the config fields, submitted as `notes` alongside
`deployment_name`/`mode`/`initial_capital`. The Deployed Strategies
list table now shows it — a truncated, muted `📝 ...` line under the
deployment name (full text on hover via `title`), the same visual
convention the Detail page already used for notes, now consistent
across both. The existing Edit modal is untouched and still the only
way to change it after the fact.

**Verified** against a real server + real Postgres + a real browser:
deployed a strategy through the actual Deploy modal with a
description typed into the new field, confirmed it POSTed correctly,
showed up truncated on the Deployed Strategies list, showed up in
full on the Detail page, confirmed the Edit modal correctly prefills
with the notes set at deploy time (not blank), edited it there, and
confirmed the new text replaced the old everywhere with no leftover
trace of the original — the full create → display → edit round trip,
not just the create half. Re-ran the Step 38 dark-mode suite
afterward since this touches shared modal CSS; still passes.

## What's here (Step 46: DTT straddle family never skips expiry day — switches to NEXT_WEEK instead)

Requested directly: `intraday_dtt_simple`'s `allow_expiry_day_entry`
(shared, via `resolve_atm_straddle_legs()`, by `intraday_dtt_adjusted`
and inherited by `intraday_dtt_advanced`) used to have a skip path —
default `false` meant "don't trade at all today" when the resolved
contract happened to expire that same afternoon. Renamed to
`switch_to_next_week_on_expiry` and the skip path removed entirely: the
flag now picks *which* contract gets sold on an expiry day, never
*whether* to sell one.

- `false` (new default) — sell the same-day-expiry contract as
  resolved, same-day gamma and all (this is what `true` used to mean
  under the old name — the "opt into it" behavior didn't go away, it's
  just what happens by default now that skipping isn't an option).
- `true` — re-resolve using `"NEXT_WEEK"` instead, for that one entry
  only. `expiry_selector` itself is never mutated, so every other day
  of the week keeps resolving however it's actually configured
  (`THIS_WEEK` or otherwise) — this is a one-day, one-entry
  substitution, not a standing config change.

This is a genuine behavior change, not a pure rename with a
backward-compatible fallback the way `decay_pct`/`spike_pct` got in
Step 26: the old key's `false` meant "skip," the new key's `false`
means "trade the same-day contract anyway" — those aren't the same
action, so silently mapping one to the other would misrepresent what
an existing deployment's config actually says. The old key is simply
no longer read; any deployment still carrying it in its persisted
config just has an unused key and now gets the new default (never
skip) instead of the old skip — which is exactly the fix, applied
automatically. `calendar_btst` was explicitly left alone — it has its
own, separately-named `allow_expiry_day_entry` and a genuinely
different trade shape (an overnight calendar spread, not an intraday
straddle), so it kept its skip-based guard on purpose rather than
picking up this behavior.

Implementation lives entirely in the one shared function
(`resolve_atm_straddle_legs`, `intraday_dtt_simple.py`) both strategies
already called through — no duplicated logic to keep in sync. It now
always returns a 5-tuple (`ce_leg, pe_leg, expiry, strike,
switched_to_next_week`) instead of sometimes returning `None`; both
callers' `if resolved is None: return` skip branches were deleted
along with it, since there's no longer a case that produces one. The
resulting `switched_to_next_week` flag is recorded in the entry fill's
own `trigger_values`, so it's visible in the Activity/trade log on any
day it actually fired, not just inferable from which contract got
traded.

**Verified**: a direct unit test against the shared function with a
stub resolver (four cases — non-expiry day ignores the flag entirely,
expiry day + `false` trades the same-day contract, expiry day + `true`
switches to `NEXT_WEEK`, and confirmed the function can no longer
return `None` for either flag value), plus a full live integration
test through the real dispatcher/runner/resolver/Postgres pipeline
(in-process ASGI) with a synthetic two-expiry options chain — one
expiry forced to the real current date (proving the check is date-based,
not a hardcoded weekday) — covering both `intraday_dtt_simple` and
`intraday_dtt_adjusted` on an actual expiry day: `false` opens a real
straddle on today's own expiring contract, `true` opens one on
`NEXT_WEEK`'s contract instead, both confirmed via the actual `expiry`
recorded in trade metadata (not the symbol text alone, since two
same-month synthetic expiries can share a week tag) — neither ever
skips.

## What's here (Step 47: minimize a deploy in progress instead of losing it)

Reported directly: the Deploy modal only offered Save (a config preset
— strategy-scoped, not the deployment name/mode/capital/notes actually
typed) or Cancel (which discards everything). Wanting to check
something elsewhere in the app mid-deploy meant either losing the
in-progress form or leaving the modal open and blocking the rest of
the UI behind its backdrop. Added a third option, modeled explicitly
on the two references given — Gmail's minimized compose window and
Jira/LinkedIn's minimized panes: a **Minimize** button that tucks the
whole form away into a bottom-right dock instead of discarding it,
restorable exactly as left.

- **What's captured**: deployment name, mode, capital, notes, and the
  config in whichever mode it was left in — the simple form's current
  field values, or the raw JSON text verbatim if Advanced was open
  (best-effort parsed too, so the hidden simple fields underneath stay
  roughly in sync for a later toggle back, same as normal Advanced
  use).
- **A stack, not a single slot**: minimizing while deploying a
  different strategy — or the same one again — queues up another
  independent chip rather than overwriting the first, matching how
  Jira lets several minimized issue panes sit side by side. Each chip
  restores or discards on its own.
- **Where it lives**: the dock (`#minimizedDock`) sits as a top-level
  sibling of `.app-shell`, same placement as the alert toast stack
  (Step 42) — outside every router view container, so it survives
  navigating anywhere else in the app. In-memory only, no localStorage
  behind it (unlike the dark-mode toggle) — a full page reload still
  clears it, matching Gmail's own minimized-compose behavior, not a
  durable draft-saving feature.
- **Cancel is unchanged** — still fully discards, no minimize entry
  created. Minimize is strictly additive, a third option next to the
  two that already existed.

**Verified** against a real server + real Postgres + a real browser:
minimized a partially-filled simple-form draft, confirmed the modal
closes without submitting and a chip appears in the dock; navigated to
a different view and confirmed the chip survives; restored it and
confirmed name/capital/notes come back exactly; separately minimized
and restored an Advanced/raw-JSON draft, confirming the exact JSON
text and the checked toggle state both round-trip; stacked two
independent drafts at once and confirmed discarding one via its own
chip leaves the other untouched; and re-confirmed Cancel still fully
clears with no draft left behind. Re-ran the Step 38 dark-mode suite
afterward since this touches shared modal/dock CSS; still passes.

## What's here (Step 48: P&L by Exit Reason on Detail, Max Drawdown on Compare)

Requested as two of three insight upgrades to the Detail and Compare
pages (the third, a P&L calendar heatmap, is Step 49).

**P&L by Exit Reason** (Detail's Stats tab): the existing Trigger
breakdown card counts every fill by reason — entries, adjustments, and
exits alike — which answers "how often did each trigger fire" but not
"how much did closing for each reason actually make or lose." New
section, same tab: for every CLOSED position, its `realized_pnl` is
attributed to the reason on whichever lot actually closed it (that
position's own last lot by `executed_at` — a multi-lot position's
earlier fills, an entry or an adjustment, may carry a different reason
than what finally closed it out), summed per reason and sorted by
total contribution. Deliberately a separate section from Trigger
breakdown rather than merged into it — mixing "count of every fill"
with "P&L of only the closing ones" in one table would leave entries
and adjustments showing a meaningless blank P&L column. Reuses the
existing `triggerBadgeHtml` classifier for the same stop/profit/adjust
coloring already used elsewhere, so one reason never reads two
different ways across the page.

**Max Drawdown column** (Compare): Compare's table showed which
deployment grew more, not which one made you sweat more to get there.
The drawdown math itself already existed, inline in Detail's Stats
tab — extracted to a shared `computeMaxDrawdown()` (api.js) so both
views compute "largest peak-to-trough decline" identically rather than
risking two definitions drifting apart, and Compare now runs it
against each selected deployment's own raw (rupee) snapshot series —
not the chart's already-%-indexed points, since drawdown is peak-
relative by definition and computing it a second time off an
already-indexed series would double that relativity for no reason.

**Verified** against a real server + real Postgres + a real browser,
with deterministic seeded data so the numbers could be checked by hand
rather than just "a table appeared": four closed positions across
three exit reasons with known P&L (`profit_target` ×2 summing to
+₹1,300, `stop_loss` −₹200, `force_exit` −₹50) — confirmed every row's
count/total/average and the sort order; two deployments' snapshot
series with a known drawdown (peak ₹110,000 → trough ₹90,000 = exactly
₹20,000 / 18.18%) and a monotonically-increasing one (zero drawdown) —
confirmed Compare's new column shows the right number for both, and
that the existing Trigger breakdown card is untouched. Re-ran the
Step 38 dark-mode suite afterward; still passes.

## What's here (Step 49: P&L calendar heatmap — per-deployment AND portfolio-wide)

The third of the three insight upgrades (Step 48 covered the other
two). A GitHub-contribution-graph-shaped calendar — but DIVERGING, not
just "more or less": a day can lose, not only contribute more or less,
so it needed a genuinely different color treatment than a plain
single-hue activity graph.

**Where it lives**: a new "Calendar" tab on Detail (one deployment's
own daily P&L — its own tab rather than folded into Stats, since a
full-year grid is a big enough visual element to earn one and Stats
was already dense), and a new collapsible "Daily P&L Calendar" section
on Reports (portfolio-wide, following the same collapse/persist
convention as By Strategy/By Deployment/Recent Periods). Both render
through one shared `renderPnlHeatmap()` (api.js) — same function, fed
either a deployment-scoped or portfolio-wide digest.

**Backend**: the portfolio-wide side reuses the existing
`GET /portfolio/pnl-digest` (just requested with a full year's worth
of days instead of the Recent-Periods table's 14). The per-deployment
side needed a genuinely new query — `list_pnl_digest_for_deployment`,
the exact same closes/fills FULL OUTER JOIN shape as the existing
`list_pnl_digest` (see that function's own docstring for the
realized-only reasoning), with a `deployment_id` filter added to both
CTEs — both `positions` and `position_lots` already carry
`deployment_id` directly, so this was a straight WHERE addition, not a
different join structure. New `GET /deployments/{id}/pnl-digest`
endpoint exposes it.

**Color**: run through the dataviz skill properly — this is a
DIVERGING ramp (polarity: loss vs. gain), not the categorical
`--chart-1..6` set Compare uses, so it follows the diverging rule (two
hues + a neutral midpoint, monotone lightness per arm) rather than the
six-hue CVD validator, which validates identity palettes and fails an
intensity ramp by design. Each arm is 4 steps, interpolated in OKLab
between this app's own `--loss-soft`/`--gain-soft` and `--loss`/`--gain`
tokens (script-generated, not eyeballed hex) — level 4 lands on exactly
the same hex as the existing semantic tokens, so "loss/gain" never
means two different reds or greens across the app. The weakest step is
15% of the way toward full, not identical to `-soft`, so it never
visually collides with the neutral "no activity" cell (`--panel`,
reused, no new token). Every cell gets a `--row-line` border regardless
of fill — the relief channel the skill requires whenever a fill's own
contrast against the surface runs low, which several of the weaker
ramp steps do by design (that's what makes the gradient read as a
gradient); the hover tooltip (exact date + P&L + closed/win-loss/fill
counts) is the second relief channel. Intensity buckets are
QUANTILE-based, computed separately for gains and losses within
whatever window is showing — a fixed rupee scale would leave a
₹10,000-capital deployment looking uniformly pale next to a
₹10,00,000 one.

**Verified** against a real server + real Postgres + a real browser,
with ~4 months of seeded, varied daily P&L (deterministic seed, so
reproducible): confirmed a specific day's hover tooltip matches a
direct DB query bucketed by IST calendar day EXACTLY (`₹-600 · 1
closed (0W/1L) · 2 fills`) — this caught a genuine test-methodology bug
of my own along the way (an ad-hoc verification query using the
Postgres session's UTC date cast instead of the app's own IST
bucketing, which briefly looked like an app bug before the mismatch
was traced to the query, not the code); confirmed month labels and the
diverging Loss/Gain legend render; confirmed Reports' section collapses,
expands, and persists collapsed state across a reload, same as its
sibling sections; and confirmed a brand-new deployment with zero closed
positions renders the correct empty-state message with an entirely
neutral grid (zero colored cells) rather than a false "no data" read
being silently miscolored. Re-ran the Step 38 dark-mode suite and the
Step 47 minimize-deploy suite afterward since this touches shared
index.html/api.js; both still pass.

## What's here (Step 50: Daily P&L Calendar on Dashboard + reorderable sections)

Two requests together: the Step 49 calendar heatmap on Dashboard too
(portfolio-wide, same as Reports already got), and — reported directly,
"I don't know if this is too much... can you give me customizability to
re order those things and remember my preferences" — the ability to
rearrange Dashboard's and Reports' own sections and have the layout
stick.

**Dashboard's Daily P&L Calendar**: a new section reusing the exact
same `renderPnlHeatmap()` and `GET /portfolio/pnl-digest` Reports'
version already uses (Step 49) — no new backend surface, just another
caller with a full-year `limit` instead of Recent Periods' 14.

**Reorderable sections**: every one of Dashboard's 5 sections (Overview,
Daily P&L Calendar, Open Positions, Recent Activity, Subscribed
Instruments) and Reports' 4 (By Strategy, By Deployment, Recent Periods,
Daily P&L Calendar) now carries ▲/▼ move buttons in its own header,
disabled at whichever edge it's already at. Deliberately buttons, not
drag-and-drop — fully usable with keyboard or touch, no drag library,
and the disabled-at-the-edges pattern was already established elsewhere
in this app (Reports' own Prev/Next period nav). Reports' period
tabs/nav/stat-cards are NOT reorderable — they're the controls that
decide what every section below them shows, not an independent widget
to relocate.

One shared `SectionOrder` helper (api.js) backs both views: order is a
plain list of section ids in localStorage, keyed per view
(`sectionOrder:dashboard` / `sectionOrder:reports`), applied by
reparenting each section's actual DOM element into its container in
the saved order (`appendChild` on an already-correctly-placed node is
a harmless no-op, so this runs on every `load()`, not just after an
explicit move). A saved order is reconciled against the view's current
default id list on every read, not trusted blindly: an id no longer
valid is dropped, and any CURRENT id missing from an old saved order —
exactly the situation for everyone who customized their layout before
this Calendar section existed — is appended in its default relative
position rather than silently hidden or jumping to the top. That
reconciliation is what let this ship without a migration: an existing
user's saved order (from a version that never had a Calendar section)
still works, and the new section just shows up in a sensible place the
first time they load either page.

On Reports specifically, the move buttons live inside the same header
that already toggles collapse on click (Step 41) — each button calls
`event.stopPropagation()` so nudging a section up/down never also
flips its collapse state.

**Verified** against a real server + real Postgres + a real browser:
confirmed Dashboard's Calendar section renders; confirmed the default
order on both pages; moved a section and confirmed the DOM order
actually changed, the correct edge button disabled itself, and the
change reached `localStorage`; confirmed both survive a full page
reload; and on Reports specifically, confirmed clicking a move button
does NOT also collapse the section while clicking the header itself
still does (no regression on Step 41's existing behavior). Re-ran the
Step 38 dark-mode suite afterward; still passes.

## What's here (Step 51: edit a deployment's config, after the fact — while paused)

Requested directly ("Can you add config edit option for all the
strategies?" / "post deployment"), and it reopens a boundary drawn
deliberately back when `DeploymentUpdate` was first built: config was
kept off that schema on purpose, because a RUNNING strategy instance
holds its own config-derived state in plain Python attributes, set
once in `on_start()` — overwriting the DB row underneath a live
instance would be invisible to it until something re-reads config from
scratch. That reasoning hasn't changed. What's new is recognizing that
"something" already exists and is already trustworthy: `pause()` fully
tears the runner down (`DeploymentManager.pause` → `runner.stop()`,
popped from the manager's own `runners` dict) and `resume()` fully
reconstructs it (`_start_runner` → a brand-new strategy instance whose
`on_start()` re-derives everything from the DB row, config included) —
the *exact same* reconstruction path a real process restart already
relies on for resume-safety (see every strategy's own "RESUME-SAFETY"
docstring section). So config is now editable, but through a gate, not
a free-for-all: **only while `paused`**. Not `active` (the running
instance wouldn't see it), not `stopped` (it can never be resumed, so
an edit there would never take effect — `resume()` has always refused
a stopped deployment, unchanged). strategy_name/mode/initial_capital
are still off-limits, still for their own original reasons (no clean
"reload" semantics the way config gets from pause/resume) — this
didn't relitigate those, only config.

**A real gap this surfaced and closed**: making a bad edited config a
genuinely reachable failure mode at resume time exposed that
`DeploymentManager.resume()` set status to `active` in the DB *before*
attempting to actually start the runner — if `on_start()` then raised
(exactly what a bad edited config does), the deployment was left
claiming `active` with no runner actually tracking it: a silently-
broken deployment, invisible until a server restart happened to self-
heal it via `load_active_on_startup`. `resume()` now wraps the start
attempt and rolls the status back to `paused` on failure before
re-raising — the same descriptive `ValueError` a bad config already
produced, still surfaces as a clean 409, but now leaves the deployment
in a state you can actually fix and retry from, not one that silently
lies about what's running. `Detail.resume()`/`Deployments.resume()`
(the two UI call sites) were themselves fire-and-forget before this —
neither had ever needed to check for a resume failure, because there
was no way to cause one — both now surface it as a real alert.

**"For all the strategies"**: the actual editing UI is the exact same
generic, schema-agnostic form the Deploy modal already uses — per-key
widgets (dropdown for known enums, boolean toggle, comma-separated
array, number, HH:MM time picker, plain text fallback) built straight
from whatever the deployment's own config actually contains, plus the
same "Advanced (edit raw JSON)" escape hatch for anything the simple
form doesn't have a widget for. Extracted the field-building logic
(`configFieldHtml`/`configFieldsContainerHtml`/`readConfigFromFields`)
out of Catalog and into api.js as pure, reusable functions once a
second, independent consumer (Detail's new Edit Config modal) needed
the exact same per-key type inference — the stateful pieces around it
(which container/textarea ids, each modal's own `_configBase`) stay
separate per caller on purpose, only the actual "what widget for this
key" logic is shared, so this works identically for every registered
strategy without a strategy-specific line of code anywhere.

**Verified** against a real server + real Postgres + a real browser —
both the API contract directly and the full UI flow: editing config
while `active` → 409; while `stopped` → 409; while `paused` → succeeds,
and the edit is CONFIRMED to actually take effect on resume, not just
sit in the DB (round-tripped `entry_time` through pause → edit →
resume → re-fetch); the resume-failure rollback specifically — edited
`instrument_tokens` to `[]` (a real strategy validation rejects this),
confirmed resume fails with the strategy's own message, confirmed the
deployment lands back on `paused` (not stuck `active`), then confirmed
a normal resume succeeds again once the config's fixed. Same sequence
repeated through the actual browser UI end to end: no Edit Config
button while active (explanatory note instead), button appears once
paused, modal pre-fills the deployment's real current values, saving
updates the read-only Config tab, and the bad-config resume path shows
a real `alert()` with the deployment visibly staying "paused" in the
header. Re-ran the Step 38 dark-mode suite and the Step 47 minimize-
deploy suite afterward (the latter specifically because it depends on
the exact config-form code that got extracted here); both still pass.

## What's here (Step 52: calendar_btst never skips expiry day — shifts the whole spread instead)

Prompted by a direct question about `calendar_btst`'s `allow_expiry_day_entry`
config flag ("how will I enter [on BTST]?" plus a request to confirm the
strategy really is SHORT THIS_WEEK ATM straddle / LONG NEXT_WEEK ATM
straddle — it is). Tracing through what the flag actually did surfaced
a real correctness gap, not just an awkward name: with the flag left
`False` (its default), the strategy *skipped entry entirely* on
THIS_WEEK's own expiry day — the exact same skip-based guard Step 46
already replaced for the whole `intraday_dtt_*` family, for the same
reason (a skip silently drops a trading day, and a paper account with
one deployment isn't diversified enough to shrug that off). Flipping
the flag to `True` avoided the skip, but did so by selling a short
leg that expires *the same day it's entered* — which doesn't just look
odd, it actively breaks: `_exit_all()` prices each leg from
`runner.dispatcher.last_prices`, falling back to entry price only if
that lookup is falsy/`None`. Kite stops streaming ticks for an expired
contract rather than sending a settlement price, so `last_prices`
doesn't go `None`, it goes *stale* — frozen at whatever the last tick
was before expiry — so the fallback never fires and next-day's BTST
exit would silently price that leg off a dead, pre-expiry tick. No
crash, no error, just a quietly wrong P&L. Not something to leave
opt-in and hope nobody flips it.

**Why this isn't just Step 46's fix copy-pasted**: `intraday_dtt_*` is
a single-expiry strategy — "switch to next week" means moving one
resolved expiry forward by one step, done. `calendar_btst` is a
*spread*: THIS_WEEK short + NEXT_WEEK long, always exactly one expiry-
step apart — that one-week gap is the entire point, it's what the
position is harvesting (differential theta decay between the two
legs). Shifting only the short leg forward (mirroring Step 46 literally)
would collapse both legs onto the *same* expiry — a degenerate combo,
not a calendar spread. The fix instead shifts **both legs together**:
short becomes whatever `OptionsResolver.resolve_expiry(underlying, 1)`
currently resolves to (i.e. what "NEXT_WEEK" means today), long becomes
`resolve_expiry(underlying, 2)` — the expiry after that. The spread
itself doesn't change shape, it just starts one week later than usual,
on the one day per cycle its usual THIS_WEEK/NEXT_WEEK pairing would
otherwise be degenerate (short expiring same-day) or require a skip.

**The change**: `allow_expiry_day_entry` (skip-or-not, opt-in to a
same-day-expiry short leg) is replaced by `switch_to_next_week_on_expiry`
(default `False`, matching the old default's caution) — never skips
either way, the flag only controls *how* the expiry-day case is
resolved. `False` keeps the old opt-in behavior available for parity
(sells the same-day-expiry short leg as-is; the docstring is explicit
this isn't recommended, given the stale-price gap above). `True` shifts
the whole spread one week later as described. `on_start()`/
`default_config`/the module docstring's RULES and CONFIG sections were
all updated to match; `_enter()`'s expiry-resolution block now always
proceeds to entry (three branches — switch+expiry-today,
no-switch+expiry-today, not-expiry-today — none of them `return` early),
and the fill metadata's `this_week_expiry`/`next_week_expiry` keys were
renamed to `short_expiry`/`long_expiry` (clearer once "short" and "this
week" can disagree, on a switched entry) with a new
`trigger_values.switched_to_next_week` flag recording which path fired.
Also fixed a stale docstring reference to the old "15:30 close" left
over from before NSE's new 3:40 PM close (see the CAS discussion above)
— now just "the close."

**Verified** live against a real dispatcher/runner/resolver/Postgres
pipeline with a synthetic three-expiry NFO options chain, forcing
"today" to be THIS_WEEK's own expiry day via a real tick timestamp, for
three scenarios: `switch=False` on expiry day → enters anyway (no skip),
`short_expiry`/`long_expiry` both land on the two original unshifted
expiries, `switched_to_next_week=False`; `switch=True` on the same
expiry day → enters with `short_expiry`/`long_expiry` shifted to the
next two expiries out, `switched_to_next_week=True`; and a regression
check with `switch=True` on an ordinary non-expiry day → completely
unaffected, `short_expiry`/`long_expiry` land on the normal THIS_WEEK/
NEXT_WEEK pair, `switched_to_next_week=False`. All three confirmed via
the actual open positions (4 legs: 2 short + 2 long) and entry-fill
metadata, not just log output.

## What's here (Step 53: strategy state survives a real restart, not just a resume)

A follow-up question about `pivot_supertrend`'s SuperTrend/pivot seeding
("is it night, do I need to give seed_candles again?") led to a bigger
one: what actually happens to that live-learned state — the SuperTrend
line, the pivots recomputed from yesterday's real OHLC — when the
server itself gets stopped and restarted, not just paused/resumed?
Answer at the time: nothing good. `self.st`/`self.pivots` only ever
lived in that one Python process's memory; every `on_start()` re-read
whatever was typed into `prev_day_ohlc`/`seed_candles`/`supertrend_seed`
at initial deploy time, so a restart silently reverted days (or weeks)
of live-learned state back to a stale, one-time seed — a real gap, not
hypothetical, given "I am bound to stop and run server" (redeploys,
routine maintenance) is exactly the normal operating condition here.

**Design, per direct instruction ("this can be a simple metadata field
in current table or a new table, you decide, but give me a plan
first")**: a new table, `deployment_state` — one row per deployment,
JSONB, wholesale-overwritten on every dump (a resumable snapshot, not
an event log). Deliberately NOT a field on `deployments.config` — that
column is user-owned (the Step 51 config-edit feature lets you hand-
edit it while paused), and mixing strategy-computed runtime state into
it risks your edit clobbering the strategy's state or vice versa.
Different owner, different write cadence, different lifecycle — same
reasoning `positions`/`deployment_events`/`deployment_snapshots` already
get their own tables instead of everything piling onto `deployments`.

**The mechanism is generic, not pivot_supertrend-specific**: one new
optional hook on `StrategyBase`, `get_persistable_state() -> Optional[dict]`
(default `None` — every existing strategy is entirely unaffected unless
it opts in), and `runner.load_state()` to read it back. The trigger
point turned out to be free: `DeploymentRunner.stop()` already ran
`strategy.on_stop()` on pause, on stop, AND on a graceful full-server
shutdown (`DeploymentManager.shutdown_all()`, wired to FastAPI's own
shutdown event) — so "dump state right after `on_stop()`" covers a
deliberate restart with zero new scheduling logic. It does NOT fire on
an ungraceful kill (SIGKILL/OOM/crash) — accepted: that just means one
restart resumes from the last graceful stop instead of that exact
instant, same "up to one step of imprecision" tradeoff this codebase
already accepts elsewhere (e.g. the last, still-forming candle at any
real day boundary).

**Wired into all three pivot/SuperTrend strategies**
(`pivot_supertrend`, `pivot_supertrend_options`,
`pivot_supertrend_options_inverse` — the three that actually carry
indicator state beyond what positions already capture).
`SuperTrendState` gained `snapshot()`/`from_snapshot()` to round-trip
its FULL internals exactly, including the raw ATR TR-buffer — needed
for `atr_smoothing="sma"` (which reads a whole rolling window on every
update, not just during warmup) to keep producing a trend immediately
post-restore rather than silently re-entering warmup. Each strategy's
`on_start()` now tries `await runner.load_state()` FIRST, before
applying any config seed, and only falls through to
`prev_day_ohlc`/`seed_candles`/`supertrend_seed` on a genuine first-ever
start (nothing persisted yet). A malformed/incompatible persisted blob
(future format, corrupted) is caught and logged, falling through to the
config-seed path rather than crashing `on_start()`.

**Verified** live, end to end, with zero config seed anywhere in the
run (the strongest possible proof this isn't just re-deriving from a
seed that happened to still be present): deployed `pivot_supertrend`
cold, warmed SuperTrend (`atr_smoothing="wilder"`) and triggered a real
day-rollover purely from live ticks, paused it (dumping state),
confirmed the `deployment_state` row directly in Postgres (trend, ATR,
pivots, `today`, `prev_day_ohlc` all present and correct). Then
simulated a genuine process restart — a brand-new `app.main` import,
fresh `CandleAggregator`/`SuperTrendState` instances, same Postgres —
resumed, and fed just 3 post-restart ticks: an entry fired, confirmed
via the open position AND the fill's own `trigger_values`
(`broken_level_key="S1"`, `trend="down"`). This is only possible if
SuperTrend/pivots genuinely survived the restart — a real cold start
needs 7 closed candles for ATR warmup alone, let alone any pivots,
so 3 ticks producing a trade is proof by construction, not just a log
line saying so. Re-ran the existing `pivot_supertrend`/
`pivot_supertrend_options`/`pivot_supertrend_options_inverse` live and
retrofit-verification suites afterward (all of which exercise the
now-changed `on_start()` path) plus the real-process restart-survival
check (kill the actual server, restart it, confirm the deployment and
its open position survive) — all still pass.

## What's here (Step 54: a real bug — deploying after hours could place a trade off a stale tick — plus per-deployment Delete)

**The bug**, reported directly ("if catch up late entry is true and the
strategy is created after market hours... trades are placed for some
reason," especially noticed on `intraday_dtt_adjusted`/`_advanced`).
Chased it with a live reproduction test rather than guessing: fed a
tick timestamped BEFORE the deployment's own creation, mimicking Kite's
own well-known behavior of delivering an immediate snapshot the moment
you subscribe to an instrument — carrying the LAST TRADE's own time,
not "now." Confirmed the pre-fix code really did place a trade off it.
The mechanism: every strategy here trusts `exchange_timestamp` as "now"
for entry/exit/day-boundary decisions, with nothing checking whether
that's actually current. A snapshot near a prior close (say 15:25) can
easily land inside `entry_time..force_exit_time`, and with
`catch_up_late_entry=True` (the default), that's enough to fire a real
entry using an hours-old price the instant you deploy —
`intraday_dtt_adjusted`/`_advanced` noticed it because their
`force_exit_time` is naturally close to the real close, so a stale
near-close snapshot often still sails under that bar.
`calendar_btst` has it worse: no `force_exit_time`-style upper bound at
all, so ANY stale tick past `entry_time` enters, no matter how late —
confirmed live too.

**The fix** deliberately avoids comparing against real wall-clock "now"
— this codebase has never needed a timezone conversion anywhere
(`exchange_timestamp` is naive IST throughout, matched directly against
naive `entry_time`/`force_exit_time` config values), and bolting one on
just for this would be one more place to get subtly wrong. It only
needs one fact, and it's airtight: a tick genuinely reflecting live
trading can never claim to be from before the deployment existed. So
`DeploymentRunner` now rejects any tick whose `exchange_timestamp` is
earlier than the deployment's own `created_at` — once, at the runner
level, before it ever reaches any strategy's `on_tick`. This protects
every strategy uniformly (not just the ones with `catch_up_late_entry`)
with one change in one place, and it's structurally incapable of a
false positive: `created_at` never changes after a deployment's
original creation, so a deployment that's been running for weeks is
completely unaffected — this only ever matters for the first few ticks
after a brand-new deployment.

**Verified** live: the exact reproduction (stale tick dated before
creation, inside `entry_time..force_exit_time`) placed a real trade
pre-fix, confirmed blocked post-fix, for both `intraday_dtt_adjusted`
and `calendar_btst`. A genuinely fresh, properly-dated tick after
creation still catches up normally — the legitimate "deployed mid-day,
missed entry_time, catch up now" case this flag exists for is
untouched. Also independently confirmed against the Step 46/52/53 tests
using dynamically-computed dates (immune to this guard by construction)
and against a deliberately-backdated `created_at` (forcing a fresh
runner via pause/resume) — all pass, and pivots/day-rollover compute
correctly once tick ordering is legitimate. A batch of older scratchpad
tests with dates hardcoded at authoring time now correctly get flagged
as "stale" by this same guard, since real time has since passed them —
expected, not a regression (confirmed by backdating one and re-running
it clean).

**Delete deployment**, requested directly ("I also need the delete
strategy option as well along with pause and stop"). Reuses
`queries.delete_deployment` — previously only called internally to roll
back a deployment whose runner failed to start right after creation,
now also a genuine `POST /deployments/{id}/delete` endpoint. Restricted
to `stopped` deployments only: `stop()` already tears its runner down
and either closes its open position (`force_close=true`) or refuses to
run with one still open, so a stopped deployment never has a runner to
tear down or a position this endpoint would otherwise have to silently
decide what to do with. Deletes the deployment and everything recorded
under it — positions, lots, events, snapshots — via the same
`ON DELETE CASCADE` `clear_all_deployments` already relies on in bulk,
just for one row. Surfaced as a "Delete" button next to Pause/Stop on
both Detail and the Deployments list, appearing only once stopped, with
a `confirm()` naming the deployment and spelling out that its entire
history goes with it. **Verified** against the real API (blocked while
active, blocked while paused, succeeds once stopped, the row and its
GET both genuinely 404 afterward, a repeat delete cleanly 404s rather
than 500ing) and through the real browser UI (button visibility, the
confirm dialog's exact wording, navigating back to the list, the
deployment gone from both Detail and the table).

## What's here (Step 55: Step 54's own fix had a timezone bug — it was blocking every real tick, permanently)

Reported directly, minutes after Step 54 shipped: two real deployments
logging `"ignoring a tick timestamped before this deployment's own
creation"` for EVERY tick, forever — not just the first one. That's
worse than the original bug: it meant those deployments could never
trade again.

**Root cause**: Step 54's guard converted `created_at` to what it
assumed was "naive IST" via a hardcoded `+5:30` offset, reasoning that
Kite's `exchange_timestamp` is naive IST throughout this codebase — true
for every entry_time/force_exit_time comparison elsewhere, but not
because Kite guarantees it. Read the actual installed `kiteconnect`
library's source (`ticker.py`): it builds `exchange_timestamp` via
`datetime.fromtimestamp(unix_ts)` — **no timezone argument** — which
returns naive time in whatever the SERVER'S OWN SYSTEM TIMEZONE is, not
a portable "always IST" guarantee. This whole app has always implicitly
assumed the deployment server's system clock is set to IST (same as
every `entry_time="10:00"` config value already assumes) — a reasonable
assumption for a single-purpose NSE app, but Step 54's hardcoded
`+5:30` broke it: on a server whose system tz is actually UTC (the
sandbox this was built in, and very plausibly the reporting user's
deployment host too — common default for a cloud VPS), `created_at`
got pushed 5.5 hours further "into the future" than any real tick could
ever be, permanently, since `created_at` never changes.

**The fix**: derive `created_at`'s comparison value the exact same
way real ticks are built — `datetime.fromtimestamp(created_at.
timestamp())`, no hardcoded offset at all. This keeps both sides of
the comparison in whatever clock domain the server's system tz actually
is, matching real `exchange_timestamp` ticks on ANY server, rather than
assuming IST and silently comparing two different clocks. The
underlying guard's logic (reject a tick claiming to be from before the
deployment existed) is unchanged and still correct — only the
"what does 'before' mean in which clock" part was wrong.

**Verified live**, and specifically in a way Step 54's own tests never
exercised, which is exactly why this shipped undetected: every earlier
test constructed tick timestamps as hand-picked `datetime()` literals
(self-consistent with the hardcoded offset, but never actually routed
through Kite's real conversion). This time, ticks were built the
identical way the real `kiteconnect` library does —
`datetime.fromtimestamp(unix_epoch)` — and confirmed: with Step 54's
original code, on this UTC-system-tz sandbox, a live tick fed
immediately after deployment creation WAS wrongly rejected (exact same
symptom the user reported, exact same ~5.5h gap); with this fix, it's
correctly accepted, while a genuinely stale tick from a real hour
earlier is still correctly rejected. Re-ran Step 54's own test suite
plus the Step 53 state-persistence and Step 52 calendar_btst suites
afterward — all still pass.

## What's here (Step 56: pivot_supertrend's family had no floor against pre-market entries)

A direct follow-up question ("For the supertrend there is no any entry
time right?") turned into a genuine, confirmed third gap in this same
family of strategies. Correct: `pivot_supertrend`/
`pivot_supertrend_options`/`pivot_supertrend_options_inverse` have no
`entry_time` schedule at all — they watch continuously and react the
instant a technical signal fires, any time of day. That design predates
NSE actually disseminating LIVE pre-market ticks through the same feed
Kite uses for regular trading (the equity index's indicative price
during the 09:00-09:15 call auction, and a genuine F&O futures pre-open
session since December 2025) — when it was built, "any time of day"
implicitly meant "any time the market's actually open," because
pre-market data simply never reached this pipeline.

**Confirmed live, not just in theory**: for an established deployment
(pivots computed from yesterday's close, SuperTrend trend carried over
continuously — the normal state of any multi-day-running deployment),
fed a synthetic pre-open indicative-price dip (09:00 → 09:05 → 09:10,
all real minutes before the actual 09:15 open). The break was detected
in that pre-market window and the resulting entry EXECUTED right at the
09:15 boundary — `sell 41 NIFTY 50 @ 23900.0 (entry)` — priced off
auction-based price discovery, not real continuous trading. The other 5
strategies (`intraday_dtt_simple`/`_adjusted`/`_advanced`,
`calendar_btst`, `strangle_monthly_v2`) were unaffected: all require an
`entry_time` and default it to market-open-or-later.

**The fix**: a new `market_open_time` config (default `"09:15"`,
nullable to disable) across all three affected strategies, added as a
second bound alongside the existing `force_exit_time` — an
`after_open` check combined into fresh-entry DETECTION only (never
exits, never a pending entry already queued from a regular-session
candle). Deliberately configurable rather than hardcoded, consistent
with `force_exit_time`'s own pattern, rather than baking NSE's exact
open time into the code as a silent constant.

**Verified**: re-ran the exact pre-market reproduction — now correctly
produces zero entries, even feeding the boundary tick at exactly 09:15
(the gate reads the CANDLE's own timestamp, i.e. the actual market
activity being evaluated, not whichever later tick happened to close
it — so a pre-market-dated candle stays blocked even when the
CLOSING tick lands right at the open). Also confirmed regular-hours
entries (>= 09:15) still fire completely normally for all three
strategies — this only narrows the window, nothing else about the
signal logic changed.

## What's here (Step 57: a restart shouldn't be able to look like a fresh "late start")

Reported live, from an actual production symptom: 4 straddle
deployments, all sharing `catch_up_late_entry: false`, all sitting flat
well after their 10:00 `entry_time` with zero entries and no errors.
The question that found the real bug: *"if a tick comes at 10:00:01,
how is that not taking the trade? catch_up_late_entry is for deploying
late the FIRST time — from day two onward it shouldn't need that logic
at all."* Exactly right, and it's the same class of gap as Step 53,
just for `today`/`entered_today` instead of SuperTrend/pivots.

**The mechanism**: `self.today`/`entered_today` only ever lived in that
Python process's memory. Every restart — redeploy, pause/resume,
anything — starts a fresh instance with `self.today = None`. The FIRST
tick that instance ever sees decides "late start or not" purely by
checking `tick_time >= entry_time`, with no way to tell "this is
genuinely my first tick ever" apart from "I've run fine for weeks and
just happened to restart a second after entry_time." Both look
identical from a fresh instance's point of view. With
`catch_up_late_entry=false`, that misdiagnosis means silently skipping
the entire day, every time a restart lands anywhere at/after
entry_time — exactly what happened today, most likely from one of this
session's own redeploys landing after 10:00.

**Scope, confirmed precisely rather than assumed**: this pattern exists
in `intraday_dtt_simple`, `intraday_dtt_adjusted` (inherited
automatically by `intraday_dtt_advanced`, no separate change needed),
and `calendar_btst` — all three. `strangle_monthly_v2` was checked and
does NOT have this pattern at all (a plain `entry_time` gate, no
`catch_up_late_entry` concept) — already correct, nothing to fix there.
`pivot_supertrend`'s family has no daily-entry-limit concept either
(can re-enter any number of times a day) — already covered by Step 53's
persistence for the pieces that DO need to survive a restart
(pivots/SuperTrend), so nothing more needed there.

**The fix**: same `get_persistable_state()`/`load_state()` hooks from
Step 53, now also persisting `today`+`entered_today` in these three
strategies. Restored BEFORE each strategy's own existing DB-based
resume-safety reattachment (which already correctly sets
`entered_today=True` when a position is genuinely still open) — so the
persisted values only ever matter for the FLAT case, which the existing
reattachment logic has no way to reconstruct on its own (nothing in the
DB says "I decided to skip today" the way an open position says "I
already entered"). Once restored, a same-day restart takes neither of
`on_tick`'s two day-tracking branches (`self.today is None` / `day !=
self.today`) — meaning the late-start question is never even asked, and
a 10:00:01 tick just enters normally, exactly as if the restart had
never happened.

**Verified live**: reproduced the exact bug first — `catch_up_late_entry
=false`, established today (a tick before entry_time, then a graceful
stop), simulated restart (fresh `app.main` import, same Postgres),
first tick of the new instance landing at 10:30 — confirmed this
produced ZERO entries pre-fix (the bug, reproduced faithfully) and a
normal 2-leg (`intraday_dtt_adjusted`) / 4-leg (`calendar_btst`) entry
post-fix. Re-ran the Step 24/46/52 switch-to-next-week suites (dynamic
dates, unaffected by the unrelated stale-hardcoded-test-date issue from
Step 54/55) afterward — still pass.

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
own front door (see "Step 7" below), required, no default. It's still
the value you pass as `X-API-Key` for scripted access, and it's still
the session-cookie signing key — but as of Step 28 it's **no longer
the ongoing UI login password**: it's a one-time seed, used only on the
very first boot (when no user account exists yet) to create an initial
`admin` user with this as its starting password. Log in with it once,
then change it via Account → Profile in the UI (or `POST
/auth/change-password`) — see Step 28 below for the full multi-user
story. The service refuses to start without this value either way.

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
  {"symbol": "NIFTY 50", "instrument_token": 256265},
  {"symbol": "SENSEX", "instrument_token": 265}
]
```

Add more entries here as needed — every token in this file gets
subscribed with the same `tick_mode` when the dispatcher connects. The
`SENSEX` entry (step 12, `strangle_monthly_v2`) is needed so
`get_spot_price("SENSEX")` can hit the dispatcher's live tick cache
instead of falling back to a REST call every time — the `265` token
value is from general knowledge of Zerodha's published instrument list,
**not independently re-verified against a live dump in this
environment**; confirm it before relying on the cache path in
production (the REST fallback works either way if it's ever wrong).

## Usage

```bash
uvicorn app.main:app --reload --port 8000
```

(Run from inside `live_deploy/` — the app uses relative imports, so
`uvicorn app.main:app` resolves the package correctly; running it from
one directory up, e.g. the repo root, fails with `ModuleNotFoundError:
No module named 'app'`. Running `python app/main.py` directly fails
differently — `ImportError: attempted relative import with no known
parent package` — for the same underlying reason. If you want a
direct-execution alternative to the uvicorn CLI, use `python run.py`
instead: a tiny launcher at the `live_deploy/` root, outside the `app`
package, that works from any current directory. See **`RUN_GUIDE.md`**
for the full writeup of that bug plus three genuinely distinct ways to
run this service — local dev, background/production on a machine you
already have (`supervisord`), and containerized (Docker) — each with
exact commands, prerequisites, and how to confirm it's actually working,
not just that the process is up.)

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
| `GET /deployments?status=active` | List deployments, optionally filtered by status — each row now also carries `realized_pnl`/`unrealized_pnl` (Step 13; see below) |
| `GET /deployments/{id}` | Full deployment detail (config, cash, status, `realized_pnl`/`unrealized_pnl`) |
| `GET /deployments/{id}/positions?status=open` | Current positions, with live `current_price` + `unrealized_pnl` computed from the dispatcher's last-tick cache |
| `GET /deployments/{id}/trades?offset=&limit=` | Paginated fill history (every lot) — each lot now also carries `symbol` and the fill's full `metadata` dict (Step 13) |
| `GET /deployments/{id}/events?offset=&limit=` | Audit log: fills, pause/resume/stop, strategy errors |
| `GET /deployments/{id}/report` | Aggregate stats: realized P&L, win rate, avg win/loss, open/closed counts |
| `GET /deployments/{id}/snapshots?limit=1000` | Equity-curve points (Step 13) — see "Equity-curve snapshots" below |
| `POST /deployments/{id}/pause` | Halt trading, keep positions as-is, stop reacting to ticks |
| `POST /deployments/{id}/resume` | Resume a paused deployment |
| `POST /deployments/{id}/stop?force_close=false` | Terminal. Refuses if positions are open unless `force_close=true`, which flattens every open position at the dispatcher's last known tick price first |

**Cross-deployment aggregates** (Step 13) — the only two endpoints in
this API that are NOT scoped to one `{deployment_id}`, added specifically
so the UI's Dashboard doesn't have to fetch every deployment's own data
and merge it client-side:

| Method & path | What it does |
|---|---|
| `GET /positions?status=open` | Every position across every deployment, each annotated with `deployment_id`/`deployment_name`/`strategy_name` — same mark-to-market formula as the per-deployment positions endpoint, so the two can never silently disagree |
| `GET /trades/recent?limit=20` | Latest fills across every deployment, newest first, same annotation |

**`realized_pnl`/`unrealized_pnl` on deployment responses**: computed
server-side (two bulk queries for the LIST endpoint — every deployment's
realized total, and every open position across every deployment,
mark-to-market summed in Python — rather than one query per deployment)
so the Deployed Strategies list and the Dashboard can show "is this
deployment currently winning or losing" without a click-through. A
freshly created deployment is correctly `0.0`/`0.0` with no query at all.

### Equity-curve snapshots

`deployment_snapshots` existed as a table with working
`record_snapshot()`/`list_snapshots()` query functions since Step 2, but
nothing ever actually called `record_snapshot()` — the equity-curve stats
tab had no data to draw from. Step 13 closes that gap:
`DeploymentManager.snapshot_loop()` runs as a background `asyncio.Task`
for the lifetime of the process (started in `main.py`'s `startup()`,
cancelled cleanly in `shutdown()`), and every `snapshot_interval_seconds`
(default 300 — a chart's granularity, not a backtest engine's; overridable
per-`DeploymentManager` instance, mainly for tests) records one point per
currently-**active** deployment: cash, mark-to-market unrealized P&L
across its open positions (`open_positions_value`), `total_value = cash +
open_positions_value`, and cumulative realized P&L. A paused/stopped
deployment (no runner) is silently skipped, not an error; one
deployment's snapshot failing doesn't stop the rest of that round from
being recorded.

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
- **Never skips the contract's own expiry day** (Step 46 superseded the
  original skip-based `allow_expiry_day_entry` guard tested here — see
  that Step's own entry for the rename/behavior-change rationale): a
  synthetic chain with TWO listed expiries, one forced to today's real
  date (deliberately not tied to any particular weekday, so this proves
  the check is date-based rather than a hardcoded "skip Thursdays") and
  one a week out — with `switch_to_next_week_on_expiry` at its default
  `false`, confirmed a real straddle still opens, on today's own
  expiring contract (checked against the actual `expiry` recorded in
  trade metadata, not just symbol text, since a same-month NEXT_WEEK
  contract can share a synthetic symbol's week tag); confirmed
  `switch_to_next_week_on_expiry: true` on the identical expiry-today
  setup instead opens on the NEXT_WEEK contract (again via metadata,
  plus the `switched_to_next_week` trigger value), for both
  `intraday_dtt_simple` and `intraday_dtt_adjusted` (proving the shared
  `resolve_atm_straddle_legs` reuse still works after the change); and,
  back on a normal THIS_WEEK chain (expiring later in the week, not
  today), confirmed a normal day's entry is completely unaffected.
- Full existing regression suite (auth, DB layer, deployment lifecycle,
  dynamic subscription, onboarding/UI, pivot_supertrend math + live,
  pivot_supertrend_options live, pivot_supertrend_options_inverse live,
  options resolver) re-run — zero regressions.

Not yet run against a real Kite tick stream or real option premiums —
same caveat as every other strategy in this README.

**`pivot_supertrend_options_inverse`** was verified with candle
sequences checked against the *real* `SuperTrendState` class directly
(via a scratch script) before being used in the integration test — so
the exact candle each flip fires on, and that the following flat candles
don't accidentally re-flip, is known with certainty rather than assumed:

- **Direction correctness, both ways**: a flip to red bought the ATM PE;
  a later flip to green (after the first position had already exited)
  bought the ATM CE — confirmed on the actual DB position rows
  (`side: "long"`, correct symbol, correct qty), not inferred from logs.
- **`hold_candles` timing, both values**: `hold_candles: 1` exits
  exactly 1 candle after entry; `hold_candles: 2` exits exactly one
  candle later than that — checked candle-by-candle (still open after
  N-1 candles, closed after N).
- **A flip while already holding is genuinely missed**: forced a
  flip-back-up in the middle of a `hold_candles: 2` PE hold — confirmed
  no second (CE) position ever opened, the original PE closed
  exactly on its own original schedule, and — the sharper check —
  becoming flat afterward did *not* auto-open a new position just
  because the "current" trend now happened to differ from entry; only a
  genuinely fresh flip re-arms it.
- **`force_exit_time` safety net**: with `hold_candles` deliberately set
  high enough that it wouldn't naturally expire until well past 15:00,
  confirmed the force-exit fires and reports `reason: "force_exit"`
  instead.
- **Resume-safety caught a real off-by-one bug before it shipped**: the
  first version of the hold-counter reconciliation formula
  under-counted by exactly one candle (it didn't count the entry
  candle's own close, which always counts as "1 held" immediately
  during uninterrupted operation) — a resumed deployment would have held
  every position one candle longer than intended. The integration test
  caught this by comparing the reconstructed count against the same
  scenario's non-resumed trace; fixed, then re-verified: a resume mid-hold
  reconstructs the exact right count and hands off seamlessly to normal
  per-candle counting, and a resume after a long pause (hold period
  already well elapsed) exits immediately rather than waiting further.
- Full existing regression suite (auth, DB layer, deployment lifecycle,
  dynamic subscription, onboarding/UI, pivot_supertrend math + live,
  pivot_supertrend_options live, intraday_dtt_simple live, options
  resolver) re-run — zero regressions.

Not yet run against a real Kite tick stream or real option premiums —
same caveat as every other strategy in this README.

**`intraday_dtt_adjusted`** — every numeric scenario was worked out BY
HAND against the spec's own worked example (Call 600, Put 600, entry
spot 35000) before being turned into ticks, using a synthetic chain with
an independent, controllable per-strike premium model (so
`get_leg_by_premium`'s strike search could be pointed at an EXACT target
premium deterministically, not approximated):

- **Both adjustment triggers, matching the spec's own numbers exactly**:
  Call 600→800/Put 600→400 → adjustment #1 (target 200, resolved to an
  exact-match strike); Call→900/combined Put→450 → adjustment #2
  (target 225, a genuinely DIFFERENT strike than adjustment #1 —
  confirming `exclude_strikes` actually excludes, not just "closest
  match, coincidentally different").
- **Hard cap**: an extreme, obviously-would-trigger condition right
  after both adjustments does NOT add a 5th leg.
- **Reversal-unwind with an EXPLICIT tie**: two of three Put legs
  deliberately priced identically at the moment of trigger — confirmed
  the EARLIER-opened one closes, not the later one, matching the
  documented tiebreak (not left to fall out of whichever order happened
  to iterate first). A second, non-tied reversal then closes a further
  leg, and correctly stops checking once back down to 1 leg per side.
- **Profit target firing WHILE an adjustment leg is open**: a full
  3-leg flatten with `reason=profit_target_total`, confirmed to
  correctly outrank a reversal condition that was ALSO true on the same
  tick (priority order actually enforced, not just documented).
- **Break-even with multiple legs open**: isolated so no other check's
  condition was also true that tick — the underlying trading past the
  upper break-even level is the ONLY thing that explains the resulting
  full flatten.
- **force_exit_time** still closes everything regardless of how many
  adjustment legs are open.
- **The expiry-day exclusion**, reused from `intraday_dtt_simple` —
  confirmed the shared function is actually wired into this strategy,
  not just that it exists.
- **Resume-safety, the main event**: restarted mid-3-leg-position with a
  nonzero (deliberately profitable, +375) `realized_pnl_today` from an
  earlier same-day reversal-unwind close. Confirmed (a) all 3 remaining
  legs reattach with correct entry prices; (b) `adjustments_used`
  reconstructs as 2, not 0 — an extreme post-restart trigger does NOT
  add a 5th leg; (c) `realized_pnl_today` reconstructs as +375, not
  reset to 0 — proved with a DISCRIMINATING price move whose own
  unrealized P&L (50) falls well short of the 120-point target on its
  own, so the resulting flatten is only explainable if the +375 was
  correctly carried forward — which also transitively proves
  `combined_entry_premium`/`entry_spot`/break-even were reconstructed
  too, since the profit-target check is gated on them being set at all.
- Two new resolver-level tests added to `test_options_resolver.py`
  alongside the rest of `get_leg_by_premium`'s coverage:
  `exclude_strikes` actually removes a strike from the search (falls
  back to the runner-up, verified against an independent brute-force
  re-scan), and an exact-tie target premium breaks toward the lower
  strike, matching the tiebreak now spelled out in that method's own
  docstring (previously true but undocumented — "whatever the sort
  happened to produce").
- **`adjustment_trigger_ratio` validated strictly between 0 and 1**:
  the adjustment and reversal-unwind triggers are only guaranteed
  mutually exclusive on the same tick when the ratio is under 1.0 — the
  tick-handling code relies on that to skip re-checking reversal-unwind
  right after acting on an adjustment trigger. Confirmed 1.0, 1.5, 0.0,
  and -0.5 are all rejected with HTTP 400 at deployment creation (not
  discovered later as a silently-broken assumption), and that a valid
  interior ratio (0.75) still deploys normally — inherited by
  `intraday_dtt_advanced` with no separate validation needed there.
- Full existing regression suite (every strategy above, plus
  `intraday_dtt_simple` re-run after its own refactor to share
  `resolve_atm_straddle_legs()` with this strategy, and
  `intraday_dtt_advanced` re-run after this validation was added to the
  base class) re-run — zero regressions.

Not yet run against a real Kite tick stream or real option premiums —
same caveat as every other strategy in this README.

**`intraday_dtt_advanced`** — first, refactoring `intraday_dtt_adjusted`
into a subclassable base (the `breakeven_multiplier` seam and the
`_handle_adjustment_trigger` extension point) was verified to change
NOTHING about that strategy's own behavior: its full existing test suite
was re-run immediately after the refactor, before writing a single line
of the new file, and passed unchanged. Then, for `intraday_dtt_advanced`
itself:

- **Below the concurrent cap behaves identically to `intraday_dtt_adjusted`**:
  the same two adjustment triggers, same numbers, same plain
  `reason=adjustment` fills — confirming the subclass doesn't
  accidentally change anything for the "not yet at cap" case.
- **A trigger AT the concurrent cap rolls**: exactly one leg closed
  (the correct cheapest one) and exactly one new leg opened in the same
  step, sized off the bigger side's premium *at the moment of that
  specific roll* (1150 → target 287.5) rather than reusing a stale value
  from the trigger before — checked by asserting the new leg's actual
  entry price, not just that "something opened". Leg count on that side
  confirmed unchanged (3 before, 3 after) and the two fills' `reason`s
  (`roll_close`/`roll_open`) confirmed distinct from ordinary
  `adjustment`/`reversal_unwind`.
- **A second roll where the ORIGINAL leg happens to be cheapest**:
  confirmed it gets rolled away exactly like any adjustment-role leg —
  the "1 original + N adjustments" framing is a leg-count ceiling, not
  a protected role, and the test proves the code doesn't quietly
  special-case the original.
- **`breakeven_multiplier` isolation, three separate checks**: the
  adjustment trigger still fires at exactly the unchanged 0.5 ratio; the
  profit target still fires at exactly `decay_pct × combined_entry_premium`
  (120, boundary-exact — NOT 132, which is what the multiplier leaking
  into the wrong formula would incorrectly require); and the break-even
  band itself is confirmed genuinely wider — a price (36250) that would
  have breached the default 1.0× bound stays open under 1.1×, and only
  flattens once a further price (36350) breaches the widened bound too.
- **Resume-safety mid-rolls**: restarted with 1 CE + 3 PE legs open
  (after one prior roll), confirmed all 4 reattach correctly, then fed a
  further extreme trigger post-restart and confirmed it correctly
  ROLLS — leg count stays at 3, not 4 (would mean "cap not recognized,
  treated as a plain add") and not 3-unchanged-with-no-new-leg (would
  mean "rejected, lifetime-cap logic leaked in from the base class") —
  proving the concurrent count really is being read live off
  `runner.open_positions`-derived state with no separate counter to
  reconstruct, as the design claims.
- Full existing regression suite (every strategy in this README,
  `intraday_dtt_adjusted` included) re-run — zero regressions.

Not yet run against a real Kite tick stream or real option premiums —
same caveat as every other strategy in this README.

**`strangle_monthly_v2`** — a synthetic NFO chain spanning TWO monthly
expiries (so the day-15/16 rotation rule has something real to switch
between) plus a small synthetic BFO chain, driven through the real API/
runner/resolver/Postgres pipeline exactly like every other strategy
above:

- **Entry sizing matches the spec's own worked example exactly**:
  capital=120000, NIFTY lot_size=50 → resolved target premium 36.0,
  confirmed against the actual filled price, not just the formula in
  isolation.
- **Every fill's full Section-12 metadata schema**, spot-checked
  directly against `position_lots` (the read API deliberately doesn't
  expose `metadata`, so this was queried straight from Postgres): both
  entry fills carry `trigger`, `action`, `leg`, `strike`, `cycle_id`,
  `contract_expiry`, `seq`, `trigger_values` (target premium, qty
  multiplier, capital reference, day-of-month, rotation selector — all
  checked against hand-computed expected values) and `target_basis`;
  `resulting_state` confirmed to reflect the snapshot AT THE MOMENT of
  each specific fill (the CE fill's own snapshot correctly shows PE not
  yet open, since CE is sold first).
- **Rotation, both sides of day 15/16, and the contract LOCK**: a fresh
  deploy on day 16 resolves NEXT_MONTH directly; a day-10 entry (THIS_MONTH)
  held open THROUGH day 20 stayed in its original contract — crossing
  day 16 while a position is open does not switch it; a checkpoint
  firing on day 20 then flattened and re-entered SAME TICK, independently
  re-applying rotation against day 20 → landed in NEXT_MONTH even though
  the position it just closed was THIS_MONTH's contract, with `cycle_id`
  incremented.
- **Section 5 (continuous 50%), both the 1-leg and 2-leg sum cases,
  pre-convergence**: a 1-leg trigger REPLACED the single leg in place
  (net count unchanged); after a genuine Section-6 accumulation grew
  that side to 2 legs, a further Section-5 trigger correctly summed
  both legs, replaced only the CHEAPEST of the two, and left the side
  at 2 legs — proving the sum-based generalization, not just "works for
  1 leg, assumed to work for 2."
- **Section 6 (daily 80% check, 15:13)**: a first breach GREW the side
  1→2 with the original/protected leg left untouched; a second breach
  REPLACED the (only) extra rather than growing to 3; and a dedicated
  check confirmed the PROTECTED-LEG guarantee directly — even when the
  side's longest-held leg was made deliberately the CHEAPEST leg on
  that side (which would make it the obvious pick under a naive
  "replace the cheapest" rule), Section 6 never selected it, only ever
  replacing among the extras, via the explicit `seq` stamp rather than
  list position.
- **All three `convergence_mode` values**, each built off a genuine
  Section-5 roll landing one side's leg on the OTHER side's own strike
  (not an artificial same-strike entry, since this strategy's entry
  itself never converges by construction): `fixed_stop` confirmed to
  hold its stop level fixed at the exact snapshot moment (a combined
  premium just below the stop left the position open, one just above
  it flattened); `trailing_stop` confirmed to trail the stop DOWN as
  premium decayed favorably and then correctly fire against the
  TRAILED level on a later rise that stayed well under the ORIGINAL
  snapshot-based stop; `active_management` confirmed to genuinely call
  `IntradayDTTAdjustedStrategy`'s own methods — the resulting leg's
  entry price matched THAT strategy's own 25%-of-bigger sizing formula
  exactly (50.0), not this strategy's 80-95% band (which would have
  produced 175) — and Section 6 (EOD) was confirmed to keep running,
  UNCHANGED, post-convergence even in this mode, correctly REPLACING
  (not growing) the delegated leg using this strategy's own band/
  protected-leg rule. A real bug was caught and fixed here during
  testing: `IntradayDTTAdjustedStrategy._handle_adjustment_trigger`'s
  own body calls `self._adjust(...)` internally, which only resolves
  correctly if `_adjust` is bound onto this instance too (not just the
  outermost call) — fixed by binding all three borrowed methods as
  genuine instance attributes via `.__get__(self)` in `on_start`,
  documented in the module docstring's "ACTIVE-MANAGEMENT DELEGATION".
- **Hedging, isolated first, then combined with a checkpoint**: entry
  confirmed 2 shorts + 2 protective longs at the correct flat NIFTY
  premium; a roll's full REVERSED fill sequence was verified directly
  against the actual ordered fills (close old short → close old
  protective → open new protective → open new short), with the new
  hedge confirmed at the SAME 2000-point distance from the NEW short
  strike; a checkpoint firing shortly after correctly flattened all 4
  legs (both shorts AND both hedges) and re-entered fresh with hedging
  still enabled.
- **SENSEX/BFO wiring** — synthetic chain only, explicitly NOT the
  real-Kite-BFO-data verification the spec itself asks for (impossible
  in this sandboxed environment, flagged rather than silently assumed):
  confirmed the resolver is constructed with `exchange="BFO"`, the spot
  price correctly routes through `INDEX_SPOT_SYMBOL` to the SENSEX
  token, `lot_size` is dynamically derived from the synthetic
  instrument master (20) rather than hardcoded, and a strangle enters
  correctly end-to-end against the synthetic BFO chain.
- **Resume-safety mid-position**: restarted with 1 original CE + 1
  adjustment PE leg open, after an earlier same-cycle Section-5 roll
  that realized +300. Confirmed both legs reattach with correct
  symbols/entry prices, and — the discriminating check — a post-restart
  price move whose own unrealized P&L (2325) falls STRICTLY SHORT of
  the checkpoint target (`checkpoint_pct=0.02 * initial_capital = 2400`)
  on its own still correctly fired the checkpoint, because
  `cycle_realized_pnl` was correctly reconstructed as the carried-
  forward +300 (2325+300=2625 ≥ 2400) — a reset-to-0 bug would have left
  the position open. `cycle_id` confirmed reconstructed as 1 (unchanged
  since no fresh entry has happened since restart).
- **Fix 1 (post-convergence freeze), the exact worked example**:
  converged at a combined premium of exactly 600 (stop_level = 660),
  then fed a genuine 80%-gap breach (PE/CE = 0.575) directly at
  `eod_check_time` — confirmed NO leg was added; fed a genuine 50%
  trigger (PE/CE = 0.375) at a separate tick — confirmed NO roll
  happened either, both under `fixed_stop`. Only then did a further move
  to a combined premium of 661 correctly fire the stop, flattening
  EXACTLY the 2 legs that had been open since convergence — never more,
  proving nothing was silently added during the frozen period.
- **Fix 2 (`initial_capital`-only sizing/checkpoint)**: a checkpoint-
  triggered re-entry's quantity and premium target confirmed IDENTICAL
  to the original entry (no more `qty_multiplier` drift from the first
  cycle's own +1750 profit); a SECOND checkpoint, fired on a cycle with
  deliberately different `cycle_realized_pnl`/cash history than the
  first, logged the EXACT SAME `checkpoint_target` (600) both times —
  discriminating because `capital_now` (still logged, informational
  only) demonstrably DID move between the two.
- **Config validation** at deployment-creation time: `instrument`
  membership, `adjustment_trigger_ratio` strictly between 0 and 1,
  `adjustment_band_min < adjustment_band_max`, `eod_gap_floor` strictly
  between 0 and 1, `convergence_mode` membership, and `max_adjustments
  >= 1` all rejected with HTTP 400; the default config still deploys
  normally.
- Full existing regression suite (every strategy above) re-run after
  the `DeploymentRunner.initial_capital` addition, the `tokens.json`
  SENSEX-token addition, and again after both fixes above — zero
  regressions each time.

Two things flagged explicitly rather than silently assumed, per the
spec's own anticipation of this limitation: (1) SENSEX/BANKEX support
depends on `kite.instruments("BFO")` actually returning the expected
row shape and the underlying Kite account holding BSE F&O market-data
permissions — both are **external facts this sandboxed environment has
no way to verify**, confirm independently before production use;
(2) fills placed by the `active_management` delegation path carry
`intraday_dtt_adjusted`'s own metadata shape rather than this
strategy's richer Section-12 schema, and hedging has zero interaction
with `active_management` in this version — both documented as known
limitations in the module's own docstring rather than silently
inconsistent.

**UI redesign (Step 13)** — the backend surface was checked BEFORE
building UI on top of it, same discipline as everywhere else in this
project: `record_snapshot()` really was dead code (grepped, confirmed
zero call sites anywhere outside `queries.py` itself) before this step
added its one caller; no aggregate cross-deployment endpoint existed
before this step added the two the Dashboard needed. Verified end-to-end
via the real API/DB pipeline (14 scenarios across
`test_ui_redesign.py`):

- **`realized_pnl`/`unrealized_pnl` correctness AND agreement** between
  the two independent enrichment code paths: a deployment with BOTH a
  closed position (realized +20000) and a live open position
  (unrealized +10000, from a real fed tick) shows identical numbers on
  `GET /deployments/{id}` (scoped queries) and inside the `GET
  /deployments` list (bulk-enriched, two queries total regardless of
  deployment count) — the two paths can never quietly drift apart.
- **`GET /positions` aggregate**: two deployments' open positions,
  correctly annotated with `deployment_name`/`strategy_name`, and —
  this redesign's own explicit spot-check requirement — one
  deployment's row in the AGGREGATE table confirmed to match that SAME
  deployment's own `/deployments/{id}/positions` numbers EXACTLY, not
  just approximately.
- **`GET /trades/recent` aggregate**: fills from multiple deployments,
  newest-first, correctly annotated.
- **`LotOut`'s new `symbol`/`metadata` fields round-trip byte-for-byte**
  — checked with BOTH a rich, `strangle_monthly_v2`-shaped metadata dict
  (`trigger_values`/`target_basis`/`resulting_state` and all) and a
  sparse, ad-hoc dict (`{"leg": "CE", "exchange": "NFO"}`, the shape
  most strategies actually write today, pre-retrofit) — the API returns
  the EXACT dict stored either way, nothing dropped or renamed.
- **Equity snapshots**: `GET /deployments/{id}/snapshots` correctly
  empty (not an error) before anything's recorded; after
  `DeploymentManager.snapshot_all_active()` runs (the exact method the
  periodic loop itself calls, not a separate test-only code path), one
  real row appears — checked DIRECTLY against the `deployment_snapshots`
  table first (cash, `open_positions_value` == mark-to-market unrealized
  P&L, `total_value`, `realized_pnl_cumulative`, all hand-verified), then
  confirmed the API returns that same row; a second round ADDS a point
  rather than overwriting the first (a real curve, not one flickering
  value); a PAUSED deployment (no runner) is silently skipped, not an
  error.
- **The snapshot loop is a genuine background task**: a real
  `asyncio.Task` running throughout the app's lifespan (`app.state.
  snapshot_task`), confirmed cancelled — not leaked — once the lifespan
  context exits.
- **Static file serving**: `index.html` plus all 5 new
  `static/js/*.js` modules confirmed served with real content.
- Full existing regression suite (every strategy above) re-run — zero
  regressions, after fixing two real, unrelated-to-the-redesign's-own-
  logic bugs this testing caught:
  1. **The hash router's own view-resolution bug** — `#/deployments/
     {id}` was silently never routing to Strategy Detail at all
     (`"deployments"` matched the plain list view's name regardless of
     whether an id followed it), caught only by a full headless-browser
     pass (Chromium via Playwright) driving REAL hash navigation — an
     earlier HTTP-only pass had called `Detail.load()` directly,
     bypassing the router entirely, and would never have caught this.
     Fixed by checking for the param's presence before falling back to
     the bare view-name match.
  2. **A real, if narrow, regression in `test_intraday_dtt_advanced_
     live.py`**: adding a `symbol` column to `list_lots` via a SQL
     `JOIN` changed Postgres's row order for two fills sharing the
     exact same `executed_at` (a roll's close-then-open pair) — a
     pre-existing fragility (row order for ties was never actually
     guaranteed by the original query either) that this specific JOIN
     happened to expose. Fixed by using a correlated subquery for
     `symbol` instead, leaving the primary `position_lots` scan
     structurally unchanged from before the column was added.

Not yet tested against a real Kite tick stream, real option premiums, or
a real browser session over an actual network (only headless Chromium
against the local test server) — same caveat as every other piece of
this project.

**Trade-reason logging retrofit (Step 14)** — verified against the same
bar already applied to `strangle_monthly_v2`'s own Section 12: for EVERY
one of the six strategies, at least one real trade from each distinct
trigger path was independently checked to confirm `trigger_values` alone
— not the code, not surrounding rows — is sufficient to recompute
whether the condition was genuinely true (6 dedicated integration test
files, one per strategy, driven through the real API/dispatcher/runner/
Postgres pipeline, largely reusing each strategy's own already-proven
tick/candle fixtures):

- **`pivot_supertrend`**: `pivot_break_long` (close > an independently
  recomputed `R`-level, same `compute_pivots()` formula the strategy
  itself uses, not re-derived), `st_flip` (close vs. `final_upper`/
  `final_lower` from `trigger_values` alone), `pivot_break_short`,
  `force_exit` (`candle_time >= force_exit_time`) — all 4 in one
  seeded run, `target_basis` confirmed absent (trades the underlying).
- **`pivot_supertrend_options`**: the same 4 triggers, PLUS
  `target_basis.selected_strike`/`fill_premium` checked against the
  actual sold leg, and the RESUME-CRITICAL `exchange` metadata key
  confirmed present.
- **`pivot_supertrend_options_inverse`**: `st_flip_entry_pe` (flip-to-
  red), `hold_expired` (`candles_held >= hold_candles`), and a second,
  isolated deployment for `force_exit` (`hold_candles` set unreachably
  high so it can't race) — RESUME-CRITICAL `entry_candle_date` and
  `exchange` both confirmed present.
- **`intraday_dtt_simple`**: `entry_time_reached`, `profit_target_decay`
  (`combined_now <= target_combined`, both computed from the SAME entry/
  exit premiums the test itself fed in), `leg_spike_stop` (the spiking
  leg's own current price vs. its own +40% threshold), `force_exit` — 4
  isolated scenarios, `resulting_state` confirmed to show CE-only after
  the CE fill, both legs after the PE fill (a running per-fill snapshot,
  not a single end-of-event summary).
- **`intraday_dtt_adjusted`**: `entry_time_reached`, `adjustment`
  (`smaller_total <= trigger_threshold`, `target_basis` vs. actual
  fill), `reversal_unwind` (`smaller_total >= bigger_now`,
  `leg_premiums` showing the full tie-break context), `profit_target_
  total` (`total_profit >= target`, `resulting_state` confirmed as a
  running per-fill snapshot — the flatten's first close still shows legs
  remaining, only the last shows `{"CE": [], "PE": []}`),
  `breakeven_fallback` (`spot_price` vs. the band), `force_exit` — all 6
  trigger paths, RESUME-CRITICAL `leg_role`/`exchange`/`entry_spot` all
  confirmed present on the entry fills.
- **`intraday_dtt_advanced`**: `roll_close`/`roll_open` specifically
  checked to carry the CONCURRENT-CAP condition
  (`concurrent_legs_before_roll[_open]`), NOT a fabricated ordinary-
  adjustment/reversal condition that didn't actually hold at that
  moment (explicitly asserted absent: no `smaller_total` key on
  `roll_close`, no `adjustment_trigger_ratio` key on `roll_open`) —
  `roll_open`'s `bigger_now` confirmed to reflect the CURRENT premium at
  roll time, not a stale value from an earlier adjustment trigger.

A real bug surfaced by this verification (not by the retrofit's own unit
scope, but by re-running `strangle_monthly_v2`'s existing regression
suite immediately after): `intraday_dtt_adjusted`'s `_adjust`/
`_unwind_one`/`_flatten_all` are ALSO reused, unmodified, by
`strangle_monthly_v2`'s `active_management` convergence mode via
unbound-method binding (see that module's own "ACTIVE-MANAGEMENT
DELEGATION" section) — an instance-method `self._legs_snapshot()`
`AttributeError`'d the moment `self` turned out to be a
`StrangleMonthlyV2Strategy`, which doesn't define or inherit that name.
Fixed by making it a plain module-level function of `legs` instead
(`_legs_snapshot(legs)` — no instance-method form ever existed), needing
nothing from `self` but the one dict every caller, delegated or not,
already keeps in the identical shape — full regression suite (all 15
pre-existing files, plus these 6 new ones) re-run clean after the fix.

## Folder layout

```
live_deploy/
├── RUN_GUIDE.md                # 3 ways to run this: local dev, background/production, Docker
├── run.py                       # `python run.py` — works where `python app/main.py` can't (see RUN_GUIDE.md)
├── supervisord.conf              # background/production option — see RUN_GUIDE.md
├── Dockerfile                     # containerized option — see RUN_GUIDE.md
├── config.example.json        # copy -> config.json (gitignored). access_token now optional.
├── tokens.json                 # committed — which instruments to subscribe to
├── requirements.txt
├── static/
│   ├── index.html                # the UI shell — sidebar nav, view containers, hash router (step 13)
│   ├── login.html                 # step 7 — served inline by AuthMiddleware, unauthenticated "/"
│   └── js/                         # step 13 — index.html's JS, split by view
│       ├── api.js                    # every fetch wrapper + shared formatting/badge helpers
│       ├── dashboard.js               # Dashboard view
│       ├── catalog.js                  # Strategy Catalog view + Deploy modal
│       ├── deployments.js               # Deployed Strategies view (filters, actions)
│       ├── detail.js                     # Strategy Detail view (Config/Positions/Trades/Stats tabs)
│       └── instruments.js                 # step 15 — Instruments view: search + subscribe/unsubscribe
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
    │   └── manager.py                      # DeploymentManager — lifecycle + registry wiring + snapshot loop (step 13)
    ├── routers/
    │   ├── health.py
    │   ├── deployments.py
    │   ├── aggregate.py                     # step 13 — GET /positions, GET /trades/recent (cross-deployment)
    │   ├── instruments.py                   # manual subscribe/unsubscribe control
    │   ├── kite_auth.py                      # login-url / callback / status
    │   ├── strategies.py                      # GET /strategies
    │   └── auth.py                             # step 7 — POST /auth/login, /auth/logout
    ├── strategies/
    │   ├── __init__.py                         # import list — triggers registration
    │   ├── registry.py                          # @register_strategy
    │   ├── trade_meta.py                          # step 14 — build_trade_meta(), shared metadata-dict shape
    │   ├── pivot_supertrend.py                   # step 4 — ports tg_int_st_pp's backtested rules to live ticks
    │   ├── pivot_supertrend_options.py            # step 6 — same signal engine, sells options instead
    │   ├── intraday_dtt_simple.py                 # step 8 — short straddle, decay/spike/time exits
    │   ├── pivot_supertrend_options_inverse.py    # step 9 — buys on ST flip, holds N candles
    │   ├── intraday_dtt_adjusted.py               # step 10 — straddle + dynamic rebalancing
    │   ├── intraday_dtt_advanced.py               # step 11 — subclass: rolling adjustments
    │   └── strangle_monthly_v2.py                 # step 12 — monthly checkpoint-cycling strangle
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
