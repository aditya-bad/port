// 5b. A live price, all the way to a strategy: who connects to whom.
{
  const INS = [['NIFTY', 25186], ['BANKNIFTY', 57240], ['25600 CE', 112], ['25400 PE', 96]];
  const S = beats([
    ['Now follow one live price all the way to a strategy. Who connects to whom, and how the newest price always wins.', { focus: 'all' }],
    ['Ticks arrive from Kite and the data vendor at market-data. It cleans them, and keeps one slot per instrument. Each new tick overwrites the one before.', { spot: ['src', 'mdx'] }],
    ['Strategies never connect to market-data. Each runner worker does, once. A worker holds the lease on a share of the strategies, and opens one gRPC stream, asking for every instrument its strategies need.', { spot: ['mdx', 'wk', 'sl'] }],
    ['So two thousand strategies don’t mean two thousand connections: there are only as many streams as runner workers. Market-data keeps one set of slots for each stream.', { spot: ['mdx', 'wk', 'sl'] }],
    ['Each stream has one batch in flight. When a worker is ready, it gets every instrument that changed, at its newest price, and a count of the ticks it skipped. A slow worker never gets a queue of old prices.', { spot: ['mdx', 'wk', 'sl'] }],
    ['Inside the worker, the same idea again. It keeps its own slot per instrument, and each strategy has one batch in flight. When a strategy finishes, its next batch holds only its own instruments that changed, at their newest price.', { spot: ['mdx', 'wk', 'st'] }],
    ['Fills, order updates and timers are different. They are never skipped, and always arrive in order.', { spot: ['mdx', 'wk', 'st'] }],
    ['If a stream drops, the worker reconnects and gets a snapshot of the latest prices, never a replay of old ones. Every instrument carries a sequence number, so an older tick can never overwrite a newer one. And a price that stops moving is marked stale.', { spot: ['src', 'mdx', 'wk', 'st', 'sl'] }],
    ['At first, the strategies run inside the worker as Python objects, as they do today. Later each one becomes its own process, fed the same batches over a local socket. The path stays the same.', { spot: ['note'] }],
  ]);
  scene({ title: 'A live price, to a strategy', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const src = named('src', box(B, 40, 330, 240, 110, 'Kite + vendor', { sub: 'WebSocket ticks', cls: 'out' }));
    const mdx = named('mdx', box(B, 340, 116, 420, 600, '', { cls: 'frame key' }));
    T(mdx, 360, 146, 'market-data', 'cl', 'start'); T(mdx, 360, 170, 'one slot per instrument, per stream', 'bs', 'start');
    const WY = [130, 330, 530];
    // a set of slots for each worker's stream
    const sets = WY.map((wy, w) => {
      const y = 190 + w * 172, g = box(B, 356, y, 388, 160, '', { cls: 'chip' });
      T(g, 372, y + 24, `slots for worker ${'ABC'[w]}`, 'bs', 'start');
      const vals = INS.map(([n], i) => { T(g, 372, y + 52 + i * 26, n, 'mono', 'start'); return T(g, 724, y + 52 + i * 26, '', 'mono', 'end'); });
      return { g, vals, y };
    });
    const tickIn = line(L, 'M280 385H336', 'b');
    const tin = stream(M, tickIn, 'b', 4, 0.6);
    // workers and their strategies
    const wk = named('wk', bounds(G(B), 880, 116, 330, 600));
    const workers = WY.map((y, w) => { const b = box(B, 880, y, 330, 170, `runner worker ${'ABC'[w]}`, { sub: 'a lease on a share of strategies', cls: 'chip' }); return b; });
    const wslot = workers.map((b, w) => { const t_ = T(B, 900, WY[w] + 150, '', 'bs', 'start'); return t_; });
    const st = named('st', bounds(G(B), 1290, 116, 270, 600));
    const strat = WY.flatMap((y, w) => [0, 1, 2].map(j => box(B, 1290, y + j * 58, 270, 50, `strategy ${'ABC'[w]}${j + 1}`, { cls: 'chip', lcls: 'cmp' })));
    const streams = WY.map((y, w) => line(L, `M744 ${sets[w].y + 80}C810 ${sets[w].y + 80} 820 ${y + 85} 876 ${y + 85}`, 'b'));
    const toS = strat.map((s, i) => { const w = Math.floor(i / 3); return line(L, `M1210 ${WY[w] + 85}C1250 ${WY[w] + 85} 1250 ${s.cy} 1286 ${s.cy}`, 'b'); });
    const lab = T(L, 812, 742, 'one gRPC stream per worker', 'hop'); op(lab, 0);
    named('sl', bounds(G(B), 600, 718, 420, 62));
    const bpk = WY.map(() => packet(M, 'batch', 'b')), spk = strat.map(() => dot(M, 'b', 8));
    const fpk = strat.map(() => packet(M, 'fill', 'g', { w: 54 }));
    const skip = T(M, 812, 768, '', 'bs'); op(skip, 0);
    const cut = boom(M, 810, 330 + 85 - 40);
    const snap = bubble(M, 810, 520, 'snapshot', { below: true });
    const stale = box(M, 40, 560, 280, 56, 'silent price → stale', { cls: 'bad', lcls: 'cmp' });
    const note = named('note', box(B, 40, 760, 1520, 100, '', { cls: 'frame' }));
    const nt = typeList(note, 64, 800, ['first: strategies run inside the worker, as Python objects (as today)', 'later: each its own process, the same batches over a local socket'], 120, 'cl', 30, 6);
    let px = INS.map(([, v]) => v);
    return (t, rt) => {
      pop(src, t, 0.3); pop(mdx, t, 0.6); pops(sets.map(s => s.g), t, 1.0, 0.3);
      pops(workers, t, 1.8, 0.3); pops(strat, t, 2.6, 0.08);
      show(tickIn, t, S.at[1] + 0.2); tin(rt, t > S.at[1] + 0.6 ? 0 : 1e9);
      // prices keep moving while ticks arrive: every slot is overwritten
      const live = t > S.at[1] + 0.6;
      sets.forEach(({ vals }) => vals.forEach((e, i) => txt(e, live ? fmt(INS[i][1] + Math.round(Math.sin(rt * (1.7 + i * 0.4) + i) * (i < 2 ? 20 : 6))) : fmt(INS[i][1]))));
      if (live) sets.forEach(({ g }, w) => flash(g, t, S.at[1] + 0.8 + w * 0.6, 0.8));
      streams.forEach((s, w) => show(s, t, S.at[2] + 0.6 + w * 0.6)); fade(lab, t, S.at[2] + 1.0);
      toS.forEach((l, i) => show(l, t, S.at[5] + 0.2 + i * 0.12));
      // batches on each stream, one in flight, looping while it is said
      const down = t > S.at[7] + 0.4 && t < S.at[7] + (S.to[7] - S.at[7]) * 0.45;
      bpk.forEach((pk, w) => {
        if (t < S.at[4] || (w === 1 && down)) { op(pk, 0); return; }
        const f = ((rt * 0.45 + w * 0.33) % 1); placeOn(pk, streams[w], f); op(pk, Math.min(1, f * 6, (1 - f) * 6));
      });
      if (t > S.at[4]) { op(skip, 1); txt(skip, `changed 3 · skipped ${18 + Math.floor((rt * 9) % 40)}`); } else op(skip, 0);
      spk.forEach((pk, i) => {
        if (t < S.at[5] + 0.6) { op(pk, 0); return; }
        const f = ((rt * 0.6 + i * 0.137) % 1); placeOn(pk, toS[i], f); op(pk, Math.min(1, f * 6, (1 - f) * 6));
      });
      fpk.forEach((pk, i) => {
        if (t < S.at[6] + 0.3 || t > S.to[6] || i % 3) { op(pk, 0); return; }
        const f = k(t, S.at[6] + 0.4 + (i / 3) * 0.6, S.at[6] + 2.4 + (i / 3) * 0.6); placeOn(pk, toS[i], f); op(pk, f > 0 && f < 1 ? 1 : 0);
      });
      if (down) { pop(cut, t, S.at[7] + 0.4); op(streams[1], 0.25); } else { op(cut, 0); if (t > S.at[7]) op(streams[1], 1); }
      snap.go(t, S.at[7] + (S.to[7] - S.at[7]) * 0.45, S.at[7] + (S.to[7] - S.at[7]) * 0.75, rt);
      if (t > S.at[7] + (S.to[7] - S.at[7]) * 0.75 && t < S.to[7]) pop(stale, t, S.at[7] + (S.to[7] - S.at[7]) * 0.75); else op(stale, 0);
      workers.forEach((b, w) => { const on = t > S.at[5]; txt(wslot[w], on ? 'its own slot per instrument' : ''); op(wslot[w], on ? 1 : 0); });
      pop(note, t, S.at[8] + 0.2); nt.go(t, S.at[8] + 0.5, S.to[8] - 0.4, rt);
    };
  });
}
