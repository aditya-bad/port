// 1. The three parts, the one rule, the three nicknames.
{
  const S = beats([
    ['This video walks through the Linck architecture. First the final design: every service, what it is written in, and what it is responsible for. Then how things flow through it. Then the smaller first version we build on the way there, and what changes between the two.', { focus: 'all' }],
    ['Everything we write sits in one of three parts.'],
    ['LinckTrade does the trading. It takes in market data, runs the strategies, checks every order and sends it to a broker, and keeps the money records, called the ledger.', { spot: ['trade', 'exch', 'brokers'] }],
    ['LinckLens turns that data into what you look at: the screens, Geek mode charts, reports, and live P&L.', { spot: ['lens'] }],
    ['Linck Platform is shared by both: the front door every request comes through, accounts and logins, billing, and notifications.', { spot: ['platform', 'you'] }],
    ['One rule keeps the parts apart. Lens reads Trade’s data, but never writes it. No screen ever writes an order, a fill or a position.'],
    ['When you press Stop or Pause, Lens doesn’t change anything itself. It passes a command to Trade, and Trade decides.'],
    ['Three services get nicknames, because people will say them all day. Iceberg runs the strategies. Heimdall, the gatekeeper, checks and places every order. Kraken talks to the brokers, with one arm per broker. Every other service keeps its plain name.', { focus: ['trade'] }],
  ]);
  scene({ title: 'Linck, in three parts', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const ch = ['1 · The final architecture: every service', '2 · How things flow through it', '3 · The first version, and the way there'];
    const chT = ch.map((s, i) => T(B, 800, 380 + i * 70, '', 'lane'));
    const exch = named('exch', box(B, 40, 120, 300, 70, 'Exchanges', { sub: 'prices, via a vendor', cls: 'out' }));
    const brk = named('brokers', box(B, 540, 120, 320, 70, 'Brokers', { sub: 'Kite, Upstox, Dhan…', cls: 'out' }));
    const you = named('you', box(B, 1120, 120, 440, 70, 'You', { sub: 'web app · phone · CLI · SDK', cls: 'out' }));
    const trade = named('trade', area(B, 40, 250, 820, 600, 'LinckTrade', 'trade', 'the trading'));
    const plat = named('platform', area(B, 980, 250, 580, 230, 'Linck Platform', 'platform', 'shared'));
    const lens = named('lens', area(B, 980, 520, 580, 330, 'LinckLens', 'lens', 'what you see'));
    const list = (x, y, rows) => { const g = G(B); rows.forEach((r, i) => T(g, x, y + i * 36, '· ' + r, 'cl', 'start')); op(g, 0); return g; };
    const tl = list(70, 320, ['takes in market data from the exchanges', 'runs the strategies', 'checks every order and sends it to a broker', 'keeps the money records: the ledger', 'builds, schedules and checks strategies']);
    const pl = list(1010, 320, ['the front door: the edge gateway', 'accounts and logins', 'billing', 'notifications']);
    const ll = list(1010, 590, ['screens and the Desk', 'Geek mode charts and reports', 'live P&L to your screen', 'the assistant']);
    const nick = [['Iceberg', 'runs the strategies'], ['Heimdall', 'checks and places orders'], ['Kraken', 'talks to brokers']]
      .map(([n, s], i) => named('nick' + i, box(B, 64 + i * 262, 650, 248, 110, n, { sub: s, cls: 'key' })));
    const arms = G(B);                                     // Kraken's arms, one per broker
    for (let i = 0; i < 5; i++) E('path', { d: `M${600 + i * 22} 760q${-8 + i * 4} 30 ${-14 + i * 7} 52`, class: 'ln c-ink' }, arms);
    op(arms, 0);
    const lp = link(L, exch, trade, 'b', { sides: ['b', 't'], label: 'prices', lx: 36, ly: 4 });
    const lo = link(L, trade, brk, 'g', { d: 'M700 250V196', label: 'orders', lx: 40, ly: 4 });
    const ly = link(L, you, plat, 'wt', { sides: ['b', 't'], label: 'requests', lx: 50, ly: 4 });
    const lq = link(L, plat, lens, 'wt', { sides: ['b', 't'] });
    const lr = link(L, trade, lens, 'pu', { d: 'M864 610H974', label: 'reads', ly: -10 });
    const lw = link(L, lens, trade, 'r', { d: 'M976 700H866', dash: true, label: 'writes', ly: -10 });
    const lc = link(L, lens, trade, 'g', { d: 'M976 790H866', label: 'commands', ly: 26 });
    const no = check(M, 920, 700, false);
    const ro = stamp(M, 1290, 800, 'Read only', 'b', { shake: 4 });
    const cmd = packet(M, 'Stop', 'g');
    const fl = [stream(M, lp, 'b', 3, 1.6), stream(M, lo, 'g', 2, 1.4), stream(M, ly, 'wt', 2, 1.6), stream(M, lr, 'pu', 3, 1.2)];
    return (t, rt) => {
      ch.forEach((s, i) => { const a = 0.3 + i * 3.1; typeOn(chT[i], t, a, a + 2.6, s, rt); op(chT[i], 1 - k(t, S.at[1], S.at[1] + 0.6)); });
      pops([trade, plat, lens], t, S.at[1], 0.3);
      pops([exch, brk, you], t, S.at[1] + 0.5, 0.2);
      fade(tl, t, S.at[2] + 0.3); fade(pl, t, S.at[4] + 0.3); fade(ll, t, S.at[3] + 0.3);
      show(lp, t, S.at[2] + 0.8); show(lo, t, S.at[2] + 2.0);
      show(ly, t, S.at[4] + 0.6); show(lq, t, S.at[4] + 1.6);
      fl[0](rt, t > S.at[2] + 1.4 ? 0 : 1e9); fl[1](rt, t > S.at[2] + 2.6 ? 0 : 1e9); fl[2](rt, t > S.at[4] + 1.2 ? 0 : 1e9);
      show(lr, t, S.at[5] + 0.4); fl[3](rt, t > S.at[5] + 1.0 ? 0 : 1e9);
      show(lw, t, S.at[5] + 1.6); pop(no, t, S.at[5] + 2.2); ro.go(t, S.at[5] + 2.8);
      show(lc, t, S.at[6] + 0.3);
      travel(cmd, lc, t, S.at[6] + 1.0, S.at[6] + 3.0);
      nick.forEach((n, i) => pop(n, t, S.at[7] + 0.4 + i * 1.6));
      fade(arms, t, S.at[7] + 3.8, 0.4);
      if (t > S.at[7] + 3.8) arms.querySelectorAll('path').forEach((a, i) => look(a, { rot: Math.sin(rt * 2.4 + i) * 6 }));
    };
  });
}

// 2–4. Words first.
function cardGrid(p, list, y0 = 118) {
  const w = 488, gap = 20, out = [];
  for (let r = 0; r * 3 < list.length; r++) {
    const row = list.slice(r * 3, r * 3 + 3), h = Math.max(...row.map(([, m]) => termH(w, m)));
    row.forEach(([word, m], i) => out.push(named('c' + (r * 3 + i), term(p, 40 + i * (w + 18), y0, w, word, m, { h }))));
    y0 += h + gap;
  }
  return out;
}
function wordScene(title, intro, list) {
  const S = beats([[intro, { focus: 'all' }], ...list.map(([, , said], i) => [said, { spot: ['c' + i] }])]);
  scene({ title, dur: S.dur, steps: S.steps }, p => {
    const cards = cardGrid(G(p), list);
    return (t, rt) => {
      cards.forEach((c, i) => { pop(c, t, 0.2 + i * (S.to[0] - 0.6) / cards.length); if (t < S.at[i + 1]) op(c, Math.min(0.35, +c.getAttribute('opacity'))); });
      cards.forEach((c, i) => flash(c, t, S.at[i + 1], 0.8));
    };
  });
}
wordScene('Words: how software runs', 'Some words first, so that nothing later is vague. These are about how our software runs.', [
  ['Service (microservice)', 'A program that runs on its own, with one job and its own data. Others talk to it over the network and never read its tables.',
    'A service, or microservice, is a program that runs on its own, with one job and its own data. Other services talk to it over the network. They never read its tables.'],
  ['Module', 'A part inside a program. It ships and runs with the program, but keeps to its own tables, so it can be split out later.',
    'A module is a part inside a program. It ships and runs with the program, but keeps to its own tables, so it can be split out into a service later.'],
  ['Container image', 'A program packed with everything it needs to run (a Docker or OCI image). The same image runs on a laptop and in production.',
    'A container image is a program packed with everything it needs to run: a Docker, or O C I, image. The same image runs on a laptop and in production.'],
  ['Kubernetes · EKS', 'Kubernetes runs containers on a group of machines and restarts them when they die. EKS is Amazon’s managed Kubernetes.',
    'Kubernetes runs containers on a group of machines, called a cluster, and restarts them when they die. EKS is Amazon’s managed Kubernetes.'],
  ['Pod · workload kinds', 'A pod is one running copy. A Deployment keeps N identical copies; a StatefulSet gives each copy a fixed name and disk; a DaemonSet puts one on every machine; a Job runs once.',
    'A pod is one running copy of a container. A Deployment keeps a number of identical copies. A StatefulSet gives each copy a fixed name and its own disk. A DaemonSet puts exactly one copy on every machine. And a Job runs once, then finishes.'],
  ['Node · node pool', 'A node is one machine, a virtual server. A node pool is a group of identical machines kept for one kind of work.',
    'A node is one machine, a virtual server. A node pool is a group of identical machines, kept for one kind of work.'],
  ['Region · zone · cell', 'A region is an AWS location (Mumbai); a zone is a separate data centre in it. A cell is a full copy of the trading path for a group of accounts.',
    'A region is an AWS location, like Mumbai. A zone is a separate data centre inside it. A cell is a full copy of the trading path for one group of accounts, so a bad day stays inside one cell.'],
  ['Sandbox', 'A fence around a strategy’s code: it reads its events and sends back commands, and nothing else. No network, no files, no threads.',
    'A sandbox is a fence around a strategy’s code. Inside, it can read its events and send back commands, and nothing else: no network, no files, no threads.'],
]);
wordScene('Words: how the parts talk', 'Next, the words for how the parts talk to each other.', [
  ['REST · JSON', 'The plain web way to ask for something: an HTTP request, with the answer as JSON text. Browsers speak it, so screens use it.',
    'REST is the plain web way to ask for something: an H T T P request, with the answer written as JSON text. Browsers speak it, so the screens use it.'],
  ['SSE', 'Server-sent events: one long HTTP response that the server keeps adding to. Screens use it for live updates.',
    'S S E, server-sent events, is one long H T T P response that the server keeps adding to. Screens use it for live updates.'],
  ['gRPC · protobuf', 'gRPC is how our services call each other: a typed request and answer, or a stream. Protobuf is its compact binary message format.',
    'gRPC is how our own services call each other: a typed request and answer, or a stream of messages. Protobuf is its compact binary message format, with code generated for every language.'],
  ['mTLS', 'Mutual TLS: both ends of a connection prove who they are with certificates, and everything is encrypted. Linkerd adds it between services.',
    'mTLS, mutual T L S, means both ends of a connection prove who they are with certificates, and everything on it is encrypted. Linkerd adds it between our services.'],
  ['Event log · topic', 'An append-only list of messages. Writers add to the end; each reader keeps its own place and can read again. Redpanda keeps ours.',
    'An event log, or topic, is an append-only list of messages. Writers add to the end; each reader keeps its own place, and can go back and read again. Redpanda, which speaks Kafka’s protocol, keeps ours.'],
  ['NATS · JetStream', 'A fast messaging system. We use it for commands to cells and for live updates, where only the latest matters. JetStream is its mode that keeps messages.',
    'NATS is a fast messaging system. We use it for commands to the cells, and for live updates where only the latest value matters. JetStream is its mode that keeps messages until they are read.'],
  ['Conflation', 'Keeping only the newest value for a reader that has fallen behind, instead of a queue of old ones.',
    'Conflation means keeping only the newest value for a reader that has fallen behind, instead of a queue of old ones.'],
  ['Temporal workflow', 'A long job written as steps. If a machine dies halfway, it resumes at the step it stopped.',
    'A Temporal workflow is a long job written as steps. If a machine dies halfway through, it resumes at the step where it stopped.'],
]);
wordScene('Words: trading', 'And the trading words.', [
  ['Tick · instrument', 'A tick is one price update for one instrument: last price, best bid and ask, volume. An instrument is anything with a price: NIFTY, a future, one option.',
    'A tick is one price update for one instrument: its last price, best bid and ask, and volume. An instrument is anything with a price: the NIFTY index, a future, or one option.'],
  ['Strategy · deployment', 'A strategy is code that decides trades. A deployment is one running copy of it, with its settings, in paper, shadow or live.',
    'A strategy is code that decides trades. A deployment is one running copy of it, with its own settings, in paper, shadow or live mode.'],
  ['Paper · shadow · live', 'Paper: simulated fills only. Shadow: every order is also sent to the simulator, to compare. Live: real orders at a real broker.',
    'Paper means simulated fills only. Shadow means every order is also sent to the simulator, so the two can be compared. Live means real orders, at a real broker.'],
  ['Batch', 'The bundle of new events a strategy gets at once: latest prices, fills, timers. It answers one batch before it gets the next.',
    'A batch is the bundle of new events a strategy gets at once: the latest prices, its fills, its timers. It answers one batch before it gets the next.'],
  ['Intent · order · fill', 'An intent is what a strategy wants, like “buy 1 lot”. An order is an intent that passed the checks and went to a broker. A fill: it traded.',
    'An intent is what a strategy wants, like “buy one lot of this option”. An order is an intent that passed the risk checks and went to a broker. A fill is the broker saying it traded, in full or in part.'],
  ['Ledger', 'The record of money: every order, fill and charge, written once and never changed. Positions and cash are worked out from it.',
    'The ledger is the record of money: every order, fill and charge, written once and never changed. Positions and cash are worked out from it.'],
  ['Lease · epoch', 'A lease is a claim renewed every few seconds: this machine runs this deployment. The epoch counts its starts; orders from an older epoch are refused.',
    'A lease is a claim that must be renewed every few seconds: this machine runs this deployment. The epoch counts how many times a deployment has started; orders from an older epoch are refused.'],
  ['Snapshot · journal', 'A snapshot is a saved copy of a strategy’s state. The journal is every batch since. Together they rebuild the strategy exactly.',
    'A snapshot is a saved copy of a strategy’s state. The journal is every batch since that snapshot. Together, they rebuild the strategy exactly.'],
  ['Tenant', 'One customer of the platform: a person, or a partner broker with its own users. Every row of data carries its tenant.',
    'A tenant is one customer of the platform: a person, or a partner broker with its own users. Every row of data carries its tenant.'],
]);
