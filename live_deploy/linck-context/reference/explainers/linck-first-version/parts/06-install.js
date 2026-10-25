// 7. Installing a site, and checking it before it trades.
{
  const FILE = [
    'site: home',
    'where: own servers        # or any cloud',
    'runs: [core-api, md, runner, oms,',
    '       broker-gw-kite, sim, workers]',
    'stores:',
    '  postgres: self-run      # or a managed one',
    '  lake: s3://linck-lake   # any object store',
    'other_sites:',
    '  users: live-gateway, notifications',
    '  standby: replica, mirror',
  ];
  const S = beats([
    ['Each site is installed from one site file. It says where the site is, which parts run there, which stores it uses, and where the other sites are.', { focus: 'all', spot: ['file'] }],
    ['The same images and settings turn into one of two things: a Helm chart for any Kubernetes, k3s on a single server included, or a Compose file for one machine.', { spot: ['file', 'helm', 'compose'] }],
    ['Before the home site trades, a site check proves it. Each store answers with the version and the features we need. Mutual TLS works to every other site. The broker sees the outgoing address we expect. And the delay to Kite and to the data feed is measured.', { spot: ['check'] }],
    ['Kite only accepts orders from registered static addresses. In the first version, that is the home site’s own outgoing address: a provider’s gateway with a reserved IP, or your server’s public IP. Moving the home site starts with registering the new one.', { spot: ['ip', 'kite'] }],
    ['And releases follow the trading day. Nothing ships between nine in the morning and three thirty-five, except an approved fix. Releases go out after three forty-five, and each one first replays yesterday, to show nothing changed that shouldn’t.', { spot: ['day'] }],
  ]);
  scene({ title: 'Installing a site', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const file = named('file', codeBlock(B, 40, 116, 640, FILE, 30, 'site.yaml'));
    const helm = named('helm', box(B, 760, 130, 380, 110, 'Helm chart', { sub: 'any Kubernetes · k3s on one server', cls: 'key' }));
    const comp = named('compose', box(B, 1180, 130, 380, 110, 'Compose file', { sub: 'one machine', cls: 'key' }));
    const lh = link(L, file, helm, 'b', { d: 'M684 185H754' }), lc = link(L, file, comp, 'b', { d: 'M684 215C900 300 1300 290 1370 246' });
    const gear1 = gear(M, 950, 280, 16), gear2 = gear(M, 1370, 280, 16);
    const ckBox = named('check', box(B, 760, 300, 800, 300, '', { cls: 'frame' }));
    T(ckBox, 784, 332, 'The site check, before it trades', 'fl', 'start');
    const CK = ['stores: versions and features', 'mutual TLS to every other site', 'the outgoing IP the broker sees', 'delay to Kite and to the feed: measured'];
    const cks = CK.map((s, i) => { const tx = T(B, 830, 384 + i * 52, '', 'cl', 'start'); const c = check_(M, 800, 378 + i * 52); return { tx, c, s }; });
    const ip = named('ip', box(B, 40, 496, 640, 110, 'Kite: registered static IP', { sub: 'the home site’s own outgoing address', cls: 'store' }));
    const kite = named('kite', box(B, 40, 652, 300, 60, 'Kite', { cls: 'out', lcls: 'cl' }));
    const li = link(L, ip, kite, 'g', { sides: ['b', 't'], d: 'M190 606V646' });
    const ord = packet(M, 'order', 'g');
    // the trading day
    const day = named('day', box(B, 360, 640, 1200, 220, '', { cls: 'frame' }));
    const x0 = 420, x1 = 1520, tx = h => x0 + (h - 8) / 9 * (x1 - x0);
    const band = G(B);
    E('rect', { x: tx(9), y: 700, width: tx(15 + 35 / 60) - tx(9), height: 50, class: 'f-r', opacity: 0.85 }, band);
    E('rect', { x: tx(15.75), y: 700, width: tx(17) - tx(15.75), height: 50, class: 'f-g', opacity: 0.85 }, band);
    E('line', { x1: x0, x2: x1, y1: 770, y2: 770, class: 'axis' }, band);
    for (let h = 8; h <= 17; h++) T(band, tx(h), 800, `${String(h).padStart(2, '0')}:00`, 'bs');
    T(band, (tx(9) + tx(15.58)) / 2, 732, 'no releases 09:00–15:35', 'pt');
    T(band, (tx(15.75) + x1) / 2, 690, 'releases', 'okl');
    T(band, 400, 840, 'each release first replays yesterday', 'bs', 'start');
    op(band, 0);
    const hand = E('line', { y1: 690, y2: 780, class: 'xline' }, B); op(hand, 0);
    function check_(p, x, y) { return check(p, x, y, true); }
    return (t, rt) => {
      pop(file, t, 0.2); file.type(t, 0.6, S.to[0] - 0.4, rt);
      pop(helm, t, S.at[1] + 0.4); show(lh, t, S.at[1] + 0.2); pop(comp, t, S.at[1] + 1.6); show(lc, t, S.at[1] + 1.2);
      if (t > S.at[1]) { spin(gear1, rt); spin(gear2, rt); op(gear1, t < S.to[1] ? 1 : 0); op(gear2, t < S.to[1] ? 1 : 0); } else { op(gear1, 0); op(gear2, 0); }
      pop(ckBox, t, S.at[2] + 0.2);
      const per = (S.to[2] - S.at[2] - 0.8) / CK.length;
      cks.forEach(({ tx: e, c, s }, i) => { const a = S.at[2] + 0.5 + i * per; typeAt(e, t, a, a + per * 0.7, s, rt); pop(c, t, a + per * 0.8); });
      pop(ip, t, S.at[3] + 0.2); pop(kite, t, S.at[3] + 0.6); show(li, t, S.at[3] + 1.0);
      if (t > S.at[3] + 1.4) { const f = ((rt * 0.5) % 1); placeOn(ord, li, f); op(ord, Math.min(1, f * 6, (1 - f) * 6)); } else op(ord, 0);
      pop(day, t, S.at[4] + 0.2); fade(band, t, S.at[4] + 0.6);
      op(hand, k(t, S.at[4] + 1.0, S.at[4] + 1.4));
      const h = lerp(8.3, 16.8, k(t, S.at[4] + 1.0, S.to[4] - 0.4));
      hand.setAttribute('x1', tx(h)); hand.setAttribute('x2', tx(h));
    };
  });
}
