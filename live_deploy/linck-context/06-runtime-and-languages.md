# 06 · The strategy runtime: any language, walk, crash, latest tick

Full doc: `reference/linck-runtime.html`. Prototype that proves it:
`tests/bench/runtime/` (`supervisor.py`, `strat.py`, `gostrat/main.go`).

## The one idea

**The runtime owns time, state and inputs. A strategy, in any language, is a
function behind a protocol**: given its saved state and a numbered batch, it
returns orders, decisions and its new state. Walking a day, branching on an
ambiguous minute, restarting after a crash, backtesting and running on a
laptop are all the runtime feeding numbered batches to that function. None of
it is written per language.

The owner asked "if a strategy is written in Go, how do we walk it / restart
it?" The answer: you don't, the runtime does, the same way for every
language. Today's repair engine copies the Python strategy object in memory,
which can't work across languages; the protocol's snapshot/restore replaces
that.

## Runtime protocol v1

Length-prefixed protobuf frames on a Unix socket the supervisor opens per
strategy (the prototype uses JSON lines). Types generated from one set of
.proto files for every SDK.

| Direction | Message | Carries |
|---|---|---|
| sup → strat | Hello | protocol version, deployment, params, last saved state, instruments |
| strat → sup | Ready | SDK name/version, subscriptions (instruments, bars, computed streams, timers) |
| sup → strat | Batch | seq, now, latest value of each changed instrument (+ skipped count), bars, fills, order updates, timers |
| strat → sup | Request | chain, quote, history, lookup: answered from host caches, never the network |
| sup → strat | Reply | the answer, within the same batch |
| strat → sup | Done | the batch's seq, commands (orders, baskets, cancels, timers), decisions, state delta / hash |
| both | Snapshot · Stop · Log · Heartbeat · Error | state with its seq; clean stop; logs; liveness; error with batch, message, stack |

Rules: one batch in flight; fills/order updates/timers never conflated and in
order; no network, files or threads inside a strategy; the only clock is
`ctx.now`, the only randomness `ctx.random` (seeded); budget 50 ms soft, 1 s
hard (then killed and restarted).

## How each language runs

- **Build** (per language, pinned build image): Python deps locked into a
  layer; Go → static binary with the SDK; Rust → native binary; TypeScript →
  bundle run by Node.
- **Start**: Python is forked from a preloaded parent ("zygote"); measured
  ~2 MB private per strategy vs 52 MB for a fresh interpreter. Go/Rust binaries
  exec directly (2–4 MB). Node is 42 MB a process (WASM later for density).
  In the cloud inside gVisor; on a laptop the CLI is the supervisor.
- **The SDK owns `main()`**: Hello → build the user's class with params and
  state → Ready → loop Batch → handlers → Done. User code only sees `ctx`.

## What every SDK must do (conformance suite)

Speak the protocol and own main(); save/restore only declared state,
serialised the same way every time; deterministic decisions (Python:
PYTHONHASHSEED fixed; Go: lint against ranging over maps and goroutines in
handlers; TS: Date/Math.random replaced); name orders the same on every
replay (`deployment:batch:n`, never user-named); report a crash as
Error{batch, error, stack} then exit; never send null for an empty list (a
real bug the prototype found in Go).

Tests per SDK and per user build: walk equivalence, crash and catch-up,
determinism (double run, equal hashes), cross-SDK reference strategies.
Policy: replay workers use the same CPU architecture as live (Go may fuse
multiply-add on arm64); strategy version pinned in the journal header.

## The four pieces the runtime keeps

1. **Sequence numbers**: every batch a deployment receives has the next
   number, for its whole life.
2. **The journal**: every batch exactly as given (references to the latest
   values seen, fills, order updates, timers, now), written **before** sending.
   ~60–120 B a batch (ticks are references into the tick archive); est. ~8 GB
   compressed a day for 2,000 strategies.
3. **Snapshots**: declared state + seq; every 100 batches, 30 s, and before a
   move.
4. **Intent ids and epochs**: id `deployment:batch:n`, same on every replay;
   each (re)start gets the next epoch; the OMS refuses a seen id or an old
   epoch.

## Walking (repair, day check, "what happened at 10:42?")

Find the batch range by time (journal is indexed by seq and time) → start a
fresh process of the same version from the snapshot just before → feed the
journal's batches with the sim venue as broker → every state hash should
match; the first batch whose decisions differ is "what went wrong". A stretch
the app never saw (a stall) gets batches built from 1-minute candles (4 ticks
a minute, as today). An ambiguous minute → **two processes from the same
snapshot**, one per reading; if they part, the owner chooses.

## Crashes

| What | Seen as | Then |
|---|---|---|
| Exception/panic | SDK sends Error and exits | restart from last snapshot, catch up |
| Process dies | socket closes / exit code | restart, catch up |
| Hang | no Done within budget | kill, restart |
| Host dies | leases expire (~5 s) | another host, epoch + 1, shipped snapshot + journal |
| Lost track of a leg | legs vs book after a failure and every 5 min | restart clean from the book (today's behaviour) |

Restart: new process, epoch + 1 → Hello with last snapshot → catch up on
journal batches, checking each state hash → send the in-flight batch, carry
on. Re-made orders have the same intent id and are refused. **Same error on
the same batch twice → quarantine** (no entries, no restart loop; the
platform's square-off, stops held by the OMS and kill switch still work).
Different errors → back-off 1, 2, 4, 8, then every 15 min. Reproduce with
`linck replay --deployment D --from 200 --to 250`.

Prototype results (Go and Python, same supervisor; `supervisor.py` and
`promises.py`): a strategy taking 40 ms per decision, fed a tick every 5 ms,
decided on prices up to 3.5 s old through a queue (474 ticks still waiting)
and ~44 ms old through conflation (500 skipped and counted); a zombie on a
3-minute-late feed had all 134 orders refused by the epoch check, while with
ids alone its 134 orders were placed and 128 of the real process's orders
refused. Earlier results: walk from snapshot 300
identical to live through 600; branch parts at batch 311; kill -9 at 458 →
restored from 400, 57 batches caught up in 14 ms (Go) / 38 ms (Python), 1
duplicate refused, orders identical; bug at 250 quarantined after the second
crash; Go = Python on all 600 batches.

## The latest tick, every hop

The owner's requirement: *"I can drop some ticks but I need to make sure the
tick the client gets is the latest one."* No transport gives this alone
(TCP-based gRPC/WS/SSE queue old ticks; NATS core can drop the newest or
disconnect; JetStream/Redpanda replay a backlog; Aeron slows the sender), so
it comes from **conflation** at every hop:

1. one slot per instrument per receiver, overwritten;
2. one batch in flight; when ready, the receiver gets the changed slots (by
   definition the latest) and a "skipped N" count;
3. per-instrument sequence numbers; drop anything ≤ last seen (no overtaking
   across reconnects);
4. a dropped link resumes from a snapshot of latest values, never a backlog.

"Latest" isn't "fresh": every value carries exchange time and age; stall
detection says "prices stopped". Measured: the slot + changed-set core does
3–19M updates/s on one core.

**Today's app breaks this** (queue of 1,000 batches per runner, oldest
dropped only when full; ticks up to 5 minutes old still processed). That's P0.

## Order of work from today's app

1. P0: latest-value slots + route ticks by instrument (days).
2. Journal, batch numbers, snapshots in today's runner (1–2 weeks); repair and
   day check then walk exactly what was seen, and a restart catches up.
3. Deterministic intent ids and epochs in the order path (days).
4. Protocol v1, Python process mode via a StrategyBase shim (runs beside
   in-process mode; decision logs must match for a week); then Go, Rust, TS
   SDKs with the conformance suite.
5. Rust supervisor behind the same protocol.

Open: snapshot cadence for big state; whether to quarantine on the first
repeat crash during 09:15–09:20; gVisor round-trip cost.
