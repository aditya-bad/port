// 7. A strategy's life: the conversation.
{
  const S = beats([
    ['Now the small flows inside one strategy’s life. First, how the supervisor and a strategy talk: length-prefixed protobuf frames, on a Unix socket the supervisor opens for that one strategy.', { focus: 'all' }],
    ['Hello comes first: the protocol version, the deployment, its parameters, its last saved state, and its instruments.', {}],
    ['The strategy answers Ready: its SDK’s name and version, and what it subscribes to: instruments, bars, computed streams and timers.', {}],
    ['Then the batches. Each has a sequence number, the time, the latest value of each instrument that changed, with how many updates were skipped, and every bar, fill, order update and timer since the last one.', {}],
    ['While it handles a batch, the strategy can ask: Request, for an option chain, a quote, some history or a lookup. The Reply comes from caches on the same machine, within the same batch, never from the network.', {}],
    ['Done ends the batch: its sequence number, the commands, meaning orders, baskets, cancels and timers, the decisions, and the change to its state, with a hash. Then the next batch.', {}],
    ['Around them: Snapshot, its state with a sequence number. Stop, for a clean stop. Log. Heartbeat, to show it is alive. And Error, with the batch, the message and the stack.', { spot: ['side'], pad: 3 }],
    ['The user never sees any of this. The SDK owns main: it reads Hello, builds the user’s class with its parameters and state, says Ready, and loops: batch, handlers, Done. The user’s code only sees ctx.', { spot: ['sdk'] }],
  ]);
  scene({ title: 'A strategy’s life: the conversation', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const XS = 330, XT = 1010;
    const sup = node(B, XS - 170, 116, 340, 'supervisor', 'Rust', 'trade', { nick: 'Iceberg' });
    const stg = node(B, XT - 170, 116, 340, 'strategy', 'any SDK', 'trade', { kind: 'in its sandbox' });
    const life = G(L);
    [XS, XT].forEach(x => E('line', { x1: x, y1: 182, x2: x, y2: 740, class: 'life' }, life)); op(life, 0);
    const sock = T(B, (XS + XT) / 2, 200, 'Unix socket · length-prefixed protobuf frames', 'bs'); op(sock, 0);
    const pipeR = route(M, [[XS + 10, 184], [XT - 10, 184]]), pipeL = route(M, [[XT - 10, 184], [XS + 10, 184]]);
    const fr = [stream(M, pipeR, 'b', 3, 1.3, 5), stream(M, pipeL, 'g', 3, 1.3, 5)];
    const msgs = [['Hello', 1, 'protocol version · deployment · params · last state · instruments'],
      ['Ready', -1, 'SDK name and version · subscriptions: instruments, bars, streams, timers'],
      ['Batch', 1, 'seq · now · latest values (+ skipped) · bars · fills · order updates · timers'],
      ['Request', -1, 'chain · quote · history · lookup'],
      ['Reply', 1, 'from this machine’s caches, within the batch'],
      ['Done', -1, 'seq · commands · decisions · state change + hash']];
    const ys = [244, 324, 404, 484, 564, 644];
    const ms = msgs.map(([n, dir, d], i) => {
      const y = ys[i], a = dir > 0 ? XS : XT, b = dir > 0 ? XT : XS;
      const l = line(L, `M${a} ${y}H${b - dir * 8}`, dir > 0 ? 'b' : 'g');
      const lab = T(B, (XS + XT) / 2, y - 12, n, 'cl'); op(lab, 0);
      const det = typer(B, (XS + XT) / 2, y + 28, d, 80, 'txt', 20, 'middle');
      const pk = packet(M, n, dir > 0 ? 'b' : 'g');
      return { l, lab, det, pk };
    });
    const loop = E('path', { d: `M${XT + 24} 640C${XT + 90} 644 ${XT + 90} 404 ${XT + 30} 404`, class: 'ln c-ink dash', 'marker-end': 'url(#ah-ink)' }, L); op(loop, 0);
    const loopT = T(B, XT + 96, 520, 'next batch', 'bs', 'start'); op(loopT, 0);
    const side = named('side', bounds(G(B), 34, 760, 1132, 92));
    const sm = [['Snapshot', 'state + seq'], ['Stop', 'clean stop'], ['Log', 'its logs'], ['Heartbeat', 'alive'], ['Error', 'batch · error · stack']]
      .map(([a, b], i) => box(B, 40 + i * 226, 766, 214, 80, a, { sub: b, cls: 'chip' }));
    const sdk = named('sdk', panel(B, 1180, 210, 380, 540, 'The SDK owns main()'));
    const sdkL = typeList(sdk, 1200, 270, ['reads Hello', 'builds your class: params, state', 'says Ready', 'loops: Batch → your handlers → Done', 'your code sees only ctx'], 30, 'cl', 26, 26);
    const heart = G(M); E('path', { d: 'M0 6C-12-6-8-16 0-9C8-16 12-6 0 6Z', class: 'f-r' }, heart); op(heart, 0);
    return (t, rt) => {
      pop(sup, t, 0.3); pop(stg, t, 0.6); fade(life, t, 0.9); fade(sock, t, 1.2); pop(sdk, t, 1.6);
      fr.forEach(f => f(rt, t > 1.4 && t < S.at[1] + 0.4 ? 0 : 1e9));
      ms.forEach(({ l, lab, det, pk }, i) => {
        const a = S.at[i + 1], b = S.to[i + 1];
        reveal(l, t, a + 0.2, a + 0.8); op(lab, k(t, a + 0.4, a + 0.8));
        travel(pk, l, t, a + 0.3, a + 1.6);
        det.go(t, a + 0.9, b - 0.3, rt);
      });
      reveal(loop, t, S.to[5] - 1.2, S.to[5] - 0.4); fade(loopT, t, S.to[5] - 0.6);
      pops(sm, t, S.at[6] + 0.3, (S.to[6] - S.at[6] - 1.0) / sm.length);
      sm.forEach((x, i) => flash(x, t, S.at[6] + 0.5 + i * (S.to[6] - S.at[6] - 1.0) / sm.length, 0.8));
      // Heartbeat beats
      const hb = S.at[6] + 0.5 + 3 * (S.to[6] - S.at[6] - 1.0) / sm.length;
      op(heart, t > hb ? 1 : 0); tr(heart, sm[3].R - 26, sm[3].Y + 22); look(heart, { s: 1 + 0.25 * Math.max(0, Math.sin(rt * 7)) });
      sdkL.go(t, S.at[7] + 0.3, S.to[7] - 0.3, rt);
    };
  });
}

// 8. Request and Reply: the delay they remove.
{
  const S = beats([
    ['Request and Reply remove the biggest delay in today’s app, and it isn’t computing.', { focus: 'all' }],
    ['Today a strategy picks its strikes by asking Kite: quotes to find a strike by premium, last prices, liquidity. Every Kite call goes through one throttle for the whole app, at least 0.35 seconds apart.', { spot: ['gate'] }],
    ['So an entry waits for its calls. Counted in today’s code: a SuperTrend entry makes 2 to 4 calls; a trend spread, 3 to 8; a DTT entry, 5, at least 1.75 seconds; a strangle entry, 11, at least 3.85 seconds. Plus Kite’s own response time.', { spot: ['bars'] }],
    ['And deployments that enter at 09:20 all queue behind each other, at that one throttle.', { spot: ['gate'] }],
    ['With an instant fake Kite, our own work for the same entries takes 29 to 76 milliseconds, measured.', { spot: ['bars'] }],
    ['In the design, option chains and prices are streams, already cached on the machine. The strike nearest 100 rupees becomes a local Request, answered in microseconds. The only remote call left is the order itself.', { focus: 'all' }],
  ]);
  scene({ title: 'Request and Reply: the delay they remove', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    // the throttle: one gate, calls through it 0.35 s apart
    const gateP = named('gate', panel(B, 40, 116, 1520, 230, 'Today: one throttle for every Kite call'));
    const gate = box(B, 900, 170, 120, 150, '≥ 0.35 s', { cls: 'key', lcls: 'lab', top: true });
    const kite = box(B, 1300, 190, 220, 110, 'Kite', { sub: 'quotes · LTP · depth', cls: 'out' });
    const lane = route(M, [[80, 245], [1290, 245]]);
    const calls = [...Array(12)].map((_, i) => packet(M, ['DTT', 'strangle', 'ST', 'spread'][i % 4], ['b', 'pu', 'g', 'wt'][i % 4], { w: 90 }));
    const qNote = T(B, 120, 330, '', 'bs', 'start');
    const intro0 = T(B, 120, 196, '', 'cl', 'start');
    // the waits, per entry
    const bars = named('bars', panel(B, 40, 366, 1520, 330, 'An entry’s wait for its Kite calls (at ≥ 0.35 s a call)'));
    const x0 = 360, sx = 260;                                            // 1 s = 260 px
    const ax = G(B);
    E('line', { x1: x0, y1: 664, x2: x0 + 4.5 * sx, y2: 664, class: 'axis' }, ax);
    for (let s = 0; s <= 4; s++) { E('line', { x1: x0 + s * sx, y1: 658, x2: x0 + s * sx, y2: 670, class: 'axis' }, ax); T(ax, x0 + s * sx, 688, `${s} s`, 'bs'); }
    op(ax, 0);
    const rows = [['SuperTrend entry', 2, 4], ['trend spread', 3, 8], ['DTT entry', 5, 5], ['strangle entry', 11, 11]].map(([n, a, b], i) => {
      const y = 418 + i * 56;
      const lab = T(B, x0 - 20, y + 20, n, 'cl', 'end'); op(lab, 0);
      const lo = E('rect', { x: x0, y, width: 0, height: 30, rx: 4, class: 'f-r', opacity: 0.8 }, B);
      const hi = E('rect', { x: x0, y, width: 0, height: 30, rx: 4, class: 'f-r', opacity: 0.3 }, B);
      const val = T(B, x0 + 8, y + 21, '', 'sv', 'start');
      return { lab, lo, hi, val, a, b, y };
    });
    const ours = E('rect', { x: x0, y: 418 + 4 * 56 - 4, width: 0, height: 0, class: 'f-g' }, B);
    const oursT = T(B, x0 - 20, 418 + 4 * 56 + 16, '', 'cl', 'end');
    // the design
    const des = named('des', panel(B, 40, 716, 1520, 136, ''));
    const loc = box(B, 70, 742, 560, 86, 'Request: the strike nearest ₹100', { sub: 'answered on the machine, in microseconds', cls: 'art' });
    const ord = box(B, 980, 742, 540, 86, 'the order', { sub: 'the only call that leaves the machine', cls: 'proc' });
    const lo = link(L, loc, ord, 'g', { label: 'then', ly: -10 });
    const lnk = stream(M, lo, 'g', 3, 1.2, 5);
    return (t, rt) => {
      pop(gateP, t, 0.3); pop(gate, t, S.to[0] * 0.35); pop(kite, t, S.to[0] * 0.6);
      typeAt(intro0, t, 0.6, S.to[0] - 0.2, 'strikes picked by asking Kite, inside the decision', rt); op(intro0, intro0.textContent ? 1 : 0);
      lnk(rt, t > S.at[5] + 2.2 ? 0 : 1e9);
      // calls trickle through the gate, one every 0.35 s (in the picture: one a second of narration)
      if (t < S.at[3]) {                                                   // four calls, one after another
        calls.forEach((c, i) => {
          const a = S.at[1] + 0.4 + i * 1.3, f = (t - a) / 2.6;
          if (i < 4 && f >= 0 && f <= 1) { tr(c, lerp(120, 1290, f), 245); op(c, Math.min(1, (1 - f) * 10)); } else op(c, 0);
        });
      } else {                                                             // 09:20: a queue at the gate
        const a = S.at[3] + 0.3, n = Math.max(0, t - a) / 0.9;
        calls.forEach((c, i) => {
          const x = n < i ? 880 - (i - n) * 100 : 880 + (n - i) * 410;
          tr(c, x, 245); op(c, x < 110 || x > 1280 ? 0 : 1);
          if (n < i) bob(c._in, rt, 1.5, 3, i);
        });
      }
      typeAt(qNote, t, S.at[3] + 0.5, S.to[3] - 0.4, 'at 09:20: every deployment entering, one queue, one call at a time', rt);
      // bars
      pop(bars, t, S.at[2] - 0.2); fade(ax, t, S.at[2] + 0.2);
      const per = (S.to[2] - S.at[2] - 1.0) / rows.length;
      rows.forEach((r, i) => {
        const a = S.at[2] + 0.4 + i * per;
        op(r.lab, k(t, a, a + 0.3));
        const f = ke(t, a + 0.2, a + per * 0.8);
        r.lo.setAttribute('width', (r.a * 0.35 * sx * f).toFixed(1));
        r.hi.setAttribute('width', (r.b * 0.35 * sx * f).toFixed(1));
        txt(r.val, f > 0.95 ? (r.a === r.b ? `${r.a} calls · ≥ ${(r.a * 0.35).toFixed(2)} s` : `${r.a}–${r.b} calls · ≥ ${(r.a * 0.35).toFixed(2)}–${(r.b * 0.35).toFixed(2)} s`) : '');
        r.val.setAttribute('x', (x0 + r.b * 0.35 * sx * f + 10).toFixed(1));
      });
      const fo = ke(t, S.at[4] + 0.4, S.at[4] + 1.2);
      ours.setAttribute('height', (30 * fo).toFixed(1)); ours.setAttribute('width', (0.076 * sx * fo).toFixed(1));
      typeAt(oursT, t, S.at[4] + 0.6, S.to[4] - 0.4, 'our own work: 29–76 ms (measured)', rt);
      // the design
      pop(des, t, S.at[5] + 0.2); pop(loc, t, S.at[5] + 0.6); show(lo, t, S.at[5] + 1.6); pop(ord, t, S.at[5] + 2.2);
      flash(loc, t, S.at[5] + 1.0, 1.2);
    };
  });
}

// 9. The walk: replaying a strategy exactly.
{
  const S = beats([
    ['The walk is how the design answers “what happened at 10:42?”, and it is how the day check and repairs work.', { focus: 'all' }],
    ['The journal keeps every batch exactly as it was given, written before it is sent: references to the latest values seen, the fills, order updates and timers, and the time. It is indexed by sequence number and by time. A batch takes about 60 to 120 bytes: an estimated 8 gigabytes a day, compressed, for 2,000 strategies.', { spot: ['jr'] }],
    ['To walk, find the batch range by time, and start a fresh process of the same strategy version, from the snapshot just before it.', {}],
    ['Feed it the journal’s batches, with the sim venue as its broker. After every batch, its state hash must match the one live recorded.', {}],
    ['The first batch whose decisions differ is what went wrong. In the prototype, measured: a walk from snapshot 300 matched live exactly, all the way to batch 600.', {}],
    ['A stretch the app never saw, a stall, has no journal. Its batches are built from one-minute candles, four ticks a minute, as today’s repair does.', {}],
    ['And a minute that can be read two ways starts two processes from the same snapshot, one for each reading. If they part, the owner chooses which one happened, on the Repair page.', {}],
  ]);
  scene({ title: 'The walk: replaying a strategy exactly', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const N = 30, cx0 = 100, cw = 46, cy = 300;                          // batches 296 … 325
    const jr = named('jr', panel(B, 40, 196, 1520, 190, 'The journal'));
    const cells = [...Array(N)].map((_, i) => {
      const g = G(B, 'slot'); E('rect', { x: cx0 + i * cw, y: cy, width: cw - 6, height: 44, rx: 4 }, g);
      if ((296 + i) % 5 === 0) T(g, cx0 + i * cw + (cw - 6) / 2, cy + 64, String(296 + i), 'bs');
      op(g, 0); return g;
    });
    const snapAt = 4;                                                    // batch 300
    const snap = G(B); diamond(snap, cx0 + snapAt * cw + 20, cy - 16, 11); T(snap, cx0 + snapAt * cw + 20, cy - 34, 'snapshot', 'bs'); op(snap, 0);
    const times = G(B); [['10:40', 0], ['10:41', 10], ['10:42', 20]].forEach(([s, i]) => T(times, cx0 + i * cw, 288, s, 'lab', 'start')); op(times, 0);
    const q = bubble(B, 1240, 150, 'what happened at 10:42?', {});
    const jG = G(B);
    const jInfo = typeList(jG, 1100, 232, ['each: values seen · fills · updates · timers · now', '~60–120 bytes a batch (estimate)', '~8 GB a day, 2,000 strategies (estimate)'], 54, 'bs', 18, 2);
    // the fresh process and its broker
    const proc = box(B, 80, 452, 420, 110, 'a fresh process', { sub: 'same version · from snapshot 300', cls: 'proc' });
    const sim = box(B, 80, 610, 420, 96, 'sim venue', { sub: 'as its broker', cls: 'chip' });
    const lsim = link(L, proc, sim, 'g', { sides: ['b', 't'] });
    const hashT = T(B, 560, 470, 'state hash vs live', 'fl', 'start'); op(hashT, 0);
    const marks = [...Array(N)].map((_, i) => { const g = check(M, cx0 + i * cw + 20, 420, true); return g; });
    const badAt = 21;
    const bad = check(M, cx0 + badAt * cw + 20, 420, false);
    const wrong = T(B, cx0 + badAt * cw + 20, 470, '', 'warnl');
    const cursor = G(M); E('rect', { x: -4, y: cy - 8, width: 8, height: 60, rx: 3, class: 'f-b' }, cursor); op(cursor, 0);
    const proto = typer(B, 560, 530, 'prototype, measured: from snapshot 300, identical to live through batch 600', 70, 'okl');
    // a stall and a fork
    const stall = E('rect', { x: cx0 + 24 * cw - 3, y: cy - 6, width: 6 * cw, height: 56, rx: 6, class: 'hl' }, B); op(stall, 0);
    const stallT = typer(B, cx0 + 24 * cw, 228, 'a stall: no journal; built from 1-minute candles, 4 ticks a minute', 30, 'bs', 16);
    const forkG = G(L);
    const up = E('path', { d: `M${cx0 + 27 * cw} 370C${cx0 + 27 * cw} 520 1180 560 1180 620`, class: 'ln c-g' }, forkG);
    const dn = E('path', { d: `M${cx0 + 27 * cw} 370C${cx0 + 27 * cw} 520 1460 560 1460 620`, class: 'ln c-wt' }, forkG);
    [up, dn].forEach(e => op(e, 0));
    const pu = box(B, 1040, 626, 240, 90, 'reads up first', { cls: 'chip', lcls: 'lab' }), pd = box(B, 1320, 626, 240, 90, 'reads down first', { cls: 'chip', lcls: 'lab' });
    const pick = typer(B, 1300, 760, 'they part: the owner chooses, on the Repair page', 40, 'cl', 22, 'middle');
    const feed = stream(M, route(M, [[cx0 + 120, cy + 70], [cx0 + 160, 452]]), 'b', 2, 0.9, 5);
    return (t, rt) => {
      pop(jr, t, 0.2);
      cells.forEach((c, i) => pop(c, t, 0.4 + i * 0.05));
      fade(times, t, 1.6); pop(snap, t, 1.9);
      q.go(t, 0.9, S.to[0] + 0.2, rt);
      jInfo.go(t, S.at[1] + 0.4, S.to[1] - 0.4, rt); op(jG, 1 - k(t, S.at[2], S.at[2] + 0.4));
      walk(cells, t, S.at[1] + 0.3, S.to[1] - 0.3);
      // find the range and start from the snapshot
      const d2 = S.to[2] - S.at[2];
      pop(proc, t, S.at[2] + d2 * 0.8);
      op(cursor, k(t, S.at[2] + 0.3, S.at[2] + 0.6));
      const cx = c => cx0 + c * cw + 20;
      if (t < S.at[3]) tr(cursor, lerp(cx(20), cx(snapAt), ke(t, S.at[2] + 0.4, S.at[2] + d2 * 0.75)), 0);
      // feed the batches; hashes match until the bad one
      pop(sim, t, S.at[3] + 0.3); show(lsim, t, S.at[3] + 0.6); fade(hashT, t, S.at[3] + 0.4);
      feed(rt, t > S.at[3] + 0.6 && t < S.to[4] ? 0 : 1e9);
      const walkEnd = S.at[4] + 1.6;
      const pos = t < S.at[3] ? snapAt : lerp(snapAt, badAt, k(t, S.at[3] + 0.6, walkEnd));
      if (t >= S.at[3]) tr(cursor, cx(Math.min(pos, badAt)), 0);
      marks.forEach((m, i) => { if (i < snapAt || i >= badAt) { op(m, 0); return; } if (t >= S.at[3] && pos >= i) pop(m, t, S.at[3] + 0.6 + (i - snapAt) / (badAt - snapAt) * (walkEnd - S.at[3] - 0.6)); else op(m, 0); });
      pop(bad, t, walkEnd);
      typeAt(wrong, t, walkEnd + 0.2, walkEnd + 1.2, 'differs: what went wrong', rt);
      proto.go(t, walkEnd + 1.4, S.to[4] - 0.2, rt);
      // a stall
      op(stall, k(t, S.at[5] + 0.3, S.at[5] + 0.7));
      stallT.go(t, S.at[5] + 0.6, S.to[5] - 0.3, rt);
      // a fork
      reveal(up, t, S.at[6] + 0.4, S.at[6] + 1.4); reveal(dn, t, S.at[6] + 0.6, S.at[6] + 1.6);
      pop(pu, t, S.at[6] + 1.4); pop(pd, t, S.at[6] + 1.6);
      pick.go(t, S.at[6] + 2.0, S.to[6] - 0.3, rt);
    };
  });
}
