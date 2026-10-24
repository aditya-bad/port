// 1. What this video covers.
{
  const S = beats([
    ['The first video showed what Linck is made of. This one shows how it runs, in nine parts.', { focus: 'all' }],
    ['How it runs today. How a release ships, and where each thing lands. How it changes without stopping trading. A strategy’s life up close, and the walk that replays it. What happens when each part fails. A trading day, start to end. How we watch it. And how it grows.', {}],
    ['Every number comes with what it is. Measured means measured, in the prototype or in today’s app. Everything else is a design target or an estimate, and the video says which.', { spot: ['legend'] }],
  ]);
  const ch = ['Today: one machine, one script', 'A release, gate by gate, and where things land', 'Changing things without stopping trading',
    'A strategy’s life, close up', 'The walk: replaying a strategy exactly', 'Everything that can fail', 'A trading day, start to end',
    'Watching it', 'How it grows: brokers, vendors, languages'];
  scene({ title: 'Running Linck', dur: S.dur, steps: S.steps }, p => {
    const B = G(p), M = G(p);
    const rows = ch.map((s, i) => {
      const g = G(B);
      E('circle', { cx: 300, cy: 162 + i * 58, r: 17, class: 'f-ink' }, g); T(g, 300, 168 + i * 58, String(i + 1), 'pt');
      const tx = T(g, 340, 170 + i * 58, '', 'lane', 'start');
      op(g, 0);
      return { g, tx, s };
    });
    const legend = named('legend', bounds(G(B), 260, 708, 1080, 120));
    const lg = [['measured', 'g', 'in the prototype, or today’s app'], ['target', 'b', 'what the design aims for'], ['estimate', 'wt', 'a planning number']]
      .map(([w, c, s], i) => { const g = box(B, 270 + i * 360, 720, 340, 96, w, { sub: s, cls: 'chip' }); E('rect', { x: 270 + i * 360, y: 720, width: 340, height: 6, class: 'f-' + c }, g); return g; });
    const sp = sparkle(M, 440, 730, { r: 40 });
    return (t, rt) => {
      const per = (S.to[1] - S.at[1] - 0.4) / (ch.length - 1);
      rows.forEach((r, i) => {
        const a = i < 1 ? 0.4 : S.at[1] + 0.2 + (i - 1) * per;
        pop(r.g, t, i < 1 ? 0.3 : a);
        typeOn(r.tx, t, i < 1 ? 0.5 : a + 0.1, i < 1 ? S.to[0] - 0.2 : a + per * 0.9, r.s, rt);
      });
      pops(lg, t, S.at[2] + 0.3, 0.6);
      sp.go(t, S.at[2] + 1.0);
      lg.forEach((g, i) => flash(g, t, S.at[2] + 1.6 + i * 1.4, 1.0));
    };
  });
}

// 2. Today: one machine, one script.
{
  const S = beats([
    ['Before any of the new design, here is Linck as it runs today, on one machine.', { focus: 'all' }],
    ['Everything runs in one Docker container, as one Python process: the web app and its API, the Kite WebSocket, the tick broadcaster, a runner for each deployment, the loops that take snapshots, record prices and watch for dead runners, ops health, and the day check and repair workers.', { spot: ['cont'] }],
    ['Postgres runs in its own container beside it, on a Docker network. The app applies its own database migrations every time it starts.', { spot: ['pg'] }],
    ['Nobody reaches it from the internet. The app listens only on the machine’s own address, 127.0.0.1, port 8000, and Tailscale Serve is the way in, from the owner’s private network.', { spot: ['you', 'ts', 'cont'] }],
    ['A release is one command: redeploy.sh. It pulls the code, stops and removes the old container, builds the image, starts the new one, and reconnects it to the database network. On start, the migrations run and every active deployment resumes.', { spot: ['term', 'cont'] }],
    ['The Redeploy button in the app does the same thing. A container can’t rebuild itself, so the app writes a trigger file, and a watcher on the machine itself, a systemd service, runs the same script.', { spot: ['cont', 'ctrl', 'watch', 'term'] }],
    ['The rules for running it: the full test gate must print SAFE TO DEPLOY first. Deploy outside market hours. Mid-day only in a quiet window, never 09:15 to 10:15 or 15:00 to 15:30. And log in to Kite afterwards, if it is before 09:15.', { spot: ['term'] }],
    ['Three things in this shape are what the new design fixes first. The website and the strategies share one process. More than one place writes money: the fill path, repairs, and one-off fix scripts. And the tick broadcaster queues old ticks for a slow reader, which is step P0.', { focus: 'all' }],
  ]);
  scene({ title: 'Today: one machine, one script', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const you = named('you', box(B, 40, 132, 300, 96, 'You', { sub: 'a browser on your tailnet', cls: 'out' }));
    const ts = named('ts', box(B, 40, 318, 300, 96, 'Tailscale Serve', { sub: 'the only way in', cls: 'proc' }));
    const kite = named('kite', box(B, 40, 650, 300, 96, 'Kite', { sub: 'WebSocket ticks · REST orders', cls: 'out' }));
    const mach = named('mach', area(B, 400, 116, 1160, 744, 'One machine', 'cell', 'the deployment box'));
    const cont = named('cont', svc(B, 430, 166, 700, 446, 'live-deploy', { lang: 'Python', kind: 'Docker container', part: 'mixed',
      does: 'One Python process, FastAPI and uvicorn. Everything runs inside it.',
      comps: ['web app + API', 'Kite WebSocket', 'tick broadcaster', 'a runner per deployment', 'manager loops', 'ops health', 'day check + repair workers'], cols: 2, ch: 40 }));
    const port = T(cont, 452, 596, 'listens on 127.0.0.1:8000 · restarts unless stopped', 'bs', 'start');
    const c = cont.comp;
    const pg = named('pg', db(B, 1170, 166, 360, 150, 'Postgres', 'its own container, on the live_deploy_net network'));
    const files = box(B, 1170, 342, 360, 80, 'config.json · tokens.json', { sub: 'mounted read-only', cls: 'file', lcls: 'mono' });
    const ctrl = named('ctrl', box(B, 1170, 446, 360, 80, 'control/redeploy.trigger', { sub: 'written by the Redeploy button', cls: 'file', lcls: 'mono' }));
    const watch = named('watch', box(B, 1170, 560, 360, 96, 'redeploy_watcher.sh', { sub: 'a systemd service on the machine', cls: 'proc', lcls: 'mono' }));
    const term = named('term', panel(B, 430, 640, 700, 204, ''));
    const cmdG = G(B), rulG = G(B), fixG = G(B);
    T(cmdG, 452, 676, '$ ./redeploy.sh', 'mono', 'start');
    const cmds = typeList(cmdG, 452, 706, ['==> git pull', '==> stop and remove the old container', '==> docker build -t live-deploy .',
      '==> docker run … --restart unless-stopped', '==> reattach to live_deploy_net', 'on start: migrations, then active deployments resume'], 60, 'bs', 19, 3);
    T(rulG, 452, 676, 'Before you run it', 'fl', 'start');
    const rules = typeList(rulG, 452, 708, ['./run_tests.sh --e2e must print SAFE TO DEPLOY', 'outside market hours: after 15:30, or before 09:00',
      'mid-day only when quiet: never 09:15–10:15 or 15:00–15:30', 'then log in to Kite, if it is before 09:15'], 66, 'txt', 22, 8);
    T(fixG, 452, 676, 'What the new design fixes first', 'fl', 'start');
    const fixes = typeList(fixG, 452, 708, ['the website and the strategies share one process', 'more than one place writes money',
      'old ticks queue up for a slow reader (step P0)'], 60, 'warnl', 24, 12);
    [rulG, fixG].forEach(g => op(g, 0));
    const lYou = link(L, you, ts, 'b', { sides: ['b', 't'], label: 'HTTPS, private network', lx: 12, ly: 4, anchor: 'start' });
    const lTs = link(L, ts, cont, 'b', { d: 'M340 366C380 366 390 330 424 330', label: '→ 127.0.0.1:8000', at: 0, lx: -150, ly: 76 });
    const lKite = link(L, kite, cont, 'g', { d: 'M340 698C390 698 390 520 424 520', label: 'one WebSocket for ticks', at: 0, lx: -150, ly: 76 });
    const lPg = link(L, cont, pg, 'pu', { d: 'M1130 240H1164' });
    const lFiles = link(L, files, cont, 'ink', { d: 'M1170 382H1136', dash: true });
    const lCtrl = link(L, cont, ctrl, 'wt', { d: 'M1130 486H1164' });
    const lWatch = link(L, ctrl, watch, 'wt', { sides: ['b', 't'] });
    const lTerm = link(L, watch, term, 'wt', { d: 'M1170 640C1150 700 1160 740 1136 740', label: 'runs it', at: 0.9, lx: 18, ly: 4, anchor: 'start' });
    const req = packet(M, 'request', 'b'), trig = packet(M, 'Redeploy', 'wt');
    const ticks = stream(M, lKite, 'g', 3, 1.4, 5), qs = stream(M, lPg, 'pu', 2, 1.2, 5);
    const resumed = [c['a runner per deployment'], c['manager loops'], c['Kite WebSocket']];
    return (t, rt) => {
      pops([mach, cont, pg, files, ctrl, watch, term, you, ts, kite], t, 0.2, (S.to[0] - 0.8) / 10);
      show(lKite, t, 1.0); ticks(rt, t > 1.6 ? 0 : 1e9);
      show(lFiles, t, 1.2); show(lYou, t, 1.4); show(lTs, t, 1.6);
      walk(Object.values(c), t, S.at[1] + 0.4, S.to[1] - 0.2);
      show(lPg, t, S.at[2] + 0.3); qs(rt, t > S.at[2] + 0.8 ? 0 : 1e9); flash(pg, t, S.at[2] + 0.5, 1.2);
      flash(port, t, S.at[3] + 0.2, 0.8);
      chain(req, [lYou, lTs], t, S.at[3] + 0.8, (S.to[3] - S.at[3] - 1.4) / 2);
      // the script, line by line; the container comes back
      cmds.go(t, S.at[4] + 0.3, S.to[4] - 0.8, rt);
      const d4 = S.to[4] - S.at[4];
      if (t > S.at[4] + d4 * 0.25 && t < S.at[4] + d4 * 0.55) op(cont, 0.35);   // stopped, rebuilt, started
      walk(resumed, t, S.to[4] - 1.6, S.to[4] - 0.2);
      show(lCtrl, t, S.at[5] + 0.3); show(lWatch, t, S.at[5] + 0.9); show(lTerm, t, S.at[5] + 1.5);
      chain(trig, [lCtrl, lWatch, lTerm], t, S.at[5] + 1.0, (S.to[5] - S.at[5] - 1.6) / 3);
      op(cmdG, 1 - k(t, S.at[6], S.at[6] + 0.4));
      op(rulG, k(t, S.at[6] + 0.2, S.at[6] + 0.6) * (1 - k(t, S.at[7], S.at[7] + 0.4)));
      rules.go(t, S.at[6] + 0.5, S.to[6] - 0.3, rt);
      op(fixG, k(t, S.at[7] + 0.2, S.at[7] + 0.6));
      fixes.go(t, S.at[7] + 0.5, S.to[7] - 0.4, rt);
      const bad = [[c['web app + API'], c['a runner per deployment']], [c['day check + repair workers'], c['a runner per deployment']], [c['tick broadcaster']]];
      const per = (S.to[7] - S.at[7] - 0.9) / 3;
      Object.values(c).forEach(x => x.classList.remove('bad'));
      bad.forEach((xs, i) => { if (t > S.at[7] + 0.5 + i * per && t < S.at[7] + 0.5 + (i + 1) * per) xs.forEach(x => x.classList.add('bad')); });
    };
  });
}
