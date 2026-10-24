// 1. Who this is for, and what it covers.
{
  const ch = ['What you write, and ctx', 'The manifest', 'The same strategy in Go', 'What the SDK does for you', 'Determinism',
    'From your laptop to live money', 'Each language, measured', 'WASM, remote, indicators', 'The tests every SDK passes', 'How many strategies fit on a machine'];
  const S = beats([
    ['This video is for developers who write strategies for Linck.', { focus: 'all' }],
    ['What you write, and the one object that connects it to the world. The manifest. The same strategy in Go. What the SDK does for you, and the rules that keep a strategy exact. How it gets from your laptop to live money. How each language runs, with measured numbers. WASM and remote strategies, indicators, and the tests every SDK must pass.', {}],
    ['And at the end, one number from the architecture video, how many strategies fit on a machine, and how to read it.', { spot: ['last'] }],
  ]);
  scene({ title: 'The Linck SDK', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const rows = ch.map((s, i) => {
      const y = 150 + i * 66, g = G(B);
      E('circle', { cx: 300, cy: y, r: 17, class: i === 9 ? 'f-r' : 'f-ink' }, g); T(g, 300, y + 6, String(i + 1), 'pt');
      const tx = T(g, 340, y + 9, '', 'lane', 'start'); op(g, 0);
      return { g, tx, s };
    });
    named('last', bounds(G(B), 270, 120 + 9 * 66, 900, 62));
    return (t, rt) => {
      const per = (S.to[1] - S.at[1] - 0.4) / 8;
      rows.forEach((r, i) => {
        const a = i === 0 ? 0.4 : i < 9 ? S.at[1] + 0.2 + (i - 1) * per : S.at[2] + 0.4;
        const b = i === 0 ? S.to[0] - 0.2 : i < 9 ? a + per * 0.9 : S.to[2] - 0.4;
        pop(r.g, t, a - 0.1); typeAt(r.tx, t, a, b, r.s, rt);
      });
    };
  });
}

// 2. The one idea.
{
  const S = beats([
    ['One idea makes everything else possible. The runtime owns time, state and the inputs. A strategy, in any language, is a function behind a protocol.', { focus: 'all' }],
    ['Given its saved state and a numbered batch, it returns orders, decisions, and its new state. Nothing else goes in, and nothing else comes out.', {}],
    ['So a live day, a backtest, a replay, a repair and a run on your laptop are all the same thing: the runtime feeding numbered batches to that function. Only the clock, the market and the broker change.', {}],
    ['None of that is written per language. A strategy in Go is walked, restarted and replayed exactly the way a Python one is: by the runtime, never by the strategy.', {}],
  ]);
  scene({ title: 'The one idea', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const fn = box(B, 620, 330, 360, 170, 'your strategy', { sub: 'a function behind a protocol', cls: 'key' });
    const st = box(B, 140, 270, 300, 90, 'saved state', { cls: 'chip' });
    const bt = box(B, 140, 470, 300, 90, 'batch #n', { cls: 'chip' });
    const outs = ['orders', 'decisions', 'new state'].map((s, i) => box(B, 1160, 250 + i * 120, 300, 90, s, { cls: 'chip' }));
    const lin = [link(L, st, fn, 'b'), link(L, bt, fn, 'b')], lout = outs.map(o => link(L, fn, o, 'g'));
    const pin = [packet(M, 'state', 'b'), packet(M, '#n', 'b')], pout = ['orders', 'decisions', 'state′'].map(s => packet(M, s, 'g'));
    const src = ['live', 'backtest', 'replay', 'repair', 'your laptop'].map((s, i) => box(B, 80 + i * 300, 640, 270, 80, s, { cls: 'out' }));
    const feeds = src.map(s => route(M, [[s.cx, s.Y], [s.cx, 600], [290, 600], [290, 566]]));
    const fp = src.map(() => packet(M, 'batch', 'b'));
    const langs = ['Python', 'Go', 'Rust', 'TypeScript'].map((s, i) => { const g = G(B); badge(g, 650 + i * 92, 300, s, 'lang', 'start'); op(g, 0); return g; });
    const own = typer(B, 800, 250, 'the runtime owns time, state and the inputs', 60, 'cl', 24, 'middle');
    const nope = typer(B, 800, 790, 'walked · restarted · replayed: by the runtime, the same way for every language', 90, 'cl', 24, 'middle');
    return (t, rt) => {
      pop(fn, t, 0.4); pop(st, t, 1.2); pop(bt, t, 1.5); own.go(t, 1.8, S.to[0] - 0.2, rt);
      show(lin[0], t, S.at[1] + 0.2); show(lin[1], t, S.at[1] + 0.4);
      travel(pin[0], lin[0], t, S.at[1] + 0.6, S.at[1] + 1.6); travel(pin[1], lin[1], t, S.at[1] + 0.9, S.at[1] + 1.9);
      flash(fn, t, S.at[1] + 2.0, 0.6);
      outs.forEach((o, i) => { pop(o, t, S.at[1] + 2.4 + i * 0.4); show(lout[i], t, S.at[1] + 2.4 + i * 0.4); travel(pout[i], lout[i], t, S.at[1] + 2.8 + i * 0.4, S.at[1] + 3.8 + i * 0.4); });
      // every kind of run is the same thing: batches into the function
      const d2 = S.to[2] - S.at[2], per = (d2 - 1.0) / src.length;
      src.forEach((s, i) => { const a = S.at[2] + 0.3 + i * per; pop(s, t, a); travel(fp[i], feeds[i], t, a + 0.3, a + per + 0.4); flash(s, t, a + 0.3, per * 0.8); });
      if (t > S.at[3]) src.forEach((s, i) => { const a = S.at[3] + 0.4 + i * 0.6, f = ((rt * 0.35 + i * 0.2) % 1); if (t > a) { placeOn(fp[i], feeds[i], f); op(fp[i], Math.max(0, Math.min(1, (0.8 - f) * 8, f * 8))); } });
      langs.forEach((g, i) => pop(g, t, S.at[3] + 0.4 + i * 0.4));
      nope.go(t, S.at[3] + 1.4, S.to[3] - 0.3, rt);
    };
  });
}
