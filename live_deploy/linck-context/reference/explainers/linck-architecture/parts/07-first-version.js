// The first version (the intermediate architecture), and the way there.
const IT = {
  edge: [40, 180, 1520, 40], core: [56, 228, 740, 64], live: [812, 228, 360, 64], notif: [1188, 228, 356, 64],
  md: [56, 364, 360, 64], runner: [432, 364, 360, 64], oms: [808, 364, 360, 64], bgw: [1184, 364, 360, 64],
  sim: [56, 492, 360, 64], workers: [432, 492, 360, 64],
};
for (let i = 0; i < 6; i++) IT['s' + i] = [160 + i * 232, 119, 220, 44];

// 26. The fold: the final map turns into the first version. Each group
// moves only into space the groups before it have already left.
{
  const S = beats([
    ['Here is the final architecture once more: twenty-one services, in three parts. Now watch it fold down into the first version, the one we actually build first.', { focus: 'all' }],
    ['The frames of the three parts go first. In the first version, the parts are not yet separate services.'],
    ['Five services wait for later, on the shelf at the top: commerce, read models, the assistant, the builder, and the tick cache agent.'],
    ['The edge gateway becomes a plain AWS load balancer, with TLS and cookie sessions. It is not our code yet.'],
    ['Notifications becomes a small Python consumer.'],
    ['The product API, control, identity and refdata fold into one Python program, core-api: a modular monolith, where each module keeps its own tables and never joins another’s.'],
    ['The three Rust market-data services fold into one Go service, md, with the bars and chain views inside it.'],
    ['Iceberg’s Rust supervisor becomes runner: Python workers, each holding a share of the strategies.'],
    ['Integrity and the backtest farm fold into Python Temporal workers.'],
    ['Heimdall stays exactly as it is: the same Go service from day one. Kraken stays too, as broker-gw-kite, with only Kite at first. The live gateway stays, in Go. And the Rust sim venue becomes sim, a Python fill model.'],
    ['All of it runs on one Kubernetes cluster, in one region, with one cell. And the stores: Redpanda’s logs move onto NATS JetStream, etcd’s leases move into Postgres, Vault waits for later, and the lake is plain Parquet files. That leaves nine deployables of our own.'],
  ]);
  const MORPH = [
    ['commerce', 'commerce', 'Go', 'platform', 2, 's0', 'commerce', null, 'infra', 'shelf'],
    ['read-models', 'read-models', 'Go', 'lens', 2, 's1', 'read-models', null, 'infra', 'shelf'],
    ['assistant', 'assistant', 'Python', 'lens', 2, 's2', 'assistant', null, 'infra', 'shelf'],
    ['builder', 'builder', 'Go', 'trade', 2, 's3', 'builder', null, 'infra', 'shelf'],
    ['cache-agent', 'cache-agent', 'Rust', 'trade', 2, 's4', 'cache-agent', null, 'infra', 'shelf'],
    ['edge-gateway', 'edge-gateway', 'Envoy + Go', 'platform', 3, 'edge', 'AWS load balancer · TLS · cookie sessions (not our code yet)', null, 'infra', 'main'],
    ['notifications', 'notifications', 'Go', 'platform', 4, 'notif', 'notifications', 'Python', 'platform', 'main'],
    ['product-api', 'product-api', 'Python', 'lens', 5, 'core', 'core-api: product · deployments · identity · instruments', 'Python', 'mixed', 'main'],
    ['control', 'control', 'Go', 'trade', 5, 'core', '', '', '', 'merge'],
    ['identity', 'identity', 'Go', 'platform', 5, 'core', '', '', '', 'merge'],
    ['refdata', 'refdata', 'Go', 'trade', 5, 'core', '', '', '', 'merge'],
    ['md-ingest', 'md-ingest', 'Rust', 'trade', 6, 'md', 'md · bars, chains inside', 'Go', 'trade', 'main'],
    ['md-sequencer', 'md-sequencer', 'Rust', 'trade', 6, 'md', '', '', '', 'merge'],
    ['computed-streams', 'computed-streams', 'Rust', 'trade', 6, 'md', '', '', '', 'merge'],
    ['supervisor', 'Iceberg (supervisor)', 'Rust', 'trade', 7, 'runner', 'runner · leased workers', 'Python', 'trade', 'main'],
    ['integrity', 'integrity', 'Go', 'trade', 8, 'workers', 'workers (Temporal)', 'Python', 'trade', 'main'],
    ['backtest-farm', 'backtest-farm', 'Rust', 'trade', 8, 'workers', '', '', '', 'merge'],
    ['oms', 'Heimdall (oms)', 'Go', 'trade', 9, 'oms', 'oms (Heimdall)', 'Go', 'trade', 'main'],
    ['broker-gw', 'Kraken (broker-gw)', 'Go', 'trade', 9, 'bgw', 'broker-gw-kite (Kraken)', 'Go', 'trade', 'main'],
    ['live-gateway', 'live-gateway', 'Go', 'lens', 9, 'live', 'live-gateway', 'Go', 'lens', 'main'],
    ['sim-venue', 'sim-venue', 'Rust', 'trade', 9, 'sim', 'sim · fill model', 'Python', 'trade', 'main'],
  ];
  scene({ title: 'From the final architecture to the first version', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const outside = [box(B, 40, 112, 470, 60, 'You: web app · phone · CLI · SDK', { cls: 'out', lcls: 'cl' }),
      box(B, 860, 112, 330, 60, 'Exchanges, via a vendor', { cls: 'out', lcls: 'cl' }), box(B, 1220, 112, 340, 60, 'Brokers: Kite, Upstox…', { cls: 'out', lcls: 'cl' })];
    const fr = [area(B, 40, 190, 740, 116, 'Linck Platform', 'platform'), area(B, 800, 190, 760, 116, 'LinckLens', 'lens'),
      area(B, 40, 322, 1520, 382, 'LinckTrade', 'trade'), area(B, 56, 446, 1488, 246, 'One cell', 'cell')];
    const shelf = area(B, 40, 108, 1520, 66, '', 'cell');
    T(shelf, 58, 147, 'not yet', 'fl', 'start');
    const cl = area(B, 40, 223, 1520, 345, '', 'cell');
    T(cl, 830, 522, 'One EKS cluster · Mumbai · 3 zones', 'cl', 'start');
    T(cl, 830, 548, 'node pools: services · runtime (gVisor) · data', 'bs', 'start');
    const count = T(B, 1560, 732, '', 'bigs', 'end');
    T(B, 40, 732, 'Stores', 'fl', 'start');
    const sp = finalSpots();
    const mb = MORPH.map(([n, disp, lang, part, step, to, nn, nl, np, kind]) => {
      const m = mbox(M, disp, lang, part); const [x, y, w] = sp[n];
      return { m, from: [x, y, w, 64], to: IT[to], step, kind, nn, nl, np };
    });
    // each store sits in a group of its own, so it can slide (a pop's CSS transform would override the slide)
    const slot = F_STORES.map(() => G(B));
    const fdb = F_STORES.map(([n, sub], i) => db(slot[i], 40 + i * 170, 746, 160, 100, n, sub));
    const ndb = { 0: db(B, 40, 746, 160, 100, 'Postgres', 'one, + a replica'), 2: db(B, 380, 746, 160, 100, 'S3 lake', 'plain Parquet'), 6: db(B, 1060, 746, 160, 100, 'NATS', 'JetStream: all events') };
    const vault = mbox(M, 'Vault + KMS', null, 'infra');
    return (t, rt) => {
      pops([...outside, ...fr, ...fdb], t, 0.2, 0.06);
      [...outside, ...fr].forEach(f => { if (t >= S.at[1]) op(f, 1 - k(t, S.at[1] + 0.2, S.to[1] - 0.2)); });
      pop(shelf, t, S.to[1] - 0.2);
      let gone = 0;
      mb.forEach(({ m, from, to, step, kind, nn, nl, np }, i) => {
        const a = S.at[step] + 0.2, b = S.to[step] - 0.2, f = ke(t, a, b);
        const r = from.map((v, j) => lerp(v, to[j], f));
        const lab = f <= 0 || f >= 1 ? 1 : Math.max(0, 1 - f / 0.12, (f - 0.88) / 0.12);
        const after = f > 0.5 && kind !== 'merge';
        m.set(r[0], r[1], r[2], r[3], { name: after ? nn : MORPH[i][1], lang: after ? nl : MORPH[i][2], part: after ? np : MORPH[i][3], label: lab });
        let o = Math.min(1, k(t, 0.3 + i * 0.12, 0.7 + i * 0.12) * 1.5);
        if (kind === 'merge') o *= 1 - k(f, 0.8, 1);
        if (kind === 'shelf') o *= 1 - 0.35 * k(f, 0.9, 1);
        op(m, o);
        if (f >= 1 && (kind !== 'main' || to === IT.edge)) gone++;
      });
      txt(count, t > S.at[2] ? `our deployables: ${21 - gone}` : '');
      pop(cl, t, S.at[10] + 0.3);
      const sa = S.at[10] + 1.2, g = ke(t, sa, S.to[10] - 0.4);
      const move = (i, dx, dy) => { slot[i].setAttribute('transform', `translate(${(dx * g).toFixed(1)},${(dy * g).toFixed(1)})`); if (t > sa) op(slot[i], g >= 1 ? 0 : 1 - 0.8 * k(g, 0, 0.12)); };   // faint while it travels
      move(4, -680, 0); move(5, 170, 0); move(7, IT.s5[0] - 1230, IT.s5[1] - 746);
      [0, 2, 6].forEach(i => { if (t > sa) op(slot[i], 1 - k(g, 0.4, 0.8)); op(ndb[i], k(g, 0.4, 0.8)); });
      vault.set(...IT.s5, { label: 1 }); op(vault, k(g, 0.85, 1) * 0.65);
    };
  });
}

// 27. The first version, service by service.
{
  const G9 = [[40, 116], [552, 116], [1064, 116], [40, 370], [552, 370], [1064, 370], [40, 624], [552, 624], [1064, 624]];
  const NINE = [
    ['core', 'core-api', 'Python', 'Deployment', 'mixed', 'A modular monolith: one team, one database. Each module owns its tables and never joins another’s.', ['product API (screens)', 'deployments (control)', 'identity, sessions', 'instruments (resolver)']],
    ['live', 'live-gateway', 'Go', 'Deployment', 'lens', 'SSE to screens, latest values only. The same service as in the final.', ['NATS subscriber', 'latest slot per screen', 'SSE writer']],
    ['notif', 'notifications', 'Python', 'consumer', 'platform', 'Reads events from NATS and sends the alerts.', ['event reader', 'channels']],
    ['md', 'md', 'Go', 'Deployment', 'trade', 'Kite’s WebSocket plus one licensed vendor. Bars and chain views inside; latest prices in Redis.', ['Kite + vendor feeds', 'bars, chain views', 'latest prices → Redis', 'conflated gRPC streams']],
    ['runner', 'runner', 'Python', 'StatefulSet', 'trade', 'Leased workers, each a shard of strategies. Python strategies only, in gVisor.', ['lease in Postgres', 'one slot per instrument', 'deployment_state rows', 'indicators in strategy']],
    ['oms', 'oms', 'Go', 'StatefulSet', 'trade', 'Heimdall from day one: single writer, risk pipeline, outbox.', ['risk pipeline', 'outbox', 'positions + lots tables', 'Kite and sim routes']],
    ['bgw', 'broker-gw-kite', 'Go', 'Deployment', 'trade', 'Kraken with one arm: Kite. Paper orders go to sim.', ['Kite adapter', 'tag: our order id', 'rate limits', 'update stream']],
    ['sim', 'sim', 'Python', 'Deployment', 'trade', 'A fill model behind the same broker contract as Kraken.', ['fill model', 'BrokerAdapter contract']],
    ['workers', 'workers', 'Python', 'Temporal workers', 'trade', 'Today’s code, moved: day check, repair, replay, backtests.', ['day check', 'repair', 'replay', 'backtests']],
  ];
  const S = beats([
    ['The first version has nine deployables of our own. Here is each one, with what it is written in and what it does.', { focus: 'all' }],
    ['Core-api, in Python, is a modular monolith: one program, one team, one database. Its modules are the product API for the screens, deployments, which is control’s job, identity, and instruments, today’s resolver. Each module owns its tables and never joins another’s, so splitting one out later is a move, not a rewrite.', { focus: ['core'], spot: ['core'], pad: 8 }],
    ['The live gateway, in Go, is the same service as in the final: SSE to screens, latest values only.', { focus: ['live'], spot: ['live'], pad: 8 }],
    ['Notifications is a small Python consumer: it reads events from NATS and sends the alerts.', { focus: ['notif'], spot: ['notif'], pad: 8 }],
    ['Md, in Go, takes Kite’s WebSocket and one licensed vendor, cleans the ticks, builds bars and chain views inside itself, keeps the latest prices in Redis, and streams ticks over conflated gRPC to the runner workers.', { focus: ['md'], spot: ['md'], pad: 8 }],
    ['Runner, in Python, runs as a StatefulSet. Each worker holds a lease in Postgres for its shard of strategies, keeps one latest slot per instrument, and saves each strategy’s state as a deployment_state row. Strategies compute their own indicators, and only Python strategies run, in gVisor.', { focus: ['runner'], spot: ['runner'], pad: 8 }],
    ['The OMS, in Go, is Heimdall from day one: the single writer, the risk pipeline and the outbox, writing the positions and lots tables in the one Postgres.', { focus: ['oms'], spot: ['oms'], pad: 8 }],
    ['Broker-gw-kite, in Go, is Kraken with one arm: Kite. Paper orders are routed to sim.', { focus: ['bgw'], spot: ['bgw'], pad: 8 }],
    ['Sim, in Python, is a fill model behind the same broker contract that Kraken speaks.', { focus: ['sim'], spot: ['sim'], pad: 8 }],
    ['The workers are Python Temporal workers running today’s code, moved: the day check, repair, replay and backtests.', { focus: ['workers'], spot: ['workers'], pad: 8 }],
    ['All nine run on one EKS cluster in Mumbai, across three zones, with three node pools: services, runtime with gVisor, and data. About six to ten machines, sized for about two thousand strategies, five thousand instruments and twenty thousand updates a second at the open: estimates. NATS JetStream on three nodes carries every event; there is one Postgres, a primary and a replica, one ClickHouse, Redis, and Parquet files on S3.', { focus: 'all' }],
  ]);
  scene({ title: 'The first version: nine deployables', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const sv = NINE.map(([id, n, lang, kind, part, does, comps], i) => named(id, svc(B, ...G9[i], 496, 236, n, { lang, kind, part, does, comps, cols: 2, ch: 34 })));
    const sz = G(M);
    box(sz, 160, 230, 1280, 440, '', { cls: 'frame key' });
    const lines = ['one EKS cluster · Mumbai · 3 zones', 'node pools: services · runtime (gVisor) · data', '~6–10 machines (estimate)', '~2,000 strategies · ~5,000 instruments · ~20k updates/s at the open (estimates)', 'NATS JetStream ×3 · one Postgres (primary + replica) · ClickHouse · Redis · S3 Parquet'];
    const lt = lines.map((s, i) => T(sz, 200, 300 + i * 74, '', i < 3 ? 'bigs' : 'cl', 'start'));
    sz.querySelectorAll('.bx').forEach(b => op(b, 1));
    op(sz, 0);
    return (t, rt) => {
      pops(sv, t, 0.2, (S.to[0] - 0.6) / 9);
      sv.forEach((s, i) => { const c = Object.values(s.comp); walk(c, t, S.at[i + 1] + 0.4, S.to[i + 1] - 0.2); });
      const z = k(t, S.at[10], S.at[10] + 0.5);
      op(B, 1 - 0.88 * z); op(sz, z);
      const per = (S.to[10] - S.at[10] - 0.8) / lines.length;
      lt.forEach((e, i) => typeOn(e, t, S.at[10] + 0.5 + i * per, S.at[10] + 0.5 + (i + 0.9) * per, lines[i], rt));
    };
  });
}

// 28–29. What is swapped for what.
function swapScene(title, intro, rows) {
  const S = beats([[intro, { focus: 'all' }], ...rows.map(r => [r[4], { spot: ['r' + rows.indexOf(r)], pad: 3 }])]);
  scene({ title, dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const hdr = G(B); [['', 40], ['first version', 270], ['final', 880], ['language', 1396]].forEach(([s, x]) => T(hdr, x, 140, s, 'fl', 'start')); op(hdr, 0);
    const rh = Math.min(86, 700 / rows.length);
    const rs = rows.map(([what, first, fin, lang], i) => {
      const y = 158 + i * rh;
      const g = named('r' + i, box(B, 40, y, 1520, rh - 10, '', { cls: 'chip' }));
      T(g, 60, y + rh / 2, what, 'cl', 'start');
      wrap(g, 270, y + rh / 2 + 6 - (wrapLines(first, 58).length - 1) * 10, first, 58, 'txt', 20);
      T(g, 840, y + rh / 2 + 6, '→', 'cl');
      wrap(g, 880, y + rh / 2 + 6 - (wrapLines(fin, 56).length - 1) * 10, fin, 56, 'txt', 20);
      if (lang) { const [a, b] = lang.split('→').map(s => s.trim()); const lx = 1396; if (a) badge(g, lx, y + rh / 2 + 4, a, 'lang', 'start'); T(g, lx + (a ? a.length * 8.6 + 20 : 0), y + rh / 2 + 4, '→', 'bs', 'start'); badge(g, lx + (a ? a.length * 8.6 + 38 : 18), y + rh / 2 + 4, b, 'lang', 'start'); }
      return g;
    });
    return (t, rt) => {
      fade(hdr, t, 0.2);
      rs.forEach((g, i) => { pop(g, t, 0.4 + i * (S.to[0] - 0.8) / rs.length); if (t < S.at[i + 1]) op(g, Math.min(0.4, +g.getAttribute('opacity'))); });
      rs.forEach((g, i) => flash(g, t, S.at[i + 1], 0.6));
    };
  });
}
swapScene('Swapped: the services', 'Now, what is swapped for what, and why. First the services. Each row: the first version, the final, and the language change.', [
  ['Edge', 'AWS load balancer, TLS, cookie sessions', 'Envoy + a Go plug-in: logins, API keys, plan limits, gRPC as REST', '→ Go',
    'The edge: a plain load balancer first; Envoy with our Go plug-in when the public API launches.'],
  ['Control plane', 'modules inside core-api', 'Go services: identity, control, commerce, refdata', 'Python → Go',
    'The control plane: Python modules inside core-api first; separate Go services once marketplace and billing work begins, or a second backend team. They move one module at a time: the gateway routes that module’s endpoints to the new Go service.'],
  ['Product API', 'the rest of core-api', 'product-api, the same code', 'Python → Python',
    'The product API stays Python. It is the rest of core-api, and keeps serving the web app.'],
  ['Market data', 'md: Kite + one vendor, bars inside, Redis', 'feed handlers, a sequencer, a shared-memory cache', 'Go → Rust',
    'Market data goes from Go to Rust, for the cost of every message times the volume, and no garbage-collector pauses. The switch comes past ten thousand instruments, a second vendor, or tick-by-tick feeds; both run on the same feed for two weeks first.'],
  ['Computed streams', 'bars in md; indicators inside each strategy', 'a Rust DAG per cell, shared by every strategy', '→ Rust',
    'Computed streams appear when the same EMA is being computed thousands of times: when duplicate work passes thirty percent of runtime CPU.'],
  ['Strategy runtime', 'runner: Python workers, leases in Postgres', 'Iceberg: a Rust supervisor, a sandbox per strategy', 'Python → Rust',
    'The runtime goes from Python workers to Iceberg’s Rust supervisor when the SDK opens to the public, for isolation per strategy, budgets, any language, and faster recovery. Deployments move across by lease handoff at minute boundaries.'],
  ['Orders & risk', 'oms', 'Heimdall: more adapters, partitioned per cell', 'Go → Go',
    'Orders and risk is built once, in Go, and only grows.'],
  ['Broker gateway', 'broker-gw-kite', 'Kraken: one deployment per broker', 'Go → Go',
    'The broker gateway is built once, in Go, and grows an arm per broker.'],
  ['Sim venue', 'sim: a Python fill model', 'a Rust service, the same library in backtests', 'Python → Rust',
    'The sim venue goes from Python to Rust for the backtest farm, with fills equal to the paisa on recorded days before the switch.'],
  ['Integrity', 'Python Temporal workers', 'a Go service + Temporal; replays on the Rust runtime', 'Python → Go',
    'Integrity goes from Python workers to a Go service once the Rust runtime is in, so replays run on the same runtime as live.'],
  ['Notifications', 'a Python consumer', 'Go: templates and channels per tenant', 'Python → Go',
    'And notifications goes from Python to Go when partner brokers need their own branding and channels.'],
]);
swapScene('Swapped: data, events and hosting', 'Then the data, the events and the hosting.', [
  ['Event backbone', 'NATS JetStream for everything', 'Redpanda for orders, fills, journal, ticks; NATS for commands and live', '',
    'Events: NATS JetStream carries everything at first. Redpanda takes the orders, fills, journal and ticks once retention outgrows JetStream, or more than five services read the fills. The outbox relay writes both for a while.'],
  ['Ledger', 'one Postgres: positions + lots tables', 'a shard per cell: a double-entry journal; positions computed from it', '',
    'The ledger: today’s positions and lots tables first; then a double-entry journal written beside them, checked equal every night, until the tables become views of it.'],
  ['State snapshots', 'a deployment_state row in Postgres', 'a local NVMe write-ahead log, shipped to S3', '',
    'Snapshots: a Postgres row per deployment first; a local write-ahead log once snapshot writes pass two hundred a second.'],
  ['Leases', 'in Postgres', 'in etcd, per cell', '',
    'Leases: in Postgres first, in etcd with the Rust runtime.'],
  ['Lake', 'Parquet files on S3', 'Apache Iceberg tables on S3', '',
    'The lake: plain Parquet files, later registered as Apache Iceberg tables, with no rewrite.'],
  ['Hosting', 'one EKS cluster, three node pools', 'a platform cluster per region + a cluster per cell', '',
    'Hosting: one cluster first; a cluster per cell with the first partner, or past five thousand strategies, with a staff cell first.'],
  ['Only in the final', '—', 'Vault + KMS · audit store · assistant · commerce · builder · public API · mobile apps', '',
    'And some things exist only in the final: Vault and KMS, the write-once audit store, the assistant, commerce, the builder, the public API and the mobile apps. They are added beside, and nothing is replaced.'],
]);

// 30. Flows in the first version.
flowScene('Flows in the first version', (L, B, M) => {
  const kt = out(B, 0, 0, 'Kite + one vendor', 'WebSocket ticks; orders');
  const md = svcAt(B, 1, 0, 'md', 'Go', 'trade', 'clean · bars · chains · Redis');
  const rn = svcAt(B, 2, 0, 'runner', 'Python', 'trade', 'latest slot · runs strategies');
  const na = store(B, 2, 1, 'NATS JetStream', 'intents, fills, commands');
  const om = svcAt(B, 1, 1, 'oms', 'Go', 'trade', 'risk · ledger tables · outbox');
  const bg = svcAt(B, 0, 1, 'broker-gw-kite', 'Go', 'trade', 'Kite; paper → sim');
  const pg = store(B, 1, 2, 'Postgres', 'positions + lots · leases');
  const lv = svcAt(B, 2, 2, 'live-gateway', 'Go', 'lens', 'SSE to screens');
  const br = out(B, 0, 3, 'Browser', 'through the load balancer');
  const co = svcAt(B, 1, 3, 'core-api', 'Python', 'mixed', 'screens · deployments');
  const e = [
    [link(L, kt, md, 'b', { label: 'WebSocket' }), kt, md],
    [link(L, md, rn, 'b', { label: 'conflated gRPC' }), md, rn],
    [link(L, rn, na, 'b', { d1: -60, d2: -60, label: 'intent', lx: 70, ly: 4, anchor: 'start' }), rn, na],
    [link(L, na, om, 'b', { d1: -18, d2: -18, label: 'JetStream' }), na, om],
    [link(L, om, pg, 'b', { label: 'one transaction', lx: 70, ly: 4, anchor: 'start' }), om, pg],
    [link(L, om, bg, 'g', { d1: -18, d2: -18, label: 'gRPC' }), om, bg, 'g'],
    [link(L, bg, kt, 'g', { d1: -60, d2: -60, label: 'REST', lx: -40, ly: 4, anchor: 'end' }), bg, kt, 'g'],
    [link(L, kt, bg, 'g', { d1: 60, d2: 60, sides: ['b', 't'], label: 'updates', lx: 70, ly: 4, anchor: 'start' }), kt, bg, 'g'],
    [link(L, bg, om, 'g', { d1: 18, d2: 18, label: 'fill', ly: 42 }), bg, om, 'g'],
    [link(L, om, na, 'g', { d1: 18, d2: 18, label: 'fill', ly: 42 }), om, na, 'g'],
    [link(L, na, lv, 'g', { label: 'fill', lx: 70, ly: 4, anchor: 'start' }), na, lv, 'g'],
    [link(L, lv, br, 'g', { d: 'M1380 636C1380 700 220 680 220 744', label: 'SSE', at: 0.5, ly: -12 }), lv, br, 'g'],
    [link(L, br, co, 'pu', { label: 'HTTPS' }), br, co, 'pu'],
    [link(L, co, pg, 'pu', { label: 'reads', lx: 70, ly: 4, anchor: 'start' }), co, pg, 'pu'],
    [link(L, co, na, 'wt', { d: 'M980 798C1100 798 1100 412 1194 412', label: 'start command', at: 0.25, lx: -14, ly: 4, anchor: 'end' }), co, na, 'wt'],
    [link(L, na, rn, 'wt', { d1: 60, d2: 60, label: 'start', lx: 70, ly: 4, anchor: 'start' }), na, rn, 'wt'],
  ];
  return { edges: e };
}, [
  ['The same flows, in the first version. The shape is the same; the pieces are simpler.', { focus: 'all' }],
  ['A price: Kite’s WebSocket and the vendor send ticks to md, in Go, which cleans them, builds bars and chain views, keeps the latest prices in Redis, and streams ticks over conflated gRPC to each runner worker. The worker keeps one latest slot per instrument, and each strategy computes its own indicators.', {}, [0, 1], 'tick'],
  ['An order: the strategy returns an intent, and its runner worker publishes it on NATS JetStream. The OMS checks it and records the order in the positions and lots tables in one transaction, then calls broker-gw-kite over gRPC, which places it at Kite. A paper order goes to sim instead.', {}, [2, 3, 4, 5, 6], 'intent'],
  ['The fill: Kite’s update comes back; the OMS records the fill and publishes it on NATS, and the live gateway pushes it over SSE to your screen.', {}, [7, 8, 9, 10, 11], 'fill'],
  ['A screen asks core-api through the load balancer, over HTTPS, and core-api reads Postgres and ClickHouse.', {}, [12, 13], 'request'],
  ['And a deploy: core-api’s deployments module checks the version and publishes a start command on NATS. A runner worker takes the lease in Postgres and starts the strategy, in gVisor.', {}, [14, 15], 'start'],
]);

// 31. The way there.
{
  const STEPS = [
    ['P0', 'Latest-value delivery in today’s app', 'a slow-strategy test'],
    ['I0', 'Today’s app on one EKS cluster, as core-api', 'a week of paper days beside the current box'],
    ['I1', 'Contracts repo; SDK v0 over today’s StrategyBase', 'fault matrix and crash suites unchanged'],
    ['I2', 'Strategies out of the API, into leased runner workers', 'a week of identical fills (dual run)'],
    ['I3', 'Go OMS + Kite gateway: shadow-write, then sole writer', 'journal = today’s tables, nightly, 2 weeks'],
    ['I4', 'Go market data; Redis prices; ClickHouse bars', 'bars equal Kite’s 1-minute candles'],
    ['I5', 'Temporal end of day; tenants + row-level security; shadow', 'end-of-day outputs equal; tenancy tests'],
    ['F1', 'Rust feed handlers and tick cache, beside Go', 'every record and bar equal, 2 weeks'],
    ['F2', 'Computed streams; SDK subscribe form', 'series equal to in-strategy values, a month'],
    ['F3', 'Rust supervisor and sandboxes; leases in etcd', 'decision hashes equal on recorded days'],
    ['F4', 'Rust sim venue; backtest farm on it', 'fills equal to the paisa'],
    ['F5', 'Redpanda for orders, fills, journal, ticks', 'outbox dual-writes; consumer offsets match'],
    ['F6', 'Control plane to Go; Envoy gateway; public API', 'contract tests on every endpoint moved'],
    ['F7', 'Cells: ledger shards, a cluster each, tenant pinning', 'a staff cell, a partner cell, then waves'],
    ['F8', 'Go, Rust, TypeScript SDKs; WASM, remote; marketplace', 'a conformance suite per SDK'],
    ['F9', 'Refdata service; more brokers', 'adapter conformance + a paper week each'],
  ];
  const S = beats([
    ['Getting there. Every step ships alone, runs beside what it replaces until it is proven equal, and leaves trading untouched.', { focus: 'all' }],
    ['Step P0 happens in today’s app: the tick broadcaster keeps one slot per instrument for each subscriber, so a slow runner or screen gets the newest tick. A slow-strategy test proves it.', { spot: ['m0'], pad: 3 }],
    ['I0 puts today’s app, unchanged, on one EKS cluster as core-api, proven by a week of paper days beside the current machine. I1 adds the contracts repository, and SDK version zero over today’s strategy base class. I2 moves strategies out of the API process into leased Python runner workers, proven by a week of identical fills.', { spot: ['m1', 'm2', 'm3'], pad: 3 }],
    ['I3 brings the Go OMS and the Kite gateway: first writing in shadow beside today’s fill path, then the single writer once the journal and today’s tables match every night for two weeks. I4 adds the Go market data service, proven by bars equal to Kite’s one-minute candles. I5 adds Temporal for the end of day, tenants with row-level security, and shadow mode.', { spot: ['m4', 'm5', 'm6'], pad: 3 }],
    ['Then the final pieces. F1: Rust feed handlers and the tick cache, beside the Go service, until every record and bar is equal for two weeks. F2: computed streams. F3: the Rust supervisor and sandboxes, with leases in etcd, until decision hashes match the Python workers. F4: the Rust sim venue and the backtest farm, with fills equal to the paisa.', { spot: ['m7', 'm8', 'm9', 'm10'], pad: 3 }],
    ['F5: Redpanda for orders, fills, the journal and ticks. F6: the control plane to Go, one module at a time, behind the Envoy gateway, and the public API. F7: cells, each with its ledger shard and its own cluster. F8: the Go, Rust and TypeScript SDKs, WASM and remote sandboxes, and the marketplace. F9: the refdata service, and more brokers.', { spot: ['m11', 'm12', 'm13', 'm14', 'm15'], pad: 3 }],
  ]);
  scene({ title: 'The way there, one proven step at a time', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const col = i => (i < 7 ? [40, 150 + i * 96] : [812, 150 + (i - 7) * 78]);
    const rows = STEPS.map(([id, what, proof], i) => {
      const [x, y] = col(i), h = i < 7 ? 86 : 70;
      const g = named('m' + i, box(B, x, y, 748, h, '', { cls: i < 7 ? 'chip' : 'chip' }));
      badge(g, x + 14, y + 30, id, i === 0 ? 'kind' : i < 7 ? 'kind' : 'kind', 'start');
      T(g, x + 70, y + 30, what, 'cl', 'start');
      T(g, x + 70, y + (i < 7 ? 62 : 56), 'proven by: ' + proof, 'bs', 'start');
      g._ck = check(g, x + 718, y + h / 2, true);
      return g;
    });
    const hd = G(B); T(hd, 40, 132, 'today’s app → the first version', 'fl', 'start'); T(hd, 812, 132, 'the first version → the final', 'fl', 'start'); op(hd, 0);
    const groups = [[0], [1, 2, 3], [4, 5, 6], [7, 8, 9, 10], [11, 12, 13, 14, 15]];
    return (t, rt) => {
      fade(hd, t, 0.2);
      rows.forEach((g, i) => { pop(g, t, 0.3 + i * (S.to[0] - 0.8) / rows.length); if (t < S.at[groups.findIndex(gr => gr.includes(i)) + 1]) op(g, Math.min(0.4, +g.getAttribute('opacity'))); });
      groups.forEach((gr, s) => gr.forEach((i, n) => { const a = S.at[s + 1] + 0.4 + n * (S.to[s + 1] - S.at[s + 1] - 0.6) / gr.length; pop(rows[i]._ck, t, a + 0.6); flash(rows[i], t, a, 0.9); }));
    };
  });
}

// 32. What never changes.
{
  const S = beats([
    ['Some things never change, from the first version to the final, because users and strategies depend on them. So they are designed once, in the first version.', { focus: 'all' }],
    ['The protobuf contracts, which only grow. Strategy code written against the SDK. What an order, a fill and a position mean in the ledger. The web app. And the decision log, strategy issues, and the repair model.'],
    ['To recap: three parts, LinckTrade, LinckLens and Linck Platform. Three nicknames: Iceberg runs the strategies, Heimdall guards every order, and Kraken reaches the brokers. Twenty-one services in the final, nine in the first version, and a proven step between them.', { spot: ['rc'] }],
  ]);
  scene({ title: 'What never changes', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const keep = ['the protobuf contracts: they only grow', 'strategy code written against the SDK', 'the ledger’s meaning: an order, a fill, a position', 'the web app', 'the decision log, strategy issues, the repair model'];
    const ks = keep.map((s, i) => box(B, 200, 150 + i * 84, 1200, 70, '', { cls: 'chip' }));
    const kt = keep.map((s, i) => T(B, 290, 194 + i * 84, '', 'bl', 'start'));
    const lock = keep.map((s, i) => check(B, 240, 185 + i * 84, true));
    const rc = G(B);
    [['LinckTrade', 'trade', 'the trading: data in, strategies, orders, the ledger'], ['LinckLens', 'lens', 'what you see: screens, Geek mode, reports'], ['Linck Platform', 'platform', 'the door, accounts, billing, notifications']]
      .forEach(([n, c, s], i) => { const a = area(rc, 40 + i * 512, 590, 496, 120, n, c); T(a, 58 + i * 512, 680, s, 'bs', 'start'); op(a, 1); });
    [['Iceberg', 'runs the strategies'], ['Heimdall', 'guards every order'], ['Kraken', 'reaches the brokers']].forEach(([n, s], i) => { const b = box(rc, 40 + i * 512, 730, 496, 110, n, { sub: s, cls: 'key' }); op(b, 1); });
    op(rc, 0);
    named('rc', bounds(G(B), 40, 590, 1520, 250));
    const cf = burst(M, 800, 560, { seed: 3, n: 40 });
    return (t, rt) => {
      ks.forEach((b, i) => pop(b, t, S.at[1] + 0.3 + i * (S.to[1] - S.at[1] - 0.8) / ks.length));
      kt.forEach((e, i) => { const a = S.at[1] + 0.5 + i * (S.to[1] - S.at[1] - 0.8) / ks.length; typeOn(e, t, a, a + (S.to[1] - S.at[1] - 0.8) / ks.length * 0.9, keep[i], rt); });
      if (t > S.at[2]) kt.forEach(e => op(e, 1 - 0.6 * k(t, S.at[2], S.at[2] + 0.5)));
      lock.forEach((c, i) => pop(c, t, S.at[1] + 0.8 + i * (S.to[1] - S.at[1] - 0.8) / ks.length));
      if (t < S.at[1]) ks.forEach((b, i) => { op(b, k(t, 0.3 + i * S.to[0] / 5, (i + 1) * S.to[0] / 5) * 0.3); look(b); });
      fade(rc, t, S.at[2] + 0.3);
      if (t > S.at[2]) ks.forEach(b => op(b, 1 - 0.6 * k(t, S.at[2], S.at[2] + 0.5)));
      cf.go(t, S.to[2] - 1.2);
    };
  });
}
