// LinckLens (pages/lens.js, app/lens.py): the logo picks the product,
// LinckTrade or LinckLens; Lens shows the Kite account's shares and mutual
// funds. This server has no Kite session, so the real answer is "Kite
// isn't connected"; the holdings themselves are answered here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BASE, browser, context, until } from './helper.mjs';

let b;
test.before(async () => { b = await browser(); });
test.after(async () => { await b.close(); });

const row = (symbol, qty, avg, ltp, prev) => {
  const invested = qty * avg, value = qty * ltp;
  return { symbol, exchange: 'NSE', isin: 'X', qty, t1_qty: 0, pledged_qty: 0, avg, ltp, prev_close: prev, invested, value,
    pnl: value - invested, pnl_pct: (value - invested) / invested * 100, day_change: (ltp - prev) * qty, day_change_pct: (ltp / prev - 1) * 100 };
};
const EQUITY = [row('ITC', 100, 450, 420, 425), row('INFY', 12, 1400, 1500, 1480)];
const MF = [{ fund: 'PARAG PARIKH FLEXI CAP FUND - DIRECT PLAN', folio: '123/45', isin: 'INF879O01027', units: 250.5, avg: 60, nav: 80,
  nav_date: '2026-10-26', invested: 15030, value: 20040, pnl: 5010, pnl_pct: 33.33 }];
const HOLDINGS = {
  equity: EQUITY, mf: MF, as_of: new Date().toISOString(),
  totals: {
    equity: { count: 2, invested: 61800, value: 60000, pnl: -1800, pnl_pct: -2.91, day_change: -260, day_change_pct: -0.43 },
    mf: { count: 1, invested: 15030, value: 20040, pnl: 5010, pnl_pct: 33.33 },
    all: { count: 3, invested: 76830, value: 80040, pnl: 3210, pnl_pct: 4.18 },
  },
};

test('the logo switches between LinckTrade and LinckLens', async () => {
  const ctx = await context(b);
  const p = await ctx.newPage();
  await p.goto(`${BASE}/linck/#/desk`);
  await p.waitForSelector('.hdr .prod-btn');
  assert.equal((await p.textContent('.hdr .wordmark')).trim(), 'Linck');
  assert.equal((await p.textContent('.hdr .prod-name')).trim(), 'Trade');
  await p.setViewportSize({ width: 320, height: 800 });
  assert.equal(await p.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0, 'fits a 320px phone');
  await p.setViewportSize({ width: 1440, height: 900 });
  await p.click('.hdr .prod-btn');
  const menu = p.locator('.prod-menu');
  assert.match(await menu.innerText(), /LinckTrade[\s\S]*Deployments[\s\S]*LinckLens[\s\S]*Your holdings/);
  assert.equal(await menu.locator('a.on').getAttribute('data-product'), 'trade');
  await p.keyboard.press('Escape');
  assert.equal(await p.locator('.prod-menu').count(), 0, 'Escape closes it');
  await p.click('.hdr .prod-btn');
  await p.click('.prod-menu [data-product="lens"]');
  await p.waitForSelector('.hdr.is-lens');
  assert.match(p.url(), /#\/lens$/);
  assert.equal((await p.textContent('.hdr .prod-name')).trim(), 'Lens');
  assert.deepEqual(await p.locator('.hdr .nav a').allInnerTexts(), ['Holdings'], 'Lens has its own pages');
  assert.equal(await p.locator('.hdr .basis-seg').count(), 0, 'no P&L basis: that is LinckTrade’s');
  // no Kite session on this server: it says so
  await p.waitForSelector('.lens .empty');
  assert.match(await p.locator('.lens .empty').innerText(), /Kite isn’t connected[\s\S]*Connect it with Reconnect at the top, then Refresh/);
  // and back
  await p.click('.hdr .prod-btn');
  await p.click('.prod-menu [data-product="trade"]');
  await p.waitForSelector('.hdr:not(.is-lens)');
  assert.match(p.url(), /#\/desk$/);
  assert.deepEqual(ctx.errors, []);
  await ctx.close();
});

for (const [width, label] of [[1440, 'desktop'], [390, 'phone']]) {
  test(`Holdings: what it cost, what it’s worth, every share and fund (${label})`, async () => {
    const ctx = await context(b, { width, height: 900, blockSW: true });   // the page's requests come to the route, not the service worker
    let reads = 0;
    await ctx.route('**/v2/lens/holdings*', r => { reads += 1; return r.fulfill({ json: HOLDINGS }); });
    const p = await ctx.newPage();
    await p.goto(`${BASE}/linck/#/lens`);
    await p.waitForSelector('[data-lens="equity"]');
    const strip = await p.locator('.lens-strip').innerText();
    assert.match(strip, /Current value\s+₹80,040\s+2 shares · 1 fund/);
    assert.match(strip, /Invested\s+₹76,830/);
    assert.match(strip, /Total P&L\s+\+₹3,210\s+\+4\.18%/);
    assert.match(strip, /Today, shares\s+−₹260/);
    assert.match(await p.locator('.lens-split').innerText(), /Shares ₹60,000 · 75\.0%[\s\S]*Mutual funds ₹20,040 · 25\.0%/);
    const eq = p.locator('[data-lens="equity"] .tr:not(.th):not(.tot)');
    assert.deepEqual(await eq.evaluateAll(rs => rs.map(r => r.dataset.symbol)), ['ITC', 'INFY'], 'largest first');
    assert.match(await eq.nth(1).innerText(), /INFY[\s\S]*12[\s\S]*1,400\.00[\s\S]*1,500\.00[\s\S]*₹16,800[\s\S]*₹18,000[\s\S]*\+₹1,200/);
    assert.match(await p.locator('[data-lens="equity"] .tot').innerText(), /2 shares[\s\S]*₹61,800[\s\S]*₹60,000[\s\S]*−₹1,800/);
    assert.match(await p.locator('[data-lens="mf"]').innerText(), /PARAG PARIKH FLEXI CAP[\s\S]*Folio 123\/45[\s\S]*250\.500[\s\S]*\+₹5,010/);
    // sort by P&L: the loser first, then flip
    await p.click('[data-lens="equity"] [data-act="sort"][data-k="pnl"]');
    assert.deepEqual(await eq.evaluateAll(rs => rs.map(r => r.dataset.symbol)), ['INFY', 'ITC']);
    await p.click('[data-lens="equity"] [data-act="sort"][data-k="pnl"]');
    assert.deepEqual(await eq.evaluateAll(rs => rs.map(r => r.dataset.symbol)), ['ITC', 'INFY']);
    // Refresh reads Kite again
    await p.click('[data-act="refresh"]');
    await until(async () => reads >= 2);
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
    assert.deepEqual(ctx.errors, []);
    await ctx.close();
  });
}
