// 1. What the first version is, and why it comes first.
{
  const S = beats([
    ['This video is about the first version of Linck: the architecture we build first, starting from today’s app. The final architecture comes later. This is the step we take now.', { focus: 'all' }],
    ['Today, Linck is one Python program in one Docker container, on one machine. The website and the strategies share that program, and several places write money. The first version fixes those two risks first.', { spot: ['today'] }],
    ['It is built from Linck Platform’s three products. LinckTrade does the trading and keeps the money. LinckLens is what you see. The Linck SDK is what strategy developers use. And Linck core is shared by all three: the way in, logins, and notifications.', { spot: ['products'] }],
    ['It is sized for about two thousand strategies, five thousand instruments, and twenty thousand price updates a second at the open. Those are estimates, to be measured.', { spot: ['sizes'] }],
    ['And it is not tied to any provider. It runs wherever the owner chooses: AWS, Azure, Google Cloud, another cloud, or their own servers, even today’s machine.', { spot: ['clouds'] }],
  ]);
  scene({ title: 'Linck, first version', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    // today
    const today = named('today', box(B, 40, 116, 560, 420, '', { cls: 'frame' }));
    T(today, 64, 150, 'Today: one machine', 'fl', 'start');
    const proc = box(B, 70, 176, 500, 250, '', { cls: 'frame key' });
    T(proc, 92, 206, 'one Python program', 'cl', 'start');
    const inside = ['the website', 'the strategies', 'writes money: several places'].map((s, i) => box(B, 92, 226 + i * 62, 456, 50, s, { cls: i === 2 ? 'bad' : 'chip', lcls: 'cmp' }));
    const risk = stamp(M, 320, 480, 'Two risks', 'r', { rot: -4, shake: 3 });
    // the first version, by product
    const products = named('products', bounds(G(B), 640, 116, 920, 420));
    const trade = area(B, 640, 116, 920, 150, 'LinckTrade', 'trade', 'the trading, the money');
    const tc = ['market-data', 'runner', 'oms', 'broker-gw-kite', 'paper-broker', 'workers'].map((s, i) => box(B, 658 + i * 148, 166, 140, 44, s, { cls: 'chip', lcls: 'cmp' }));
    const lens = area(B, 640, 280, 450, 120, 'LinckLens', 'lens', 'what you see');
    const lc = ['live-gateway', 'core-api: screens'].map((s, i) => box(B, 658 + i * 212, 324, 204, 44, s, { cls: 'chip', lcls: 'cmp' }));
    const core = area(B, 1110, 280, 450, 120, 'Linck core', 'platform', 'shared');
    const cc = ['notifications', 'core-api: logins'].map((s, i) => box(B, 1128 + i * 212, 324, 204, 44, s, { cls: 'chip', lcls: 'cmp' }));
    const sdk = area(B, 640, 414, 920, 122, 'Linck SDK', 'sdk', 'for strategy developers');
    const sc = ['SDK v0 (Python)', 'the CLI: push, deploy, logs'].map((s, i) => box(B, 658 + i * 300, 458, 290, 44, s, { cls: 'chip', lcls: 'cmp' }));
    // sizes
    const sizes = named('sizes', bounds(G(B), 40, 560, 1520, 150));
    const nums = [[2000, 'strategies'], [5000, 'instruments'], [20000, 'updates a second at the open']].map(([n, s], i) => {
      const g = box(B, 40 + i * 512, 560, 496, 150, '', { cls: 'chip' });
      const v = T(g, 288 + i * 512, 636, '', 'big'); T(g, 288 + i * 512, 686, s, 'bs');
      return { g, v, n };
    });
    const est = T(B, 1556, 730, 'estimates, to be measured', 'bs', 'end'); op(est, 0);
    // providers
    const clouds = named('clouds', bounds(G(B), 40, 760, 1520, 80));
    const cl = ['AWS', 'Azure', 'Google Cloud', 'another cloud', 'your own servers', 'today’s machine'].map((s, i) => box(B, 40 + i * 256, 770, 240, 64, s, { cls: i < 4 ? 'out' : 'key', lcls: 'cl' }));
    const sp = sparkle(M, 1400, 760, { r: 50 });
    return (t, rt) => {
      pop(today, t, 0.3); pop(proc, t, 0.8); pops(inside, t, 1.2, 0.4);
      pops([trade, lens, core, sdk], t, 2.4, 0.3); pops([...tc, ...lc, ...cc, ...sc], t, 3.4, 0.12);
      walk(inside, t, S.at[1] + 0.4, S.to[1] - 1.6);
      risk.go(t, S.to[1] - 1.4);
      const d2 = S.to[2] - S.at[2];
      [[trade, tc], [lens, lc], [sdk, sc], [core, cc]].forEach(([a, cs], i) => { flash(a, t, S.at[2] + 0.3 + i * d2 / 4, d2 / 4 - 0.2); walk(cs, t, S.at[2] + 0.3 + i * d2 / 4, S.at[2] + (i + 1) * d2 / 4); });
      nums.forEach(({ g, v, n }, i) => { pop(g, t, S.at[3] + 0.2 + i * 0.4); countUp(v, t, S.at[3] + 0.5 + i * 0.6, S.at[3] + 2.2 + i * 0.6, 0, n, x => '~' + fmt(Math.round(x / 10) * 10)); });
      fade(est, t, S.to[3] - 1.6);
      pops(cl, t, S.at[4] + 0.3, Math.max(0.3, (S.to[4] - S.at[4] - 2) / 6));
      sp.go(t, S.to[4] - 1.0);
    };
  });
}

// 2. Words first.
wordScene('Words first', 'A few words first, so nothing later is vague.', [
  ['Site · home site', 'A site is one place Linck runs: a region of any cloud, or your own data centre. The home site is where the first version keeps its money and its one database.',
    'A site is one place Linck runs: a region of any cloud, or a data centre of your own. The home site is where the first version keeps the money, and its one database.'],
  ['Kubernetes · k3s · Compose', 'Kubernetes runs containers on a group of machines and restarts them when they die. k3s is a small Kubernetes for one server. Compose runs the same containers on one machine.',
    'Kubernetes runs containers on a group of machines, and restarts them when they die. Every cloud offers it, and k3s is a small Kubernetes for a single server. Docker Compose runs the same containers on one machine, without Kubernetes.'],
  ['Deployable', 'One program we build and ship on its own, as a container image. The first version has nine of them.',
    'A deployable is one program we build and ship on its own, as a container image. The first version has nine of them.'],
  ['Modular monolith', 'One program made of modules. Each module keeps its own tables and never reads another’s, so it can be moved out later.',
    'A modular monolith is one program made of modules. Each module keeps its own tables and never reads another’s, so it can be moved out into its own service later.'],
  ['NATS JetStream', 'A messaging system that keeps each message until it is read. It carries the first version’s events: intents, fills, commands, live updates.',
    'NATS JetStream is a messaging system that keeps each message until it is read. It carries the first version’s events: intents, fills, commands and live updates.'],
  ['Conflation', 'Keeping only the newest price for a reader that has fallen behind, instead of a queue of old ones.',
    'Conflation means keeping only the newest price for a reader that has fallen behind, instead of a queue of old ones.'],
  ['Lease', 'A claim, renewed every few seconds, that one runner worker runs one strategy. If the worker dies, the lease runs out and another takes over.',
    'A lease is a claim, renewed every few seconds, that one runner worker runs one strategy. If the worker dies, the lease runs out, and another worker takes over.'],
  ['Temporal workflow', 'A long job written as steps. If a machine dies halfway, it resumes at the step where it stopped.',
    'A Temporal workflow is a long job written as steps. If a machine dies halfway through, it resumes at the step where it stopped.'],
  ['Intent · fill', 'An intent is what a strategy wants, like “sell one lot”. A fill is the broker, or the simulator, saying it traded.',
    'An intent is what a strategy wants, like “sell one lot of this option”. A fill is the broker, or the simulator, saying it traded.'],
]);
