# 05 · Target architecture

Full detail with diagrams: `reference/linck-architecture.html` (v2, commit
119ca8a; plain text in `reference/text/`). This is the summary an agent needs.

## The rule that makes it cheap

**Contracts are final from day one; implementations grow into them.** Every
boundary speaks its final protobuf contract from the intermediate stage on.
Swapping Go market data for Rust, or Python runner workers for a Rust
supervisor, changes what's behind an interface, never the interface. No
big-bang rewrite.

## Principles

- **One writer of money**: only Orders & risk (OMS) writes orders, fills,
  positions, cash. Repairs, manual closes, marketplace all go through it.
- **Same engine everywhere**: backtest, replay, repair, paper, shadow, live
  run identical strategy code; only clock, market and venue change.
- **Latest wins on prices, exactly-once on money.**
- **Nothing silent**: every handled failure is an issue with a day attached.
- **Cells** bound the blast radius; a tenant lives in one cell.
- **Determinism**: runtime-owned clock and randomness, pinned images.
- **Compute once, share everywhere** (each instrument subscribed once; each
  indicator computed once per cell).
- A part becomes its own service only if it must fail on its own, scale on
  its own axis, run a different language, or own data nobody else writes.

## Design drivers (final)

50k running strategies (2k intermediate); ~60k instruments; 100–300k
updates/s at the open; tick → intent p99 < 50 ms in a cell; intent → broker
p99 < 30 ms; ~150k orders at 15:15; zero lost/doubled orders; host lost →
trading again < 10 s.

## Components (final), one language each

| Component | Language | Notes |
|---|---|---|
| Linck web | TypeScript | static files on a CDN |
| Mobile apps | Kotlin, Swift | two apps |
| SDKs | Python, Go, Rust, TypeScript | thin libraries over one protocol |
| CLI `linck` | Rust | run a past day locally, push, deploy |
| Edge gateway | Envoy + Go plug-in | REST/SSE/gRPC, auth, plan limits |
| Product API | Python | what screens read/write |
| Live gateway | Go | SSE to screens, latest values only, ≤ every 250 ms |
| Identity, Control (registry, scheduler, lifecycle), Commerce, Builder, Reference data | Go | control plane |
| Market data ingest + sequencer | Rust | one handler per feed source; 96-byte fixed record |
| Tick cache agent | Rust | DaemonSet; shared memory slot per instrument (seqlock) + ring |
| Computed streams | Rust | bars, indicators, IV/greeks, chain views, computed once per cell |
| Strategy runtime | Rust supervisor | sandboxes: gVisor process, Wasmtime WASM, or remote gRPC |
| Orders & risk (OMS) | Go | state machine, risk pipeline in-process, outbox, reconciliation |
| Broker gateway | Go | one codebase, one deployment per broker; adapters may be sidecars |
| Sim venue | Rust | paper/shadow/backtest fills from depth with queue and latency models |
| Integrity | Go + Temporal | day check, repair, shadow comparison, reconciliation |
| Backtest farm | Rust jobs | spot nodes, reads the lake |
| Read models, Notifications | Go | event consumers |
| Assistant | Python | read-only tools |
| linck-core | Rust library | indicators, fill model, codec; bindings PyO3, napi-rs, WASM (wazero for Go) |

Deliberately **not** services: the ledger (OMS tables), risk checks (inside
the OMS transaction), the scheduler (module of Control), indicators (library),
decision log (events), a strategy (a sandbox, not a pod).

## Protocols

- Outside: HTTPS 443. REST/JSON for requests, **SSE** for live updates to
  screens; gRPC for SDK, CLI and remote strategies.
- Between our services: gRPC + protobuf, mTLS, a deadline on every call (2 s;
  200 ms inside a cell), retries only on idempotent methods. Never REST inside.
- Events: **Redpanda** for anything whose loss costs money or history
  (`orders.intent.v1`, `orders.event.v1`, `ledger.journal.v1`,
  `md.archive.v1`, decisions); **NATS** for commands and live-update signals.
  Intermediate: NATS JetStream for all of it.
- **Ticks**: conflated **gRPC server streams** (latest value per instrument,
  one batch in flight), then shared memory on the host, then a Unix socket into
  the strategy. No message broker on the tick path. (Aeron and NATS were
  dropped for ticks; see `08-decisions.md`.)
- Long work: Temporal workflows.

## Data

Postgres (control; ledger shard per cell with a double-entry journal and
positions as projections; product), ClickHouse (bars, decisions, read models),
lake on S3 (Iceberg/Parquet: every tick, chains, point-in-time reference
data), Redis, etcd (leases, epochs), Vault + KMS (broker tokens; only the
broker gateway decrypts), write-once audit store. Row-level security by
tenant.

## Exactly-once money

Intent id = hash(deployment, epoch, decision seq, leg) is the idempotency
key; fencing epochs reject a zombie runner's intents; transactional outbox;
our order id travels as the broker tag and is reconciled on reconnect;
positions reconciled with the broker every few minutes (broker wins). Baskets:
HEDGE_FIRST (default), SIMULTANEOUS (exits), each leg a limit order with price
protection. Exits are never blocked by cash/margin rules. Shadow mode mirrors
every order to the sim venue.

## Deployment

Containers on Kubernetes (AWS EKS, Mumbai, 3 AZs; DR Hyderabad), Helm + Argo
CD, Terraform. Platform cluster per region + one cluster per cell. Strategies
are **not pods** (50k pods is too many; pods start in seconds): the supervisor
starts sandboxes. Fixed-IP NAT egress per cell (brokers require registered
IPs). Distroless signed images. Releases only after 15:45 IST; every release
replays yesterday first ("yesterday diff"); canary cell then waves; DB changes
expand → migrate → contract.

## Intermediate architecture (build first)

One region, one cell, today's code carried over. Nine deployables:
`core-api` (Python modular monolith: today's app), `live-gateway`, `md`,
`oms`, `broker-gw-kite` (Go), `runner` (Python StatefulSet of leased
workers), `sim` (Python fill model behind the broker contract), `workers`
(Temporal), `notifications`. NATS JetStream, Postgres, ClickHouse, Redis, S3.
Sized for ~2k strategies, ~5k instruments, ~20k updates/s. It fixes today's
two structural risks first: the website and strategies share a process, and
several places write money.

Each stand-in has a swap trigger (e.g. Rust market data when >10k
instruments or a second vendor; Rust supervisor when the SDK opens to the
public; Redpanda when retention outgrows JetStream; cells at ~5k strategies or
the first broker partner).

## Migration steps

| Step | What | Proven equal by |
|---|---|---|
| **P0** | Today's app: tick broadcaster conflates per subscriber (one slot per instrument, one batch in flight), ticks routed by instrument | Slow-strategy test: after a stall, the next tick processed is the newest; fills/events still in order |
| I0 | Images, one EKS cluster, today's app as core-api | A week of paper days beside the box |
| I1 | Contracts repo; SDK v0 facade over StrategyBase | Fault matrix and crash suites unchanged |
| I2 | Strategies out of the API into leased Python runner workers; conflated gRPC ticks; NATS intents/fills | A week of identical fills (dual run) |
| I3 | Go OMS + Kite broker gateway, shadow-write then cut over | Journal = today's tables nightly for two weeks |
| I4 | Go market data, Redis latest prices, ClickHouse bars | Bars = Kite 1-minute candles |
| I5 | Temporal EOD chain, tenants + RLS, shadow mode | EOD outputs equal; tenancy tests |
| F1–F9 | Rust feed handlers; computed streams; Rust supervisor + sandboxes (etcd leases); Rust sim venue + backtest farm; Redpanda; Go control plane (strangler); cells; more SDKs/WASM/remote/marketplace; reference data + more brokers | Records equal; decision hashes equal; fills to the paisa; contract tests |

The runtime doc (`06-runtime-and-languages.md`) adds, before I2: journal,
batch numbers and snapshots in today's runner; deterministic intent ids and
epochs; protocol v1 with Python process mode.

## Open architecture questions

Data vendor (snapshot vs tick-by-tick); own SPAN margin vs broker basket API
at scale; Python-in-WASM; cell size (measure on I2; the old "~1 ms/event"
assumption was wrong, it's 7–47 µs); global vs per-cell scheduler; gVisor cost
per round trip.
