# 07 · Brokers, latency and load

Full doc: `reference/linck-brokers-languages-load.html`. Benchmarks:
`tests/bench/` (run by name; see its README). Measured on 23 Oct 2026 on a
4 vCPU 2.8 GHz VM inside a sandboxed container (round trips on the slow side).
Every figure is **measured** or an **estimate**; keep that distinction.

## Brokers without a Go SDK

The gateway needs a broker's **API**, not its SDK. Money-critical work
(idempotency tag, rate limits, order state machine, reconnect,
reconciliation) is written once in the Go core. An adapter only translates.

| Option | When |
|---|---|
| A. Go adapter in process (generate the client from OpenAPI where published) | default |
| B. Sidecar in the SDK's language, same pod, same `BrokerAdapter` gRPC contract over a Unix socket (~60 µs measured vs 20–200 ms broker API, est.) | login/signing only the SDK does, undocumented socket format, or need it in days |
| C. Mixed: orders in Go, price socket or login in a sidecar | e.g. Fyers |

Plan per broker (checked 23 Oct 2026; re-check docs first): Kite → A (Go
client); Angel One → A (`smartapigo`); Upstox → A, generated from its OpenAPI;
Dhan → B (Python `dhanhq`) then A; Fyers → C. A sidecar never decides retries,
never holds limits or counters, talks only to its broker and the core. One
conformance suite runs against the contract for Go adapters and sidecars
alike, then a paper week with shadow orders, then a daily 09:00 smoke test.

`BrokerAdapter`: Capabilities, Login, Place, Modify, Cancel, OrdersByTag,
Positions, BasketMargin, OrderUpdates (stream), Ticks (stream, only where the
user's own feed is used).

## Many languages on one runner

The runner never runs strategy code; it starts processes that speak one
protocol (see `06-runtime-and-languages.md`). So moving the runner from Python
to Rust changes nothing for any strategy.

Round trip runner ↔ strategy (measured, Rust supervisor, Unix socket):

| Strategy in | 1 tick p50 | 16 ticks p50 | 256 ticks p50 | per tick @256 | idle memory |
|---|---|---|---|---|---|
| Rust | 39–42 µs | 37–39 µs | 49–53 µs | 0.21 µs | 2.0 MB |
| Go | 44–46 µs | 48–49 µs | 55–59 µs | 0.24–0.26 µs | 4.1 MB |
| Node | 57–58 µs | 62–63 µs | 74–82 µs | 0.33–0.36 µs | 42 MB |
| Python | 56–62 µs | 74–78 µs | 196–224 µs | 0.87–1.06 µs | 9.3 MB (2 MB forked) |

Turned down: embedding Python in the Rust supervisor (one crash takes all
down); one process per language (shared heap and GIL, no per-strategy
budgets); gRPC over TCP per strategy (kept only for "bring your own process");
every SDK as a thin client of one Rust core (kept only for indicators).

## Latency: where the time goes

- Today's strategy decision: **7–47 µs** (measured; p50 7–11 µs).
- Today's runner per tick that doesn't trade: **0.06–0.15 ms**.
- Conflation core: 0.05–0.33 µs an update; 3–19M updates/s per core.
- Decoding a tick: 0.2–1.1 µs.
- **The real delay today is Kite REST calls inside decisions**: an entry makes
  2–11 calls (quotes to pick strikes by premium, LTP, liquidity) spaced
  ≥0.35 s by the process-wide throttle, so a trade waits **0.7–4 s** plus
  Kite's response time, and deployments entering at 09:20 queue behind each
  other. Per trade: DTT entry 5 calls (≥1.75 s), strangle entry 11 (≥3.85 s),
  SuperTrend 2–4, trend spread 3–8. Our own work with an instant fake Kite:
  29–76 ms.
- The fix (in the architecture): option chains and prices as cached streams;
  "strike nearest ₹100" becomes a local Request answered in microseconds; the
  only remote call left is the order.

Budget (p99 targets): tick → intent < 50 ms in a cell; intent → broker
< 30 ms our side; gRPC host hop 0.2–1 ms (estimate, measure on I2); broker ack
20–200 ms (estimate, log per order from day one).

**Not measured yet**: NATS JetStream publish/ack and a gRPC stream hop (NATS is
on the order path in the intermediate design, not the tick path); gVisor cost;
real feed update rates and the open burst.

## Load

Unit costs (measured): fan-out to strategy processes 35–45k batches/s on the
4 vCPU host (16-tick batches); today's broadcaster where every runner sees
every tick: ~2.2M ticks/s at 10 runners, ~0.44M at 50, ~100k at 200.

Formula:

```
ingest updates/s = instruments × updates/instrument/s × open-burst factor
batches/s per host = strategies × batches each/s (≤10/s tick, ~1/s bar strategies)
CPU per host = batches/s × (~60 µs round trip + strategy's own time)
memory per host = strategies × (2–4 MB Python/Go/Rust; 42 MB Node) + caches
hosts = max(CPU, memory) ÷ 60%      # keep 40% for the open, a failed host, moves
```

| | Today | 2,000 strategies | 20,000 |
|---|---|---|---|
| Instruments | a few hundred | 5,000 | 20,000 |
| Updates steady → open (est.) | hundreds/s | 10k → 100k/s | 40k → 400k/s |
| Strategy batches at the open | — | 20k/s (~1.5 cores) | 200k/s (~15 cores) |
| Strategy memory | one process | ~6–10 GB | ~60–100 GB |
| Runtime hosts (16 vCPU/64 GB) | 1 machine | 2 | 4–6 in 2 cells |

The open-burst factor of 10× is an assumption until logged. **Correction to
the architecture doc**: its "3–5k strategies a host at ~1 ms/event" is wrong;
host size is set by memory and the open burst, not per-event CPU.

Today's ceiling: one Python process, ~100k ticks/s with 200 deployments on
one core before strategy work, plus the backlog risk. Neither bites at today's
size; P0 fixes both.
