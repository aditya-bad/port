// 12. A trading day, start to end.
{
  const ev = [
    [8.5, 0, 0, 'Before 08:30', 'reference data loads the exchanges’ contract files and every broker’s list; symbol maps load',
      'Before 08:30, reference data loads the exchanges’ contract files and every broker’s instrument list. New expiries appear, and each broker gateway loads its symbol map.'],
    [8.75, 0, 1, 'Logins', 'each broker’s daily login and token refresh; today: the owner’s Kite login',
      'Each broker’s daily login runs, and its token is refreshed. Today, that is the owner’s Kite login in the app, before 09:15.'],
    [8.95, 1, 0, 'Subscribe, smoke-test', 'the union of what running strategies need, new expiries included; a smoke test per broker',
      'Market data subscribes to every instrument that running strategies need, the union, taken from control, new expiries included. And every broker gets a smoke test before 09:00.'],
    [9.25, 1, 1, 'The open', 'no releases from 09:00; no moves 09:10–09:20; indicators warm; burst assumed 10×',
      'From 09:00 no releases go out, and from 09:10 to 09:20 no strategy moves. At 09:15 the indicators are already warm, from the lake. The burst at the open is assumed to be ten times normal, until it is logged.'],
    [12, 2, 0, 'All day', 'positions reconciled every few minutes; no deployment silent for 60 s',
      'All day, positions are reconciled with each broker every few minutes, and no running deployment may be silent for more than 60 seconds.'],
    [15.25, 2, 1, '15:15 exits', 'about 150,000 orders in minutes, by account, risk-reducing first; drained by 15:20',
      'At 15:15 come the exits. The design allows for about 150,000 orders in minutes, split by account, within each broker’s limits, risk-reducing orders first. They are drained by 15:20.'],
    [15.58, 3, 0, '15:35', 'zero unreconciled positions; the freeze lifts',
      'By 15:35, zero positions are unreconciled, and the freeze lifts.'],
    [16.5, 3, 1, '15:45 → 16:30', 'export; releases may go out, a cell at a time; checks and repair cases by 16:30',
      'At 15:45 the day is exported, and releases may go out, a cell at a time. The end-of-day checks and repair cases are ready by 16:30.'],
  ];
  const S = beats([
    ['A trading day, by the clock. This is the final design; at the end, what today’s app already does.', { focus: 'all' }],
    ...ev.map(e => [e[5], {}]),
    ['Today’s app already keeps this clock, in a smaller way. Every minute it turns problems into issues, and alerts until they clear. It records each deployment’s state at the open and after the close, and starts the day check after the 15:45 export. Mark as holiday mutes the alerts until 07:00 the next day.', { spot: ['today'] }],
  ]);
  scene({ title: 'A trading day, start to end', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const AY = 470, X = h => 80 + (h - 7) * (1440 / 9.75);
    const ax = G(B);
    E('line', { x1: X(7), y1: AY, x2: X(16.75), y2: AY, class: 'axis' }, ax);
    for (let h = 7; h <= 16; h++) { E('line', { x1: X(h), y1: AY - 6, x2: X(h), y2: AY + 6, class: 'axis' }, ax); T(ax, X(h), AY + 26, `${String(h).padStart(2, '0')}:00`, 'bs'); }
    op(ax, 0);
    const frz = G(B); E('rect', { x: X(9), y: AY - 5, width: X(15.58) - X(9), height: 10, class: 'f-r', opacity: 0.25 }, frz); op(frz, 0);
    const nmG = G(B); [[9 + 10 / 60, 9 + 20 / 60], [15 + 10 / 60, 15 + 25 / 60]].forEach(([a, b]) => E('rect', { x: X(a), y: AY - 9, width: X(b) - X(a), height: 18, class: 'f-r', opacity: 0.6 }, nmG)); op(nmG, 0);
    const cards = ev.map(([h, slot, below, title, body]) => {
      const x = 40 + slot * 390, y = below ? 540 : 140, w = 350, hh = 170;
      const g = box(B, x, y, w, hh, '', { cls: 'chip' });
      T(g, x + 18, y + 32, title, 'cl', 'start');
      const tp = typer(g, x + 18, y + 64, body, 34, 'txt', 23);
      const ld = line(L, below ? `M${x + w / 2} ${y}L${X(h)} ${AY + 8}` : `M${x + w / 2} ${y + hh}L${X(h)} ${AY - 8}`, 'ink', { arrow: false });
      return { g, tp, ld };
    });
    const cur = G(M); E('rect', { x: -2, y: AY - 34, width: 4, height: 68, class: 'f-b' }, cur);
    const clk = T(cur, 0, AY - 42, '', 'sl'); op(cur, 0);
    const exits = stream(M, route(M, [[X(15.25), AY], [X(15.25) + 40, AY - 60], [1560, AY - 90]]), 'r', 8, 0.9, 5);
    const today = named('today', panel(B, 40, 736, 1520, 116, 'Today’s app'));
    const tl = typer(today, 70, 790, 'every minute: problems become issues, alerting until they clear · state recorded at the open and after the close · the day check after the 15:45 export · Mark as holiday mutes alerts until 07:00', 128, 'txt', 24);
    const hm = h => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60) % 60).padStart(2, '0')}`;
    return (t, rt) => {
      fade(ax, t, 0.3); fade(cur, t, 0.8); fade(frz, t, S.at[4] + 0.3);
      // the cursor walks the day, to each event in turn
      let h = 7;
      ev.forEach((e, i) => { const prev = i ? ev[i - 1][0] : 7; if (t >= S.at[i + 1]) h = lerp(prev, e[0], ke(t, S.at[i + 1], S.at[i + 1] + (S.to[i + 1] - S.at[i + 1]) * 0.5)); });
      if (t < S.at[1]) h = lerp(7, 8.2, ke(t, 0.8, S.to[0]));
      tr(cur, X(h), 0); txt(clk, hm(h));
      cards.forEach(({ g, tp, ld }, i) => {
        const a = S.at[i + 1], b = S.to[i + 1];
        pop(g, t, a + 0.2); reveal(ld, t, a + 0.3, a + 0.8); tp.go(t, a + 0.5, b - 0.3, rt);
        flash(g, t, a + 0.2, 0.8);
      });
      op(nmG, k(t, S.at[4] + 1.2, S.at[4] + 1.6));
      exits(rt, t > S.at[6] + 0.8 && t < S.at[7] ? 0 : 1e9);
      pop(today, t, S.at[9] + 0.2); tl.go(t, S.at[9] + 0.5, S.to[9] - 0.3, rt);
    };
  });
}

// 13. Watching it.
{
  const S = beats([
    ['How we watch all of this.', { focus: 'all' }],
    ['Every service sends its traces, metrics and logs through OpenTelemetry: traces to Tempo, metrics to Prometheus and Mimir, logs to Loki, and all of it shows on Grafana.', { spot: ['top'] }],
    ['One trace follows a tick all the way to a fill: ingest, the sequencer, the cache, the strategy’s batch and its decision, the intent, Heimdall, Kraken, the broker, and the fill coming back. So a slow trade shows exactly which hop was slow.', { spot: ['wf'] }],
    ['Dashboards show every SLO, per cell and per tenant. Tick to intent under 50 milliseconds, and intent to broker under 30, at the 99th percentile. No running deployment silent for more than 60 seconds. Exits drained by 15:20. Zero unreconciled positions at 15:35. End-of-day checks by 16:30. And 99.95 percent availability in market hours. All of them are targets.', { spot: ['slo'] }],
    ['And nothing is silent. Every failure the code handles becomes an issue, with a day attached, like today’s strategy issues and ops issues. Every failure it doesn’t handle is an alert.', { spot: ['wf'] }],
  ]);
  scene({ title: 'Watching it', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    named('top', bounds(G(B), 40, 120, 1520, 380));
    const sv = box(B, 40, 210, 300, 140, 'every service', { sub: 'traces · metrics · logs' });
    const ot = box(B, 420, 230, 300, 100, 'OpenTelemetry', { sub: 'collects all three', cls: 'proc' });
    const st = [['Tempo', 'traces'], ['Prometheus · Mimir', 'metrics'], ['Loki', 'logs']].map(([a, b], i) => db(B, 800, 124 + i * 128, 340, 110, a, b));
    const gf = box(B, 1220, 230, 340, 100, 'Grafana', { sub: 'dashboards per cell and tenant', cls: 'key' });
    const l1 = link(L, sv, ot, 'b'), ls = st.map(s => link(L, ot, s, 'b')), lg = st.map(s => link(L, s, gf, 'pu'));
    const fl = [stream(M, l1, 'b', 3, 1.2, 5), ...ls.map(l => stream(M, l, 'b', 2, 1.3, 5)), ...lg.map(l => stream(M, l, 'pu', 2, 1.5, 5))];
    // one trace, tick to fill
    const wf = named('wf', panel(B, 40, 520, 900, 330, 'One trace, tick to fill (illustrative)'));
    const spans = ['md-ingest', 'sequencer', 'cache agent', 'supervisor: batch', 'strategy: decides', 'intent log', 'Heimdall: risk, ledger', 'Kraken', 'broker ack', 'fill back'];
    const spG = G(wf);
    const sp = spans.map((s, i) => {
      const y = 556 + i * 28, x = 260 + i * 62;
      const lab = T(spG, 250, y + 16, s, 'bs', 'end');
      const r = E('rect', { x, y, width: 0, height: 20, rx: 3, class: i < 5 ? 'f-b' : 'f-g' }, spG);
      return { lab, r, x, w: [28, 18, 10, 30, 46, 22, 40, 34, 120, 60][i] };
    });
    const iss = G(wf);
    const i1 = box(iss, 70, 580, 840, 110, 'a failure the code handles', { sub: '→ an issue, with its day attached (today: strategy issues, ops issues)', cls: 'chip' });
    const i2 = box(iss, 70, 710, 840, 110, 'a failure it doesn’t handle', { sub: '→ an alert', cls: 'bad' });
    [i1, i2].forEach(b => op(b, 1)); op(iss, 0);
    // SLOs
    const slo = named('slo', panel(B, 960, 520, 600, 330, 'SLOs (targets)'));
    const sl = typeList(slo, 990, 574, ['tick → intent < 50 ms (p99, in a cell)', 'intent → broker < 30 ms (p99)', 'no running deployment silent > 60 s',
      'exits drained by 15:20', '0 unreconciled positions at 15:35', 'end-of-day checks by 16:30', '99.95% availability, market hours'], 52, 'lab', 26, 12);
    return (t, rt) => {
      pops([sv, ot, ...st, gf], t, 0.2, (S.to[0] + 1.6) / 7);
      show(l1, t, S.at[1] + 0.3); ls.forEach((l, i) => show(l, t, S.at[1] + 0.8 + i * 0.4)); lg.forEach((l, i) => show(l, t, S.at[1] + 2.2 + i * 0.4));
      fl.forEach((f, i) => f(rt, t > S.at[1] + 0.6 + i * 0.3 ? 0 : 1e9));
      pops([wf, slo], t, S.at[1] + 1.0, 0.3);
      const d2 = S.to[2] - S.at[2];
      sp.forEach(({ r, w }, i) => r.setAttribute('width', (w * ke(t, S.at[2] + 0.4 + i * d2 * 0.08, S.at[2] + 0.4 + (i + 0.9) * d2 * 0.08)).toFixed(1)));
      flash(wf, t, S.to[2] - 1.4, 1.0);
      sl.go(t, S.at[3] + 0.4, S.to[3] - 0.3, rt);
      const sw = k(t, S.at[4] + 0.1, S.at[4] + 0.5);
      sp.forEach(({ r, lab }) => { op(r, 1 - sw); op(lab, 1 - sw); });
      op(iss, sw); flash(i2, t, S.to[4] - 1.6, 1.0);
    };
  });
}

// 14. If a region fails, and who owns what.
{
  const S = beats([
    ['If a whole region fails, and who looks after what.', { focus: 'all' }],
    ['Production runs in Mumbai, across three zones. Hyderabad is a warm standby.', { spot: ['mum', 'hyd'] }],
    ['The ledger has a synchronous replica inside Mumbai, so a zone can fail without losing a money record. Its copy in Hyderabad is asynchronous.', { spot: ['mum', 'hyd'] }],
    ['The target for bringing back a whole region is 30 minutes.', { spot: ['mum', 'hyd'] }],
    ['Six teams own it. Market data: the feeds, computed streams and reference data. Runtime: the supervisor, sandboxes, the scheduler and the SDK. Money: orders and risk, the adapters, the ledger and the sim venue. Integrity: the day check, repair, shadow and reconciliation. Product: web, mobile, the product API and analytics. And platform: the clusters, CI and CD, observability and security.', { spot: ['teams'] }],
  ]);
  scene({ title: 'If a region fails, and who owns what', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const mum = named('mum', area(B, 40, 116, 960, 410, 'Mumbai', 'cell', 'ap-south-1 · primary'));
    const hyd = named('hyd', area(B, 1040, 116, 520, 410, 'Hyderabad', 'cell', 'ap-south-2 · warm standby'));
    const zs = ['zone a', 'zone b', 'zone c'].map((z, i) => box(B, 66 + i * 312, 176, 290, 330, z, { cls: 'chip', top: true, lcls: 'lab' }));
    const pri = db(B, 96, 246, 230, 120, 'ledger', 'primary');
    const rep = db(B, 408, 246, 230, 120, 'ledger', 'synchronous replica');
    const cel = box(B, 720, 246, 230, 120, 'cells', { sub: 'spread over zones', cls: 'chip' });
    const cpy = db(B, 1170, 246, 260, 120, 'ledger', 'asynchronous copy');
    const lsync = link(L, pri, rep, 'g');
    const lasync = link(L, rep, cpy, 'wt', { d: 'M638 330C800 430 1000 430 1164 330', dash: true });
    const fs = [stream(M, lsync, 'g', 2, 0.8, 5), stream(M, lasync, 'wt', 2, 2.2, 5)];
    const zf = typer(B, 96, 470, 'a zone fails: no money record lost', 40, 'okl');
    const rf = typer(B, 1070, 470, 'a region fails: back in 30 minutes (target)', 44, 'okl');
    const teams = named('teams', bounds(G(B), 40, 548, 1520, 304));
    const tm = [['Market data', 'feeds · computed streams · reference data · vendors'], ['Runtime', 'supervisor · sandboxes · scheduler · SDK'],
      ['Money', 'orders & risk · adapters · ledger · sim venue'], ['Integrity', 'day check · repair · shadow · reconciliation'],
      ['Product', 'web · mobile · product API · analytics'], ['Platform', 'clusters · CI/CD · observability · security']]
      .map(([a, b], i) => { const x = 40 + (i % 3) * 516, y = 552 + Math.floor(i / 3) * 154; const g = box(B, x, y, 488, 140, a, { cls: 'chip', top: true }); g.tp = typer(g, x + 244, y + 84, b, 50, 'txt', 22, 'middle'); return g; });
    const zap = puff(M, 211, 340);
    return (t, rt) => {
      pop(mum, t, 0.3); pop(hyd, t, 0.6);
      pops(zs, t, S.at[1] + 0.3, 0.4); pops([pri, cel, cpy], t, S.at[1] + 1.4, 0.4);
      pop(rep, t, S.at[2] + 0.3); show(lsync, t, S.at[2] + 0.8); show(lasync, t, S.at[2] + (S.to[2] - S.at[2]) * 0.65);
      fs[0](rt, t > S.at[2] + 1.2 ? 0 : 1e9); fs[1](rt, t > S.at[2] + (S.to[2] - S.at[2]) * 0.7 ? 0 : 1e9);
      zf.go(t, S.at[2] + 1.6, S.at[2] + (S.to[2] - S.at[2]) * 0.6, rt);
      rf.go(t, S.at[3] + 0.3, S.to[3] - 0.3, rt);
      // zone a goes down; the replica carries on
      zap.go(t, S.at[2] + 1.4);
      zs[0].classList.toggle('bad', t > S.at[2] + 1.4 && t < S.at[3]);
      flash(rep, t, S.at[2] + 1.6, 1.0);
      const per = (S.to[4] - S.at[4] - 0.8) / tm.length;
      tm.forEach((g, i) => { pop(g, t, S.at[4] + 0.3 + i * per); g.tp.go(t, S.at[4] + 0.5 + i * per, S.at[4] + 0.3 + (i + 1) * per, rt); });
    };
  });
}

// 15. How it grows.
{
  const steps = [['Write the adapter', 'in Go, against the broker contract', '1–2 weeks'], ['Add its symbol map', 'a loader in reference data', 'days'],
    ['Pass the conformance suite', 'recorded orders, rejects, partial fills, reconnects', 'in CI'], ['A paper week', 'the real API, with shadow orders; then a smoke test every morning', '1 week'],
    ['Turn it on', 'per tenant, with a feature flag', 'minutes']];
  const S = beats([
    ['Last: how Linck grows. A new broker touches only two places: the broker gateway, and reference data.', { focus: 'all' }],
    ['First, write the adapter, in Go, against the broker contract: one to two weeks, an estimate.', { spot: ['s0'] }],
    ['Add its symbol-map loader to reference data: days.', { spot: ['s1'] }],
    ['Pass the adapter conformance suite in CI: recorded order conversations, rejects, partial fills and reconnects.', { spot: ['s2'] }],
    ['Then a paper week through the broker’s real API, with shadow orders, and from then on a smoke test every morning before 09:00.', { spot: ['s3'] }],
    ['Then it is turned on per tenant, with a feature flag: minutes. The order: Kite today, then Upstox, Dhan, Angel One, Fyers, ICICI Direct, and then FIX, for institutions.', {}],
    ['A new data vendor is one more feed handler. Everything after it reads our own record, so nothing downstream changes.', { spot: ['ven'] }],
    ['And a new strategy language is a new SDK, a build image and a runtime image, and a pass of the conformance suite. Nothing else in the platform changes. The SDK video covers that part.', { spot: ['lang'] }],
  ]);
  scene({ title: 'How it grows: brokers, vendors, languages', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const two = panel(B, 940, 116, 620, 150, 'A new broker changes only');
    const tw = [box(two, 966, 168, 280, 76, 'broker gateway', { sub: 'Kraken: a new arm', cls: 'chip' }), box(two, 1262, 168, 276, 76, 'reference data', { sub: 'a symbol map', cls: 'chip' })];
    tw.forEach(b => op(b, 1));
    const ss = steps.map(([a, b, d], i) => {
      const y = 116 + i * 124, g = named('s' + i, box(B, 40, y, 860, 108, '', { cls: 'chip' }));
      E('circle', { cx: 86, cy: y + 54, r: 22, class: 'f-ink' }, g); T(g, 86, y + 61, String(i + 1), 'pt');
      T(g, 130, y + 44, a, 'cl', 'start');
      const tp = typer(g, 130, y + 76, b, 60, 'txt', 22);
      const tag = box(g, 730, y + 30, 150, 48, d, { cls: 'key', lcls: 'lab' }); op(tag, 1);
      return { g, tp };
    });
    const road = named('road', panel(B, 940, 290, 620, 210, 'The order'));
    const br = ['Kite (today)', 'Upstox', 'Dhan', 'Angel One', 'Fyers', 'ICICI Direct', 'FIX 4.4'].map((s, i) => box(road, 966 + (i % 4) * 146, 340 + Math.floor(i / 4) * 72, 134, 56, s, { cls: i ? 'chip' : 'key', lcls: 'cl' }));
    const ven = named('ven', panel(B, 940, 520, 620, 130, 'A new data vendor'));
    const vt = typer(ven, 966, 590, 'one more feed handler; everything after it reads our record', 54, 'cl', 24);
    const lang = named('lang', panel(B, 940, 670, 620, 180, 'A new strategy language'));
    const lt = typeList(lang, 966, 732, ['a new SDK', 'a build image and a runtime image', 'a pass of the conformance suite', 'nothing else in the platform changes'], 50, 'cl', 24, 8);
    return (t, rt) => {
      pop(two, t, 0.3); tw.forEach((b, i) => flash(b, t, 1.0 + i * 0.8, 0.8));
      ss.forEach(({ g, tp }, i) => { pop(g, t, S.at[i + 1] + 0.2); tp.go(t, S.at[i + 1] + 0.5, S.to[i + 1] - 0.3, rt); });
      pop(road, t, S.at[5] + 0.6);
      br.forEach((b, i) => pop(b, t, S.at[5] + 1.0 + i * (S.to[5] - S.at[5] - 1.6) / br.length));
      pop(ven, t, S.at[6] + 0.2); vt.go(t, S.at[6] + 0.5, S.to[6] - 0.3, rt);
      pop(lang, t, S.at[7] + 0.2); lt.go(t, S.at[7] + 0.5, S.to[7] - 0.3, rt);
    };
  });
}

// 16. Recap.
{
  const lines = [
    ['Today: one machine, one container, one script.', 'A release: CI, build, staging, the yesterday diff, a canary cell, then waves, after 15:45.'],
    ['Changes in steps, behind flags, by lease handoff.', 'Hello, Ready, Batch, Request, Reply, Done; the walk replays it exactly.'],
    ['Every part fails in a known way.', 'A day to the clock, 08:30 to 16:30; traced, measured, never silent.'],
  ];
  const S = beats([
    ['To recap. Today, Linck is one machine, one container and one script. In the new design, a release passes CI, the build, staging and the yesterday diff, then a canary cell and waves, after 15:45.', { focus: 'all' }],
    ['Changes go out in steps, behind flags, and by lease handoff, so trading doesn’t notice. A strategy talks in Hello, Ready, Batch, Request, Reply and Done, and the walk replays it exactly.', {}],
    ['Every part fails in a known way and recovers in a known way. The day runs to the clock, from 08:30 to 16:30. And everything is traced, measured against its targets, and never silent.', {}],
  ]);
  scene({ title: 'Running Linck, in short', dur: S.dur, steps: S.steps }, p => {
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
