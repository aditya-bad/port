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

Step 2 (**not built yet** — "later I will tell strategies"): live
strategies running on top of this same tick stream, in
`app/strategies/`. That package exists as an empty placeholder.

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
  "tick_mode": "full"
}
```

The `access_token` expires daily — refresh it each session. `tick_mode`
is one of Kite's three tick verbosity levels: `"ltp"`, `"quote"`, or
`"full"` (default — includes market depth).

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

## Verified without live Kite credentials

No Kite credentials exist in this environment (same limitation as the
other folders in this repo). The dispatcher's `kite_ticker_cls` injection
point was used to substitute a fake `KiteTicker` that replicates the real
one's thread-based callback behavior, and the following was verified
end-to-end:

- `TickBroadcaster`: fan-out to multiple subscribers, unsubscribe stops
  delivery, bounded-queue drop-oldest behavior under a slow consumer
- `LiveDataDispatcher`: `on_connect` correctly subscribes + sets mode on
  all configured tokens; a tick simulated from a genuine background
  thread (not just a direct function call) correctly crosses into the
  asyncio loop and reaches the broadcaster; `on_close` correctly flips
  `connected` to `false`
- Full FastAPI app via `TestClient`: `/health` reflects live dispatcher
  state; **two independent WebSocket clients connect at once and both
  receive the exact same tick from one simulated Kite connection** — the
  core "one upstream, N downstream" property, confirmed at the actual
  HTTP/WebSocket layer, not just in the broadcaster unit tests
- Disconnecting a WebSocket client correctly unsubscribes it — the
  downstream count in `/health` drops back down

This confirms the dispatcher mechanics are correct. It has not been run
against a real Kite WebSocket — that requires your `config.json`.

## Relationship to the rest of the repo

Fully isolated from the main `port` repo's Nifty 50 backtest pipeline,
from `generic/`, and from `tg_int_st_pp/`. Shares no code, no data, no
config with any of them. This is a live/real-time service, not a
backtest — it doesn't read or write anything under `data/`.
