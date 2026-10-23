// Check a built page before anyone sees it, and leave pictures to look at.
//   node check.mjs <project>/out/<slug>.html [--fast] [--dark]
// Fails (exit 1) on: a script error, a step with no caption, a stretch of
// 2 s or more where nothing on screen moves (the "paused, waiting for the
// narrator" look; over 1.2 s is a warning), and a camera that keeps moving
// once it has framed its subject (every edge and letter creeps by a
// fraction of a pixel a frame, which reads as everything shaking). Warns
// about labels that overlap, run off
// the stage or out of their box, and about steps whose voice is much longer
// than their animation (a long hold). Writes frames and one contact sheet
// per scene to <project>/check/ — look at every sheet.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const EV = process.env.EV_HOME || path.join(os.homedir(), '.cache/explainer-video');
const { chromium } = await import(process.env.PW || path.join(EV, 'node/node_modules/playwright/index.mjs'));
const html = path.resolve(process.argv[2]);
const fast = process.argv.includes('--fast'), dark = process.argv.includes('--dark');
const out = path.join(path.dirname(path.dirname(html)), 'check');
fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });
const b = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const p = await b.newPage({ viewport: { width: 1600, height: 1000 }, colorScheme: dark ? 'dark' : 'light' });
const errors = [], fails = [], warns = [];
p.on('pageerror', e => errors.push(e.message));
p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
await p.goto(`file://${html}?record=1&seek=1&scene=1`);
const n = await p.evaluate(() => SCENES.length);
const stage = await p.locator('#stage').boundingBox();
// the picture only: not the progress bar, which moves even when nothing else does
const shot = async () => { await p.evaluate(() => { const g = document.querySelector('#hud .prog'); if (g) g.style.display = 'none'; }); return p.screenshot({ clip: stage }); };
const labels = () => p.evaluate(() => {
  const vis = el => { let o = 1; for (let e = el; e && e.tagName !== 'svg'; e = e.parentNode) { const c = getComputedStyle(e); o *= +(e.getAttribute('opacity') ?? 1) * +c.opacity; if (c.display === 'none') return 0; } return o; };
  const ts = [...document.querySelectorAll('#layer text, #hud text')].filter(t => t.textContent.trim() && vis(t) > 0.3).map(t => ({ t: t.textContent, p: t.parentNode, r: t.getBoundingClientRect() }));
  const st = document.getElementById('stage').getBoundingClientRect(), outl = [];
  const zoomed = /scale\(([\d.]+)\)/.test(document.getElementById('cam').getAttribute('transform') || '') && +RegExp.$1 > 1.05;   // cropping is the point of a zoom
  for (let i = 0; i < ts.length; i++) for (let j = i + 1; j < ts.length; j++) {
    const a = ts[i].r, c = ts[j].r;
    if (ts[i].p === ts[j].p) continue;                    // parts of one effect (the z's of a snore)
    if (a.left < c.right - 1 && c.left < a.right - 1 && a.top < c.bottom - 2 && c.top < a.bottom - 2) outl.push(`"${ts[i].t}" overlaps "${ts[j].t}"`);
  }
  if (!zoomed) for (const x of ts) if (x.r.left < st.left - 1 || x.r.right > st.right + 1 || x.r.top < st.top - 1 || x.r.bottom > st.bottom + 1) outl.push(`"${x.t}" runs off the stage`);
  document.querySelectorAll('#layer .bx').forEach(g => {
    const r = g.querySelector(':scope > rect').getBoundingClientRect();
    g.querySelectorAll(':scope > text').forEach(t => { const q = t.getBoundingClientRect(); if (vis(t) > 0.3 && (q.left < r.left + 2 || q.right > r.right - 2)) outl.push(`"${t.textContent}" is wider than its box`); });
  });
  return outl;
});
for (let s = 1; s <= n; s++) {
  const plan = await p.evaluate(s => window.__plan(s), s);
  const title = await p.evaluate(s => SCENES[s - 1].title, s);
  console.log(`\nscene ${s}: ${title} — ${plan.total.toFixed(1)} s`);
  const frames = [];
  for (const [j, st] of plan.steps.entries()) {
    const hold = Math.max(0, st.D - st.span / st.rate);
    console.log(`  step ${j + 1}: animation ${st.span.toFixed(1)} s, voice ${st.dur.toFixed(1)} s → ${st.D.toFixed(1)} s at ${st.rate.toFixed(2)}×${hold > 0.05 ? `, then ${hold.toFixed(1)} s of hold` : ''}`);
    if (hold > 1.5) warns.push(`scene ${s} step ${j + 1}: ${hold.toFixed(1)} s with the animation finished while the voice goes on — add motion to this step, split it, or say less`);
    for (const f of [0.08, 0.6, 0.98]) {
      const tau = st.T0 + st.D * f;
      await p.evaluate(([s, t]) => window.__seek(s, t), [s, tau]);
      const cap = (await p.textContent('#cap')).trim();
      if (!cap) fails.push(`scene ${s} step ${j + 1}: no caption at ${tau.toFixed(1)} s`);
      for (const l of await labels()) warns.push(`scene ${s} step ${j + 1} at ${tau.toFixed(1)} s: ${l}`);
      if (f === 0.6) { const file = `s${String(s).padStart(2, '0')}-${String(j + 1).padStart(2, '0')}.png`; fs.writeFileSync(path.join(out, file), await p.screenshot()); frames.push(file); }
    }
  }
  // the camera holds still once it has arrived: compare its frame (not the
  // shake of a quake) early and late in each step that lasts long enough
  const frameOf = () => p.evaluate(() => (document.getElementById('cam').getAttribute('transform') || '').replace(/^translate\([^)]*\)\s*/, ''));
  for (const [j, st] of plan.steps.entries()) {
    if (st.D < 2) continue;
    await p.evaluate(([s, t]) => window.__seek(s, t), [s, st.T0 + 1.4]);
    const early = await frameOf();
    await p.evaluate(([s, t]) => window.__seek(s, t), [s, st.T0 + st.D - 0.1]);
    if (early !== await frameOf()) fails.push(`scene ${s} step ${j + 1}: the camera keeps moving after it has framed its subject — everything on screen will look like it's shaking`);
  }
  // nothing still for long: compare frames every half second; a run of
  // identical ones is a stretch where the picture waits for the voice
  const last = plan.steps[plan.steps.length - 1], end = last.T0 + last.D, dt = fast ? 1 : 0.5;
  let prev = null, from = null;
  const runs = [];
  for (let tau = 0.5; tau <= end + 1e-6; tau += dt) {
    await p.evaluate(([s, t]) => window.__seek(s, t), [s, tau]);
    const img = await shot();
    if (prev && img.equals(prev)) { if (from == null) from = tau - dt; }
    else if (from != null) { runs.push([from, tau - dt]); from = null; }
    prev = img;
  }
  if (from != null) runs.push([from, end]);
  for (const [a, b] of runs) {
    const msg = `scene ${s}: nothing moves from ${a.toFixed(1)} to ${b.toFixed(1)} s (${(b - a).toFixed(1)} s) — add motion there, or say less`;
    if (b - a >= 2) fails.push(msg); else if (b - a >= 1.2) warns.push(msg);
  }
  // a contact sheet of the scene, one frame per step
  const cols = 3, w = 520, h = Math.round(w * 1000 / 1600);
  fs.writeFileSync(path.join(out, 'sheet.html'), `<body style="margin:0;display:grid;grid-template-columns:repeat(${cols},${w}px);gap:2px;background:#777">${frames.map(f => `<img src="${f}" style="width:${w}px;height:${h}px">`).join('')}</body>`);
  const sp = await b.newPage({ viewport: { width: cols * w + (cols - 1) * 2, height: Math.ceil(frames.length / cols) * (h + 2) } });
  await sp.goto(`file://${path.join(out, 'sheet.html')}`);
  await sp.screenshot({ path: path.join(out, `sheet-${String(s).padStart(2, '0')}.png`), fullPage: true });
  await sp.close();
}
await b.close();
for (const e of errors) fails.push(`script error: ${e}`);
console.log(`\n${warns.length} warning(s)${warns.length ? ':\n  ' + [...new Set(warns)].join('\n  ') : ''}`);
console.log(`${fails.length} failure(s)${fails.length ? ':\n  ' + fails.join('\n  ') : ''}`);
console.log(`frames and contact sheets: ${out}`);
process.exit(fails.length ? 1 : 0);
