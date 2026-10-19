// The cycle sheet's chart: the index and the P&L scrub together, and
// the P&L is green above zero, red below.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, IDS, browser, context } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

async function openCycle(p) {
  await p.goto(`${BASE}/linck/#/d/${IDS['Nifty Strangle A']}/cycles`);
  await p.waitForSelector('[data-act=sess]', { timeout: 10000 });
  await p.locator('[data-act=sess]').first().click();
  await p.waitForSelector('.ss-plot svg.ss-u', { timeout: 15000 });
  return p.locator('.ss-plot').boundingBox();
}

const guide = p => p.evaluate(() => {
  const g = document.querySelector('.tip-guide').getBoundingClientRect();
  const dots = [...document.querySelectorAll('.tip-dot')].filter(d => d.style.display === 'block').length;
  return { top: g.top, h: g.height, dots, tip: document.querySelector('.tip').innerText };
});

test('hovering either panel shows both: one guide through the index and the P&L', async () => {
  const ctx = await context(b, { width: 1440, height: 1000 });
  const p = await ctx.newPage();
  await openCycle(p);
  await p.waitForTimeout(800);                             // let the sheet finish laying out
  const box = await p.locator('.ss-plot').boundingBox();
  for (const y of [60, box.height - 40]) {                // over the index, then over the P&L
    await p.mouse.move(box.x + box.width * 0.6, box.y + y);
    await p.waitForTimeout(150);
    const g = await guide(p);
    assert.ok(Math.abs(g.top - box.y) < 2 && Math.abs(g.h - box.height) < 2, `guide spans both panels (${JSON.stringify(g)})`);
    assert.equal(g.dots, 2, 'a dot on each line');
    assert.match(g.tip, /NIFTY/); assert.match(g.tip, /Combined P&L/);
  }
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

test('the P&L line and fill are green above zero and red below', async () => {
  const ctx = await context(b, { width: 1440, height: 1000 });
  const p = await ctx.newPage();
  await openCycle(p);
  const colours = await p.evaluate(() => [...document.querySelectorAll('.ss-p g[clip-path]')].map(g =>
    [...g.querySelectorAll('path')].map(x => x.getAttribute('stroke') || x.getAttribute('fill'))));
  assert.deepEqual(colours, [['var(--g)', 'var(--g)'], ['var(--r)', 'var(--r)']]);
  await ctx.close();
});
