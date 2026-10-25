// 4. The stores: what each holds, and run it yourself or managed.
{
  const ST = [
    ['pg', 'Postgres', 'primary + replica', 'CloudNativePG or Patroni', 'RDS · Azure Database · Cloud SQL'],
    ['nats', 'NATS JetStream', '3 nodes · every event', 'NATS', 'Synadia Cloud'],
    ['ch', 'ClickHouse', 'bars · reports · Geek', 'ClickHouse', 'ClickHouse Cloud'],
    ['redis', 'Redis', 'latest prices · sessions', 'Valkey or Redis', 'ElastiCache · Azure Cache · Memorystore'],
    ['tmp', 'Temporal', 'end-of-day workflows', 'Temporal', 'Temporal Cloud'],
    ['lake', 'Object storage', 'the lake: every tick', 'MinIO or Ceph', 'S3 · Azure Blob · GCS'],
  ];
  const S = beats([
    ['Under the nine sit six stores. Each is reached by an open protocol, so each can run on your own servers, or as a provider’s managed version that speaks the same protocol.', { focus: 'all' }],
    ['One Postgres, a primary and a replica, holds everything that needs a transaction: today’s tables, the positions and lots the OMS writes, deployments, the leases that say which runner worker runs which strategy, and each strategy’s saved state.', { spot: ['pg', 'r0'], pad: 3 }],
    ['NATS JetStream, on three nodes, carries every event: intents, fills and order updates, commands, and live updates for the screens. It keeps each message until it is read. Prices never go through it.', { spot: ['nats', 'r1'], pad: 3 }],
    ['ClickHouse holds the bars, reports and Geek mode charts, where scanning millions of rows fast matters. Redis keeps the latest price of each instrument, and the login sessions.', { spot: ['ch', 'redis', 'r2', 'r3'], pad: 3 }],
    ['Temporal runs the end-of-day chain, the day check and repair, and resumes it where it stopped. And the lake is plain Parquet files in object storage: every tick, kept for replays and backtests.', { spot: ['tmp', 'lake', 'r4', 'r5'], pad: 3 }],
    ['A provider’s managed store can stand in for any of them, but only if it speaks the same protocol and passes our tests for it. Nothing in the first version needs a store that only one provider has.', { spot: ['tbl'] }],
  ]);
  scene({ title: 'The stores', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const dbs = ST.map(([id, n, sub], i) => named(id, db(B, 40 + i * 256, 120, 240, 170, n, sub)));
    const tbl = named('tbl', bounds(G(B), 40, 320, 1520, 540));
    const hdr = G(B); [['store', 60], ['run it yourself', 420], ['or a managed one, if it passes the tests', 860]].forEach(([s, x]) => T(hdr, x, 342, s, 'fl', 'start')); op(hdr, 0);
    const rows = ST.map(([id, n, , self, man], i) => {
      const y = 360 + i * 82, g = named('r' + i, box(B, 40, y, 1520, 72, '', { cls: 'chip' }));
      T(g, 60, y + 44, n, 'cl', 'start');
      return { g, a: typer(g, 420, y + 44, self, 40, 'txt'), b: typer(g, 860, y + 44, man, 60, 'txt') };
    });
    const ok = stamp(M, 1380, 820, 'Any provider', 'g', { rot: -5 });
    const steps = [[0], [1], [2, 3], [4, 5]];
    return (t, rt) => {
      pops(dbs, t, 0.3, (S.to[0] - 1.2) / 6); fade(hdr, t, S.to[0] - 0.6);
      rows.forEach(({ g }, i) => pop(g, t, S.to[0] - 0.8 + i * 0.1));
      steps.forEach((ids, s) => ids.forEach((i, j) => {
        const a = S.at[s + 1] + 0.3 + j * (S.to[s + 1] - S.at[s + 1]) / ids.length, b = a + (S.to[s + 1] - S.at[s + 1]) / ids.length - 0.6;
        flash(dbs[i], t, a, 1.0);
        rows[i].a.go(t, a + 0.3, (a + b) / 2, rt); rows[i].b.go(t, (a + b) / 2, b, rt);
      }));
      if (t > S.at[5]) walk(rows.map(r => r.g), t, S.at[5] + 0.3, S.to[5] - 1.4);
      ok.go(t, S.to[5] - 1.2);
    };
  });
}
