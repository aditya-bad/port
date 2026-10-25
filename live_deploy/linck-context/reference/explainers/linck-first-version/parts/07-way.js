// 8. The way there: from today's app to the first version, one proven step at a time.
{
  const STEPS = [
    ['P0', 'Latest-value delivery in today’s app', 'each subscriber keeps one slot per instrument, one batch in flight', 'a slow-strategy test: after a stall, the next tick is the newest'],
    ['I0', 'Today’s app on one Kubernetes cluster, as core-api', 'images, Helm charts, a home site the owner picks', 'a week of paper days beside the current machine'],
    ['I1', 'A contracts repository; SDK v0 over today’s StrategyBase', 'the protobuf contracts every part will speak', 'the fault matrix and crash suites unchanged'],
    ['I2', 'Strategies out of the API, into leased runner workers', 'conflated gRPC ticks; intents and fills on NATS', 'a week of identical fills, run side by side'],
    ['I3', 'The Go OMS and the Kite gateway', 'shadow-write first, then the only writer of money', 'its journal equals today’s tables, every night for two weeks'],
    ['I4', 'Go market data; latest prices in Redis; bars in ClickHouse', 'md takes over the feeds', 'bars equal Kite’s one-minute candles'],
    ['I5', 'Temporal end of day; tenants with row-level security; shadow mode', 'the workers move into Temporal', 'end-of-day outputs equal; tenancy tests'],
  ];
  const S = beats([
    ['Getting there from today’s app. Each step ships on its own, runs beside what it replaces until it is proven equal, and never stops trading.', { focus: 'all' }],
    ['P0 happens inside today’s app. The tick broadcaster keeps one slot per instrument for each subscriber, with one batch in flight, so a slow strategy always reads the newest price. A slow-strategy test proves it.', { spot: ['m0'], pad: 3 }],
    ['I0 puts today’s app, unchanged, on one Kubernetes cluster at the home site, as core-api, proven by a week of paper days beside the current machine. I1 adds the contracts repository, and SDK version zero over today’s strategy base class.', { spot: ['m1', 'm2'], pad: 3 }],
    ['I2 moves the strategies out of the API, into leased Python runner workers, fed by conflated gRPC ticks, with intents and fills on NATS. A week of identical fills, side by side, proves it.', { spot: ['m3'], pad: 3 }],
    ['I3 brings the Go OMS and the Kite gateway. They write in shadow first, beside today’s fill path, and become the only writer of money once the journal and today’s tables match every night for two weeks.', { spot: ['m4'], pad: 3 }],
    ['I4 adds the Go market data service, with the latest prices in Redis and bars in ClickHouse, proven by bars equal to Kite’s one-minute candles. I5 adds Temporal for the end of day, tenants with row-level security, and shadow mode.', { spot: ['m5', 'm6'], pad: 3 }],
    ['After I5, the first version is complete. The final architecture builds on it later, and that part is paused for now.', { spot: ['next'] }],
  ]);
  scene({ title: 'The way there', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const rows = STEPS.map(([id, what, how, proof], i) => {
      const y = 116 + i * 100, g = named('m' + i, box(B, 40, y, 1520, 90, '', { cls: 'chip' }));
      badge(g, 60, y + 34, id, 'kind', 'start');
      T(g, 120, y + 34, what, 'cl', 'start');
      T(g, 120, y + 64, how, 'bs', 'start');
      g._p = T(g, 1520, y + 64, '', 'okl', 'end');
      g._ck = check(g, 1520, y + 30, true);
      return g;
    });
    const next = named('next', box(B, 40, 822, 1520, 44, 'then: the final architecture (paused for now)', { cls: 'out', lcls: 'cl' }));
    const groups = [[0], [1, 2], [3], [4], [5, 6]];
    const done = burst(M, 1400, 760, { seed: 7 });
    return (t, rt) => {
      rows.forEach((g, i) => { pop(g, t, 0.3 + i * (S.to[0] - 0.8) / rows.length); if (t < S.at[groups.findIndex(gr => gr.includes(i)) + 1]) op(g, Math.min(0.4, +g.getAttribute('opacity'))); });
      groups.forEach((gr, s) => gr.forEach((i, n) => {
        const a = S.at[s + 1] + 0.3 + n * (S.to[s + 1] - S.at[s + 1] - 0.4) / gr.length, b = a + (S.to[s + 1] - S.at[s + 1] - 0.4) / gr.length;
        flash(rows[i], t, a, 0.9); typeAt(rows[i]._p, t, a + 0.4, b - 0.6, 'proven by: ' + STEPS[i][3], rt); pop(rows[i]._ck, t, b - 0.4);
      }));
      pop(next, t, S.at[6] + 0.3); done.go(t, S.at[6] + 0.6);
    };
  });
}

// 9. Recap.
{
  const lines = [
    ['Nine deployables, from today’s code.', 'The trading path, core-api, the live gateway, notifications and the workers; six stores under them.'],
    ['One home site, any provider.', 'Everything that writes the one Postgres stays together; the rest may run anywhere from day one.'],
    ['One proven step at a time.', 'P0, then I0 to I5, each beside what it replaces until it is equal.'],
  ];
  const S = beats([
    ['To recap. The first version is nine deployables of our own, built from today’s code, over six stores that each have a version you can run yourself.', { focus: 'all' }],
    ['It runs at one home site, on any cloud or your own servers. Everything that writes the one Postgres stays together there; the live gateway, notifications, backtests, the lake, a standby and the SDK can be anywhere from day one.', {}],
    ['And it gets built one proven step at a time: P0, then I0 to I5, each running beside what it replaces until it is equal, without ever stopping trading.', {}],
  ]);
  scene({ title: 'The first version, in short', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const rows = lines.map(([a, b], i) => {
      const g = box(B, 120, 150 + i * 220, 1360, 190, '', { cls: 'chip' });
      return { g, a: typer(g, 160, 150 + i * 220 + 72, a, 90, 'lane', 30), b: typer(g, 160, 150 + i * 220 + 130, b, 110, 'txt', 24) };
    });
    const sp = sparkle(M, 1420, 600, { r: 60 });
    return (t, rt) => {
      rows.forEach((r, i) => { pop(r.g, t, S.at[i] + 0.2); r.a.go(t, S.at[i] + 0.4, S.at[i] + (S.to[i] - S.at[i]) * 0.35, rt); r.b.go(t, S.at[i] + (S.to[i] - S.at[i]) * 0.4, S.to[i] - 0.3, rt); });
      sp.go(t, S.to[2] - 1.0);
    };
  });
}
