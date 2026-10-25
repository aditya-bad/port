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
- **Deploy anywhere**: any part on any provider or the user's own servers,
  mixed; only a cell's trading path must share a site (see "Deploy anywhere").
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
| Linck web | TypeScript | static files from the edge gateway; a CDN is optional |
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
| Backtest farm | Rust jobs | any spare machines (spot on a provider, or own servers), reads the lake |
| Read models, Notifications | Go | event consumers |
| Assistant | Python | read-only tools |
| linck-core | Rust library | indicators, fill model, codec; bindings PyO3, napi-rs, WASM (wazero for Go) |

Which product each part belongs to: **LinckTrade** does the trading and keeps
the money (market data, tick cache, computed streams, runtime, OMS, broker
gateway, sim venue, control, builder, reference data, integrity, backtest
farm). **LinckLens** turns its data into what people see (web, mobile, product
API, live gateway, read models, assistant) and never writes an order, fill or
position. **Linck SDK** is what strategy developers use (the SDKs, the CLI with
its local supervisor and sim venue, protocol v1, the conformance suite,
linck-core). Edge gateway, identity, commerce and notifications are shared by
all three; this doc calls them *Linck core* and installs them with whichever
site the user picks (a proposal, not yet confirmed).

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
lake in object storage (Iceberg/Parquet: every tick, chains, point-in-time
reference data; S3 API, Azure Blob, GCS or self-run), Redis, etcd (leases,
epochs), Vault (broker tokens; only the broker gateway decrypts), write-once
audit store. Each is reached by its own protocol and can be self-run; see
"Deploy anywhere". Row-level security by
tenant.

## Exactly-once money

Intent id = `deployment:batch:n` (the same on every replay; the epoch is a
separate field) is the idempotency key; fencing epochs reject a zombie
runner's intents; transactional outbox;
our order id travels as the broker tag and is reconciled on reconnect;
positions reconciled with the broker every few minutes (broker wins). Baskets:
HEDGE_FIRST (default), SIMULTANEOUS (exits), each leg a limit order with price
protection. Exits are never blocked by cash/margin rules. Shadow mode mirrors
every order to the sim venue.

## Deploy anywhere (owner, 25 Oct)

**Linck Platform is three products: LinckTrade, LinckLens and the Linck SDK.**
Whoever runs Linck chooses where each part runs: any cloud (AWS, Azure, GCP
or another), their own servers, or a mix (say Trade on their own servers,
Lens on Azure, the backtest farm on AWS). It has to work the same in every
mix. Nothing in the design may need one provider. (Earlier versions of these
docs assumed AWS: EKS in Mumbai, RDS, S3, KMS, a NAT gateway. The owner never
chose that; it was an agent's assumption, removed on 25 Oct.)

**1. Only portable building blocks.** Each dependency is named by the
protocol it is reached by, and has a version anyone can run themselves. A
provider's managed service may stand in only if it speaks the same protocol
and passes our conformance tests for it.

| Need | Reached by | Run it yourself | Or a managed one, if it passes the tests |
|---|---|---|---|
| Containers | OCI images; Kubernetes API | any conformant Kubernetes: k3s on one server, kubeadm or Talos on own hardware; Docker Compose bundle for one machine without Kubernetes | EKS, AKS, GKE, others |
| Relational | Postgres wire protocol, core features only (RLS, logical replication; no provider extensions) | Postgres with CloudNativePG or Patroni | RDS/Aurora Postgres, Azure Database for PostgreSQL, Cloud SQL |
| Lake | one object-store layer (Rust `object_store`, Iceberg FileIO): S3 API, Azure Blob/ADLS, GCS, local disk | MinIO or Ceph | S3, Azure Blob, GCS |
| Analytics | ClickHouse protocol | ClickHouse | ClickHouse Cloud |
| Cache | Redis protocol | Valkey or Redis | ElastiCache, Azure Cache, Memorystore |
| Money/history log | Kafka API (idempotent producer, transactions, retention) | Redpanda | any Kafka-API service that passes the tests |
| Commands, live signals | NATS | NATS | Synadia Cloud |
| Leases, epochs | etcd | etcd | none needed |
| Workflows | Temporal | Temporal | Temporal Cloud |
| Secrets | Vault API | Vault or OpenBao, unsealed by Shamir shares or an HSM (PKCS#11) | unsealed by AWS KMS, Azure Key Vault or GCP KMS |
| Images | OCI registry; Sigstore signatures checked at admission | Harbor | ECR, ACR, Artifact Registry, GHCR |
| Way in | Envoy edge gateway in the cluster | MetalLB or a plain public IP in front | the provider's load balancer in front |
| Web files | served by the edge gateway | — | any CDN, optional |
| Service identity | SPIFFE IDs, mTLS | SPIRE, federated between sites | — |
| Telemetry | OpenTelemetry (OTLP) | Prometheus, Grafana, Loki, Tempo | any OTLP backend |

Not used, because nobody can run them outside one provider: DynamoDB, SQS,
Kinesis, Cosmos DB, Pub/Sub, Lambda/Functions, provider IAM as service
identity, VPC peering as the only link.

**2. Sites, and what must stay together.** A *site* is one place (a cloud
region or a data centre) with one Kubernetes cluster or one Compose host. A
site runs any set of parts.

- **Must stay in one site: a cell's trading path.** Market data ingest, tick
  cache, computed streams, strategy runtime, OMS, broker gateway, the cell's
  ledger Postgres and its event log. The budget (tick → intent p99 < 50 ms,
  intent → broker < 30 ms) and the OMS transaction don't survive a hop
  between providers: 1–3 ms in the same city, 10–30 ms between Indian cities,
  and unpredictable over the internet (estimates, to measure).
- **May be anywhere:** each cell (cell A on own servers, cell B on Azure);
  all of LinckLens (web, product API, live gateway, read models, assistant);
  the control plane, builder and reference data; integrity; the backtest farm
  (spot or idle machines on any provider, or own servers) and the lake;
  identity, commerce, notifications; the SDK and CLI (a laptop); remote
  strategies (the user's own machine, with their own latency).

**3. What crosses between sites, and how.** Only three things: gRPC calls
(mTLS, deadlines, retries only on idempotent methods), event streams
(Redpanda topics mirrored with MirrorMaker 2; NATS leaf nodes, in the
intermediate JetStream sourcing over a leaf node), and object-store reads. No
database is shared between sites, and no cluster network spans two. It must
work over the plain internet on 443 with mTLS; a private link (WireGuard, a
VPN, a provider interconnect) is optional, for speed and privacy.

**4. Trade never waits on another site to trade.** Dependencies point one
way: Lens reads from Trade. What Trade needs from elsewhere it caches in the
cell: identity's signing keys, deployments and their epochs, reference data
for the day. If the link drops, Trade keeps trading and keeps its log; Lens
shows how old its data is; when the link returns, the mirrors catch up from
their offsets (retention ≥ 7 days, so nothing is lost). Pausing and closing
from a screen can't reach a cut-off cell, so each Trade site has its own
door: the CLI against that site's edge gateway, and the loss limits in the
OMS, which never need a remote call. Strategy time is the batch's `ctx.now`,
so clock differences between sites never change a decision.

**5. Broker IPs belong to a site.** Brokers need registered static IPs
(SEBI). Each site that runs a broker gateway gets its own fixed egress IP:
the provider's NAT with a reserved IP (AWS, Azure, GCP), or the server's own
public IP. Moving a broker gateway to another site starts with registering the
new IP; the move runbook checks it.

**6. Install, per site.** One Helm chart per product (`linck-trade`,
`linck-lens`, `linck-core` for edge, identity, commerce, notifications) and a
site file saying which parts run here and where the other sites are.
`helm install` or any GitOps tool (Argo CD, Flux). Optional OpenTofu modules
create the cluster, network and buckets for AWS, Azure, GCP or own servers;
the charts never need them. A site check proves a site before it trades:
store versions and features, mTLS to the other sites, the egress IP the broker
sees, the latency to the broker and to the feed.

**7. Proven, not promised.** CI runs the gate, the fault matrix and the
determinism checks on a local cluster (k3s), plus a split test: Trade and
Lens on two clusters with 30 ms injected between them and the link cut for
five minutes mid-day, asserting no lost or doubled order, Lens catching up,
screens showing their age. Before each release the same suite runs on own
servers and on each supported provider. Data stays in India: each site says
where it is, and the site check warns if a store holding user data isn't.

**Running it.** Containers, distroless signed images. Strategies are **not
pods** (50k pods is too many; pods start in seconds): the supervisor starts
sandboxes. Disaster recovery is a second site of the user's choosing, on any
provider: Postgres streaming replication over TLS, Redpanda mirrored, the lake
copied. Releases only after 15:45 IST; every release replays yesterday first
("yesterday diff"); canary cell then waves; DB changes expand → migrate →
contract.

## Intermediate architecture (build first)

One site (wherever the owner chooses; today's box with k3s works), one cell,
today's code carried over. Nine deployables:
`core-api` (Python modular monolith: today's app), `live-gateway`, `md`,
`oms`, `broker-gw-kite` (Go), `runner` (Python StatefulSet of leased
workers), `sim` (Python fill model behind the broker contract), `workers`
(Temporal), `notifications`. NATS JetStream, Postgres, ClickHouse, Redis, object
storage.
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
| I0 | Images, Helm charts, one Kubernetes cluster at a site the owner picks, today's app as core-api | A week of paper days beside the box |
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
