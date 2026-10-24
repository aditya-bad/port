// 10. How each language runs, measured.
tableScene('How each language runs', 'Each language runs a little differently. The round trips and the memory here were measured: a Rust supervisor and a strategy, over a Unix socket.',
  [['language', 60, 14], ['SDK', 260, 22], ['in our cloud', 560, 40], ['round trip, 1 tick', 1010, 20], ['memory', 1280, 26]], [
    ['Python', 'linck · PyPI', 'CPython in gVisor, forked from a zygote', '56–62 µs (measured)', '~2 MB forked (measured)',
      'A Python strategy is CPython inside gVisor, forked from a zygote that already has the SDK loaded, so each costs about 2 megabytes. One tick there and back takes 56 to 62 microseconds.'],
    ['Go', 'linck.dev/sdk', 'a static binary; or WASM, for density', '44–46 µs (measured)', '4.1 MB (measured)',
      'Go is a static binary in the same kind of sandbox, or WASM for density: 44 to 46 microseconds, and about 4 megabytes.'],
    ['Rust', 'linck · crates.io', 'native; or a WASM component', '39–42 µs (measured)', '2.0 MB (measured)',
      'Rust is native, or a WASM component, and the fastest: 39 to 42 microseconds, and 2 megabytes.'],
    ['TypeScript', '@linck/sdk · npm', 'Node; or a WASM component', '57–58 µs (measured)', '42 MB (measured)',
      'TypeScript runs in Node: 57 to 58 microseconds, but 42 megabytes a process, which is why WASM comes later, for density.'],
    ['C++, others', 'WASM bindings, or remote', 'a WASM component, or your own process over gRPC', '—', '—',
      'And C++, or any other language, comes in as a WASM component, or as your own process over gRPC. Remote adds the network to every decision.'],
  ], { rh: 130 });

// 11. WASM and remote strategies.
{
  const wit = ['// wit/strategy.wit', 'package linck:strategy@1.0.0;', 'world strategy {', '  import ctx: interface {', '    now: func() -> s64;',
    '    chain: func(u: string, expiry: string) -> chain-view;', '    decide: func(rule: string, outcome: string, summary: string);', '  }',
    '  export on-start: func(config: list<u8>, state: option<list<u8>>);', '  export on-batch: func(batch: list<u8>) -> list<u8>;', '}'];
  const S = beats([
    ['Two more ways to run a strategy: as WASM, and remote.', { focus: 'all' }],
    ['A WASM strategy implements one interface, a WIT world: linck strategy, version one. It imports ctx functions, like now, chain and decide, and exports on-start and on-batch.', { spot: ['wit'] }],
    ['on-batch takes an event batch and returns commands, both as protobuf bytes: the same messages as every other sandbox. Bindings are generated for Rust, for Go through TinyGo, for TypeScript and for C++. It runs in Wasmtime, and starts in milliseconds.', { spot: ['wit'] }],
    ['A remote strategy runs on your own machine, and talks over one gRPC stream in both directions, carrying the same batches and commands. Its prices are conflated too, and each batch is acknowledged.', { spot: ['rem'] }],
    ['But the platform still owns risk, the orders and the clock. A remote strategy proposes; Heimdall decides. And the network is now part of every decision.', { spot: ['rem'] }],
  ]);
  scene({ title: 'WASM and remote strategies', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const cb = named('wit', codeBlock(B, 40, 116, 820, wit, 30, 'WASM: one WIT world', 'lab'));
    const bind = ['Rust', 'Go (TinyGo)', 'TypeScript', 'C++'].map((s, i) => box(B, 40 + i * 205, 540, 195, 60, s, { cls: 'chip', lcls: 'cl' }));
    const wt = box(B, 40, 630, 820, 80, 'Wasmtime', { sub: 'starts in milliseconds', cls: 'proc' });
    const rem = named('rem', panel(B, 900, 116, 660, 734, 'Remote'));
    const you = box(B, 940, 176, 580, 110, 'your machine', { sub: 'your process, any language', cls: 'out' });
    const plat = box(B, 940, 560, 580, 250, 'the platform', { sub: '', cls: 'key', top: true });
    const owns = typeList(plat, 980, 640, ['owns risk', 'owns the orders: Heimdall decides', 'owns the clock'], 40, 'cl', 26, 14);
    const down = link(L, plat, you, 'b', { sides: ['t', 'b'], d1: -120, d2: -120, label: 'batches (conflated)', lx: -14, ly: 4, anchor: 'end' });
    const upL = link(L, you, plat, 'g', { sides: ['b', 't'], d1: 120, d2: 120, label: 'commands (acknowledged)', lx: 14, ly: 4, anchor: 'start' });
    const grpc = T(B, 1230, 430, 'one gRPC stream, both ways', 'bs'); op(grpc, 0);
    const pb = packet(M, 'batch', 'b'), pc = packet(M, 'commands', 'g');
    const net = typer(B, 1230, 470, 'the network: part of every decision', 40, 'warnl', 22, 'middle');
    return (t, rt) => {
      pop(cb, t, 0.3); cb.type(t, 0.6, S.at[1] + 2.5, rt);
      cb.hl(t, 2, 7, S.at[1] + 0.3, S.to[1]); cb.hl(t, 8, 9, S.at[1] + (S.to[1] - S.at[1]) * 0.6, S.to[1]);
      cb.hl(t, 9, 9, S.at[2] + 0.2, S.to[2]);
      pops(bind, t, S.at[2] + 1.2, 0.5); pop(wt, t, S.at[2] + 3.4);
      pop(rem, t, S.at[3] - 0.2); pop(you, t, S.at[3] + 0.2); pop(plat, t, S.at[3] + 0.4);
      show(down, t, S.at[3] + 0.8); show(upL, t, S.at[3] + 1.2); fade(grpc, t, S.at[3] + 1.4);
      if (t > S.at[3] + 1.6) { const f = (rt * 0.4) % 1, g = (rt * 0.4 + 0.5) % 1; placeOn(pb, down, f); op(pb, Math.min(1, f * 8, (1 - f) * 8)); placeOn(pc, upL, g); op(pc, Math.min(1, g * 8, (1 - g) * 8)); }
      else { op(pb, 0); op(pc, 0); }
      owns.go(t, S.at[4] + 0.3, S.at[4] + (S.to[4] - S.at[4]) * 0.6, rt);
      net.go(t, S.at[4] + (S.to[4] - S.at[4]) * 0.65, S.to[4] - 0.2, rt);
    };
  });
}

// 12. Indicators.
{
  const S = beats([
    ['Indicators stay identical across languages, in two ways.', { focus: 'all' }],
    ['Subscribe to a computed stream, and the platform computes it once for the whole cell, and shares it. Ask for EMA 20 on NIFTY’s hourly bars, and you get the same node as everyone else who asked.', { spot: ['sub'] }],
    ['Or call linck-core, the shared Rust library, inside your strategy. Rust uses it directly, Python through PyO3, Node through napi-rs, and Go as a WASM module run by wazero, so Go needs no cgo.', { spot: ['core'] }],
    ['Either way, live, a backtest and a run on your laptop compute the same numbers. The first version has indicators inside each strategy; the subscribe form arrives with computed streams, and the old form keeps working.', { focus: 'all' }],
  ]);
  scene({ title: 'Indicators: subscribe, or call linck-core', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const sub = named('sub', panel(B, 40, 116, 740, 600, 'Subscribe'));
    const cs = box(B, 210, 176, 400, 110, 'computed streams', { sub: 'Rust · once per cell', cls: 'proc' });
    const node = box(B, 260, 330, 300, 70, 'EMA 20 · NIFTY 1h', { cls: 'key', lcls: 'cl' });
    const strs = [0, 1, 2, 3, 4].map(i => box(B, 70 + i * 140, 560, 124, 90, ['Py', 'Go', 'Rust', 'TS', 'Py'][i], { cls: 'chip', lcls: 'cl' }));
    const ls = strs.map(s => link(L, node, s, 'b', { sides: ['b', 't'] }));
    const lsN = link(L, cs, node, 'b', { sides: ['b', 't'] });
    const fl = ls.map(l => stream(M, l, 'b', 1, 1.4, 5));
    const core = named('core', panel(B, 820, 116, 740, 600, 'Call linck-core'));
    const lc = box(B, 990, 176, 400, 110, 'linck-core', { sub: 'one Rust library', cls: 'proc' });
    const bnd = [['Rust', 'directly'], ['Python', 'PyO3'], ['Node', 'napi-rs'], ['Go', 'WASM via wazero: no cgo']]
      .map(([a, b], i) => box(B, 850 + (i % 2) * 350, 380 + Math.floor(i / 2) * 150, 330, 120, a, { sub: b, cls: 'chip' }));
    const lb = bnd.map(b => link(L, lc, b, 'g', { sides: ['b', 't'] }));
    const same = typer(B, 800, 790, 'live · backtest · your laptop: the same numbers', 60, 'lane', 30, 'middle');
    return (t, rt) => {
      pops([sub, core], t, 0.3, 0.4);
      const d1 = S.to[1] - S.at[1];
      pop(cs, t, S.at[1] + 0.2); show(lsN, t, S.at[1] + 0.6); pop(node, t, S.at[1] + 0.9);
      strs.forEach((s, i) => { const a = S.at[1] + 1.4 + i * (d1 - 2.0) / strs.length; pop(s, t, a); show(ls[i], t, a + 0.1); fl[i](rt, t > a + 0.4 ? 0 : 1e9); });
      flash(node, t, S.to[1] - 1.2, 0.9);
      pop(lc, t, S.at[2] + 0.2);
      bnd.forEach((b, i) => { const a = S.at[2] + 1.0 + i * (S.to[2] - S.at[2] - 1.4) / 4; pop(b, t, a); show(lb[i], t, a); flash(b, t, a, 0.8); });
      same.go(t, S.at[3] + 0.3, S.at[3] + (S.to[3] - S.at[3]) * 0.5, rt);
    };
  });
}

// 13. The tests every SDK passes.
tableScene('The tests every SDK passes', 'Every SDK release, and every build of your strategy, runs the same conformance suite. No SDK ships without it.',
  [['test', 60, 20], ['what it proves', 380, 56], ['in the prototype', 1050, 50]], [
    ['Walk equivalence', 'restore from snapshot k and replay k+1 … n: orders, decisions and state hash identical to the straight run', '300 batches identical, Go and Python (measured)',
      'Walk equivalence: restore from a snapshot and replay from there, and the orders, decisions and state hashes must be identical to the straight run. In the prototype, 300 batches were identical, in Go and in Python.'],
    ['Crash and catch-up', 'kill mid-batch, restore, catch up: every state hash matches, orders identical, no duplicate placed', 'identical; 1 duplicate refused (measured)',
      'Crash and catch-up: kill it mid-batch, restore it, and catch it up. Every state hash must match, and no order may be placed twice. In the prototype, the one order made again was refused.'],
    ['Determinism', 'the same day run twice: the same hash of every decision and intent', 'covered by the walk test',
      'Determinism: the same day, run twice, gives the same hash of every decision and intent.'],
    ['Cross-SDK', 'reference strategies written in each SDK make the same decisions on the same journal', 'Go = Python on all 600 batches (measured)',
      'Cross-SDK: reference strategies written in each SDK make the same decisions on the same journal. In the prototype, Go matched Python on all 600 batches.'],
    ['Old strategies', 'a v0 adapter in the SDK core runs today’s StrategyBase strategies unchanged', 'how our nine strategies move first',
      'And old strategies keep running. An adapter in the SDK core runs today’s StrategyBase strategies unchanged, which is how our own nine strategies move first.'],
    ['Old protocols', 'the runtime keeps every protocol version it shipped for at least 12 months; SDKs follow semver', 'a rule, from day one',
      'The runtime keeps every protocol version it ever shipped for at least twelve months, and the SDKs follow semantic versioning, so an upgrade never breaks a running strategy.'],
  ]);
