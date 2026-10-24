// 7. What the SDK does for you.
{
  const layers = [['your strategy', 'params · State · subscribe · handlers', 'key'], ['SDK API', 'Strategy · params · State · decorators · ctx', ''],
    ['SDK core', 'event loop · state codec · determinism guards · v0 adapter', ''], ['runtime protocol v1', 'protobuf frames on a socket', 'proc'],
    ['the host', 'the cloud supervisor, or the simulator on your laptop', 'out']];
  const duties = [['owns main()', 'Hello → build your class → Ready → loop Batch → Done'], ['saves only declared state', 'serialised the same way every time'],
    ['keeps it deterministic', 'ctx.now, ctx.random; Python’s hash seed fixed'], ['names your orders', 'deployment:batch:n, the same on every replay'],
    ['reports a crash', 'Error{batch, error, stack}, then exits'], ['never sends null for []', 'a real bug the prototype found in Go']];
  const S = beats([
    ['Between your code and the runtime, the SDK is a few thin layers.', { focus: 'all' }],
    ['Your strategy sits on the SDK’s API: Strategy, params, State, the handler decorators and ctx. Under that, the SDK core: the event loop, the state codec, the determinism guards, and an adapter for version zero. Under that, protocol version one, and then the host: the cloud supervisor, or the simulator on your laptop.', { spot: ['lay'] }],
    ['Every SDK, in every language, has the same duties. It owns main: it reads Hello, builds your class, says Ready, and runs the loop.', { spot: ['d0'], pad: 3 }],
    ['It saves and restores only the state you declare, serialised the same way every time.', { spot: ['d1'], pad: 3 }],
    ['It keeps decisions deterministic, and it names every order the same way on every replay: the deployment, the batch, and a number. You never name an order.', { spot: ['d2', 'd3'], pad: 3 }],
    ['It reports a crash with where and why: the batch, the error and the stack. And it never sends null for an empty list. That one is a real bug the prototype found in Go.', { spot: ['d4', 'd5'], pad: 3 }],
  ]);
  scene({ title: 'What the SDK does for you', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    named('lay', bounds(G(B), 40, 120, 700, 720));
    const ls = layers.map(([a, b, c], i) => { const g = box(B, 40, 120 + i * 146, 700, 128, a, { cls: c, top: true }); g.tp = typer(g, 390, 120 + i * 146 + 84, b, 60, 'txt', 22, 'middle'); return g; });
    const ds = duties.map(([a, b], i) => { const y = 120 + i * 122, g = named('d' + i, box(B, 800, y, 760, 108, '', { cls: 'chip' }));
      T(g, 824, y + 40, a, 'cl', 'start'); g.tp = typer(g, 824, y + 76, b, 70, 'txt', 22); g.ok = check(M, 1520, y + 54, true); return g; });
    const pk = packet(M, 'batch', 'b');
    const up = route(M, [[700, 120 + 4 * 146 + 64], [700, 150]]);
    return (t, rt) => {
      const per = (S.to[1] - S.at[1] - 0.6) / ls.length;
      ls.forEach((g, i) => { pop(g, t, 0.3 + i * 0.25); g.tp.go(t, S.at[1] + 0.3 + i * per, S.at[1] + 0.3 + (i + 0.9) * per, rt); flash(g, t, S.at[1] + 0.3 + i * per, per * 0.9); });
      // a batch climbs the layers to your code, over and over
      if (t > S.at[1]) { const f = (rt * 0.3) % 1; placeOn(pk, up, f); op(pk, Math.min(1, f * 8, (1 - f) * 8)); } else op(pk, 0);
      const map = [2, 3, 4, 4, 5, 5];
      ds.forEach((g, i) => {
        const st = map[i], two = map.filter(x => x === st).length, j = map.indexOf(st);
        const a = S.at[st] + 0.3 + (i - j) * (S.to[st] - S.at[st] - 0.6) / two, b = a + (S.to[st] - S.at[st] - 0.6) / two;
        pop(g, t, a - 0.1); g.tp.go(t, a + 0.2, b - 0.2, rt); pop(g.ok, t, b - 0.1);
      });
    };
  });
}

// 8. Determinism.
{
  const S = beats([
    ['Determinism is what makes walks, repairs and certification exact: the same inputs give the same decisions, every time.', { focus: 'all' }],
    ['The only clock is ctx.now, the batch’s time. The only randomness is ctx.random, seeded for each deployment and day.', { spot: ['r0'], pad: 3 }],
    ['No network, no files and no threads inside a handler. And the runtime image is pinned, for each strategy version.', { spot: ['r1'], pad: 3 }],
    ['Each language has a trap, and the SDK closes it. Python’s hash seed is fixed. Go’s lint flags map ranging and goroutines. TypeScript’s Date and Math.random are replaced.', { spot: ['r2'], pad: 3 }],
    ['Floating point is deterministic for the same operations, in the same order, on the same CPU: in the prototype, Go and Python EMAs match bit for bit. Two traps are closed by policy. Replay machines use the same CPU architecture as live, because Go may fuse a multiply and an add on arm64. And the strategy version is pinned in the journal’s header.', { spot: ['r3'], pad: 3 }],
    ['Certification proves it: a day run twice, and the hash of every decision and intent compared. One difference fails the build.', { spot: ['runs'] }],
  ]);
  scene({ title: 'Determinism', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const rules = [['one clock, one randomness', 'ctx.now: the batch’s time · ctx.random: seeded per deployment and day'],
      ['nothing from outside', 'no network, files or threads in a handler · a pinned runtime image'],
      ['each language’s trap', 'Python: hash seed fixed · Go: no map ranging, no goroutines · TS: no Date, no Math.random'],
      ['floating point', 'same ops, same order, same CPU: bit for bit (prototype EMAs) · one CPU architecture · version pinned in the journal']];
    const rs = rules.map(([a, b], i) => { const y = 120 + i * 130, g = named('r' + i, box(B, 40, y, 880, 116, '', { cls: 'chip' }));
      T(g, 64, y + 40, a, 'cl', 'start'); g.tp = typer(g, 64, y + 74, b, 92, 'txt', 22); return g; });
    const runs = named('runs', panel(B, 960, 120, 600, 720, 'One day, twice (illustrative)'));
    T(runs, 1060, 196, 'run 1', 'fl'); T(runs, 1360, 196, 'run 2', 'fl');
    const hs = ['a41f', '9c07', '17be', 'e2d0', '5b93', 'c48a', '0f6e', '7da1', '3e25', 'b960'];
    const rows = hs.map((h, i) => [T(runs, 1060, 240 + i * 52, '', 'mono'), T(runs, 1360, 240 + i * 52, '', 'mono'), check(M, 1210, 234 + i * 52, true)]);
    const pass = stamp(M, 1260, 790, 'Same', 'g', { rot: -6 });
    return (t, rt) => {
      rs.forEach((g, i) => { pop(g, t, S.at[i + 1] + 0.2); g.tp.go(t, S.at[i + 1] + 0.5, S.to[i + 1] - 0.3, rt); });
      pop(runs, t, 0.4);
      const d = S.to[5] - S.at[5];
      rows.forEach(([a, b, c], i) => {
        const s = S.at[5] + 0.3 + i * (d - 1.6) / hs.length;
        typeAt(a, t, s, s + 0.25, `decision ${hs[i]}`, rt); typeAt(b, t, s + 0.1, s + 0.35, `decision ${hs[i]}`, rt); pop(c, t, s + 0.4);
      });
      pass.go(t, S.to[5] - 1.0);
      // in the opening, the two runs tick over in step: a hint of what's coming
      if (t < S.at[1]) rows.slice(0, 3).forEach(([a, b], i) => { const s = `decision ${hs[(Math.floor(rt * 2) + i) % hs.length]}`; txt(a, s); txt(b, s); op(a, 0.4); op(b, 0.4); });
      else if (t < S.at[5]) rows.forEach(([a, b]) => { op(a, 0); op(b, 0); });
    };
  });
}

// 9. From your laptop to live money.
{
  const path = [
    ['linck init', 'scaffolds a strategy, with tests', 'laptop'],
    ['linck run --day 2026-10-14', 'replays that day on your laptop: the same SDK core, the same sim venue', 'seconds'],
    ['linck backtest --from 2024-01 --sweep stop=0.2:0.4:0.05', 'on the farm: point-in-time chains, walk-forward', 'minutes'],
    ['linck push', 'source and lockfile, never a binary; a hermetic build in the pinned image', 'minutes'],
    ['certify', 'fault matrix · determinism · budgets; signed (Sigstore)', 'minutes'],
    ['linck deploy --paper', 'pins that exact version; paper fills from the sim venue', 'days'],
    ['shadow', 'priced against the live book, beside what Kite would really have done', 'weeks'],
    ['live', 'your broker; from 1 lot with a loss limit, raised when you say', 'ongoing'],
    ['watched', 'the day check, issues, drift from its backtest, repair', 'every day'],
  ];
  const S = beats([
    ['From your laptop to live money, in nine steps.', { focus: 'all' }],
    ['linck init scaffolds a strategy, with tests. linck run replays a real day on your laptop, through the same SDK core and the same sim venue library as the cloud, in seconds. The CLI carries the supervisor, the sim venue and a reader for the lake, so a local run equals a cloud run.', { spot: ['p0', 'p1'], pad: 3 }],
    ['linck backtest runs on the farm: a sweep of a parameter, with option chains as they were on each day, and walk-forward tests to catch overfitting. Minutes.', { spot: ['p2'], pad: 3 }],
    ['linck push sends your source and lockfile, never a binary, to the builder, which builds it in the pinned image for your language: uv, go mod, cargo or npm. Then it is certified: the fault matrix, the determinism run and the budgets, and signed.', { spot: ['p3', 'p4'], pad: 3 }],
    ['linck deploy, with paper, pins that exact version. Moving to shadow and then live is a change of state, not a rebuild. Paper takes days. Shadow, priced against the live book beside what Kite would really have done, takes weeks.', { spot: ['p5', 'p6'], pad: 3 }],
    ['Live goes through your own broker, starting at one lot with a loss limit, raised when you say. And every day it is watched: the day check, issues, drift from its backtest, and repair when it is needed.', { spot: ['p7', 'p8'], pad: 3 }],
  ]);
  scene({ title: 'From your laptop to live money', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const rows = path.map(([a, b, d], i) => {
      const y = 116 + i * 82, g = named('p' + i, box(B, 40, y, 1520, 72, '', { cls: 'chip' }));
      E('circle', { cx: 78, cy: y + 36, r: 18, class: i < 5 ? 'f-ink' : i < 7 ? 'f-b' : 'f-g' }, g); T(g, 78, y + 42, String(i + 1), 'pt');
      T(g, 112, y + 30, a, 'lab', 'start');
      g.tp = typer(g, 112, y + 56, b, 110, 'txt', 20);
      const tag = box(g, 1380, y + 14, 160, 44, d, { cls: 'key', lcls: 'lab' }); op(tag, 1);
      return g;
    });
    const groups = [[0, 1], [2], [3, 4], [5, 6], [7, 8]];
    const sp = sparkle(M, 1300, 116 + 7 * 82 + 36, { r: 50 });
    return (t, rt) => {
      groups.forEach((gs, s) => gs.forEach((i, j) => {
        const a = S.at[s + 1] + 0.2 + j * (S.to[s + 1] - S.at[s + 1]) / gs.length, b = S.at[s + 1] + (j + 1) * (S.to[s + 1] - S.at[s + 1]) / gs.length - 0.2;
        if (t < S.at[1]) { pop(rows[i], t, 0.3 + i * 0.12); if (t > 0.8 + i * 0.12) op(rows[i], 0.3); }
        else if (t < a) op(rows[i], 0.3);
        else { op(rows[i], 1); flash(rows[i], t, a, 0.7); }
        rows[i].tp.go(t, a + 0.3, b, rt);
      }));
      sp.go(t, S.at[5] + 1.0);
    };
  });
}
