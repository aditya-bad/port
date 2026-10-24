// 6. Changing things without stopping trading.
{
  const S = beats([
    ['Linck keeps changing while it trades. Six rules keep trading from noticing.', { focus: 'all' }],
    ['Database changes go in three steps, over at least two releases: expand, adding the new column or table; migrate, filling it in; and contract, removing the old one, a release later. So every release runs against both the old schema and the new one.', { spot: ['dbp'] }],
    ['New behaviour hides behind feature flags, with OpenFeature, per tenant and per cell. It reaches staff first, then one cell, then everyone.', { spot: ['flagp'] }],
    ['A strategy moves between machines by lease handoff, at a minute boundary. It never sees a restart, only its next batch, on a new machine.', { spot: ['movep'] }],
    ['Moves never happen from 09:10 to 09:20, or from 15:10 to 15:25: the open and the exits. And when Kubernetes drains a machine, its supervisor hands its leases away first.', { spot: ['movep'] }],
    ['A strategy version is never upgraded under a running deployment. A new version is a new pin, and the user chooses it.', { spot: ['verp'] }],
    ['And the runtime keeps every protocol version it ever shipped for at least twelve months, so old strategies keep running on new supervisors.', { spot: ['verp'] }],
  ]);
  scene({ title: 'Changing things without stopping trading', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    // the database change
    const dbp = named('dbp', panel(B, 40, 116, 760, 336, 'A database change'));
    const tbl = G(B);
    box(tbl, 80, 170, 300, 44, 'positions', { cls: 'chip', lcls: 'mono' });
    const oldC = box(tbl, 80, 222, 146, 200, 'old', { cls: 'chip', lcls: 'lab', top: true });
    const newC = box(tbl, 234, 222, 146, 200, 'new', { cls: 'art', lcls: 'lab', top: true });
    [tbl, ...tbl.children].forEach(g => op(g, 1)); op(tbl, 0); op(newC, 0);
    const rows = [0, 1, 2, 3].map(i => { const r = E('rect', { x: 246, y: 268 + i * 36, width: 122, height: 24, rx: 4, class: 'f-g' }, tbl); op(r, 0); return r; });
    const oldRows = [0, 1, 2, 3].map(i => E('rect', { x: 92, y: 268 + i * 36, width: 122, height: 24, rx: 4, class: 'f-ink', opacity: 0.25 }, oldC));
    const ph = typeList(dbp, 420, 196, ['expand: add the new column, write both', 'migrate: fill it in', 'contract: drop the old one, a release later', 'each release runs on the old and the new schema'], 34, 'cl', 24, 20);
    const gone = puff(M, 153, 320);
    // feature flags
    const flagp = named('flagp', panel(B, 820, 116, 740, 336, 'Feature flags · OpenFeature'));
    const ringG = G(B);
    const rings = [[60, 'staff'], [110, 'one cell'], [150, 'everyone']].map(([r, s], i) => {
      const g = G(ringG); E('circle', { cx: 1040, cy: 296, r, class: 'f-wt', opacity: 0.0 }, g); E('circle', { cx: 1040, cy: 296, r, class: 'ln c-wt' }, g);
      T(g, 1040 + r * 0.72 + 4, 296 - r * 0.72 - 4, s, 'lab', 'start'); op(g, 0); return g;
    });
    const flagT = typeList(flagp, 1240, 220, ['per tenant', 'per cell', 'staff → a cell → everyone'], 24, 'cl', 24, 18);
    // moving a strategy
    const movep = named('movep', panel(B, 40, 470, 1520, 230, 'Moving a strategy'));
    const hA = box(B, 70, 514, 400, 150, 'machine A', { sub: '', cls: 'chip', top: true });
    const hB = box(B, 1130, 514, 400, 150, 'machine B', { sub: '', cls: 'chip', top: true });
    const tok = [0, 1, 2].map(i => { const g = G(M); E('circle', { cx: 0, cy: 0, r: 22, class: i ? 'f-ink' : 'f-g' }, g); T(g, 0, 6, 'S', 'pt'); op(g, 0); return g; });
    const hops = [0, 1, 2].map(i => route(M, [[200 + i * 90, 616], [500, 560], [800, 548], [1100, 560], [1240 + i * 90, 616]]));
    const hop = hops[0];
    const clk = T(B, 800, 530, '', 'big');
    const lease = T(B, 800, 576, '', 'bs');
    const day = G(B), dx = h => 520 + (h - 9) * (560 / 6.6);              // 09:00 → 15:36
    E('line', { x1: 520, y1: 648, x2: 1080, y2: 648, class: 'axis' }, day);
    [[9 + 10 / 60, 9 + 20 / 60], [15 + 10 / 60, 15 + 25 / 60]].forEach(([a, b]) => E('rect', { x: dx(a), y: 636, width: dx(b) - dx(a), height: 24, class: 'f-r', opacity: 0.45 }, day));
    [['09:10', 9 + 10 / 60, 'end'], ['09:20', 9 + 20 / 60, 'start'], ['15:10', 15 + 10 / 60, 'end'], ['15:25', 15 + 25 / 60, 'start']].forEach(([s, h, a]) => T(day, dx(h) + (a === 'end' ? -4 : 4), 682, s, 'bs', a));
    T(day, 800, 626, 'no moves', 'warnl'); op(day, 0);
    const drain = T(hA, 270, 600, '', 'warnl'); op(drain, 0);
    // versions
    const verp = named('verp', panel(B, 40, 716, 1520, 136, ''));
    const dep = box(B, 70, 744, 420, 80, 'deployment · pinned to v3', { cls: 'key', lcls: 'cl' });
    const v4 = box(B, 560, 744, 300, 80, 'v4 published', { cls: 'chip', lcls: 'cl' });
    const noL = link(L, v4, dep, 'r', { dash: true }), noUp = check(M, 525, 784, false);
    const choose = typer(verp, 900, 790, 'a new pin, when the user chooses it', 40, 'cl');
    const pro = typer(verp, 900, 790, 'runtime protocol versions: every one kept ≥ 12 months', 60, 'cl');
    return (t, rt) => {
      pops([dbp, flagp, movep, verp], t, 0.2, 0.4);
      // database: expand, migrate, contract
      const d1 = S.to[1] - S.at[1];
      fade(tbl, t, S.at[1] + 0.2);
      pop(newC, t, S.at[1] + d1 * 0.12);
      rows.forEach((r, i) => op(r, k(t, S.at[1] + d1 * (0.36 + i * 0.06), S.at[1] + d1 * (0.4 + i * 0.06))));
      op(oldC, 1 - k(t, S.at[1] + d1 * 0.66, S.at[1] + d1 * 0.72));
      gone.go(t, S.at[1] + d1 * 0.66);
      ph.go(t, S.at[1] + 0.3, S.to[1] - 0.4, rt);
      // flags: the rings fill outward
      const d2 = S.to[2] - S.at[2];
      rings.forEach((g, i) => { pop(g, t, S.at[2] + 0.2 + i * 0.3); op(g.firstChild, 0.35 * k(t, S.at[2] + d2 * (0.35 + i * 0.18), S.at[2] + d2 * (0.45 + i * 0.18))); });
      flagT.go(t, S.at[2] + 0.4, S.to[2] - 0.3, rt);
      // a move at a minute boundary
      pops([hA, hB], t, S.at[3] + 0.2, 0.2);
      const d3 = S.to[3] - S.at[3], m0 = S.at[3] + d3 * 0.35, m1 = S.at[3] + d3 * 0.75;
      if (t < S.at[4]) {
        if (t < m0) { tr(tok[0], 200, 610); op(tok[0], k(t, S.at[3] + 0.4, S.at[3] + 0.8)); } else travel(tok[0], hop, t, m0, m1, true);
        txt(clk, t > S.at[3] + 0.4 ? (t < m0 + 0.2 ? '10:41:59' : '10:42:00') : '');
        txt(lease, t > m0 ? 'lease handed to B · next batch on B' : (t > S.at[3] + 0.4 ? 'lease held by A' : ''));
        tok.slice(1).forEach(g => op(g, 0)); op(drain, 0);
      } else {
        // the open and the exits: no moves; a drained machine hands its leases away first
        txt(clk, ''); txt(lease, '');
        fade(day, t, S.at[4] + 0.2);
        const d4 = S.to[4] - S.at[4], a = S.at[4] + d4 * 0.55;
        typeAt(drain, t, a, a + 1.0, 'draining', rt); op(drain, drain.textContent ? 1 : 0);
        tok.forEach((g, i) => { const s = a + 0.8 + i * 0.5; if (t < s) { tr(g, 200 + i * 90, 616); op(g, 1); } else travel(g, hops[i], t, s, s + 1.4, true); });
      }
      // versions
      pop(dep, t, S.at[5] + 0.3); pop(v4, t, S.at[5] + 1.2);
      if (t < S.at[6]) { choose.go(t, S.at[5] + 1.8, S.to[5] - 0.3, rt); pro.go(t, 1e9, 1e9); }
      else { choose.go(t, 1e9, 1e9); pro.go(t, S.at[6] + 0.3, S.to[6] - 0.3, rt); }
      show(noL, t, S.at[5] + 1.6); pop(noUp, t, S.at[5] + 2.0);
    };
  });
}
