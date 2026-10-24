// 14. How many strategies fit on a machine: what the architecture video said, and how to read it.
{
  const S = beats([
    ['Finally, one number from the architecture video. It said a runtime machine could hold an estimated three to five thousand strategies. Here is how to read that.', { focus: 'all' }],
    ['That estimate came from an assumption: about one millisecond of CPU for each strategy event. Measured, today’s strategies take 7 to 47 microseconds to decide on a tick, and the runner around them about a tenth of a millisecond. A round trip to a sandboxed strategy is 39 to 62 microseconds. So CPU per decision is not what fills a machine.', { spot: ['pa'] }],
    ['What fills it is memory, and the burst at the open. Memory is the number of strategies times what each one costs: 2 to 4 megabytes for forked Python, Go or Rust, but 42 for Node, plus the caches.', { spot: ['pb'] }],
    ['CPU is the batches a second at the open, times about 60 microseconds of round trip, plus each strategy’s own time. And a machine is only filled to 60 percent: 40 is kept for the open, for a machine that fails, and for moves.', { spot: ['pb'] }],
    ['Worked out, as estimates: 2,000 light strategies make about 20,000 batches a second at the open, about one and a half cores, and need 6 to 10 gigabytes: two runtime machines of 16 virtual CPUs and 64 gigabytes. 20,000 strategies need 4 to 6 machines, in two cells.', { spot: ['pc'] }],
    ['So read three to five thousand as a placeholder, not a measurement. The real number depends on the language mix, and on how heavy each strategy is: a heavy one is placed by its measured CPU. And the burst at the open is assumed to be ten times normal, until it is logged.', { spot: ['pd'] }],
    ['It gets measured at step I2, by replaying recorded days at one, ten and fifty times speed, before any cell is sized. What you control, as a developer, is your strategy’s memory and its time per batch, and your manifest declares both.', { spot: ['pd'] }],
  ]);
  scene({ title: 'How many strategies fit on a machine', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const qt = box(B, 40, 116, 1520, 92, '', { cls: 'key' });
    const qtT = typer(qt, 800, 172, 'The architecture video: “an estimated three to five thousand on one machine”', 100, 'lane', 30, 'middle');
    // where it came from
    const pa = named('pa', panel(B, 40, 228, 740, 260, 'Where the number came from'));
    const as = typer(pa, 70, 300, 'assumed: ~1 ms of CPU for each strategy event', 60, 'cl');
    const strike = E('line', { x1: 64, y1: 294, x2: 520, y2: 294, class: 'zig' }, pa); op(strike, 0);
    const ms = typeList(pa, 70, 352, ['a decision on a tick: 7–47 µs (measured)', 'the runner around it: ~0.1 ms (measured)', 'a round trip to a sandbox: 39–62 µs (measured)'], 60, 'okl', 26, 14);
    // what fills a machine
    const pb = named('pb', panel(B, 820, 228, 740, 260, 'What fills a machine'));
    const f1 = typer(pb, 850, 300, 'memory = strategies × (2–4 MB; Node: 42 MB) + caches', 64, 'lab');
    const f2 = typeList(pb, 850, 346, ['CPU = batches/s at the open × (~60 µs + its own time)', 'machines = max(CPU, memory) ÷ 60%', '40% kept: the open, a failed machine, moves'], 64, 'lab', 26, 14);
    // worked out
    const pc = named('pc', panel(B, 40, 508, 740, 344, 'Worked out (estimates)'));
    const rows = [['2,000 strategies', '~20k batches/s at the open ≈ 1.5 cores · 6–10 GB', 2, 0], ['20,000 strategies', '~200k batches/s ≈ 15 cores · 60–100 GB', 4, 2]].map(([a, b, n, extra], i) => {
      const y = 576 + i * 136;
      const g = G(pc); T(g, 70, y, a, 'cl', 'start'); const tp = typer(g, 70, y + 30, b, 60, 'txt', 22);
      const icons = [...Array(n + extra)].map((_, j) => { const r = E('rect', { x: 70 + j * 62, y: y + 52, width: 50, height: 40, rx: 5, class: j < n ? 'f-b' : 'f-b', opacity: j < n ? 0.85 : 0.3 }, g); op(r, 0); return r; });
      const lab = T(g, 70 + (n + extra) * 62 + 10, y + 80, i ? '4–6 machines · 2 cells' : '2 machines · 16 vCPU, 64 GB', 'bs', 'start'); op(lab, 0);
      return { g, tp, icons, lab, n, extra };
    });
    // how to read it
    const pd = named('pd', panel(B, 820, 508, 740, 344, 'How to read “3–5k”'));
    const r1 = typeList(pd, 850, 572, ['a placeholder, not a measurement', 'it depends on the language mix and how heavy each strategy is', 'the open burst: assumed 10× until logged'], 62, 'txt', 23, 12);
    const r2 = typeList(pd, 850, 724, ['measured at step I2: recorded days at 1×, 10×, 50×', 'yours to control: memory and time per batch, in linck.toml'], 62, 'cl', 23, 12);
    const tag = stamp(M, 1380, 548, 'Placeholder', 'wt', { rot: -5, shake: 3 });
    return (t, rt) => {
      pop(qt, t, 0.2); qtT.go(t, 0.5, S.to[0] - 0.4, rt);
      pops([pa, pb, pc, pd], t, S.to[0] - 1.2, 0.2);
      const d1 = S.to[1] - S.at[1];
      as.go(t, S.at[1] + 0.3, S.at[1] + d1 * 0.25, rt); op(strike, k(t, S.at[1] + d1 * 0.28, S.at[1] + d1 * 0.32));
      ms.go(t, S.at[1] + d1 * 0.35, S.to[1] - d1 * 0.12, rt);
      f1.go(t, S.at[2] + 0.4, S.to[2] - 0.4, rt);
      f2.go(t, S.at[3] + 0.3, S.to[3] - 0.3, rt);
      const d4 = S.to[4] - S.at[4];
      rows.forEach(({ tp, icons, lab, n, extra }, i) => {
        const a = S.at[4] + 0.3 + i * d4 * 0.5, b = a + d4 * 0.45;
        tp.go(t, a, a + d4 * 0.25, rt);
        icons.forEach((r, j) => op(r, k(t, a + d4 * 0.27 + j * 0.15, a + d4 * 0.27 + j * 0.15 + 0.2) * (j < n ? 1 : 0.35)));
        op(lab, k(t, b - 0.4, b));
      });
      r1.go(t, S.at[5] + 0.4, S.to[5] - 0.3, rt);
      if (t < S.at[6]) tag.go(t, S.at[5] + 1.0); else op(tag, 1);
      r2.go(t, S.at[6] + 0.3, S.to[6] - 0.3, rt);
    };
  });
}

// 15. Recap.
{
  const lines = [
    ['A strategy is a function behind a protocol.', 'The runtime owns time, state and inputs; ctx is the only door to the world.'],
    ['The SDK owns main, your state, and your orders’ names.', 'Determinism by rule: one clock, one randomness, a pinned image, and tests.'],
    ['Laptop, backtest, push, certify, paper, shadow, live.', 'How many fit on a machine: set by memory and the open, measured before cells are sized.'],
  ];
  const S = beats([
    ['To recap. A strategy is a function behind a protocol: the runtime owns time, state and the inputs, and ctx is the only door to the world.', { focus: 'all' }],
    ['The SDK owns main, saves your state and names your orders. Determinism is kept by rule: one clock, one source of randomness, a pinned image, and tests that prove it.', {}],
    ['A strategy goes from your laptop to a backtest, to push and certification, then paper, shadow and live. And how many fit on a machine is set by memory and the open, and measured before any cell is sized.', {}],
  ]);
  scene({ title: 'The SDK, in short', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const rows = lines.map(([a, b], i) => {
      const g = box(B, 120, 150 + i * 220, 1360, 190, '', { cls: 'chip' });
      return { g, a: typer(g, 160, 150 + i * 220 + 72, a, 90, 'lane', 30), b: typer(g, 160, 150 + i * 220 + 130, b, 110, 'txt', 24) };
    });
    const sp = sparkle(M, 1420, 600, { r: 60 });
    return (t, rt) => {
      rows.forEach((r, i) => { pop(r.g, t, S.at[i] + 0.2); r.a.go(t, S.at[i] + 0.4, S.at[i] + (S.to[i] - S.at[i]) * 0.4, rt); r.b.go(t, S.at[i] + (S.to[i] - S.at[i]) * 0.45, S.to[i] - 0.3, rt); });
      sp.go(t, S.to[2] - 1.0);
    };
  });
}
