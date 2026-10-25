// 5. The flows.
flowScene('How things flow', (L, B, M) => {
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
  ['Now how things flow through it: a price, an order, its fill, a screen, and a deploy.', { focus: 'all' }],
  ['A price: Kite’s WebSocket and the vendor send ticks to md, in Go, which cleans them, builds bars and chain views, keeps the latest prices in Redis, and streams ticks over conflated gRPC to each runner worker. The worker keeps one latest slot per instrument, and each strategy computes its own indicators.', {}, [0, 1], 'tick'],
  ['An order: the strategy returns an intent, and its runner worker publishes it on NATS JetStream. The OMS checks it and records the order in the positions and lots tables in one transaction, then calls broker-gw-kite over gRPC, which places it at Kite. A paper order goes to sim instead.', {}, [2, 3, 4, 5, 6], 'intent'],
  ['The fill: Kite’s update comes back; the OMS records the fill and publishes it on NATS, and the live gateway pushes it over SSE to your screen.', {}, [7, 8, 9, 10, 11], 'fill'],
  ['A screen asks core-api through the load balancer, over HTTPS, and core-api reads Postgres and ClickHouse.', {}, [12, 13], 'request'],
  ['And a deploy: core-api’s deployments module checks the version and publishes a start command on NATS. A runner worker takes the lease in Postgres and starts the strategy, in gVisor.', {}, [14, 15], 'start'],
]);

