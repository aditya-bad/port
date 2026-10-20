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

// Equity curves: a loss (below the zero line) is red, a gain green, on the
// Performance tab, Reports and Geek mode's overview.
const EQUITY = [
  ['Performance', `d/${IDS['BankNifty DTT Plain']}/performance`, 'svg[data-scrub=eq]'],
  ['Reports', 'reports', 'svg[data-scrub=acct]'],
  ['Geek overview', `d/${IDS['Nifty Monthly Strangle']}/geek`, '.gk svg'],
];
for (const [name, route, sel] of EQUITY) {
  test(`equity below zero is red, above zero green (${name})`, async () => {
    const ctx = await context(b);
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/${route}`);
    await p.waitForSelector(`${sel} [data-fill=down]`, { timeout: 10000 });
    // one synchronous read: Geek mode redraws its views every few seconds
    const c = await p.evaluate(sel => {
      const svg = document.querySelector(`${sel}:has([data-fill=down])`);
      const css = v => { const e = document.createElement('i'); e.style.color = `var(${v})`; document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; };
      const f = s => getComputedStyle(svg.querySelector(`[data-fill=${s}]`)).fill;
      return { up: f('up'), down: f('down'), gs: css('--gs'), rs: css('--rs') };
    }, sel);
    assert.equal(c.up, c.gs, 'gains green');
    assert.equal(c.down, c.rs, 'losses red');
    await ctx.close();
  });
}

// Cycles count oldest first: the sheet's "k of n" and its title agree,
// and Older steps down (cycle 9 of 10, then 8 of 10).
test('the cycle sheet numbers cycles oldest first, title and counter alike', async () => {
  const ctx = await context(b, { width: 1440, height: 1000 });
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/d/${IDS['Nifty Monthly Strangle']}/cycles`);
  await p.waitForSelector('[data-act=sess]', { timeout: 10000 });
  await p.locator('[data-act=sess]').first().click();
  await p.waitForSelector('.sess .ss-plot', { timeout: 15000 });
  const read = async () => {
    const [, k, n] = (await p.locator('.ss-bar').innerText()).match(/(\d+) of (\d+)/);
    const [, t] = (await p.locator('.ss-head h1').innerText()).match(/Cycle #(\d+)/);
    return [+k, +n, +t];
  };
  const [k, n, t] = await read();
  assert.equal(n, 10);
  assert.equal(t, k, 'title and counter agree');
  await p.keyboard.press('ArrowRight');                   // older
  await p.waitForFunction(want => document.querySelector('.ss-bar').innerText.includes(`${want} of`), k - 1);
  await p.waitForSelector('.sess .ss-head h1');
  assert.deepEqual(await read(), [k - 1, 10, k - 1]);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});
