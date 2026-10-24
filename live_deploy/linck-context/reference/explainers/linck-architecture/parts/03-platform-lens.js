// The four quarters a service scene uses.
const Q = [[40, 116], [812, 116], [40, 500], [812, 500]];
const QW = 748, QH = 360;

// 8. Linck Platform.
{
  const S = beats([
    ['Linck Platform: four services that both other parts lean on.', { focus: 'all' }],
    ['The edge gateway is the only door in. It is Envoy, an off-the-shelf proxy, plus an authentication plug-in we write in Go. It runs as a Deployment of three or more copies, scaled by the number of requests.', { focus: ['edge'], spot: ['edge'] }],
    ['A request arrives over HTTPS on port 443, and Envoy ends the encryption. The plug-in checks who you are, from a session cookie, an API key or a login token, and counts the request against your plan’s rate limit in Redis.', { spot: ['edge'] }],
    ['Then it routes: REST requests to the product API, live streams to the live gateway, and gRPC from the SDK and the CLI to control. For the public API, it also offers every gRPC method as REST.', { spot: ['edge'] }],
    ['Identity, in Go, owns users, tenants, API keys and roles, in its own Postgres database, and the login sessions, which it keeps in Redis. It is a security boundary, so it is kept small and audited on its own.', { focus: ['identity'], spot: ['identity'] }],
    ['Commerce, in Go, is money coming in: plans, metering of what each tenant uses, invoices, and later marketplace licences and revenue share. Its own database, its own team.', { focus: ['commerce'], spot: ['commerce'] }],
    ['Notifications, a Go consumer, reads events off the logs and sends them out: push, email, Telegram, WhatsApp and webhooks, with templates per tenant. It runs on its own, so a slow email provider can never back up anything else.', { focus: ['notif'], spot: ['notif'] }],
  ]);
  scene({ title: 'Linck Platform, inside', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const edge = named('edge', svc(B, ...Q[0], QW, QH, 'edge-gateway', { lang: 'Envoy + Go', kind: 'Deployment · 3+', part: 'platform',
      does: 'The only door in: every request from outside passes through it.', comps: ['TLS on port 443', 'auth plug-in (Go)', 'plan rate limits', 'router', 'REST → gRPC', 'access log'], cols: 3 }));
    const outs = [['product-api', 'REST'], ['live-gateway', 'SSE'], ['control', 'gRPC']].map(([n, s], i) => box(edge, 300 + i * 160, 300, 150, 56, n, { sub: s, cls: 'out', lcls: 'cmp' }));
    outs.forEach(o => op(o, 1));
    const redis = db(edge, 74, 290, 160, 70, 'Redis', '');
    op(redis, 1);
    const req = dot(M, 'wt', 8);
    const c = edge.comp;
    const reqPath = route(M, [[40, 220], mid(c['TLS on port 443']), mid(c['auth plug-in (Go)']), mid(c['plan rate limits']), [c['router'].cx, c['router'].cy]]);
    const toOut = outs.map(o => route(M, [mid(c['router']), [o.cx, o.Y]]));
    const id = named('identity', svc(B, ...Q[1], QW, QH, 'identity', { lang: 'Go', kind: 'Deployment · 3', part: 'platform',
      does: 'Who you are and what you may do. A security boundary, audited on its own.', comps: ['users', 'tenants', 'API keys', 'roles', 'sessions', 'login'], cols: 3 }));
    const idDb = db(id, 860, 300, 300, 70, 'identity DB · Postgres', ''), sesDb = db(id, 1200, 300, 300, 70, 'sessions · Redis', '');
    [idDb, sesDb].forEach(d => op(d, 1));
    const cm = named('commerce', svc(B, ...Q[2], QW, QH, 'commerce', { lang: 'Go', kind: 'Deployment · 2', part: 'platform',
      does: 'Money in: what each tenant pays for, and what it used.', comps: ['plans', 'metering', 'invoices', 'marketplace licences', 'revenue share'], cols: 3 }));
    const cmDb = db(cm, 88, 690, 300, 70, 'commerce DB · Postgres', ''); op(cmDb, 1);
    const nt = named('notif', svc(B, ...Q[3], QW, QH, 'notifications', { lang: 'Go', kind: 'consumer · 2', part: 'platform',
      does: 'Tells you when something happens, on the channel you chose.', comps: ['event reader', 'templates per tenant'], cols: 2 }));
    const ch = ['push', 'email', 'Telegram', 'WhatsApp', 'webhooks'].map((n, i) => box(nt, 830 + i * 144, 760, 134, 46, n, { cls: 'out', lcls: 'cmp' }));
    ch.forEach(o => op(o, 1));
    const evIn = topic(nt, 830, 690, 300, 'orders.event.v1 …', 'b', { h: 34, lcls: 'tlab' }); op(evIn, 1);
    const nc = nt.comp;
    const fan = ch.map(o => route(M, [[1130, 707], mid(nc['event reader']), mid(nc['templates per tenant']), [o.cx, o.Y]]));
    const fanDots = fan.map(f => stream(M, f, 'wt', 1, 2.6, 6));
    const all = [edge, id, cm, nt];
    return (t, rt) => {
      pops(all, t, 0.2, (S.to[0] - 0.6) / 4);
      // a request walks through the edge, again and again while it's explained
      if (t > S.at[2] + 0.3) {
        const ph = ((rt * 0.4) % 1), seg = Math.floor(((rt * 0.4) / 1) % 3);
        if (ph < 0.6) { op(req, 1); placeOn(req, reqPath, ph / 0.6); }
        else if (t > S.at[3]) { op(req, 1); placeOn(req, toOut[seg], (ph - 0.6) / 0.4); }
        else op(req, 0);
      } else op(req, 0);
      if (t > S.at[2] + 0.3 && t < S.at[3]) walk(['TLS on port 443', 'auth plug-in (Go)', 'plan rate limits'].map(n => c[n]), t, S.at[2] + 0.3, S.to[2]);
      walk(['users', 'tenants', 'API keys', 'roles', 'sessions'].map(n => id.comp[n]), t, S.at[4] + 0.4, S.to[4]);
      walk(['plans', 'metering', 'invoices', 'marketplace licences', 'revenue share'].map(n => cm.comp[n]), t, S.at[5] + 0.4, S.to[5]);
      fanDots.forEach((f, i) => f(rt, t > S.at[6] + 0.4 + i * 0.2 ? 0 : 1e9));
    };
  });
}

// 9. LinckLens.
{
  const S = beats([
    ['LinckLens: four services that turn Trade’s data into what you see. None of them can write an order, a fill or a position.', { focus: 'all' }],
    ['The product API, in Python, serves everything the screens read and write: the Desk, reports, Geek mode data, journal notes, repair cases and preferences. It is today’s API, kept, running three to twenty copies, scaled by CPU.', { focus: ['pa'], spot: ['pa'] }],
    ['It reads charts and reports from ClickHouse, positions from a read-only replica of the ledger, and deployment details from control over gRPC. The only tables it writes are its own, in the product database.', { spot: ['pa'] }],
    ['A Stop or Pause button becomes a gRPC command to control. Trade does the rest.', { spot: ['pa'] }],
    ['The live gateway, in Go, holds up to a hundred thousand open screens. It listens on NATS for live P&L, positions and alerts, and keeps one latest value per screen, sent at most every 250 milliseconds over SSE. A slow phone gets the newest number, never a backlog.', { focus: ['lg'], spot: ['lg'] }],
    ['Read models, a Go consumer, reads the order, ledger and decision logs from Redpanda, and builds the ClickHouse tables behind Geek mode, reports and leaderboards. If a table is ever wrong, it is rebuilt by reading the logs again.', { focus: ['rm'], spot: ['rm'] }],
    ['The assistant, in Python, writes SDK code and explains a trading day. Its tools are read-only, and its model calls have their own cost limits.', { focus: ['as'], spot: ['as'] }],
  ]);
  scene({ title: 'LinckLens, inside', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const pa = named('pa', svc(B, ...Q[0], QW, QH, 'product-api', { lang: 'Python', kind: 'Deployment · 3–20', part: 'lens',
      does: 'Everything the screens read and write. Owns the product database.', comps: ['Desk & book', 'reports', 'Geek mode data', 'journal notes', 'repair cases', 'preferences'], cols: 3 }));
    const src = [['ClickHouse', 'charts, reports'], ['ledger replica', 'read-only'], ['product DB', 'its own tables']].map(([n, s], i) => db(pa, 70 + i * 176, 286, 160, 80, n, s));
    src.forEach(d => op(d, 1));
    const ctl = box(pa, 600, 286, 168, 80, 'control', { sub: 'gRPC', cls: 'out' }); op(ctl, 1);
    const stop = packet(M, 'Stop', 'g');
    const stopPath = route(M, [[300, 214], [560, 250], [ctl.cx, ctl.Y - 4]]);
    const reads = [0, 1].map(i => route(M, [[src[i].cx, src[i].Y], mid(pa.comp[i ? 'Desk & book' : 'Geek mode data'])]));
    const writes = route(M, [mid(pa.comp['journal notes']), [src[2].cx, src[2].Y]]);
    const rd = [stream(M, reads[0], 'pu', 2, 1.8, 6), stream(M, reads[1], 'pu', 2, 2.2, 6), stream(M, writes, 'pu', 1, 2.0, 6)];
    const lg = named('lg', svc(B, ...Q[1], QW, QH, 'live-gateway', { lang: 'Go', kind: 'Deployment · 3–10', part: 'lens',
      does: 'Up to 100,000 open screens: live P&L, positions, alerts. Latest values only.', comps: ['NATS subscriber', 'latest slot per screen', 'SSE writer, ≤ 4 a second'], cols: 3 }));
    const nats = topic(lg, 832, 260, 300, 'live.<tenant>.<dep>', 'pu', { h: 34, lcls: 'tlab' }); op(nats, 1);
    const slot = box(lg, 1160, 256, 180, 42, '', { cls: 'chip key', lcls: 'sv' }); op(slot, 1);
    const slotT = T(lg, 1250, 284, '', 'sv');
    const fast = box(lg, 860, 340, 250, 90, 'laptop', { top: true, cls: 'out' }); op(fast, 1);
    const slow = box(lg, 1180, 340, 360, 90, 'slow phone', { top: true, cls: 'out' }); op(slow, 1);
    const fastT = T(lg, 985, 415, '', 'sv'), slowT = T(lg, 1360, 415, '', 'sv');
    const sweat_ = sweat(lg, 1530, 350);
    const pnl = r => 1240 + Math.round(Math.sin(r * 1.7) * 380 + Math.sin(r * 4.3) * 90);
    const live = stream(M, nats.pipe, 'pu', 4, 1.0, 5);
    const rm = named('rm', svc(B, ...Q[2], QW, QH, 'read-models', { lang: 'Go', kind: 'consumer · 2–4', part: 'lens',
      does: 'Turns events into tables for charts. Can be rebuilt from the logs at any time.', comps: ['Redpanda reader', 'projectors', 'ClickHouse writer'], cols: 3 }));
    const logs = ['orders.event.v1', 'ledger.journal.v1', 'strategy.decision.v1'].map((n, i) => topic(rm, 60, 670 + i * 44, 330, n, 'b', { h: 34, lcls: 'tlab' }));
    logs.forEach(x => op(x, 1));
    const cog = gear(rm, 470, 740, 22);
    const chDb = db(rm, 560, 680, 200, 120, 'ClickHouse', 'Geek, reports, leaderboards'); op(chDb, 1);
    const toCh = route(M, [[390, 740], [448, 740], [492, 740], [556, 740]]);
    const lf = [...logs.map((x, i) => stream(M, x.pipe, 'b', 2, 1.6 + i * 0.3, 5)), stream(M, toCh, 'pu', 2, 1.4, 6)];
    const as = named('as', svc(B, ...Q[3], QW, QH, 'assistant', { lang: 'Python', kind: 'Deployment · 2', part: 'lens',
      does: 'Writes SDK code and explains a trading day. Read-only tools; its own cost limits.', comps: ['code writer', 'day explainer', 'read-only tools', 'cost limits'], cols: 4 }));
    const code = T(as, 834, 720, '', 'mono', 'start'), code2 = T(as, 834, 752, '', 'mono', 'start');
    const all = [pa, lg, rm, as];
    return (t, rt) => {
      pops(all, t, 0.2, (S.to[0] - 0.6) / 4);
      rd.forEach((f, i) => f(rt, t > S.at[2] + 0.3 + i * 0.6 ? 0 : 1e9));
      travel(stop, stopPath, t, S.at[3] + 0.2, S.to[3] - 0.2, true);
      if (t > S.to[3] - 0.2) op(stop, 0);
      live(rt, t > S.at[4] + 0.3 ? 0 : 1e9);
      const on = t > S.at[4] + 0.3;
      txt(slotT, on ? `₹ ${fmt(pnl(rt))}` : '');
      txt(fastT, on ? `₹ ${fmt(pnl(Math.floor(rt * 4) / 4))}` : '');
      txt(slowT, on ? `₹ ${fmt(pnl(Math.floor(rt / 1.5) * 1.5))}  newest` : '');
      sweat_.go(rt, on && t < S.to[4]);
      lf.forEach(f => f(rt, t > S.at[5] + 0.3 ? 0 : 1e9));
      spin(cog, t > S.at[5] + 0.3 ? rt : 0); op(cog, t > 0.6 ? 1 : 0);
      typeOn(code, t, S.at[6] + 0.3, S.at[6] + (S.to[6] - S.at[6]) * 0.5, 'await ctx.orders.basket([hedge.buy(), short.sell()],', rt);
      typeOn(code2, t, S.at[6] + (S.to[6] - S.at[6]) * 0.5, S.to[6] - 0.2, '    policy="hedge_first", tag="entry")', rt);
    };
  });
}
