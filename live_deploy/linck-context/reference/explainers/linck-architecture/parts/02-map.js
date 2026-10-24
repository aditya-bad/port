// 5. The whole final architecture on one screen.
{
  const S = beats([
    ['Here is the whole final architecture on one screen: twenty-one services, and the stores underneath them. The lines between them come later, one flow at a time.', { focus: 'all' }],
    ['Across the top is the outside world: you, on the web app, a phone, the command-line tool or the SDK; the exchanges, whose prices reach us through a licensed data vendor; and the brokers, who carry out the orders.', { spot: ['outside'] }],
    ['Linck Platform has four services: the edge gateway, identity, commerce, and notifications.', { spot: ['platform'] }],
    ['LinckLens has four: the product API, the live gateway, read models, and the assistant.', { spot: ['lens'] }],
    ['LinckTrade has thirteen. Five run once per region: control, the builder, refdata, integrity, and the backtest farm.', { spot: ['region'] }],
    ['Eight run inside every cell, on the trading path: market data ingest, the sequencer, computed streams, the tick cache agent, Iceberg, Heimdall, Kraken, and the sim venue. Each cell also has its own etcd, its own Redpanda, and its own ledger.', { spot: ['cell'] }],
    ['The coloured badge on each service is its language. Rust, in red, is for the hot path, where every microsecond and every byte counts. Go, in green, is for services that must be correct and simple to run. Python, in yellow, is where the product changes every day.'],
    ['Underneath are the stores. Each holds one kind of data, and only the service that owns it connects to it.', { spot: ['stores'] }],
  ]);
  scene({ title: 'The final architecture, all of it', dur: S.dur, steps: S.steps }, p => {
    const m = finalMap(p);
    const lg = G(p);
    box(lg, 330, 250, 940, 420, '', { cls: 'frame key' });
    const rows = [['Rust', 'the hot path: every microsecond and every byte counts'], ['Go', 'services that must be correct and simple to run'], ['Python', 'the product, which changes every day']];
    const rt_ = rows.map(([l, s], i) => { badge(lg, 380, 340 + i * 100, l, 'lang', 'start'); return T(lg, 480, 345 + i * 100, '', 'bigs', 'start'); });
    for (const b of lg.querySelectorAll('.bx')) op(b, 1);
    op(lg, 0);
    return (t, rt) => {
      pops(m.all, t, 0.2, (S.to[0] - 1) / m.all.length);
      const leg = Math.min(k(t, S.at[6], S.at[6] + 0.5), 1 - k(t, S.to[6] - 0.4, S.to[6]));
      op(m.B, 1 - 0.88 * leg);
      op(lg, leg);
      const sl = (S.to[6] - S.at[6] - 1) / 3;
      rows.forEach(([, s], i) => { const a = S.at[6] + 0.5 + i * sl; typeOn(rt_[i], t, a, a + sl * 0.95, s, rt); });
    };
  });
}

// 6. Where it runs: AWS, clusters, node pools, and how a release gets there.
{
  const S = beats([
    ['Everything runs on Amazon Web Services, in the Mumbai region, spread over three zones. A warm standby in Hyderabad takes over if the whole region fails.', { focus: 'all' }],
    ['Each region has one platform cluster, on EKS. It runs everything that is not on the trading path: the Platform and Lens services, Trade’s region services, and shared systems like ClickHouse, NATS, Temporal and Vault.', { focus: ['pc'] }],
    ['Each cell is its own cluster, with three node pools. Market data machines are pinned to their CPUs, and always on-demand: never spot machines, which Amazon can take back.', { focus: ['cc'], spot: ['mdpool'] }],
    ['Runtime machines have 64 virtual CPUs, a fast local NVMe disk for the journal, and gVisor installed. They are tainted: marked so that Kubernetes places nothing on them except the supervisor and the cache agent.', { spot: ['rtpool'] }],
    ['Strategies are not pods. Kubernetes only sees those two. The supervisor starts every strategy itself, as a sandbox: an estimated three to five thousand on one machine.', { spot: ['machine'] }],
    ['The services pool runs Heimdall, Kraken, the sim venue, and the cell’s own etcd and Redpanda.', { spot: ['svpool'] }],
    ['Amazon runs the heavy stores: Postgres on RDS, with a standby in another zone; Redis on ElastiCache; S3 for files; and KMS for keys. Brokers only accept orders from registered addresses, so each cell’s orders leave through NAT gateways with two fixed IP addresses.', { focus: 'all', spot: ['aws', 'brokers'] }],
    ['Releases come from Git. Each service becomes a distroless container image, holding only the program and no shell, signed with cosign and stored in ECR, Amazon’s image registry. Argo CD makes each cluster match the Helm charts in Git, and Terraform builds the accounts, networks and clusters themselves.', { spot: ['pipe'] }],
    ['And one rule for trading days: no production release between 09:00 and 15:35, except an approved hotfix. Releases go out after 15:45, one cell at a time.'],
  ]);
  scene({ title: 'Where it runs', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const reg = area(B, 40, 116, 1100, 610, 'AWS Mumbai', 'cell', 'ap-south-1 · three zones');
    const dr = box(B, 790, 126, 330, 40, 'DR: Hyderabad, warm standby', { cls: 'out', lcls: 'cl' });
    const repl = line(L, 'M640 146C700 146 720 146 784 146', 'ink', { dash: true });
    const rf = stream(M, repl, 'ink', 3, 1.8, 4);
    const pc = named('pc', box(B, 64, 180, 1052, 128, '', { cls: 'frame' }));
    T(pc, 84, 208, 'Platform cluster (EKS) · one per region', 'cl', 'start');
    const pools = [['services pool', 'Platform, Lens, region services'], ['data pool', 'ClickHouse · NATS · Temporal · Vault'], ['jobs pool (spot)', 'builds, backtests']]
      .map(([n, s], i) => box(B, [84, 396, 804][i], 222, [300, 396, 300][i], 70, n, { sub: s, cls: 'chip' }));
    const cc = named('cc', box(B, 64, 322, 1052, 392, '', { cls: 'frame' }));
    T(cc, 84, 350, 'Cell cluster (EKS) · one per cell', 'cl', 'start');
    const pool = (x, w, n, s) => { const b = box(B, x, 364, w, 330, '', { cls: 'frame' }); T(b, x + 16, 392, n, 'cl', 'start'); T(b, x + 16, 414, s, 'bs', 'start'); return b; };
    const md = named('mdpool', pool(84, 300, 'market-data pool', 'CPU-pinned · on-demand'));
    const rtp = named('rtpool', pool(400, 430, 'runtime pool', '64 vCPU · NVMe · gVisor · tainted'));
    const sv = named('svpool', pool(846, 254, 'services pool', 'the rest of the cell'));
    const pod = (x, y, w, n) => box(B, x, y, w, 40, n, { cls: 'chip', lcls: 'cmp' });
    const mdp = ['md-ingest (A)', 'md-ingest (B)', 'md-sequencer', 'computed-streams'].map((n, i) => pod(104, 434 + i * 56, 260, n));
    const mach = named('machine', box(B, 416, 430, 398, 244, '', { cls: 'frame key' }));
    T(mach, 432, 452, 'one runtime machine', 'bs', 'start');
    const sup = pod(432, 462, 182, 'supervisor (pod)'), cag = pod(622, 462, 176, 'cache-agent (pod)');
    T(mach, 432, 526, 'sandboxes: not pods', 'bs', 'start');
    const sand = G(B), cells = [];
    for (let r = 0; r < 6; r++) for (let c = 0; c < 20; c++) cells.push(E('rect', { x: 432 + c * 18.4, y: 538 + r * 20, width: 14, height: 14, rx: 2, class: 'f-g' }, sand));
    cells.forEach(e => op(e, 0));
    const svp = ['Heimdall ×3', 'Kraken, per broker', 'sim-venue', 'etcd', 'Redpanda ×3'].map((n, i) => pod(866, 434 + i * 52, 214, n));
    const aws = named('aws', area(B, 1170, 116, 390, 610, 'Run by AWS', 'cell'));
    const am = [['RDS Postgres', 'standby in a second zone'], ['ElastiCache Redis', 'sessions, limits'], ['S3', 'files, the lake, journals'], ['KMS', 'encryption keys'], ['Load balancers', 'in front of the edge'], ['NAT gateways', '2 fixed IPs per cell']]
      .map(([n, s], i) => box(B, 1190, 164 + i * 90, 350, 76, n, { sub: s, cls: 'store' }));
    const brokers = named('brokers', box(B, 1290, 760, 270, 60, 'Brokers', { cls: 'out' }));
    const nat = link(L, am[5], brokers, 'g', { sides: ['b', 't'], label: 'orders, from fixed IPs', lx: -110, ly: 6 });
    const steps = [['Git', ''], ['CI + build', 'images, SBOM'], ['ECR', 'signed (cosign)'], ['Argo CD', 'Helm charts in Git'], ['clusters', 'cell by cell']];
    const pipe = steps.map(([n, s], i) => box(B, 40 + i * 214, 760, 190, 80, n, { sub: s || undefined, cls: i === 4 ? 'key' : '' }));
    const tf = box(B, 1110, 760, 160, 80, 'Terraform', { sub: 'builds clusters', cls: 'chip' });
    named('pipe', bounds(G(B), 40, 732, 1230, 108));
    const pl = pipe.slice(1).map((b, i) => link(L, pipe[i], b, 'b'));
    const rel = packet(M, 'release', 'b');
    const relPath = route(M, [[135, 748], [1035, 748]]);
    const fl = stream(M, nat, 'g', 3, 1.4);
    // the trading-day rule
    const day = G(p);
    box(day, 160, 300, 1280, 330, '', { cls: 'frame key' });
    const x0 = 220, x1 = 1380, tx = h => x0 + (h - 8) / 9 * (x1 - x0);     // 08:00 → 17:00
    E('rect', { x: tx(9), y: 430, width: tx(15 + 35 / 60) - tx(9), height: 50, class: 'f-r', opacity: 0.85 }, day);
    E('rect', { x: tx(15.75), y: 430, width: tx(17) - tx(15.75), height: 50, class: 'f-g', opacity: 0.85 }, day);
    E('line', { x1: x0, x2: x1, y1: 505, y2: 505, class: 'axis' }, day);
    for (let h = 8; h <= 17; h++) T(day, tx(h), 535, `${String(h).padStart(2, '0')}:00`, 'bs');
    T(day, (tx(9) + tx(15.58)) / 2, 462, 'freeze: no releases 09:00–15:35', 'pt');
    T(day, (tx(15.75) + x1) / 2, 410, 'releases', 'okl');
    T(day, 800, 360, 'A trading day', 'bigs');
    const hand = E('line', { y1: 420, y2: 515, class: 'xline' }, day);
    op(day, 0);
    return (t, rt) => {
      pop(reg, t, 0.2); pop(aws, t, 0.9); pop(dr, t, S.at[0] + 1.6); show(repl, t, S.at[0] + 2.2); rf(rt, t > S.at[0] + 2.6 ? 0 : 1e9);
      pop(pc, t, S.at[1] + 0.2); pops(pools, t, S.at[1] + 0.8, 0.5);
      pop(cc, t, S.at[2] + 0.2); pop(md, t, S.at[2] + 0.6); pops(mdp, t, S.at[2] + 1.2, 0.4);
      pop(rtp, t, S.at[3] + 0.2); pop(mach, t, S.at[3] + 0.8); pop(sup, t, S.at[3] + 1.6); pop(cag, t, S.at[3] + 2.0);
      const n = Math.round(cells.length * k(t, S.at[4] + 0.6, S.to[4] - 0.4));
      cells.forEach((e, i) => op(e, i < n ? 0.85 : 0));
      if (t > S.at[4]) bob(sup, rt, 1.5);
      pop(sv, t, S.at[5] + 0.2); pops(svp, t, S.at[5] + 0.6, 0.35);
      pops(am, t, S.at[6] + 0.2, 0.45); pop(brokers, t, S.at[6] + 2.6); show(nat, t, S.at[6] + 3.0);
      fl(rt, t > S.at[6] + 3.4 ? 0 : 1e9);
      pops(pipe, t, S.at[7] + 0.2, 0.4); pl.forEach((l, i) => show(l, t, S.at[7] + 0.5 + i * 0.4)); pop(tf, t, S.at[7] + 2.4);
      travel(rel, relPath, t, S.at[7] + 2.4, S.to[7] - 0.3);
      const dv = Math.min(k(t, S.at[8], S.at[8] + 0.5), 1);
      op(day, dv); op(B, 1 - 0.88 * dv); op(L, 1 - 0.88 * dv); op(M, 1 - 0.88 * dv);
      const h = lerp(8.4, 16.8, k(t, S.at[8] + 0.4, S.to[8]));
      hand.setAttribute('x1', tx(h)); hand.setAttribute('x2', tx(h));
    };
  });
}

// 7. The stores, one job each.
{
  const S = beats([
    ['Now the stores. Every store has one job, and only the service that owns a store connects to it. Nobody reads another service’s tables.', { focus: 'all' }],
    ['Postgres is the relational database, for anything that needs transactions. Amazon RDS runs it, with a standby copy in a second zone. There is one database per owner: control, a ledger shard for each cell, product, identity, commerce, refdata, and integrity.', { focus: ['pg'], spot: ['pg'] }],
    ['Each uses row-level security. Every transaction sets its tenant, and Postgres hides every row that belongs to any other tenant, so a query that forgets its filter still can’t see someone else’s data.', { spot: ['rls'] }],
    ['ClickHouse is a columnar database: it stores each column together, so it can scan millions of rows a second for a chart. It holds bars, strategy decisions, a copy of every fill, and the read models behind Geek mode and reports.', { focus: 'all', spot: ['ch'] }],
    ['The lake is Parquet files on S3: every tick, option chain and fill, and the reference data as it was on each day. They are organised as Apache Iceberg tables, an open table format. Careful: that is a file format, not our Iceberg service. Same word, two different things.', { spot: ['lake', 'neq'] }],
    ['Redis, on ElastiCache, keeps small things that expire: login sessions, rate-limit counters and small caches. Etcd keeps the leases and epochs in each cell; every reader sees its changes in the same order, which is what a lease needs. Vault and KMS keep broker tokens and signing keys, and only Kraken can unseal a token.', { spot: ['redis', 'etcd', 'vault'] }],
    ['Redpanda keeps the event logs where losing a message would cost money or history: intents, orders, the ledger journal, the tick archive, and strategy decisions. Each is ordered by its key: the account, the instrument, or the deployment.', { spot: ['rp'] }],
    ['NATS carries commands to the cells, kept for a day, and live updates to screens, where only the latest value matters. Temporal runs long jobs, like the end-of-day chain, and resumes them where they stopped.', { spot: ['nats', 'temporal'] }],
  ]);
  scene({ title: 'The stores, one job each', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const pg = named('pg', area(B, 40, 116, 1010, 290, 'Postgres', 'cell', 'on Amazon RDS · a standby in a second zone'));
    const dbs = [['control', 'tenants, versions, deployments'], ['ledger shard', 'orders, journal, outbox'], ['product', 'notes, repair cases, prefs'],
      ['identity', 'users, sessions, API keys'], ['commerce', 'plans, invoices'], ['refdata', 'instruments, symbols'], ['integrity', 'checks, cases']]
      .map(([n, s], i) => db(B, 58 + i * 140, 162, 132, 150, n, s));
    const rls = named('rls', box(B, 58, 326, 972, 66, '', { cls: 'chip' }));
    const code = T(B, 76, 352, '', 'mono', 'start');
    T(rls, 76, 380, 'row-level security: every tenant-owned table, in every database', 'bs', 'start');
    const ch = named('ch', db(B, 1080, 130, 480, 270, 'ClickHouse', 'columnar: bars, strategy decisions, a copy of every fill, and the read models behind Geek mode and reports; by month and by tenant'));
    const lake = named('lake', db(B, 40, 432, 520, 150, 'S3 lake', 'Parquet files, as Apache Iceberg tables: ticks, chains, fills, reference data over time; by date, then underlying'));
    const neq = named('neq', box(B, 200, 592, 360, 36, 'Iceberg tables ≠ our Iceberg service', { cls: 'bad', lcls: 'bs' }));
    const redis = named('redis', db(B, 580, 432, 300, 176, 'Redis', 'ElastiCache: sessions, rate-limit counters, small caches; entries expire'));
    const etcd = named('etcd', db(B, 900, 432, 300, 176, 'etcd', 'leases and epochs, one per cell; one order of changes for every reader'));
    const vault = named('vault', db(B, 1220, 432, 340, 176, 'Vault + KMS', 'broker tokens and signing keys, keys per tenant; only Kraken unseals a token'));
    const rp = named('rp', area(B, 40, 662, 1010, 200, 'Redpanda', 'cell', 'event logs on disk, ordered by key'));
    const topics = ['orders.intent.v1 · by account · 7 days', 'orders.event.v1 · by account · 30 days, then the lake', 'ledger.journal.v1 · by account · 30 days, then the lake',
      'md.archive.v1 · by instrument · 3 days, then the lake', 'strategy.decision.v1 · by deployment · into ClickHouse'];
    const tp = topics.map((s, i) => topic(B, 60, 704 + i * 31, 970, s, 'b', { h: 27, lcls: 'tlab' }));
    const nats = named('nats', area(B, 1080, 640, 480, 140, 'NATS', 'cell'));
    const ns = [['ctl.cell.<id>.*', 'commands, kept 1 day'], ['live.<tenant>.<deployment>', 'latest only']].map(([s, w], i) => {
      const tpc = topic(B, 1096, 682 + i * 46, 448, s, 'pu', { h: 34, lcls: 'tlab' }); T(tpc, 1530, 682 + i * 46 + 23, w, 'bs', 'end'); return tpc; });
    const temporal = named('temporal', box(B, 1080, 792, 480, 70, 'Temporal', { sub: 'long jobs that resume where they stopped', cls: 'store' }));
    const flows = tp.map((x, i) => stream(M, x.pipe, 'b', 4, 2.2 + i * 0.3, 5));
    const nflows = ns.map((x, i) => stream(M, x.pipe, 'pu', 3, 1.6 + i * 0.5, 5));
    const things = [pg, ...dbs, rls, ch, lake, redis, etcd, vault, rp, ...tp, nats, ...ns, temporal];
    return (t, rt) => {
      pops(things, t, 0.2, (S.to[0] - 0.8) / things.length);
      op(neq, 0); pop(neq, t, S.at[4] + 3.0);
      typeOn(code, t, S.at[2] + 0.3, S.to[2] - 1, "USING (tenant_id = current_setting('linck.tenant_id')::uuid)", rt);
      walk(dbs, t, S.at[1] + 1.4, S.to[1]);
      flash(rls, t, S.at[2], 0.5);
      flows.forEach(f => f(rt, t > S.at[6] + 0.4 ? 0 : 1e9));
      nflows.forEach(f => f(rt, t > S.at[7] + 0.4 ? 0 : 1e9));
    };
  });
}
