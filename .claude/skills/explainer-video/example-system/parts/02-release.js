// 3. A release, gate by gate.
{
  const S = beats([
    ['Once there is more than one machine, this is how a change reaches production. Every stage is a gate, and a release that fails one goes no further.', { focus: 'all' }],
    ['It starts as a commit. CI runs the linters and the unit tests; the contract check, which refuses any breaking change to a version-one protobuf message; the fault matrix, today’s 108 cases, grown with every incident; and the determinism check: a day replayed twice, with equal decision hashes.', { spot: ['g1', 'pnl'] }],
    ['Then the build: an image for each service, built reproducibly and distroless, with a software bill of materials, an SBOM, made by Syft, and a signature, made with cosign. The clusters run only signed images.', { spot: ['g2', 'pnl'] }],
    ['Staging is a full cell, fed a recorded market at real speed, all day, every day, so the open and the 15:15 exits are rehearsed every hour.', { spot: ['g3', 'pnl'] }],
    ['Then the yesterday diff. Production’s previous day is replayed through the new build. If any decision comes out different, the release stops, until a person marks the difference as intended.', { spot: ['g3', 'g4', 'pnl'] }],
    ['Only then does it reach production: a canary cell first, then waves of cells, then all of them, and only after 15:45. Between 09:00 and 15:35 nothing goes out, except an approved hotfix.', { spot: ['g4', 'g5', 'g6', 'g7', 'pnl'] }],
    ['There are three environments. Every pull request gets a dev namespace with seeded data: today’s browser-test server, grown up. Staging is that one recorded cell. And production is cells in Mumbai, with disaster recovery in Hyderabad.', { focus: 'all' }],
  ]);
  scene({ title: 'A release, gate by gate', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const spec = [['Commit', 'a pull request', 40, 124], ['CI', 'tests · contract · faults · hashes', 430, 124], ['Build', 'images · SBOM · signature', 820, 124],
      ['Staging', 'a cell on a recorded market', 1210, 124], ['Yesterday diff', 'yesterday, replayed through it', 1210, 290], ['Canary cell', 'one cell first', 820, 290],
      ['Waves', 'a few cells at a time', 430, 290], ['All cells', 'after 15:45 only', 40, 290]];
    const gs = spec.map(([l, s, x, y], i) => named('g' + i, box(B, x, y, 350, 110, l, { sub: s, cls: i === 4 ? 'key' : '' })));
    const ls = gs.slice(1).map((g, i) => link(L, gs[i], g, 'b', i === 3 ? { sides: ['b', 't'] } : {}));
    const pnl = named('pnl', panel(B, 40, 446, 1520, 404, ''));
    const part = [...Array(7)].map(() => { const g = G(B); op(g, 0); return g; });
    const head = (g, s) => T(g, 70, 482, s, 'fl', 'start');
    // CI
    head(part[1], 'CI: every pull request');
    const ci = typeList(part[1], 110, 532, ['lint and unit tests', 'contract check: no breaking change to a v1 protobuf message (Buf)',
      'fault matrix: today’s 108 cases, grown with every incident', 'determinism: one day replayed twice, decision hashes equal'], 90, 'lab', 30, 22);
    const ciOk = [0, 1, 2, 3].map(i => check(part[1], 80, 526 + i * 52, true));
    // build
    head(part[2], 'Build');
    const bd = typeList(part[2], 80, 532, ['an image per service: built reproducibly, distroless (no shell)', 'an SBOM: every library inside, made by Syft',
      'a signature, made with cosign', 'the clusters admit only signed images'], 70, 'lab', 30, 22);
    const sealed = stamp(M, 1320, 640, 'Signed', 'g', { rot: -6 });
    // staging
    head(part[3], 'Staging: a recorded market, at real speed');
    const st = typeList(part[3], 80, 532, ['a full cell, all day, every day', 'the open and the 15:15 exits, rehearsed every hour'], 60, 'lab', 30, 22);
    const bar = G(part[3]); E('rect', { x: 760, y: 560, width: 720, height: 14, rx: 7, class: 'mini' }, bar);
    ['09:15', '15:15', '15:30'].forEach((s, i) => { const x = 760 + [0, 600, 720][i]; E('line', { x1: x, y1: 548, x2: x, y2: 586, class: 'axis' }, bar); T(bar, x, 612, s, 'bs'); });
    const cur = G(part[3]); E('rect', { x: -4, y: 540, width: 8, height: 54, rx: 3, class: 'f-b' }, cur);
    const rep = T(part[3], 1120, 700, '', 'bs');
    // yesterday diff
    head(part[4], 'Yesterday diff: the same day, two builds');
    T(part[4], 110, 524, 'production, yesterday', 'fl', 'start'); T(part[4], 830, 524, 'the new build', 'fl', 'start');
    const dec = [['09:20 entry · sell two legs', '09:20 entry · sell two legs'], ['10:42 adjustment · no action', '10:42 adjustment · no action'],
      ['13:05 roll · up 100 points', '13:05 roll · up 100 points'], ['14:30 exit · stop hit', '14:30 no action']];
    const dl = dec.map(([a, b], i) => [typer(part[4], 110, 566 + i * 40, a, 40, 'mono'), typer(part[4], 830, 566 + i * 40, b, 40, i === 3 ? 'warnl' : 'mono')]);
    const diffHl = E('rect', { x: 90, y: 680, width: 1420, height: 34, rx: 6, class: 'hl' }, part[4]); op(diffHl, 0);
    const held = stamp(M, 1330, 790, 'Held', 'r');
    const marked = typer(part[4], 110, 790, 'until a person marks it intended', 40, 'cl');
    // production
    head(part[5], 'Production, by the clock');
    const ax = G(part[5]), x0 = 120, px = 1320 / 9;                        // 08:00 → 17:00
    const X = h => x0 + (h - 8) * px;
    E('line', { x1: x0, y1: 640, x2: x0 + 1320, y2: 640, class: 'axis' }, ax);
    for (let h = 8; h <= 17; h++) { E('line', { x1: X(h), y1: 632, x2: X(h), y2: 648, class: 'axis' }, ax); T(ax, X(h), 676, `${String(h).padStart(2, '0')}:00`, 'bs'); }
    const frz = E('rect', { x: X(9), y: 600, width: X(15 + 35 / 60) - X(9), height: 30, class: 'f-r', opacity: 0.25 }, part[5]);
    T(part[5], (X(9) + X(15.58)) / 2, 588, 'no releases, except an approved hotfix', 'warnl');
    const go = E('rect', { x: X(15.75), y: 600, width: X(17) - X(15.75), height: 30, class: 'f-g', opacity: 0.3 }, part[5]);
    T(part[5], X(15.75) + 6, 588, 'from 15:45: release', 'okl', 'start');
    [frz, go].forEach(r => op(r, 0));
    const wave = [0, 1, 2].map(i => box(part[5], 120 + i * 470, 730, 420, 80, ['canary: one cell', 'waves: a few cells at a time', 'then every cell'][i], { cls: 'chip' }));
    // environments
    head(part[6], 'Three environments');
    const env = [['Dev', 'a namespace per pull request, seeded data'], ['Staging', 'one cell on a recorded market'], ['Production', 'cells in Mumbai · DR in Hyderabad']]
      .map(([a, b], i) => { const g = box(part[6], 80 + i * 490, 540, 450, 150, a); g.sub = typer(g, 80 + i * 490 + 225, 640, b, 46, 'bs', 18, 'middle'); return g; });
    const pk = packet(M, 'release', 'b');
    return (t, rt) => {
      pops(gs, t, 0.2, (S.to[0] - 1) / gs.length);
      ls.forEach((l, i) => show(l, t, 0.6 + i * (S.to[0] - 1.2) / ls.length));
      pop(pnl, t, S.to[0] - 0.8);
      part.forEach((g, i) => { if (i) op(g, k(t, S.at[i] + 0.1, S.at[i] + 0.5) * (i < 6 ? 1 - k(t, S.at[i + 1] - 0.1, S.at[i + 1] + 0.3) : 1)); });
      // the release moves through the gates
      const legs = [[], [0], [1], [2], [3], [4, 5, 6]];
      if (t < S.at[1] + 0.3) { tr(pk, gs[0].cx, gs[0].B + 4); op(pk, k(t, 1.0, 1.4)); }
      for (let i = 1; i <= 5; i++) {
        const a = S.at[i] + 0.3, b = i === 5 ? S.to[5] - 0.6 : a + 1.6;
        if (t >= a && t < (i < 5 ? S.at[i + 1] + 0.3 : 1e9)) {
          if (t <= b) chain(pk, legs[i].map(j => ls[j]), t, a, (b - a) / legs[i].length);
          else { const g = gs[i < 5 ? i : 7]; tr(pk, g.cx, g.B + 4); op(pk, 1); }
        }
      }
      if (t > S.at[6]) op(pk, 1 - k(t, S.at[6], S.at[6] + 0.4));
      flash(gs[1], t, S.at[1] + 1.9, 0.8); flash(gs[2], t, S.at[2] + 1.9, 0.8); flash(gs[3], t, S.at[3] + 1.9, 0.8);
      // CI
      ci.go(t, S.at[1] + 0.6, S.to[1] - 0.6, rt);
      ciOk.forEach((c, i) => pop(c, t, S.at[1] + 0.6 + (i + 1) * (S.to[1] - S.at[1] - 1.2) / 4));
      // build
      bd.go(t, S.at[2] + 0.5, S.to[2] - 1.4, rt);
      if (t < S.at[3]) sealed.go(t, S.to[2] - 1.2); else op(sealed, 0);
      // staging: a replayed day, looping
      st.go(t, S.at[3] + 0.4, S.at[3] + (S.to[3] - S.at[3]) * 0.6, rt);
      const f = ((rt * 0.22) % 1);
      tr(cur, 760 + f * 720, 0);
      txt(rep, t > S.at[3] + 0.6 ? `replaying ${['09', '10', '11', '12', '13', '14', '15'][Math.min(6, Math.floor(f * 7))]}:${String(Math.floor((f * 7 % 1) * 60)).padStart(2, '0')}` : '');
      // yesterday diff
      const d4 = S.to[4] - S.at[4];
      dl.forEach(([a, b], i) => { const s0 = S.at[4] + 0.4 + i * d4 * 0.14; a.go(t, s0, s0 + d4 * 0.12, rt); b.go(t, s0 + 0.2, s0 + 0.2 + d4 * 0.12, rt); });
      op(diffHl, k(t, S.at[4] + d4 * 0.62, S.at[4] + d4 * 0.66));
      if (t < S.at[5]) held.go(t, S.at[4] + d4 * 0.68); else op(held, 0);
      marked.go(t, S.at[4] + d4 * 0.74, S.to[4] - 0.3, rt);
      // production
      const d5 = S.to[5] - S.at[5];
      op(go, k(t, S.at[5] + d5 * 0.2, S.at[5] + d5 * 0.3)); op(frz, k(t, S.at[5] + d5 * 0.6, S.at[5] + d5 * 0.7));
      wave.forEach((w, i) => pop(w, t, S.at[5] + d5 * (0.25 + i * 0.12)));
      // environments
      pops(env, t, S.at[6] + 0.6, (S.to[6] - S.at[6] - 1.2) / 3);
      env.forEach((e, i) => { const a = S.at[6] + 0.9 + i * (S.to[6] - S.at[6] - 1.2) / 3; flash(e, t, a, 1.0); e.sub.go(t, a + 0.2, a + (S.to[6] - S.at[6] - 1.2) / 3, rt); });
    };
  });
}

// 4. What ships where.
tableScene('What ships where', 'Not everything ships the same way. Here is each kind of thing: what it is packaged as, where it goes, and how it reaches production.',
  [['', 60, 20], ['packaged as', 330, 30], ['goes to', 690, 30], ['how it reaches production', 1050, 46]], [
    ['Our services', 'container images, signed', 'our image registry, ECR', 'Argo CD, cell by cell, after the gates',
      'Our services ship as signed container images, kept in ECR, Amazon’s image registry. Argo CD rolls them out cell by cell, after the gates.'],
    ['Users’ strategies', 'signed artifacts: source, lockfile, manifest. Not images', 'the artifact registry', 'pinned to a deployment; the supervisor fetches that exact version',
      'A user’s strategy is not an image. It is a signed artifact, its source, lockfile and manifest, in the artifact registry. It is pinned to a deployment, and the supervisor fetches that exact version.'],
    ['Runtime images', 'one pinned image per language version', 'ECR, on every runtime machine', 'rolled out like a service; a strategy keeps the one it was certified on',
      'The runtime images that strategies run in, one per language version, roll out like a service, but each strategy keeps the image it was certified on.'],
    ['SDKs', 'packages', 'PyPI, the Go module proxy, crates.io, npm', 'semver and release notes; the conformance suite must pass',
      'The SDKs are packages, on PyPI, the Go module proxy, crates.io and npm, with semantic versions, and none ships unless the conformance suite passes.'],
    ['CLI', 'one binary per operating system', 'GitHub releases, Homebrew', 'updates itself; refuses versions older than the platform supports',
      'The CLI is one binary per operating system, from GitHub releases and Homebrew. It updates itself, and refuses to run a version older than the platform supports.'],
    ['Web app', 'static files', 'S3, behind a CDN', 'an atomic switch; the old version stays for instant rollback',
      'The web app is static files on S3, behind a CDN. A release is an atomic switch, and the old version stays, for an instant rollback.'],
    ['Mobile apps', 'app bundles', 'Play Store, App Store', 'staged rollout; new features behind flags',
      'And the mobile apps go through the Play Store and the App Store, in staged rollouts, with new features behind flags.'],
  ]);

// 5. One cluster, then many; one repository.
{
  const S = beats([
    ['Where releases land, and where the code lives.', { focus: 'all' }],
    ['In the first version: a Helm chart for each service, and Argo CD makes the one EKS cluster match what is in Git. On a laptop, Docker Compose runs the same services.', { spot: ['first'] }],
    ['In the final version: the same charts, with Argo CD ApplicationSets rolling out cell by cell, and Terraform building the AWS accounts, the networks and the clusters themselves.', { spot: ['final'] }],
    ['About fifteen AWS accounts, an estimate: one for each cell, and one each for control, data and security. A broker partner’s cells can sit in an account of their own.', { spot: ['final'] }],
    ['All the code lives in one repository. contracts, with Buf. services, the Go services. engine, the Rust workspace, built with Cargo. python, the product API and the assistant, with uv. sdks, the four SDKs. web. And deploy: Terraform, Helm and Argo CD.', { spot: ['repo'] }],
    ['Each language keeps its own tools, with one task runner, just, on top. And CI filters by path, so a change to the web app doesn’t rebuild any Rust.', { spot: ['repo'] }],
  ]);
  scene({ title: 'One cluster, then many; one repository', dur: S.dur, steps: S.steps }, p => {
    const L = G(p), B = G(p), M = G(p);
    const first = named('first', panel(B, 40, 120, 740, 340, 'First version'));
    const fin = named('final', panel(B, 820, 120, 740, 340, 'Final'));
    const fl = typeList(first, 70, 186, ['a Helm chart for each service', 'Argo CD makes the one EKS cluster match Git', 'Docker Compose runs it on a laptop'], 60, 'cl', 26, 22);
    const cl1 = box(first, 520, 300, 220, 120, 'one EKS cluster', { sub: 'Mumbai, 3 zones', cls: 'chip' });
    const ff = typeList(fin, 850, 186, ['the same charts', 'Argo CD ApplicationSets: cell by cell', 'Terraform: accounts, networks, clusters'], 46, 'cl', 26, 22);
    const acc = G(fin);
    const accs = ['cell 1', 'cell 2', 'cell 3', '…', 'control', 'data', 'security', 'partner'].map((s, i) => box(acc, 850 + (i % 4) * 170, 320 + Math.floor(i / 4) * 64, 156, 52, s, { cls: 'chip', lcls: 'lab' }));
    const accT = T(fin, 1530, 452, '≈ 15 AWS accounts (estimate)', 'bs', 'end');
    const repo = named('repo', panel(B, 40, 486, 1520, 364, 'One repository'));
    const dirs = [['contracts/', 'Buf'], ['services/', 'Go: control, oms, broker-gw…'], ['engine/', 'Cargo: Rust'], ['python/', 'uv: product API, assistant'],
      ['sdks/', 'python · go · rust · ts'], ['web/', 'the web app'], ['deploy/', 'Terraform · Helm · Argo CD']];
    const ds = dirs.map(([a, b], i) => box(repo, 70 + (i % 4) * 365, 530 + Math.floor(i / 4) * 110, 345, 92, a, { sub: b, lcls: 'mono' }));
    const just = box(repo, 70 + 3 * 365, 640, 345, 92, 'just', { sub: 'one task runner on top', cls: 'key', lcls: 'mono' });
    const pipes = ['web build', 'Rust build', 'Go build', 'Python build'].map((s, i) => box(repo, 70 + i * 365, 770, 345, 56, s, { cls: 'chip', lcls: 'lab' }));
    const chg = packet(M, 'change in web/', 'pu');
    const chgPath = route(M, [[800, 506], [ds[5].cx, ds[5].cy + 4]]);
    const skip = T(repo, 800, 845, '', 'bs');
    return (t, rt) => {
      pops([first, fin, repo], t, 0.2, 0.4);
      fl.go(t, S.at[1] + 0.3, S.to[1] - 1.2, rt); pop(cl1, t, S.to[1] - 1.0);
      ff.go(t, S.at[2] + 0.3, S.to[2] - 0.3, rt);
      accs.forEach((a, i) => { pop(a, t, S.at[3] + 0.3 + i * (S.to[3] - S.at[3] - 1.2) / accs.length); });
      fade(accT, t, S.to[3] - 0.8);
      ds.forEach((d, i) => pop(d, t, S.at[4] + 0.4 + i * (S.to[4] - S.at[4] - 1.0) / ds.length));
      ds.forEach((d, i) => flash(d, t, S.at[4] + 0.6 + i * (S.to[4] - S.at[4] - 1.0) / ds.length, 0.9));
      pop(just, t, S.at[5] + 0.4);
      pops(pipes, t, S.at[5] + 1.0, 0.2);
      const d5 = S.to[5] - S.at[5];
      travel(chg, chgPath, t, S.at[5] + d5 * 0.35, S.at[5] + d5 * 0.55, false);
      if (t > S.at[5] + d5 * 0.55) { flash(ds[5], t, S.at[5] + d5 * 0.55, 0.8); pipes.forEach((x, i) => { x.classList.toggle('hot', i === 0 && t > S.at[5] + d5 * 0.6); x.classList.toggle('off', i > 0 && t > S.at[5] + d5 * 0.6); }); }
      else pipes.forEach(x => x.classList.remove('hot', 'off'));
      typeAt(skip, t, S.at[5] + d5 * 0.65, S.to[5] - 0.2, 'only the web build runs; no Rust rebuilt', rt); op(skip, skip.textContent ? 1 : 0);
    };
  });
}
