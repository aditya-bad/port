// 6. Where each part can run: one home site, and what may be anywhere.
{
  const S = beats([
    ['Now, where each part runs. The owner picks one home site: a region of any cloud, or a data centre of their own. Everything that writes to the one Postgres lives there.', { focus: 'all', spot: ['home'] }],
    ['That is the trading path: market-data, runner, the OMS, the Kite gateway and paper-broker. Then core-api, the day check and repair workers, and the stores.', { spot: ['home'] }],
    ['Why one place? Core-api is still one program, and it shares the one Postgres with the OMS. A database is never shared between two sites, so everything that writes to it stays together.', { spot: ['home'] }],
    ['Some parts can run anywhere from day one. The live gateway and the web app’s files can sit close to your users. The gateway hears the home site’s events through a NATS leaf node, and checks logins with keys it keeps a copy of.', { spot: ['o0'] }],
    ['Notifications can run anywhere too. It only reads events, through its own leaf node.', { spot: ['o1'] }],
    ['Backtests run on their own Temporal queue, so they can use spare machines anywhere. They read the lake, which can be any object storage, and send their results back over gRPC.', { spot: ['o2'] }],
    ['A standby site, wherever the owner chooses, keeps a copy of everything: a Postgres replica over TLS, a mirror of the JetStream events, and a copy of the lake.', { spot: ['o3'] }],
    ['And the SDK and the command-line tool run on a developer’s laptop, talking to the home site over gRPC with TLS.', { spot: ['o4'] }],
    ['If a link to another site drops, the home site keeps trading. Screens elsewhere show how old their data is, and catch up when the link comes back.', { spot: ['home', 'o0'] }],
    ['LinckLens gets a site of its own later, when the product API moves out of core-api. Then it reads its own copy of the data, and sends its writes as commands. That is a later step, not day one.', { spot: ['later'] }],
  ]);
  scene({ title: 'Where each part can run', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const home = named('home', area(B, 40, 116, 820, 590, 'Home site', 'cell', 'any cloud, or your own servers'));
    const tp = box(B, 60, 166, 780, 176, '', { cls: 'frame key' }); T(tp, 80, 192, 'the trading path · LinckTrade', 'bs', 'start');
    const tpc = ['market-data', 'runner', 'oms', 'broker-gw-kite', 'paper-broker'].map((s, i) => { const b = box(B, 76 + i * 152, 210, 144, 52, s, { cls: 'chip', lcls: 'cmp' }); if (s === 'oms') named('oms', b); return b; });
    const kite = T(tp, 80, 316, 'Kite’s registered static IP = this site’s own outgoing address', 'bs', 'start');
    const core = named('core', box(B, 60, 360, 380, 96, 'core-api', { sub: 'screens · deployments · logins', cls: 'chip' }));
    const wk = box(B, 460, 360, 380, 96, 'workers', { sub: 'day check · repair · replay', cls: 'chip' });
    const stores = [['Postgres', 'the one database'], ['NATS', 'JetStream ×3'], ['Redis', ''], ['ClickHouse', ''], ['Temporal', '']]
      .map(([n, s], i) => { const d = db(B, 60 + i * 156, 480, 144, 110, n, s); if (i === 0) named('pg', d); return d; });
    const never = box(B, 60, 610, 780, 80, '', { cls: 'bad' });
    const nv = typer(never, 450, 657, 'one database, never shared between sites: it stays here', 70, 'cl', 22, 'middle');
    // the other places
    const OT = [
      ['Near your users', 'live-gateway · web app files', 'NATS leaf'],
      ['Any site', 'notifications', 'NATS leaf'],
      ['Spare machines', 'backtest workers · the lake', 'Temporal'],
      ['Standby site', 'Postgres replica · event mirror · lake copy', 'replicas'],
      ['A laptop', 'Linck SDK · the CLI', 'gRPC · TLS'],
    ];
    const others = OT.map(([n, s], i) => named('o' + i, box(B, 1010, 116 + i * 120, 550, 104, n, { sub: s, cls: 'out' })));
    const links = OT.map(([, , via], i) => {
      const y = 168 + i * 120, l = line(L, `M${i === 4 ? 1006 : 864} ${y}${i === 4 ? `H864` : 'H1006'}`, i === 3 ? 'pu' : i === 4 ? 'b' : 'g', {});
      l._lab = T(L, 935, y - 12, via, 'hop'); op(l._lab, 0);
      return l;
    });
    const fl = links.map((l, i) => stream(M, l, i === 3 ? 'pu' : i === 4 ? 'b' : 'g', 2, 1.6));
    // a link drops
    const cut = boom(M, 935, 168);
    const age = bubble(M, 935, 182, 'stale: 40 s', { below: true });
    const still = bubble(M, 600, 166, 'still trading', {});
    // later: Lens on its own site
    const later = named('later', box(B, 40, 724, 1520, 136, '', { cls: 'frame' }));
    const lt = typeList(later, 70, 768, ['Later: LinckLens on its own site, when the product API leaves core-api.', 'It reads its own copy of the data, and sends its writes to the home site as commands.'], 120, 'cl', 30, 10);
    return (t, rt) => {
      pop(home, t, 0.3); pop(tp, t, S.at[1] + 0.2); pops(tpc, t, S.at[1] + 0.5, 0.35);
      fade(kite, t, S.at[1] + 2.4); pop(core, t, S.at[1] + 2.6); pop(wk, t, S.at[1] + 3.0); pops(stores, t, S.at[1] + 3.4, 0.25);
      if (t > S.at[2]) { flash(core, t, S.at[2] + 0.3, 1.2); flash(stores[0], t, S.at[2] + 1.2, 1.2); flash(tpc[2], t, S.at[2] + 2.0, 1.2); }
      pop(never, t, S.at[2] + 2.6); nv.go(t, S.at[2] + 3.0, S.to[2] - 0.4, rt);
      others.forEach((o, i) => pop(o, t, S.at[i + 3] + 0.2));
      const down = t > S.at[8] + 0.6 && t < S.to[8] - 1.4;
      links.forEach((l, i) => { show(l, t, S.at[i + 3] + 0.8); if (t > S.at[8] && i === 0) op(l, down ? 0.25 : 1); fl[i](rt, t > S.at[i + 3] + 1.2 && !(i === 0 && down) ? 0 : 1e9); });
      if (down) pop(cut, t, S.at[8] + 0.6); else op(cut, 0);
      age.go(t, S.at[8] + 1.2, S.to[8] - 1.4, rt); still.go(t, S.at[8] + 2.0, S.to[8] - 1.4, rt);
      pop(later, t, S.at[9] + 0.2); lt.go(t, S.at[9] + 0.5, S.to[9] - 0.5, rt);
    };
  });
}
