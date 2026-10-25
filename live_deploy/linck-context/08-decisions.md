# 08 · Decisions and their reasons

Each entry: what was decided, what was turned down, why. "Owner" means the
owner chose it; otherwise it was proposed and accepted. Revisit an entry only
when its reason stops being true, and ask the owner first.

## Product and behaviour

| Decision | Instead of | Why |
|---|---|---|
| Cycle P&L everywhere (intraday = day; positional = entry→exit; switch day shows both) (owner, 23 Oct) | Day P&L | Day P&L is misleading for positional strategies |
| P&L basis switch Gross / After charges / + slippage, global (owner) | One number | Honesty: always know which number you're looking at |
| Slippage recorded alongside fills; fills stay at LTP | Filling at bid/ask | Strategy decisions unchanged, both views available; switch later after reviewing real data |
| Charges per position, options formula only, verified to the paisa against Zerodha | Generic segments | Only options strategies exist; exact match was required |
| Closing positions keeps the deployment running (owner) | Pausing it | The owner didn't want close = pause; Close everything still pauses |
| Strangle: liquidity as a **tie-break within ±5%** of target, not a band-wide preference (owner: "I don't want too much deviation") | Round strikes only; best OI anywhere in the band | Stay close to the configured percentages |
| Strangle: freshness gate + live-quote confirmation before any decision | A fixed "no adjustments before 9:20" window | Fixes the cause (stale previous-day price) for every leg, any time |
| Strangle EOD roll-out (roll the tested side out instead of converging into a straddle at 15:13), **EOD only**, default on (owner) | All-day avoidance; leave as is | Overnight gap risk is where a straddle hurts most; all-day avoidance would remove the convergence stop, the only early stop-loss |
| `checkpoint_exit_not_before` optional floor (owner) | "Two ticks" condition | Owner withdrew the two-ticks idea |
| Candle store keeps **options and futures only**, 1-minute, no duplicates, roll up on read (owner) | Storing indices; storing 5-min too | Kite keeps index history forever but drops options/futures at expiry |
| Futures per leg: same expiry → that month's monthly → none (owner) | Nearest future | A December leg must not be valued off October |
| VIX fetched from Kite on demand, nothing stored (owner) | VIX tables | Kite keeps VIX history; storing it was unnecessary |
| Linck as the new UI, built from the owner's Claude Design files; classic kept as fallback (owner) | The earlier "Desk" redesign (withdrawn) | The owner called Desk "AI slop"; Linck design was theirs |
| Modular UI: themes/preferences/pages/tabs/rules each one list to extend (owner's only ask) | — | Future changes without rewrites |
| New backend endpoints under `/v2`, never disturbing v1 (owner) | Changing v1 | Don't break the current flow |
| Linck installed as a PWA; app always opens Linck (owner chose option A) | TWA/Capacitor/native | Same experience, updates on every redeploy |
| Brand: the terminal-prompt-printing-a-candle mark; Paper version kept unused; header shows the word "Linck" (owner) | Lynx, stamps, letter marks | Owner wanted the mark to be about algo trading; liked the wordmark in the header |
| Geek mode: full-screen mode with a transition, read-only, follows the theme (owner) | A tab; dark-only | Owner wanted everything else to drop away; dark-only was rejected |
| What-if replay paused (owner) | Building it | Needs the full option chain stored; owner wants to use other views first |
| Repair: ambiguous minutes are the owner's choice; spikes count; 1-minute candles (owner) | Always worst case | Owner decides the path |
| Repair in the website, in phases; runs in a **separate worker process** | In the live process | The replay swaps the clock, Kite and caches app-wide; unsafe inside the live app |
| Day check counts trades/legs/end state as mismatch, not prices | Price thresholds | A 1-minute candle isn't the tick the app filled at |
| Repair page shows only the day check and reports; live alerts keep running in the background (owner chose "a") | Stopping minute checks | A 10:00 Kite disconnect must still alert |
| Repaired badge scoped to cycles; Flagged stays until repaired/dismissed (owner) | Per deployment forever | It misled more over time |
| Replay as an Opstra/Sensibull payoff replay, everything in one pane, Trades/Episodes below (no tabs) (owner) | Revealing chart; tabs | Owner found the earlier replay bad to analyse |
| Shadow mode: LIMIT + 0.5% buffer; margin vs both deployment capital and account; freeze limits from the daily NSE file; rejections → alert + report; Standard ready-for-live thresholds (owner, 23 Oct) | Market orders; one margin basis; config-only limits; report-only | Most realistic live model; one Kite account shared by all deployments |
| Shadow never delays or changes a fill; its Kite calls wait for 1 s of Kite idleness | Inline checks | Trading first |
| Every bug gets a test that fails without the fix (owner) | — | The gate must say whether it is safe to deploy |
| Feature ideas: only shadow mode kept; others parked (owner, 22 Oct) | Kill switch, Telegram, tax export, backtester… | Most existed already or were generic |

## Architecture

| Decision | Instead of | Why |
|---|---|---|
| Neither a monolith nor microservices everywhere: modular monolith + split services where behaviour differs | Either extreme | The book needs one transaction; trading must not restart with the website |
| Contracts final first; implementations swapped on triggers | Big-bang rewrite | Cheap, staged migration |
| Rust on the hot data path, sim venue, supervisor | C++, Go, Java | Speed with memory safety next to untrusted code; no GC on the tick path |
| Go for control, OMS, broker gateways | Rust, Java, Python | Readable where money moves; OMS is I/O-bound |
| Python first for strategies; other languages via one protocol | A DSL; one language | Where quants are; each language is just an SDK |
| One protocol, thin SDKs, three sandbox kinds (gVisor, Wasmtime, remote) | A runtime per language; a shared C core | Don't multiply the hardest code; avoid FFI crashes |
| **The runtime owns walk/branch/snapshot/recovery** | Copying strategy objects (today's Python repair) | Works for any language |
| Python strategies forked from a zygote | Fresh interpreters | 2 MB vs 52 MB each |
| Broker adapters behind a gRPC contract, Go in process or a sidecar in the SDK's language | Requiring Go SDKs; the gateway in Python | Money logic written once |
| **Ticks on conflated gRPC streams + shared memory**, latest value guaranteed by conflation (owner's requirement) | Aeron, NATS, WebSocket/SSE between services, own UDP | No transport gives latest-wins; conflation does; gRPC is the simplest typed transport that signals a slow receiver. Aeron: microsecond gain unused by ms-scale strategies, no official Rust client, media driver to run, no VPC multicast. NATS: can drop the newest or disconnect a slow consumer. Look again only if the host hop > 5 ms p99 at peak |
| SSE to screens | WebSocket | Screens only listen; today's app already moved to SSE |
| Redpanda for money/history logs, NATS for commands/live signals | Kafka only, NATS only, Pulsar | Kafka API without JVM; cheap fan-out for control |
| Postgres double-entry ledger | TigerBeetle | Write rates are well inside Postgres; SQL reporting |
| ClickHouse analytics; lake in object storage as Iceberg tables (S3 API, Azure Blob, GCS, or MinIO/Ceph on own servers) | TimescaleDB, BigQuery | Fast scans, data in India, any provider |
| **Deploy anywhere**: each part of Linck Platform (LinckTrade, LinckLens, Linck SDK) runs on any cloud or the user's own servers, mixed (owner, 25 Oct) | One provider. Earlier docs assumed AWS (EKS Mumbai, RDS, S3, KMS, NAT); the owner never chose it | The person running Linck decides where each part runs |
| Any conformant Kubernetes (k3s on one server, own hardware, EKS/AKS/GKE); a Compose bundle for one machine; strategies as sandboxes not pods | One provider's Kubernetes, ECS, Nomad, serverless; pod per strategy | The same charts on every provider and on own servers; gVisor runtime classes; 50k pods is too many |
| Every dependency reached by an open protocol that has a self-run version; a managed service only if it passes our conformance tests for that protocol | Provider-only services (DynamoDB, SQS, Kinesis, Cosmos DB, Lambda) | Any part must move between providers without a rewrite |
| A cell's trading path in one site; every other part may be anywhere | Splitting market data, runtime, OMS and broker gateway across sites | The 50 ms + 30 ms budget and the OMS transaction don't survive a hop between providers |
| First version names: `market-data` (was `md`), `paper-broker` (was `sim`) (owner, 25 Oct; owner chose paper-broker, left market-data to the agent) | md, sim; tickstream, sim-venue | Names that say what the service is |
| First version first; the final architecture's rework paused (owner, 25 Oct) | Reworking the final first | The owner needs the first iteration first |
| First version: one home site for everything that writes the one Postgres; live gateway, web files, notifications, backtests, the lake and a standby may be anywhere | Splitting core-api's Postgres across sites | A database is never shared across sites; Lens gets its own site when the product API leaves core-api |
| Between sites only gRPC (mTLS), mirrored event streams and object-store reads; SPIFFE/SPIRE identity federated between sites; works over the plain internet | Shared databases across sites, provider IAM, VPC peering as the only link | Works between any two providers and own servers |
| Trade never waits on another site to trade; it caches what it needs and has its own door (CLI, OMS loss limits) | Trade calling Lens or a remote identity on the order path | A cut link between sites must not stop trading |
| etcd leases (Postgres in intermediate) | Redis locks | Linearizable with fencing |
| Temporal for the EOD chain | Cron + queues, Airflow | Resume at the step that stopped |
| Cells | One big cluster, per-tenant stacks | Bounded blast radius |
| Protobuf + Buf, monorepo with native tools + just | JSON Schema/Avro; polyrepo; Bazel | Codegen everywhere; one PR across contract and code |
| Chains and quotes as cached streams; no network in a decision | Kite REST inside decisions | That's where today's seconds go; also makes replays exact |
| P0 in today's Python now | Waiting for Rust | Removes the backlog in days |

## Owner's answers to explicit questions (for the record)

- Android vs web: web now, Android later.
- Charges backfill: not run yet at the time (11 Oct).
- After the repair page: repair page, then shadow; write up shadow first.
- Shadow: LIMIT + buffer; margin "Both"; freeze limits from the daily NSE
  file; rejections "Alert + report"; ready-for-live "Standard".
