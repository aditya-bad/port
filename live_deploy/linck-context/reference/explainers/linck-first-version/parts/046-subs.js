// 5c. Different strategies need different instruments: subscriptions, grouping, and what it costs.
{
  const ROWS = [
    ['weekly options', ['NIFTY', '25600 CE wk', '25400 PE wk'], 'W', 'A'],
    ['monthly options', ['NIFTY', '25600 CE mo', '25400 PE mo'], 'M', 'B'],
    ['equity', ['RELIANCE', 'HDFCBANK', 'INFY'], 'E', 'C'],
  ];
  const RY = [130, 330, 530];
  const S = beats([
    ['Strategies don’t all need the same prices. A weekly-options strategy, a monthly-options strategy and an equity strategy each need a different set of instruments. Here is how that stays efficient.', { focus: 'all' }],
    ['Each strategy declares its instruments when it starts: NIFTY and this week’s strikes, or this month’s, or a list of stocks. Nothing else is ever sent to it.', { spot: ['cards'] }],
    ['The plan is to place strategies that need the same instruments on the same runner worker: weekly options on worker A, monthly on worker B, equities on worker C. So each worker asks for a small set, not the whole market.', { spot: ['cards', 'workers'] }],
    ['Market-data keeps slots only for what each stream asked for. Worker A’s slots hold weekly strikes, worker B’s hold monthly ones, worker C’s hold stocks. An instrument nobody asked for never leaves market-data.', { spot: ['workers', 'sets'] }],
    ['And each instrument comes from Kite or the vendor only once, however many workers want it. NIFTY goes to two workers here, from one subscription; copying it into a second slot is cheap.', { spot: ['sets', 'src'] }],
    ['Inside a worker, an index says which strategies follow each instrument. When a weekly strike changes, only the weekly strategies holding it get it in their next batch. The others aren’t woken.', { spot: ['cardW', 'gapA', 'wA', 'gapB', 'sets'] }],
    ['Option strategies move strikes as the market moves. When a strategy asks for a new strike, its worker adds it to the stream and market-data opens a slot. A strike nobody follows any more is dropped.', { spot: ['cardW', 'wA', 'sets'] }],
    ['And it is cheap. A slot is small, about 128 bytes in the design, so even five thousand instruments in one worker’s set is under a megabyte. The slot updates measured three to nineteen million a second on one core; the first version expects about twenty thousand a second at the open, an estimate.', { spot: ['cost'], pad: 3 }],
  ]);
  scene({ title: 'Different strategies, different instruments', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    named('cards', bounds(G(B), 40, 130, 400, 580)); named('workers', bounds(G(B), 490, 130, 330, 580));
    named('sets', bounds(G(B), 870, 116, 420, 600)); named('gapA', bounds(G(B), 40, 130, 450, 206)); named('gapB', bounds(G(B), 820, 130, 50, 190));
    // strategy cards
    const cards = ROWS.map(([n, ins, s], r) => {
      const y = RY[r], c = box(B, 40, y, 400, 180, '', { cls: 'chip' }); if (r === 0) named('cardW', c);
      T(c, 58, y + 28, `${n} strategies`, 'cl', 'start');
      const chips = ins.map((i, j) => box(B, 56 + j * 128, y + 44, 120, 40, i, { cls: 'key', lcls: 'cmp' }));
      const st = [1, 2, 3].map(j => box(B, 56 + (j - 1) * 128, y + 120, 120, 40, `${s}${j}`, { cls: 'chip', lcls: 'cmp' }));
      return { c, chips, st };
    });
    const newChip = box(B, 56 + 2 * 128, RY[0] + 44, 120, 40, '25700 CE wk', { cls: 'key', lcls: 'cmp' });
    const z = zzz(M, 56 + 128 + 96, RY[0] + 128);
    // workers
    const workers = ROWS.map(([, ins, , w], r) => {
      const y = RY[r], b = box(B, 490, y, 330, 180, '', { cls: 'chip' }); if (r === 0) named('wA', b);
      T(b, 510, y + 30, `runner worker ${w}`, 'cl', 'start');
      const ask0 = T(B, 510, y + 60, '', 'bs', 'start'), ask = T(B, 510, y + 82, '', 'bs', 'start'), idx = T(B, 510, y + 120, '', 'bs', 'start'), idx2 = T(B, 510, y + 144, '', 'bs', 'start');
      return { b, ask0, ask, idx, idx2, ins };
    });
    // market-data and its slot sets
    const mdx = box(B, 870, 116, 420, 600, '', { cls: 'frame key' }); T(mdx, 890, 146, 'market-data', 'cl', 'start');
    const sets = ROWS.map(([, ins, , w], r) => {
      const y = RY[r] + 30, g = box(B, 886, y, 388, 140, '', { cls: 'chip' }); if (r === 0) named('setA', g);
      T(g, 902, y + 24, `slots for worker ${w} only`, 'bs', 'start');
      const rows = ins.map((n, j) => ({ n: T(g, 902, y + 56 + j * 26, n, 'mono', 'start'), v: T(g, 1256, y + 56 + j * 26, '', 'mono', 'end') }));
      return { g, rows, y };
    });
    const src = named('src', box(B, 1340, 330, 220, 110, 'Kite + vendor', { sub: 'one subscription each', cls: 'out' }));
    const feed = sets.map((s, r) => line(L, `M1336 385C1310 385 1300 ${s.y + 70} 1278 ${s.y + 70}`, 'b'));
    const strm = workers.map((w, r) => line(L, `M882 ${sets[r].y + 70}H826`, 'b'));
    const toC = cards.map((c, r) => line(L, `M486 ${RY[r] + 90}H446`, 'b'));
    const fs = feed.map(l => stream(M, l, 'b', 2, 1.4)), ss = strm.map(l => stream(M, l, 'b', 2, 1.2)), cs = toC.map(l => stream(M, l, 'b', 2, 1.2));
    const nifty = [packet(M, 'NIFTY', 'b'), packet(M, 'NIFTY', 'b')];
    const tick = packet(M, '25600 CE wk', 'g');
    const tickPath = route(M, [[1160, RY[0] + 100], [460, RY[0] + 100], [460, RY[0] + 190], [116, RY[0] + 190], [116, RY[0] + 164]]);
    const tick2 = packet(M, '', 'g', { w: 20 });
    const tickPath2 = route(M, [[116, RY[0] + 190], [372, RY[0] + 190], [372, RY[0] + 164]]);
    const cost = named('cost', box(B, 40, 730, 1520, 130, '', { cls: 'frame' }));
    const ct = typeList(cost, 64, 774, ['a slot ≈ 128 bytes (design) → 5,000 instruments ≈ 640 KB per worker', 'slot updates: 3–19 million a second on one core (measured) · first version at the open: ~20,000 a second (estimate)'], 130, 'cl', 30, 8);
    return (t, rt) => {
      cards.forEach(({ c, chips, st }, r) => { pop(c, t, 0.3 + r * 0.3); pops(chips, t, 0.6 + r * 0.3, 0.15); pops(st, t, 0.9 + r * 0.3, 0.1); });
      pop(mdx, t, 1.6); pops(sets.map(s => s.g), t, 1.8, 0.25); pop(src, t, 2.4);
      if (t < S.at[1]) walk(cards.map(c => c.c), t, 3.4, S.to[0] - 0.3);
      if (t > S.at[1]) cards.forEach(({ chips }, r) => walk(chips, t, S.at[1] + 0.4 + r * (S.to[1] - S.at[1] - 0.6) / 3, S.at[1] + (r + 1) * (S.to[1] - S.at[1] - 0.6) / 3));
      workers.forEach(({ b, ask0, ask, idx, idx2, ins }, r) => {
        const a = S.at[2] + 0.4 + r * (S.to[2] - S.at[2] - 1) / 3;
        pop(b, t, a); show(toC[r], t, a + 0.3);
        typeAt(ask0, t, a + 0.4, a + 0.9, 'asks for:', rt);
        typeAt(ask, t, a + 0.9, a + 2.2, ins.join(', '), rt);
        if (r === 0 && t > S.at[6] + 1.2) txt(ask, 'NIFTY, 25600 CE wk, 25700 CE wk');
        cs[r](rt, t > a + 0.8 ? 0 : 1e9);
      });
      sets.forEach(({ g, rows }, r) => {
        show(strm[r], t, S.at[3] + 0.3 + r * 0.5); ss[r](rt, t > S.at[3] + 0.8 + r * 0.5 ? 0 : 1e9);
        if (t > S.at[3]) flash(g, t, S.at[3] + 0.3 + r * (S.to[3] - S.at[3]) / 3, 1.2);
        rows.forEach(({ n, v }, j) => {
          const base = r < 2 ? [25186, 112, 96][j] : [2950, 1712, 1530][j];
          op(v, t > S.at[3] ? 1 : 0); txt(v, t > S.at[3] ? fmt(base + Math.round(Math.sin(rt * (1.5 + j * 0.3) + r) * (j === 0 || r === 2 ? 9 : 4))) : '');
          if (r === 0 && j === 2) { txt(n, t > S.at[6] + 1.2 ? '25700 CE wk' : '25400 PE wk'); }
        });
      });
      feed.forEach((l, r) => show(l, t, S.at[4] + 0.2 + r * 0.3)); fs.forEach((f, r) => f(rt, t > S.at[4] + 0.6 ? 0 : 1e9));
      // NIFTY: one subscription at the source, copied into worker A's and B's slots
      nifty.forEach((pk, r) => travel(pk, feed[r], t, S.at[4] + 1.6, S.at[4] + 3.4));
      if (t > S.at[4] + 3.4 && t < S.to[4]) { flash(sets[0].g, t, S.at[4] + 3.4, 1.0); flash(sets[1].g, t, S.at[4] + 3.4, 1.0); }
      // the index: a weekly strike changes; only W1 and W3 hold it
      const ia = S.at[5] + 0.3;
      typeAt(workers[0].idx, t, ia, ia + 1.4, 'index: 25600 CE → W1, W3', rt); typeAt(workers[0].idx2, t, ia + 1.4, ia + 2.6, '       25400 PE → W2', rt);
      if (t > S.at[6] + 1.2) txt(workers[0].idx2, '       25700 CE → W2');
      travel(tick, tickPath, t, ia + 2.6, ia + 5.6);
      travel(tick2, tickPath2, t, ia + 5.0, ia + 6.6);
      if (t > ia + 5.4 && t < S.to[5]) { flash(cards[0].st[0], t, ia + 5.4, 1.2); flash(cards[0].st[2], t, ia + 6.4, 1.2); }
      z.go(rt, t > ia + 5.4 && t < S.to[5]);
      // a strike changes: 25400 PE dropped, 25700 CE added
      const sa = S.at[6] + 0.4;
      if (t > S.at[6]) { op(cards[0].chips[2], 1 - k(t, sa, sa + 0.6)); pop(newChip, t, sa + 0.6); } else op(newChip, 0);
      if (t > S.at[6] + 1.2 && t < S.to[6]) flash(sets[0].g, t, S.at[6] + 1.2, 1.4);
      pop(cost, t, S.at[7] + 0.2); ct.go(t, S.at[7] + 0.5, S.to[7] - 0.4, rt);
    };
  });
}
