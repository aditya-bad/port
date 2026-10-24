// 10. Control, builder, refdata: Trade's region services.
{
  const S = beats([
    ['Now LinckTrade, starting with three of the services that run once per region.', { focus: 'all' }],
    ['Control, in Go, is the system of record for strategies and deployments. Its registry holds every strategy and version. Each deployment moves from paper to shadow to live, and can be started, stopped or paused.', { focus: ['control'], spot: ['ctlTop'] }],
    ['It also holds each account’s risk limits, and its broker links: which account trades at which broker. Three copies run over its own Postgres database.', { spot: ['ctlTop'] }],
    ['Its scheduler is leader-elected: the three copies agree on one leader, and only the leader places strategies. If the leader dies, another copy takes over.', { spot: ['copies'] }],
    ['It packs deployments onto runtime machines by their expected CPU, from their history, and prefers a machine that already follows the same instruments. Then it sends the start command to the cell over NATS.', { spot: ['bins'] }],
    ['The builder, in Go, turns a user’s source code into a signed strategy artifact. Each build runs as its own Kubernetes Job, inside gVisor, because the code isn’t ours.', { focus: ['builder'], spot: ['builder'] }],
    ['It builds in a pinned image per language, then certifies: the fault matrix of crash and failure cases, a determinism double-run of the same day, and the resource budget. A build that passes is signed with Sigstore and stored in the artifact registry.', { spot: ['builder'] }],
    ['Refdata, in Go, is the instrument master: our id for every instrument, its lot size, expiry, strike and freeze quantity, and its symbol at every broker. Each morning it loads the exchange files and each broker’s list before 08:30. Everyone reads it; only refdata writes it.', { focus: ['refdata'], spot: ['refdata'] }],
  ]);
  scene({ title: 'Control, builder, refdata', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const ctl = named('control', svc(B, 40, 116, 748, 744, 'control', { lang: 'Go', kind: 'Deployment · 3', part: 'trade',
      does: 'The system of record for strategies and deployments. Owns the control database.',
      comps: ['strategy registry', 'deployments', 'lifecycle: paper → shadow → live', 'start · stop · pause', 'risk limits', 'broker links', 'scheduler (leader only)', 'command sender (NATS)'], cols: 2 }));
    named('ctlTop', bounds(G(B), 40, 116, 748, ctl.top - 116 + 6));
    const cps = [0, 1, 2].map(i => box(ctl, 70 + i * 236, 400, 220, 54, `copy ${i + 1}`, { cls: 'chip' }));
    cps.forEach(b => op(b, 1));
    named('copies', bounds(G(B), 70, 392, 692, 70));
    const crown = G(M, 'crown'); E('path', { d: 'M-16 8L-16 -6L-8 2L0 -10L8 2L16 -6L16 8Z', class: 'f-y', stroke: 'currentColor' }, crown); op(crown, 0);
    const lead = T(M, 0, 0, 'leader', 'lab'); op(lead, 0);
    const boom_ = puff(M, 180, 427);
    const bins = [0, 1, 2].map(i => box(ctl, 80 + i * 236, 520, 200, 300, `machine ${'ABC'[i]}`, { cls: 'frame', top: true }));
    bins.forEach(b => op(b, 1));
    named('bins', bounds(G(B), 70, 480, 692, 350));
    T(ctl, 414, 506, 'placing deployments on runtime machines', 'bs');
    const blk = [[0, 60, 'b'], [1, 40, 'g'], [0, 30, 'b'], [2, 70, 'pu'], [1, 50, 'g'], [2, 40, 'pu'], [0, 50, 'b'], [1, 30, 'g'], [2, 30, 'pu']];
    const fill = [0, 0, 0];
    const blocks = blk.map(([h, size, c]) => { const y = 810 - fill[h] - size; fill[h] += size + 4; const r = E('rect', { x: 100 + h * 236, y, width: 160, height: size, rx: 4, class: 'f-' + c }, M); op(r, 0); return { r, y }; });
    const leg = G(ctl); [['b', 'NIFTY'], ['g', 'BANKNIFTY'], ['pu', 'FINNIFTY']].forEach(([c, n], i) => { E('rect', { x: 92 + i * 200, y: 836, width: 14, height: 14, class: 'f-' + c }, leg); T(leg, 112 + i * 200, 849, n, 'bs', 'start'); });
    const bd = named('builder', svc(B, 812, 116, 748, 360, 'builder', { lang: 'Go', kind: 'Deployment · 2 + a Job per build', part: 'trade',
      does: 'Turns a user’s source into a signed, certified strategy artifact.', comps: ['pinned build image', 'certification', 'sign (Sigstore)', 'artifact registry'], cols: 4 }));
    const cert = ['fault matrix', 'determinism double-run', 'resource budget'].map((n, i) => box(bd, 830 + i * 240, 300, 228, 40, n, { cls: 'chip', lcls: 'cmp' }));
    cert.forEach(b => op(b, 1));
    const job = box(bd, 830, 370, 712, 80, 'each build: a Kubernetes Job inside gVisor', { cls: 'frame', lcls: 'cl' }); op(job, 1);
    const src = dot(M, 'b', 9);
    const srcPath = route(M, [[860, 236], ...['pinned build image', 'certification', 'sign (Sigstore)', 'artifact registry'].map(n => mid(bd.comp[n]))]);
    const rf = named('refdata', svc(B, 812, 500, 748, 360, 'refdata', { lang: 'Go', kind: 'Deployment · 3', part: 'trade',
      does: 'The instrument master. Everyone reads it; only refdata writes it.', comps: ['instrument master', 'broker symbol maps', 'morning load, before 08:30', 'new expiries'], cols: 2 }));
    const cols = ['our id', 'lot size', 'expiry', 'strike', 'freeze qty', 'symbol at each broker'], cw = [86, 86, 86, 86, 104, 224];
    const cx0 = cw.map((w, i) => 830 + cw.slice(0, i).reduce((a, b) => a + b + 4, 0));
    const hdr = cols.map((c, i) => box(rf, cx0[i], 700, cw[i], 40, c, { cls: 'chip', lcls: 'bs' }));
    hdr.forEach(b => op(b, 1));
    const rows = [0, 1, 2].map(r => { const g = G(rf); cols.forEach((c, i) => E('rect', { x: cx0[i], y: 746 + r * 30, width: cw[i], height: 24, class: 'mini' }, g)); return g; });
    const files = ['exchange files', 'broker lists'].map((n, i) => packet(M, n, 'b'));
    const fpath = [route(M, [[1470, 678], [1250, 678]]), route(M, [[1470, 678], [1250, 678]])];
    return (t, rt) => {
      pops([ctl, bd, rf], t, 0.2, 0.6);
      walk(['strategy registry', 'deployments', 'lifecycle: paper → shadow → live', 'start · stop · pause'].map(n => ctl.comp[n]), t, S.at[1] + 0.4, S.to[1]);
      walk(['risk limits', 'broker links'].map(n => ctl.comp[n]), t, S.at[2] + 0.4, S.to[2]);
      // the leader: copy 2, then copy 2 dies and copy 3 takes over
      const dead = t > S.at[3] + (S.to[3] - S.at[3]) * 0.55;
      const who = dead ? cps[2] : cps[1];
      tr(crown, who.cx, who.Y - 4); op(crown, t > S.at[3] + 0.3 ? 1 : 0); bob(crown, rt, 2);
      op(cps[1], dead ? 0.3 : 1); boom_.go(t, S.at[3] + (S.to[3] - S.at[3]) * 0.55);
      flash(ctl.comp['scheduler (leader only)'], t, S.at[3] + 0.3, 1.2);
      const per = (S.to[4] - S.at[4] - 1) / blocks.length;
      blocks.forEach(({ r, y }, i) => {
        const a = S.at[4] + 0.3 + i * per, f = k(t, a, a + per * 0.9);
        op(r, f > 0 ? 0.85 : 0); r.setAttribute('y', (lerp(470, y, bounceOut(f))).toFixed(1));
      });
      op(leg, k(t, S.at[4], S.at[4] + 0.5));
      flash(ctl.comp['command sender (NATS)'], t, S.to[4] - 1.0, 0.9);
      travel(src, srcPath, t, S.at[5] + 0.5, S.to[6] - 0.5, true);
      if (t > S.to[6] - 0.5) op(src, 0);
      walk(cert, t, S.at[6] + 1.0, S.to[6] - 1.4);
      files.forEach((f, i) => travel(f, fpath[i], t, S.at[7] + 1.0 + i * 1.2, S.at[7] + 3.0 + i * 1.2));
      rows.forEach((r, i) => op(r, k(t, S.at[7] + 2.0 + i * 0.8, S.at[7] + 2.6 + i * 0.8)));
    };
  });
}

// 11. Market data in: feed handlers and the sequencer.
{
  const S = beats([
    ['Inside a cell, prices come in through market data ingest, written in Rust. There is one ingest service per source: the licensed vendor feed, and a user’s broker socket where their live trading needs it.', { focus: 'all' }],
    ['Each runs as a StatefulSet A and B pair: two copies on the same feed, so if one dies, the other already has the data.', { spot: ['ingest'] }],
    ['Inside, the socket client receives the source’s own format. The decoder turns it into our record without allocating memory. The mapper swaps the source’s symbol for our instrument id. And the tick filter drops bad prints: zero or negative prices, out-of-order and duplicate updates, and spikes, held until confirmed.', { focus: ['ingest'], spot: ['ingest'] }],
    ['Our record is 96 bytes, the same in live trading, backtests and replays. Prices are whole numbers of ten-thousandths of a rupee, so there is no float rounding. Exchange time is the truth; receive time measures our own lag.', { focus: ['record'], spot: ['record'] }],
    ['The sequencer runs as one active copy with a standby. It gives each instrument’s updates a gap-free sequence number, so every reader can tell an update it skipped from one that went missing.', { focus: 'all', spot: ['seq'] }],
    ['When two sources carry the same instrument, it takes whichever arrives first and records the other’s lag, so failing over costs nothing.', { spot: ['seq', 'ingest'] }],
    ['Then each update goes two ways: to the archive, the Redpanda topic md.archive.v1 and then the lake, which keeps every tick; and out over gRPC streams, to computed streams and to every runtime machine in the cell.', { spot: ['seq'] }],
  ]);
  scene({ title: 'Market data in', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const ven = box(B, 40, 180, 190, 80, 'vendor feed', { sub: 'TCP / WebSocket', cls: 'out' });
    const kite = box(B, 40, 300, 190, 80, 'broker socket', { sub: 'Kite: binary WS', cls: 'out' });
    const ing = named('ingest', svc(B, 270, 116, 580, 420, 'md-ingest-<source>', { lang: 'Rust', kind: 'StatefulSet · A/B', part: 'trade',
      does: 'One per source. Turns the source’s format into our record.', comps: ['socket client', 'decoder', 'instrument mapper', 'tick filter'], cols: 1, ch: 40 }));
    const ab = ['copy A', 'copy B'].map((n, i) => box(ing, 300 + i * 270, 430, 250, 80, n, { sub: 'same feed', cls: 'chip' }));
    ab.forEach(b => op(b, 1));
    const seq = named('seq', svc(B, 890, 116, 670, 420, 'md-sequencer', { lang: 'Rust', kind: 'active + standby', part: 'trade',
      does: 'One order per instrument, so exactly one copy is active.', comps: ['first arrival wins', 'sequence numbers', 'archive writer', 'gRPC stream server'], cols: 2, ch: 40 }));
    const arch = topic(seq, 910, 340, 630, 'md.archive.v1 → the lake', 'b', { h: 40, lcls: 'tlab' }); op(arch, 1);
    const grpc = box(seq, 910, 400, 630, 110, 'gRPC server streams', { sub: 'to computed streams and every runtime machine', cls: 'out' }); op(grpc, 1);
    const l1 = link(L, ven, ing, 'b', { d: 'M230 220C252 220 252 236 268 236' }), l2 = link(L, kite, ing, 'b', { d: 'M230 340C252 340 252 300 268 300' });
    const l3 = link(L, ing, seq, 'b', { d: 'M850 330H886' });
    const lf = [stream(M, l1, 'b', 2, 1.2, 5), stream(M, l2, 'b', 2, 1.5, 5), stream(M, l3, 'b', 2, 1.0, 5)];
    // the record
    const rec = named('record', bounds(G(B), 40, 560, 1520, 300));
    const fields = [[0, 4, 'instrument_id', 'u32'], [4, 2, 'flags', 'u16'], [6, 2, 'venue', 'u16'], [8, 8, 'seq', 'u64'], [16, 16, 'exch_ts · recv_ts', 'i64 ×2'],
      [32, 32, 'ltp · bid · ask · avg', 'i64 ×4 · ₹ × 10⁻⁴'], [64, 16, 'bid · ask · last qty', 'u32 ×4'], [80, 16, 'volume · oi', 'u64 ×2']];
    const X = b => 60 + b * (1480 / 96);
    const recG = G(B); T(recG, 60, 596, 'one tick = 96 bytes (offsets in bytes)', 'cl', 'start');
    const segs = fields.map(([o, n, name, ty], i) => {
      const g = G(recG);
      E('rect', { x: X(o), y: 640, width: X(o + n) - X(o), height: 56, class: 'mini', style: i % 2 ? '' : 'fill: color-mix(in oklch, var(--b) 16%, var(--card))' }, g);
      T(g, X(o) + 3, 630, String(o), 'bt', 'start');
      const wide = (X(o + n) - X(o)) > name.length * 9.6 + 10;
      if (wide) { T(g, (X(o) + X(o + n)) / 2, 664, name, 'cmp'); T(g, (X(o) + X(o + n)) / 2, 686, ty, 'bs'); }
      else { const ly = 728 + (i % 3) * 44; E('path', { d: `M${(X(o) + X(o + n)) / 2} 698V${ly - 16}`, class: 'life' }, g); T(g, (X(o) + X(o + n)) / 2, ly, `${name} · ${ty}`, 'bs', i === 0 ? 'start' : 'middle'); }
      op(g, 0); return g;
    });
    T(recG, X(96) - 3, 630, '96', 'bt', 'end');
    op(recG, 0);
    const tick = dot(M, 'b', 8);
    const tickPath = route(M, [[230, 220], [268, 236], mid(ing.comp['socket client']), mid(ing.comp['decoder']), mid(ing.comp['instrument mapper']), mid(ing.comp['tick filter']), [886, 330],
      mid(seq.comp['first arrival wins']), mid(seq.comp['sequence numbers'])]);
    const out1 = route(M, [mid(seq.comp['sequence numbers']), mid(seq.comp['archive writer']), [1200, 360]]);
    const out2 = route(M, [mid(seq.comp['sequence numbers']), mid(seq.comp['gRPC stream server']), [1225, 455]]);
    const s1 = stream(M, out1, 'b', 2, 1.6, 6), s2 = stream(M, out2, 'b', 2, 1.6, 6);
    const race = [packet(M, 'A', 'b'), packet(M, 'B', 'b')];
    const fw = seq.comp['first arrival wins'];
    const raceP = [route(M, [[ab[0].cx, ab[0].Y], [700, 320], [886, 330], [fw.X + 30, fw.B + 18]]), route(M, [[ab[1].cx, ab[1].Y], [760, 340], [886, 330], [fw.X + 30, fw.B + 18]])];
    const lagT = T(M, 1060, 312, '', 'lab', 'start');
    const bad = packet(M, '₹0.00', 'r');
    const badP = route(M, [[330, 300], mid(ing.comp['decoder']), mid(ing.comp['tick filter'])]);
    return (t, rt) => {
      pops([ven, kite, ing, seq], t, 0.2, 0.5);
      [l1, l2, l3].forEach((l, i) => show(l, t, 1.6 + i * 0.4));
      lf.forEach((f, i) => f(rt, t > 2.4 + i * 0.4 ? 0 : 1e9));
      flash(ab[0], t, S.at[1] + 0.4, 0.8); flash(ab[1], t, S.at[1] + 1.4, 0.8);
      // a good tick walks through the ingest, over and over
      if (t > S.at[2] + 0.3 && t < S.at[3]) { op(tick, 1); placeOn(tick, tickPath, ((rt * 0.18) % 1) * 0.8); } else op(tick, 0);
      walk(['socket client', 'decoder', 'instrument mapper', 'tick filter'].map(n => ing.comp[n]), t, S.at[2] + 0.3, S.to[2] - 1.2);
      travel(bad, badP, t, S.to[2] - 1.6, S.to[2] - 0.3);
      fade(recG, t, S.at[3] + 0.2);
      segs.forEach((g, i) => pop(g, t, S.at[3] + 0.4 + i * (S.to[3] - S.at[3] - 1) / segs.length));
      walk(['first arrival wins', 'sequence numbers'].map(n => seq.comp[n]), t, S.at[4] + 0.3, S.to[4]);
      race.forEach((r, i) => travel(r, raceP[i], t, S.at[5] + 0.4 + i * 0.5, S.at[5] + 2.0 + i * 0.9));
      typeOn(lagT, t, S.at[5] + 2.6, S.to[5] - 0.2, 'A first; B’s lag recorded');
      s1(rt, t > S.at[6] + 0.4 ? 0 : 1e9); s2(rt, t > S.at[6] + 1.2 ? 0 : 1e9);
    };
  });
}

// 12. The latest price, never a backlog.
{
  const S = beats([
    ['Ticks follow one promise: a reader always gets the latest price, never a backlog of old ones.', { focus: 'all' }],
    ['With a plain queue, a slow strategy falls behind and works through old prices. In our prototype, measured: a strategy taking 40 milliseconds a decision, fed a tick every 5 milliseconds, decided on prices three and a half seconds old, with 474 ticks still waiting.', { spot: ['queue'] }],
    ['So every hop keeps one slot per instrument, overwritten by each update. However slow the reader, the next thing it reads is the newest price. The same strategy, measured: prices about 44 milliseconds old, with 500 updates skipped and counted.', { spot: ['slots'] }],
    ['The rules that make it a promise. One batch in flight per reader. Skipped updates are counted and reported, never hidden. A new reader first gets a snapshot of everything it follows. An instrument that stops updating is marked stale, never quietly repeated. And memory stays bounded: one slot per instrument.', { spot: ['rules'] }],
    ['Some things are never skipped: fills, order updates, rejects, timers and commands queue in order. Bars and indicators are built upstream from every tick, so skipping can’t corrupt them, and the archive keeps every tick.', { spot: ['never'] }],
  ]);
  scene({ title: 'The latest price, never a backlog', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const qa = named('queue', area(B, 40, 116, 740, 400, 'A queue', 'cell', 'what we don’t do'));
    const sa = named('slots', area(B, 820, 116, 740, 400, 'One slot per instrument', 'cell', 'what we do'));
    const prodA = box(B, 64, 220, 150, 80, 'prices', { sub: 'every 5 ms', cls: 'out' });
    const pipe = box(B, 250, 230, 300, 60, '', { cls: 'chip' });
    const stA = box(B, 590, 210, 170, 100, 'strategy', { sub: '40 ms a decision' });
    const qPk = Array.from({ length: 14 }, (_, i) => { const r = E('rect', { x: 0, y: 238, width: 16, height: 44, rx: 3, class: 'f-b' }, M); op(r, 0); return r; });
    const ageA = T(B, 410, 380, '', 'big'), noteA = T(B, 410, 430, '', 'cl');
    const sw = sweat(B, 770, 214);
    const prodB = box(B, 844, 220, 150, 80, 'prices', { sub: 'every 5 ms', cls: 'out' });
    const slotB = box(B, 1030, 222, 300, 76, '', { cls: 'chip key' });
    const slotT = T(B, 1180, 270, '', 'sv');
    const stB = box(B, 1370, 210, 170, 100, 'strategy', { sub: '40 ms a decision' });
    const ageB = T(B, 1190, 380, '', 'big'), noteB = T(B, 1190, 430, '', 'cl');
    const msr = [T(B, 410, 480, 'measured, prototype', 'bs'), T(B, 1190, 480, 'measured, prototype', 'bs')];
    const fB = stream(M, route(M, [[994, 260], [1028, 260]]), 'b', 2, 0.4, 5);
    const promise = T(B, 800, 700, '', 'lane');
    const rules = named('rules', area(B, 40, 540, 1000, 320, 'The rules', 'cell'));
    const rl = ['one batch in flight per reader', 'skipped updates counted, never hidden', 'a snapshot first, for a new reader', 'stale is marked, never repeated', 'memory bounded: one slot per instrument']
      .map((s, i) => box(B, 64, 584 + i * 54, 952, 44, s, { cls: 'chip', lcls: 'cl' }));
    const nv = named('never', area(B, 1070, 540, 490, 320, 'Never skipped', 'cell'));
    const nl = ['fills', 'order updates · rejects', 'timers · commands', 'bars, built from every tick', 'the archive: every tick']
      .map((s, i) => box(B, 1094, 584 + i * 54, 442, 44, s, { cls: 'chip', lcls: 'cl' }));
    return (t, rt) => {
      pops([qa, sa], t, 0.2, 0.4);
      typeOn(promise, t, 0.6, S.to[0] - 0.3, 'the latest price, never a backlog', rt); op(promise, 1 - k(t, S.at[3], S.at[3] + 0.4));
      // the queue fills, and the strategy works on old prices
      const qa_ = t > S.at[1] + 0.3;
      pops([prodA, pipe, stA], t, S.at[1] + 0.2, 0.3);
      const n = qa_ ? Math.min(14, Math.floor((rt % 9) * 2.4)) : 0;
      qPk.forEach((r, i) => { op(r, i < n ? 0.9 : 0); r.setAttribute('x', (530 - i * 20).toFixed(1)); });
      sw.go(rt, qa_ && t < S.to[1] + 30);
      countUp(ageA, t, S.at[1] + 1, S.to[1] - 0.6, 0, 3.5, v => qa_ ? `${v.toFixed(1)} s old` : '');
      countUp(noteA, t, S.at[1] + 1, S.to[1] - 0.6, 0, 474, v => qa_ ? `${Math.round(v)} ticks waiting` : '');
      const sb = t > S.at[2] + 0.3;
      pops([prodB, slotB, stB], t, S.at[2] + 0.2, 0.3);
      txt(slotT, sb ? `NIFTY ${fmt(24500 + Math.round(Math.sin(rt * 3) * 40 + Math.sin(rt * 11) * 6))}.${String(Math.floor((rt * 97) % 100)).padStart(2, '0')}` : '');
      fB(rt, sb ? 0 : 1e9);
      countUp(ageB, t, S.at[2] + 1, S.to[2] - 0.6, 0, 44, v => sb ? `~${Math.round(v)} ms old` : '');
      countUp(noteB, t, S.at[2] + 1, S.to[2] - 0.6, 0, 500, v => sb ? `${Math.round(v)} skipped, counted` : '');
      op(msr[0], k(t, S.to[1] - 1, S.to[1] - 0.5)); op(msr[1], k(t, S.to[2] - 1, S.to[2] - 0.5));
      pop(rules, t, S.at[3] + 0.2); rl.forEach((b, i) => pop(b, t, S.at[3] + 0.6 + i * (S.to[3] - S.at[3] - 1) / 5));
      pop(nv, t, S.at[4] + 0.2); nl.forEach((b, i) => pop(b, t, S.at[4] + 0.6 + i * (S.to[4] - S.at[4] - 1) / 5));
    };
  });
}

// 13. Computed streams.
{
  const S = beats([
    ['Computed streams, in Rust, works out what many strategies would otherwise each compute on their own: bars, indicators, implied volatility and greeks, and option chain views. It runs as an A and B pair in each cell.', { focus: 'all' }],
    ['Each series is a node in a DAG: a graph of calculations where each feeds the next, and nothing loops back. A node’s id is a hash of its function, its parameters and its inputs.', { spot: ['dag'] }],
    ['So when two strategies ask for EMA 20 on NIFTY’s one-hour bars, they get the same node, computed once.', { spot: ['ema', 'sa', 'sb'] }],
    ['With each batch of updates, it recalculates only the nodes that changed, in order. Bars close on exchange time, not when our machine happens to see the tick.', { spot: ['dag'] }],
    ['Implied volatility uses Jäckel’s “Let’s Be Rational” method on the future’s price, with the Black-76 model, and the greeks come from the same. Chain views answer questions like “which strike is at the money, by the future” in memory.', { spot: ['iv', 'gk', 'cv'] }],
    ['Every output goes back out as a synthetic instrument, so a strategy subscribes to EMA 20 exactly the way it subscribes to a price.', { spot: ['out'] }],
    ['When a cell starts, it warms up from the lake, so an indicator is ready at 09:15, not twenty bars later.', { spot: ['warm'] }],
  ]);
  scene({ title: 'Computed streams', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const cs = named('cs', svc(B, 40, 116, 520, 744, 'computed-streams', { lang: 'Rust', kind: 'StatefulSet · A/B', part: 'trade',
      does: 'Computed once, read by every strategy that asks.', comps: ['series catalogue', 'DAG evaluator', 'bar builder', 'indicators', 'IV and greeks', 'chain views', 'warm-up from the lake', 'synthetic-instrument writer'], cols: 1, ch: 44 }));
    named('dag', bounds(G(B), 600, 116, 960, 600));
    const n = (id, x, y, w, l, s, cls = '') => named(id, box(B, x, y, w, 70, l, { sub: s, cls }));
    const fut = n('fut', 600, 140, 240, 'NIFTY future', 'ticks', 'out'), opt = n('opt', 600, 420, 240, 'NIFTY options', 'ticks', 'out');
    const bar = n('bar', 900, 140, 220, '1-hour bars', 'close on exchange time');
    const ema = n('ema', 1180, 140, 250, 'EMA(20)', 'id = hash(ema, 20, bars)', 'key');
    const iv = n('iv', 900, 420, 220, 'IV', 'Black-76 on the future');
    const gk = n('gk', 1180, 420, 180, 'greeks', 'same model');
    const cv = n('cv', 1180, 560, 360, 'chain view', '“ATM by future”, “strike by premium”');
    const sa = named('sa', box(B, 1450, 120, 110, 60, 'strategy A', { cls: 'out', lcls: 'cmp' })), sb = named('sb', box(B, 1450, 230, 110, 60, 'strategy B', { cls: 'out', lcls: 'cmp' }));
    const ls = [link(L, fut, bar, 'b'), link(L, bar, ema, 'b'), link(L, opt, iv, 'b'), link(L, fut, iv, 'b', { sides: ['b', 't'], d1: 40, d2: -40 }), link(L, iv, gk, 'b'),
      link(L, iv, cv, 'b', { sides: ['b', 'l'] }), link(L, sa, ema, 'ink', { sides: ['l', 'r'], d2: -18, dash: true }), link(L, sb, ema, 'ink', { sides: ['l', 'r'], d2: 18, dash: true })];
    const out = named('out', box(B, 600, 760, 960, 90, 'out as synthetic instruments → the tick cache', { sub: 'subscribed to like a price: EMA(20) has an instrument id', cls: 'key' }));
    const warm = named('warm', db(B, 600, 600, 240, 130, 'S3 lake', 'yesterday’s ticks and bars'));
    const same = bubble(M, 1290, 212, 'same node!', { below: true });
    const flows = [stream(M, ls[0], 'b', 2, 1.2, 5), stream(M, ls[1], 'b', 2, 1.2, 5), stream(M, ls[2], 'b', 2, 1.2, 5), stream(M, ls[4], 'b', 2, 1.2, 5), stream(M, ls[5], 'b', 2, 1.6, 5)];
    const outP = route(M, [[1290, 210], [1290, 300], [1080, 300], [1080, 760]]);
    const outD = stream(M, outP, 'g', 3, 2.0, 7);
    const wl = link(L, warm, cs, 'pu', { sides: ['l', 'r'], d2: 230 });
    const wf = stream(M, wl, 'pu', 3, 1.4, 6);
    return (t, rt) => {
      pop(cs, t, 0.2);
      pops([fut, opt, bar, ema, iv, gk, cv], t, 1.0, (S.to[0] - 1.5) / 7);
      ls.slice(0, 6).forEach((l, i) => show(l, t, S.at[1] + i * (S.to[1] - S.at[1]) / 6, S.at[1] + (i + 1) * (S.to[1] - S.at[1]) / 6));
      flows.forEach(f => f(rt, t > S.at[1] + 2.4 ? 0 : 1e9));
      pops([sa, sb], t, S.at[2] + 0.3, 0.6); show(ls[6], t, S.at[2] + 1.0); show(ls[7], t, S.at[2] + 1.6);
      same.go(t, S.at[2] + 2.2, S.to[2] + 0.4, rt);
      walk([bar, ema], t, S.at[3] + 0.4, S.to[3]);
      walk([iv, gk, cv], t, S.at[4] + 0.4, S.to[4]);
      pop(out, t, S.at[5] + 0.3); outD(rt, t > S.at[5] + 0.8 ? 0 : 1e9);
      pop(warm, t, S.at[6] + 0.3); show(wl, t, S.at[6] + 0.8); wf(rt, t > S.at[6] + 1.2 ? 0 : 1e9);
      flash(cs.comp['warm-up from the lake'], t, S.at[6] + 1.2, 1.5);
    };
  });
}

// 14. The tick cache agent.
{
  const S = beats([
    ['On every runtime machine, the tick cache agent, in Rust, takes the streams and writes them into shared memory: memory that the agent and the supervisor on that machine both map. Reading a price is a memory read: no copy, no network.', { focus: 'all' }],
    ['There is one slot per instrument, 128 bytes, overwritten with each update. The slot is the latest price by construction.', { spot: ['slots'] }],
    ['Slots use a seqlock. The writer bumps a version number to odd, writes, then bumps it to even. A reader copies the slot, then checks the version didn’t change; if it did, it simply reads again. Nobody ever waits on a lock.', { focus: ['slot0', 'ver'], spot: ['slot0', 'ver'] }],
    ['Beside the slots is a ring of the last million updates, two to the twentieth, with a cursor for each reader. A reader that falls a whole lap behind doesn’t hold anyone up: it jumps to the slots, and is told how many it skipped.', { focus: 'all', spot: ['ring'] }],
    ['It runs as a DaemonSet: exactly one copy on every runtime machine.', { spot: ['agent'] }],
  ]);
  scene({ title: 'The tick cache agent', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const agent = named('agent', svc(B, 40, 116, 460, 300, 'cache-agent', { lang: 'Rust', kind: 'DaemonSet', part: 'trade',
      does: 'Writes the tick streams into shared memory.', comps: ['stream client', 'slot writer', 'ring writer'], cols: 1, ch: 40 }));
    const inS = box(B, 40, 450, 460, 70, 'gRPC streams in', { sub: 'ticks + computed series', cls: 'out' });
    const mem = area(B, 540, 116, 1020, 744, 'Shared memory on this machine', 'cell');
    const names = ['NIFTY', 'BANKNIFTY', 'NIFTY FUT', '24500 CE', '24500 PE', '24600 CE', '24400 PE', 'EMA(20) 1h', 'IV ATM %', 'FINNIFTY', 'SENSEX', 'INDIA VIX'];
    const BASE = [24500, 51800, 24560, 142, 128, 96, 104, 24480, 13.2, 23900, 80500, 13.4];   // illustrative
    const slots = names.map((nm, i) => { const b = box(B, 570 + (i % 4) * 240, 170 + Math.floor(i / 4) * 92, 224, 78, nm, { cls: 'chip', top: true, lcls: 'cmp' }); b._v = T(b, b.cx, b.Y + 62, '', 'sv'); return b; });
    named('slots', bounds(G(B), 570, 170, 944, 260)); named('slot0', slots[0]);
    const ver = named('ver', box(B, 570, 460, 464, 120, '', { cls: 'frame key' }));
    const verT = T(ver, 590, 500, '', 'mono', 'start'), verS = T(ver, 590, 540, '', 'cl', 'start');
    const ring = named('ring', bounds(G(B), 1060, 450, 480, 400));
    const R = G(B); const cx = 1300, cy = 655, rr = 160;
    E('circle', { cx, cy, r: rr, class: 'memb', fill: 'none' }, R);
    for (let i = 0; i < 48; i++) { const a = i / 48 * Math.PI * 2; E('line', { x1: cx + Math.cos(a) * (rr - 12), y1: cy + Math.sin(a) * (rr - 12), x2: cx + Math.cos(a) * (rr + 12), y2: cy + Math.sin(a) * (rr + 12), class: 'life' }, R); }
    T(R, cx, cy - 4, 'ring: 2²⁰ updates', 'cl'); T(R, cx, cy + 20, 'a cursor per reader', 'bs');
    op(R, 0);
    const head = dot(M, 'b', 10), curs = [dot(M, 'g', 9), dot(M, 'wt', 9)];
    const capL = T(M, 1300, 846, '', 'lab');
    const fl = stream(M, link(L, inS, agent, 'b', { sides: ['t', 'b'] }), 'b', 2, 1.0, 6);
    const wr = stream(M, route(M, [[500, 250], [568, 250]]), 'b', 3, 0.6, 5);
    const at = (a, r = rr) => [cx + Math.cos(a - Math.PI / 2) * r, cy + Math.sin(a - Math.PI / 2) * r];
    return (t, rt) => {
      pops([agent, inS, mem], t, 0.2, 0.4);
      fl(rt, t > 1.6 ? 0 : 1e9); wr(rt, t > 2.4 ? 0 : 1e9);
      pops(slots, t, 1.6, (S.to[0] - 2) / slots.length);
      slots.forEach((b, i) => txt(b._v, t > 2 ? (BASE[i] * (1 + 0.0009 * Math.sin(rt * (1.3 + i * 0.37) + i))).toFixed(BASE[i] < 100 ? 2 : 1) : ''));
      // the seqlock, step by step
      pop(ver, t, S.at[2] + 0.3);
      const cyc = ((rt * 0.5) % 1), v = 40 + 2 * Math.floor(rt * 0.5);
      const phase = cyc < 0.25 ? [v, 'reader copies; version unchanged: done'] : cyc < 0.5 ? [v + 1, 'writer bumps it to odd, and writes'] : cyc < 0.75 ? [v + 1, 'a reader sees odd: reads again'] : [v + 2, 'writer bumps it to even: done'];
      txt(verT, t > S.at[2] + 0.5 ? `NIFTY slot · version ${phase[0]} (${phase[0] % 2 ? 'odd' : 'even'})` : '');
      txt(verS, t > S.at[2] + 0.5 ? phase[1] : '');
      flash(slots[0], t > S.at[2] + 0.5 && t < S.to[2] && cyc >= 0.25 && cyc < 0.75 ? t : -1, t - 0.01, 1);
      // the ring
      fade(R, t, S.at[3] + 0.3);
      const on = t > S.at[3] + 0.6, hA = rt * 1.3;
      op(head, on ? 1 : 0); tr(head, ...at(hA));
      op(curs[0], on ? 1 : 0); tr(curs[0], ...at(hA - 0.5, rr - 28));
      const lag = Math.min(Math.PI * 2, (rt % 8) * 0.9), lapped = lag >= Math.PI * 2 - 0.01;
      op(curs[1], on ? 1 : 0); tr(curs[1], ...at(hA - (lapped ? 0.3 : lag), rr + 28));
      txt(capL, on ? (lapped ? 'a lap behind: jumps to the slots, told it skipped' : 'green keeps up; orange falls behind') : '');
    };
  });
}
