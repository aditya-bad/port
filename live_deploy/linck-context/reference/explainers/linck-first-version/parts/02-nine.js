// 3. The nine deployables, one by one.
{
  const G9 = [[40, 116], [552, 116], [1064, 116], [40, 370], [552, 370], [1064, 370], [40, 624], [552, 624], [1064, 624]];
  const NINE = [
    ['core', 'core-api', 'Python', 'Deployment', 'mixed', 'A modular monolith: one team, one database. Each module owns its tables and never joins another’s.', ['product API (screens)', 'deployments (control)', 'identity, sessions', 'instruments (resolver)']],
    ['live', 'live-gateway', 'Go', 'Deployment', 'lens', 'SSE to screens, latest values only. The same service as in the final.', ['NATS subscriber', 'latest slot per screen', 'SSE writer']],
    ['notif', 'notifications', 'Python', 'consumer', 'platform', 'Reads events from NATS and sends the alerts.', ['event reader', 'channels']],
    ['md', 'market-data', 'Go', 'Deployment', 'trade', 'Kite’s WebSocket plus one licensed vendor. Bars and chain views inside; latest prices in Redis.', ['Kite + vendor feeds', 'bars, chain views', 'latest prices → Redis', 'conflated gRPC streams']],
    ['runner', 'runner', 'Python', 'StatefulSet', 'trade', 'Leased workers, each a shard of strategies. Python strategies only, in gVisor.', ['lease in Postgres', 'one slot per instrument', 'deployment_state rows', 'indicators in strategy']],
    ['oms', 'oms', 'Go', 'StatefulSet', 'trade', 'Heimdall from day one: single writer, risk pipeline, outbox.', ['risk pipeline', 'outbox', 'positions + lots tables', 'Kite and paper routes']],
    ['bgw', 'broker-gw-kite', 'Go', 'Deployment', 'trade', 'Kraken with one arm: Kite. Paper orders go to paper-broker.', ['Kite adapter', 'tag: our order id', 'rate limits', 'update stream']],
    ['sim', 'paper-broker', 'Python', 'Deployment', 'trade', 'A pretend broker: fills paper orders, behind the same contract as Kraken.', ['fill model', 'BrokerAdapter contract']],
    ['workers', 'workers', 'Python', 'Temporal workers', 'trade', 'Today’s code, moved: day check, repair, replay, backtests.', ['day check', 'repair', 'replay', 'backtests']],
  ];
  const S = beats([
    ['The first version has nine deployables of our own. Here is each one: what it is written in, and what it does. The colour band says which product it belongs to: blue for LinckTrade, purple for LinckLens, orange for Linck core, and black for core-api, which holds a little of each.', { focus: 'all' }],
    ['Core-api, in Python, is a modular monolith: one program, one team, one database. Its modules are the product API for the screens, deployments, which is control’s job, identity, and instruments, today’s resolver. Each module owns its tables and never joins another’s, so splitting one out later is a move, not a rewrite.', { focus: ['core'], spot: ['core'], pad: 8 }],
    ['The live gateway, in Go, is the same service as in the final: SSE to screens, latest values only.', { focus: ['live'], spot: ['live'], pad: 8 }],
    ['Notifications is a small Python consumer: it reads events from NATS and sends the alerts.', { focus: ['notif'], spot: ['notif'], pad: 8 }],
    ['Market-data, in Go, takes Kite’s WebSocket and one licensed vendor, cleans the ticks, builds bars and chain views inside itself, keeps the latest prices in Redis, and streams ticks over conflated gRPC to the runner workers.', { focus: ['md'], spot: ['md'], pad: 8 }],
    ['Runner, in Python, runs as a StatefulSet. Each worker holds a lease in Postgres for its shard of strategies, keeps one latest slot per instrument, and saves each strategy’s state as a deployment_state row. Strategies compute their own indicators, and only Python strategies run, in gVisor.', { focus: ['runner'], spot: ['runner'], pad: 8 }],
    ['The OMS, in Go, is Heimdall from day one: the single writer, the risk pipeline and the outbox, writing the positions and lots tables in the one Postgres.', { focus: ['oms'], spot: ['oms'], pad: 8 }],
    ['Broker-gw-kite, in Go, is Kraken with one arm: Kite. Paper orders are routed to paper-broker.', { focus: ['bgw'], spot: ['bgw'], pad: 8 }],
    ['Paper-broker, in Python, is a pretend broker: a fill model behind the same broker contract that Kraken speaks, so paper orders are filled the way a broker would.', { focus: ['sim'], spot: ['sim'], pad: 8 }],
    ['The workers are Python Temporal workers running today’s code, moved: the day check, repair, replay and backtests.', { focus: ['workers'], spot: ['workers'], pad: 8 }],
    ['All nine run on one Kubernetes cluster, or one Compose host, at a home site the owner chooses. It has three node pools: services, runtime with gVisor, and data. About six to ten machines, an estimate. NATS JetStream on three nodes carries every event, beside one Postgres, ClickHouse, Redis, Temporal and the lake.', { focus: 'all' }],
  ]);
  scene({ title: 'The first version: nine deployables', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const sv = NINE.map(([id, n, lang, kind, part, does, comps], i) => named(id, svc(B, ...G9[i], 496, 236, n, { lang, kind, part, does, comps, cols: 2, ch: 34 })));
    const sz = G(M);
    box(sz, 160, 230, 1280, 440, '', { cls: 'frame key' });
    const lines = ['one Kubernetes cluster, or one Compose host', 'at a home site you choose', 'node pools: services · runtime (gVisor) · data', '~6–10 machines (estimate)', 'NATS JetStream ×3 · Postgres · ClickHouse · Redis · Temporal · the lake'];
    const lt = lines.map((s, i) => T(sz, 200, 300 + i * 74, '', i < 3 ? 'bigs' : 'cl', 'start'));
    sz.querySelectorAll('.bx').forEach(b => op(b, 1));
    op(sz, 0);
    return (t, rt) => {
      pops(sv, t, 0.2, (S.to[0] * 0.5) / 9);
      walk(sv, t, S.to[0] * 0.55, S.to[0] - 0.3);
      sv.forEach((s, i) => { const c = Object.values(s.comp); walk(c, t, S.at[i + 1] + 0.4, S.to[i + 1] - 0.2); });
      const z = k(t, S.at[10], S.at[10] + 0.5);
      op(B, 1 - 0.88 * z); op(sz, z);
      const per = (S.to[10] - S.at[10] - 0.8) / lines.length;
      lt.forEach((e, i) => typeOn(e, t, S.at[10] + 0.5 + i * per, S.at[10] + 0.5 + (i + 0.9) * per, lines[i], rt));
    };
  });
}

