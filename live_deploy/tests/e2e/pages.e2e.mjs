// Every Linck page at phone, laptop and desktop widths: renders, no
// script errors, no sideways scroll, and no text drawn over other text
// or a chart.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

const A = IDS['Nifty Strangle A'], D = IDS['BankNifty DTT Adjusted'];
const ROUTES = ['desk', 'deployments', `d/${A}/now`, `d/${A}/performance`, `d/${A}/cycles`, `d/${D}/cycles`,
  `d/${A}/trades`, `d/${A}/settings`, 'leaderboard', 'compare', 'reports', 'strategies',
  'tools/brokerage', 'tools/sip', 'tools/instruments', 'account'];
const WIDTHS = (process.env.E2E_WIDTHS || '390,1024,1440').split(',').map(Number);

// Text runs that collide with other text, or with a graphic.
const DETECT = () => {
  const out = [];
  const vis = el => { const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; };
  const runs = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) {
    const n = w.currentNode;
    if (!n.textContent.trim()) continue;
    const el = n.parentElement;
    if (!el || !vis(el) || el.closest('.tip, .toast, [aria-hidden="true"], .odo, script, style, noscript, .sr-only')) continue;
    const r = document.createRange(); r.selectNodeContents(n);
    for (const rc of r.getClientRects()) if (rc.width > 1 && rc.height > 1) runs.push({ el, rc, t: n.textContent.trim().slice(0, 30) });
  }
  const clip = (el, rc) => {
    let x0 = rc.left, y0 = rc.top, x1 = rc.right, y1 = rc.bottom;
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.overflowX !== 'visible' || s.overflowY !== 'visible') {
        const b = a.getBoundingClientRect();
        x0 = Math.max(x0, b.left); y0 = Math.max(y0, b.top); x1 = Math.min(x1, b.right); y1 = Math.min(y1, b.bottom);
      }
    }
    return x1 - x0 > 1 && y1 - y0 > 1 ? { left: x0, top: y0, right: x1, bottom: y1 } : null;
  };
  const R = runs.map(x => ({ ...x, c: clip(x.el, x.rc) })).filter(x => x.c);
  const hit = (a, b, pad = 1) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > pad && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > pad;
  const layer = el => { const l = el.closest('.appbar, .bnav, .cmp-bar, .selbar, .scrim, .wiz, .menu, .ss-bar'); return l ? l.className.split(' ')[0] : 'page'; };
  for (let i = 0; i < R.length; i++) for (let j = i + 1; j < R.length; j++) {
    const a = R[i], b = R[j];
    if (a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el) || layer(a.el) !== layer(b.el)) continue;
    if (hit(a.c, b.c, 2)) out.push(`text/text "${a.t}" × "${b.t}"`);
  }
  const gfx = [...document.querySelectorAll('svg, .lane-ev, .meter, .journey, .cbar, .hist, .ud-bars, .podium .pod-bar')].filter(vis);
  for (const t of R) for (const g of gfx) {
    if (t.el.closest('.ss-lab') || g.contains(t.el) || t.el.contains(g) || layer(t.el) !== layer(g)) continue;
    if (hit(t.c, g.getBoundingClientRect(), 3)) out.push(`text/graphic "${t.t}" × <${g.tagName.toLowerCase()}>`);
  }
  return [...new Set(out)];
};

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

for (const width of WIDTHS) {
  test(`every page at ${width}px`, async () => {
    const ctx = await context(b, { width, height: 900 });
    const problems = [];
    for (const r of ROUTES) {
      const p = await ctx.newPage();
      await p.goto(`${BASE}/linck/#/${r}`);
      await p.waitForSelector('main *', { timeout: 8000 });
      await p.waitForTimeout(1200);
      const name = r.replace(/[0-9a-f-]{36}/, 'ID');
      const { sw, cw } = await p.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
      if (sw > cw + 1) problems.push(`${name}: scrolls sideways (${sw} > ${cw})`);
      const text = await p.evaluate(() => document.querySelector('main')?.innerText || '');
      if (/undefined|NaN|\[object Object\]/.test(text)) problems.push(`${name}: shows undefined/NaN`);
      for (const f of await p.evaluate(DETECT)) problems.push(`${name}: ${f}`);
      await p.close();
    }
    problems.push(...ctx.errors);
    await ctx.close();
    assert.deepEqual(problems, []);
  });
}
