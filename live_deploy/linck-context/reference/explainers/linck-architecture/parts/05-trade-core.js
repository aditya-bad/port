// 15. Iceberg: the strategy runtime.
{
  const S = beats([
    ['Iceberg is the strategy runtime: simple on top, deep underneath. On each runtime machine, one supervisor, written in Rust, runs thousands of strategies. It runs as a DaemonSet.', { focus: 'all' }],
    ['Each strategy runs in its own sandbox. Most are processes inside gVisor, a kernel in user space from Google that stands between the code and the real machine. So a strategy can be written with any SDK: Python, Go, Rust or TypeScript.', { spot: ['gv'] }],
    ['Python strategies start by forking a zygote: a Python process with the SDK already loaded. Each then costs about 2 megabytes instead of 52, measured. WASM strategies run in Wasmtime, and remote strategies run on the user’s own machine, over a gRPC stream.', { spot: ['sbx'] }],
    ['When a strategy is ready, the batch builder makes its next batch from the latest values in shared memory, plus every fill, order update and timer waiting for it.', {}],
    ['The protocol codec sends the batch as a protobuf message over a Unix socket. The strategy answers with commands: orders, cancels, timers, and what it decided and why.', { focus: 'all' }],
    ['The budget timer gives each batch 50 milliseconds before a warning, and one second before the strategy is killed and restarted.', { spot: ['budget'] }],
    ['The journal writes each answered batch to a write-ahead log on the machine’s local NVMe disk, shipped to S3 every second. The snapshotter saves the strategy’s state every 100 batches, every 30 seconds, and before a move.', { spot: ['wal', 's3'] }],
    ['The lease keeper holds this machine’s lease on each deployment in etcd. The intent writer puts every order intent on the Redpanda topic orders.intent.v1 before anything else happens, and decisions go to strategy.decision.v1.', { spot: ['etcd', 'topics'] }],
    ['Strategies must be deterministic: the only clock is ctx.now, the only randomness is ctx.random, and Python’s hash seed is fixed. Replaying a day gives the same decisions, and certification checks it by comparing hashes.', { spot: ['det'] }],
  ]);
  scene({ title: 'Iceberg: the strategy runtime', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const ice = named('ice', svc(B, 40, 116, 760, 744, 'supervisor', { nick: 'Iceberg', lang: 'Rust', kind: 'DaemonSet', part: 'trade',
      does: 'One per runtime machine. Runs thousands of sandboxed strategies.',
      comps: ['batch builder', 'sandbox manager', 'protocol codec', 'budget timer', 'journal (WAL)', 'snapshotter', 'lease keeper', 'intent writer', 'decision writer', 'quarantine'], cols: 2, ch: 40 }));
    const c = ice.comp;
    named('bb', c['batch builder']);
    const budget = named('budget', box(ice, 64, 466, 712, 150, 'budget per batch', { cls: 'frame', top: true, lcls: 'cl' })); op(budget, 1);
    const segs = [['under 50 ms: fine', 'g'], ['50 ms to 1 s: warned', 'wt'], ['over 1 s: killed, restarted', 'r']];
    segs.forEach(([s, col], i) => { E('rect', { x: 90 + i * 224, y: 520, width: 216, height: 30, class: 'f-' + col, opacity: 0.25 }, budget); T(budget, 198 + i * 224, 576, s, 'bs'); });
    const fillB = E('rect', { x: 90, y: 520, width: 0, height: 30, class: 'f-ink', opacity: 0.55 }, M);
    const det = named('det', box(ice, 64, 640, 712, 196, 'determinism', { cls: 'frame', top: true, lcls: 'cl' })); op(det, 1);
    [['ctx.now', 'the only clock: the batch’s time'], ['ctx.random', 'the only randomness, seeded'], ['hash seed', 'PYTHONHASHSEED fixed'], ['no', 'network, files or threads']]
      .forEach(([a, b], i) => { T(det, 90, 704 + i * 34, a, 'mono', 'start'); T(det, 290, 704 + i * 34, b, 'cl', 'start'); });
    const shm = named('shm', box(B, 830, 116, 730, 60, 'tick cache: shared memory, from the cache agent', { cls: 'out', lcls: 'cl' }));
    const sbx = named('sbx', area(B, 830, 196, 730, 344, 'sandboxes', 'cell'));
    const gv = named('gv', box(B, 850, 244, 340, 280, 'gVisor process', { top: true }));
    T(gv, 1020, 300, 'Python · Go · Rust · TypeScript', 'bs');
    const zyg = G(B); E('circle', { cx: 920, cy: 400, r: 30, class: 'f-y' }, zyg); T(zyg, 920, 406, 'zygote', 'bt'); op(zyg, 0);
    const kids = [0, 1, 2, 3, 4].map(i => { const g = G(M); E('circle', { cx: 0, cy: 0, r: 16, class: 'f-g' }, g); T(g, 0, 5, 'S', 'pt'); op(g, 0); return g; });
    const memT = T(B, 1020, 500, '', 'bs');
    const wa = box(B, 1206, 244, 160, 280, 'Wasmtime', { sub: 'WASM' });
    const rm = box(B, 1382, 244, 158, 280, 'remote', { sub: 'gRPC stream' });
    const wal = named('wal', db(B, 830, 570, 220, 120, 'local WAL', 'NVMe, each batch'));
    const s3 = named('s3', db(B, 1100, 570, 170, 120, 'S3', 'every 1 s'));
    const etcd = named('etcd', db(B, 1320, 570, 240, 120, 'etcd', 'leases, epochs'));
    const t1 = topic(B, 830, 722, 730, 'orders.intent.v1 · Redpanda', 'b', { h: 40, lcls: 'tlab' });
    const t2 = topic(B, 830, 782, 730, 'strategy.decision.v1 · Redpanda', 'b', { h: 40, lcls: 'tlab' });
    named('topics', bounds(G(B), 830, 722, 730, 100));
    const batch = packet(M, 'batch', 'b'), cmd = packet(M, 'commands', 'g');
    const bPath = route(M, [[c['batch builder'].R + 50, 186], [1020, 186], [1020, 250]]);
    const cPath = route(M, [[1020, 250], [1020, 188], [840, 188]]);
    const shmF = stream(M, route(M, [[830, 146], [c['batch builder'].R + 12, c['batch builder'].cy]]), 'b', 3, 1.0, 5);
    const walL = link(L, c['journal (WAL)'], wal, 'pu', { sides: ['r', 'l'], d: `M${c['journal (WAL)'].R} ${c['journal (WAL)'].cy}C820 ${c['journal (WAL)'].cy} 790 630 826 630` });
    const s3L = link(L, wal, s3, 'pu'), etL = link(L, c['lease keeper'], etcd, 'g', { d: `M${c['lease keeper'].R} ${c['lease keeper'].cy}C${c['lease keeper'].R + 40} 560 1440 540 1440 566` });
    const inL = link(L, c['intent writer'], t1, 'b', { d: `M${c['intent writer'].R} ${c['intent writer'].cy}C820 ${c['intent writer'].cy} 790 742 826 742` });
    const dcL = link(L, c['decision writer'], t2, 'b', { d: `M${c['decision writer'].R} ${c['decision writer'].cy}C810 ${c['decision writer'].cy} 790 802 826 802` });
    const fl = { wal: stream(M, walL, 'pu', 2, 1.0, 5), s3: stream(M, s3L, 'pu', 1, 1.0, 6), et: stream(M, etL, 'g', 1, 2.0, 5), in: stream(M, inL, 'b', 2, 1.4, 5), dc: stream(M, dcL, 'b', 2, 1.8, 5),
      t1: stream(M, t1.pipe, 'b', 3, 1.6, 5), t2: stream(M, t2.pipe, 'b', 3, 1.8, 5) };
    return (t, rt) => {
      pop(ice, t, 0.2); pops([shm, sbx, gv, wa, rm, wal, s3, etcd, t1, t2], t, 1.0, (S.to[0] - 1.6) / 10);
      walk(['sandbox manager'].map(n => c[n]), t, S.at[1], S.to[1]);
      // the zygote forks strategies, each a few MB
      fade(zyg, t, S.at[2] + 0.3);
      kids.forEach((g, i) => { const a = S.at[2] + 0.8 + i * (S.to[2] - S.at[2] - 1.4) / 5, f = ke(t, a, a + 0.6);
        op(g, f > 0.35 ? 1 : 0); tr(g, lerp(920, 980 + (i % 3) * 70, f), lerp(400, 360 + Math.floor(i / 3) * 70 + (i % 3 === 1 ? 20 : 0), f)); });
      countUp(memT, t, S.at[2] + 0.8, S.to[2] - 0.6, 0, 5, v => v > 0 ? `${Math.round(v)} strategies × ~2 MB each (measured)` : '');
      walk([c['batch builder']], t, S.at[3] + 0.3, S.to[3]);
      shmF(rt, t > 1.6 ? 0 : 1e9);
      travel(batch, bPath, t, S.at[3] + 1.0, S.to[3] - 0.3, true);
      if (t > S.to[3] - 0.3) op(batch, 0);
      walk([c['protocol codec']], t, S.at[4], S.at[4] + 1.5);
      travel(cmd, cPath, t, S.at[4] + 1.6, S.to[4] - 0.3, true);
      if (t > S.to[4] - 0.3 && t < S.at[5]) { op(cmd, 1); bob(cmd._in, rt, 2); } else if (t >= S.at[5]) op(cmd, 0);
      walk([c['budget timer']], t, S.at[5], S.to[5]);
      const ms = t > S.at[5] + 0.3 && t < S.to[5] ? (rt % 3) / 3 : 0;      // a batch taking longer and longer
      fillB.setAttribute('width', (ms * 672).toFixed(1)); op(fillB, ms > 0 ? 0.55 : 0);
      walk([c['journal (WAL)'], c['snapshotter']], t, S.at[6], S.to[6]);
      [walL, s3L].forEach((l, i) => show(l, t, S.at[6] + 0.4 + i * 0.8)); fl.wal(rt, t > S.at[6] + 1.0 ? 0 : 1e9); fl.s3(rt, t > S.at[6] + 1.8 ? 0 : 1e9);
      walk([c['lease keeper'], c['intent writer'], c['decision writer']], t, S.at[7], S.to[7]);
      show(etL, t, S.at[7] + 0.4); show(inL, t, S.at[7] + 1.6); show(dcL, t, S.to[7] - 2.0);
      fl.et(rt, t > S.at[7] + 1.0 ? 0 : 1e9); fl.in(rt, t > S.at[7] + 2.2 ? 0 : 1e9); fl.dc(rt, t > S.to[7] - 1.4 ? 0 : 1e9);
      fl.t1(rt, t > S.at[7] + 2.6 ? 0 : 1e9); fl.t2(rt, t > S.to[7] - 1.0 ? 0 : 1e9);
    };
  });
}

// 16. Heimdall: orders and risk.
{
  const S = beats([
    ['Heimdall, the gatekeeper, is orders and risk, in Go. It is the only thing that writes orders, fills, positions and money. It runs as a StatefulSet of three, each copy owning a share of the accounts.', { focus: 'all' }],
    ['Its intake reads intents from the log. Every intent has an id: the deployment, the batch, and a number, the same on every replay. The id is unique in the database, so a repeated intent finds its first order instead of making a second.', {}],
    ['The epoch fence refuses any intent whose epoch is older than the deployment’s current lease: two machines can never trade one deployment.', {}],
    ['Then the risk pipeline, in the same process, in under a millisecond. Static checks: lot size, tick size, price band, freeze quantity, market hours. Deployment checks: its lot and entry limits, and its own kill switch.', { focus: ['risk'], spot: ['risk'] }],
    ['Account checks: margin, the daily loss limit, and the order rate. Global checks: the tenant’s and the platform’s kill switches, and rules written in CEL, a small expression language from Google. One rule overrides: an exit is never blocked by cash or margin, only by the static checks.', { spot: ['risk'] }],
    ['The basket planner places trades with several legs. Hedge first, the default, places the buy legs and waits for their fills before it sells; simultaneous sends every leg at once, for exits. Every leg is a limit order with price protection.', { focus: 'all' }],
    ['Each order moves through a state machine: new, risk checked, routed, open, partly filled, filled; or rejected, cancelled, or expired.', { spot: ['sm'] }],
    ['The ledger writer records every change in the cell’s Postgres ledger shard as double entry: each fill moves cash, position value and charges in lines that sum to zero. Charges and slippage are lines of their own, and positions and cash are worked out from the journal.', { spot: ['ledger'] }],
    ['In the same transaction, it writes an outbox row. A relay publishes the outbox to Redpanda, so nothing is ever published without being saved, or saved without being published.', { spot: ['outbox'] }],
    ['Every few minutes the reconciler compares positions with the broker’s; the broker wins, and any difference becomes an issue. And in shadow mode, every order is mirrored to the sim venue too, and both fills land in shadow fills, to compare.', {}],
  ]);
  scene({ title: 'Heimdall: orders and risk', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const hd = named('hd', svc(B, 40, 116, 760, 744, 'oms', { nick: 'Heimdall', lang: 'Go', kind: 'StatefulSet · 3, by account', part: 'trade',
      does: 'The only writer of orders, fills, positions and the ledger.',
      comps: ['intake', 'duplicate check', 'epoch fence', 'risk pipeline', 'basket planner', 'order state machine', 'ledger writer', 'outbox relay', 'reconciler', 'shadow mirror'], cols: 2, ch: 40 }));
    const c = hd.comp;
    named('in1', c['intake']); named('dup', c['duplicate check']); named('fence', c['epoch fence']); named('plan', c['basket planner']);
    named('recon', bounds(G(B), c['reconciler'].X, c['reconciler'].Y, c['shadow mirror'].R - c['reconciler'].X, 40));
    const risk = named('risk', box(hd, 64, 460, 712, 380, 'risk pipeline · in process · under 1 ms', { cls: 'frame', top: true, lcls: 'cl' })); op(risk, 1);
    const gates = [['static', 'lot · tick size · price band · freeze qty · hours'], ['deployment', 'max lots · open legs · entries a day · its kill switch'],
      ['account', 'margin · daily loss limit · order rate (token bucket)'], ['global', 'tenant and platform kill switches · CEL rules']]
      .map(([n, s], i) => { const b = box(hd, 84, 512 + i * 66, 672, 56, '', { cls: 'chip' }); T(b, 100, 545 + i * 66, n, 'cl', 'start'); T(b, 220, 545 + i * 66, s, 'bs', 'start'); op(b, 1); return b; });
    const exitT = T(hd, 420, 800, 'exits: never blocked by cash or margin', 'okl'); op(exitT, 0);
    const in2 = named('in2', topic(B, 830, 116, 730, 'orders.intent.v1 · id = deployment:batch:n · epoch', 'b', { h: 40, lcls: 'tlab' }));
    const smA = named('sm', area(B, 830, 176, 730, 270, 'order states', 'cell'));
    const st = (x, y, s, cls = 'chip') => box(B, x, y, 130, 40, s, { cls, lcls: 'cmp' });
    const sN = [st(850, 222, 'new'), st(990, 222, 'risk checked'), st(1130, 222, 'routed'), st(1270, 222, 'open'), st(1270, 300, 'partly filled'), st(1410, 300, 'filled', 'chip key')];
    const sX = [st(990, 380, 'rejected', 'chip bad'), st(1270, 380, 'cancelled'), st(1410, 380, 'expired')];
    const sl = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5]].map(([a, b]) => link(L, sN[a], sN[b], 'ink'));
    sl.push(link(L, sN[3], sN[5], 'ink', { d: `M${sN[3].R} 242C1380 242 1475 250 1475 296` }));
    const ledger = named('ledger', area(B, 830, 466, 730, 220, 'ledger shard · Postgres · double entry', 'cell'));
    const rows = [['fill · position value', '+ 2,450.00'], ['fill · cash', '− 2,474.50'], ['charges', '+ 24.50'], ['sum per fill', '= 0']];
    const rowsG = rows.map(([a, b], i) => { const g = G(B); T(g, 860, 530 + i * 34, a, 'mono', 'start'); T(g, 1400, 530 + i * 34, b, 'sv', 'end'); op(g, 0); return g; });
    T(ledger, 1540, 668, 'illustrative numbers', 'bt', 'end');
    const outbox = named('outbox', area(B, 830, 706, 730, 154, 'outbox → relay → Redpanda', 'cell'));
    const ot = [topic(B, 850, 750, 690, 'orders.event.v1', 'b', { h: 34, lcls: 'tlab' }), topic(B, 850, 800, 690, 'ledger.journal.v1', 'b', { h: 34, lcls: 'tlab' })];
    const intent = packet(M, 'intent', 'b'), dupP = packet(M, 'same id', 'r');
    const iPath = route(M, [[812, c['intake'].B + 6], [c['intake'].cx + 60, c['intake'].B + 6]]);
    const inL = line(L, `M900 156C900 200 860 ${c['intake'].B + 6} 812 ${c['intake'].B + 6}`, 'b', { dash: true });
    const inF = [stream(M, in2.pipe, 'b', 4, 1.8, 5), stream(M, inL, 'b', 2, 1.2, 5)];
    const no = stamp(M, 420, 680, 'Duplicate', 'r', { shake: 4 });
    const old = packet(M, 'epoch 3, now 4', 'r');
    const no2 = stamp(M, 420, 680, 'Refused', 'r', { shake: 4 });
    const legs = [packet(M, 'buy leg', 'g'), packet(M, 'sell leg', 'wt')];
    const legP = route(M, [[c['basket planner'].R + 40, c['basket planner'].B + 6], [812, c['basket planner'].B + 6], [812, 282], [846, 282]]);
    const fls = ot.map((x, i) => stream(M, x.pipe, 'b', 3, 1.6 + i * 0.3, 5));
    const tokB = T(M, 1000, 860, '', 'bs');
    return (t, rt) => {
      pop(hd, t, 0.2); pops([in2, smA, ...sN, ...sX, ledger, outbox, ...ot], t, 1.0, (S.to[0] - 1.6) / 16);
      sl.forEach((l, i) => show(l, t, 1.6 + i * 0.4));
      show(inL, t, 2.0); inF.forEach(f => f(rt, t > 2.4 ? 0 : 1e9));
      travel(intent, iPath, t, S.at[1] + 0.3, S.at[1] + 2.2, true); if (t > S.at[3]) op(intent, 0);
      walk([c['intake'], c['duplicate check']], t, S.at[1] + 1.0, S.to[1]);
      travel(dupP, iPath, t, S.to[1] - 3.2, S.to[1] - 1.6, false);
      no.go(t, S.to[1] - 1.6); if (t > S.at[2]) op(no, 0);
      walk([c['epoch fence']], t, S.at[2], S.to[2]);
      travel(old, iPath, t, S.at[2] + 0.3, S.at[2] + 1.6, false);
      no2.go(t, S.at[2] + 1.6); if (t > S.at[3]) op(no2, 0);
      gates.forEach((g, i) => fade(g, t, S.at[3] + 0.2 + i * 0.2));
      walk(gates.slice(0, 2), t, S.at[3] + 0.6, S.to[3]);
      walk(gates.slice(2), t, S.at[4] + 0.3, S.at[4] + (S.to[4] - S.at[4]) * 0.7);
      pop(exitT, t, S.at[4] + (S.to[4] - S.at[4]) * 0.72);
      legs.forEach((l, i) => travel(l, legP, t, S.at[5] + 0.6 + i * (S.to[5] - S.at[5]) * 0.45, S.at[5] + 2.4 + i * (S.to[5] - S.at[5]) * 0.45));
      walk([...sN.slice(0, 4), sN[4], sN[5]], t, S.at[6] + 0.3, S.to[6] - 1.4); walk(sX, t, S.to[6] - 1.4, S.to[6]);
      rowsG.forEach((g, i) => pop(g, t, S.at[7] + 0.6 + i * (S.to[7] - S.at[7] - 1) / 4));
      walk([c['ledger writer']], t, S.at[7], S.to[7]);
      walk([c['outbox relay']], t, S.at[8], S.to[8]); fls.forEach(f => f(rt, t > S.at[8] + 0.4 ? 0 : 1e9));
      walk([c['reconciler'], c['shadow mirror']], t, S.at[9] + 0.3, S.to[9]);
    };
  });
}

// 17. Kraken: the broker gateway.
{
  const S = beats([
    ['Kraken is the broker gateway, in Go: one code base, with an arm for each broker. The same image is deployed once per broker in each cell, started with a flag such as broker equals kite, so one broker’s outage, limits or IP rules never touch another.', { focus: 'all' }],
    ['Logins and tokens: each broker’s login flow and its daily refresh. Tokens are sealed in Vault, and opened only in this service’s memory.', {}],
    ['The symbol map, loaded from refdata before 08:30, turns our instrument id into the broker’s token and trading symbol, and the translator turns our order types into the broker’s.', {}],
    ['An order above the exchange’s freeze quantity is sliced into child orders under the limit. And our order id goes in the broker’s tag field, so a retry finds the first attempt instead of placing twice.', { spot: ['slice'] }],
    ['A token bucket per app key and per account keeps us under each broker’s published rate limits.', {}],
    ['The broker’s WebSocket or postbacks become one ordered stream of order updates back to Heimdall. On reconnect, it asks the broker for every order tagged today, and reconciles before anything new goes out.', {}],
    ['Each adapter declares its broker’s capabilities, such as basket margin or modify, and Heimdall plans around them. Most adapters run inside the service. A broker whose SDK exists only in Python runs as a sidecar in the same pod, about 60 microseconds away, measured.', { spot: ['arms'] }],
    ['Orders leave the cell through NAT gateways with fixed IP addresses, registered with each broker.', { spot: ['nat'] }],
  ]);
  scene({ title: 'Kraken: the broker gateway', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const kr = named('kr', svc(B, 40, 116, 760, 744, 'broker-gw-<broker>', { nick: 'Kraken', lang: 'Go', kind: 'Deployment per broker', part: 'trade',
      does: 'Speaks each broker’s API behind one contract. One deployment per broker, same image.',
      comps: ['login & tokens', 'symbol map', 'order translator', 'freeze slicer', 'tag: our order id', 'rate limiter', 'update stream', 'reconnect reconcile', 'capabilities', 'adapter'], cols: 2, ch: 40 }));
    const c = kr.comp;
    named('login', c['login & tokens']); named('sym', c['symbol map']); named('tr', c['order translator']); named('rate', c['rate limiter']);
    named('upd', bounds(G(B), c['update stream'].X, c['update stream'].Y, c['reconnect reconcile'].R - c['update stream'].X, 40));
    const flag = T(kr, 420, 500, '', 'mono');
    const slice = named('slice', box(kr, 64, 540, 712, 300, 'one big order, sliced', { cls: 'frame', top: true, lcls: 'cl' })); op(slice, 1);
    const big = packet(M, 'qty above freeze limit', 'b', { w: 300 });
    const kids = [0, 1, 2].map(i => packet(M, `child ${i + 1} · tag #8812`, 'g', { w: 210 }));
    const vault = named('vault', db(B, 830, 116, 240, 110, 'Vault', 'sealed tokens'));
    const body = G(B, 'lf-ghost'); E('ellipse', { cx: 1180, cy: 330, rx: 86, ry: 70, class: 'body' }, body); T(body, 1180, 324, 'Kraken', 'cl'); T(body, 1180, 346, 'one image', 'bs'); op(body, 0);
    const brokers = [['Kite', 'in process'], ['Upstox', 'in process'], ['Angel One', 'in process'], ['Dhan', 'sidecar · Python'], ['FIX', 'institutions, later']];
    const bx = brokers.map(([n, s], i) => box(B, 1340, 130 + i * 92, 220, 76, n, { sub: s, cls: i === 3 ? 'chip' : 'out' }));
    named('arms', bounds(G(B), 1090, 126, 470, 450));
    const arms = bx.map((b, i) => line(L, `M1250 ${300 + i * 12}C1290 ${300 + i * 12} 1300 ${b.cy} 1336 ${b.cy}`, 'ink', { arrow: false }));
    const nat = named('nat', box(B, 830, 620, 330, 100, 'NAT gateways', { sub: '2 fixed IPs per cell', cls: 'store' }));
    const brk = box(B, 1230, 620, 330, 100, 'brokers', { sub: 'allow only registered IPs', cls: 'out' });
    named('nat', bounds(G(B), 830, 600, 730, 140));
    const nl = link(L, nat, brk, 'g');
    const nf = stream(M, nl, 'g', 3, 1.2, 6);
    const vl = link(L, vault, c['login & tokens'], 'pu', { d: `M830 171C800 171 800 ${c['login & tokens'].cy} ${c['login & tokens'].R + 6} ${c['login & tokens'].cy}` });
    const keyF = stream(M, vl, 'pu', 2, 1.6, 5);
    const upd = stream(M, route(M, [[1336, 168], [1250, 168], [c['update stream'].R + 20, c['update stream'].cy]]), 'g', 3, 1.4, 6);
    const sc = T(B, 1450, 600, '', 'bs');
    return (t, rt) => {
      pop(kr, t, 0.2); fade(body, t, 1.0); pops(bx, t, 1.4, 0.3);
      arms.forEach((a, i) => { reveal(a, t, 1.6 + i * 0.3, 2.2 + i * 0.3); if (t > 2.4) look(a, { rot: Math.sin(rt * 1.8 + i) * 2 }); });
      pops([vault, nat, brk], t, 2.0, 0.4);
      typeOn(flag, t, S.at[0] + 4, S.to[0] - 0.4, '--broker=kite', rt);
      show(vl, t, S.at[1] + 0.4);
      keyF(rt, t > S.at[1] + 1.0 ? 0 : 1e9);
      walk([c['login & tokens']], t, S.at[1], S.to[1]);
      walk([c['symbol map'], c['order translator']], t, S.at[2] + 0.3, S.to[2]);
      // one order becomes three, each tagged with our order id
      const a = S.at[3] + 0.4, m = S.at[3] + (S.to[3] - S.at[3]) * 0.4;
      if (t >= a && t < m) { op(big, 1); tr(big, lerp(240, 420, ke(t, a, m - 0.3)), 640); } else op(big, 0);
      kids.forEach((kd, i) => { if (t >= m) { op(kd, 1); tr(kd, lerp(420, 170 + i * 240, ke(t, m, m + 0.8)), lerp(640, 720 + (i % 2) * 50, ke(t, m, m + 0.8))); } else op(kd, 0); });
      if (t > S.at[4]) kids.forEach(kd => op(kd, 1 - k(t, S.at[4], S.at[4] + 0.4)));
      walk([c['freeze slicer'], c['tag: our order id']], t, S.at[3] + 0.3, S.to[3]);
      walk([c['rate limiter']], t, S.at[4], S.to[4]);
      upd(rt, t > S.at[5] + 0.3 ? 0 : 1e9);
      walk([c['update stream'], c['reconnect reconcile']], t, S.at[5] + 0.3, S.to[5]);
      walk([c['capabilities'], c['adapter']], t, S.at[6] + 0.3, S.to[6]);
      typeOn(sc, t, S.to[6] - 3, S.to[6] - 0.3, '~60 µs over a Unix socket');
      show(nl, t, S.at[7] + 0.3); nf(rt, t > S.at[7] + 0.8 ? 0 : 1e9);
    };
  });
}

// 18. The sim venue and the backtest farm.
{
  const S = beats([
    ['The sim venue, in Rust, is a pretend broker. It fills paper and shadow orders from real market depth, with models for queue position and latency.', { focus: 'all', spot: ['sim'] }],
    ['It speaks the same contract as Kraken, so Heimdall can’t tell paper from live. It runs two to six copies, so paper load never slows a live order.', { spot: ['sim'] }],
    ['Its fill model lives in linck-core, a shared Rust library with a binding for each SDK language, so live, backtests and local runs compute the same fills, the same indicators, the same everything.', { spot: ['core'] }],
    ['The backtest farm runs the same runtime and the same sim venue in batch, over the lake: backtests, parameter sweeps, and replays. Each backtest is a Kubernetes Job.', { spot: ['farm'] }],
    ['It runs on spot machines: spare AWS capacity at a fraction of the price, which Amazon can take back. Karpenter, the node autoscaler, adds machines as the queue grows: from nothing to 500 virtual CPUs in minutes.', { spot: ['farm'] }],
  ]);
  scene({ title: 'The sim venue and the backtest farm', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const sim = named('sim', svc(B, 40, 116, 748, 520, 'sim-venue', { lang: 'Rust', kind: 'Deployment · 2–6', part: 'trade',
      does: 'A pretend broker: fills paper and shadow orders from market depth.', comps: ['simulated books', 'queue position', 'latency model', 'fill model', 'BrokerAdapter contract'], cols: 3 }));
    // a small order book: our order joins the queue at the best bid and moves up as trades eat it
    const lv = [['24,501.00', 'ask', 380], ['24,500.50', 'ask', 220], ['24,500.00', 'bid', 300], ['24,499.50', 'bid', 260]];
    const bk = lv.map(([pr, side, q], i) => { const g = G(B); E('rect', { x: 70, y: 330 + i * 52, width: 680, height: 46, class: side === 'ask' ? 'f-r' : 'f-g', opacity: 0.12 }, g); T(g, 90, 360 + i * 52, pr, 'sv', 'start'); T(g, 260, 360 + i * 52, side, 'bs', 'start'); op(g, 0); return g; });
    const queue = Array.from({ length: 6 }, (_, i) => { const r = E('rect', { y: 440, width: 54, height: 26, rx: 3, class: 'f-ink', opacity: 0.35 }, M); op(r, 0); return r; });
    const ours = packet(M, 'ours', 'b');
    const qT = T(B, 410, 560, '', 'cl');
    const farm = named('farm', svc(B, 812, 116, 748, 520, 'backtest-farm', { lang: 'Rust', kind: 'a Job per backtest · spot', part: 'trade',
      does: 'The runtime and the sim venue in batch, over the lake.', comps: ['lake reader', 'runtime, in batch', 'sim venue library', 'results'], cols: 2 }));
    const lake = db(B, 840, 330, 200, 110, 'S3 lake', 'ticks, chains');
    const machines = Array.from({ length: 24 }, (_, i) => { const r = E('rect', { x: 1080 + (i % 8) * 56, y: 330 + Math.floor(i / 8) * 56, width: 46, height: 46, rx: 4, class: 'f-wt', opacity: 0.8 }, M); op(r, 0); return r; });
    const vc = T(B, 1300, 540, '', 'big');
    T(farm, 1300, 580, 'vCPU, scaled by queue depth (Karpenter)', 'bs');
    const core = named('core', box(B, 40, 666, 1520, 190, '', { cls: 'frame key' }));
    T(core, 70, 706, 'linck-core · a Rust library', 'bigs', 'start'); T(core, 70, 734, 'indicators · the fill model · the protocol codec · determinism helpers', 'bs', 'start');
    const users = ['strategy runtime', 'sim venue', 'backtest farm', 'Python SDK', 'Go SDK', 'Rust SDK', 'TypeScript SDK'].map((n, i) => box(B, 70 + i * 210, 760, 198, 60, n, { cls: 'chip', lcls: 'cmp' }));
    const ll = link(L, lake, farm.comp['lake reader'], 'pu', { sides: ['t', 'b'] });
    const lf = stream(M, ll, 'pu', 3, 1.2, 5);
    return (t, rt) => {
      pop(sim, t, 0.2); pop(farm, t, 0.5);
      bk.forEach((g, i) => pop(g, t, 1.0 + i * 0.4));
      // our order waits behind others at 24,500.00; trades eat the queue
      const on = t > S.at[0] + 2.4;
      const eaten = on ? Math.floor((rt * 1.2) % 8) : 0;
      queue.forEach((r, i) => { op(r, on && i >= eaten ? 1 : 0); r.setAttribute('x', 340 + (i - Math.min(eaten, i)) * 0 + i * 60); });
      op(ours, on ? 1 : 0); tr(ours, 340 + 6 * 60 + 30, 453);
      txt(qT, on ? (eaten >= 6 ? 'filled: the queue ahead of us traded' : `${6 - eaten} orders ahead of ours`) : '');
      if (on && eaten >= 6) squash(ours._in, t, t - 0.1);
      walk(['BrokerAdapter contract', 'latency model'].map(n => sim.comp[n]), t, S.at[1] + 0.3, S.to[1]);
      pop(core, t, S.at[2] + 0.2); pops(users, t, S.at[2] + 0.8, (S.to[2] - S.at[2] - 1.2) / users.length);
      pop(lake, t, S.at[3] + 0.3); show(ll, t, S.at[3] + 0.8); lf(rt, t > S.at[3] + 1.2 ? 0 : 1e9);
      walk(['lake reader', 'runtime, in batch', 'sim venue library', 'results'].map(n => farm.comp[n]), t, S.at[3] + 1.0, S.to[3]);
      const n = Math.round(machines.length * k(t, S.at[4] + 0.6, S.to[4] - 0.6));
      machines.forEach((r, i) => op(r, i < n ? 0.8 : 0));
      countUp(vc, t, S.at[4] + 0.6, S.to[4] - 0.6, 0, 500, v => t > S.at[4] + 0.4 ? `${Math.round(v)} vCPU` : '');
    };
  });
}

// 19. Integrity.
{
  const S = beats([
    ['Integrity, in Go with Temporal workflows, checks that what happened is what should have happened. It runs once per region, mostly after the market closes, and owns its own Postgres database.', { focus: 'all' }],
    ['The day check replays each deployment on the broker’s one-minute candles after the 15:45 export, and flags any day where the replay and what actually happened differ.', { spot: ['dc'] }],
    ['Each difference becomes a repair case, with proof charts and the paths a fix could take. Replays run as backtest-farm jobs, on the same Rust runtime as live, so a replay is exact by construction.', {}],
    ['It also compares shadow fills with real ones, reconciles positions with each broker, and collects the issues strategies report about themselves.', {}],
    ['Because the end-of-day chain is a Temporal workflow, a chain that stops halfway resumes at the step where it stopped. The target: checks and repair cases ready by 16:30.', { spot: ['wf'] }],
  ]);
  scene({ title: 'Integrity', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const ig = named('ig', svc(B, 40, 116, 620, 744, 'integrity', { lang: 'Go + Temporal', kind: 'Deployment · 2', part: 'trade',
      does: 'Checks, repair cases, reconciliation, issues. Findings late, never wrong.',
      comps: ['day check', 'repair cases', 'shadow comparison', 'broker reconciliation', 'strategy issues', 'replay jobs'], cols: 1, ch: 44 }));
    const c = ig.comp;
    named('rc', bounds(G(B), c['repair cases'].X, c['repair cases'].Y, c['repair cases'].W, 44));
    named('more', bounds(G(B), c['shadow comparison'].X, c['shadow comparison'].Y, c['shadow comparison'].W, 3 * 52 - 8));
    const dc = named('dc', area(B, 690, 116, 870, 400, 'day check: replay vs what happened', 'cell'));
    // two price lines; the replay parts from the real day at one minute
    const xs = Array.from({ length: 40 }, (_, i) => 720 + i * 20);
    const y1 = i => 330 + Math.sin(i / 5) * 60 + Math.sin(i / 2.3) * 14;
    const real = E('path', { d: 'M' + xs.map((x, i) => `${x} ${y1(i)}`).join('L'), class: 'ln c-ink' }, B); op(real, 0);
    const rep = E('path', { d: 'M' + xs.map((x, i) => `${x} ${y1(i) + (i > 24 ? (i - 24) * 3 : 0)}`).join('L'), class: 'ln c-b dash' }, B); op(rep, 0);
    const mark = E('rect', { x: 1200, y: 220, width: 360, height: 260, class: 'hl' }, B); op(mark, 0);
    const legend = G(B); T(legend, 720, 480, '— what happened', 'bs', 'start'); T(legend, 900, 480, '- - replay on 1-minute candles', 'bs', 'start'); op(legend, 0);
    const flag = bubble(M, 1380, 220, 'differs from here', { below: false });
    const wf = named('wf', area(B, 690, 546, 870, 314, 'the end-of-day chain (Temporal)', 'cell'));
    const chain = [['15:45', 'export'], ['day check', 'per deployment'], ['repair cases', 'with proof'], ['16:30', 'ready']].map(([n, s], i) => box(B, 712 + i * 212, 610, 196, 84, n, { sub: s, cls: i === 3 ? 'key' : '' }));
    const cl = chain.slice(1).map((b, i) => link(L, chain[i], b, 'ink'));
    const res = T(B, 1125, 760, '', 'cl');
    const pf = puff(M, 1030, 600);
    const runner = dot(M, 'g', 9);
    const rPath = route(M, chain.map(b => [b.cx, b.B + 24]));
    return (t, rt) => {
      pop(ig, t, 0.2); pops([dc, wf, ...chain], t, 1.0, (S.to[0] - 1.6) / 6); cl.forEach((l, i) => show(l, t, S.to[0] * 0.55 + i * S.to[0] * 0.13, S.to[0] * 0.55 + (i + 1) * S.to[0] * 0.13));
      reveal(real, t, S.at[1] + 0.4, S.at[1] + 2.4); reveal(rep, t, S.at[1] + 1.4, S.to[1] - 0.6); fade(legend, t, S.at[1] + 0.6);
      fade(mark, t, S.to[1] - 1.0); flag.go(t, S.to[1] - 0.8, null, rt);
      walk([c['day check']], t, S.at[1], S.to[1]);
      walk([c['repair cases'], c['replay jobs']], t, S.at[2] + 0.3, S.to[2]);
      walk([c['shadow comparison'], c['broker reconciliation'], c['strategy issues']], t, S.at[3] + 0.3, S.to[3]);
      // the chain runs, a machine dies at the second step, and it resumes there
      const a = S.at[4] + 0.4, cr = a + (S.to[4] - a) * 0.35, rs = cr + 1.0;
      if (t < a) op(runner, 0);
      else if (t < cr) { op(runner, 1); placeOn(runner, rPath, 0.42 * ke(t, a, cr)); }
      else if (t < rs) { op(runner, 0.3); placeOn(runner, rPath, 0.42); }
      else { op(runner, 1); placeOn(runner, rPath, lerp(0.42, 1, ke(t, rs, S.to[4] - 0.3))); }
      pf.go(t, cr); if (t > cr && t < cr + 0.4) quake(t, cr, 0.3, 4);
      txt(res, t > rs ? 'resumed at the step it stopped' : t > cr ? 'a machine died here' : '');
    };
  });
}
