// Flows: one thing at a time, hop by hop.
const CX = [40, 620, 1200], CW = 360, RY = [120, 330, 540, 750];
const out = (B, c, r, label, sub) => box(B, CX[c], RY[r], CW, 96, label, { sub, cls: 'out' });
const store = (B, c, r, label, sub) => box(B, CX[c], RY[r], CW, 96, label, { sub, cls: 'store' });
const svcAt = (B, c, r, name, lang, part, sub, nick) => node(B, CX[c], RY[r], CW, name, lang, part, { sub, nick });

// 20. A price.
flowScene('Flow: a price reaches a strategy', (L, B, M, S) => {
  const ex = out(B, 0, 0, 'Exchange (NSE)', 'a trade happens'), vd = out(B, 1, 0, 'Licensed vendor', 'sends every price');
  const ing = svcAt(B, 2, 0, 'md-ingest', 'Rust', 'trade', 'decode · map to our id · filter');
  const sq = svcAt(B, 2, 1, 'md-sequencer', 'Rust', 'trade', 'gives the next sequence number');
  const cs = svcAt(B, 1, 1, 'computed-streams', 'Rust', 'trade', 'updates bars, EMA, greeks');
  const ar = store(B, 2, 2, 'md.archive.v1', 'Redpanda, then the lake');
  const ag = svcAt(B, 1, 2, 'cache-agent', 'Rust', 'trade', 'on every runtime machine');
  const sh = store(B, 0, 2, 'shared memory', 'one slot per instrument');
  const sp = svcAt(B, 0, 3, 'supervisor', 'Rust', 'trade', 'builds the batch when ready', 'Iceberg');
  const st = out(B, 1, 3, 'Strategy', 'in its sandbox: decides');
  const e = [
    [link(L, ex, vd, 'b', { label: 'exchange feed' }), ex, vd],
    [link(L, vd, ing, 'b', { label: 'TCP / WebSocket' }), vd, ing],
    [link(L, ing, sq, 'b', { label: 'gRPC', lx: 70, ly: 4, anchor: 'start' }), ing, sq],
    [link(L, sq, cs, 'b', { label: 'gRPC stream' }), sq, cs],
    [link(L, sq, ar, 'b', { label: 'Redpanda', lx: 70, ly: 4, anchor: 'start' }), sq, ar],
    [link(L, sq, ag, 'b', { d: 'M1250 426C1250 500 1100 588 986 588', label: 'gRPC stream · latest only', at: 0.4, lx: 30, ly: -14 }), sq, ag],
    [link(L, cs, ag, 'b', { label: 'gRPC stream', lx: 70, ly: 4, anchor: 'start' }), cs, ag],
    [link(L, ag, sh, 'b', { label: 'writes the slot' }), ag, sh],
    [link(L, sh, sp, 'b', { label: 'memory read', lx: 70, ly: 4, anchor: 'start' }), sh, sp],
    [link(L, sp, st, 'b', { label: 'Unix socket' }), sp, st],
  ];
  const tg = G(B); T(tg, 1380, 800, 'p99 < 50 ms', 'big'); T(tg, 1380, 834, 'tick → intent, in a cell: a target', 'bs'); op(tg, 0);
  return { edges: e, extra: t => pop(tg, t, S.at[6] + 0.3) };
}, [
  ['Now the flows, one at a time. First, one price, from the exchange to a strategy.', { focus: 'all' }],
  ['A trade happens on the exchange. The licensed vendor sends the new price over its own TCP or WebSocket connection, and md-ingest decodes it into our 96-byte record, maps it to our instrument id, and runs the tick filter.', {}, [0, 1], 'tick'],
  ['Over gRPC, like every call between our services, the record reaches the sequencer, which gives it the next sequence number for that instrument. A copy goes to the archive topic on Redpanda, and from there to the lake.', {}, [2, 4], 'tick'],
  ['Then it streams out over gRPC, to computed streams, which updates the bars, the EMA and the greeks that depend on it, and to the cache agent on every runtime machine. Each stream keeps only the latest value for a reader that has fallen behind.', {}, [3, 6, 5], 'tick'],
  ['The cache agent writes the price into its slot in shared memory, over the last one.', {}, [7], 'tick'],
  ['When the strategy is ready, the supervisor reads the latest values from shared memory, builds the next batch, and sends it as protobuf over a Unix socket. A strategy that was busy simply sees the newest price.', {}, [8, 9], 'batch'],
  ['The target, from a tick to the strategy’s intent, is under 50 milliseconds at the 99th percentile, inside a cell. That is a design target, not yet a measurement.', {}],
]);

// 21. An order.
flowScene('Flow: an order, from decision to your screen', (L, B, M) => {
  const st = out(B, 0, 0, 'Strategy', 'answers its batch: an intent');
  const sp = svcAt(B, 1, 0, 'supervisor', 'Rust', 'trade', 'writes the intent first', 'Iceberg');
  const it = store(B, 2, 0, 'orders.intent.v1', 'Redpanda · keyed by account');
  const hd = svcAt(B, 2, 1, 'oms', 'Go', 'trade', 'dedupe · fence · risk · record', 'Heimdall');
  const kr = svcAt(B, 1, 1, 'broker-gw-kite', 'Go', 'trade', 'translate · tag · place', 'Kraken');
  const bk = out(B, 0, 1, 'Broker (Kite)', 'REST in, WebSocket out');
  const lg_ = store(B, 2, 2, 'ledger shard', 'order + outbox: one transaction');
  const ev = store(B, 1, 2, 'orders.event.v1', 'Redpanda, from the outbox');
  const rm = svcAt(B, 0, 2, 'read-models', 'Go', 'lens', 'writes ClickHouse tables');
  const lv = svcAt(B, 1, 3, 'live-gateway', 'Go', 'lens', 'latest value per screen');
  const sc = out(B, 0, 3, 'Your screen', 'the fill appears');
  const nt = svcAt(B, 2, 3, 'notifications', 'Go', 'platform', 'push to your phone');
  const e = [
    [link(L, st, sp, 'b', { label: 'Unix socket' }), st, sp],
    [link(L, sp, it, 'b', { label: 'Redpanda' }), sp, it],
    [link(L, it, hd, 'b', { label: 'reads in order', lx: 70, ly: 4, anchor: 'start' }), it, hd],
    [link(L, hd, lg_, 'b', { label: 'one transaction', lx: 70, ly: 4, anchor: 'start' }), hd, lg_],
    [link(L, hd, kr, 'g', { d1: -18, d2: -18, label: 'gRPC' }), hd, kr, 'g'],
    [link(L, kr, bk, 'g', { d1: -18, d2: -18, label: 'REST · fixed IP' }), kr, bk, 'g'],
    [link(L, bk, kr, 'g', { d1: 18, d2: 18, label: 'WebSocket', ly: 42 }), bk, kr, 'g'],
    [link(L, kr, hd, 'g', { d1: 18, d2: 18, label: 'order updates', ly: 42 }), kr, hd, 'g'],
    [link(L, lg_, ev, 'g', { label: 'outbox relay' }), lg_, ev, 'g'],
    [link(L, ev, rm, 'g', { label: 'consumer' }), ev, rm, 'g'],
    [link(L, ev, lv, 'g', { label: 'region · NATS live', lx: 70, ly: 4, anchor: 'start' }), ev, lv, 'g'],
    [link(L, lv, sc, 'g', { label: 'SSE' }), lv, sc, 'g'],
    [link(L, ev, nt, 'g', { d: 'M986 600C1100 600 1380 650 1380 744', label: 'consumer', at: 0.55, lx: 50, ly: 0 }), ev, nt, 'g'],
  ];
  return { edges: e, color: 'b' };
}, [
  ['Next, one order: from a strategy’s decision to a fill on your screen.', { focus: 'all' }],
  ['The strategy answers its batch with an intent, say a hedged basket of two legs. The supervisor writes it to orders.intent.v1 on Redpanda, keyed by account, before anything else happens, so it can’t be lost.', {}, [0, 1], 'intent'],
  ['Heimdall reads it. It checks the id for a duplicate and the epoch fence, runs the risk pipeline, and records the order and an outbox row in the ledger shard, in one Postgres transaction.', {}, [2, 3], 'intent'],
  ['It calls Kraken over gRPC. Kraken translates the order, tags it with our order id, and places it through the broker’s REST API, leaving through the cell’s fixed IP. For a paper order, the sim venue fills it instead.', {}, [4, 5], 'order'],
  ['The broker’s update comes back over its WebSocket. Kraken turns it into one ordered stream of order updates, and Heimdall records the fill in the journal, with its outbox row, in one transaction.', {}, [6, 7, 3], 'fill'],
  ['The relay publishes the fill to orders.event.v1. Read models write it into ClickHouse, for Geek mode and reports.', {}, [8, 9], 'fill'],
  ['The cell’s log is copied to the region, the fill reaches the live gateway as a live update on NATS, and it is pushed over SSE to your screen. Notifications sends a push to your phone.', {}, [10, 11, 12], 'fill'],
  ['The target, from intent to broker, is under 30 milliseconds at the 99th percentile. Again, a design target, not yet a measurement.', {}],
]);

// 22. A screen.
flowScene('Flow: opening a screen', (L, B, M) => {
  const rd = box(B, 620, 110, CW, 96, 'Redis', { sub: 'sessions · plan counters', cls: 'store' });
  const ct = node(B, 1200, 110, CW, 'control', 'Go', 'trade', { sub: 'deployment details' });
  const br = box(B, 40, 300, CW, 96, 'Browser', { sub: 'opens the Desk', cls: 'out' });
  const ed = node(B, 620, 300, CW, 'edge-gateway', 'Envoy + Go', 'platform', { sub: 'TLS · session · plan limit' });
  const pa = node(B, 1200, 300, CW, 'product-api', 'Python', 'lens', { sub: 'builds the answer' });
  const ch = box(B, 1200, 500, CW, 96, 'ClickHouse', { sub: 'charts, reports', cls: 'store' });
  const rp = box(B, 1200, 700, CW, 96, 'ledger replica', { sub: 'positions, read-only', cls: 'store' });
  const lv = node(B, 620, 560, CW, 'live-gateway', 'Go', 'lens', { sub: 'latest value per screen' });
  const na = box(B, 620, 750, CW, 96, 'NATS', { sub: 'live.<tenant>.<deployment>', cls: 'store' });
  const e = [
    [link(L, br, ed, 'b', { d1: -18, d2: -18, label: 'HTTPS · REST/JSON' }), br, ed],
    [link(L, ed, rd, 'b', { label: 'session?', lx: 70, ly: 4, anchor: 'start' }), ed, rd],
    [link(L, ed, pa, 'b', { d1: -18, d2: -18, label: 'REST/JSON' }), ed, pa],
    [link(L, pa, ct, 'b', { label: 'gRPC', lx: 70, ly: 4, anchor: 'start' }), pa, ct],
    [link(L, pa, ch, 'b', { label: 'native protocol', lx: 70, ly: 4, anchor: 'start' }), pa, ch],
    [link(L, pa, rp, 'b', { d: 'M1200 380C1150 380 1150 748 1194 748', label: 'Postgres', at: 0.5, lx: -14, ly: 4, anchor: 'end' }), pa, rp],
    [link(L, pa, ed, 'g', { d1: 18, d2: 18, label: 'JSON', ly: 42 }), pa, ed, 'g'],
    [link(L, ed, br, 'g', { d1: 18, d2: 18, label: 'JSON', ly: 42 }), ed, br, 'g'],
    [link(L, ed, lv, 'pu', { label: 'SSE', lx: 70, ly: 4, anchor: 'start' }), ed, lv, 'pu'],
    [link(L, na, lv, 'pu', { label: 'latest value', lx: 70, ly: 4, anchor: 'start' }), na, lv, 'pu'],
    [link(L, lv, br, 'pu', { d: 'M614 608C400 608 220 560 220 402', label: 'live updates, via the edge', at: 0.35, ly: -12 }), lv, br, 'pu'],
  ];
  return { edges: e };
}, [
  ['Opening a screen: the Desk, for example.', { focus: 'all' }],
  ['The browser asks the edge gateway over HTTPS: a REST request, with JSON. The edge ends TLS, checks the session cookie against the session kept in Redis, and counts the request against the plan’s limit.', {}, [0, 1], 'request'],
  ['It passes the request to the product API, which reads ClickHouse for the charts, the ledger’s read-only replica for positions, and asks control over gRPC for the deployments.', {}, [2, 4, 5, 3], 'request'],
  ['The answer goes back as JSON, and the screen draws.', {}, [6, 7], 'JSON'],
  ['The browser also keeps one SSE stream open, through the edge, to the live gateway. From then on, live P&L, positions and alerts arrive on it: the latest value, at most four times a second.', {}, [8, 9, 10], 'P&L'],
]);

// 23. Shipping a strategy.
flowScene('Flow: shipping a strategy', (L, B, M, S) => {
  const yo = out(B, 0, 0, 'You', 'linck push · linck deploy');
  const ed = svcAt(B, 1, 0, 'edge-gateway', 'Envoy + Go', 'platform', 'gRPC on port 443');
  const bd = svcAt(B, 2, 0, 'builder', 'Go', 'trade', 'starts a build job');
  const jb = box(B, CX[2], RY[1], CW, 96, 'a Kubernetes Job', { sub: 'gVisor · pinned image · certify', cls: 'proc' });
  const rg = store(B, 1, 1, 'artifact registry', 'signed: source, lockfile, manifest');
  const ct = svcAt(B, 0, 1, 'control', 'Go', 'trade', 'certified? within limits? place');
  const na = store(B, 0, 2, 'NATS', 'ctl.cell.<id>.*');
  const sp = svcAt(B, 1, 2, 'supervisor', 'Rust', 'trade', 'fetch · verify · start · lease', 'Iceberg');
  const et = store(B, 2, 2, 'etcd', 'the lease, with a new epoch');
  const sb = box(B, CX[1], RY[3], CW, 96, 'your strategy', { sub: 'a new sandbox; first batch', cls: 'key' });
  const e = [
    [link(L, yo, ed, 'b', { d1: -18, d2: -18, label: 'gRPC · push' }), yo, ed],
    [link(L, ed, bd, 'b', { label: 'gRPC' }), ed, bd],
    [link(L, bd, jb, 'b', { label: 'starts', lx: 70, ly: 4, anchor: 'start' }), bd, jb],
    [link(L, jb, rg, 'b', { label: 'signed (Sigstore)' }), jb, rg],
    [link(L, yo, ed, 'g', { d1: 18, d2: 18, label: 'Deploy', ly: 42 }), yo, ed, 'g'],
    [link(L, ed, ct, 'g', { d: 'M700 216C700 280 260 260 220 326', label: 'gRPC', at: 0.5, ly: -8 }), ed, ct, 'g'],
    [link(L, ct, na, 'g', { label: 'start command', lx: 70, ly: 4, anchor: 'start' }), ct, na, 'g'],
    [link(L, na, sp, 'g', { label: 'to the cell' }), na, sp, 'g'],
    [link(L, rg, sp, 'g', { label: 'fetch, verify', lx: 70, ly: 4, anchor: 'start' }), rg, sp, 'g'],
    [link(L, sp, et, 'g', { label: 'take the lease' }), sp, et, 'g'],
    [link(L, sp, sb, 'g', { label: 'start, subscribe', lx: 70, ly: 4, anchor: 'start' }), sp, sb, 'g'],
  ];
  const rk = rocket(M);
  const rp = line(L, 'M600 700C700 640 760 660 800 744', 'pu', { arrow: false }); op(rp, 0);
  const cf = burst(M, 800, 800, { seed: 9 });
  return { edges: e, extra: (t, rt) => { op(rp, 0); rk.go(rp, t, S.to[7] - 2.2, S.to[7] - 0.6, rt); cf.go(t, S.to[7] - 0.6); } };
}, [
  ['Shipping a strategy, from your laptop to running in a cell.', { focus: 'all' }],
  ['You run linck push. The command-line tool sends your source over gRPC, through the edge, to the builder.', {}, [0, 1], 'source'],
  ['The builder starts a Kubernetes Job in gVisor, with the pinned build image for your language. It builds, then certifies: the fault matrix, the determinism double-run, and the resource budget. That takes minutes.', {}, [2], 'build'],
  ['The passing artifact is signed with Sigstore and stored in the artifact registry: your source, its lockfile and its manifest. It is not a container image.', {}, [3], 'artifact'],
  ['You press Deploy, or run linck deploy. Control checks that this version is certified and within your limits, and its scheduler picks a machine with room and the right instruments.', {}, [4, 5], 'deploy'],
  ['Control sends the start command over NATS, on the subject for that cell.', {}, [6, 7], 'start'],
  ['The supervisor on that machine fetches the signed artifact and checks its signature, takes the lease in etcd with a new epoch, and starts the sandbox.', {}, [8, 9, 10], 'start'],
  ['The strategy subscribes to its instruments, and the first batch arrives. From here on, it is the price journey you saw before.', {}],
]);

// 24. When things break.
{
  const S = beats([
    ['Now, what happens when things break. Four cases.', { focus: 'all' }],
    ['A strategy crashes. The supervisor starts a new process with the next epoch, loads the last snapshot, and replays the journal since then, checking every state hash. Orders it makes again carry the same ids, so Heimdall refuses them as duplicates. The same crash on the same batch twice means quarantine: no new entries and no restart loop, while square-off, stops and the kill switch still work.', { focus: ['p0'] }],
    ['A machine dies. Its leases in etcd expire in about five seconds. The scheduler grants them to another machine with the epoch plus one, which loads the snapshot and journal shipped to S3 and catches prices up from its own cache, in under two seconds.', { focus: ['p1'] }],
    ['A zombie: the old copy didn’t really die, and keeps sending intents. Each carries the old epoch, so Heimdall’s fence refuses every one. In our prototype, measured: a zombie on a feed three minutes late had all 134 of its orders refused.', { focus: ['p2'] }],
    ['A broker goes down. Its Kraken deployment reports it; Heimdall stops new entries for that broker’s accounts, queues their exits, and alerts. Other brokers carry on. If a data vendor goes down, the other source already carries the instruments.', { focus: ['p3'] }],
  ]);
  scene({ title: 'Flow: when things break', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const pn = [[40, 116], [812, 116], [40, 494], [812, 494]].map(([x, y], i) => named('p' + i, area(B, x, y, 748, 360, ['A strategy crashes', 'A machine dies', 'A zombie', 'A broker goes down'][i], 'cell')));
    // 0: crash → restart from snapshot + journal → quarantine on the second identical crash
    const s0 = box(B, 70, 180, 220, 80, 'strategy', { sub: 'epoch 7' });
    const pf0 = puff(M, 180, 200);
    const s1 = box(B, 70, 300, 220, 80, 'strategy', { sub: 'epoch 8', cls: 'key' });
    const snap = box(B, 340, 180, 200, 80, 'last snapshot', { cls: 'chip' }), jr = box(B, 560, 180, 200, 80, 'journal since', { cls: 'chip' });
    const rl = [link(L, snap, s1, 'pu', { d: 'M440 260C440 300 360 340 294 340' }), link(L, jr, s1, 'pu', { d: 'M660 260C660 330 380 346 294 346' })];
    const q = stamp(M, 520, 380, 'Quarantine', 'r', { shake: 3 });
    const rlF = rl.map(l => stream(M, l, 'pu', 2, 1.3, 5));
    // 1: machine A dies; lease expires; B takes over
    const ma = box(B, 842, 180, 240, 110, 'machine A', { sub: 'lease · epoch 4' }), mb = box(B, 1290, 180, 240, 110, 'machine B', { sub: '', cls: 'key' });
    const mbT = T(B, 1410, 270, '', 'bs');
    const pf1 = puff(M, 960, 200);
    const tmr = T(B, 1186, 380, '', 'big'), tmrS = T(B, 1186, 420, '', 'bs');
    const s3 = box(B, 1290, 330, 240, 64, 'S3: snapshot + journal', { cls: 'store', lcls: 'cmp' });
    const etb = box(B, 842, 330, 240, 64, 'etcd: leases', { cls: 'store', lcls: 'cmp' });
    const hbA = link(L, ma, etb, 'g', { sides: ['b', 't'] }), hbB = link(L, etb, mb, 'g', { d: 'M1082 350C1180 350 1250 320 1300 296' }), s3B = link(L, s3, mb, 'pu', { sides: ['t', 'b'] });
    const hb = [stream(M, hbA, 'g', 1, 1.0, 6), stream(M, hbB, 'g', 1, 1.0, 6), stream(M, s3B, 'pu', 2, 1.0, 5)];
    // 2: zombie
    const gh = ghost(B, 440, 790);
    const zh = box(B, 70, 740, 260, 80, 'old copy', { sub: 'epoch 3' }), lv = box(B, 70, 560, 260, 80, 'real copy', { sub: 'epoch 4', cls: 'key' });
    const fence = box(B, 520, 600, 240, 160, 'Heimdall', { sub: 'fence: epoch ≥ 4', cls: 'key' });
    const zp = line(L, 'M330 780C420 780 440 700 516 700', 'r'), lp = line(L, 'M330 600C420 600 440 650 516 650', 'g');
    const zk = packet(M, 'epoch 3', 'r'), lk = packet(M, 'epoch 4', 'g');
    const zc = T(B, 420, 840, '', 'cl');
    // 3: broker down
    const krk = box(B, 842, 560, 220, 80, 'Kraken · kite', { cls: 'bad' }), kru = box(B, 842, 680, 220, 80, 'Kraken · upstox');
    const bkk = box(B, 1310, 560, 220, 80, 'Kite', { sub: 'down', cls: 'out' }), bku = box(B, 1310, 680, 220, 80, 'Upstox', { sub: 'up', cls: 'out' });
    const ku = link(L, kru, bku, 'g'), kk = link(L, krk, bkk, 'r', { dash: true });
    const kuF = stream(M, ku, 'g', 3, 1.2, 6);
    const xk = check(M, 1186, 600, false);
    const qd = T(B, 1186, 820, '', 'cl');
    return (t, rt) => {
      pops(pn, t, 0.2, 0.3);
      // crash
      const a = S.at[1];
      pop(s0, t, a + 0.3); pf0.go(t, a + 1.2); if (t > a + 1.2) op(s0, 1 - k(t, a + 1.2, a + 1.6)); quake(t, a + 1.2, 0.3, 3);
      pops([snap, jr], t, a + 2.0, 0.4); rl.forEach((l, i) => show(l, t, a + 2.8 + i * 0.4)); pop(s1, t, a + 3.6);
      q.go(t, S.to[1] - 2.0);
      rlF.forEach((f, i) => f(rt, t > a + 3.2 + i * 0.4 ? 0 : 1e9));
      // machine
      const b = S.at[2];
      pops([ma, mb, s3, etb], t, b + 0.3, 0.3);
      const took = b + (S.to[2] - b) * 0.6;
      show(hbA, t, b + 0.6); hb[0](rt, t > b + 0.8 && t < b + 1.2 ? 0 : 1e9);
      show(hbB, t, took); hb[1](rt, t > took ? 0 : 1e9); show(s3B, t, took); hb[2](rt, t > took ? 0 : 1e9); pf1.go(t, b + 1.2); if (t > b + 1.2) op(ma, 1 - 0.7 * k(t, b + 1.2, b + 1.6));
      countUp(tmr, t, b + 1.6, b + (S.to[2] - b) * 0.55, 0, 5, v => t > b + 1.5 ? `${v.toFixed(1)} s` : '');
      txt(tmrS, t > b + 1.5 ? (t > b + (S.to[2] - b) * 0.55 ? 'lease expired → machine B, epoch 5' : 'waiting for the lease to expire') : '');
      txt(mbT, t > b + (S.to[2] - b) * 0.6 ? 'lease · epoch 5 · resumed' : '');
      flash(mb, t, b + (S.to[2] - b) * 0.6, 1.2);
      // zombie
      const c = S.at[3];
      pops([lv, zh, fence], t, c + 0.3, 0.3); fade(gh, t, c + 0.6); gh.go(rt);
      show(zp, t, c + 1.2); show(lp, t, c + 1.2);
      const zz = t > c + 1.6;
      if (zz) { placeOn(zk, zp, (rt * 0.5) % 1); op(zk, 1); placeOn(lk, lp, (rt * 0.5 + 0.5) % 1); op(lk, 1); } else { op(zk, 0); op(lk, 0); }
      countUp(zc, t, c + 2.0, S.to[3] - 0.4, 0, 134, v => zz ? `old epoch refused: ${Math.round(v)} of 134 (measured)` : '');
      // broker
      const d = S.at[4];
      pops([krk, kru, bkk, bku], t, d + 0.3, 0.3); show(ku, t, d + 1.0); show(kk, t, d + 1.0); kuF(rt, t > d + 1.4 ? 0 : 1e9); pop(xk, t, d + 1.6);
      typeOn(qd, t, d + 2.4, S.to[4] - 0.5, 'Kite accounts: no new entries, exits queued, alert sent');
    };
  });
}

// 25. The end of the day.
{
  const S = beats([
    ['And the end of each trading day, in order.', { focus: 'all' }],
    ['By 15:20, the 15:15 exits are drained. By 15:35, zero positions are left unreconciled with the brokers.'],
    ['At 15:45 the day’s data is exported, and from then on releases may go out, one cell at a time.'],
    ['Integrity’s end-of-day chain runs as a Temporal workflow: the day check replays every deployment on the one-minute candles, and each difference becomes a repair case.'],
    ['The target: checks and repair cases ready by 16:30. Meanwhile the tick archive keeps three days on Redpanda before it lives in the lake, and the read models are already up to date.'],
  ]);
  scene({ title: 'Flow: the end of the day', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const x0 = 120, x1 = 1480, tx = h => x0 + (h - 15) / 1.75 * (x1 - x0);      // 15:00 → 16:45
    const ax = G(B); E('line', { x1: x0, x2: x1, y1: 470, y2: 470, class: 'axis' }, ax);
    const hm = h => `${Math.floor(h)}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`;
    for (let h = 15; h <= 16.75 + 1e-9; h += 0.25) { E('line', { x1: tx(h), x2: tx(h), y1: 462, y2: 478, class: 'axis' }, ax); T(ax, tx(h), 506, hm(h), 'bs'); }
    op(ax, 0);
    const ev = [[15.25, 15 + 20 / 60, 'exits drained', '15:15 exits done by 15:20', 'g', 0], [15 + 35 / 60, null, 'reconciled', 'zero unreconciled positions', 'g', 1],
      [15.75, 16.75, 'export · releases open', 'releases may go out, cell by cell', 'b', 2], [15.75, 16.5, 'day check → repair cases', 'Temporal workflow', 'pu', 3], [16.5, null, 'ready', 'checks and cases by 16:30', 'g', 4]];
    const items = ev.map(([a, b, l, s, col, row]) => {
      const g = G(B), y = 150 + row * 56;
      if (b) E('rect', { x: tx(a), y: y - 18, width: tx(b) - tx(a), height: 28, rx: 4, class: 'f-' + col, opacity: 0.3 }, g);
      E('circle', { cx: tx(a), cy: y - 4, r: 8, class: 'f-' + col }, g);
      E('line', { x1: tx(a), x2: tx(a), y1: y + 6, y2: 462, class: 'life' }, g);
      T(g, tx(a) + 16, y + 2, `${l}: ${s}`, 'cl', 'start');
      op(g, 0); return g;
    });
    const hand = E('line', { y1: 130, y2: 480, class: 'xline' }, M); op(hand, 0);
    const clock = T(M, 0, 120, '', 'sl');
    const arch = box(B, 120, 600, 1360, 90, 'tick archive: md.archive.v1 keeps 3 days on Redpanda → Parquet in the S3 lake', { cls: 'store', lcls: 'cl' });
    const rmx = box(B, 120, 720, 1360, 90, 'read models: already current, built from the events as they happened', { cls: 'store', lcls: 'cl' });
    const steps = [1, 1, 2, 3, 4];
    return (t, rt) => {
      fade(ax, t, 0.2);
      const h = lerp(15.05, 16.7, k(t, 0.4, S.dur - 0.2));
      op(hand, 1); hand.setAttribute('x1', tx(h)); hand.setAttribute('x2', tx(h));
      txt(clock, hm(h)); clock.setAttribute('x', tx(h)); op(clock, 1);
      items.forEach((g, i) => pop(g, t, i < 2 ? S.at[1] + 0.3 + i * (S.to[1] - S.at[1]) / 2 : S.at[i] + 0.3));
      pop(arch, t, S.at[4] + 1.2); pop(rmx, t, S.at[4] + 2.4);
    };
  });
}
